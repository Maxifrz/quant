"""Walk-Forward-Analyse mit Purging und Embargo.

Jede Zahl aus Phase 1 ist In-Sample: Strategie, Parameter und Kostenannahmen
wurden auf demselben Zeitraum betrachtet, ueber den anschliessend berichtet
wurde. Solche Zahlen sagen nichts ueber die Zukunft. Belastbar wird es erst,
wenn jedes Ergebnis aus einem Fenster stammt, das bei seiner Entstehung noch
nicht sichtbar war -- und wenn die Fenster sauber getrennt sind.

Geometrie eines Fensters, in Bars gezaehlt:

    |<------- train_bars ------->|<- embargo ->|<---- test_bars ---->|
    ^                            ^             ^                     ^
    train_start              train_end     test_start             test_end
                  |<- warmup ->|
                  ^
                  hier startet der Lauf -- gezaehlt wird erst ab test_start

Vier Stellen, an denen so etwas ueblicherweise falsch gebaut wird:

1. **Eine Strategie-Instanz fuer alle Fenster.** `self._state` (offene
   Position, Trailing-Stop, Zaehler) traegt dann das Ende von Fenster i in
   den Anfang von Fenster i+1. Das ist Leakage, und sie ist im Ergebnis
   unsichtbar. Deshalb nimmt `walk_forward` eine **Fabrik** statt einer
   Instanz und prueft beim Start, dass sie wirklich neue Objekte liefert.

2. **Warmup im Testfenster.** Die Strategie liefert erst nach
   `warmup_bars` Bars ein Signal. Laesst man das Testfenster isoliert
   laufen, verbrennt dessen Anfang mit Warmup -- bei 55 Bars Donchian und
   250 Bars Test sind das 22% des Fensters, in denen nicht gehandelt wird,
   und das Ergebnis haengt an der Fenstergroesse statt an der Strategie.
   Hier startet der Lauf deshalb bei `test_start - warmup_bars`, und nur
   der Teil ab `test_start` geht in die OOS-Kurve ein. Das ist kein
   Lookahead: ein live laufendes System haette diese Bars zu diesem
   Zeitpunkt ebenfalls gekannt.

3. **Kein Embargo.** Features sind rollierend (ATR-20, Donchian-55); ein
   Bar unmittelbar nach dem Train-Ende steckt noch in denselben Fenstern
   wie die letzten Train-Bars. Ohne Abstand leckt Information ueber die
   Grenze. `embargo_bars` schiebt den Testbeginn nach hinten (Lopez de
   Prado, "Advances in Financial Machine Learning", Kap. 7).

4. **Equity-Werte aneinandergehaengt statt Renditen verkettet.** Jedes
   Fenster startet mit `initial_cash`. Haengt man die absoluten Kurven
   hintereinander, springt die Kurve an jeder Fenstergrenze auf 100k
   zurueck -- jede daraus berechnete Vola, jeder Drawdown ist Muell.
   Verkettet werden deshalb die *Renditen* der Segmente.

Purging: es wird hier (noch) nichts optimiert -- die Strategieparameter sind
fest, ein Parameter-Suchlauf kommt erst mit Phase 5. Das Train-Fenster ist
darum aktuell reservierter Platz und keine Anpassung: es legt fest, wo das
Testfenster beginnt, und gibt Warmup und Embargo einen definierten Ort.
Sobald optimiert wird, ist die Fenstergeometrie bereits da und die OOS-Zahlen
bleiben vergleichbar. Die Fabrik wird dann von `Callable[[], Strategy]` zu
"nimm die Train-Bars, gib eine angepasste Strategie zurueck" erweitert; alles
andere in dieser Datei bleibt stehen.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from qt.backtest.engine import BacktestResult, run_backtest
from qt.backtest.metrics import Metrics, compute
from qt.core.config import BacktestConfig
from qt.core.types import Bar
from qt.strategy.base import Strategy

# Liefert bei jedem Aufruf eine **neue** Strategie-Instanz. Siehe Punkt 1 oben.
StrategyFactory = Callable[[], Strategy]


class InsufficientDataError(ValueError):
    """Die Daten reichen fuer die verlangte Fenstergeometrie nicht aus.

    Eigener Typ, damit ein Screening-Lauf ueber viele Kandidaten diesen Fall
    von echten Fehlern unterscheiden kann, ohne Fehlertexte zu lesen.
    """


@dataclass
class WalkForwardWindow:
    """Ein Fenster. Alle Zeitpunkte sind Bar-**Open**-Zeiten, Enden inklusive.

    `result` ist der Lauf ueber Warmup *und* Test -- die Kennzahlen darin
    beziehen sich also auf einen laengeren Zeitraum als das Testfenster.
    Bewertet wird ausschliesslich `oos_equity` bzw. `metrics`.

    `oos_equity` ist nach Bar-**Close**-Zeiten indiziert, wie
    `BacktestResult.equity`. Sie beginnt mit einem Referenzpunkt -- dem
    letzten Stand vor dem Testfenster, auf `initial_cash` normiert. Ohne ihn
    fiele die Bewegung des ersten Testbars aus der Renditereihe heraus, weil
    `pct_change` den ersten Wert verwirft, und genau dort steckt der
    Einstieg. Die Reihe hat deshalb `test_bars + 1` Punkte.
    """

    index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    warmup_start: datetime
    result: BacktestResult
    metrics: Metrics
    oos_equity: pd.Series

    @property
    def n_trades(self) -> int:
        return self.metrics.n_trades


@dataclass
class WalkForwardResult:
    """Ergebnis aller Fenster plus die verkettete OOS-Kurve.

    `equity` ist die einzige Kurve, aus der man Aussagen ableiten darf:
    Spalten `ts`, `equity`, `window` -- letztere macht die Fenstergrenzen im
    Chart und im Test wiederfindbar.
    """

    windows: list[WalkForwardWindow]
    equity: pd.DataFrame
    metrics: Metrics
    strategy: str
    symbols: list[str]
    timeframe: str
    config: BacktestConfig
    train_bars: int
    test_bars: int
    step_bars: int
    embargo_bars: int
    warmup_bars: int
    unused_tail_bars: int

    @property
    def n_windows(self) -> int:
        return len(self.windows)

    @property
    def oos_bars(self) -> int:
        return sum(w.metrics.n_bars - 1 for w in self.windows)

    def window_frame(self) -> pd.DataFrame:
        """Kennzahlen je Fenster nebeneinander.

        Der eigentliche Ertrag der Walk-Forward-Analyse steckt weniger im
        Gesamtsharpe als in der Streuung darueber: eine Strategie, die in
        einem von sechs Fenstern alles verdient, ist eine Zufallsstichprobe
        und keine Kante.
        """
        return pd.DataFrame(
            [
                {
                    "window": w.index,
                    "test_start": w.test_start,
                    "test_end": w.test_end,
                    "return": w.metrics.total_return,
                    "sharpe": w.metrics.sharpe,
                    "max_dd": w.metrics.max_drawdown,
                    "trades": w.metrics.n_trades,
                    "turnover": w.metrics.turnover,
                    "fees": w.metrics.fees_paid,
                }
                for w in self.windows
            ]
        )


def walk_forward(
    make_strategy: StrategyFactory,
    bars: dict[str, list[Bar]],
    train_bars: int,
    test_bars: int,
    step_bars: int | None = None,
    embargo_bars: int = 0,
    cfg: BacktestConfig | None = None,
) -> WalkForwardResult:
    """Rollierende Out-of-Sample-Fenster ueber `bars` laufen lassen.

    `make_strategy` ist eine Fabrik, keine Instanz: jedes Fenster bekommt
    eine frische Strategie, sonst wandert Zustand ueber die Fenstergrenze.

    `bars` bildet Symbol -> chronologische Bar-Liste ab, wie bei
    `run_backtest`. Gezaehlt wird in Slots der gemeinsamen Zeitachse (die
    sortierte Vereinigung aller Bar-Zeiten), damit mehrere Symbole dieselben
    Fenstergrenzen sehen.

    `step_bars` ist per Default `test_bars`, die Testfenster liegen dann
    lueckenlos hintereinander. Groessere Schritte lassen Luecken -- das ist
    erlaubt. Kleinere waeren ueberlappende Testfenster; die verkettete Kurve
    wuerde denselben Zeitraum doppelt zaehlen, deshalb werden sie abgelehnt.

    `embargo_bars` Bars zwischen Train- und Testfenster bleiben ungenutzt.

    Reicht der Rest am Ende nicht fuer ein volles Fenster, wird er verworfen
    (`unused_tail_bars`) -- ein kuerzeres letztes Testfenster waere mit den
    anderen nicht vergleichbar, und genau dieser Vergleich ist der Zweck.

    Wirft `InsufficientDataError`, wenn die Daten nicht reichen.
    """
    cfg = cfg or BacktestConfig()
    step_bars = test_bars if step_bars is None else step_bars
    _check_geometry(train_bars, test_bars, step_bars, embargo_bars)

    probe = _probe(make_strategy)
    warmup = probe.warmup_bars
    timeline = _timeline(bars)
    _check_data(len(timeline), train_bars, test_bars, embargo_bars, warmup)

    span = train_bars + embargo_bars + test_bars
    starts = range(0, len(timeline) - span + 1, step_bars)

    windows = [
        _run_window(
            index=index,
            start=start,
            timeline=timeline,
            bars=bars,
            make_strategy=make_strategy,
            cfg=cfg,
            probe=probe,
            train_bars=train_bars,
            test_bars=test_bars,
            embargo_bars=embargo_bars,
        )
        for index, start in enumerate(starts)
    ]

    equity = chain_returns(windows, cfg.initial_cash)
    metrics = compute(
        equity.set_index("ts")["equity"],
        probe.timeframe,
        n_trades=sum(w.metrics.n_trades for w in windows),
        turnover=sum(w.metrics.turnover for w in windows),
        fees_paid=sum(w.metrics.fees_paid for w in windows),
    )

    return WalkForwardResult(
        windows=windows,
        equity=equity,
        metrics=metrics,
        strategy=probe.describe(),
        symbols=sorted(bars),
        timeframe=probe.timeframe,
        config=cfg,
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
        embargo_bars=embargo_bars,
        warmup_bars=warmup,
        unused_tail_bars=len(timeline) - (starts[-1] + span),
    )


def _run_window(
    index: int,
    start: int,
    timeline: list[datetime],
    bars: dict[str, list[Bar]],
    make_strategy: StrategyFactory,
    cfg: BacktestConfig,
    probe: Strategy,
    train_bars: int,
    test_bars: int,
    embargo_bars: int,
) -> WalkForwardWindow:
    """Ein einzelnes Fenster rechnen: Lauf ab Warmup, gewertet ab Testbeginn."""
    test_lo = start + train_bars + embargo_bars
    test_hi = test_lo + test_bars - 1
    run_lo = test_lo - probe.warmup_bars

    test_start = timeline[test_lo]
    run_bars = {
        symbol: window
        for symbol, stream in bars.items()
        if (window := slice_bars(stream, timeline[run_lo], timeline[test_hi]))
    }
    result = run_backtest(make_strategy(), run_bars, cfg)

    # Der Lauf ist nach Bar-**Close**-Zeiten indiziert -- das ist der Moment,
    # zu dem die Information vorliegt. Die Fenstergrenzen sind Open-Zeiten,
    # also wird hier auf den Close des ersten Testbars umgerechnet statt auf
    # test_start verglichen. Ein Off-by-one hier wuerde genau einen Bar
    # Lookahead in jedes Fenster tragen.
    cutoff = min(
        bar.close_ts
        for stream in run_bars.values()
        for bar in stream
        if bar.ts >= test_start
    )
    oos_equity = oos_segment(
        result.equity.set_index("ts")["equity"], cutoff, cfg.initial_cash
    )

    # Fills tragen die Open-Zeit ihres Ausfuehrungsbars, nicht dessen Close:
    # der erste OOS-Fill liegt exakt auf test_start.
    fills = [fill for fill in result.fills if fill.ts >= test_start]

    return WalkForwardWindow(
        index=index,
        train_start=timeline[start],
        train_end=timeline[start + train_bars - 1],
        test_start=test_start,
        test_end=timeline[test_hi],
        warmup_start=timeline[run_lo],
        result=result,
        metrics=compute(
            oos_equity,
            probe.timeframe,
            n_trades=len(fills),
            turnover=sum(fill.notional for fill in fills),
            fees_paid=sum(fill.fee for fill in fills),
        ),
        oos_equity=oos_equity,
    )


def oos_segment(equity: pd.Series, cutoff: pd.Timestamp, initial_cash: float) -> pd.Series:
    """Den Teil ab `cutoff`, normiert auf den letzten Stand davor.

    Der Referenzpunkt ist der Warmup-Ausgang: dort ist noch nichts gehandelt,
    das Eigenkapital steht auf `initial_cash`. Er bleibt als erster Punkt in
    der Reihe, damit die Bewegung des ersten Testbars in den Renditen
    auftaucht -- `pct_change` verwirft sonst genau diesen ersten Schritt, und
    der enthaelt den Einstieg.
    """
    before = equity.loc[equity.index < cutoff]
    after = equity.loc[equity.index >= cutoff]
    if after.empty:
        raise InsufficientDataError("Testfenster ohne Bars -- Zeitachse pruefen.")

    base = float(before.iloc[-1]) if len(before) else initial_cash
    if base <= 0:
        raise InsufficientDataError(
            "Eigenkapital am Testbeginn ist null oder negativ -- kein sinnvoller Bezugswert."
        )

    segment = after if before.empty else pd.concat([before.iloc[-1:], after])
    return segment / base * initial_cash


def chain_returns(windows: list[WalkForwardWindow], initial_cash: float) -> pd.DataFrame:
    """OOS-Segmente ueber ihre Renditen verketten.

    Multipliziert wird, nicht angehaengt: jedes Segment startet bei
    `initial_cash`, sein Verlauf ist also ein Wachstumsfaktor. Der
    Referenzpunkt jedes Segments faellt dabei weg (ausser beim ersten), denn
    er ist definitionsgemaess der Stand, den die Kurve schon hat -- er wuerde
    einen Bar mit Rendite null einfuegen und die Vola verwaessern.
    """
    level = initial_cash
    frames: list[pd.DataFrame] = []

    for window in windows:
        segment = window.oos_equity / initial_cash * level
        if frames:
            segment = segment.iloc[1:]
        level = float(segment.iloc[-1])
        frames.append(
            pd.DataFrame(
                {"ts": segment.index, "equity": segment.to_numpy(), "window": window.index}
            )
        )

    return pd.concat(frames, ignore_index=True)


def _timeline(bars: dict[str, list[Bar]]) -> list[datetime]:
    """Gemeinsame Zeitachse: sortierte Vereinigung aller Bar-Open-Zeiten.

    Ueber Symbole hinweg zu zaehlen statt je Symbol haelt die Fenstergrenzen
    identisch. Zaehlte man je Symbol, bekaeme ein Symbol mit Datenluecke ein
    zeitlich verschobenes Testfenster und die Segmente liessen sich nicht
    mehr zu einer Portfoliokurve verketten.
    """
    if not bars or all(not stream for stream in bars.values()):
        raise InsufficientDataError("Keine Bars uebergeben.")
    return sorted({bar.ts for stream in bars.values() for bar in stream})


def slice_bars(stream: list[Bar], start: datetime, end: datetime) -> list[Bar]:
    """Bars mit Open-Zeit in [start, end], Grenzen inklusive."""
    return [bar for bar in stream if start <= bar.ts <= end]


def _probe(make_strategy: StrategyFactory) -> Strategy:
    """Instanz fuer Warmup, Timeframe und Beschreibung -- und die Fabrikprobe.

    `lambda: strategy` sieht aus wie eine Fabrik, teilt aber eine Instanz
    ueber alle Fenster. Der Effekt waere nicht sichtbar, sondern nur zu gute
    OOS-Zahlen. Ein Fehler beim Start ist billiger als eine Strategie, die
    aufgrund geliehenen Zustands live geht.
    """
    first = make_strategy()
    if not isinstance(first, Strategy):
        raise TypeError(
            f"make_strategy muss eine Strategy liefern, nicht {type(first).__name__}. "
            "Erwartet wird eine Fabrik, z.B. lambda: DonchianTrend(symbols, tf)."
        )
    if make_strategy() is first:
        raise ValueError(
            "make_strategy gibt dieselbe Instanz zurueck. Jedes Fenster braucht "
            "eine frische Strategie, sonst traegt self._state (Position, Stop, "
            "Zaehler) das Ende eines Fensters in den Anfang des naechsten. "
            "Statt lambda: strategy -> lambda: Strategie(symbols, timeframe)."
        )
    return first


def _check_geometry(
    train_bars: int, test_bars: int, step_bars: int, embargo_bars: int
) -> None:
    """Fenstermasse pruefen, bevor irgendetwas gerechnet wird."""
    sizes = (("train_bars", train_bars), ("test_bars", test_bars), ("step_bars", step_bars))
    for name, value in sizes:
        if value < 1:
            raise ValueError(f"{name} muss mindestens 1 sein, ist {value}.")
    if embargo_bars < 0:
        raise ValueError(f"embargo_bars darf nicht negativ sein, ist {embargo_bars}.")
    if step_bars < test_bars:
        raise ValueError(
            f"step_bars={step_bars} < test_bars={test_bars} ergibt ueberlappende "
            "Testfenster. Die verkettete OOS-Kurve wuerde denselben Zeitraum "
            "mehrfach zaehlen und Sharpe wie Drawdown verfaelschen."
        )


def _check_data(
    n_bars: int,
    train_bars: int,
    test_bars: int,
    embargo_bars: int,
    warmup_bars: int,
) -> None:
    """Genug Daten fuer mindestens ein Fenster? Sonst laut abbrechen.

    Ein stiller Leerlauf waere hier besonders teuer: eine leere Fensterliste
    sieht in einem Screening-Lauf aus wie "Strategie hat nichts gehandelt"
    und nicht wie "Zeitraum war zu kurz".
    """
    if train_bars + embargo_bars < warmup_bars:
        raise InsufficientDataError(
            f"Warmup der Strategie ({warmup_bars} Bars) passt nicht vor das Testfenster: "
            f"train_bars={train_bars} + embargo_bars={embargo_bars} = "
            f"{train_bars + embargo_bars}. train_bars auf mindestens "
            f"{max(1, warmup_bars - embargo_bars)} setzen -- ein Train-Fenster kuerzer "
            "als der Warmup enthaelt ohnehin kein einziges gueltiges Signal."
        )

    needed = train_bars + embargo_bars + test_bars
    if n_bars < needed:
        raise InsufficientDataError(
            f"Zu wenig Bars fuer ein Fenster: {n_bars} vorhanden, {needed} noetig "
            f"(train {train_bars} + embargo {embargo_bars} + test {test_bars}). "
            f"Es fehlen {needed - n_bars} Bars -- Zeitraum verlaengern, kleineren "
            "Timeframe waehlen oder die Fenster verkleinern."
        )


# ---------------------------------------------------------------------------
# Rueckwaertskompatible Namen
# ---------------------------------------------------------------------------
#
# Diese drei Funktionen sind die einzige Definition von "OOS-Segment",
# "Verkettung ueber Renditen" und "Bar-Ausschnitt" im System -- `qt.portfolio.gate`
# baut darauf auf. Sie waren mit Unterstrich benannt, was jeden Aufrufer
# ausserhalb dieses Moduls wie einen Regelbruch aussehen liess. Die alten Namen
# bleiben, damit nichts bricht.
_oos_segment = oos_segment
_chain = chain_returns
_slice = slice_bars
