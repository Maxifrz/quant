"""Simulierter Broker: fuehrt Orders aus und fuehrt Buch.

Die eine Regel, die alles entscheidet:

    Ein Signal vom Close des Bars t wird auf dem **Open von Bar t+1**
    ausgefuehrt -- nie auf dem Close, auf dem es entstanden ist.

Wer auf dem Signal-Close ausfuehrt, handelt zu einem Preis, den er zum
Entscheidungszeitpunkt noch nicht kannte. Das ist Lookahead in seiner
teuersten Form und laesst nahezu jede Strategie profitabel aussehen.

Derselbe Broker bedient Backtest und Paper-Trading. Live wird er durch
`qt.live.broker_ccxt` ersetzt, das dieselbe Schnittstelle erfuellt.
"""

from __future__ import annotations

from datetime import datetime

from qt.backtest.costs import FillContext, FillModel, FlatFillModel
from qt.core.config import BacktestConfig
from qt.core.types import Fill, Order, Position, equity


class SimBroker:
    """Kontofuehrung mit simulierten Fills."""

    def __init__(self, cfg: BacktestConfig, fill_model: FillModel | None = None) -> None:
        self.cfg = cfg
        # Ohne explizites Modell das Verhalten aus Phase 1: konstanter
        # Aufschlag, unabhaengig von der Ordergroesse.
        self.fill_model = fill_model or FlatFillModel(cfg.costs)
        self.cash = cfg.initial_cash
        self.positions: dict[str, Position] = {}
        self.fills: list[Fill] = []
        self.fees_paid = 0.0
        self.turnover = 0.0
        self._pending: list[tuple[Order, str]] = []

    # ------------------------------------------------------------------
    # Orderfluss
    # ------------------------------------------------------------------

    def submit(self, order: Order) -> None:
        """Order fuer die naechste Ausfuehrungsgelegenheit vormerken.

        Es passiert hier bewusst nichts weiter: die Verzoegerung bis zum
        naechsten Bar-Open *ist* das Realismusmodell.

        Eine bereits vorgemerkte Order desselben Symbols wird **ersetzt**,
        nicht ergaenzt. Alle Orders im System sind Differenzen zu einem
        Zielgewicht -- zwei aufeinanderfolgende Vormerkungen sind also zwei
        Schaetzungen derselben Absicht, nicht zwei Absichten. Wuerden sie
        sich addieren, verdoppelte das Portfolio seine Position, sobald zwei
        Bars desselben Symbols (etwa 1h und 4h) gleichzeitig schliessen.
        """
        if order.qty == 0:
            return
        self._pending = [(o, s) for o, s in self._pending if s != order.symbol]
        self._pending.append((order, order.symbol))

    def execute_pending(
        self,
        symbol: str,
        open_price: float,
        ts: datetime,
        bar_volume: float | None = None,
    ) -> list[Fill]:
        """Vorgemerkte Orders dieses Symbols auf dem Bar-Open ausfuehren.

        `bar_volume` ist das Volumen des Bars, auf dem ausgefuehrt wird. Nur
        groessenabhaengige Fill-Modelle werten es aus; das Flat-Modell
        ignoriert es.
        """
        if not self._pending:
            return []

        ready = [(o, s) for o, s in self._pending if s == symbol]
        if not ready:
            return []
        self._pending = [(o, s) for o, s in self._pending if s != symbol]

        fills: list[Fill] = []
        for order, _ in ready:
            fill = self._fill(order, open_price, ts, bar_volume)
            if fill is not None:
                fills.append(fill)
        return fills

    def _fill(
        self,
        order: Order,
        reference_price: float,
        ts: datetime,
        bar_volume: float | None = None,
    ) -> Fill | None:
        notional = abs(order.qty) * reference_price
        if notional < self.cfg.min_trade_notional:
            # Zu klein: wuerde nur Gebuehren erzeugen und Rundungsrauschen
            # in echte Kosten verwandeln.
            return None

        cost = self.fill_model.fill(
            reference_price, order.qty, FillContext(bar_volume=bar_volume)
        )

        self.cash -= order.qty * cost.fill_price + cost.fee
        self.fees_paid += cost.fee
        self.turnover += abs(order.qty) * cost.fill_price

        position = self.positions.setdefault(order.symbol, Position(order.symbol))
        self._update_position(position, order.qty, cost.fill_price)

        fill = Fill(
            symbol=order.symbol,
            ts=ts,
            qty=order.qty,
            price=cost.fill_price,
            fee=cost.fee,
            slippage_cost=cost.slippage_cost,
        )
        self.fills.append(fill)
        return fill

    @staticmethod
    def _update_position(position: Position, qty: float, price: float) -> None:
        """Menge und Durchschnittspreis fortschreiben.

        Der Durchschnittspreis wird nur beim Aufstocken neu gemischt. Beim
        Reduzieren bleibt er stehen, beim Drehen startet er neu -- sonst
        beschreibt er eine Position, die es nicht mehr gibt.
        """
        new_qty = position.qty + qty
        if position.qty == 0 or (position.qty > 0) != (qty > 0):
            if abs(new_qty) < 1e-12:
                position.qty = 0.0
                position.avg_price = 0.0
                return
            if (position.qty > 0) != (new_qty > 0):
                position.qty = new_qty
                position.avg_price = price
                return
        else:
            total = abs(position.qty) + abs(qty)
            position.avg_price = (
                position.avg_price * abs(position.qty) + price * abs(qty)
            ) / total
        position.qty = new_qty

    # ------------------------------------------------------------------
    # Kontostand
    # ------------------------------------------------------------------

    def qty(self, symbol: str) -> float:
        position = self.positions.get(symbol)
        return position.qty if position else 0.0

    def equity(self, prices: dict[str, float]) -> float:
        """Eigenkapital: Cash plus Marktwert aller Positionen."""
        return equity(self.cash, self.positions, prices)

    def has_pending(self) -> bool:
        return bool(self._pending)
