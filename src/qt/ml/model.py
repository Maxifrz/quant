"""Das Modell -- und die Kontrollen, ohne die es nichts bedeutet.

Bewusst **logistische Regression mit Regularisierung und fuenf Merkmalen**.
Kein Gradient Boosting. Bei effektiv 2.506 Beobachtungen ist ein Verfahren mit
hoher Kapazitaet kein besseres Werkzeug, sondern ein schnellerer Weg zum
Overfitting -- und die Erfahrung des Projekts zeigt in dieselbe Richtung:
`elliott` mit sechs Parametern streut um 0,99 Sharpe, `macross` mit zweien um
0,12 (ADR-047).

**Gepurgte Vorwaerts-Validierung.** Standard-k-Fold leckt bei Zeitreihen
katastrophal. Hier wird chronologisch trainiert und getestet, und zwischen
beiden liegt eine Sperrzone: jedes Trainings-Label, dessen Laufzeit (`t0` bis
`t1`) in das Testfenster hineinragt, wird **entfernt**. Ohne dieses Purging
sieht das Modell im Training Ergebnisse, die zum Testzeitraum gehoeren.
Dasselbe Prinzip wie in `qt.backtest.walkforward`, nur auf Labels statt Bars.

Das Purging muss ueber **alle Maerkte** laufen und nicht je Markt: die
Zeitachse ist gemeinsam, und ein BTC-Label, das in den ETH-Testzeitraum
hineinragt, leckt genauso.

**Die drei Kontrollen sind wichtiger als die Kennzahl.** Sie standen im Plan
fest, bevor eine Zahl vorlag:

1. **Messlatte** -- schlaegt das Modell die ungefilterten Einstiege?
2. **Vertauschte Labels** -- dasselbe Verfahren auf permutierten Labels muss
   nichts liefern. Genau diese Kontrolle hat `hashribbon` gekippt (ADR-048).
3. **Markt-Placebo** -- auf einer Gruppe Maerkte trainiert, auf einer anderen
   getestet. Ein Modell, das nur auf seinen eigenen Maerkten wirkt, hat
   Marktnamen gelernt und keine Struktur.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from qt.ml.dataset import FEATURES

__all__ = ["FoldResult", "evaluate", "purged_folds"]


@dataclass(frozen=True, slots=True)
class FoldResult:
    """Ergebnis eines Testfensters."""

    n_train: int
    n_test: int
    n_gehandelt: int
    trefferquote: float
    rendite_mittel: float
    rendite_summe: float
    basis_trefferquote: float
    basis_rendite_mittel: float


def purged_folds(
    panel: pd.DataFrame, n_folds: int = 5, embargo_days: int = 20
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Chronologische Folds mit Purging und Embargo.

    Ein Trainings-Label faellt raus, wenn sein Zeitraum `[t0, t1]` das
    Testfenster beruehrt -- plus `embargo_days` Puffer danach, weil
    Autokorrelation ueber die Fenstergrenze reicht.

    Vorwaerts statt symmetrisch: Training nur aus der Vergangenheit. Ein
    Modell, das mit spaeteren Daten trainiert und auf frueheren getestet
    wird, beantwortet eine Frage, die im Handel nie gestellt wird.
    """
    t0 = pd.to_datetime(panel["t0"]).to_numpy()
    t1 = pd.to_datetime(panel["t1"]).to_numpy()
    ordnung = np.argsort(t0)

    grenzen = np.array_split(ordnung, n_folds + 1)
    embargo = np.timedelta64(embargo_days, "D")
    folds: list[tuple[np.ndarray, np.ndarray]] = []

    for k in range(1, n_folds + 1):
        test = grenzen[k]
        if len(test) == 0:
            continue
        test_start = t0[test].min()
        test_ende = t0[test].max()

        # Kandidaten sind alle frueheren Ereignisse ...
        kandidaten = np.concatenate(grenzen[:k])
        # ... aber nur die, deren Laufzeit vor dem Testfenster endet.
        sauber = t1[kandidaten] < (test_start - embargo)
        train = kandidaten[sauber]

        if len(train) >= 100 and len(test) >= 30:
            folds.append((train, test))
        del test_ende
    return folds


def evaluate(
    panel: pd.DataFrame,
    schwelle: float = 0.5,
    n_folds: int = 5,
    C: float = 0.1,
    seed: int = 0,
    shuffle_labels: bool = False,
) -> list[FoldResult]:
    """Gepurgte Vorwaerts-Validierung des Meta-Modells.

    `shuffle_labels` ist die Negativkontrolle: die Labels werden **innerhalb
    des Trainingsfensters** permutiert, alles andere bleibt gleich. Findet das
    Verfahren dann immer noch etwas, misst es die Konstruktion und nicht die
    Daten.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    X = panel[list(FEATURES)].to_numpy(dtype=float)
    y = panel["label"].to_numpy(dtype=int)
    r = panel["ret"].to_numpy(dtype=float)
    w = panel["weight"].to_numpy(dtype=float)

    rng = np.random.default_rng(seed)
    ergebnisse: list[FoldResult] = []

    for train, test in purged_folds(panel, n_folds=n_folds):
        y_train = y[train]
        if shuffle_labels:
            y_train = rng.permutation(y_train)
        if len(np.unique(y_train)) < 2:
            continue

        skalierer = StandardScaler().fit(X[train])
        modell = LogisticRegression(
            C=C, max_iter=2000, solver="lbfgs", class_weight="balanced"
        )
        # Uniqueness als Stichprobengewicht: ueberlappende Labels sind keine
        # eigenstaendigen Beobachtungen und duerfen nicht wie welche zaehlen.
        modell.fit(skalierer.transform(X[train]), y_train, sample_weight=w[train])

        p = modell.predict_proba(skalierer.transform(X[test]))[:, 1]
        genommen = p >= schwelle

        gehandelt = r[test][genommen]
        ergebnisse.append(
            FoldResult(
                n_train=len(train),
                n_test=len(test),
                n_gehandelt=int(genommen.sum()),
                trefferquote=float((gehandelt > 0).mean()) if len(gehandelt) else np.nan,
                rendite_mittel=float(gehandelt.mean()) if len(gehandelt) else np.nan,
                rendite_summe=float(gehandelt.sum()) if len(gehandelt) else 0.0,
                basis_trefferquote=float((r[test] > 0).mean()),
                basis_rendite_mittel=float(r[test].mean()),
            )
        )
    return ergebnisse


def summary(ergebnisse: list[FoldResult]) -> str:
    """Modell gegen Basis, Fold fuer Fold.

    Die Fold-Spalte ist der Punkt: ein Gesamtvorsprung, der in einem Fenster
    entsteht und in den uebrigen nicht, ist eine Stichprobe und keine Kante --
    dieselbe Regel wie im Walk-Forward (ADR-004).
    """
    if not ergebnisse:
        return "Keine auswertbaren Folds."

    zeilen = [
        f"{'Fold':>4} {'Train':>7} {'Test':>6} {'genommen':>9} "
        f"{'Treffer':>9} {'Basis':>8} {'Rend i.M.':>10} {'Basis':>9}",
        "-" * 70,
    ]
    for i, f in enumerate(ergebnisse):
        zeilen.append(
            f"{i:>4} {f.n_train:>7,} {f.n_test:>6,} "
            f"{f.n_gehandelt:>9,} {f.trefferquote:>8.1%} {f.basis_trefferquote:>7.1%} "
            f"{f.rendite_mittel:>9.2%} {f.basis_rendite_mittel:>8.2%}"
        )

    besser = sum(
        1
        for f in ergebnisse
        if np.isfinite(f.rendite_mittel) and f.rendite_mittel > f.basis_rendite_mittel
    )
    gewichtet = np.nansum([f.rendite_summe for f in ergebnisse])
    basis = sum(f.basis_rendite_mittel * f.n_test for f in ergebnisse)

    zeilen += [
        "",
        f"  Folds, in denen das Modell die Basis schlaegt: {besser} von {len(ergebnisse)}",
        f"  Summe Modell {gewichtet:+.1%}  gegen Basis {basis:+.1%}",
    ]
    return "\n".join(zeilen)
