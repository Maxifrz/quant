"""Soll gegen Ist -- der Vergleich, der bis Phase 7 keiner sein konnte.

ADR-037 hat dieses Modul ausdruecklich **nicht** gebaut, mit einer Begruendung,
die bis heute gilt: ein Abgleich braucht zwei unabhaengige Quellen, und mit nur
einem `SimBroker` gab es nichts, wogegen abgeglichen werden koennte. Ein Modul,
das den Kontostand mit sich selbst vergleicht, ist keine Pruefung, sondern eine
Zeile, die immer "in Ordnung" sagt.

Mit `qt.live.broker_ccxt` gibt es die zweite Quelle. Erst jetzt hat der
Vergleich einen Gegenstand:

    Soll  = der lokale Zustand (`PaperState`), fortgeschrieben aus Signalen
            und erwarteten Fills
    Ist   = das Guthaben, das die Boerse meldet

## Warum Abweichung ein Stopp ist und keine Korrektur

Es waere bequem, den lokalen Zustand einfach auf den Boersenstand zu ziehen.
Genau das darf nicht passieren. Eine Abweichung heisst, dass eine Annahme
falsch war -- eine Order ist nicht durchgekommen, eine Teilfuellung wurde nicht
bemerkt, eine Gebuehr wurde in der Basiswaehrung abgezogen. Wer den Zustand
nachzieht, loescht die Spur und faehrt mit demselben Fehler weiter, nur
unsichtbar. ZIEL.md sagt es fuer Phase D in einem Satz: "Divergenz hier ist ein
Stopp."

Dieses Modul **meldet** deshalb nur. Es hat keine Funktion, die etwas
angleicht, und das ist Absicht -- wie bei der Promotion im Research-Loop
(ADR-032), wo der fehlende Befehl die eigentliche Sicherung ist.

## Die Toleranz, und warum sie klein und sichtbar ist

Boersen runden Mengen auf ihr Raster und ziehen Gebuehren manchmal von der
gekauften Waehrung ab. Ein Abgleich ohne jede Toleranz meldete deshalb
staendig Abweichungen und waere nach einer Woche abgeschaltet. Eine grosszuegige
Toleranz dagegen ist der Ort, an dem ein echter Fehler verschwindet.

Der Kompromiss hier: die Toleranz ist **relativ und klein** (Default 0,1 %),
und der Bericht nennt in jedem Fall die absoluten Zahlen -- auch bei "in
Ordnung". Wer sie liest, sieht die Groesse der Abweichung und nicht nur ihr
Urteil.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Abweichung", "ReconcileReport", "reconcile"]

#: Relativ, nicht absolut: eine feste Menge waere fuer BTC riesig und fuer
#: DOGE unsichtbar.
STANDARD_TOLERANZ = 0.001


@dataclass(frozen=True, slots=True)
class Abweichung:
    """Ein Symbol, zwei Zahlen, ein Urteil."""

    symbol: str
    soll: float
    ist: float
    toleranz: float = STANDARD_TOLERANZ

    @property
    def differenz(self) -> float:
        return self.ist - self.soll

    @property
    def relativ(self) -> float:
        """Bezogen auf die groessere der beiden Mengen.

        Auf `soll` zu beziehen bricht genau im interessantesten Fall: soll 0,
        ist 0,3 -- eine Position, die es nicht geben duerfte, ergaebe eine
        Division durch null und damit keine Meldung.
        """
        nenner = max(abs(self.soll), abs(self.ist))
        return abs(self.differenz) / nenner if nenner > 0 else 0.0

    @property
    def ok(self) -> bool:
        return self.relativ <= self.toleranz

    def zeile(self) -> str:
        marke = "ok  " if self.ok else "NEIN"
        return (
            f"  {marke} {self.symbol:<12} soll {self.soll:>16.8f}   "
            f"ist {self.ist:>16.8f}   Abw {self.relativ:>7.2%}"
        )


@dataclass(slots=True)
class ReconcileReport:
    """Das Ergebnis des Abgleichs."""

    positionen: list[Abweichung] = field(default_factory=list)
    guthaben: Abweichung | None = None

    @property
    def alle(self) -> list[Abweichung]:
        return [*self.positionen, *([self.guthaben] if self.guthaben else [])]

    @property
    def ok(self) -> bool:
        return all(a.ok for a in self.alle)

    @property
    def auffaellig(self) -> list[Abweichung]:
        return [a for a in self.alle if not a.ok]

    def table(self) -> str:
        zeilen = [
            "  Abgleich: lokaler Zustand gegen Boersenbestand",
            "  " + "-" * 70,
        ]
        zeilen += [a.zeile() for a in self.positionen]
        if self.guthaben is not None:
            zeilen.append(self.guthaben.zeile())
        zeilen.append("  " + "-" * 70)
        if self.ok:
            zeilen.append("  In Ordnung -- beide Quellen stimmen im Rahmen der Toleranz.")
        else:
            zeilen.append(
                f"  ABWEICHUNG in {len(self.auffaellig)} von {len(self.alle)} "
                "Zeilen."
            )
            zeilen.append(
                "  Das ist ein Stopp, keine Korrektur: der lokale Zustand wird "
                "nicht nachgezogen.\n"
                "  Erst die Ursache finden -- nicht durchgekommene Order, "
                "Teilfuellung, Gebuehr in\n"
                "  der Basiswaehrung -- dann von Hand entscheiden."
            )
        return "\n".join(zeilen)


def reconcile(
    soll_positionen: dict[str, float],
    ist_positionen: dict[str, float],
    soll_guthaben: float | None = None,
    ist_guthaben: float | None = None,
    toleranz: float = STANDARD_TOLERANZ,
) -> ReconcileReport:
    """Zwei Bestaende vergleichen und die Abweichungen benennen.

    Verglichen wird ueber die **Vereinigung** beider Symbolmengen, nicht ueber
    die des Solls. Der gefaehrlichste Fall ist die Position, die es lokal gar
    nicht gibt: eine Order, die durchkam, obwohl sie als abgelehnt verbucht
    wurde. Wer nur ueber das Soll iteriert, sieht genau die nicht.
    """
    symbole = sorted(set(soll_positionen) | set(ist_positionen))
    positionen = [
        Abweichung(
            symbol=sym,
            soll=float(soll_positionen.get(sym, 0.0)),
            ist=float(ist_positionen.get(sym, 0.0)),
            toleranz=toleranz,
        )
        for sym in symbole
    ]

    guthaben = None
    if soll_guthaben is not None and ist_guthaben is not None:
        guthaben = Abweichung(
            symbol="(Guthaben)",
            soll=float(soll_guthaben),
            ist=float(ist_guthaben),
            toleranz=toleranz,
        )

    return ReconcileReport(positionen=positionen, guthaben=guthaben)
