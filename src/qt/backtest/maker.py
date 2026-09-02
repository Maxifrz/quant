"""Wie oft wuerde eine passive Limit-Order ueberhaupt gefuellt?

Das Maker-Kostenregime (`qt.core.config.COINBASE_MAKER`) ist mit Abstand das
guenstigste im Projekt: keine Spanne, keine Slippage, die halbe Gebuehr. ADR-009
hat daran die Hoffnung geknuepft, dass eine Strategie, die als Taker bei Faktor
0,46 landet, als Maker bei 4,04 laege.

Diese Rechnung unterstellt etwas, das sie nicht mitrechnet: **dass die Order
gefuellt wird.** Wer als Maker handeln will, muss passiv im Buch liegen -- ein
Kauflimit *unter* dem Markt, ein Verkaufslimit *darueber*. Kommt der Kurs nicht
zurueck, passiert nichts. Eine nicht gefuellte Order ist keine Ersparnis,
sondern ein verpasster Trade, und in einem Trendsignal ist der verpasste Trade
systematisch der, der funktioniert haette: Trendfolge kauft in die Staerke, und
genau dann laeuft der Kurs dem Kauflimit davon.

Ob dieser Einwand zutrifft, ist keine Meinungsfrage. Der Store haelt Open, High
und Low jedes Bars; damit ist je vorgemerkter Order nachrechenbar, was
passiert waere.

**Das Modell.** Die Engine merkt eine Order am Schluss von Bar t vor und fuehrt
sie am Open von Bar t+1 aus (`engine.run_backtest`, Schritt 2). Das Limit
liegt beim Signalpreis, also dem Schluss von Bar t. Fuer Bar t+1 gilt dann:

* **marktnah** -- das Limit ist schon beim Open erreichbar (Kauf: Open <= Limit).
  Die Order wird gefuellt, aber sie nimmt Liquiditaet und zahlt Taker.
* **passiv gefuellt** -- das Limit liegt beim Open jenseits des Marktes, und der
  Bar handelt spaeter hindurch (Kauf: Open > Limit und Low <= Limit). Nur das
  ist ein Maker-Fill.
* **nicht gefuellt** -- der Bar erreicht das Limit nie.

**Was das Modell nicht kann**, und das begrenzt die Aussage:

* Ein Bar, dessen Low das Limit *beruehrt*, haette die Order vielleicht nur
  teilweise oder gar nicht gefuellt -- in der Warteschlange stehen andere
  zuerst. Das Modell zaehlt jede Beruehrung als vollen Fill und ist damit
  **zu freundlich**. Die gemessene Fuellquote ist eine Obergrenze.
* Was ein Ausfall wirklich kostet, haengt daran, was die Strategie danach tut
  (nachlaufen, aussetzen, spaeter erneut versuchen). Hier steht die Quote, nicht
  die Folgekosten.

Eine Obergrenze genuegt aber fuer die Frage, die ADR-056 zu entscheiden hat:
liegt sie niedrig, ist das Maker-Regime erledigt, ohne dass es die feinere
Rechnung braucht.
"""

from __future__ import annotations

from dataclasses import dataclass

from qt.core.types import Bar, Fill


@dataclass(frozen=True, slots=True)
class MakerQuote:
    """Dreiteilung aller vorgemerkten Orders eines Laufs.

    Gezaehlt wird zweimal: nach Anzahl und nach Gegenwert. Beides ist noetig,
    weil eine Strategie viele kleine Rebalancings und wenige grosse Einstiege
    haben kann -- und wenn ausgerechnet die grossen ausfallen, sagt die
    Stueckzahl das Gegenteil vom Geld.
    """

    symbol: str
    marktnah: int
    passiv_gefuellt: int
    nicht_gefuellt: int
    notional_marktnah: float
    notional_passiv: float
    notional_ausfall: float

    @property
    def n(self) -> int:
        return self.marktnah + self.passiv_gefuellt + self.nicht_gefuellt

    @property
    def maker_quote(self) -> float:
        """Anteil der Orders, die wirklich als Maker durchgingen."""
        return self.passiv_gefuellt / self.n if self.n else float("nan")

    @property
    def ausfallquote(self) -> float:
        """Anteil der Orders, die gar nicht gefuellt worden waeren."""
        return self.nicht_gefuellt / self.n if self.n else float("nan")

    @property
    def ausfallquote_notional(self) -> float:
        """Dasselbe nach Gegenwert -- die Zahl, die zaehlt."""
        gesamt = self.notional_marktnah + self.notional_passiv + self.notional_ausfall
        return self.notional_ausfall / gesamt if gesamt > 0 else float("nan")


def _bar_index(bars: list[Bar]) -> dict:
    return {bar.ts: i for i, bar in enumerate(bars)}


def klassifiziere(fills: list[Fill], bars: dict[str, list[Bar]]) -> list[MakerQuote]:
    """Jede Order eines Laufs den drei Faellen zuordnen, je Symbol.

    Die Fills stammen aus einem Taker-Lauf und sagen, **wann** die Strategie
    handeln wollte und in welche Richtung. Genau das wird gebraucht: die Frage
    ist nicht, was ein Maker-Lauf getan haette, sondern ob die Absichten des
    vorliegenden Laufs passiv ausfuehrbar gewesen waeren.
    """
    ergebnis = []
    for symbol, symbol_bars in sorted(bars.items()):
        idx = _bar_index(symbol_bars)
        zaehler = {"marktnah": 0, "passiv": 0, "ausfall": 0}
        notional = {"marktnah": 0.0, "passiv": 0.0, "ausfall": 0.0}

        for fill in fills:
            if fill.symbol != symbol:
                continue
            i = idx.get(fill.ts)
            # Ein Fill ohne Vorgaengerbar hat keinen Signalpreis: die Order
            # kann dann nicht am Schluss des Vorbars vorgemerkt worden sein.
            if i is None or i == 0:
                continue

            limit = symbol_bars[i - 1].close
            bar = symbol_bars[i]
            wert = abs(fill.qty) * limit

            if fill.qty > 0:
                marktnah = bar.open <= limit
                erreicht = bar.low <= limit
            else:
                marktnah = bar.open >= limit
                erreicht = bar.high >= limit

            if marktnah:
                schluessel = "marktnah"
            elif erreicht:
                schluessel = "passiv"
            else:
                schluessel = "ausfall"
            zaehler[schluessel] += 1
            notional[schluessel] += wert

        if not any(zaehler.values()):
            continue
        ergebnis.append(
            MakerQuote(
                symbol=symbol,
                marktnah=zaehler["marktnah"],
                passiv_gefuellt=zaehler["passiv"],
                nicht_gefuellt=zaehler["ausfall"],
                notional_marktnah=notional["marktnah"],
                notional_passiv=notional["passiv"],
                notional_ausfall=notional["ausfall"],
            )
        )
    return ergebnis


def zusammenfassen(quoten: list[MakerQuote]) -> MakerQuote:
    """Alle Symbole zu einer Zeile addieren, fuer die Kopfzahl."""
    if not quoten:
        raise ValueError("Keine Quoten zum Zusammenfassen.")
    return MakerQuote(
        symbol="gesamt",
        marktnah=sum(q.marktnah for q in quoten),
        passiv_gefuellt=sum(q.passiv_gefuellt for q in quoten),
        nicht_gefuellt=sum(q.nicht_gefuellt for q in quoten),
        notional_marktnah=sum(q.notional_marktnah for q in quoten),
        notional_passiv=sum(q.notional_passiv for q in quoten),
        notional_ausfall=sum(q.notional_ausfall for q in quoten),
    )
