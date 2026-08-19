"""Technische Bausteine.

Bewusst reines NumPy auf Arrays statt pandas-Rolling: die Engine ruft diese
Funktionen einmal pro Bar auf einem kurzen Fenster auf, nicht einmal auf der
ganzen Historie. Jede Funktion gibt einen **Skalar fuer den letzten Bar**
zurueck -- damit kann man gar nicht erst versehentlich eine ganze Zukunftsreihe
mitnehmen.
"""

from __future__ import annotations

import numpy as np


def sma(values: np.ndarray, n: int) -> float:
    """Einfacher gleitender Durchschnitt der letzten n Werte."""
    if len(values) < n:
        return float("nan")
    return float(values[-n:].mean())


def stdev(values: np.ndarray, n: int) -> float:
    """Stichproben-Standardabweichung der letzten n Werte."""
    if len(values) < n:
        return float("nan")
    return float(values[-n:].std(ddof=1))


def zscore(values: np.ndarray, n: int) -> float:
    """Abstand des letzten Werts vom Mittel, in Standardabweichungen."""
    if len(values) < n:
        return float("nan")
    sd = stdev(values, n)
    if not np.isfinite(sd) or sd == 0:
        return float("nan")
    return float((values[-1] - sma(values, n)) / sd)


def donchian(highs: np.ndarray, lows: np.ndarray, n: int) -> tuple[float, float]:
    """Hoechstes Hoch und tiefstes Tief der letzten n Bars.

    Wichtig: **ohne** den aktuellen Bar. Ein Breakout ueber ein Hoch, das den
    aktuellen Bar mitzaehlt, kann per Definition nie ausloesen -- ein
    klassischer Off-by-one, der eine Trendstrategie stumm schaltet.
    """
    if len(highs) < n + 1:
        return float("nan"), float("nan")
    return float(highs[-n - 1 : -1].max()), float(lows[-n - 1 : -1].min())


def true_range(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    """True Range je Bar. Erster Bar hat keinen Vorgaenger -> High-Low."""
    if len(closes) < 2:
        return np.abs(highs - lows)
    prev_close = np.concatenate(([closes[0]], closes[:-1]))
    return np.maximum.reduce(
        [highs - lows, np.abs(highs - prev_close), np.abs(lows - prev_close)]
    )


def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, n: int) -> float:
    """Average True Range ueber die letzten n Bars."""
    if len(closes) < n + 1:
        return float("nan")
    return float(true_range(highs, lows, closes)[-n:].mean())


def realised_vol(closes: np.ndarray, n: int, bars_per_year: float) -> float:
    """Annualisierte realisierte Volatilitaet aus Log-Renditen."""
    if len(closes) < n + 1:
        return float("nan")
    returns = np.diff(np.log(closes[-n - 1 :]))
    return float(returns.std(ddof=1) * np.sqrt(bars_per_year))
