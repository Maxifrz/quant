"""Die Geld-Brief-Spanne aus Hoch, Tief und Schluss schaetzen -- Versuch, gescheitert.

**Das Ergebnis zuerst: es funktioniert auf diesen Daten nicht.** Das Modul
bleibt trotzdem im Repo, so wie `timesfm` und `elliott` (ADR-022, ADR-033):
offen als untauglich markiert, mit der Gegenprobe, die es zeigt. Wer den
Versuch nicht dokumentiert, macht ihn ein zweites Mal.

Das Kostenmodell hat zwei Bestandteile, und sie sind sehr unterschiedlich gut
belegt. Die Gebuehr steht im Gebuehrenplan der Boerse -- nachlesbar, mit Datum
(ADR-056). Der halbe Spread stand bisher als blanke Zahl im Code:
`half_spread_bps=2.0` fuer Krypto, `1.0` fuer US-ETFs. Beides war geraten.

Geraten ist hier besonders unangenehm, weil ADR-009 gemessen hat, dass das
Kostenniveau mehr leistet als jede Strategieentscheidung des Projekts. Eine
Annahme mit dieser Hebelwirkung sollte nicht aus dem Bauch kommen, wenn sie
sich messen laesst.

Sie laesst sich messen. Der Store haelt Tages-OHLC fuer 28 Maerkte, und dafuer
gibt es zwei veroeffentlichte Schaetzer:

* **Corwin/Schultz (2012)**, *Journal of Finance* 67(2). Idee: die Spanne
  Hoch-zu-Tief eines Tages enthaelt Volatilitaet **und** Spread, die Spanne
  ueber zwei Tage enthaelt doppelte Volatilitaet und **denselben** Spread.
  Volatilitaet skaliert mit der Zeit, der Spread nicht -- damit lassen sich
  beide trennen.
* **Abdi/Ranaldo (2017)**, *Review of Financial Studies* 30(12). Nutzt
  zusaetzlich den Schlusskurs: der Mittelwert aus Hoch und Tief schaetzt den
  wahren Mittelkurs, der Schluss weicht davon um den halben Spread ab.

Beide sind hier implementiert, und das ist Absicht. Ein einzelner Schaetzer
liefert eine Zahl, die man glauben muss; zwei unabhaengige liefern eine
Uebereinstimmung oder einen Widerspruch, und beides ist Information.

**Was die Schaetzer nicht koennen**, weil es sonst niemand dazusagt:

* Sie sehen den Spread des **ganzen Tages**, nicht den zum Ausfuehrungs-
  zeitpunkt. Wer zur Eroeffnung handelt, zahlt mehr als der Tagesschnitt.
* Kurssprungen ueber Nacht schreiben sie dem Spread zu. Corwin/Schultz nennen
  das selbst als Hauptfehlerquelle und schlagen eine Korrektur vor; die ist
  hier bewusst weggelassen, weil sie den Schaetzer nach unten zieht und eine
  zu niedrige Kostenschaetzung der teurere Fehler waere.
* Negative Schaetzwerte kommen vor -- die Formeln sind nicht auf positive
  Werte beschraenkt. Ueblich und hier so gemacht: auf 0 setzen. Das verzerrt
  den Mittelwert nach oben, weshalb der **Median** berichtet wird.

**Erst mitteln, dann aufloesen -- und das ist keine Feinheit.** Beide Formeln
sind nichtlinear und stehen in den Papieren unter einem Erwartungswert. Wer
sie je Tagespaar auswertet und die Ergebnisse hinterher mittelt, misst
ueberwiegend Rauschen: der erste Anlauf dieses Moduls tat genau das und meldete
**47 Basispunkte fuer eine Reihe mit einem Spread von exakt null**. Rund 42 %
der Paarschaetzungen waren negativ, auf 0 geklemmt, und der Median der
verbleibenden lag hoch. Aggregiert man dieselben Daten zuerst, kommt −16 bps
heraus, also null im Rahmen des Rauschens.

Aufgefallen ist das nur an der Gegenprobe unten -- an echten Kursen haette die
Zahl plausibel ausgesehen und waere in ein ADR gewandert. Deshalb steht die
Gegenprobe als Test im Repo und nicht als einmaliges Skript.

**Was dabei herauskam: keiner der beiden Schaetzer trifft, aber sie klammern.**
Gegen simulierte Reihen mit bekanntem Spread (`tests/test_spread.py`, 12
Ziehungen ueber je 1900 Tage) verhalten sie sich systematisch:

| Tagesvol | wahrer Spread | Corwin/Schultz | Abdi/Ranaldo |
|---|---|---|---|
| 1 % | 0 bps | 0,0 | 13,7 |
| 1 % | 15 bps | 7,1 | 17,4 |
| 2 % | 0 bps | 0,0 | 27,5 |
| 2 % | 40 bps | 23,8 | 41,5 |
| 4 % | 0 bps | 0,0 | 54,9 |
| 4 % | 40 bps | 9,5 | 60,0 |

Corwin/Schultz liegt durchgehend **zu tief** und klemmt bei 0. Abdi/Ranaldo
liegt durchgehend **zu hoch**, und zwar um rund 0,14 mal die Tagesvolatilitaet
-- bei 0 % Spread und 4 % Tagesvol meldet er 55 bps.

In der Simulation klammern die beiden Zahlen den wahren Wert also ein: einer
zu tief, einer zu hoch. Auf dieser Grundlage sah das nach einem brauchbaren
Instrument aus.

**Auf echten Kursen faellt es auseinander.** Zwei unabhaengige Befunde:

1. **Die Klammer dreht sich um.** Ueber die 28 Reihen im Store liegt
   Corwin/Schultz in 10 Faellen *ueber* Abdi/Ranaldo -- bei ADA-USD 31,2 gegen
   0,0. In der Simulation kommt das nie vor. Was auch immer die beiden hier
   messen, es ist nicht dieselbe Groesse.
2. **Der Fall mit bekannter Antwort geht daneben.** BTC/USD auf Coinbase ist
   eines der liquidesten Paare ueberhaupt und handelt mit einer Spanne im
   Bereich eines Basispunkts. Die Schaetzer melden 23,3 und 45,9 bps. Das ist
   nicht knapp daneben, das ist um den Faktor 20 daneben.

Die Ursache ist bekannt und in beiden Papieren benannt: die Identifikation
haengt daran, dass die Volatilitaet innerhalb des Zweitagesfensters konstant
ist. Echte Kurse haben Volatilitaetsclusterung und Spruenge; was dabei an
Streuung entsteht, schreiben beide Formeln dem Spread zu. Die Simulation oben
hat weder Clusterung noch Spruenge und ist deshalb zu freundlich -- sie
kalibriert eine Verzerrung, die auf echte Daten nicht uebertraegt.

**Konsequenz fuer das Kostenmodell:** `half_spread_bps` bleibt eine Annahme.
Sie ist jetzt als Annahme gekennzeichnet statt als Zahl, die nach Messung
aussieht (ADR-056). Aus Tages-OHLC ist sie nicht zu holen; dafuer braeuchte es
Orderbuch- oder Tickdaten, und die hat dieses Projekt nicht.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# (3 - 2*sqrt(2)) taucht in beiden Corwin/Schultz-Termen auf.
_CS_NENNER = 3.0 - 2.0 * np.sqrt(2.0)

# Blockgroesse fuer die Mittelung. 21 ist der uebliche Handelsmonat und die
# Groesse, mit der Corwin/Schultz selbst rechnen. Kleiner rauscht mehr,
# groesser verwischt Aenderungen der Liquiditaet ueber die Jahre.
MONAT = 21


@dataclass(frozen=True, slots=True)
class SpreadSchaetzung:
    """Zwei Schaetzer fuer dieselbe Groesse, plus wie oft sie klemmten.

    `corwin_schultz` und `abdi_ranaldo` sind **volle** Spreads in
    Basispunkten. Das Kostenmodell rechnet mit dem halben Spread -- die
    Umrechnung steht in `half_spread_bps`, damit sie nicht an jeder
    Aufrufstelle neu passiert und dort einmal vergessen wird.

    `anteil_geklemmt` ist die Diagnose: der Anteil der Bloecke, deren
    Schaetzwert negativ oder undefiniert war und auf 0 gesetzt wurde. Je
    hoeher, desto weniger weiss der Schaetzer. Ueber etwa 0,3 ist die Zahl
    nicht mehr ernst zu nehmen.
    """

    symbol: str
    n_bloecke: int
    corwin_schultz: float
    abdi_ranaldo: float
    anteil_geklemmt_cs: float
    anteil_geklemmt_ar: float

    @property
    def klammer_bps(self) -> tuple[float, float]:
        """Untere und obere Grenze des vollen Spreads.

        Bewusst ein Paar und keine Einzelzahl: die beiden Schaetzer sind in
        bekannter Richtung verzerrt (Modul-Doku), ihr Mittelwert waere eine
        erfundene Praezision.
        """
        return (self.corwin_schultz, self.abdi_ranaldo)

    @property
    def breite_bps(self) -> float:
        """Wie weit die Klammer ist -- das Mass fuer ihren Aussagewert."""
        return self.abdi_ranaldo - self.corwin_schultz


def _log(reihe: pd.Series) -> np.ndarray:
    return np.log(reihe.to_numpy(dtype=float))


def _fenster_mittel(werte: np.ndarray, fenster: int) -> np.ndarray:
    """Mittelwert je Block von `fenster` Werten; der Rest faellt weg.

    Ein angebrochener letzter Block wuerde auf weniger Beobachtungen mitteln
    und damit staerker rauschen als die uebrigen -- in einem Median unter
    Bloecken zaehlt er trotzdem voll mit. Lieber weglassen.
    """
    n = (len(werte) // fenster) * fenster
    if n == 0:
        return np.empty(0)
    return werte[:n].reshape(-1, fenster).mean(axis=1)


def corwin_schultz(
    high: pd.Series, low: pd.Series, fenster: int = MONAT
) -> np.ndarray:
    """Voller Spread je Block, als Anteil des Preises.

    Corwin/Schultz (2012), Gleichungen (14) bis (18). `beta` und `gamma`
    werden **je Block gemittelt**, erst danach folgt `alpha` -- die Reihenfolge
    ist der Unterschied zwischen einer Messung und Rauschen (Modul-Doku).

    Negative Werte bleiben stehen; das Klemmen macht `schaetze`, damit der
    Anteil vorher zaehlbar ist.
    """
    h, lo = _log(high), _log(low)
    if len(h) < 2:
        return np.empty(0)

    # beta: quadrierte Tagesspannen zweier aufeinanderfolgender Tage.
    # Volatilitaet skaliert mit der Zeit, der Spread nicht -- daher trennbar.
    tages = (h - lo) ** 2
    beta = _fenster_mittel(tages[:-1] + tages[1:], fenster)

    # gamma: quadrierte Spanne ueber beide Tage zusammen.
    h2 = np.maximum(h[:-1], h[1:])
    lo2 = np.minimum(lo[:-1], lo[1:])
    gamma = _fenster_mittel((h2 - lo2) ** 2, fenster)
    if beta.size == 0:
        return np.empty(0)

    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / _CS_NENNER - np.sqrt(
        gamma / _CS_NENNER
    )
    return 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))


def abdi_ranaldo(
    high: pd.Series, low: pd.Series, close: pd.Series, fenster: int = MONAT
) -> np.ndarray:
    """Voller Spread je Block nach Abdi/Ranaldo (2017), Gleichung (10).

    `eta` ist der Mittelwert aus log-Hoch und log-Tief und schaetzt den wahren
    Mittelkurs; der Schluss weicht davon um den halben Spread ab. Das Produkt
    zweier solcher Abweichungen hat im Erwartungswert das Quadrat des halben
    Spreads -- **im Erwartungswert**, weshalb auch hier erst gemittelt wird.

    Ein Blockmittel kann negativ werden; die Wurzel ist dann nicht definiert
    und der Block kommt als `nan` zurueck. Das ist Absicht: `schaetze` zaehlt
    diese Faelle, und ein hoher Anteil heisst, dass der Schaetzer nichts weiss.
    """
    h, lo, c = _log(high), _log(low), _log(close)
    if len(c) < 3:
        return np.empty(0)

    eta = (h + lo) / 2.0
    produkt = _fenster_mittel((c[1:-1] - eta[1:-1]) * (c[1:-1] - eta[2:]), fenster)
    with np.errstate(invalid="ignore"):
        return 2.0 * np.sqrt(produkt)


def schaetze(symbol: str, df: pd.DataFrame, fenster: int = MONAT) -> SpreadSchaetzung:
    """Beide Schaetzer ueber eine ganze Reihe, als Median ueber Bloecke.

    Median und nicht Mittelwert: ein einzelner Kurssprung reicht, um einen
    Block weit nach oben zu ziehen, und das Klemmen negativer Bloecke bei 0
    verschiebt den Mittelwert zusaetzlich.
    """
    if not {"high", "low", "close"} <= set(df.columns):
        raise ValueError(f"{symbol}: Spalten high/low/close fehlen.")
    if fenster < 2:
        raise ValueError(f"fenster={fenster} ist zu klein; mindestens 2.")

    cs = corwin_schultz(df["high"], df["low"], fenster)
    ar = abdi_ranaldo(df["high"], df["low"], df["close"], fenster)

    def _median_bps(werte: np.ndarray) -> tuple[float, float]:
        if werte.size == 0:
            return float("nan"), float("nan")
        schlecht = ~np.isfinite(werte) | (werte < 0)
        gekappt = np.where(schlecht, 0.0, werte)
        return float(np.median(gekappt) * 10_000.0), float(schlecht.mean())

    cs_bps, cs_neg = _median_bps(cs)
    ar_bps, ar_neg = _median_bps(ar)

    return SpreadSchaetzung(
        symbol=symbol,
        n_bloecke=int(max(cs.size, ar.size)),
        corwin_schultz=cs_bps,
        abdi_ranaldo=ar_bps,
        anteil_geklemmt_cs=cs_neg,
        anteil_geklemmt_ar=ar_neg,
    )


# ---------------------------------------------------------------------------
# Und der Weg, der funktioniert: das Orderbuch fragen (ADR-070)
# ---------------------------------------------------------------------------
#
# Vier Schaetzversuche aus Kursreihen sind gescheitert -- zwei hier aus
# Tages-OHLC (oben), zwei in ADR-067 aus 1,48 Mio. Ticks. Jedesmal derselbe
# Befund: **ohne Quotes geht es nicht.**
#
# Quotes gibt es. Sie sind oeffentlich, brauchen keinen Schluessel, und sie
# stehen im Orderbuch. Was sie nicht liefern, ist Historie: eine
# Momentaufnahme sagt, was der Spread *jetzt* ist, nicht was er 2019 war und
# erst recht nicht, was er im naechsten Absturz sein wird. Das ist weniger,
# als ADR-056 wollte, und mehr als eine unbelegte Zahl.
#
# Gemessen wird der **effektive** halbe Spread bei einer Ordergroesse, nicht
# die Spanne an der Spitze des Buchs. Der Unterschied ist der ganze Punkt:
# an der Spitze steht eine Spanne fuer eine unendlich kleine Order, und die
# hat noch nie jemand gehandelt.

COINBASE_BUCH = "https://api.exchange.coinbase.com/products/{}/book?level=2"


@dataclass(frozen=True, slots=True)
class BuchMessung:
    """Effektiver halber Spread bei einer bestimmten Ordergroesse."""

    symbol: str
    notional: float
    mitte: float
    kauf_bps: float
    verkauf_bps: float

    @property
    def halb_bps(self) -> float:
        """Mittel aus beiden Seiten -- das Gegenstueck zu `half_spread_bps`."""
        return (self.kauf_bps + self.verkauf_bps) / 2


def _vwap(seite: list[tuple[float, float]], notional: float) -> float | None:
    """Durchschnittspreis, bis `notional` gefuellt ist. `None`, wenn das Buch
    nicht reicht -- ein zu duennes Buch ist ein Befund und keine Zahl."""
    rest, kosten, menge = notional, 0.0, 0.0
    for preis, qty in seite:
        nehmen = min(qty, rest / preis)
        if nehmen <= 0:
            break
        kosten += nehmen * preis
        menge += nehmen
        rest -= nehmen * preis
        if rest <= 1e-9:
            break
    if rest > 1e-9 or menge <= 0:
        return None
    return kosten / menge


def miss_am_buch(
    produkt: str, notional: float, timeout: float = 30.0
) -> BuchMessung | None:
    """Eine Momentaufnahme des Coinbase-Buchs auswerten.

    `produkt` in der Schreibweise der Boerse (`BTC-USD`). Rueckgabe `None`,
    wenn das Buch die Ordergroesse nicht hergibt.
    """
    import requests

    antwort = requests.get(COINBASE_BUCH.format(produkt), timeout=timeout)
    antwort.raise_for_status()
    daten = antwort.json()
    bids = [(float(p), float(q)) for p, q, *_ in daten["bids"]]
    asks = [(float(p), float(q)) for p, q, *_ in daten["asks"]]
    if not bids or not asks:
        return None

    mitte = (bids[0][0] + asks[0][0]) / 2
    kauf, verkauf = _vwap(asks, notional), _vwap(bids, notional)
    if kauf is None or verkauf is None:
        return None
    return BuchMessung(
        symbol=produkt,
        notional=notional,
        mitte=mitte,
        kauf_bps=(kauf - mitte) / mitte * 1e4,
        verkauf_bps=(mitte - verkauf) / mitte * 1e4,
    )
