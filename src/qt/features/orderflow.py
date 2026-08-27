"""Order Flow, verdichtet auf Bars.

Einzeltrades sind sub-bar; der `FeatureStore` dieses Projekts ist bar-basiert
und die ganze Point-in-Time-Garantie haengt daran (ADR-001). Rohe Ticks in die
Engine zu geben hiesse, genau diese Zusage aufzugeben -- und zwar an der
Stelle, an der sie am meisten wert ist.

Deshalb wird hier verdichtet statt erweitert: aus den Trades eines Bars werden
vier Zahlen, die mit demselben `close_ts` versehen sind wie der Bar selbst und
damit derselben Sichtbarkeitsregel folgen wie jede andere Kennzahl. Die Engine
bleibt unveraendert, das Kostenmodell bleibt gueltig, die Handelsfrequenz
bleibt bei 4h.

Die vier Kennzahlen je Bar:

    delta        gekaufte minus verkaufte Menge (Aggressor-Seite)
    buy_share    Anteil der Kaufmenge am Gesamtvolumen, in [0, 1]
    n_trades     Anzahl der Trades
    avg_size     durchschnittliche Trade-Groesse

`delta` ist die eigentliche neue Information: Volumen **mit Richtung**. Das
steht in OHLCV schlicht nicht drin -- ein Bar mit hohem Volumen und
unveraendertem Schluss kann von einem Kaufueberhang stammen, der auf Widerstand
lief, oder von einem ausgeglichenen Umsatz. Fuer die Kursreihe sieht beides
identisch aus.

`avg_size` trennt "viele Kleine" von "wenigen Grossen". Ob das etwas taugt, ist
eine offene Frage -- die Vorstellung, grosse Trades seien besser informiert,
ist eine Erzaehlung und keine Messung, und moderne Ausfuehrung zerlegt grosse
Orders ohnehin.

**Die Kante des Bars gehoert dem Bar, der sie schliesst.** Ein Trade exakt auf
`close_ts` zaehlt zum laufenden Bar, nicht zum naechsten. Andersherum waere ein
Ein-Millisekunden-Blick in die Zukunft -- klein, aber genau die Sorte Fehler,
die ein Backtest belohnt und die live nicht existiert.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qt.core.types import timeframe_seconds

COLUMNS = ["ts", "delta", "buy_share", "n_trades", "avg_size", "volume"]


def aggregate(trades: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Trades zu Bar-Kennzahlen verdichten.

    `ts` ist die **Open-Zeit** des Bars, genau wie in `qt.data.store` -- damit
    laesst sich das Ergebnis ohne Umrechnung an die Bars anfuegen. Bars ohne
    Trades tauchen nicht auf; sie entstehen erst beim Zusammenfuehren und
    bekommen dort ausdruecklich `nan` statt 0.

    Der Unterschied ist wesentlich: 0 hiesse "ausgeglichener Fluss", `nan`
    heisst "keine Information". Eine Strategie, die beides verwechselt, handelt
    Datenluecken als Signal.
    """
    if trades.empty:
        return pd.DataFrame(columns=COLUMNS)

    df = trades.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    seconds = timeframe_seconds(timeframe)
    df["bucket"] = df["ts"].dt.floor(f"{seconds}s")

    signed = np.where(df["side"].to_numpy() == "buy", 1.0, -1.0)
    df["signed_amount"] = df["amount"].to_numpy() * signed
    df["buy_amount"] = np.where(signed > 0, df["amount"].to_numpy(), 0.0)

    grouped = df.groupby("bucket", sort=True)
    out = pd.DataFrame(
        {
            "delta": grouped["signed_amount"].sum(),
            "volume": grouped["amount"].sum(),
            "buy_amount": grouped["buy_amount"].sum(),
            "n_trades": grouped["amount"].size(),
        }
    )
    out["buy_share"] = np.where(
        out["volume"].to_numpy() > 0,
        out["buy_amount"].to_numpy() / out["volume"].to_numpy(),
        np.nan,
    )
    out["avg_size"] = np.where(
        out["n_trades"].to_numpy() > 0,
        out["volume"].to_numpy() / out["n_trades"].to_numpy(),
        np.nan,
    )
    out = out.drop(columns=["buy_amount"]).reset_index(names="ts")
    return out[COLUMNS]


def attach(bars: pd.DataFrame, flow: pd.DataFrame) -> pd.DataFrame:
    """Flow-Kennzahlen an eine Bar-Tabelle anfuegen.

    Bars ohne Flow-Daten bekommen `nan`, **nicht** 0 -- siehe `aggregate`.
    Die Volumenspalte des Flows wird umbenannt statt die des Bars zu
    ueberschreiben: die beiden stammen von verschiedenen Boersen und muessen
    unterscheidbar bleiben, sonst wird aus zwei Messungen unbemerkt eine.
    """
    merged = bars.merge(
        flow.rename(columns={"volume": "flow_volume"}), on="ts", how="left"
    )
    return merged


def cvd(delta: np.ndarray) -> np.ndarray:
    """Kumulative Volumendifferenz.

    `nan` wird als 0 fortgeschrieben statt die ganze Reihe zu vergiften: eine
    Bar ohne Flow-Daten soll die Summe nicht loeschen, sondern nur nicht
    weiterbewegen. Dass die Information fehlte, steht in `n_trades`.
    """
    clean = np.nan_to_num(np.asarray(delta, dtype=float), nan=0.0)
    return np.cumsum(clean)


def flow_zscore(delta: np.ndarray, n: int) -> float:
    """z-Score der letzten Volumendifferenz gegen die letzten n.

    Skalar fuer den letzten Bar, wie alles in `qt.features.ta` -- damit kann
    man gar nicht erst versehentlich eine Zukunftsreihe mitnehmen.

    Roh ist `delta` nicht vergleichbar: eine Differenz von 50 BTC bedeutet in
    einer ruhigen Nacht etwas anderes als waehrend eines Ausverkaufs. Der
    z-Score macht daraus eine Zahl, die ueber Regime hinweg denselben Sinn hat.
    """
    values = np.asarray(delta, dtype=float)
    if len(values) < n + 1:
        return float("nan")
    fenster = values[-n - 1 : -1]
    fenster = fenster[np.isfinite(fenster)]
    if len(fenster) < max(3, n // 2):
        return float("nan")
    sd = fenster.std(ddof=1)
    if not np.isfinite(sd) or sd == 0:
        return float("nan")
    last = values[-1]
    if not np.isfinite(last):
        return float("nan")
    return float((last - fenster.mean()) / sd)


def divergence(closes: np.ndarray, cumulative: np.ndarray, n: int) -> float:
    """Laeuft der Preis dem Fluss davon?

    Gibt die Differenz der beiden auf [-1, 1] normierten Positionen im
    Fenster zurueck: positiv heisst, der Preis steht relativ hoeher als der
    kumulierte Fluss, negativ das Gegenteil.

    Die uebliche Erzaehlung dazu -- "Divergenz kuendigt eine Umkehr an" -- ist
    genau das, eine Erzaehlung. Die Funktion misst nur, ob die beiden Reihen
    auseinanderlaufen; ob das etwas vorhersagt, entscheidet der Walk-Forward.
    """
    if len(closes) < n or len(cumulative) < n:
        return float("nan")
    preis = _position(np.asarray(closes[-n:], dtype=float))
    fluss = _position(np.asarray(cumulative[-n:], dtype=float))
    if not np.isfinite(preis) or not np.isfinite(fluss):
        return float("nan")
    return float(preis - fluss)


def _position(values: np.ndarray) -> float:
    """Wo steht der letzte Wert im Fenster? 1 = Hoch, -1 = Tief."""
    lo, hi = float(values.min()), float(values.max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        return float("nan")
    return 2.0 * (float(values[-1]) - lo) / (hi - lo) - 1.0
