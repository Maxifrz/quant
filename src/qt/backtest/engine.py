"""Die Engine -- der eine Codepfad fuer Backtest, Paper und Live (ADR-001).

Der Ablauf pro Bar ist bewusst starr, weil genau diese Reihenfolge daran
hindert, versehentlich in die Zukunft zu greifen:

    1. Uhr auf die Close-Zeit des Bars stellen
    2. Ausstehende Orders auf dem **Open** dieses Bars ausfuehren
       (sie stammen aus der Entscheidung des vorherigen Bars)
    3. Bar in den Feature-Store aufnehmen -- ab jetzt ist er bekannt
    4. Strategie entscheiden lassen (sieht Daten bis einschliesslich Close)
    5. Zieldifferenz als Order fuer den naechsten Bar vormerken

Schritt 2 kommt vor Schritt 3: die Ausfuehrung passiert zu einem Preis, der
zum Entscheidungszeitpunkt bekannt war, aber die Entscheidung selbst kannte
den aktuellen Bar noch nicht.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from qt.backtest.broker_sim import SimBroker
from qt.core.clock import BacktestClock
from qt.core.config import BacktestConfig
from qt.core.events import merge_bar_streams
from qt.core.types import Bar, Fill, Order
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy, clip_weight


@dataclass
class BacktestResult:
    """Ergebnis eines Laufs. `equity` ist die Zeitreihe fuer alle Metriken."""

    equity: pd.DataFrame
    fills: list[Fill]
    strategy: str
    symbols: list[str]
    timeframe: str
    config: BacktestConfig
    warmup_end: datetime | None = None
    meta: dict = field(default_factory=dict)

    @property
    def n_trades(self) -> int:
        return len(self.fills)

    @property
    def fees_paid(self) -> float:
        return sum(f.fee for f in self.fills)

    @property
    def slippage_paid(self) -> float:
        return sum(f.slippage_cost for f in self.fills)


def run_backtest(
    strategy: Strategy,
    bars: dict[str, list[Bar]],
    cfg: BacktestConfig | None = None,
) -> BacktestResult:
    """Eine Strategie ueber historische Bars laufen lassen.

    `bars` bildet Symbol -> chronologische Bar-Liste ab. Mehrere Symbole
    werden nach Close-Zeit gemischt, damit die Reihenfolge der realen
    Informationsankunft entspricht.
    """
    cfg = cfg or BacktestConfig()
    if not bars or all(len(v) == 0 for v in bars.values()):
        raise ValueError("Keine Bars uebergeben.")

    events = merge_bar_streams(list(bars.values()))
    clock = BacktestClock(events[0].ts)
    store = FeatureStore(clock, maxlen=max(1000, strategy.warmup_bars * 3))
    broker = SimBroker(cfg)

    # Letzter bekannter Preis je Symbol, fuer die Equity-Bewertung.
    last_price: dict[str, float] = {}
    # Aktuelles Zielgewicht je Symbol; nan-Signale lassen es unveraendert.
    target: dict[str, float] = {sym: 0.0 for sym in bars}

    bars_seen: dict[str, int] = {sym: 0 for sym in bars}
    warmup_end: datetime | None = None
    rows: list[dict] = []

    for event in events:
        bar = event.bar
        clock.advance(event.ts)

        # 2. Orders des vorherigen Bars auf diesem Open ausfuehren.
        broker.execute_pending(bar.symbol, bar.open, bar.ts)

        # 3. Bar ist ab jetzt bekannt.
        store.on_bar(bar)
        last_price[bar.symbol] = bar.close
        bars_seen[bar.symbol] += 1

        # 4. Entscheidung -- erst nach dem Warmup zaehlt sie.
        warm = bars_seen[bar.symbol] >= strategy.warmup_bars
        if warm:
            # Der Lauf gilt erst als warm, wenn **jedes** Symbol genug Historie
            # hat. Beim ersten Symbol zu starten hiesse, die uebrigen mit
            # Nullgewicht ins Portfolio zu nehmen -- das saehe wie eine
            # Flat-Entscheidung aus statt wie fehlende Daten.
            if warmup_end is None and all(
                seen >= strategy.warmup_bars for seen in bars_seen.values()
            ):
                warmup_end = event.ts
            weight = clip_weight(strategy.on_bar(bar.symbol, store))
            if not math.isnan(weight):
                target[bar.symbol] = weight

        # 5. Differenz zwischen Ziel- und Ist-Position vormerken.
        if warm:
            order = _rebalance_order(
                broker, bar.symbol, target[bar.symbol], bar.close, last_price, cfg
            )
            if order is not None:
                broker.submit(order)

        rows.append(
            {
                "ts": event.ts,
                "equity": broker.equity(last_price),
                "cash": broker.cash,
                "fees_paid": broker.fees_paid,
                "turnover": broker.turnover,
                f"weight_{bar.symbol}": target[bar.symbol],
                f"price_{bar.symbol}": bar.close,
            }
        )

    equity = pd.DataFrame(rows).groupby("ts", as_index=False).last()
    equity = equity.sort_values("ts", ignore_index=True).ffill()

    return BacktestResult(
        equity=equity,
        fills=broker.fills,
        strategy=strategy.describe(),
        symbols=sorted(bars),
        timeframe=strategy.timeframe,
        config=cfg,
        warmup_end=warmup_end,
        meta={"n_events": len(events)},
    )


def _rebalance_order(
    broker: SimBroker,
    symbol: str,
    target_weight: float,
    reference_price: float,
    prices: dict[str, float],
    cfg: BacktestConfig,
) -> Order | None:
    """Order, die die Ist-Position auf das Zielgewicht bringt.

    Das Gewicht bezieht sich auf das aktuelle Eigenkapital. Bewertet wird mit
    dem Close des gerade geschlossenen Bars -- ausgefuehrt wird spaeter zum
    Open des naechsten. Diese Luecke ist real und gehoert genau so ins Modell.

    Genau wegen dieser Luecke braucht es das Rebalancing-Band: ohne es weicht
    die Ist-Position nach jeder Ausfuehrung minimal vom Ziel ab, und die
    naechste Bewertung erzeugt sofort wieder eine Mikro-Order. Das haelt das
    Gewicht perfekt -- und zahlt dafuer bei jedem Bar Gebuehren (ADR-008).
    """
    # Bewertung mit den **letzten bekannten Preisen aller** Symbole. Wuerde
    # hier nur der Preis des aktuellen Symbols stehen, faellt SimBroker.equity
    # fuer alle uebrigen Positionen auf deren Einstandspreis zurueck -- die
    # Positionsgroesse haenge dann an einem Eigenkapital, in dem der Rest des
    # Portfolios zu historischen Kursen steht.
    equity = broker.equity({**prices, symbol: reference_price})
    if equity <= 0 or reference_price <= 0:
        return None

    capped = max(-cfg.max_gross_exposure, min(cfg.max_gross_exposure, target_weight))
    target_qty = capped * equity / reference_price
    delta = target_qty - broker.qty(symbol)
    delta_notional = abs(delta) * reference_price

    threshold = max(cfg.min_trade_notional, cfg.rebalance_band * equity)
    if delta_notional < threshold:
        return None
    return Order(symbol=symbol, qty=delta, reason=f"target_weight={capped:.4f}")
