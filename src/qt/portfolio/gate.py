"""Das Gate -- ein Allokator kommt nur durch, wenn er die Baselines schlaegt.

Diese Datei ist die Umsetzung von ADR-004. Ab Phase 3 sitzt an der
Allokator-Stelle ein LLM. Ohne Vergleichsmassstab wird jedes LLM-Ergebnis
als Erfolg gelesen: die Kurve steigt, also war die Allokation gut. Equal-
Weight und Vol-Parity sind aber erstaunlich schwer zu schlagen, und ein
LLM, das sie nicht schlaegt, hat nichts beigetragen ausser Kosten und
Unvorhersagbarkeit.

Das Gate ist die Instanz, die das entscheidet: **der Kandidat geht nur
weiter, wenn er out-of-sample jede einzelne Baseline schlaegt.** Tut er das
nicht, ist das ein Ergebnis und kein Fehler -- und `verdict()` sagt genau
das, im Klartext, samt Namen der Baseline, an der er gescheitert ist.

Warum das Gate nicht `walk_forward()` benutzt
---------------------------------------------
Die Fenstergeometrie ist dieselbe (Train | Embargo | Test, Warmup vor dem
Testbeginn, Verkettung ueber Renditen statt ueber Equity-Werte), aber
`walk_forward()` nimmt **eine** Strategie und `dict[symbol, list[Bar]]` und
ruft `run_backtest` auf. Hier wird ein **Portfolio** bewertet: ein Satz
Strategien, ein Allokator, optional eine Risk-Engine, Bars nach
`(Symbol, Timeframe)` geschluesselt, Lauf ueber `run_portfolio_backtest`.
Die Signatur passt an keiner Stelle, und ein Umbau von `walk_forward()` auf
beides haette aus einer klaren Funktion eine mit zwei Betriebsarten
gemacht. Die Fensterlogik ist deshalb hier nachgebaut -- die beiden
heiklen Bausteine (`_oos_segment`, `_chain`) werden aber importiert und
nicht kopiert: sie sind die Definition von "OOS-Segment" und "Verkettung"
in diesem System, und eine zweite Kopie wuerde davon abdriften.

Was hier fair heisst
--------------------
Ein Vergleich zwischen Allokatoren ist wertlos, sobald sie unterschiedliche
Bars, Fenster, Configs, Risk-Grenzen oder Takte sehen. Deshalb werden die
Bar-Ausschnitte je Fenster **einmal** geschnitten und allen Allokatoren
dasselbe Objekt uebergeben. Nicht "mit denselben Argumenten geschnitten" --
dasselbe Objekt. Das ist der einzige Aufbau, bei dem ein spaeterer
Umbau die Fairness nicht leise brechen kann.

Frisch je Fenster und je Allokator sind dagegen: Strategien (sie tragen
`self._state`), Allokatoren (ein LLM-Allokator haelt Gespraechsverlauf) und
die Risk-Engine (ihr Kill-Switch ist bewusst klebrig). Deshalb nimmt
`run_gate` Fabriken. Instanzen werden akzeptiert, aber je Lauf tief
kopiert -- sonst traegt Fenster i seinen Zustand nach i+1, und dieses Leck
ist im Ergebnis unsichtbar.

Warum nicht nur Sharpe verglichen wird
--------------------------------------
Zwei Allokatoren mit gleichem Sharpe sind nicht gleich gut: wer dafuer den
doppelten Umsatz braucht, verliert bei Coinbase-Gebuehren real Geld
(ADR-009). Und ein Allokator, der nur zu 5% im Markt ist, ist nicht besser,
sondern etwas anderes -- annualisierte Kennzahlen ueber eine ueberwiegend
flache Reihe messen vor allem Untaetigkeit (ADR-016). Beides steht in der
Tabelle und beides geht ins Urteil ein.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from qt.backtest.costs import FillModel
from qt.backtest.metrics import Metrics, compute
from qt.backtest.portfolio_engine import (
    RETURN_HISTORY,
    PortfolioResult,
    run_portfolio_backtest,
)

# Bewusst importiert statt nachgebaut: `_oos_segment` normiert ein Testfenster
# auf den letzten Stand davor (inklusive des Referenzpunkts, ohne den der
# erste Testbar aus den Renditen faellt), `_chain` verkettet die Segmente
# ueber ihre Renditen statt ueber ihre Absolutwerte. Beides sind subtile
# Definitionen, die es im System genau einmal geben darf. Dass sie
# unterstrichen sind, ist ein Argument dafuer, sie in `walkforward.py`
# oeffentlich zu machen -- nicht dafuer, sie hier zu kopieren.
from qt.backtest.walkforward import (
    InsufficientDataError,
    _chain,
    _oos_segment,
    _slice,
)
from qt.core.config import BacktestConfig
from qt.core.types import Bar, timeframe_seconds
from qt.portfolio.base import Allocator, RiskLimits
from qt.portfolio.baselines import default_set
from qt.strategy.base import Strategy

BarSet = dict[tuple[str, str], list[Bar]]
AllocatorSource = Allocator | Callable[[], Allocator]
StrategySource = dict[str, Strategy] | Callable[[], dict[str, Strategy]]
RiskSource = RiskLimits | Callable[[], RiskLimits | None] | None

# ---------------------------------------------------------------------------
# Die Schwellen des Gates.
#
# Bewusst Modulkonstanten und **keine** Parameter von `run_gate`: eine
# Schwelle, die man am Aufrufort setzen kann, wird am Aufrufort gesenkt,
# sobald der Kandidat sie reisst. Genau das ist der Fehler, gegen den dieses
# Modul gebaut ist. Wer sie aendern will, aendert sie hier -- sichtbar, in
# einem Commit, mit einem ADR daneben.
# ---------------------------------------------------------------------------

# Sharpe-Unterschiede darunter sind kein Vorsprung, sondern Rauschen. In dem
# Band entscheidet der Umsatz (ADR-009).
SHARPE_TIE = 0.02

# Bei Sharpe-Gleichstand muss der Kandidat spuerbar weniger umschlagen, nicht
# nur eine Nachkommastelle weniger.
TURNOVER_EDGE = 0.90

# Anteil der Fenster, die der Kandidat gegen **jede** Baseline gewinnen muss.
# Strikt groesser: wer bei acht Fenstern vier gewinnt, hat nicht gewonnen,
# sondern eine Muenze geworfen.
MIN_WINDOW_WIN_RATE = 0.50

# Untergrenze der Zeit im Markt, als Anteil der mittleren Baseline. Darunter
# vergleicht man keine bessere Allokation, sondern eine andere Frage
# (ADR-016).
MIN_TIME_IN_MARKET_RATIO = 0.50


@dataclass(slots=True)
class GateWindow:
    """Ein Out-of-Sample-Fenster eines einzelnen Allokators.

    `oos_equity` ist nach Bar-**Close**-Zeiten indiziert und beginnt mit dem
    Referenzpunkt unmittelbar vor dem Testfenster (siehe `_oos_segment`).
    Die Feldnamen `index` und `oos_equity` sind der Vertrag mit `_chain`.
    """

    index: int
    test_start: datetime
    test_end: datetime
    metrics: Metrics
    oos_equity: pd.Series


@dataclass
class GateEntry:
    """Ein Teilnehmer am Vergleich: Kandidat oder Baseline.

    `metrics` bezieht sich auf die **verkettete** OOS-Kurve ueber alle
    Fenster, `windows` auf die einzelnen Fenster. Der Gesamtwert allein
    taugt nicht als Urteil: ein Allokator, der in einem von acht Fenstern
    alles verdient, hat eine Zufallsstichprobe und keine Kante.

    `error` ist gesetzt, wenn der Allokator in einem Fenster geflogen ist.
    Das ist kein Grund abzubrechen -- ein Allokator, der wirft, ist ein
    Allokator, der durchfaellt, und das gehoert ins Ergebnis statt in einen
    Stacktrace.
    """

    allocator: str
    metrics: Metrics
    is_baseline: bool
    windows: list[GateWindow] = field(default_factory=list)
    equity: pd.DataFrame = field(default_factory=pd.DataFrame)
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None

    @property
    def window_sharpes(self) -> list[float]:
        return [w.metrics.sharpe for w in self.windows]

    @property
    def sharpe_sd(self) -> float:
        """Streuung des Sharpe ueber die Fenster.

        Steht neben dem Gesamtsharpe, weil beide zusammen erst eine Aussage
        ergeben: ein hoher Mittelwert mit riesiger Streuung ist ein
        Gluecksfall, kein Verfahren.
        """
        values = self.window_sharpes
        if len(values) < 2:
            return 0.0
        return float(np.std(values, ddof=1))


@dataclass
class GateResult:
    """Ergebnis eines Gate-Laufs. Der Kandidat steht immer an Position 0.

    Nachgeschlagen wird ueber die Position und nicht ueber den Namen: ein
    Kandidat darf denselben Namen tragen wie eine Baseline (etwa beim
    Regressionstest "schlaegt sich der Allokator selbst?"), und dann waere
    ein Namensschluessel mehrdeutig.
    """

    entries: list[GateEntry]
    candidate: str
    timeframe: str
    config: BacktestConfig
    train_bars: int
    test_bars: int
    step_bars: int
    embargo_bars: int
    warmup_bars: int
    unused_tail_bars: int = 0

    # -- Zugriff ------------------------------------------------------------

    @property
    def candidate_entry(self) -> GateEntry:
        return self.entries[0]

    @property
    def baselines(self) -> list[GateEntry]:
        return [e for e in self.entries[1:] if e.is_baseline]

    @property
    def n_windows(self) -> int:
        return len(self.candidate_entry.windows)

    def window_wins(self) -> dict[str, tuple[int, int]]:
        """Je Baseline: in wievielen Fenstern der Kandidat sie schlaegt.

        Das ist die Streuungsangabe, ohne die der Gesamtvergleich in die
        Irre fuehrt -- ein einziges dominantes Fenster reisst jeden
        Gesamtsharpe hoch.
        """
        candidate = self.candidate_entry
        out: dict[str, tuple[int, int]] = {}
        for baseline in self.baselines:
            pairs = list(zip(candidate.windows, baseline.windows, strict=False))
            wins = sum(1 for c, b in pairs if _beats(c.metrics, b.metrics))
            out[baseline.allocator] = (wins, len(pairs))
        return out

    def window_frame(self) -> pd.DataFrame:
        """Sharpe je Fenster und Allokator, fuer Auswertung ausserhalb."""
        rows: list[dict] = []
        for window in self.candidate_entry.windows:
            row: dict = {
                "window": window.index,
                "test_start": window.test_start,
                "test_end": window.test_end,
            }
            for entry in self.entries:
                match = [w for w in entry.windows if w.index == window.index]
                row[entry.allocator] = match[0].metrics.sharpe if match else float("nan")
            rows.append(row)
        return pd.DataFrame(rows)

    # -- Urteil -------------------------------------------------------------

    def blockers(self) -> list[str]:
        """Alle Gruende, aus denen der Kandidat nicht durchgeht. Leer = bestanden.

        Bewusst eine vollstaendige Liste statt eines ersten Treffers: wer nur
        den ersten Grund sieht, repariert ihn und laesst das Gate erneut
        laufen, bis es endlich gruen ist -- das ist Overfitting auf die
        Pruefung selbst.
        """
        reasons: list[str] = []
        candidate = self.candidate_entry

        if candidate.failed:
            reasons.append(f"Kandidat ist geflogen -- {candidate.error}")

        # Eine Baseline, die nicht durchlaeuft, ist ein Fehler in unserem
        # eigenen Code. Sie darf aber unter keinen Umstaenden dazu fuehren,
        # dass der Kandidat leichter durchkommt, weil ein Massstab fehlt.
        for baseline in self.baselines:
            if baseline.failed:
                reasons.append(
                    f"Baseline {baseline.allocator} ist geflogen -- ohne "
                    f"vollstaendiges Feld gibt es kein Urteil ({baseline.error})"
                )

        if not self.baselines:
            reasons.append("Keine Baseline im Lauf -- ein Gate ohne Massstab ist keines")

        if candidate.failed or any(b.failed for b in self.baselines):
            return reasons

        # Absolut, nicht nur relativ. Ein Gate, das eine dauerhaft
        # verlustreiche Allokation durchwinkt, weil die Baselines noch
        # schlechter sind, ist ein Ranking und keine Freigabe.
        if candidate.metrics.sharpe <= 0:
            reasons.append(
                f"Sharpe {candidate.metrics.sharpe:.2f} ist nicht positiv -- der "
                "Kandidat verdient out-of-sample kein Geld, unabhaengig von den "
                "Baselines"
            )

        for baseline in self.baselines:
            if not _beats(candidate.metrics, baseline.metrics):
                reasons.append(
                    f"schlaegt {baseline.allocator} nicht: Sharpe "
                    f"{candidate.metrics.sharpe:.2f} gegen "
                    f"{baseline.metrics.sharpe:.2f} bei Umsatz "
                    f"{candidate.metrics.turnover:,.0f} gegen "
                    f"{baseline.metrics.turnover:,.0f}"
                )

        for name, (wins, total) in self.window_wins().items():
            if total and wins / total <= MIN_WINDOW_WIN_RATE:
                reasons.append(
                    f"gewinnt nur {wins} von {total} Fenstern gegen {name} -- "
                    "ein Vorsprung aus einem einzelnen Fenster ist eine "
                    "Zufallsstichprobe"
                )

        floor = self._time_in_market_floor()
        if candidate.metrics.time_in_market < floor:
            reasons.append(
                f"Zeit im Markt {candidate.metrics.time_in_market:.1%} liegt unter "
                f"{floor:.1%} der Baselines -- das ist keine bessere Allokation, "
                "sondern eine andere Strategie (ADR-016)"
            )

        return reasons

    def passed(self) -> bool:
        """Geht der Kandidat weiter?

        Streng und ohne Ermessen: er muss **jede** Baseline schlagen, nicht
        die schlechteste und nicht den Durchschnitt. Der Grund steht in
        ADR-004 -- Equal-Weight und Vol-Parity sind schwer zu schlagen, und
        ein LLM, das nur eine der drei Baselines hinter sich laesst, hat den
        Beweis nicht erbracht, dass sein Beitrag mehr ist als Rauschen.

        Wer hier spaeter "die Mehrheit der Baselines" oder "im Mittel besser"
        einsetzt, hat das Gate abgeschafft und nur den Namen behalten.
        """
        return not self.blockers()

    def verdict(self) -> str:
        """Klartext-Urteil, auch wenn es unbequem ist."""
        candidate = self.candidate_entry
        if self.passed():
            best = max(self.baselines, key=lambda e: e.metrics.sharpe)
            wins = ", ".join(
                f"{name} {w}/{t}" for name, (w, t) in self.window_wins().items()
            )
            return (
                f"BESTANDEN -- {self.candidate} schlaegt alle "
                f"{len(self.baselines)} Baselines out-of-sample ueber "
                f"{self.n_windows} Fenster.\n"
                f"  Sharpe {candidate.metrics.sharpe:.2f} gegen die beste Baseline "
                f"{best.allocator} mit {best.metrics.sharpe:.2f}.\n"
                f"  Gewonnene Fenster: {wins}.\n"
                "  Freigabe heisst weiterfahren, nicht recht behalten: der "
                "Vorsprung ist an dieser Datenlage gemessen und gehoert im "
                "Forward-Paper-Trading nachgeprueft (ADR-003)."
            )

        lines = [f"DURCHGEFALLEN -- {self.candidate} geht nicht weiter."]
        lines += [f"  - {reason}" for reason in self.blockers()]
        lines.append(
            "  Das ist ein Ergebnis und kein Fehler (ADR-004): ein Allokator, "
            "der die Baselines nicht schlaegt, gehoert nicht in den Kreislauf."
        )
        return "\n".join(lines)

    # -- Darstellung --------------------------------------------------------

    def table(self) -> str:
        """Lesbare Gegenueberstellung: Gesamtwerte und Streuung je Fenster.

        Sharpe steht bewusst nicht allein. `Umsatz` beziffert, was ein
        gleicher Sharpe an Gebuehren kostet (ADR-009), `Zeit i.M.` verhindert,
        dass eine ueberwiegend flache Reihe wie eine ruhige gelesen wird
        (ADR-016), und `sd(F)` zeigt, ob das Ergebnis auf allen Fenstern
        steht oder auf einem.
        """
        width = min(max([len(e.allocator) for e in self.entries] + [16]) + 2, 30)
        head = (
            f"    {'Allokator':<{width}}{'Sharpe':>8}{'sd(F)':>8}{'Rendite':>10}"
            f"{'MaxDD':>9}{'Calmar':>10}{'Zeit i.M.':>11}{'Umsatz':>14}{'Trades':>8}"
        )
        lines = [
            f"Gate: {self.candidate} gegen {len(self.baselines)} Baselines, "
            f"{self.n_windows} OOS-Fenster "
            f"(train {self.train_bars} / embargo {self.embargo_bars} / "
            f"test {self.test_bars}, Warmup {self.warmup_bars})",
            "",
            head,
            "  " + "-" * (len(head) - 2),
        ]

        for entry in self.entries:
            # Der Kandidat wird vorangestellt markiert und nicht hinten
            # angehaengt: ein Marker hinter dem Namen verschiebt die ganze
            # Zeile gegen die Kopfzeile, und eine Tabelle, deren Spalten nicht
            # untereinander stehen, wird nicht gelesen.
            mark = "  " if entry.is_baseline else "* "
            label = _shorten(entry.allocator, width - 1)
            if entry.failed:
                lines.append(f"  {mark}{label:<{width}}FEHLER: {entry.error}")
                continue
            m = entry.metrics
            lines.append(
                f"  {mark}{label:<{width}}"
                + _cell(m.sharpe, 8, ".2f")
                + _cell(entry.sharpe_sd, 8, ".2f")
                + _cell(m.total_return, 10, ".1%")
                + _cell(m.max_drawdown, 9, ".1%")
                + _cell(m.calmar, 10, ".2f")
                + _cell(m.time_in_market, 11, ".1%")
                + _cell(m.turnover, 14, ",.0f")
                + _cell(m.n_trades, 8, ",d")
            )
        lines.append("  * = Kandidat, alle uebrigen sind Baselines")

        lines += ["", "  Sharpe je Fenster:"]
        lines.append(self._window_table())

        wins = self.window_wins()
        if wins:
            summary = ", ".join(f"{name} {w}/{t}" for name, (w, t) in wins.items())
            lines.append(f"\n  Fenster, die der Kandidat gewinnt: {summary}")
        return "\n".join(lines)

    def _window_table(self) -> str:
        """Sharpe je Fenster, eine Spalte je Allokator.

        Der Gesamtwert allein verschweigt, ob ein Vorsprung auf allen Fenstern
        steht oder auf einem einzigen -- und das ist der Unterschied zwischen
        einem Verfahren und einer Zufallsstichprobe.
        """
        col = min(max([len(e.allocator) for e in self.entries] + [8]) + 2, 22)
        labels = [_shorten(e.allocator, col - 1) for e in self.entries]
        head = "    " + f"{'Fenster':<14}" + "".join(f"{lab:>{col}}" for lab in labels)
        lines = [head, "    " + "-" * (len(head) - 4)]
        for window in self.candidate_entry.windows:
            cells = []
            for entry in self.entries:
                match = [w for w in entry.windows if w.index == window.index]
                cells.append(
                    _cell(match[0].metrics.sharpe, col, ".2f") if match else f"{'-':>{col}}"
                )
            label = f"{window.index}: {window.test_start:%Y-%m-%d}"
            lines.append("    " + f"{label:<14}" + "".join(cells))
        return "\n".join(lines)

    def _time_in_market_floor(self) -> float:
        """Untergrenze der Zeit im Markt, abgeleitet aus den Baselines.

        Median statt Maximum: eine einzelne dauerpositionierte Baseline soll
        die Grenze nicht allein setzen, ein durchweg flaches Feld sie aber
        auch nicht auf null druecken.
        """
        values = [b.metrics.time_in_market for b in self.baselines]
        if not values:
            return 0.0
        return float(np.median(values)) * MIN_TIME_IN_MARKET_RATIO


def run_gate(
    candidate: AllocatorSource,
    strategies: StrategySource,
    bars: BarSet,
    train_bars: int,
    test_bars: int,
    baselines: list[AllocatorSource] | Callable[[], list[Allocator]] | None = None,
    step_bars: int | None = None,
    embargo_bars: int = 0,
    cfg: BacktestConfig | None = None,
    risk: RiskSource = None,
    allocate_every: int = 1,
    fill_model: FillModel | None = None,
    candidate_name: str | None = None,
) -> GateResult:
    """Einen Allokator out-of-sample gegen die Baselines antreten lassen.

    Der Kandidat und jede Baseline laufen ueber **dieselben** rollierenden
    Fenster, mit denselben Bar-Objekten, derselben Config, derselben
    Risk-Engine-Konfiguration und demselben Allokationstakt. Bewertet wird
    ausschliesslich der Teil ab Testbeginn; der Vorlauf davor dient dem
    Warmup von Strategien *und* Allokatoren und wird nicht gezaehlt.

    `candidate`, `strategies`, `baselines` und `risk` duerfen Instanzen oder
    Fabriken sein. Instanzen werden je Lauf tief kopiert -- sowohl
    Strategien (`self._state`) als auch Allokatoren (ein LLM-Allokator haelt
    Gespraechsverlauf) als auch die Risk-Engine (klebriger Kill-Switch)
    tragen Zustand, und geteilter Zustand zwischen Fenstern ist ein Leck,
    das im Ergebnis unsichtbar bleibt. Eine Fabrik, die zweimal dieselbe
    Instanz liefert, wird abgelehnt.

    Ohne `baselines` tritt `qt.portfolio.baselines.default_set()` an. Eine
    leere Liste ist ein Fehler: ein Gate ohne Massstab ist kein Gate.

    Wirft `InsufficientDataError`, wenn die Daten die Fenstergeometrie nicht
    hergeben -- ein stiller Leerlauf saehe aus wie "Kandidat hat nichts
    gehandelt" statt wie "Zeitraum zu kurz".
    """
    cfg = cfg or BacktestConfig()
    step_bars = test_bars if step_bars is None else step_bars
    _check_geometry(train_bars, test_bars, step_bars, embargo_bars)

    make_strategies = _strategy_factory(strategies)
    make_candidate = _allocator_factory(candidate, "candidate")
    make_risk = _risk_factory(risk)
    baseline_factories = _baseline_factories(baselines)
    if not baseline_factories:
        raise ValueError(
            "Keine Baselines uebergeben. Ein Gate ohne Massstab ist kein Gate -- "
            "ohne Vergleich wird jedes Ergebnis als Erfolg gelesen (ADR-004)."
        )

    if not bars or all(len(stream) == 0 for stream in bars.values()):
        raise InsufficientDataError("Keine Bars uebergeben.")

    probe_strategies = make_strategies()
    probe_allocators = [make_candidate()] + [f() for f in baseline_factories]

    # Der feinste Timeframe ist der Takt der Zeitachse und damit auch der
    # Bezug fuer die Annualisierung: die Equity-Kurve eines Portfolio-Laufs
    # hat einen Punkt je eindeutiger Bar-Close-Zeit. Mit dem groebsten
    # Timeframe zu annualisieren (dem Takt des Allokators) waere um den
    # Faktor zwischen beiden daneben.
    slot_seconds = min(timeframe_seconds(tf) for _, tf in bars)
    metrics_tf = min({tf for _, tf in bars}, key=timeframe_seconds)

    warmup_slots = _warmup_slots(probe_strategies, probe_allocators, bars, slot_seconds)
    timeline = _timeline(bars)
    _check_data(len(timeline), train_bars, test_bars, embargo_bars, warmup_slots)

    span = train_bars + embargo_bars + test_bars
    starts = list(range(0, len(timeline) - span + 1, step_bars))

    # Einmal schneiden, allen dasselbe geben. Wuerde jeder Allokator seinen
    # eigenen Schnitt bekommen, waere jede Abweichung darin ein stiller
    # Vorteil -- und der Vergleich wertlos.
    geometry = [
        _cut_window(index, start, timeline, bars, train_bars, embargo_bars, test_bars,
                    warmup_slots)
        for index, start in enumerate(starts)
    ]

    entries: list[GateEntry] = []
    used: set[str] = set()

    def add(factory: Callable[[], Allocator], is_baseline: bool, name: str) -> None:
        entries.append(
            _run_entry(
                name=_unique(name, used),
                factory=factory,
                is_baseline=is_baseline,
                geometry=geometry,
                make_strategies=make_strategies,
                make_risk=make_risk,
                cfg=cfg,
                allocate_every=allocate_every,
                fill_model=fill_model,
                metrics_tf=metrics_tf,
            )
        )

    add(make_candidate, False, candidate_name or probe_allocators[0].describe())
    for factory, probe in zip(baseline_factories, probe_allocators[1:], strict=True):
        add(factory, True, probe.describe())

    return GateResult(
        entries=entries,
        candidate=entries[0].allocator,
        timeframe=metrics_tf,
        config=cfg,
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
        embargo_bars=embargo_bars,
        warmup_bars=warmup_slots,
        unused_tail_bars=len(timeline) - (starts[-1] + span),
    )


# ---------------------------------------------------------------------------
# Vergleich
# ---------------------------------------------------------------------------


def _beats(candidate: Metrics, baseline: Metrics) -> bool:
    """Schlaegt `candidate` die Baseline?

    Sharpe entscheidet, aber nicht auf der dritten Nachkommastelle: liegen
    beide innerhalb von `SHARPE_TIE`, ist der Unterschied Rauschen und der
    Umsatz entscheidet. Ein Allokator, der denselben Sharpe mit der Haelfte
    des Umschlags erreicht, ist der bessere -- bei Coinbase-Retail-Gebuehren
    ist das der Unterschied zwischen 6,4x und 0,46x (ADR-009).

    Gleichstand in beidem heisst **nicht geschlagen**. Die Beweislast liegt
    beim Kandidaten.
    """
    diff = candidate.sharpe - baseline.sharpe
    if diff > SHARPE_TIE:
        return True
    if diff < -SHARPE_TIE:
        return False
    return candidate.turnover < baseline.turnover * TURNOVER_EDGE


# ---------------------------------------------------------------------------
# Fenster
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Cut:
    """Ein Fenster als fertiger Bar-Ausschnitt -- identisch fuer alle Allokatoren."""

    index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    warmup_start: datetime
    bars: BarSet


def _cut_window(
    index: int,
    start: int,
    timeline: list[datetime],
    bars: BarSet,
    train_bars: int,
    embargo_bars: int,
    test_bars: int,
    warmup_slots: int,
) -> _Cut:
    """Bar-Ausschnitt eines Fensters: Vorlauf ab `test_start - warmup`.

    Der Vorlauf liegt bewusst *vor* dem Testfenster und nicht darin. Liesse
    man das Testfenster isoliert laufen, verbrennt sein Anfang mit Warmup
    und das Ergebnis haengt an der Fenstergroesse statt am Allokator. Ein
    Lookahead ist es nicht: ein live laufendes System haette diese Bars zu
    diesem Zeitpunkt ebenfalls gekannt.
    """
    test_lo = start + train_bars + embargo_bars
    test_hi = test_lo + test_bars - 1
    run_lo = test_lo - warmup_slots

    window_bars = {
        key: stream
        for key, source in bars.items()
        if (stream := _slice(source, timeline[run_lo], timeline[test_hi]))
    }
    return _Cut(
        index=index,
        train_start=timeline[start],
        train_end=timeline[start + train_bars - 1],
        test_start=timeline[test_lo],
        test_end=timeline[test_hi],
        warmup_start=timeline[run_lo],
        bars=window_bars,
    )


def _run_entry(
    name: str,
    factory: Callable[[], Allocator],
    is_baseline: bool,
    geometry: list[_Cut],
    make_strategies: Callable[[], dict[str, Strategy]],
    make_risk: Callable[[], RiskLimits | None],
    cfg: BacktestConfig,
    allocate_every: int,
    fill_model: FillModel | None,
    metrics_tf: str,
) -> GateEntry:
    """Einen Allokator ueber alle Fenster laufen lassen.

    Wirft der Allokator, wird das zum Ergebnis und nicht zum Absturz: das
    Gate prueft ab Phase 3 ein LLM, und "die Antwort war unbrauchbar" ist
    dort ein Normalfall. Ein durchgereichter Stacktrace wuerde den gesamten
    Vergleich verlieren, inklusive der Baselines, die sauber gelaufen sind.
    """
    windows: list[GateWindow] = []
    for cut in geometry:
        try:
            result = run_portfolio_backtest(
                make_strategies(),
                cut.bars,
                factory(),
                risk=make_risk(),
                cfg=cfg,
                allocate_every=allocate_every,
                fill_model=fill_model,
            )
        except Exception as exc:  # noqa: BLE001 -- siehe Docstring
            return GateEntry(
                allocator=name,
                metrics=compute(pd.Series(dtype=float), metrics_tf),
                is_baseline=is_baseline,
                error=f"Fenster {cut.index}: {type(exc).__name__}: {exc}",
            )
        windows.append(_score_window(cut, result, cfg, metrics_tf))

    equity = _chain(windows, cfg.initial_cash)
    metrics = compute(
        equity.set_index("ts")["equity"],
        metrics_tf,
        n_trades=sum(w.metrics.n_trades for w in windows),
        turnover=sum(w.metrics.turnover for w in windows),
        fees_paid=sum(w.metrics.fees_paid for w in windows),
    )
    return GateEntry(
        allocator=name,
        metrics=metrics,
        is_baseline=is_baseline,
        windows=windows,
        equity=equity,
    )


def _score_window(
    cut: _Cut, result: PortfolioResult, cfg: BacktestConfig, metrics_tf: str
) -> GateWindow:
    """Nur den Teil ab Testbeginn bewerten."""
    # Die Equity-Kurve ist nach Bar-**Close**-Zeiten indiziert, die
    # Fenstergrenzen sind Open-Zeiten. Deshalb wird auf den Close des ersten
    # Testbars umgerechnet statt gegen `test_start` verglichen -- ein
    # Off-by-one hier traegt genau einen Bar Lookahead in jedes Fenster.
    cutoff = min(
        bar.close_ts
        for stream in cut.bars.values()
        for bar in stream
        if bar.ts >= cut.test_start
    )
    oos_equity = _oos_segment(
        result.equity.set_index("ts")["equity"], cutoff, cfg.initial_cash
    )

    # Fills tragen die Open-Zeit ihres Ausfuehrungsbars: der erste OOS-Fill
    # liegt exakt auf `test_start`.
    fills = [fill for fill in result.fills if fill.ts >= cut.test_start]

    return GateWindow(
        index=cut.index,
        test_start=cut.test_start,
        test_end=cut.test_end,
        metrics=compute(
            oos_equity,
            metrics_tf,
            n_trades=len(fills),
            turnover=sum(fill.notional for fill in fills),
            fees_paid=sum(fill.fee for fill in fills),
        ),
        oos_equity=oos_equity,
    )


def _timeline(bars: BarSet) -> list[datetime]:
    """Gemeinsame Zeitachse: sortierte Vereinigung aller Bar-Open-Zeiten.

    Ueber alle Symbole und Timeframes hinweg gezaehlt, damit jeder Lauf
    dieselben Fenstergrenzen sieht. Je Symbol zu zaehlen wuerde bei einer
    Datenluecke ein zeitlich verschobenes Testfenster ergeben.
    """
    return sorted({bar.ts for stream in bars.values() for bar in stream})


def _warmup_slots(
    strategies: dict[str, Strategy],
    allocators: list[Allocator],
    bars: BarSet,
    slot_seconds: int,
) -> int:
    """Vorlauf vor dem Testbeginn, in Slots der gemeinsamen Zeitachse.

    Zwei Warmups liegen hintereinander, nicht nebeneinander: die Strategien
    brauchen ihren Vorlauf, bevor sie ueberhaupt ein Signal geben -- und erst
    danach entstehen die Papier-Renditen, aus denen ein Allokator wie
    VolParity seine Schaetzung zieht. Vorher enthaelt `ctx.returns` nur
    Nullen, und jeder lookback-basierte Allokator faellt auf
    Gleichgewichtung zurueck.

    Deshalb wird der Vorlauf ueber **alle** Teilnehmer gebildet und nicht je
    Allokator: bekaeme ein Allokator mit langem Lookback einen laengeren
    Vorlauf, saehe er andere Bars als die anderen und der Vergleich waere
    hinueber. Der Preis ist ein laengerer Vorlauf fuer alle -- also
    verbrauchte Historie, nicht verzerrte Ergebnisse.

    Der Allokator-Anteil ist bei `RETURN_HISTORY` gedeckelt: die Engine haelt
    nicht mehr Papier-Renditen vor, laengerer Vorlauf brauchte also nichts.
    """
    per_strategy = [
        math.ceil(s.warmup_bars * timeframe_seconds(s.timeframe) / slot_seconds)
        for s in strategies.values()
    ]
    alloc_tf = max({tf for _, tf in bars}, key=timeframe_seconds)
    per_allocator = [
        math.ceil(
            min(a.warmup_bars, RETURN_HISTORY) * timeframe_seconds(alloc_tf) / slot_seconds
        )
        for a in allocators
    ]
    return max(per_strategy, default=0) + max(per_allocator, default=0)


def _check_geometry(
    train_bars: int, test_bars: int, step_bars: int, embargo_bars: int
) -> None:
    """Fenstermasse pruefen, bevor irgendetwas gerechnet wird."""
    for name, value in (
        ("train_bars", train_bars),
        ("test_bars", test_bars),
        ("step_bars", step_bars),
    ):
        if value < 1:
            raise ValueError(f"{name} muss mindestens 1 sein, ist {value}.")
    if embargo_bars < 0:
        raise ValueError(f"embargo_bars darf nicht negativ sein, ist {embargo_bars}.")
    if step_bars < test_bars:
        raise ValueError(
            f"step_bars={step_bars} < test_bars={test_bars} ergibt ueberlappende "
            "Testfenster. Die verkettete OOS-Kurve wuerde denselben Zeitraum "
            "mehrfach zaehlen -- und beide Teilnehmer gleichermassen falsch "
            "bewerten, was den Vergleich nicht rettet, sondern nur verschleiert."
        )


def _check_data(
    n_bars: int, train_bars: int, test_bars: int, embargo_bars: int, warmup_slots: int
) -> None:
    """Reichen die Daten fuer mindestens ein Fenster?"""
    if train_bars + embargo_bars < warmup_slots:
        raise InsufficientDataError(
            f"Warmup von Strategien und Allokatoren ({warmup_slots} Slots) passt "
            f"nicht vor das Testfenster: train_bars={train_bars} + "
            f"embargo_bars={embargo_bars} = {train_bars + embargo_bars}. "
            f"train_bars auf mindestens {max(1, warmup_slots - embargo_bars)} setzen "
            "oder Allokatoren mit kuerzerem Lookback antreten lassen -- sonst "
            "startet der Vergleich, bevor die Allokatoren ueberhaupt rechnen."
        )
    needed = train_bars + embargo_bars + test_bars
    if n_bars < needed:
        raise InsufficientDataError(
            f"Zu wenig Bars fuer ein Fenster: {n_bars} vorhanden, {needed} noetig "
            f"(train {train_bars} + embargo {embargo_bars} + test {test_bars})."
        )


# ---------------------------------------------------------------------------
# Fabriken
# ---------------------------------------------------------------------------


def _allocator_factory(source: AllocatorSource, label: str) -> Callable[[], Allocator]:
    """Aus Instanz oder Fabrik eine Fabrik machen, die frische Objekte liefert."""
    if isinstance(source, Allocator):
        template = source
        return lambda: copy.deepcopy(template)
    if not callable(source):
        raise TypeError(
            f"{label} muss ein Allocator oder eine Fabrik sein, nicht "
            f"{type(source).__name__}."
        )
    first = source()
    if not isinstance(first, Allocator):
        raise TypeError(
            f"{label}-Fabrik muss einen Allocator liefern, nicht "
            f"{type(first).__name__}."
        )
    if source() is first:
        raise ValueError(
            f"{label}-Fabrik gibt dieselbe Instanz zurueck. Jedes Fenster braucht "
            "einen frischen Allokator, sonst traegt sein Zustand (Gespraechs"
            "verlauf, letzte Allokation, Cache) das Ende eines Fensters in den "
            "Anfang des naechsten -- und das Leck ist im Ergebnis unsichtbar."
        )
    return source


def _strategy_factory(source: StrategySource) -> Callable[[], dict[str, Strategy]]:
    """Dasselbe fuer den Strategiesatz.

    Geprueft wird nicht nur, dass das Dict neu ist, sondern dass keine
    einzelne Strategie geteilt wird: `lambda: {"a": a, "b": b}` liefert bei
    jedem Aufruf ein neues Dict und trotzdem dieselben Instanzen.
    """
    if isinstance(source, dict):
        template = source
        if not template:
            raise ValueError("Keine Strategien uebergeben.")
        return lambda: copy.deepcopy(template)
    if not callable(source):
        raise TypeError(
            f"strategies muss ein dict oder eine Fabrik sein, nicht "
            f"{type(source).__name__}."
        )
    first = source()
    if not isinstance(first, dict) or not first:
        raise TypeError("strategies-Fabrik muss ein nicht-leeres dict liefern.")
    second = source()
    shared = [sid for sid, s in first.items() if second.get(sid) is s]
    if shared:
        raise ValueError(
            f"strategies-Fabrik teilt Instanzen ueber Aufrufe hinweg: {sorted(shared)}. "
            "Jedes Fenster braucht frische Strategien, sonst traegt self._state "
            "(Position, Stop, Zaehler) das Ende eines Fensters in den Anfang des "
            "naechsten. Statt lambda: {'a': a} -> lambda: {'a': Strategie(...)}."
        )
    return source


def _risk_factory(source: RiskSource) -> Callable[[], RiskLimits | None]:
    """Risk-Engine je Lauf frisch.

    Ihr Kill-Switch ist bewusst klebrig und kennt keine automatische
    Entsperrung. Eine geteilte Instanz waere deshalb der schlimmste Fall
    ueberhaupt: der erste Halt in Fenster 1 legt alle folgenden Fenster und
    alle folgenden Allokatoren still, und die Tabelle zeigte lauter Nullen,
    ohne dass irgendetwas fehlschlaegt.
    """
    if source is None:
        return lambda: None
    if isinstance(source, RiskLimits):
        template = source
        return lambda: copy.deepcopy(template)
    if not callable(source):
        raise TypeError(
            f"risk muss RiskLimits, eine Fabrik oder None sein, nicht "
            f"{type(source).__name__}."
        )
    return source


def _baseline_factories(
    baselines: list[AllocatorSource] | Callable[[], list[Allocator]] | None,
) -> list[Callable[[], Allocator]]:
    """Die Massstaebe. Ohne Angabe das Standardfeld aus ADR-004."""
    if baselines is None:
        sources: list[AllocatorSource] = list(default_set())
    elif callable(baselines):
        sources = list(baselines())
    else:
        sources = list(baselines)
    return [
        _allocator_factory(source, f"baseline[{i}]") for i, source in enumerate(sources)
    ]


def _unique(name: str, used: set[str]) -> str:
    """Namen eindeutig halten, ohne einen davon zu verstecken.

    Ein Kandidat darf denselben Namen tragen wie eine Baseline -- etwa beim
    Test "schlaegt sich ein Allokator selbst?". In der Tabelle muessen die
    beiden Zeilen trotzdem unterscheidbar bleiben.
    """
    candidate = name
    suffix = 2
    while candidate in used:
        candidate = f"{name} #{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _shorten(name: str, width: int) -> str:
    return name if len(name) <= width else name[: width - 1] + "~"


def _cell(value: float, width: int, spec: str) -> str:
    """Eine Zahl in fester Spaltenbreite, notfalls in Exponentialschreibweise.

    Ein einziger Ausreisser -- ein Calmar aus einem sehr kurzen Testfenster
    etwa -- sprengt sonst die Spaltenbreite und verschiebt alles rechts davon.
    Eine Tabelle, deren Spalten nicht mehr untereinander stehen, wird nicht
    gelesen, und dann ist die ganze Gegenueberstellung umsonst.
    """
    text = format(value, spec)
    if len(text) > width:
        text = format(float(value), ".1e")
    return f"{text:>{width}}"
