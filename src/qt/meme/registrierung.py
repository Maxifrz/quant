"""Die Registrierung des Memecoin-Papiertests (ADR-081).

**Alles in dieser Datei stand fest, bevor der erste Testtag begann.** Die
Zahlen sind nicht gemessen und nicht optimiert, sie sind gesetzt -- und genau
deshalb duerfen sie sich nach dem Blick auf Ergebnisse nicht mehr aendern.
Dieselbe Regel wie bei den Gate-Schwellen (ADR-057): keine Option auf der
Befehlszeile, jede Aenderung ist ein Diff, und `tests/test_meme_registrierung.py`
haelt die Werte fest, sodass eine Aenderung zwei Diffs braucht.

Registriert am 2026-09-26, dem Kalibriertag. Der erste Testtag beginnt am
2026-09-27 um 00:00 UTC; bis dahin existiert kein einziger Datenpunkt, gegen
den eine dieser Zahlen haette angepasst werden koennen.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

# -- Zeitraum ----------------------------------------------------------------

#: Liefert nur die Schwelle fuer R1 am ersten Testtag. Seine Renditen werden
#: nie ausgewertet.
KALIBRIERTAG = date(2026, 9, 26)
ERSTER_TESTTAG = date(2026, 9, 27)
LETZTER_TESTTAG = date(2026, 10, 24)
#: Vorher gibt `qt meme auswerten` nichts heraus, nur die Vollstaendigkeit.
#: Zwischenblicke auf Ergebnisse sind die haeufigste Art, einen Test zu
#: verderben, ohne es zu merken: man hoert auf, wenn es gerade gut aussieht.
AUSWERTUNG_AB = datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)

# -- Grundgesamtheit und Stichprobe --------------------------------------------

PUMP_PROGRAMM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
#: Jeder Start (Create und CreateV2) laeuft ueber dieses Konto, jeder Handel
#: nicht. Seine Signaturen zaehlen die Starts vollstaendig auf, die toten
#: eingeschlossen -- eine Liste von Memecoins aus einer Kursseite enthielte
#: nur Ueberlebende.
MINT_AUTORITAET = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"

STICHPROBE_JE_TAG = 500
#: Mindestanteil fehlerfreier Datensaetze, damit ein Tag zaehlt.
MIN_VOLLSTAENDIG = 0.95
#: Weniger vollstaendige Testtage, und das Ergebnis ist "unentschieden".
MIN_TESTTAGE = 21

# -- Zeitplan je Token, in Sekunden nach dem Block des Starts --------------------

#: Wann die Regel entscheidet. Eine Minute: kein Wettlauf mit Snipern, die im
#: selben Block kaufen wie der Ersteller (Solidus Labs, Mai 2025).
SIGNAL_NACH = 60
#: Zwischen Entscheidung und Ausfuehrung. Alles, was bis dahin gehandelt
#: wurde, hat vor uns gehandelt.
LATENZ = 2
HALTEDAUER = 30 * 60

# -- Position und Kosten --------------------------------------------------------

EINSATZ_SOL = 0.25
#: Grundgebuehr plus Prioritaetsgebuehr, je Transaktion; zwei je Handel.
NETZWERK_SOL_JE_TX = 0.0002
#: Verkauf nach dem Kurvenabschluss in den PumpSwap-Pool. Pauschal und bewusst
#: grosszuegig; die tatsaechliche Gebuehr steht in keinem Ereignis der Kurve.
POOL_GEBUEHR = 0.01


@dataclass(frozen=True, slots=True)
class Szenario:
    """Wie viel schlechter die echte Ausfuehrung ist als die gerechnete.

    `abschlag` trifft jede Seite eines normalen Handels: Latenz jenseits der
    zwei Sekunden, Konkurrenz im selben Slot, Sandwich-Bots, die die
    Slippage-Toleranz ausschoepfen. `graduierung` trifft den Verkauf nach dem
    Kurvenabschluss, wenn alle Halter gleichzeitig in den neuen Pool draengen.
    """

    name: str
    abschlag: float
    graduierung: float


SZENARIEN = (
    Szenario("guenstig", 0.0, 0.0),
    Szenario("primaer", 0.01, 0.05),
    Szenario("streng", 0.03, 0.15),
)
#: Nur dieses Szenario entscheidet. Die anderen zeigen, wie viel an der
#: Annahme haengt.
PRIMAER = "primaer"

# -- Regeln -------------------------------------------------------------------

#: R0 kauft jeden Start der Stichprobe: die Kontrolle, was Teilnehmen kostet.
#: R1 kauft nur, wenn der SOL-Zufluss der ersten Minute -- ohne die
#: Transaktion des Starts selbst, also ohne den Kauf des Erstellers -- ueber
#: dem 95-%-Quantil des Vortags liegt. Das Quantil kommt aus dem Vortag,
#: damit keine Information aus der Zukunft des Testtags einfliesst.
R1_QUANTIL = 0.95
REGELN = ("R0", "R1")

# -- Entscheidung ---------------------------------------------------------------

MIN_HANDEL_R1 = 100
BOOTSTRAP_ZIEHUNGEN = 10_000
BOOTSTRAP_SAAT = 81
#: Zweiseitiges 95-%-Intervall aus einem Block-Bootstrap ueber Tage.
INTERVALL = (0.025, 0.975)

NEIN = "NEIN"
VIELLEICHT = "VIELLEICHT"
UNENTSCHIEDEN = "UNENTSCHIEDEN"

# -- Sammlung -------------------------------------------------------------------

#: Ein Tag wird gesammelt, wenn auch sein letzter Start seinen Ausstieg hinter
#: sich hat -- plus eine Stunde Luft.
SAMMELN_AB_NACH_TAGESENDE = timedelta(seconds=SIGNAL_NACH + LATENZ + HALTEDAUER + LATENZ + 3600)


def testtage() -> list[date]:
    """Alle 28 Testtage, aeltester zuerst."""
    tage = []
    tag = ERSTER_TESTTAG
    while tag <= LETZTER_TESTTAG:
        tage.append(tag)
        tag += timedelta(days=1)
    return tage


def alle_tage() -> list[date]:
    """Kalibriertag und Testtage -- alles, was gesammelt wird."""
    return [KALIBRIERTAG, *testtage()]


def tagesgrenzen(tag: date) -> tuple[int, int]:
    """Beginn (inklusive) und Ende (exklusive) eines UTC-Tages als Unixzeit."""
    beginn = datetime.combine(tag, time(0, 0), tzinfo=timezone.utc)
    return int(beginn.timestamp()), int((beginn + timedelta(days=1)).timestamp())


def saat(tag: date) -> int:
    """Zufallssaat der Stichprobe eines Tages, aus dem Datum abgeleitet.

    Niemand waehlt sie, und jeder kann sie nachrechnen.
    """
    digest = hashlib.sha256(f"qt-meme-papiertest|{tag.isoformat()}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def faellig(tag: date, jetzt: datetime) -> bool:
    """Darf dieser Tag schon gesammelt werden?"""
    _, ende = tagesgrenzen(tag)
    grenze = datetime.fromtimestamp(ende, tz=timezone.utc) + SAMMELN_AB_NACH_TAGESENDE
    return jetzt >= grenze
