"""Engine-Plausibilitaet.

Wenn eine Strategie schlecht abschneidet, gibt es zwei moegliche Ursachen:
die Strategie taugt nichts, oder die Engine rechnet falsch. Diese Tests
schliessen die zweite aus -- ohne sie waere jedes schlechte Ergebnis
unbrauchbar, weil man ihm nicht trauen koennte.
"""

from __future__ import annotations

import numpy as np
import pytest

from qt.backtest.engine import run_backtest
from qt.backtest.metrics import buy_and_hold
from qt.core.config import BacktestConfig, CostConfig
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy
from tests.conftest import make_bars

FREE = CostConfig(taker_fee_bps=0, half_spread_bps=0, slippage_bps=0)


class AlwaysLong(Strategy):
    name = "alwayslong"

    @property
    def warmup_bars(self) -> int:
        return 2

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        return 1.0


class AlwaysFlat(Strategy):
    name = "alwaysflat"

    @property
    def warmup_bars(self) -> int:
        return 2

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        return 0.0


def test_always_long_tracks_buy_and_hold():
    """Voll investiert und ohne Kosten muss Buy-&-Hold herauskommen.

    Der wichtigste Plausibilitaetstest der Engine: er prueft Positionsgroesse,
    Bewertung und Ausfuehrung in einem Zug gegen ein analytisch bekanntes
    Ergebnis. Die kleine Restabweichung stammt aus dem Warmup und dem
    Einstieg zum naechsten Open -- beides gewollt.
    """
    bars = make_bars(1000, seed=42)
    result = run_backtest(
        AlwaysLong(["BTC/USD"], "1h"), {"BTC/USD": bars}, BacktestConfig(costs=FREE)
    )

    equity_end = result.equity["equity"].iloc[-1]
    bh_end = buy_and_hold(result.equity.set_index("ts")["price_BTC/USD"], 100_000.0).iloc[-1]

    assert equity_end == pytest.approx(bh_end, rel=0.02)
    assert result.n_trades <= 3, "Konstantes Gewicht darf kaum handeln"


def test_flat_strategy_never_trades():
    """Ohne Position bleibt das Kapital exakt unveraendert."""
    result = run_backtest(AlwaysFlat(["BTC/USD"], "1h"), {"BTC/USD": make_bars(300)})

    assert result.n_trades == 0
    assert result.equity["equity"].iloc[-1] == pytest.approx(100_000.0, rel=1e-12)


def test_rebalance_band_suppresses_micro_orders():
    """Ohne Band erzeugt die Close/Open-Luecke bei jedem Bar eine Mikro-Order.

    Siehe ADR-008 -- das war ein echter Befund aus dem ersten Backtest,
    kein theoretisches Risiko.
    """
    bars = {"BTC/USD": make_bars(800, seed=13)}
    strategy_args = (["BTC/USD"], "1h")

    without = run_backtest(
        AlwaysLong(*strategy_args), bars, BacktestConfig(rebalance_band=0.0)
    )
    with_band = run_backtest(
        AlwaysLong(*strategy_args), bars, BacktestConfig(rebalance_band=0.05)
    )

    assert with_band.n_trades < without.n_trades


def test_short_position_profits_when_price_falls():
    """Vorzeichen der Short-Seite. Ein Vorzeichenfehler waere sonst unsichtbar,
    weil er im Backtest nur als 'schlechte Strategie' erscheint."""

    class AlwaysShort(Strategy):
        name = "alwaysshort"

        @property
        def warmup_bars(self) -> int:
            return 2

        def on_bar(self, symbol: str, store: FeatureStore) -> float:
            return -1.0

    falling = np.linspace(100, 50, 200)
    result = run_backtest(
        AlwaysShort(["BTC/USD"], "1h"),
        {"BTC/USD": make_bars(200, prices=falling)},
        BacktestConfig(costs=FREE),
    )

    assert result.equity["equity"].iloc[-1] > 100_000.0


def test_multi_symbol_run_produces_weights_for_each():
    """Zwei Symbole parallel -- Vorbereitung fuer das Portfolio in Phase 2."""
    bars = {
        "BTC/USD": make_bars(300, symbol="BTC/USD", seed=1),
        "ETH/USD": make_bars(300, symbol="ETH/USD", seed=2),
    }
    result = run_backtest(AlwaysLong(["BTC/USD", "ETH/USD"], "1h"), bars)

    assert set(result.symbols) == {"BTC/USD", "ETH/USD"}
    for symbol in bars:
        assert f"weight_{symbol}" in result.equity.columns


# ---------------------------------------------------------------------------
# Buchhaltung des Brokers: der Einstandspreis
# ---------------------------------------------------------------------------
#
# `avg_price` taucht in keiner Kennzahl auf, deshalb faellt ein falscher Wert
# in keinem Backtest auf. Er steht aber im Tagesreport, in der
# Zustandsdatei des Paper-Kontos und im Rueckfallpfad von `SimBroker.equity`,
# wenn ein Preis fehlt -- also genau an den drei Stellen, an denen ein Mensch
# hinsieht, wenn etwas schiefgegangen ist (ADR-053).


def _broker():
    from qt.backtest.broker_sim import SimBroker

    return SimBroker(BacktestConfig(costs=FREE))


@pytest.mark.parametrize("richtung", [1.0, -1.0])
def test_eine_neue_position_traegt_den_fill_preis_als_einstand(richtung):
    """Auch beim Short aus flach -- das war der Fehler.

    Mit `position.qty == 0` und `qty < 0` waren frueher beide Vorzeichen-
    vergleiche falsch, der Zweig griff nicht, und `avg_price` blieb auf 0.0
    stehen. Long war korrekt, Short nicht, und das Projekt handelt long-only
    -- der Fehler konnte deshalb beliebig lange unentdeckt bleiben.
    """
    from qt.core.types import Order

    broker = _broker()
    broker.submit(Order(symbol="BTC/USD", qty=5.0 * richtung))
    broker.execute_pending("BTC/USD", 100.0, make_bars(1)[0].ts)

    position = broker.positions["BTC/USD"]
    assert position.qty == pytest.approx(5.0 * richtung)
    assert position.avg_price == pytest.approx(100.0), (
        "eine frisch eroeffnete Position ohne Einstandspreis"
    )


def test_aufstocken_mischt_den_einstand_mengengewichtet():
    from qt.core.types import Order

    broker = _broker()
    ts = make_bars(1)[0].ts
    broker.submit(Order(symbol="BTC/USD", qty=10.0))
    broker.execute_pending("BTC/USD", 100.0, ts)
    broker.submit(Order(symbol="BTC/USD", qty=5.0))
    broker.execute_pending("BTC/USD", 130.0, ts)

    # (100*10 + 130*5) / 15
    assert broker.positions["BTC/USD"].avg_price == pytest.approx(110.0)


def test_reduzieren_laesst_den_einstand_stehen():
    """Ein neu gemischter Einstand beschriebe eine Position, die es nie gab."""
    from qt.core.types import Order

    broker = _broker()
    ts = make_bars(1)[0].ts
    broker.submit(Order(symbol="BTC/USD", qty=10.0))
    broker.execute_pending("BTC/USD", 100.0, ts)
    broker.submit(Order(symbol="BTC/USD", qty=-4.0))
    broker.execute_pending("BTC/USD", 130.0, ts)

    assert broker.positions["BTC/USD"].qty == pytest.approx(6.0)
    assert broker.positions["BTC/USD"].avg_price == pytest.approx(100.0)


def test_drehen_startet_den_einstand_neu():
    from qt.core.types import Order

    broker = _broker()
    ts = make_bars(1)[0].ts
    broker.submit(Order(symbol="BTC/USD", qty=10.0))
    broker.execute_pending("BTC/USD", 100.0, ts)
    broker.submit(Order(symbol="BTC/USD", qty=-15.0))
    broker.execute_pending("BTC/USD", 130.0, ts)

    assert broker.positions["BTC/USD"].qty == pytest.approx(-5.0)
    assert broker.positions["BTC/USD"].avg_price == pytest.approx(130.0)


def test_glattstellen_setzt_den_einstand_zurueck():
    from qt.core.types import Order

    broker = _broker()
    ts = make_bars(1)[0].ts
    broker.submit(Order(symbol="BTC/USD", qty=10.0))
    broker.execute_pending("BTC/USD", 100.0, ts)
    broker.submit(Order(symbol="BTC/USD", qty=-10.0))
    broker.execute_pending("BTC/USD", 130.0, ts)

    position = broker.positions["BTC/USD"]
    assert position.qty == 0.0
    assert position.avg_price == 0.0
