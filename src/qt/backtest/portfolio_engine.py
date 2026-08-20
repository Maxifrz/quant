"""Portfolio-Engine: mehrere Strategien, ein Konto.

Erweitert `run_backtest` aus `engine.py` um die Portfolio-Schicht. Der
Ablauf pro Bar bleibt derselbe und damit auch die Point-in-Time-Garantie --
dazwischen liegen jetzt Allokator und Risk-Engine:

    1. Uhr auf die Close-Zeit stellen
    2. Vorgemerkte Orders auf dem **Open** ausfuehren
    3. Bar in den Feature-Store aufnehmen
    4. Papier-Rendite jeder betroffenen Strategie fortschreiben
    5. Betroffene Strategien entscheiden lassen -> Zielgewicht je Strategie
    6. Allokator (im vorgesehenen Takt) -> Kapitalanteil je Strategie
    7. Kombinieren -> Zielgewicht je Symbol
    8. Risk-Engine -> beschnittenes Zielgewicht
    9. Differenz zum Ist als Order fuer den naechsten Bar vormerken

Schritt 6 und 8 sind die Stellen, an denen ab Phase 3 das LLM sitzt --
beziehungsweise die Stelle, die es bewacht.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from qt.backtest.broker_sim import SimBroker
from qt.core.clock import BacktestClock
from qt.core.config import BacktestConfig
from qt.core.events import merge_bar_streams
from qt.core.types import Bar, Fill, Order, bars_per_year, timeframe_seconds
from qt.features import ta
from qt.features.registry import FeatureStore
from qt.portfolio.base import (
    AllocationContext,
    Allocator,
    PortfolioWeights,
    RiskLimits,
    RiskState,
    combine,
)
from qt.strategy.base import Strategy, clip_weight

# Wieviele vergangene Bar-Renditen je Strategie der Allokator sehen darf.
RETURN_HISTORY = 512


@dataclass
class PortfolioResult:
    """Ergebnis eines Portfolio-Laufs."""

    equity: pd.DataFrame
    fills: list[Fill]
    strategy_ids: list[str]
    symbols: list[str]
    allocator: str
    config: BacktestConfig
    allocations: pd.DataFrame = field(default_factory=pd.DataFrame)
    risk_events: list[tuple[datetime, str]] = field(default_factory=list)
    warmup_end: datetime | None = None
    timeframe: str = "1h"
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

    @property
    def strategy(self) -> str:
        """Damit Tearsheet und Metriken dieselbe Schnittstelle sehen wie
        bei einem Einzelstrategie-Lauf."""
        return f"{self.allocator}[{', '.join(self.strategy_ids)}]"


def run_portfolio_backtest(
    strategies: dict[str, Strategy],
    bars: dict[tuple[str, str], list[Bar]],
    allocator: Allocator,
    risk: RiskLimits | None = None,
    cfg: BacktestConfig | None = None,
    allocate_every: int = 1,
) -> PortfolioResult:
    """Mehrere Strategien unter einem Allokator laufen lassen.

    `bars` ist nach (Symbol, Timeframe) geschluesselt, damit Strategien mit
    unterschiedlichen Timeframes im selben Lauf koexistieren koennen. Jede
    Strategie wird nur zu den Bars befragt, die zu ihrem eigenen Timeframe
    und ihrer Symbolliste gehoeren.

    `allocate_every` zaehlt in Bars des **groebsten** vorhandenen Timeframes.
    Der Allokator laeuft also auf einem langsameren Takt als die Strategien --
    das ist ab Phase 3 zwingend, weil dort ein LLM an dieser Stelle steht und
    ein Aufruf pro Minutenbar weder bezahlbar noch sinnvoll waere.
    """
    cfg = cfg or BacktestConfig()
    if not strategies:
        raise ValueError("Keine Strategien uebergeben.")
    if not bars or all(len(v) == 0 for v in bars.values()):
        raise ValueError("Keine Bars uebergeben.")

    _check_coverage(strategies, bars)

    events = merge_bar_streams(list(bars.values()))
    clock = BacktestClock(events[0].ts)
    max_warmup = max(s.warmup_bars for s in strategies.values())
    store = FeatureStore(clock, maxlen=max(1000, max_warmup * 3))
    broker = SimBroker(cfg)

    strategy_ids = sorted(strategies)
    symbols = sorted({sym for sym, _ in bars})

    # Takt des Allokators: der groebste Timeframe im Lauf.
    alloc_tf = max({tf for _, tf in bars}, key=timeframe_seconds)

    # Zielgewicht je Strategie und Symbol.
    weights: dict[str, dict[str, float]] = {sid: {} for sid in strategy_ids}
    # Papier-Renditen je Strategie, ausschliesslich fuer die Allokation.
    returns: dict[str, deque[float]] = {
        sid: deque(maxlen=RETURN_HISTORY) for sid in strategy_ids
    }
    allocation = {sid: 1.0 / len(strategy_ids) for sid in strategy_ids}

    last_price: dict[str, float] = {}
    prev_close: dict[tuple[str, str], float] = {}
    bars_seen: dict[tuple[str, str], int] = {key: 0 for key in bars}
    peak_equity = cfg.initial_cash
    alloc_ticks = 0
    last_alloc_ts: datetime | None = None
    halted = False
    last_reasons: set[str] = set()
    warmup_end: datetime | None = None

    rows: list[dict] = []
    alloc_rows: list[dict] = []
    risk_events: list[tuple[datetime, str]] = []
    final_weights: PortfolioWeights = {}

    for event in events:
        bar = event.bar
        key = (bar.symbol, bar.timeframe)
        clock.advance(event.ts)

        broker.execute_pending(bar.symbol, bar.open, bar.ts)
        store.on_bar(bar)
        bars_seen[key] += 1

        active = [
            sid
            for sid in strategy_ids
            if strategies[sid].timeframe == bar.timeframe
            and bar.symbol in strategies[sid].symbols
        ]

        _accrue_paper_returns(
            active, strategies, weights, returns, bar, prev_close.get(key)
        )
        prev_close[key] = bar.close
        last_price[bar.symbol] = bar.close

        for sid in active:
            if bars_seen[key] < strategies[sid].warmup_bars:
                continue
            signal = clip_weight(strategies[sid].on_bar(bar.symbol, store))
            if not math.isnan(signal):
                weights[sid][bar.symbol] = signal

        if warmup_end is None and _portfolio_is_warm(strategies, bars_seen):
            warmup_end = event.ts

        # Allokation nur im vorgesehenen Takt neu bestimmen. Gezaehlt werden
        # Zeitpunkte, nicht Events: bei mehreren Symbolen schliessen mehrere
        # Bars gleichzeitig, und der Takt soll nicht an der Anzahl der
        # gehandelten Symbole haengen.
        if bar.timeframe == alloc_tf and event.ts != last_alloc_ts:
            last_alloc_ts = event.ts
            alloc_ticks += 1
            if alloc_ticks % allocate_every == 0 and warmup_end is not None:
                allocation = _allocate(
                    allocator, strategy_ids, returns, broker, last_price,
                    allocation, event.ts, alloc_tf,
                )
                alloc_rows.append({"ts": event.ts, **allocation})

        target = combine(weights, allocation)

        if risk is not None:
            equity = broker.equity(last_price)
            peak_equity = max(peak_equity, equity)
            state = RiskState(
                ts=event.ts,
                equity=equity,
                peak_equity=peak_equity,
                realised_vol=_symbol_vols(store, strategies, symbols, risk),
                # Ein einmal ausgeloester Halt wird mitgeschleppt. Ohne das
                # saehe jede RiskLimits-Implementierung, die ihren Zustand aus
                # `state` statt aus sich selbst liest, bei jedem Bar ein
                # frisches, ungehaltenes Portfolio -- und wuerde nie anhalten.
                halted=halted,
            )
            target, reasons = risk.apply(target, state)
            halted = halted or state.halted or bool(getattr(risk, "halted", False))
            # Nur Aenderungen protokollieren. Ein greifender Cap oder ein
            # ausgeloester Halt meldet sich sonst bei jedem Bar erneut und
            # begraebt die interessanten Eingriffe unter Tausenden Kopien --
            # ein Protokoll, das niemand liest, ist keines.
            for reason in reasons:
                if reason not in last_reasons:
                    risk_events.append((event.ts, reason))
            last_reasons = set(reasons)

        final_weights = target

        if warmup_end is not None:
            order = _rebalance_order(
                broker, bar.symbol, target.get(bar.symbol, 0.0), bar.close,
                last_price, cfg,
            )
            if order is not None:
                broker.submit(order)

        row = {
            "ts": event.ts,
            "equity": broker.equity(last_price),
            "cash": broker.cash,
            "fees_paid": broker.fees_paid,
            "turnover": broker.turnover,
            f"price_{bar.symbol}": bar.close,
        }
        row.update({f"weight_{sym}": w for sym, w in target.items()})
        rows.append(row)

    equity = pd.DataFrame(rows).groupby("ts", as_index=False).last()
    equity = equity.sort_values("ts", ignore_index=True).ffill()

    return PortfolioResult(
        equity=equity,
        fills=broker.fills,
        strategy_ids=strategy_ids,
        symbols=symbols,
        allocator=allocator.describe(),
        config=cfg,
        allocations=pd.DataFrame(alloc_rows),
        risk_events=risk_events,
        warmup_end=warmup_end,
        timeframe=alloc_tf,
        meta={"n_events": len(events), "final_weights": final_weights},
    )


def _check_coverage(
    strategies: dict[str, Strategy], bars: dict[tuple[str, str], list[Bar]]
) -> None:
    """Sicherstellen, dass jede Strategie die Daten bekommt, die sie braucht.

    Ohne diese Pruefung laeuft eine Strategie stumm mit und traegt still
    nichts bei -- das faellt in den Kennzahlen kaum auf und kostet Stunden
    bei der Fehlersuche.
    """
    missing = [
        f"{sid} braucht {sym} @ {strategy.timeframe}"
        for sid, strategy in strategies.items()
        for sym in strategy.symbols
        if (sym, strategy.timeframe) not in bars
    ]
    if missing:
        raise ValueError("Fehlende Bar-Daten:\n  " + "\n  ".join(missing))


def _portfolio_is_warm(
    strategies: dict[str, Strategy], bars_seen: dict[tuple[str, str], int]
) -> bool:
    """Handelt das Portfolio schon?

    Erst wenn **jede** Strategie ihren Warmup hinter sich hat. Wuerde man
    frueher beginnen, verteilte der Allokator Kapital an Strategien, die
    noch gar kein Signal geben koennen -- deren Nullgewicht saehe dann aus
    wie eine bewusste Flat-Entscheidung und verwaesserte das Portfolio.

    Der Preis dafuer ist, dass die langsamste Strategie den Start bestimmt.
    Das ist bewusst so: lieber spaeter anfangen als die ersten Monate mit
    einem halb blinden Portfolio verbringen.
    """
    for strategy in strategies.values():
        for symbol in strategy.symbols:
            if bars_seen.get((symbol, strategy.timeframe), 0) < strategy.warmup_bars:
                return False
    return True


def _accrue_paper_returns(
    active: list[str],
    strategies: dict[str, Strategy],
    weights: dict[str, dict[str, float]],
    returns: dict[str, deque[float]],
    bar: Bar,
    previous_close: float | None,
) -> None:
    """Rendite fortschreiben, die jede Strategie auf dem Papier erzielt haette.

    Bewusst **ohne Kosten**: diese Reihe dient ausschliesslich dazu, dass der
    Allokator die Strategien untereinander vergleichen kann. Wuerde man ihr
    Kosten aufbuerden, haenge der Vergleich an der Positionsgroesse, die der
    Allokator gerade selbst vergeben hat -- eine Rueckkopplung, die eine
    zufaellig kleine Allokation dauerhaft klein hielte.

    Die tatsaechliche Kontoentwicklung enthaelt selbstverstaendlich alle
    Kosten; nur dieser Vergleichsmassstab ist brutto.
    """
    if previous_close is None or previous_close <= 0:
        return
    bar_return = bar.close / previous_close - 1.0
    for sid in active:
        held = weights[sid].get(bar.symbol, 0.0)
        returns[sid].append(held * bar_return)


def _allocate(
    allocator: Allocator,
    strategy_ids: list[str],
    returns: dict[str, deque[float]],
    broker: SimBroker,
    last_price: dict[str, float],
    current: dict[str, float],
    ts: datetime,
    timeframe: str,
) -> dict[str, float]:
    """Allokator aufrufen und seine Ausgabe auf Brauchbarkeit pruefen.

    Der Allokator ist ab Phase 3 ein LLM. Schon hier gilt deshalb: seine
    Ausgabe wird nicht geglaubt, sondern geprueft. Faellt sie unbrauchbar
    aus, wird auf Gleichgewichtung zurueckgefallen statt den Lauf
    abzubrechen -- ein Ausfall des Allokators darf das Portfolio nicht in
    einen undefinierten Zustand bringen.
    """
    ctx = AllocationContext(
        ts=ts,
        strategy_ids=list(strategy_ids),
        returns={sid: np.asarray(returns[sid], dtype=float) for sid in strategy_ids},
        equity=broker.equity(last_price),
        current=dict(current),
        timeframe=timeframe,
    )
    proposal = allocator.allocate(ctx)

    clean = {
        sid: float(proposal.get(sid, 0.0))
        for sid in strategy_ids
        if math.isfinite(proposal.get(sid, 0.0))
    }
    total = sum(abs(v) for v in clean.values())
    if not clean or total == 0:
        return {sid: 1.0 / len(strategy_ids) for sid in strategy_ids}
    if total > 1.0:
        clean = {sid: v / total for sid, v in clean.items()}
    return {sid: clean.get(sid, 0.0) for sid in strategy_ids}


def _symbol_vols(
    store: FeatureStore,
    strategies: dict[str, Strategy],
    symbols: list[str],
    risk: RiskLimits | None,
) -> dict[str, float]:
    """Realisierte Volatilitaet je Symbol fuer die Risk-Engine.

    Genommen wird der groebste Timeframe, auf dem das Symbol gehandelt wird:
    er rauscht am wenigsten, und die Risikoschicht soll nicht auf jedes
    Minutenzucken reagieren.

    Die Fensterlaenge kommt aus der Config der Risk-Engine selbst, nicht aus
    einer Zahl an dieser Stelle. Zwei Orte, die denselben Parameter
    festlegen, laufen auseinander -- und dann beschneidet die Risikoschicht
    nach einer anderen Vola, als sie zu messen glaubt.
    """
    from qt.portfolio.risk import RiskConfig, realised_vol_map

    cfg = getattr(risk, "cfg", None)
    if not isinstance(cfg, RiskConfig):
        cfg = RiskConfig()

    by_timeframe: dict[str, dict[str, np.ndarray]] = {}
    for symbol in symbols:
        timeframes = sorted(
            {s.timeframe for s in strategies.values() if symbol in s.symbols},
            key=timeframe_seconds,
            reverse=True,
        )
        for timeframe in timeframes:
            window = store.window(symbol, timeframe)
            if len(window) <= cfg.vol_lookback:
                continue
            by_timeframe.setdefault(timeframe, {})[symbol] = window.closes()
            break

    out: dict[str, float] = {}
    for timeframe, closes in by_timeframe.items():
        for symbol, vol in realised_vol_map(closes, timeframe, cfg).items():
            if math.isfinite(vol):
                out[symbol] = vol
    return out


def _rebalance_order(
    broker: SimBroker,
    symbol: str,
    target_weight: float,
    reference_price: float,
    prices: dict[str, float],
    cfg: BacktestConfig,
) -> Order | None:
    """Order, die die Ist-Position auf das Zielgewicht bringt.

    Identisch zur Einzelstrategie-Variante in `engine.py`, inklusive
    Rebalancing-Band (ADR-008).
    """
    # Bewertung mit den **letzten bekannten Preisen aller** Symbole. Wuerde
    # hier nur der Preis des aktuellen Symbols stehen, faellt SimBroker.equity
    # fuer alle uebrigen Positionen auf deren Einstandspreis zurueck -- die
    # Positionsgroesse haenge dann an einem Eigenkapital, in dem der Rest des
    # Portfolios zu historischen Kursen steht.
    equity = broker.equity({**prices, symbol: reference_price})
    if equity <= 0 or reference_price <= 0 or not math.isfinite(target_weight):
        return None

    capped = max(-cfg.max_gross_exposure, min(cfg.max_gross_exposure, target_weight))
    target_qty = capped * equity / reference_price
    delta = target_qty - broker.qty(symbol)
    delta_notional = abs(delta) * reference_price

    threshold = max(cfg.min_trade_notional, cfg.rebalance_band * equity)
    if delta_notional < threshold:
        return None
    return Order(symbol=symbol, qty=delta, reason=f"target_weight={capped:.4f}")
