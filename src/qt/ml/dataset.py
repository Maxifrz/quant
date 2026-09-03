"""Merkmale und Datensatz -- die Seite, die nicht in die Zukunft schauen darf.

Waehrend `qt.ml.labeling` die Antwort berechnet und dafuer nach vorne sehen
**muss**, gilt hier das Gegenteil: jedes Merkmal an Position `i` benutzt
ausschliesslich Bars bis einschliesslich `i`. Die Trennung ist der einzige
Grund, warum ein Ergebnis aus diesem Modul etwas bedeutet.

**Warum nicht ueber `FeatureStore`.** Der Plan sah das vor, und es waere die
sicherste Loesung -- die Point-in-Time-Zusage kaeme dann von derselben
Maschinerie wie im Backtest. Der Preis waere ein Bar-Loop ueber 32.000 Bars in
14 Maerkten fuer jede Merkmalsvariante. Stattdessen wird hier vektorisiert und
**streng nachlaufend** gerechnet, und die Zusage kommt aus einem Test: in
`tests/test_ml_dataset.py` werden zukuenftige Bars veraendert, und die
Merkmalsmatrix muss bitidentisch bleiben. Dieselbe Bauart wie der bestehende
Lookahead-Test (ADR-001), nur auf die Matrix statt auf die Equity-Kurve.

Das ist ein bewusster Tausch: Geschwindigkeit gegen eine geerbte Garantie,
abgesichert durch eine gemessene. Wer den Test entfernt, hat die Zusage
verloren, ohne dass irgendetwas rot wird -- deshalb steht das hier und nicht
nur im Commit.

**Fuenf Merkmale, mehr nicht.** `elliott` mit sechs Parametern streut ueber
BTCs hoehere Timeframes um 0,99 Sharpe, `macross` mit zweien um 0,12
(ADR-047). Diese Erfahrung gilt fuer Merkmale genauso, und bei effektiv 2.506
Beobachtungen ist Sparsamkeit keine Vorsicht, sondern Notwendigkeit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qt.data.store import read_bars
from qt.ml.labeling import (
    LabeledEvent,
    atr_series,
    average_uniqueness,
    cusum_events,
    triple_barrier,
)

__all__ = ["FEATURES", "build_dataset", "features_matrix", "primary_side"]

FEATURES = ("trend", "dist_ma", "vol_rank", "drawdown", "momentum")

FAST, SLOW = 10, 50  # macross-Parameter aus ADR-035, nicht neu gewaehlt


def primary_side(closes: np.ndarray) -> np.ndarray:
    """Die Richtung des Primaermodells: `macross`, long oder flach.

    Bewusst dieselben Parameter wie die getestete Strategie. Sie hier neu zu
    waehlen waere ein weiterer Versuch auf denselben Daten -- und zwar einer,
    den niemand zaehlt.
    """
    seite = np.zeros(len(closes), dtype=int)
    if len(closes) <= SLOW:
        return seite
    ma_f = _trailing_mean(closes, FAST)
    ma_s = _trailing_mean(closes, SLOW)
    gueltig = np.isfinite(ma_f) & np.isfinite(ma_s)
    seite[gueltig] = (ma_f[gueltig] > ma_s[gueltig]).astype(int)
    return seite


def features_matrix(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray
) -> np.ndarray:
    """Merkmalsmatrix, eine Zeile je Bar, streng nachlaufend.

    Alle fuenf Groessen sind **einheitenlos** -- Verhaeltnisse oder in ATR
    gemessen. Ein Merkmal in Dollar waere ueber 14 Maerkte mit Kursen von
    Cents bis Zehntausenden nicht vergleichbar, und das Modell wuerde in
    erster Linie lernen, welcher Markt gerade dran ist.
    """
    atr = atr_series(highs, lows, closes)

    ma_f = _trailing_mean(closes, FAST)
    ma_s = _trailing_mean(closes, SLOW)

    with np.errstate(divide="ignore", invalid="ignore"):
        trend = ma_f / ma_s - 1.0
        dist = (closes / ma_s - 1.0) / atr

        renditen = np.diff(np.log(closes), prepend=np.log(closes[0]))
        vol = _trailing_std(renditen, 20)
        vol_rank = _trailing_rank(vol, 252)

        hoch = _trailing_max(closes, 252)
        drawdown = closes / hoch - 1.0

        vorher = np.concatenate((np.full(20, np.nan), closes[:-20]))
        momentum = (closes / vorher - 1.0) / atr

    return np.column_stack([trend, dist, vol_rank, drawdown, momentum])


def build_dataset(
    symbols: list[str],
    timeframe: str = "1d",
    cusum_threshold: float = 1.0,
    pt_mult: float = 2.0,
    sl_mult: float = 1.0,
    max_bars: int = 20,
    cost: float = 0.009,
) -> pd.DataFrame:
    """Panel ueber mehrere Maerkte: Merkmale, Label, Gewicht, Zeitpunkte.

    `t0` und `t1` bleiben als **Zeitstempel** erhalten, nicht nur als Index:
    die Kreuzvalidierung muss ueber Maerkte hinweg purgen, und dafuer braucht
    sie eine gemeinsame Zeitachse statt 14 getrennter Zaehlungen.

    **Vorbehalt, der beim Lesen der Folds zaehlt:** die Maerkte haben sehr
    verschiedene Spannen. XRP/USD endet im Januar 2021 (694 Bars), AVAX
    beginnt Ende 2021, BTC laeuft durch. Aufgenommen wird ab `SLOW + 260`
    Bars -- die spaeten Folds enthalten deshalb *andere* und teils weniger
    Maerkte als die fruehen. Ein Fold-zu-Fold-Vergleich ist damit kein
    Vergleich gleicher Dinge, und ein Leistungsabfall ueber die Zeit kann
    schlicht heissen, dass die Zusammensetzung sich geaendert hat (ADR-053).
    """
    zeilen: list[pd.DataFrame] = []

    for symbol in symbols:
        df = read_bars(symbol, timeframe).sort_values("ts").reset_index(drop=True)
        c = df.close.to_numpy()
        h = df.high.to_numpy()
        low = df.low.to_numpy()
        if len(c) < SLOW + 260:
            continue

        seite = primary_side(c)
        ereignisse = cusum_events(c, cusum_threshold)
        ereignisse = ereignisse[ereignisse >= SLOW]
        seiten = seite[ereignisse]
        ereignisse = ereignisse[seiten != 0]
        seiten = seiten[seiten != 0]
        if len(ereignisse) == 0:
            continue

        labels: list[LabeledEvent] = triple_barrier(
            symbol, h, low, c, ereignisse, seiten,
            atr_series(h, low, c), pt_mult, sl_mult, max_bars, cost,
        )
        if not labels:
            continue

        matrix = features_matrix(h, low, c)
        idx = np.array([e.t0 for e in labels])
        gewichte = average_uniqueness(labels, len(c))

        teil = pd.DataFrame(matrix[idx], columns=list(FEATURES))
        teil["symbol"] = symbol
        teil["t0"] = df.ts.to_numpy()[idx]
        teil["t1"] = df.ts.to_numpy()[[e.t1 for e in labels]]
        teil["label"] = [e.label for e in labels]
        teil["ret"] = [e.ret for e in labels]
        teil["weight"] = gewichte
        zeilen.append(teil)

    if not zeilen:
        return pd.DataFrame(columns=[*FEATURES, "symbol", "t0", "t1", "label", "ret", "weight"])

    panel = pd.concat(zeilen, ignore_index=True)
    # Zeilen mit unvollstaendigen Merkmalen fliegen raus statt imputiert zu
    # werden. Ein imputierter Wert ist eine erfundene Beobachtung, und bei
    # dieser Stichprobengroesse faellt sie ins Gewicht.
    panel = panel.dropna(subset=list(FEATURES)).reset_index(drop=True)
    return panel.sort_values("t0", ignore_index=True)


# ---------------------------------------------------------------------------
# Nachlaufende Bausteine. Jeder gibt an Position i nur Bars bis i frei.
# ---------------------------------------------------------------------------


def _fenster(werte: np.ndarray, n: int):
    return np.lib.stride_tricks.sliding_window_view(werte, n)


def _trailing_mean(werte: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(werte), np.nan)
    if len(werte) >= n:
        out[n - 1 :] = _fenster(werte, n).mean(axis=1)
    return out


def _trailing_std(werte: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(werte), np.nan)
    if len(werte) >= n:
        out[n - 1 :] = _fenster(werte, n).std(axis=1, ddof=1)
    return out


def _trailing_max(werte: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(werte), np.nan)
    if len(werte) >= n:
        out[n - 1 :] = _fenster(werte, n).max(axis=1)
    return out


def _trailing_rank(werte: np.ndarray, n: int) -> np.ndarray:
    """Perzentilrang des aktuellen Werts im nachlaufenden Fenster.

    Rang statt Rohwert, damit dasselbe Merkmal ueber Maerkte mit sehr
    verschiedener Volatilitaet dasselbe bedeutet.
    """
    out = np.full(len(werte), np.nan)
    if len(werte) < n:
        return out
    block = _fenster(werte, n)
    letzte = block[:, -1]
    with np.errstate(invalid="ignore"):
        out[n - 1 :] = (block <= letzte[:, None]).mean(axis=1)
    return out
