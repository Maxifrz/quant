"""Walk-Forward: die Trennung zwischen den Fenstern.

Eine Walk-Forward-Analyse, die leckt, ist schlimmer als gar keine: sie
liefert Zahlen mit dem Etikett "out-of-sample", denen man dann glaubt. Die
Lecks sind alle leise -- eine geteilte Strategie-Instanz, ein Warmup, der im
Testfenster verbrennt, ein fehlendes Embargo, aneinandergehaengte statt
verkettete Kurven. Keines davon wirft einen Fehler, alle vier verschieben
das Ergebnis. Deshalb steht hier fuer jedes ein Test.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from qt.backtest.engine import run_backtest
from qt.backtest.walkforward import (
    InsufficientDataError,
    WalkForwardResult,
    walk_forward,
)
from qt.core.config import BacktestConfig, CostConfig
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy
from qt.strategy.registry import get, load_library
from tests.conftest import make_bars

load_library()

FREE = BacktestConfig(costs=CostConfig(taker_fee_bps=0, half_spread_bps=0, slippage_bps=0))
SYMBOL = "BTC/USD"
HOUR = timedelta(hours=1)


class AlwaysLong(Strategy):
    name = "alwayslong"

    @property
    def warmup_bars(self) -> int:
        return 5

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        return 1.0


class AlwaysFlat(Strategy):
    name = "alwaysflat"

    @property
    def warmup_bars(self) -> int:
        return 5

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        return 0.0


class Counting(Strategy):
    """Gewicht haengt ausschliesslich an einem Zaehler in `self._state`.

    Genau die Bauform, die ueber eine geteilte Instanz leckt: das Ergebnis
    haengt daran, wieviele Bars die Instanz vorher gesehen hat, und nicht am
    Markt. Damit wird ein Leck zwischen Fenstern sichtbar, statt sich in
    Nachkommastellen zu verstecken.
    """

    name = "counting"

    @property
    def warmup_bars(self) -> int:
        return 5

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        seen = self._state.get("seen", 0) + 1
        self._state["seen"] = seen
        return 1.0 if (seen // 10) % 2 == 0 else -1.0


class SlowStarter(Strategy):
    name = "slowstarter"

    @property
    def warmup_bars(self) -> int:
        return 50

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        return 1.0


def long_factory(symbols: list[str] | None = None):
    symbols = symbols or [SYMBOL]
    return lambda: AlwaysLong(list(symbols), "1h")


def run_default(
    n: int = 600,
    train_bars: int = 200,
    test_bars: int = 100,
    step_bars: int | None = None,
    embargo_bars: int = 0,
    seed: int = 7,
) -> WalkForwardResult:
    bars = {SYMBOL: make_bars(n, seed=seed)}
    return walk_forward(
        long_factory(),
        bars,
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
        embargo_bars=embargo_bars,
        cfg=FREE,
    )


def slice_bars(bars: list, start, end) -> list:
    return [bar for bar in bars if start <= bar.ts <= end]


def test_windows_tile_the_span_without_overlap():
    """Testfenster liegen lueckenlos hintereinander und ueberschneiden sich nie.

    Ueberlappende Testfenster wuerden denselben Zeitraum mehrfach in die
    Gesamtkurve tragen und jede Kennzahl daraus aufblasen.
    """
    result = run_default(n=600, train_bars=200, test_bars=100)
    bars = make_bars(600, seed=7)

    assert result.n_windows == 4
    assert result.unused_tail_bars == 0

    for window in result.windows:
        assert window.train_start < window.train_end < window.test_start <= window.test_end
        assert window.metrics.n_bars == 100 + 1, "Referenzpunkt plus test_bars"

    for previous, current in zip(result.windows, result.windows[1:]):
        assert current.test_start > previous.test_end
        assert current.train_start == previous.train_start + 100 * HOUR

    assert result.windows[0].test_start == bars[200].ts
    assert result.windows[-1].test_end == bars[599].ts
    assert result.oos_bars == 4 * 100


def test_incomplete_tail_is_reported_not_silently_shortened():
    """Ein kuerzeres letztes Fenster waere mit den anderen nicht vergleichbar."""
    result = run_default(n=650, train_bars=200, test_bars=100)

    assert result.n_windows == 4
    assert result.unused_tail_bars == 50


def test_embargo_shifts_test_start_and_leaves_the_gap_unused():
    """Das Embargo verschiebt den Testbeginn, nicht das Trainingsende.

    Rollierende Features (ATR, Donchian) reichen ueber das Trainingsende
    hinaus; ohne Abstand steckt der erste Testbar noch in denselben Fenstern
    wie die letzten Trainingsbars.
    """
    without = run_default(embargo_bars=0)
    with_embargo = run_default(embargo_bars=10)

    first_without, first_with = without.windows[0], with_embargo.windows[0]

    assert first_with.train_end == first_without.train_end
    assert first_with.test_start - first_without.test_start == 10 * HOUR
    assert first_with.test_start - first_with.train_end == 11 * HOUR, "10 Bars Luecke"
    assert first_with.test_end - first_with.test_start == 99 * HOUR, "Test gleich lang"


def test_warmup_is_taken_from_before_the_test_window():
    """Der Warmup liegt vor `test_start` -- das Testfenster wird voll gehandelt.

    Laeuft die Strategie erst ab `test_start` an, verbrennt sie die ersten
    `warmup_bars` Bars jedes Fensters ohne Position. Das Ergebnis haengt dann
    an der Fenstergroesse statt an der Strategie.
    """
    bars = make_bars(600, seed=7)
    result = run_default(n=600)
    window = result.windows[0]

    assert window.warmup_start == window.test_start - 5 * HOUR
    assert window.oos_equity.index[0] == pd.Timestamp(window.test_start)
    assert window.oos_equity.index[1] == pd.Timestamp(window.test_start + HOUR)
    assert float(window.oos_equity.iloc[0]) == pytest.approx(FREE.initial_cash)

    # Der erste Fill liegt auf dem Open des ersten Testbars: die Strategie war
    # bereits warm, als das Testfenster begann.
    assert min(fill.ts for fill in window.result.fills) == window.test_start

    naive = run_backtest(
        AlwaysLong([SYMBOL], "1h"),
        {SYMBOL: slice_bars(bars, window.test_start, window.test_end)},
        FREE,
    )
    assert min(fill.ts for fill in naive.fills) == window.test_start + 5 * HOUR


def test_state_does_not_leak_between_windows():
    """Jedes Fenster muss isoliert und in beliebiger Reihenfolge reproduzierbar sein.

    Wird dieselbe Strategie-Instanz wiederverwendet, traegt `self._state` das
    Ende von Fenster i in den Anfang von Fenster i+1 -- unsichtbar im
    Ergebnis, aber es ist Leakage.
    """
    bars = {SYMBOL: make_bars(600, seed=3)}
    factory = lambda: Counting([SYMBOL], "1h")  # noqa: E731
    result = walk_forward(factory, bars, train_bars=200, test_bars=100, cfg=FREE)

    for window in reversed(result.windows):
        standalone = run_backtest(
            factory(),
            {SYMBOL: slice_bars(bars[SYMBOL], window.warmup_start, window.test_end)},
            FREE,
        )
        np.testing.assert_array_equal(
            window.result.equity["equity"].to_numpy(),
            standalone.equity["equity"].to_numpy(),
        )

    # Gegenprobe: mit geteilter Instanz kommt tatsaechlich etwas anderes heraus.
    # Ohne sie wuerde der Test oben auch dann bestehen, wenn `Counting` gar
    # keinen Zustand traegt.
    leaky = factory()
    first, second = result.windows[0], result.windows[1]
    run_backtest(
        leaky, {SYMBOL: slice_bars(bars[SYMBOL], first.warmup_start, first.test_end)}, FREE
    )
    leaked = run_backtest(
        leaky, {SYMBOL: slice_bars(bars[SYMBOL], second.warmup_start, second.test_end)}, FREE
    )
    assert not np.array_equal(
        leaked.equity["equity"].to_numpy(), second.result.equity["equity"].to_numpy()
    )


def test_factory_returning_the_same_instance_is_rejected():
    """`lambda: strategy` sieht aus wie eine Fabrik und ist keine."""
    strategy = Counting([SYMBOL], "1h")

    with pytest.raises(ValueError, match="dieselbe Instanz"):
        walk_forward(
            lambda: strategy,
            {SYMBOL: make_bars(600)},
            train_bars=200,
            test_bars=100,
            cfg=FREE,
        )


def test_chained_curve_has_no_jumps_at_window_boundaries():
    """Verkettet werden Renditen, nicht Kontostaende.

    Haengt man die absoluten Kurven aneinander, faellt die Equity an jeder
    Fenstergrenze auf `initial_cash` zurueck. Der Sprung waere um
    Groessenordnungen groesser als jede echte Bar-Rendite -- genau darauf
    prueft dieser Test.
    """
    bars = make_bars(600, seed=7)
    result = run_default(n=600)

    equity = result.equity["equity"].to_numpy()
    windows = result.equity["window"].to_numpy()
    returns = np.abs(np.diff(equity) / equity[:-1])

    prices = pd.Series([bar.close for bar in bars])
    max_bar_move = float(prices.pct_change().abs().max())

    boundaries = np.flatnonzero(windows[1:] != windows[:-1])
    assert boundaries.size == result.n_windows - 1

    # Voll investiert ohne Hebel: keine Bar-Rendite kann groesser sein als die
    # Preisbewegung desselben Bars.
    assert returns[boundaries].max() <= max_bar_move * 1.05
    assert returns.max() <= max_bar_move * 1.05

    assert result.equity["ts"].is_monotonic_increasing
    assert result.equity["ts"].is_unique
    assert len(result.equity) == result.oos_bars + 1, "Startpunkt plus alle OOS-Bars"


def test_flat_strategy_gives_a_flat_chained_curve():
    """Ohne Position darf die Verkettung nirgends etwas erfinden."""
    result = walk_forward(
        lambda: AlwaysFlat([SYMBOL], "1h"),
        {SYMBOL: make_bars(600, seed=4)},
        train_bars=200,
        test_bars=100,
        cfg=FREE,
    )

    assert result.metrics.n_trades == 0
    np.testing.assert_allclose(
        result.equity["equity"].to_numpy(), FREE.initial_cash, rtol=1e-12
    )


def test_window_metrics_add_up_to_the_total():
    """Trades, Umsatz und Gebuehren der Fenster sind die des Gesamtergebnisses.

    Umschlagshaeufigkeit gehoert in jede Bewertung (ADR-009) -- sie darf beim
    Zusammensetzen der Fenster nicht verloren gehen.
    """
    result = walk_forward(
        lambda: get("trend")([SYMBOL], "1h"),
        {SYMBOL: make_bars(900, seed=21)},
        train_bars=300,
        test_bars=150,
        embargo_bars=12,
    )

    assert result.metrics.n_trades == sum(w.metrics.n_trades for w in result.windows)
    assert result.metrics.turnover == pytest.approx(
        sum(w.metrics.turnover for w in result.windows)
    )
    assert result.metrics.fees_paid > 0, "Mit Default-Kosten muss gezahlt werden"
    assert len(result.window_frame()) == result.n_windows


def test_multiple_symbols_share_one_window_grid():
    """Zwei Symbole muessen dieselben Fenstergrenzen sehen.

    Zaehlte man Bars je Symbol, bekaeme ein Symbol mit Datenluecke ein
    zeitlich verschobenes Testfenster -- die Segmente liessen sich dann nicht
    mehr zu einer Portfoliokurve verketten.
    """
    bars = {
        SYMBOL: make_bars(500, symbol=SYMBOL, seed=1),
        "ETH/USD": make_bars(500, symbol="ETH/USD", seed=2),
    }
    result = walk_forward(
        long_factory([SYMBOL, "ETH/USD"]), bars, train_bars=200, test_bars=100, cfg=FREE
    )

    assert result.symbols == [SYMBOL, "ETH/USD"]
    assert result.n_windows == 3
    assert result.windows[0].test_start == bars[SYMBOL][200].ts
    assert result.equity["ts"].is_monotonic_increasing


def test_too_little_data_names_the_missing_bars():
    """Kein stiller Leerlauf: eine leere Fensterliste saehe im Screening aus
    wie "Strategie hat nichts gehandelt" statt wie "Zeitraum zu kurz"."""
    with pytest.raises(InsufficientDataError) as excinfo:
        walk_forward(
            long_factory(), {SYMBOL: make_bars(250)}, train_bars=200, test_bars=100, cfg=FREE
        )

    message = str(excinfo.value)
    assert "250" in message and "300" in message and "50" in message
    assert isinstance(excinfo.value, ValueError)


def test_train_window_shorter_than_warmup_is_rejected():
    """Ein Trainingsfenster kuerzer als der Warmup enthaelt kein einziges Signal."""
    with pytest.raises(InsufficientDataError, match="Warmup"):
        walk_forward(
            lambda: SlowStarter([SYMBOL], "1h"),
            {SYMBOL: make_bars(600)},
            train_bars=20,
            test_bars=100,
            cfg=FREE,
        )


def test_overlapping_test_windows_are_rejected():
    with pytest.raises(ValueError, match="ueberlappende"):
        run_default(test_bars=100, step_bars=50)


@pytest.mark.parametrize("strategy_name", ["trend", "meanrev"])
def test_two_runs_are_bit_identical(strategy_name):
    """Ohne Determinismus ist jede OOS-Zahl eine Momentaufnahme."""
    bars = {SYMBOL: make_bars(900, seed=11)}

    runs = [
        walk_forward(
            lambda: get(strategy_name)([SYMBOL], "1h"),
            bars,
            train_bars=300,
            test_bars=150,
            embargo_bars=12,
        )
        for _ in range(2)
    ]

    np.testing.assert_array_equal(
        runs[0].equity["equity"].to_numpy(), runs[1].equity["equity"].to_numpy()
    )
    assert runs[0].metrics == runs[1].metrics
    pd.testing.assert_frame_equal(runs[0].window_frame(), runs[1].window_frame())


@pytest.mark.parametrize("timeframe", ["4h"])
def test_smoke_on_real_data(timeframe):
    """Ein Lauf ueber die echten BTC-Daten -- Unit-Tests laufen ohne sie."""
    from qt.data.store import parquet_path, read_bars, to_bars

    if not parquet_path(SYMBOL, timeframe).exists():
        pytest.skip("Keine gespeicherten Daten -- qt data pull")

    df = read_bars(SYMBOL, timeframe)
    result = walk_forward(
        lambda: get("trend")([SYMBOL], timeframe),
        {SYMBOL: to_bars(SYMBOL, timeframe, df)},
        train_bars=2000,
        test_bars=500,
        embargo_bars=24,
    )

    assert result.n_windows >= 3
    assert result.oos_bars == result.n_windows * 500
    assert result.equity["ts"].is_monotonic_increasing
    assert (result.equity["equity"] > 0).all()
