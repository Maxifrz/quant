"""Die Begriffe, die alle Module teilen.

Bewusst schlank gehalten: je mehr hier steht, desto mehr muss beim Umbau
mitwandern. Alles, was nur ein Modul braucht, gehoert dorthin.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

# Unterstuetzte Timeframes und ihre Laenge in Sekunden.
TIMEFRAMES: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def timeframe_seconds(tf: str) -> int:
    """Laenge eines Timeframes in Sekunden."""
    try:
        return TIMEFRAMES[tf]
    except KeyError:
        raise ValueError(
            f"Unbekannter Timeframe {tf!r}. Bekannt: {sorted(TIMEFRAMES)}"
        ) from None


def bars_per_year(tf: str) -> float:
    """Wieviele Bars dieses Timeframes in ein Jahr passen.

    Krypto handelt 24/7, deshalb keine Handelskalender-Korrektur.
    """
    return 365.25 * 86400 / timeframe_seconds(tf)


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class Bar:
    """Ein abgeschlossener OHLCV-Bar.

    `ts` ist die **Open-Zeit** des Bars (so liefern es alle Exchanges).
    Der Bar ist erst ab `close_ts` bekannt -- das ist der Kern der
    Point-in-Time-Regel, siehe qt.features.registry.
    """

    symbol: str
    timeframe: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def close_ts(self) -> datetime:
        """Zeitpunkt, ab dem dieser Bar bekannt ist."""
        from datetime import timedelta

        return self.ts + timedelta(seconds=timeframe_seconds(self.timeframe))


@dataclass(frozen=True, slots=True)
class Order:
    """Eine Market-Order in Basiswaehrungs-Einheiten.

    `qty` ist vorzeichenbehaftet: positiv = kaufen, negativ = verkaufen.
    Wir kennen bewusst nur Market-Orders -- Limit-Orders realistisch zu
    simulieren erfordert ein Orderbuch-Modell, das wir nicht haben. Lieber
    ein ehrliches einfaches Modell als ein optimistisches komplexes.
    """

    symbol: str
    qty: float
    reason: str = ""

    @property
    def side(self) -> Side:
        return Side.BUY if self.qty > 0 else Side.SELL


@dataclass(frozen=True, slots=True)
class Fill:
    """Eine ausgefuehrte Order inklusive aller Reibungsverluste."""

    symbol: str
    ts: datetime
    qty: float
    price: float
    fee: float
    slippage_cost: float

    @property
    def notional(self) -> float:
        return abs(self.qty) * self.price

    @property
    def total_cost(self) -> float:
        return self.fee + self.slippage_cost


@dataclass(slots=True)
class Position:
    """Eine offene Position in einem Symbol."""

    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0

    def market_value(self, price: float) -> float:
        return self.qty * price


def equity(
    cash: float,
    positions: Mapping[str, Position],
    prices: Mapping[str, float],
) -> float:
    """Eigenkapital: Cash plus Marktwert aller Positionen.

    Fehlt fuer ein Symbol ein Preis, wird der Einstand angesetzt. Das ist
    keine Schaetzung, sondern die einzige ehrliche Wahl: ohne Preis ist die
    unrealisierte Bewertung unbekannt, und sie mit null anzusetzen waere ein
    Totalverlust, den niemand gemeldet hat.

    Steht hier und nicht in `qt.backtest.broker_sim`, weil ausser dem Broker
    inzwischen auch der Report-Pfad Eigenkapital ausrechnet -- und zwei
    Implementierungen derselben Groesse driften. Genau das ist der Fehler,
    den ADR-001 fuer den Backtest-Pfad vermeidet.
    """
    return cash + sum(
        position.market_value(prices.get(symbol, position.avg_price))
        for symbol, position in positions.items()
    )


@dataclass(slots=True)
class PortfolioSnapshot:
    """Zustand des Portfolios zu einem Zeitpunkt -- eine Zeile der Equity-Curve."""

    ts: datetime
    cash: float
    positions: dict[str, float] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    fees_paid: float = 0.0
    turnover: float = 0.0

    @property
    def equity(self) -> float:
        return self.cash + sum(
            qty * self.prices.get(sym, 0.0) for sym, qty in self.positions.items()
        )

    @property
    def gross_exposure(self) -> float:
        """Brutto-Exposure als Bruchteil des Eigenkapitals.

        Bei nicht-positivem Eigenkapital ist der Quotient nicht definiert.
        `inf` statt 0.0, weil 0.0 sich als "keine Position" laese -- ein
        gesprengtes Konto darf im Report nicht harmlos aussehen.
        """
        eq = self.equity
        if eq <= 0:
            return float("inf") if any(self.positions.values()) else 0.0
        return (
            sum(
                abs(qty) * self.prices.get(sym, 0.0)
                for sym, qty in self.positions.items()
            )
            / eq
        )


def utc(ts: datetime) -> datetime:
    """Auf UTC normalisieren. Naive Zeitstempel gelten als UTC.

    Alles im System ist tz-aware UTC. Gemischte Zeitzonen sind eine
    verlaessliche Quelle stiller Off-by-one-Fehler in Backtests.
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)
