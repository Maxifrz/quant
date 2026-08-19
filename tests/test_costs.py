"""Kostentests.

Ein Backtest ohne korrektes Kostenmodell ist eine Luege -- besonders auf
kurzen Timeframes, wo Gebuehren und Slippage den ganzen Edge auffressen.
Diese Tests nageln fest, dass die Kosten exakt und immer gegen uns wirken.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from qt.backtest import costs
from qt.backtest.broker_sim import SimBroker
from qt.backtest.engine import run_backtest
from qt.core.config import BacktestConfig, CostConfig
from qt.core.types import Order
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy
from tests.conftest import make_bars

TS = datetime(2020, 1, 1, tzinfo=timezone.utc)


def test_costs_always_work_against_us():
    cfg = CostConfig()
    buy = costs.apply(10_000.0, 1.0, cfg)
    sell = costs.apply(10_000.0, -1.0, cfg)

    assert buy.fill_price > 10_000.0, "Kauf muss teurer als der Referenzpreis sein"
    assert sell.fill_price < 10_000.0, "Verkauf muss billiger sein"
    assert buy.fee > 0 and sell.fee > 0


def test_round_trip_loses_exactly_the_modelled_cost():
    """Kauf und sofortiger Verkauf zum selben Preis kostet genau round_trip_bps.

    Der schaerfste Test des Kostenmodells: das Ergebnis ist analytisch
    bekannt, also faellt jede Abweichung sofort auf.
    """
    cfg = BacktestConfig(initial_cash=100_000.0)
    broker = SimBroker(cfg)
    price, qty = 10_000.0, 1.0

    broker.submit(Order("BTC/USD", qty))
    broker.execute_pending("BTC/USD", price, TS)
    broker.submit(Order("BTC/USD", -qty))
    broker.execute_pending("BTC/USD", price, TS)

    assert broker.qty("BTC/USD") == pytest.approx(0.0, abs=1e-12)

    notional = qty * price
    expected_loss = notional * costs.round_trip_bps(cfg.costs) * 1e-4
    actual_loss = cfg.initial_cash - broker.equity({"BTC/USD": price})

    assert actual_loss == pytest.approx(expected_loss, rel=1e-6), (
        f"Round-Trip kostete {actual_loss:.4f}, modelliert sind {expected_loss:.4f}"
    )


def test_zero_cost_config_is_exactly_free():
    """Ohne Gebuehren darf ein Round-Trip auf flachem Preis nichts kosten.

    Faengt versehentlich hartkodierte Kosten ab, die das Modell umgehen.
    """
    cfg = BacktestConfig(
        initial_cash=100_000.0,
        costs=CostConfig(taker_fee_bps=0, half_spread_bps=0, slippage_bps=0),
    )
    broker = SimBroker(cfg)
    broker.submit(Order("BTC/USD", 1.0))
    broker.execute_pending("BTC/USD", 10_000.0, TS)
    broker.submit(Order("BTC/USD", -1.0))
    broker.execute_pending("BTC/USD", 10_000.0, TS)

    assert broker.equity({"BTC/USD": 10_000.0}) == pytest.approx(100_000.0, rel=1e-12)


def test_costs_reduce_returns_in_a_full_backtest():
    """Dieselbe Strategie muss mit Kosten schlechter abschneiden als ohne."""

    class Flipper(Strategy):
        """Wechselt staendig die Richtung -- maximaler Umsatz, kein Edge."""

        name = "flipper"

        @property
        def warmup_bars(self) -> int:
            return 2

        def on_bar(self, symbol: str, store: FeatureStore) -> float:
            return 1.0 if len(store.window(symbol, self.timeframe)) % 2 == 0 else -1.0

    bars = {"BTC/USD": make_bars(200, seed=3)}
    free = run_backtest(
        Flipper(["BTC/USD"], "1h"),
        bars,
        BacktestConfig(costs=CostConfig(taker_fee_bps=0, half_spread_bps=0, slippage_bps=0)),
    )
    paid = run_backtest(Flipper(["BTC/USD"], "1h"), bars, BacktestConfig())

    assert paid.fees_paid > 0
    assert paid.equity["equity"].iloc[-1] < free.equity["equity"].iloc[-1], (
        "Kosten haben das Ergebnis nicht verschlechtert -- werden sie ueberhaupt angewandt?"
    )


def test_dust_orders_are_dropped():
    """Winzige Orders werden verworfen, statt Gebuehren aus Rundungsrauschen zu erzeugen."""
    cfg = BacktestConfig(min_trade_notional=10.0)
    broker = SimBroker(cfg)
    broker.submit(Order("BTC/USD", 0.0001))  # 1 USD Gegenwert
    fills = broker.execute_pending("BTC/USD", 10_000.0, TS)

    assert fills == []
    assert broker.fees_paid == 0.0
