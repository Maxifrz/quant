"""Was R0 und R1 mit den gesammelten Kurvenstaenden verdient haetten (ADR-081).

Jeder Handel wird exakt auf der Kurve gerechnet (`pumpfun`), dann um das
verschlechtert, was die Rechnung nicht sieht: Latenz jenseits der zwei
Sekunden, Konkurrenz im selben Slot, Sandwich-Bots. Wie stark, sagt das
Szenario; entscheiden darf nur das primaere.

**Dieser Code laeuft auf echten Testtagen erst nach dem 2026-10-25.** Davor
nur auf synthetischen Daten in den Tests -- ein Zwischenblick ist die
haeufigste Art, einen vorab registrierten Test zu verderben, ohne es zu merken.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date

import numpy as np

from qt.meme import registrierung as reg
from qt.meme.pumpfun import Zustand, kaufen, pool_verkauf, verkaufen_mit_uns

LAMPORTS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class Handel:
    tag: date
    regel: str
    szenario: str
    rendite: float
    art: str  # "normal" oder "graduiert"


def signal(satz: dict) -> float:
    """SOL-Zufluss der ersten Minute ohne die Transaktion des Starts, in Lamports."""
    return float(satz["b"]["rsol"] - satz["a"]["rsol"])


def schwelle(saetze_vortag: list[dict]) -> float:
    """95-%-Quantil des Signals am Vortag (numpy, linear interpoliert)."""
    werte = [signal(s) for s in saetze_vortag if s.get("fehler") is None]
    if not werte:
        raise ValueError("Vortag ohne fehlerfreie Datensaetze -- keine Schwelle")
    return float(np.quantile(werte, reg.R1_QUANTIL))


Gebuehr = tuple[int, int, int]


def uebliche_gebuehr(saetze: list[dict]) -> Gebuehr:
    """Haeufigste Anfangsgebuehr des Tages, fuer Starts, an denen niemand
    eine Gebuehr gezahlt hat (kein Erstellerkauf, nur Mayhem-Agent)."""
    zaehler = Counter(
        (s["a"]["fee_bps"], s["a"]["creator_fee_bps"], s["a"].get("zusatz_bps", 0))
        for s in saetze
        if s.get("fehler") is None and s["a"]["fee_bps"] >= 0
    )
    if not zaehler:
        raise ValueError("Kein Datensatz des Tages traegt eine Gebuehr")
    return zaehler.most_common(1)[0][0]


def _zustand(d: dict, ersatz: Gebuehr) -> Zustand:
    z = Zustand.aus_dict(d)
    if z.fee_bps < 0:
        z = z.mit_gebuehr(*ersatz)
    return z


def rsol_bei_abschluss(z: Zustand, k0: dict) -> float:
    """Echtes SOL in der Kurve, wenn sie voll ist.

    Auf der Kurve des Stands `z` gerechnet, nicht auf der vom Start: bei
    Mayhem-Coins hat der Agent die virtuelle SOL-Basis verschoben. Fuer
    normale Coins kommt dasselbe heraus, rund 85 SOL.
    """
    basis = z.vsol - z.rsol
    return z.vsol * z.vtok / (k0["vtok"] - k0["rtok"]) - basis


def handeln(satz: dict, sz: reg.Szenario, gebuehr: Gebuehr) -> tuple[float, str] | None:
    """Rendite eines Handels in SOL, oder `None`, wenn die Kurve beim Einstieg voll war."""
    einstieg = _zustand(satz["c"], gebuehr)
    if einstieg.voll:
        return None
    ausstieg = _zustand(satz["e"], gebuehr)
    k0 = satz["k0"]

    einsatz = reg.EINSATZ_SOL * LAMPORTS
    netz = 2 * reg.NETZWERK_SOL_JE_TX * LAMPORTS
    kauf = kaufen(einstieg, einsatz)
    tokens = kauf.tokens * (1.0 - sz.abschlag)

    # Mit uns in der Kurve waere sie frueher voll gewesen, falls die beim
    # Ausstieg noch uebrigen Tokens nicht fuer unseren Kauf gereicht haetten.
    graduiert = kauf.macht_voll or ausstieg.voll or ausstieg.rtok <= kauf.tokens
    if graduiert:
        pool_tok = k0["supply"] - k0["rtok"]
        kurve = einstieg if kauf.macht_voll else ausstieg
        brutto = pool_verkauf(rsol_bei_abschluss(kurve, k0), pool_tok, tokens)
        erloes = brutto * (1.0 - reg.POOL_GEBUEHR) * (1.0 - sz.graduierung)
        art = "graduiert"
    else:
        brutto = verkaufen_mit_uns(ausstieg, kauf.netto, tokens)
        erloes = brutto * (1.0 - ausstieg.gebuehr) * (1.0 - sz.abschlag)
        art = "normal"
    return (erloes + kauf.rest - netz - einsatz) / einsatz, art


def simulieren(tage: dict[date, list[dict]], testtage: list[date]) -> tuple[list[Handel], dict]:
    """R0 und R1 ueber alle Testtage und Szenarien.

    `tage` enthaelt auch den Kalibriertag: er liefert die R1-Schwelle des
    ersten Testtags und wird selbst nicht gehandelt. Fehlt ein Vortag, gilt
    der juengste vorhandene davor (ADR-081).
    """
    handel: list[Handel] = []
    nicht_handelbar = Counter()
    vorhanden = sorted(tage)
    for tag in testtage:
        if tag not in tage:
            continue
        vortage = [t for t in vorhanden if t < tag and tage[t]]
        if not vortage:
            continue
        grenze = schwelle(tage[vortage[-1]])
        saetze = [s for s in tage[tag] if s.get("fehler") is None]
        gebuehr = uebliche_gebuehr(saetze)
        for satz in saetze:
            regeln = ["R0"] + (["R1"] if signal(satz) > grenze else [])
            for sz in reg.SZENARIEN:
                ergebnis = handeln(satz, sz, gebuehr)
                for regel in regeln:
                    if ergebnis is None:
                        if sz.name == reg.PRIMAER:
                            nicht_handelbar[regel] += 1
                        continue
                    handel.append(Handel(tag, regel, sz.name, ergebnis[0], ergebnis[1]))
    return handel, dict(nicht_handelbar)
