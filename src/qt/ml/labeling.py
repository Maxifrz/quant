"""Labeling fuer ueberwachtes Lernen auf Bars.

Der Modellwahl geht die Label-Frage voraus, und sie entscheidet mehr. Das
naive Label -- "Vorzeichen der naechsten Bar-Rendite" -- ist fast reines
Rauschen und bildet die Handelsentscheidung nicht ab: eine reale Position hat
ein Ziel, einen Stop und eine Geduldsgrenze, und welches davon zuerst greift,
ist die Frage. Hier steht deshalb das **Triple-Barrier**-Verfahren.

Drei Regeln, die dieses Modul von einer Lehrbuchfassung unterscheiden, und
alle drei kommen aus Messungen dieses Projekts:

1. **Barrieren in ATR-Vielfachen, nie in Preiskonstanten.** Dieselbe Regel,
   die der Kritiker an LLM-Kandidaten durchsetzt (ADR-030): eine Schwelle in
   Dollar ist an ein Kursniveau gebunden und ausserhalb ihres Zeitraums
   sinnlos. Ueber 14 Maerkte mit Kursen von Cents bis Zehntausenden waere sie
   nicht einmal innerhalb eines Zeitpunkts vergleichbar.

2. **Ein Ziel unter den Handelskosten wird gar nicht erst gelabelt.** Bei 90
   Basispunkten Round-Trip ist ein Ziel von 0,5% vor Kosten tot. Ein Label,
   das solche Ereignisse als "Gewinner" fuehrt, trainiert ein Modell darauf,
   Geld zu verlieren -- und der Backtest merkt es erst am Ende.

3. **Ereignisse statt jeder Bar.** Aufeinanderfolgende Tagesbars sind fast
   identisch; jede als Beispiel zu zaehlen blaeht die Stichprobe auf und man
   haelt die Aufblaehung fuer Evidenz. Der CUSUM-Filter zieht nur dort ein
   Sample, wo sich kumulativ etwas bewegt hat.

**Was Labels duerfen und Merkmale nicht.** Ein Label schaut per Definition in
die Zukunft -- es ist die Antwort. Ein Merkmal darf das nie. Diese Trennung
ist der Grund, warum die Barriere-Rechnung hier vektorisiert und offline
laufen darf, waehrend die Merkmale den Point-in-Time-Pfad ueber `FeatureStore`
nehmen muessen. Wer beides im selben pandas-Aufruf baut, verliert die Grenze
aus dem Blick, und der Backtest wird zur Fiktion.

**Ueberlappung ist der stille Killer.** Zwei Labels, deren Zeitraeume sich
schneiden, sind keine zwei unabhaengigen Beobachtungen. Bei 45 Bars Horizont
ueberlappen sich benachbarte Labels zu ueber 95%. `average_uniqueness`
rechnet das aus; die Summe der Gewichte ist die **effektive**
Stichprobengroesse, und nur die zaehlt.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "LabeledEvent",
    "atr_series",
    "average_uniqueness",
    "cusum_events",
    "triple_barrier",
]


@dataclass(frozen=True, slots=True)
class LabeledEvent:
    """Ein Ereignis mit seinem Ausgang."""

    symbol: str
    t0: int  # Index des Ereignisses
    t1: int  # Index, an dem eine Barriere fiel
    side: int  # Richtung der unterstellten Position (+1 / -1)
    label: int  # 1 = Ziel zuerst, 0 = Stop oder Zeit
    ret: float  # Rendite ueber den Zeitraum, nach Kosten
    barrier: str  # "ziel", "stop" oder "zeit"

    @property
    def bars(self) -> int:
        return self.t1 - self.t0


def atr_series(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, n: int = 14
) -> np.ndarray:
    """Nachlaufende ATR je Bar, als Anteil des Kurses.

    Als **Anteil** und nicht absolut, damit derselbe Multiplikator ueber
    Maerkte mit Kursen von Cents bis Zehntausenden dasselbe bedeutet.

    Nachlaufend heisst: der Wert an Position i benutzt nur Bars bis
    einschliesslich i. Das ist hier zwingend, weil die ATR die
    **Barrierenbreite beim Einstieg** setzt -- sie ist damit Teil der
    Entscheidung und nicht der Antwort.
    """
    vorher = np.concatenate(([closes[0]], closes[:-1]))
    tr = np.maximum.reduce(
        [highs - lows, np.abs(highs - vorher), np.abs(lows - vorher)]
    )
    out = np.full(len(tr), np.nan)
    kumuliert = np.cumsum(tr)
    out[n - 1 :] = (
        np.concatenate(([kumuliert[n - 1]], kumuliert[n:] - kumuliert[:-n])) / n
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        return out / closes


def cusum_events(
    closes: np.ndarray, threshold: float = 1.0, vol_lookback: int = 50
) -> np.ndarray:
    """Symmetrischer CUSUM-Filter -- Ereignisse statt jeder Bar.

    Ein Sample entsteht, wenn die kumulierte Abweichung seit dem letzten
    Ereignis `threshold` mal die nachlaufende Volatilitaet ueberschreitet.
    Danach wird zurueckgesetzt. Damit haengt die Ereignisdichte nicht am
    Kursniveau und nicht am Markt, sondern nur daran, ob etwas passiert ist.

    Die Schwelle in **Volatilitaetseinheiten** statt in Prozent ist der Grund,
    warum derselbe Wert fuer BTC und fuer ALGO funktioniert.
    """
    renditen = np.diff(np.log(closes), prepend=np.log(closes[0]))
    vol = _rolling_std(renditen, vol_lookback)

    events: list[int] = []
    auf = ab = 0.0
    for i in range(vol_lookback, len(closes)):
        if not np.isfinite(vol[i]) or vol[i] <= 0:
            continue
        auf = max(0.0, auf + renditen[i])
        ab = min(0.0, ab + renditen[i])
        grenze = threshold * vol[i]
        if auf > grenze or ab < -grenze:
            auf = ab = 0.0
            events.append(i)
    return np.array(events, dtype=int)


def triple_barrier(
    symbol: str,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    events: np.ndarray,
    sides: np.ndarray,
    atr: np.ndarray,
    pt_mult: float = 2.0,
    sl_mult: float = 1.0,
    max_bars: int = 20,
    cost: float = 0.009,
) -> list[LabeledEvent]:
    """Fuer jedes Ereignis: welche Barriere faellt zuerst?

    `sides` ist die Richtung, die das **Primaermodell** vorgibt (bei
    Meta-Labeling etwa `macross`). Das Label sagt dann nicht "wohin geht der
    Markt", sondern "traegt dieser Einstieg" -- eine erheblich leichtere
    Frage, und die einzige, die bei dieser Stichprobengroesse eine Chance hat.

    `cost` sind die Round-Trip-Kosten. Ein Ziel, das sie nicht deckt, wird
    uebersprungen statt als Gewinner gefuehrt (Regel 2 im Modul-Docstring).

    Geprueft wird konservativ: **innerhalb eines Bars gilt der Stop als zuerst
    getroffen**, wenn beide Barrieren im selben Bar liegen. Ohne diese Regel
    entscheidet ein Muenzwurf zugunsten des Ergebnisses, und Backtests
    belohnen genau das.
    """
    out: list[LabeledEvent] = []
    n = len(closes)

    for t0, side in zip(events, sides, strict=True):
        if side == 0 or t0 + 1 >= n:
            continue
        breite = atr[t0]
        if not np.isfinite(breite) or breite <= 0:
            continue

        ziel_ab = pt_mult * breite
        if ziel_ab <= cost:
            # Ziel unter Kosten -- kein Label, keine Zeile im Datensatz.
            continue
        stop_ab = sl_mult * breite

        einstieg = closes[t0]
        t1 = min(t0 + max_bars, n - 1)
        barriere = "zeit"
        treffer = t1

        for i in range(t0 + 1, t1 + 1):
            hoch = (highs[i] / einstieg - 1.0) * side
            tief = (lows[i] / einstieg - 1.0) * side
            stop_getroffen = tief <= -stop_ab
            ziel_getroffen = hoch >= ziel_ab
            if stop_getroffen:
                barriere, treffer = "stop", i
                break
            if ziel_getroffen:
                barriere, treffer = "ziel", i
                break

        roh = (closes[treffer] / einstieg - 1.0) * side
        if barriere == "ziel":
            roh = ziel_ab
        elif barriere == "stop":
            roh = -stop_ab

        netto = roh - cost
        out.append(
            LabeledEvent(
                symbol=symbol,
                t0=int(t0),
                t1=int(treffer),
                side=int(side),
                label=int(netto > 0),
                ret=float(netto),
                barrier=barriere,
            )
        )
    return out


def average_uniqueness(events: list[LabeledEvent], n_bars: int) -> np.ndarray:
    """Wie einzigartig ist jedes Label -- die eigentliche Stichprobengroesse.

    Fuer jeden Bar wird gezaehlt, wieviele Labels ihn gleichzeitig belegen.
    Die Einzigartigkeit eines Labels ist der Mittelwert von 1/Belegung ueber
    seine Laufzeit. Ein Label, das sich mit keinem anderen ueberschneidet,
    bekommt 1,0; eines, das sich durchgehend mit drei anderen teilt, 0,25.

    **Die Summe dieser Gewichte ist die effektive Stichprobengroesse.** Sie
    ist typischerweise ein Bruchteil der Zeilenzahl, und der Unterschied ist
    der Grund, warum so viele Modelle auf Marktdaten im Backtest funktionieren
    und sonst nirgends: 5.000 Zeilen mit effektiv 200 Beobachtungen sehen in
    jeder Bibliothek wie 5.000 aus.
    """
    if not events:
        return np.array([])

    belegung = np.zeros(n_bars, dtype=float)
    for e in events:
        belegung[e.t0 : e.t1 + 1] += 1.0

    gewichte = np.empty(len(events), dtype=float)
    for i, e in enumerate(events):
        anteil = belegung[e.t0 : e.t1 + 1]
        gewichte[i] = float(np.mean(1.0 / anteil)) if len(anteil) else 0.0
    return gewichte


def _rolling_std(werte: np.ndarray, n: int) -> np.ndarray:
    """Nachlaufende Standardabweichung, `nan` vor dem ersten vollen Fenster."""
    out = np.full(len(werte), np.nan)
    if len(werte) < n:
        return out
    fenster = np.lib.stride_tricks.sliding_window_view(werte, n)
    out[n - 1 :] = fenster.std(axis=1, ddof=1)
    return out
