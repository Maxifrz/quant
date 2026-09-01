"""Fills zu Round-Trips verdichten -- die Trade-Sicht, die dem Projekt fehlte.

Bis hierher gab es zwei Aufloesungen: die Equity-Kurve (je Bar) und einzelne
Fills. Beides beantwortet die naheliegendste Frage **nicht**: kam ein Verlust
aus wenigen grossen Fehlgriffen oder aus vielen kleinen Gebuehrenverlusten?
`Metrics.hit_rate` hilft dabei nicht -- sie zaehlt Bars mit positiver Rendite,
nicht Trades mit positivem Ergebnis. Eine Strategie kann in 48% der Bars
gewinnen und trotzdem in 8 von 10 Trades verlieren.

**Was ein Round-Trip hier ist:** die Zeitspanne, in der eine Position von null
verschieden ist. Sie beginnt mit dem Fill, der aus einer flachen Position
herausfuehrt, und endet mit dem Fill, der sie wieder auf null bringt. Ein Fill,
der das Vorzeichen dreht, schliesst den alten Trade und eroeffnet im selben
Moment den neuen -- seine Gebuehr wird dabei anteilig nach Menge aufgeteilt,
sonst traegt einer der beiden Trades Kosten, die er nicht verursacht hat.

**Was dieses Modul ausdruecklich nicht tut:** es interpretiert nicht. Es gibt
keine Aussage darueber, *warum* ein Trade schieflief. Diese Grenze ist Absicht
(ADR-049): eine Auswertung, die aus Gewinnern und Verlierern Regeln ableitet,
ist ueberwachtes Lernen auf denselben Daten -- mit unzaehlbaren Freiheitsgraden
und an der Deflated Sharpe Ratio (ADR-005/032) vorbei. Messen ist harmlos,
Schlussfolgern nicht.

**Zur Renditedefinition:** `return_pct` bezieht sich auf das **eingesetzte
Nominal beim Einstieg**, nicht auf das Kontoguthaben. Eine halb so grosse
Position mit derselben Kursbewegung soll dieselbe Prozentzahl zeigen; sonst
misst man die Positionsgroesse und nennt es Trefferquote.

`mae` und `mfe` (groesster Buchverlust und groesster Buchgewinn waehrend der
Haltedauer) kommen aus der Preisspalte der Equity-Kurve. Sie sind der Grund,
warum dieses Modul die `BacktestResult` und nicht nur die Fill-Liste braucht:
ohne sie sieht ein Trade, der zwischendurch 40% im Minus stand und flach
schloss, genauso aus wie einer, der nie unter Wasser war.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from qt.core.types import Fill

__all__ = ["RoundTrip", "round_trips", "summary_table"]


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """Eine Position von null nach null."""

    symbol: str
    entry_ts: datetime
    exit_ts: datetime
    direction: int  # +1 long, -1 short
    qty: float  # groesste gehaltene Menge, absolut
    entry_price: float  # mengengewichtet
    exit_price: float  # mengengewichtet
    bars_held: int
    gross_pnl: float  # vor Gebuehren, nach Slippage (die steckt im Preis)
    fees: float
    net_pnl: float
    entry_notional: float
    mae: float  # groesster Buchverlust waehrend der Haltedauer, in Prozent
    mfe: float  # groesster Buchgewinn, in Prozent

    @property
    def return_pct(self) -> float:
        """Ergebnis bezogen auf das eingesetzte Nominal."""
        if self.entry_notional <= 0:
            return 0.0
        return self.net_pnl / self.entry_notional

    @property
    def cost_share(self) -> float:
        """Anteil der Gebuehren am Bruttoergebnis, absolut gerechnet.

        Ueber 1.0 heisst: die Gebuehren haben einen Gewinn in einen Verlust
        gedreht (oder einen Verlust mehr als verdoppelt). Das ist die Zahl,
        an der man Ueberhandeln erkennt, ohne die Strategie zu kennen.
        """
        if self.gross_pnl == 0:
            return math.inf if self.fees > 0 else 0.0
        return self.fees / abs(self.gross_pnl)

    @property
    def won(self) -> bool:
        return self.net_pnl > 0


def round_trips(result) -> list[RoundTrip]:
    """Round-Trips aus einem `BacktestResult`.

    Der Positionsverlauf wird aus den Fills rekonstruiert und nicht aus den
    Gewichten: Gewichte sind die *Absicht*, Fills sind, was passiert ist. Eine
    Order, die am Mindestnominal gescheitert ist, taucht in den Gewichten auf
    und in den Fills nicht -- und die Trade-Sicht soll zeigen, was gehandelt
    wurde.
    """
    preise = _preisreihen(result.equity)
    trades: list[RoundTrip] = []

    for symbol in sorted({fill.symbol for fill in result.fills}):
        fills = [f for f in result.fills if f.symbol == symbol]
        fills.sort(key=lambda f: f.ts)
        trades.extend(_je_symbol(symbol, fills, preise.get(symbol)))

    trades.sort(key=lambda t: t.entry_ts)
    return trades


def _je_symbol(
    symbol: str, fills: list[Fill], preise: pd.Series | None
) -> list[RoundTrip]:
    trades: list[RoundTrip] = []
    position = 0.0
    offen: list[tuple[Fill, float]] = []  # Fill und die davon eroeffnende Menge

    for fill in fills:
        rest = fill.qty

        # Teil, der eine bestehende Position schliesst.
        if position != 0.0 and (position > 0) != (rest > 0):
            schliessend = min(abs(rest), abs(position)) * (1 if rest > 0 else -1)
            offen.append((fill, schliessend))
            position += schliessend
            rest -= schliessend

            if abs(position) < 1e-12:
                trades.append(_bauen(symbol, offen, preise))
                offen = []
                position = 0.0

        # Rest eroeffnet (oder vergroessert) eine Position.
        if abs(rest) > 1e-12:
            offen.append((fill, rest))
            position += rest

    # Eine am Laufende offene Position ist kein Round-Trip. Sie hier
    # mitzuzaehlen hiesse, ein unrealisiertes Ergebnis als realisiertes
    # auszuweisen -- genau die Sorte Schoenrechnung, die ein Backtest belohnt.
    return trades


def _bauen(
    symbol: str, teile: list[tuple[Fill, float]], preise: pd.Series | None
) -> RoundTrip:
    erster = teile[0][0]
    letzter = teile[-1][0]
    richtung = 1 if teile[0][1] > 0 else -1

    eingang = [(f, q) for f, q in teile if (q > 0) == (richtung > 0)]
    ausgang = [(f, q) for f, q in teile if (q > 0) != (richtung > 0)]

    menge = sum(abs(q) for _, q in eingang)
    entry_price = _vwap(eingang)
    exit_price = _vwap(ausgang)

    # Cashflow: -qty * price je Teil. Slippage steckt bereits im Preis
    # (siehe SimBroker), sie darf deshalb nicht noch einmal abgezogen werden.
    brutto = -sum(q * f.price for f, q in teile)

    # Gebuehren anteilig: ein Fill kann zwei Trades beruehren, wenn er das
    # Vorzeichen dreht. Ohne die Aufteilung traegt einer von beiden Kosten,
    # die er nicht verursacht hat.
    gebuehren = sum(f.fee * abs(q) / abs(f.qty) for f, q in teile if f.qty)

    nominal = menge * entry_price
    mae, mfe = _mae_mfe(preise, erster.ts, letzter.ts, entry_price, richtung)

    return RoundTrip(
        symbol=symbol,
        entry_ts=erster.ts,
        exit_ts=letzter.ts,
        direction=richtung,
        qty=menge,
        entry_price=entry_price,
        exit_price=exit_price,
        bars_held=_bars_dazwischen(preise, erster.ts, letzter.ts),
        gross_pnl=brutto,
        fees=gebuehren,
        net_pnl=brutto - gebuehren,
        entry_notional=nominal,
        mae=mae,
        mfe=mfe,
    )


def _vwap(teile: list[tuple[Fill, float]]) -> float:
    menge = sum(abs(q) for _, q in teile)
    if menge <= 0:
        return 0.0
    return sum(f.price * abs(q) for f, q in teile) / menge


def _preisreihen(equity: pd.DataFrame) -> dict[str, pd.Series]:
    """`price_{symbol}`-Spalten der Equity-Kurve als Reihen je Symbol."""
    reihen: dict[str, pd.Series] = {}
    for spalte in equity.columns:
        if not spalte.startswith("price_"):
            continue
        symbol = spalte[len("price_") :]
        reihen[symbol] = pd.Series(
            equity[spalte].to_numpy(), index=pd.to_datetime(equity["ts"])
        ).dropna()
    return reihen


def _bars_dazwischen(
    preise: pd.Series | None, start: datetime, ende: datetime
) -> int:
    if preise is None or preise.empty:
        return 0
    fenster = preise.loc[(preise.index >= start) & (preise.index <= ende)]
    return max(len(fenster) - 1, 0)


def _mae_mfe(
    preise: pd.Series | None,
    start: datetime,
    ende: datetime,
    entry_price: float,
    richtung: int,
) -> tuple[float, float]:
    """Groesster Buchverlust und -gewinn waehrend der Haltedauer, in Prozent.

    Ohne Preisreihe `nan` statt `0.0`: "nicht gemessen" und "war nie im Minus"
    sind verschiedene Aussagen, und die zweite waere hier erfunden.
    """
    if preise is None or preise.empty or entry_price <= 0:
        return math.nan, math.nan

    fenster = preise.loc[(preise.index >= start) & (preise.index <= ende)]
    if fenster.empty:
        return math.nan, math.nan

    bewegung = (fenster.to_numpy() / entry_price - 1.0) * richtung
    return float(bewegung.min()), float(bewegung.max())


def summary_table(trades: list[RoundTrip]) -> str:
    """Kennzahlen ueber alle Round-Trips, plus Gewinner und Verlierer getrennt.

    Die Trennung ist der Punkt: ein Gesamtergebnis sagt nicht, ob es aus
    wenigen grossen Fehlgriffen kommt oder aus vielen kleinen. Genau diese
    Frage war ueber die Equity-Kurve nicht zu beantworten.
    """
    if not trades:
        return "Keine abgeschlossenen Round-Trips."

    gewinner = [t for t in trades if t.won]
    verlierer = [t for t in trades if not t.won]

    zeilen = [
        f"{'':22}{'alle':>12}{'Gewinner':>12}{'Verlierer':>12}",
        "-" * 58,
    ]

    def zeile(name: str, fn, formatter=lambda x: f"{x:.2f}") -> str:
        werte = []
        for gruppe in (trades, gewinner, verlierer):
            werte.append(formatter(fn(gruppe)) if gruppe else "--")
        return f"{name:22}{werte[0]:>12}{werte[1]:>12}{werte[2]:>12}"

    def mittel(werte: list[float]) -> float:
        endlich = [w for w in werte if math.isfinite(w)]
        return sum(endlich) / len(endlich) if endlich else math.nan

    zeilen += [
        zeile("Anzahl", len, lambda x: f"{x:,}"),
        zeile("Anteil", lambda g: len(g) / len(trades), lambda x: f"{x:.1%}"),
        zeile(
            "Netto-Ergebnis",
            lambda g: sum(t.net_pnl for t in g),
            lambda x: f"{x:,.0f}",
        ),
        zeile(
            "Rendite i.M.",
            lambda g: mittel([t.return_pct for t in g]),
            lambda x: f"{x:.2%}",
        ),
        zeile(
            "Median-Rendite",
            lambda g: sorted(t.return_pct for t in g)[len(g) // 2],
            lambda x: f"{x:.2%}",
        ),
        zeile("Gebuehren", lambda g: sum(t.fees for t in g), lambda x: f"{x:,.0f}"),
        zeile(
            "Kostenanteil i.M.",
            lambda g: mittel([t.cost_share for t in g]),
            lambda x: f"{x:.1%}",
        ),
        zeile(
            "Haltedauer i.M.",
            lambda g: mittel([float(t.bars_held) for t in g]),
            lambda x: f"{x:,.0f} Bars",
        ),
        zeile(
            "groesster Buchverlust",
            lambda g: mittel([t.mae for t in g]),
            lambda x: f"{x:.1%}",
        ),
        zeile(
            "groesster Buchgewinn",
            lambda g: mittel([t.mfe for t in g]),
            lambda x: f"{x:.1%}",
        ),
    ]

    gesamt = sum(t.net_pnl for t in trades)
    if gewinner and gesamt != 0:
        groesster = max(t.net_pnl for t in gewinner)
        zeilen += [
            "",
            f"  Groesster Einzelgewinn traegt {groesster / gesamt:+.0%} des "
            "Gesamtergebnisses.",
            "  Liegt der Wert nahe bei 100%, ist das Ergebnis eine Stichprobe "
            "und keine Kante.",
        ]

    return "\n".join(zeilen)
