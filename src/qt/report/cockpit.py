"""Was das Cockpit anzeigt -- gesammelt, nicht gerechnet.

Getrennt von `qt.report.html`, weil Sammeln und Rendern zwei verschiedene
Fehlerarten haben: hier faellt auf, wenn eine Zahl aus der falschen Quelle
kommt, dort, wenn sie falsch aussieht. Zusammen in einer Datei faellt beides
schwerer auf.

Die Regel dieses Moduls ist, dass es **nichts nachrechnet**, was das System
schon ausrechnet. `IntegrityReport` kommt aus `qt.data.integrity`, das
Eigenkapital aus `qt.core.types.equity` (derselben Funktion, die auch der
`SimBroker` benutzt), und die Frage "welche Bars haette ein Tick jetzt zu
verarbeiten" wird mit `merge_bar_streams` und derselben Grenze `ts <= now`
beantwortet wie in `qt.live.runner`. Eine zweite Implementierung waere eine
zweite Wahrheit -- und die im Report ist die, der man beim Hinsehen glaubt.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from qt.core.config import PROJECT_ROOT, DataConfig
from qt.core.events import merge_bar_streams
from qt.core.types import equity as compute_equity
from qt.data.integrity import IntegrityReport, check
from qt.data.store import available, read_bars, to_bars
from qt.live.state import PaperState, state_path

# Ab wieviel Bar-Laengen ohne neuen verarbeiteten Bar das Konto als veraltet
# gilt. Drei, nicht eins: ein einzelner ausgefallener Tick ist normal, drei
# hintereinander sind ein Muster. Vier waeren bei einem Tagesbar schon fast
# eine Woche.
STALE_AFTER_BARS = 3


@dataclass(slots=True)
class Segment:
    """Ein Stueck der Abdeckungsleiste: vorhanden oder fehlend."""

    start: datetime
    end: datetime
    missing_bars: int
    x0: float
    x1: float

    @property
    def is_gap(self) -> bool:
        return self.missing_bars > 0


@dataclass(slots=True)
class SeriesView:
    """Ein (Symbol, Timeframe) im Store."""

    report: IntegrityReport
    segments: list[Segment]

    @property
    def largest_gap(self) -> Segment | None:
        gaps = [s for s in self.segments if s.is_gap]
        return max(gaps, key=lambda s: s.missing_bars) if gaps else None


@dataclass(slots=True)
class PaperView:
    """Der Zustand eines Paper-Kontos, angereichert um das, was fehlt.

    `PaperState` weiss nicht, wieviel es wert ist -- es kennt Mengen und
    Einstaende, aber keine aktuellen Preise. Und es weiss nicht, ob es
    hinterherhinkt: der Cursor sagt, bis wohin entschieden wurde, nicht, was
    seitdem im Store liegt. Beides wird hier ergaenzt.
    """

    strategy: str
    symbols: list[str]
    timeframe: str
    state: PaperState
    path: Path
    prices: dict[str, float]
    equity: float
    unprocessed_bars: int
    newest_close: datetime | None
    max_drawdown: float
    now: datetime

    @property
    def drawdown(self) -> float:
        """Abstand zum Hoechststand, wie der Kill-Switch ihn misst."""
        if self.state.peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - self.equity / self.state.peak_equity)

    @property
    def headroom(self) -> float:
        """Was bis zur Kill-Switch-Schwelle noch fallen darf."""
        return self.max_drawdown - self.drawdown

    @property
    def last_processed(self) -> datetime | None:
        return self.state.last_processed()

    @property
    def age_seconds(self) -> float | None:
        last = self.last_processed
        if last is None:
            return None
        return (self.now - last).total_seconds()

    @property
    def warm(self) -> bool:
        return self.state.warmup_end is not None


@dataclass(slots=True)
class Cockpit:
    """Alles, was auf die Seite kommt."""

    now: datetime
    paper: PaperView | None
    missing_paper_path: Path | None
    series: list[SeriesView]
    command: str
    git_commit: str
    data_dir: Path
    warnings: list[str] = field(default_factory=list)


def strip_segments(report: IntegrityReport) -> list[Segment]:
    """Die Abdeckungsleiste als Folge von Stuecken.

    Der Grund fuer diese Darstellung steht im UI-Plan: der
    Order-Flow-Bestand sieht als Zusammenfassung gesund aus -- 2,4 Mio
    Trades ueber 361 Tage -- und hat in der Mitte ein 301-Tage-Loch. Eine
    Abdeckungszahl von 99% verbirgt genau den Fall, der ein
    Train/Test/Embargo-Fenster unmoeglich macht; eine Leiste zeigt ihn.
    """
    if report.start is None or report.end is None:
        return []

    span = (report.end - report.start).total_seconds()
    if span <= 0:
        return []

    def x(ts: datetime) -> float:
        return (ts - report.start).total_seconds() / span

    segments: list[Segment] = []
    cursor = report.start
    for gap in sorted(report.gaps, key=lambda g: g.start):
        if gap.start > cursor:
            segments.append(Segment(cursor, gap.start, 0, x(cursor), x(gap.start)))
        segments.append(
            Segment(gap.start, gap.end, gap.missing_bars, x(gap.start), x(gap.end))
        )
        cursor = gap.end
    if cursor < report.end:
        segments.append(Segment(cursor, report.end, 0, x(cursor), 1.0))
    return segments


def collect(
    strategy: str,
    symbols: list[str],
    timeframe: str,
    *,
    max_drawdown: float = 0.20,
    now: datetime | None = None,
    data_dir: Path | None = None,
    state_dir: Path | None = None,
    command: str = "",
) -> Cockpit:
    """Den Zustand einlesen. Liest ausschliesslich -- schreibt nirgends."""
    now = now or datetime.now(timezone.utc)
    data_dir = data_dir or DataConfig().data_dir

    series = [
        SeriesView(report=report, segments=strip_segments(report))
        for report in (
            check(sym, tf, read_bars(sym, tf, data_dir=data_dir))
            for sym, tf in available(data_dir)
        )
    ]

    path = state_path(strategy, symbols, timeframe, state_dir or data_dir)
    state = PaperState.load(path)

    warnings: list[str] = []
    paper: PaperView | None = None
    if state is not None:
        prices, unprocessed, newest = _market_context(
            symbols, timeframe, state, now, data_dir
        )
        missing = [s for s in symbols if s not in prices and s in state.positions]
        if missing:
            warnings.append(
                f"Kein Preis im Store fuer {', '.join(missing)} -- diese Positionen "
                f"sind zum Einstand bewertet, nicht zum Markt."
            )
        paper = PaperView(
            strategy=strategy,
            symbols=symbols,
            timeframe=timeframe,
            state=state,
            path=path,
            prices=prices,
            equity=compute_equity(state.cash, state.positions_as_objects(), prices),
            unprocessed_bars=unprocessed,
            newest_close=newest,
            max_drawdown=max_drawdown,
            now=now,
        )

    return Cockpit(
        now=now,
        paper=paper,
        missing_paper_path=None if state else path,
        series=series,
        command=command,
        git_commit=git_commit(),
        data_dir=data_dir,
        warnings=warnings,
    )


def _market_context(
    symbols: list[str],
    timeframe: str,
    state: PaperState,
    now: datetime,
    data_dir: Path,
) -> tuple[dict[str, float], int, datetime | None]:
    """Letzte bekannte Preise und die Zahl noch nicht verarbeiteter Bars.

    Die Zaehlung benutzt bewusst `merge_bar_streams` und dieselbe Grenze
    `ts <= now` wie `qt.live.runner.run_paper_tick`. Nachgebaut waere sie
    eine Schaetzung: `BarEvent.ts` ist die **Close**-Zeit, `read_bars`
    liefert Open-Zeiten, und ein Bar, dessen Close noch in der Zukunft
    liegt, wird vom Tick vollstaendig verworfen (ADR-038). Wer das hier
    anders zaehlt, zeigt eine Zahl, die kein Tick je einloest.
    """
    streams = []
    for symbol in symbols:
        try:
            df = read_bars(symbol, timeframe, data_dir=data_dir)
        except FileNotFoundError:
            continue
        if not df.empty:
            streams.append(to_bars(symbol, timeframe, df))

    if not streams:
        return {}, 0, None

    events = [e for e in merge_bar_streams(streams) if e.ts <= now]
    if not events:
        return {}, 0, None

    prices: dict[str, float] = {}
    for event in events:
        prices[event.bar.symbol] = event.bar.close

    last_processed = state.last_processed()
    unprocessed = (
        len(events)
        if last_processed is None
        else sum(1 for e in events if e.ts > last_processed)
    )
    return prices, unprocessed, events[-1].ts


def git_commit() -> str:
    """Der Codestand, auf den sich die Zahlen beziehen.

    Eine Zahl ohne Codestand ist ein Screenshot: die ROADMAP korrigiert an
    mehreren Stellen frueher gemessene Werte, weil sich der Code darunter
    geaendert hatte.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unbekannt"
    return out.stdout.strip() or "unbekannt"
