"""Handelskosten.

Ohne Kostenmodell ist jeder Backtest eine Luege. Besonders auf kurzen
Timeframes fressen Gebuehren und Slippage den gesamten Edge -- eine
Strategie mit Sharpe 2.0 vor Kosten kann nach Kosten glatt negativ sein.

Deshalb sind die Defaults pessimistisch: lieber eine gute Strategie
verwerfen als eine schlechte live schalten.

Modell: die effektive Ausfuehrung liegt um (halber Spread + Slippage)
schlechter als der Referenzpreis, dazu kommt die Taker-Gebuehr auf den
Gegenwert. Bewusst kein Market-Impact-Modell -- das braeuchte Orderbuchtiefe,
die wir nicht haben, und ein erfundenes Impact-Modell ist schlechter als
ein ehrlich konstanter Aufschlag.
"""

from __future__ import annotations

from dataclasses import dataclass

from qt.core.config import CostConfig

BPS = 1e-4


@dataclass(frozen=True, slots=True)
class TradeCost:
    fill_price: float
    fee: float
    slippage_cost: float

    @property
    def total(self) -> float:
        return self.fee + self.slippage_cost


def apply(reference_price: float, qty: float, cfg: CostConfig) -> TradeCost:
    """Kosten einer Market-Order.

    `qty` vorzeichenbehaftet: positiv kauft (teurer), negativ verkauft
    (billiger). Der Aufschlag wirkt immer gegen uns -- das ist der ganze
    Punkt.
    """
    if qty == 0:
        return TradeCost(reference_price, 0.0, 0.0)

    adverse_bps = (cfg.half_spread_bps + cfg.slippage_bps) * BPS
    direction = 1.0 if qty > 0 else -1.0
    fill_price = reference_price * (1 + direction * adverse_bps)

    notional = abs(qty) * fill_price
    fee = notional * cfg.taker_fee_bps * BPS
    slippage_cost = abs(qty) * abs(fill_price - reference_price)

    return TradeCost(fill_price=fill_price, fee=fee, slippage_cost=slippage_cost)


def round_trip_bps(cfg: CostConfig) -> float:
    """Kosten eines vollen Round-Trips in Basispunkten.

    Nuetzliche Faustzahl: eine Strategie muss pro Trade mehr als diesen Wert
    verdienen, um ueberhaupt bei null herauszukommen.
    """
    return 2 * (cfg.taker_fee_bps + cfg.half_spread_bps + cfg.slippage_bps)
