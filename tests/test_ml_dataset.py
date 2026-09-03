"""Tests der ML-Schicht.

Der wichtigste Test ist `test_zukuenftige_bars_aendern_die_merkmale_nicht`.
`qt.ml.dataset` verzichtet bewusst auf den `FeatureStore` und rechnet
vektorisiert -- damit kommt die Point-in-Time-Zusage nicht mehr aus geerbter
Maschinerie, sondern **nur noch aus diesem Test**. Wer ihn entfernt, hat die
Zusage verloren, ohne dass irgendetwas rot wird.

Die uebrigen Tests halten die Eigenschaften fest, an denen ML auf Marktdaten
ueblicherweise scheitert: Ueberlappung als Unabhaengigkeit auszugeben,
Labels unter den Handelskosten, und Trainingsdaten, die ins Testfenster
hineinragen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qt.ml.dataset import FEATURES, features_matrix, primary_side
from qt.ml.labeling import (
    LabeledEvent,
    atr_series,
    average_uniqueness,
    cusum_events,
    triple_barrier,
)
from qt.ml.model import purged_folds


def _reihe(n: int = 400, seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, n)))
    spanne = close * 0.02
    return close + spanne, close - spanne, close


# ---------------------------------------------------------------------------
# Point in Time -- der Kern
# ---------------------------------------------------------------------------


def test_zukuenftige_bars_aendern_die_merkmale_nicht():
    """Merkmale an Position i duerfen nur Bars bis i benutzen.

    Aufbau wie der bestehende Lookahead-Test (ADR-001): die zweite Haelfte
    der Reihe wird veraendert, die erste Haelfte der Merkmalsmatrix muss
    bitidentisch bleiben.
    """
    h, tief, c = _reihe(400)
    grenze = 200

    basis = features_matrix(h, tief, c)[:grenze]

    h2, tief2, c2 = h.copy(), tief.copy(), c.copy()
    c2[grenze:] *= 3.0
    h2[grenze:] *= 3.0
    tief2[grenze:] *= 3.0
    veraendert = features_matrix(h2, tief2, c2)[:grenze]

    gleich = np.isclose(basis, veraendert, equal_nan=True)
    assert gleich.all(), (
        "Merkmale haben sich durch spaetere Bars veraendert -- "
        f"betroffen: {[FEATURES[j] for j in np.unique(np.where(~gleich)[1])]}"
    )


def test_die_primaerseite_schaut_auch_nicht_nach_vorn():
    h, tief, c = _reihe(400)
    grenze = 200
    basis = primary_side(c)[:grenze]
    c2 = c.copy()
    c2[grenze:] *= 3.0
    assert np.array_equal(basis, primary_side(c2)[:grenze])


def test_der_test_wuerde_einen_echten_lookahead_fangen():
    """Gegenprobe: ein zentriertes Mittel muss auffallen.

    Ohne diese Gegenprobe koennte der Test oben gruen sein, weil die Reihe
    zufaellig unempfindlich ist -- statt weil die Merkmale sauber sind.
    """
    h, tief, c = _reihe(400)
    grenze = 200

    def zentriert(x: np.ndarray, n: int = 21) -> np.ndarray:
        return np.convolve(x, np.ones(n) / n, mode="same")

    basis = zentriert(c)[:grenze]
    c2 = c.copy()
    c2[grenze:] *= 3.0
    assert not np.allclose(basis, zentriert(c2)[:grenze]), (
        "Die Gegenprobe schlaegt nicht an -- dann prueft der Test oben nichts."
    )


# ---------------------------------------------------------------------------
# Labeling
# ---------------------------------------------------------------------------


def test_ein_ziel_unter_den_kosten_wird_nicht_gelabelt():
    """Sonst trainiert man das Modell darauf, Geld zu verlieren."""
    n = 200
    c = np.full(n, 100.0)
    c[1::2] = 100.05  # winzige Bewegung -> ATR weit unter den Kosten
    h, tief = c * 1.0001, c * 0.9999
    ereignisse = np.arange(60, 150, 5)
    labels = triple_barrier(
        "X", h, tief, c, ereignisse, np.ones(len(ereignisse), dtype=int),
        atr_series(h, tief, c), pt_mult=2.0, sl_mult=1.0, max_bars=20, cost=0.009,
    )
    assert labels == [], "Ziele unter den Kosten muessen uebersprungen werden"


def test_bei_gleichzeitigem_treffer_gewinnt_der_stop():
    """Konservativ, weil Backtests die andere Wahl belohnen."""
    c = np.full(60, 100.0)
    h = c.copy()
    tief = c.copy()
    h[31] = 130.0  # Ziel im selben Bar ...
    tief[31] = 70.0  # ... wie der Stop
    atr = np.full(60, 0.05)
    labels = triple_barrier(
        "X", h, tief, c, np.array([30]), np.array([1]), atr,
        pt_mult=2.0, sl_mult=1.0, max_bars=20, cost=0.0,
    )
    assert labels[0].barrier == "stop"
    assert labels[0].label == 0


def test_uniqueness_faellt_mit_der_ueberlappung():
    """Zwei deckungsgleiche Labels sind eine Beobachtung, nicht zwei."""
    getrennt = [
        LabeledEvent("X", 0, 5, 1, 1, 0.1, "ziel"),
        LabeledEvent("X", 10, 15, 1, 1, 0.1, "ziel"),
    ]
    deckungsgleich = [
        LabeledEvent("X", 0, 5, 1, 1, 0.1, "ziel"),
        LabeledEvent("X", 0, 5, 1, 1, 0.1, "ziel"),
    ]
    assert average_uniqueness(getrennt, 20).sum() == pytest.approx(2.0)
    assert average_uniqueness(deckungsgleich, 20).sum() == pytest.approx(1.0)


def test_cusum_zieht_weniger_ereignisse_als_bars():
    _, _, c = _reihe(1000)
    ereignisse = cusum_events(c, threshold=1.0)
    assert 0 < len(ereignisse) < len(c) / 2, (
        "Ein Filter, der fast jeden Bar nimmt, filtert nichts -- "
        "dann ist die Stichprobe aufgeblaeht statt gross."
    )
    assert np.all(np.diff(ereignisse) > 0)


# ---------------------------------------------------------------------------
# Validierung
# ---------------------------------------------------------------------------


def _panel(n: int = 600) -> pd.DataFrame:
    tage = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "t0": tage,
            "t1": tage + pd.Timedelta(days=20),
            "label": np.tile([0, 1], n // 2),
            "ret": np.zeros(n),
            "weight": np.ones(n),
            "symbol": "X",
            **{f: np.zeros(n) for f in FEATURES},
        }
    )


def test_kein_trainingslabel_ragt_ins_testfenster():
    """Das ist das Purging. Ohne es sieht das Modell die Antwort.

    Geprueft wird die Eigenschaft selbst, nicht die Implementierung: fuer
    jeden Fold muss jedes Trainings-`t1` echt vor jedem Test-`t0` liegen.
    """
    panel = _panel()
    t0 = pd.to_datetime(panel["t0"]).to_numpy()
    t1 = pd.to_datetime(panel["t1"]).to_numpy()

    folds = purged_folds(panel, n_folds=4, embargo_days=20)
    assert folds, "keine Folds gebildet"

    for train, test in folds:
        assert t1[train].max() < t0[test].min(), (
            "Ein Trainings-Label endet nach dem Beginn des Testfensters."
        )


def test_das_embargo_haelt_zusaetzlichen_abstand():
    panel = _panel()
    t0 = pd.to_datetime(panel["t0"]).to_numpy()
    t1 = pd.to_datetime(panel["t1"]).to_numpy()
    ohne = purged_folds(panel, n_folds=4, embargo_days=0)
    mit = purged_folds(panel, n_folds=4, embargo_days=60)
    assert sum(len(tr) for tr, _ in mit) < sum(len(tr) for tr, _ in ohne)
    for train, test in mit:
        abstand = t0[test].min() - t1[train].max()
        assert abstand >= np.timedelta64(60, "D")


def test_training_liegt_immer_vor_dem_test():
    """Vorwaerts, nie rueckwaerts -- sonst beantwortet man eine andere Frage."""
    panel = _panel()
    t0 = pd.to_datetime(panel["t0"]).to_numpy()
    for train, test in purged_folds(panel, n_folds=4):
        assert t0[train].max() < t0[test].min()
