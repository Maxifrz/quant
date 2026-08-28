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


# ---------------------------------------------------------------------------
# Fill-Modelle
# ---------------------------------------------------------------------------


def test_flat_model_ignores_size():
    """Das Flat-Modell macht Kapazitaet unsichtbar -- bewusst, aber dokumentiert."""
    from qt.backtest.costs import FillContext, FlatFillModel

    model = FlatFillModel()
    small = model.fill(10_000.0, 0.01, FillContext(bar_volume=1000.0))
    large = model.fill(10_000.0, 500.0, FillContext(bar_volume=1000.0))

    assert small.fill_price == large.fill_price


def test_size_aware_model_charges_more_for_larger_orders():
    from qt.backtest.costs import FillContext, SizeAwareFillModel

    model = SizeAwareFillModel()
    ctx = FillContext(bar_volume=1000.0)

    tiny = model.fill(10_000.0, 0.1, ctx)
    big = model.fill(10_000.0, 100.0, ctx)

    assert big.fill_price > tiny.fill_price
    assert big.slippage_cost / abs(100.0) > tiny.slippage_cost / abs(0.1)


def test_size_aware_impact_follows_square_root_law():
    """Vierfache Beteiligung -> doppelter Aufschlag, nicht vierfacher.

    Nagelt die Wurzel-Naeherung fest. Ein linearer Impact waere sonst eine
    stille Verschaerfung, ein konstanter eine stille Entschaerfung.
    """
    from qt.backtest.costs import FillContext, SizeAwareFillModel

    model = SizeAwareFillModel(impact_bps=100.0)
    base = model.fill(10_000.0, 1.0, FillContext(bar_volume=100.0))    # 1%
    quad = model.fill(10_000.0, 4.0, FillContext(bar_volume=100.0))    # 4%

    flat_price = 10_000.0 * (1 + (2.0 + 3.0) * 1e-4)  # halber Spread + Basis-Slippage
    impact_base = base.fill_price - flat_price
    impact_quad = quad.fill_price - flat_price

    assert impact_quad == pytest.approx(2 * impact_base, rel=1e-6)


def test_size_aware_falls_back_without_volume():
    """Ohne Volumen wird nicht geschaetzt, sondern auf das Flat-Modell zurueckgefallen.

    Eine Impact-Schaetzung ohne Volumenbezug waere geraten -- und geraten ist
    schlechter als bescheiden.
    """
    from qt.backtest.costs import FillContext, FlatFillModel, SizeAwareFillModel

    ctx = FillContext(bar_volume=None)
    sized = SizeAwareFillModel().fill(10_000.0, 5.0, ctx)
    flat = FlatFillModel().fill(10_000.0, 5.0, ctx)

    assert sized.fill_price == flat.fill_price
    assert SizeAwareFillModel().fill(
        10_000.0, 5.0, FillContext(bar_volume=0.0)
    ).fill_price == flat.fill_price


def test_size_aware_caps_participation_at_full_bar():
    """Mehr als das ganze Bar-Volumen zu handeln erhoeht den Aufschlag nicht weiter.

    Jenseits von 100% Beteiligung ist die Wurzel-Naeherung ohnehin bedeutungslos.
    Der Deckel verhindert, dass ein absurder Ordervorschlag zu absurden
    Kostenzahlen fuehrt statt zu einer erkennbar unmoeglichen Order.
    """
    from qt.backtest.costs import FillContext, SizeAwareFillModel

    model = SizeAwareFillModel()
    full = model.fill(10_000.0, 100.0, FillContext(bar_volume=100.0))
    over = model.fill(10_000.0, 10_000.0, FillContext(bar_volume=100.0))

    assert over.fill_price == pytest.approx(full.fill_price)


def test_size_aware_makes_capacity_visible_in_a_backtest():
    """Der eigentliche Zweck: mehr Kapital muss schlechter abschneiden.

    Unter dem Flat-Modell liefert dieselbe Strategie bei 100k und bei 100 Mio
    exakt dasselbe Ergebnis -- sie skaliert unendlich. Genau diese Blindheit
    gegenueber Kapazitaet soll das groessenabhaengige Modell beheben.
    """
    from qt.backtest.costs import FlatFillModel, SizeAwareFillModel
    from qt.backtest.engine import run_backtest
    from qt.core.config import BacktestConfig
    from qt.strategy.base import Strategy

    class Flipper(Strategy):
        name = "flipper"

        @property
        def warmup_bars(self) -> int:
            return 2

        def on_bar(self, symbol: str, store: FeatureStore) -> float:
            return 1.0 if len(store.window(symbol, self.timeframe)) % 4 < 2 else -1.0

    bars = {"BTC/USD": make_bars(300, seed=21)}

    def factor(cash: float, model) -> float:
        result = run_backtest(
            Flipper(["BTC/USD"], "1h"), bars, BacktestConfig(initial_cash=cash), model
        )
        return result.equity["equity"].iloc[-1] / cash

    assert factor(1e5, FlatFillModel()) == pytest.approx(
        factor(1e9, FlatFillModel()), rel=1e-6
    ), "Flat-Modell sollte kapazitaetsblind sein"

    assert factor(1e9, SizeAwareFillModel()) < factor(1e5, SizeAwareFillModel()), (
        "Groessenabhaengiges Modell muss grosses Kapital bestrafen"
    )


def test_time_in_market_separates_inactivity_from_throttling():
    """Zeit im Markt und aktive Vola trennen zwei Effekte, die `ann_vol` vermischt.

    Genau diese Vermischung hat hier zu einer Fehldiagnose gefuehrt (ADR-016):
    eine zu 93% flache Strategie zeigte 3,7% annualisierte Vola, was wie eine
    zu scharfe Risk-Engine aussah -- tatsaechlich war es Untaetigkeit.
    """
    import pandas as pd

    from qt.backtest.metrics import compute

    # Zwei Reihen mit identischer Bewegung, aber unterschiedlich viel Untaetigkeit.
    rng = np.random.default_rng(3)
    moves = rng.normal(0, 0.02, 100)

    dense = pd.Series(100_000 * np.cumprod(1 + moves))
    sparse_returns = np.zeros(400)
    sparse_returns[:100] = moves
    sparse = pd.Series(100_000 * np.cumprod(1 + sparse_returns))

    m_dense = compute(dense, "1h")
    m_sparse = compute(sparse, "1h")

    assert m_sparse.ann_vol < m_dense.ann_vol / 1.5, (
        "Untaetigkeit muss die Gesamt-Vola verduennen"
    )
    assert m_sparse.ann_vol_active == pytest.approx(m_dense.ann_vol_active, rel=0.05), (
        "Die Vola *waehrend* der Positionierung darf sich nicht unterscheiden"
    )
    assert m_sparse.time_in_market == pytest.approx(0.25, abs=0.02)
    assert m_dense.time_in_market == pytest.approx(1.0, abs=0.02)


# --------------------------------------------------------------------------
# Buy-&-Hold mit Einstiegskosten
# --------------------------------------------------------------------------


def test_one_way_bps_ist_die_haelfte_des_round_trips():
    """Die Beziehung soll im Code stehen und nicht im Kopf des Lesers."""
    from qt.backtest.costs import one_way_bps, round_trip_bps
    from qt.core.config import CostConfig

    cfg = CostConfig()
    assert round_trip_bps(cfg) == pytest.approx(2 * one_way_bps(cfg))
    assert one_way_bps(cfg) == pytest.approx(45.0)


def test_buy_and_hold_ohne_kosten_bleibt_die_alte_formel():
    """Der Default darf nichts an bestehenden Vergleichen aendern."""
    import pandas as pd

    from qt.backtest.metrics import buy_and_hold

    preise = pd.Series([100.0, 110.0, 90.0, 120.0])
    kurve = buy_and_hold(preise, 100_000.0)
    assert kurve.iloc[0] == pytest.approx(100_000.0)
    assert kurve.iloc[-1] == pytest.approx(120_000.0)


def test_einstiegskosten_verkleinern_die_gekaufte_menge():
    """Die Kosten kommen auf den Gegenwert obendrauf, wie im SimBroker.

    Mit Kapital C und Kostensatz f gilt N * p0 * (1 + f) = C -- wer stattdessen
    die Kurve nachtraeglich skalierte, kaeme auf dieselbe Zahl, aber nicht auf
    dieselbe Begruendung.
    """
    import pandas as pd

    from qt.backtest.metrics import buy_and_hold

    preise = pd.Series([100.0, 200.0])
    kurve = buy_and_hold(preise, 100_000.0, entry_cost_bps=45.0)

    einheiten = 100_000.0 / (100.0 * 1.0045)
    assert kurve.iloc[-1] == pytest.approx(einheiten * 200.0)
    # Der erste Punkt ist das Startkapital -- sonst kuerzt sich die Gebuehr
    # in `compute` restlos heraus.
    assert kurve.iloc[0] == pytest.approx(100_000.0)


def test_einstiegskosten_schlagen_in_der_gesamtrendite_durch():
    """Der Test, der die erste Fassung widerlegt hat.

    `compute` misst `total_return` als equity[-1] / equity[0]. Skaliert man die
    ganze Kurve mit einem konstanten Faktor, kuerzt er sich restlos heraus --
    die Gebuehr stuende im Code und in keiner Kennzahl.
    """
    import numpy as np
    import pandas as pd

    from qt.backtest.metrics import buy_and_hold, compute

    rng = np.random.default_rng(7)
    preise = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.02, 400)))
    preise.index = pd.date_range("2024-01-01", periods=400, freq="1D", tz="UTC")

    ohne = compute(buy_and_hold(preise, 100_000.0), "1d")
    mit = compute(buy_and_hold(preise, 100_000.0, entry_cost_bps=45.0), "1d")

    assert mit.total_return < ohne.total_return
    # 45bps auf das Startkapital, in der Gesamtrendite wiederzufinden.
    verhaeltnis = (1 + mit.total_return) / (1 + ohne.total_return)
    assert verhaeltnis == pytest.approx(1 / 1.0045, rel=1e-6)


def test_negative_einstiegskosten_werden_abgelehnt():
    import pandas as pd

    from qt.backtest.metrics import buy_and_hold

    with pytest.raises(ValueError, match="negativ"):
        buy_and_hold(pd.Series([100.0, 110.0]), 100_000.0, entry_cost_bps=-1.0)
