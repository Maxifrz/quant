"""Vom Zielgewicht zur Ordermenge -- mit echtem Geld statt mit Modellgeld.

Der Backtest darf beliebig kleine Mengen handeln. Eine Boerse darf das nicht:
sie hat eine Mindestmenge, einen Mindestgegenwert und ein Rundungsraster. Was
darunter faellt, wird nicht etwa ungenau ausgefuehrt -- es wird **gar nicht**
ausgefuehrt.

**Das ist ein Verhaltensunterschied und kein Rundungsfehler.** Unterhalb der
Mindestordergroesse wirkt die Boerse wie ein zusaetzliches Rebalancing-Band,
das der Backtest nicht kennt: die Anpassung findet nicht ungenauer statt,
sondern gar nicht. Zwei Konten mit derselben Strategie und demselben Signal
handeln dann verschieden, und das kleinere handelt weniger.

**Wie stark, ist eine Messung und keine Vermutung.** Coinbase BTC/USD am
2026-09-03: **keine** Mindestmenge, Mindestgegenwert **1 USD**, Raster 1e-8.
Damit greift der Effekt erst, wenn eine Anpassung unter einem Dollar liegt --
bei einem Prozent Band also unterhalb von rund 100 USD Kontogroesse. Die
allgemeine Fassung des Satzes waere zu stark; nachzusehen mit
`qt live groesse` (ADR-062).

Die Richtung ist zufaellig, nicht konservativ. Weniger Handel spart Gebuehren
und verpasst Ausstiege. Deshalb gibt dieses Modul den Grund mit heraus, statt
eine Null zurueckzugeben: `zu_klein` ist eine Beobachtung, die in den
Tagesreport gehoert, und nicht das Ausbleiben eines Ereignisses.

Die Grenzen kommen von der Boerse (`market['limits']`, `market['precision']`),
nicht aus einer Konstante hier. Eine gepflegte Kopie fremder Zahlen veraltet
still -- dieselbe Lehre wie bei den Gebuehrensaetzen in ADR-056.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["Marktgrenzen", "Sizing", "menge_fuer_zielgewicht"]


@dataclass(frozen=True, slots=True)
class Marktgrenzen:
    """Was die Boerse fuer ein Symbol zulaesst.

    Alle Felder sind optional, weil nicht jede Boerse alle meldet. `None`
    heisst "nicht gemeldet" und wird als "keine Grenze" behandelt -- nicht als
    null. Der Unterschied ist der zwischen einer fehlenden Angabe und einer
    Angabe, die alles verbietet.
    """

    min_menge: float | None = None
    min_gegenwert: float | None = None
    schritt: float | None = None
    max_menge: float | None = None

    @classmethod
    def aus_ccxt(cls, market: dict) -> Marktgrenzen:
        """Aus einem ccxt-Market-Dict lesen, ohne an fehlenden Feldern zu scheitern."""
        limits = market.get("limits") or {}
        menge = limits.get("amount") or {}
        kosten = limits.get("cost") or {}
        praezision = (market.get("precision") or {}).get("amount")

        # ccxt meldet `precision.amount` je nach Boerse als Nachkommastellen
        # (4) **oder** als Schrittweite (0.0001). Beides kommt vor, und beides
        # ist eine gueltige Antwort -- die Unterscheidung an der Groesse ist
        # der einzige Weg, ohne Boersen-Sonderfall auszukommen.
        schritt: float | None = None
        if praezision is not None:
            schritt = (
                10.0 ** -int(praezision) if praezision >= 1 else float(praezision)
            )
            if schritt <= 0:
                schritt = None

        return cls(
            min_menge=menge.get("min"),
            min_gegenwert=kosten.get("min"),
            schritt=schritt,
            max_menge=menge.get("max"),
        )


@dataclass(frozen=True, slots=True)
class Sizing:
    """Das Ergebnis: was bestellt wird, und warum nicht mehr.

    `grund` ist leer, wenn die Menge unveraendert durchging. Sonst steht dort,
    welche Grenze gegriffen hat -- damit ein ausgelassener Trade im Report
    auftaucht statt als Stille.
    """

    menge: float
    grund: str = ""

    @property
    def handelt(self) -> bool:
        return self.menge != 0.0


def _auf_schritt(menge: float, schritt: float | None) -> float:
    """Zum Raster hin **abrunden**, betragsmaessig.

    Abrunden und nicht kaufmaennisch runden: aufgerundet ueberschreitet eine
    Order die Grenze, gegen die sie gerade geprueft wurde. Betragsmaessig,
    damit eine Verkaufsorder nicht durch Rundung groesser wird als die
    Position, die sie schliessen soll.
    """
    if not schritt or schritt <= 0:
        return menge
    stufen = math.floor(abs(menge) / schritt)
    return math.copysign(stufen * schritt, menge)


def menge_fuer_zielgewicht(
    ziel_gewicht: float,
    aktuelle_menge: float,
    preis: float,
    eigenkapital: float,
    grenzen: Marktgrenzen | None = None,
    band: float = 0.0,
) -> Sizing:
    """Differenz zum Zielgewicht als handelbare Menge.

    `band` ist das Rebalancing-Band aus ADR-008, als Anteil des Eigenkapitals:
    darunter wird nicht nachgezogen. Es steht **vor** den Boersengrenzen,
    weil es eine Entscheidung der Strategie ist und keine der Boerse -- und
    weil die beiden sonst in der Begruendung durcheinandergerieten.

    Reihenfolge danach, und sie ist Absicht: erst auf das Raster abrunden,
    dann gegen die Mindestmenge und den Mindestgegenwert pruefen. Umgekehrt
    ginge eine Order durch, die die Mindestmenge nur vor dem Abrunden hielt.
    """
    if preis <= 0:
        return Sizing(0.0, "kein Preis")
    if eigenkapital <= 0:
        return Sizing(0.0, "kein Kapital")

    ziel_menge = ziel_gewicht * eigenkapital / preis
    differenz = ziel_menge - aktuelle_menge
    if differenz == 0.0:
        return Sizing(0.0, "")

    if band > 0 and abs(differenz) * preis < band * eigenkapital:
        return Sizing(0.0, f"unter dem Rebalancing-Band ({band:.1%})")

    grenzen = grenzen or Marktgrenzen()

    if grenzen.max_menge is not None and abs(differenz) > grenzen.max_menge:
        differenz = math.copysign(grenzen.max_menge, differenz)

    gerundet = _auf_schritt(differenz, grenzen.schritt)
    if gerundet == 0.0:
        return Sizing(
            0.0, f"unter dem Mengenraster der Boerse ({grenzen.schritt:g})"
        )

    if grenzen.min_menge is not None and abs(gerundet) < grenzen.min_menge:
        return Sizing(
            0.0, f"unter der Mindestmenge der Boerse ({grenzen.min_menge:g})"
        )

    if (
        grenzen.min_gegenwert is not None
        and abs(gerundet) * preis < grenzen.min_gegenwert
    ):
        return Sizing(
            0.0,
            f"unter dem Mindestgegenwert der Boerse ({grenzen.min_gegenwert:g})",
        )

    return Sizing(gerundet, "")
