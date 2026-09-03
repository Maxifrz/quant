"""Portfolio-Engine: mehrere Strategien unter einem Allokator.

Der Schwerpunkt liegt auf den Stellen, an denen ab Phase 3 ein LLM sitzt.
Dort wird nicht geprueft, ob es "gut" allokiert -- sondern ob das Portfolio
auch dann definiert bleibt, wenn der Allokator Unsinn liefert.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from qt.backtest.portfolio_engine import run_portfolio_backtest
from qt.core.config import BacktestConfig, CostConfig
from qt.features.registry import FeatureStore
from qt.portfolio.base import AllocationContext, Allocator, combine
from qt.strategy.base import Strategy
from tests.conftest import make_bars

FREE = CostConfig(taker_fee_bps=0, half_spread_bps=0, slippage_bps=0)


class Equal(Allocator):
    name = "equal"

    def allocate(self, ctx: AllocationContext) -> dict[str, float]:
        n = len(ctx.strategy_ids)
        return {sid: 1.0 / n for sid in ctx.strategy_ids}


class Broken(Allocator):
    """Allokator, der genau das tut, wovor die Pruefschicht schuetzen soll."""

    name = "broken"

    def __init__(self, payload: dict[str, float]) -> None:
        self.payload = payload

    def allocate(self, ctx: AllocationContext) -> dict[str, float]:
        return dict(self.payload)


class ConstantWeight(Strategy):
    """Haelt ein festes Gewicht -- macht Portfolio-Effekte exakt nachrechenbar."""

    name = "constant"

    def __init__(self, symbols, timeframe, weight: float = 1.0) -> None:
        super().__init__(symbols, timeframe, weight=weight)

    @property
    def warmup_bars(self) -> int:
        return 2

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        return self.params["weight"]


def _bars(n: int = 400, timeframe: str = "1h"):
    return {
        ("BTC/USD", timeframe): make_bars(n, symbol="BTC/USD", timeframe=timeframe, seed=1),
        ("ETH/USD", timeframe): make_bars(n, symbol="ETH/USD", timeframe=timeframe, seed=2),
    }


def _strategies(weight: float = 1.0):
    symbols = ["BTC/USD", "ETH/USD"]
    return {
        "a": ConstantWeight(symbols, "1h", weight=weight),
        "b": ConstantWeight(symbols, "1h", weight=weight),
    }


def test_runs_and_reports_both_strategies():
    result = run_portfolio_backtest(_strategies(), _bars(), Equal())

    assert result.strategy_ids == ["a", "b"]
    assert result.symbols == ["BTC/USD", "ETH/USD"]
    assert result.warmup_end is not None
    assert not result.allocations.empty


def test_missing_bars_are_reported_not_silently_ignored():
    """Eine Strategie ohne Daten laeuft sonst stumm mit und traegt nichts bei."""
    strategies = {"a": ConstantWeight(["BTC/USD", "DOGE/USD"], "1h")}

    with pytest.raises(ValueError, match="DOGE/USD"):
        run_portfolio_backtest(strategies, _bars(), Equal())


def test_two_identical_strategies_equal_one_at_full_weight():
    """Zwei gleiche Strategien zu je 50% ergeben dieselbe Position wie eine zu 100%.

    Prueft, dass `combine` Gewichte tatsaechlich addiert statt sie zu
    ueberschreiben -- ein Fehler, der im Ergebnis nur als 'halb so viel
    Rendite' auftauchen wuerde und leicht zu uebersehen ist.
    """
    bars = _bars()
    cfg = BacktestConfig(costs=FREE)

    both = run_portfolio_backtest(_strategies(), bars, Equal(), cfg=cfg)
    single = run_portfolio_backtest(
        {"a": ConstantWeight(["BTC/USD", "ETH/USD"], "1h")}, bars, Equal(), cfg=cfg
    )

    assert both.equity["equity"].iloc[-1] == pytest.approx(
        single.equity["equity"].iloc[-1], rel=1e-9
    )


def test_opposing_strategies_cancel_out():
    """Gegenlaeufige Strategien gleicher Groesse ergeben ein flaches Portfolio."""
    symbols = ["BTC/USD"]
    strategies = {
        "long": ConstantWeight(symbols, "1h", weight=1.0),
        "short": ConstantWeight(symbols, "1h", weight=-1.0),
    }
    result = run_portfolio_backtest(
        strategies,
        {("BTC/USD", "1h"): make_bars(300, seed=4)},
        Equal(),
        cfg=BacktestConfig(costs=FREE),
    )

    assert result.n_trades == 0
    assert result.equity["equity"].iloc[-1] == pytest.approx(100_000.0, rel=1e-9)


@pytest.mark.parametrize(
    "payload",
    [
        {"a": float("nan"), "b": float("nan")},
        {"a": float("inf"), "b": 0.5},
        {},
        {"a": 0.0, "b": 0.0},
        {"unbekannt": 1.0},
    ],
    ids=["nan", "inf", "leer", "nullsumme", "fremde-id"],
)
def test_broken_allocator_never_breaks_the_portfolio(payload):
    """Der Allokator ist ab Phase 3 ein LLM. Seine Ausgabe wird geprueft,
    nicht geglaubt -- und ein Ausfall faellt auf Gleichgewichtung zurueck,
    statt den Lauf in einen undefinierten Zustand zu bringen."""
    result = run_portfolio_backtest(_strategies(), _bars(), Broken(payload))

    equity = result.equity["equity"]
    assert equity.notna().all()
    assert np.isfinite(equity.to_numpy()).all()
    assert equity.iloc[-1] > 0


def test_allocator_output_is_normalised_to_at_most_one():
    """Ein Allokator, der mehr als 100% verteilt, wird herunterskaliert."""
    result = run_portfolio_backtest(
        _strategies(), _bars(), Broken({"a": 5.0, "b": 5.0}),
        cfg=BacktestConfig(costs=FREE),
    )
    allocations = result.allocations.drop(columns=["ts"])

    assert allocations.abs().sum(axis=1).max() <= 1.0 + 1e-9


def test_allocation_cadence_counts_timestamps_not_events():
    """`allocate_every` darf nicht an der Anzahl gehandelter Symbole haengen."""
    bars = _bars()
    every_bar = run_portfolio_backtest(_strategies(), bars, Equal(), allocate_every=1)
    every_fifth = run_portfolio_backtest(_strategies(), bars, Equal(), allocate_every=5)

    ratio = len(every_bar.allocations) / len(every_fifth.allocations)
    assert 4.5 <= ratio <= 5.5


def test_warmup_waits_for_the_slowest_strategy():
    """Ein Portfolio darf nicht handeln, solange eine Strategie blind ist."""

    class Slow(ConstantWeight):
        name = "slow"

        @property
        def warmup_bars(self) -> int:
            return 150

    strategies = {
        "fast": ConstantWeight(["BTC/USD"], "1h"),
        "slow": Slow(["BTC/USD"], "1h"),
    }
    bars = {("BTC/USD", "1h"): make_bars(400, seed=8)}
    result = run_portfolio_backtest(strategies, bars, Equal())

    first_ts = result.equity["ts"].iloc[0]
    warm_index = result.equity.index[result.equity["ts"] == result.warmup_end][0]
    assert warm_index >= 149, "Portfolio hat vor dem Warmup der langsamen Strategie gehandelt"
    assert result.warmup_end > first_ts


def test_deterministic_across_runs():
    bars = _bars()
    runs = [
        run_portfolio_backtest(_strategies(), bars, Equal()).equity["equity"].to_numpy()
        for _ in range(2)
    ]
    np.testing.assert_array_equal(runs[0], runs[1])


def test_combine_adds_and_offsets():
    weights = {"a": {"BTC/USD": 1.0}, "b": {"BTC/USD": -1.0, "ETH/USD": 0.5}}

    assert combine(weights, {"a": 0.5, "b": 0.5}) == {"BTC/USD": 0.0, "ETH/USD": 0.25}
    assert combine({"a": {"BTC/USD": math.nan}}, {"a": 1.0}) == {}


# ---------------------------------------------------------------------------
# Zusammenspiel mit der Risk-Engine
# ---------------------------------------------------------------------------


def test_risk_engine_halts_the_portfolio_and_stays_halted():
    """Ein ausgeloester Kill-Switch beendet den Lauf faktisch.

    Der wichtigste Integrationstest der Portfolio-Schicht: die Risk-Engine
    kann fuer sich korrekt sein und trotzdem wirkungslos bleiben, wenn die
    Engine ihren Zustand pro Bar wegwirft. Geprueft wird deshalb nicht der
    Halt selbst -- das tut `tests/test_risk.py` -- sondern dass die Position
    danach flach bleibt, auch wenn sich der Markt erholt.
    """
    from qt.portfolio.risk import RiskConfig, RiskEngine

    # Erst halbieren, dann weit ueber den Ausgangswert steigen.
    crash = np.linspace(100.0, 40.0, 200)
    recovery = np.linspace(40.0, 400.0, 300)
    prices = np.concatenate([crash, recovery])

    strategies = {"a": ConstantWeight(["BTC/USD"], "1h", weight=1.0)}
    bars = {("BTC/USD", "1h"): make_bars(len(prices), prices=prices)}
    risk = RiskEngine(RiskConfig(max_drawdown=0.15))

    result = run_portfolio_backtest(
        strategies, bars, Equal(), risk=risk, cfg=BacktestConfig(costs=FREE)
    )

    assert risk.halted, "Kill-Switch haette bei -60% ausloesen muessen"

    weights = result.equity["weight_BTC/USD"].to_numpy()
    assert weights[-1] == 0.0
    # Nach dem Halt darf keine einzige Position mehr aufgebaut werden.
    first_halt = int(np.argmax(weights == 0.0))
    assert np.all(weights[first_halt:] == 0.0), (
        "Portfolio hat nach dem Halt wieder gehandelt -- der Halt wird nicht "
        "ueber Bars hinweg fortgeschrieben"
    )


def test_risk_engine_leaves_a_calm_portfolio_alone():
    """Ohne Grenzverletzung greift die Risk-Engine nicht ein.

    Gegenprobe zum Halt-Test: eine Sicherung, die immer ausloest, ist so
    nutzlos wie eine, die nie ausloest.
    """
    from qt.portfolio.risk import RiskConfig, RiskEngine

    flat = np.full(300, 100.0)
    strategies = {"a": ConstantWeight(["BTC/USD"], "1h", weight=0.1)}
    bars = {("BTC/USD", "1h"): make_bars(300, prices=flat)}
    risk = RiskEngine(RiskConfig(max_drawdown=0.20))

    result = run_portfolio_backtest(
        strategies, bars, Equal(), risk=risk, cfg=BacktestConfig(costs=FREE)
    )

    assert not risk.halted
    assert result.equity["equity"].iloc[-1] == pytest.approx(100_000.0, rel=1e-9)


def test_symbol_vol_window_follows_risk_config():
    """Die Fensterlaenge der Vola kommt aus der RiskConfig, nicht aus der Engine.

    Zwei Orte, die denselben Parameter festlegen, laufen auseinander.
    """
    from qt.portfolio.risk import RiskConfig, RiskEngine

    bars = _bars(400)
    strategies = _strategies()

    short = RiskEngine(RiskConfig(vol_lookback=32))
    long = RiskEngine(RiskConfig(vol_lookback=300))

    a = run_portfolio_backtest(strategies, bars, Equal(), risk=short)
    b = run_portfolio_backtest(_strategies(), bars, Equal(), risk=long)

    # Unterschiedliche Vola-Fenster muessen zu unterschiedlichen Skalierungen
    # und damit zu unterschiedlichen Kurven fuehren.
    assert not np.array_equal(
        a.equity["equity"].to_numpy(), b.equity["equity"].to_numpy()
    )


def test_position_sizing_values_the_whole_portfolio_at_market():
    """Positionsgroessen muessen gegen Marktwerte gerechnet werden, nicht Einstaende.

    Der Fehler, den dieser Test festnagelt: bewertet man das Eigenkapital nur
    mit dem Preis des gerade schliessenden Symbols, fallen alle uebrigen
    Positionen auf ihren Einstandspreis zurueck. Bei einem Symbol faellt das
    nie auf. Bei zweien, von denen eines stark gestiegen ist, sizet das
    Portfolio dauerhaft zu klein -- und zwar leise.
    """
    n = 300
    flat = np.full(n, 100.0)
    tripled = np.linspace(100.0, 300.0, n)

    strategies = {"a": ConstantWeight(["BTC/USD", "ETH/USD"], "1h", weight=0.5)}
    bars = {
        ("BTC/USD", "1h"): make_bars(n, symbol="BTC/USD", prices=tripled),
        ("ETH/USD", "1h"): make_bars(n, symbol="ETH/USD", prices=flat),
    }
    result = run_portfolio_backtest(
        strategies, bars, Equal(), cfg=BacktestConfig(costs=FREE)
    )

    equity = result.equity["equity"].iloc[-1]
    final_prices = {"BTC/USD": tripled[-1], "ETH/USD": flat[-1]}

    # Die gehaltene ETH-Position muss rund 50% des *aktuellen* Eigenkapitals
    # ausmachen. Mit Einstandsbewertung laege sie deutlich darunter, weil das
    # Eigenkapital den BTC-Gewinn nicht mitzaehlte.
    eth_qty = next(f for f in reversed(result.fills) if f.symbol == "ETH/USD")
    held_eth = sum(f.qty for f in result.fills if f.symbol == "ETH/USD")
    eth_share = held_eth * final_prices["ETH/USD"] / equity

    assert 0.4 <= eth_share <= 0.6, (
        f"ETH-Anteil {eth_share:.2%} statt ~50% -- Eigenkapital vermutlich "
        "mit Einstandspreisen statt Marktwerten bewertet"
    )
    assert eth_qty.qty == eth_qty.qty  # kein nan


def test_allocator_gets_the_history_it_declares():
    """Die Engine muss so viel Renditehistorie vorhalten, wie der Allokator
    per `warmup_bars` verlangt.

    Der Fehler, den dieser Test festnagelt: die Historie war fest auf 512 Bars
    gedeckelt, `BestSingle` verlangt 720. Sein Sharpe blieb damit in jedem Lauf
    `nan`, und die Baseline, die beziffern soll was Performance-Chasing kostet,
    fiel unbemerkt auf Gleichgewichtung zurueck. Sie war eine zweite
    Equal-Weight-Zeile -- ohne dass irgendetwas fehlschlug.
    """

    class Hungry(Allocator):
        name = "hungry"

        def __init__(self, needs: int) -> None:
            self.needs = needs
            self.max_seen = 0

        @property
        def warmup_bars(self) -> int:
            return self.needs

        def allocate(self, ctx: AllocationContext) -> dict[str, float]:
            self.max_seen = max(self.max_seen, ctx.history_length())
            n = len(ctx.strategy_ids)
            return {sid: 1.0 / n for sid in ctx.strategy_ids}

    bars = _bars(1200)
    hungry = Hungry(needs=720)
    run_portfolio_backtest(_strategies(), bars, hungry)

    assert hungry.max_seen >= hungry.warmup_bars, (
        f"Allokator verlangt {hungry.warmup_bars} Bars, sah aber nur "
        f"{hungry.max_seen} -- er rechnet dauerhaft auf nan"
    )


def test_equity_timeframe_is_the_finest_not_the_allocation_cadence():
    """Die Equity-Kurve hat einen Punkt je Bar-Close -- also im feinsten
    Timeframe, nicht im Takt des Allokators.

    Stuende dort der groebste, annualisierten Metriken und Tearsheet um genau
    dieses Verhaeltnis daneben; bei 1h-Daten mit 1d-Takt um Faktor 24.
    """
    bars = {
        ("BTC/USD", "1h"): make_bars(400, symbol="BTC/USD", timeframe="1h", seed=1),
        ("BTC/USD", "4h"): make_bars(100, symbol="BTC/USD", timeframe="4h", seed=1),
    }
    strategies = {
        "fast": ConstantWeight(["BTC/USD"], "1h", weight=0.5),
        "slow": ConstantWeight(["BTC/USD"], "4h", weight=0.5),
    }
    result = run_portfolio_backtest(strategies, bars, Equal())

    assert result.timeframe == "1h"
    assert result.allocation_timeframe == "4h"
