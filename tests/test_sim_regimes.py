"""GARCH- und HMM-Pfadgeneratoren.

Kein Test hier fittet auf sieben Jahren Realdaten -- kurze synthetische
Reihen mit **bekannten** Eigenschaften sind aussagekraeftiger und halten die
Suite schnell. Wenn eine Reihe zwei Vola-Regime hat, muss das HMM zwei
finden; wenn sie eine bekannte Volatilitaet hat, muessen die simulierten
Pfade in derselben Groessenordnung liegen.

Der Schwerpunkt liegt auf drei Dingen, die still schieflaufen koennen:
Skalierung (ein vergessener Faktor 100 verschiebt jede Risikozahl um zwei
Groessenordnungen), Determinismus, und die Zustandssortierung des HMM.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from qt.core.types import bars_per_year
from qt.sim.regimes import (
    GARCHPaths,
    HMMRegimePaths,
    RegimeFitFailed,
)

# Kurz genug fuer eine schnelle Suite, lang genug fuer einen stabilen Fit.
N_HISTORY = 400
HORIZON = 40
N_PATHS = 300


def garch_like(
    n: int = N_HISTORY, seed: int = 0, vol: float = 0.02, df: float | None = None
) -> np.ndarray:
    """Reihe mit echtem Vol-Clustering, aus einem GARCH-artigen Prozess.

    Bewusst nicht `rng.normal(...)`: eine i.i.d.-Reihe hat kein Clustering,
    und ein GARCH-Fit darauf schaetzt alpha/beta nahe null. Tests, die
    Clustering nachweisen sollen, braeuchten dann eine Eigenschaft, die in
    den Trainingsdaten gar nicht steckt.
    """
    rng = np.random.default_rng(seed)
    # Persistenz alpha+beta = 0.80, nicht 0.95. Bei hoher Persistenz und
    # kurzer Historie laeuft die Schaetzung an den Rand des stationaeren
    # Bereichs (alpha+beta = 1), und `regimes.py` weist das zu Recht ab --
    # der Test wuerde dann eine korrekte Sicherung als Fehlschlag melden.
    omega, alpha, beta = vol**2 * 0.20, 0.10, 0.70
    sigma2 = vol**2
    out = np.empty(n)
    eps = 0.0
    for i in range(n):
        sigma2 = omega + alpha * eps**2 + beta * sigma2
        if df is None:
            shock = rng.standard_normal()
        else:
            # Auf Varianz 1 normiert, damit `vol` weiterhin die Groessenordnung
            # setzt und nur die Tail-Dicke sich aendert.
            shock = rng.standard_t(df) / np.sqrt(df / (df - 2.0))
        eps = np.sqrt(sigma2) * shock
        out[i] = eps
    return out


def two_regime_series(n: int = N_HISTORY, seed: int = 0) -> np.ndarray:
    """Abwechselnd ruhige und turbulente Bloecke -- zwei klar getrennte Regime."""
    rng = np.random.default_rng(seed)
    block = n // 8
    parts = []
    for i in range(8):
        scale = 0.004 if i % 2 == 0 else 0.030
        parts.append(rng.normal(0.0, scale, block))
    return np.concatenate(parts)


@pytest.fixture(scope="module")
def garch() -> GARCHPaths:
    return GARCHPaths(min_history=64)


@pytest.fixture(scope="module")
def hmm() -> HMMRegimePaths:
    return HMMRegimePaths(min_history=64)


def abs_autocorr(values: np.ndarray, lag: int = 1) -> float:
    """Autokorrelation der Betraege -- das Mass fuer Vol-Clustering."""
    a = np.abs(values)
    a = a - a.mean()
    denom = float(np.dot(a, a))
    if denom <= 0:
        return 0.0
    return float(np.dot(a[:-lag], a[lag:]) / denom)


# ---------------------------------------------------------------------------
# Skalierung -- der Faktor-100-Test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vol", [0.005, 0.02, 0.05])
def test_garch_simulates_in_the_scale_of_its_input(garch, vol):
    """`arch` rechnet auf Renditen in Prozent, wir auf Dezimalzahlen.

    Ein vergessener Faktor 100 in der Hin- oder Rueckskalierung schlaegt
    nirgends fehl -- er verschiebt nur jede Risikozahl um zwei
    Groessenordnungen. Deshalb wird hier gegen eine Historie mit *bekannter*
    Volatilitaet geprueft, nicht gegen sich selbst.
    """
    history = garch_like(vol=vol, seed=1)
    ensemble = garch.generate(history, horizon=HORIZON, n_paths=N_PATHS, seed=1)

    simulated = ensemble.paths.std()
    assert 0.25 * history.std() < simulated < 4.0 * history.std(), (
        f"Historie std {history.std():.4f}, simuliert {simulated:.4f} -- "
        "das riecht nach einem Skalierungsfehler"
    )


@pytest.mark.parametrize("vol", [0.005, 0.02, 0.05])
def test_hmm_simulates_in_the_scale_of_its_input(hmm, vol):
    rng = np.random.default_rng(2)
    history = rng.normal(0.0, vol, N_HISTORY)
    ensemble = hmm.generate(history, horizon=HORIZON, n_paths=N_PATHS, seed=1)

    simulated = ensemble.paths.std()
    assert 0.25 * history.std() < simulated < 4.0 * history.std()


def test_annualised_vol_lands_in_a_plausible_range(garch):
    """Gegenprobe in der Einheit, in der man das Ergebnis liest.

    Eine Krypto-Vola von 60% p.a. ist plausibel, 0,6% und 6000% sind es
    nicht -- und genau diese beiden waeren die Folge eines Faktor-100-Fehlers.
    """
    history = garch_like(vol=0.02, seed=3)
    ensemble = garch.generate(history, horizon=HORIZON, n_paths=N_PATHS, seed=1)

    annualised = ensemble.paths.std() * np.sqrt(bars_per_year("4h"))
    assert 0.05 < annualised < 5.0, f"{annualised:.1%} p.a. ist nicht plausibel"


# ---------------------------------------------------------------------------
# GARCH: Vol-Clustering und Tails
# ---------------------------------------------------------------------------


def test_garch_produces_volatility_clustering(garch):
    """Das ist der Grund, warum es GARCH neben dem Bootstrap gibt."""
    history = garch_like(seed=4)
    ensemble = garch.generate(history, horizon=250, n_paths=200, seed=1)

    clustering = float(np.mean([abs_autocorr(p) for p in ensemble.paths]))
    assert clustering > 0.05, (
        f"Autokorrelation der Betraege {clustering:.3f} -- ohne Clustering "
        "ist GARCH nur eine umstaendliche Normalverteilung"
    )


def test_t_innovations_have_fatter_tails_than_normal():
    """Normalverteilte Innovationen unterschaetzen Krypto-Tails deutlich.

    Der Default ist deshalb Student-t. Dieser Test haelt fest, dass die
    Wahl ueberhaupt einen Unterschied macht -- sonst waere sie Zierrat.

    Die Historie braucht dafuer **selbst** fette Tails. Auf normalverteilten
    Innovationen schaetzt der t-Fit korrekt ein sehr grosses nu und ist damit
    effektiv normal -- der Test wuerde dann eine richtige Schaetzung als
    Fehlschlag melden.
    """
    history = garch_like(seed=5, df=4.0, n=800)

    def kurtosis(dist: str) -> float:
        gen = GARCHPaths(dist=dist, min_history=64)
        paths = gen.generate(history, horizon=200, n_paths=300, seed=1).paths.ravel()
        centred = paths - paths.mean()
        return float(np.mean(centred**4) / np.mean(centred**2) ** 2)

    assert kurtosis("t") > kurtosis("normal")


def test_garch_starts_from_the_current_state_not_the_long_run_average(garch):
    """Ein Pfad, der mitten in einem Vol-Cluster startet, beginnt turbulent.

    Wer stattdessen mit der langfristigen Varianz startet, beantwortet "wie
    sieht ein durchschnittliches Jahr aus" statt "was passiert als
    Naechstes" -- und genau letzteres ist die Frage.
    """
    base = garch_like(seed=6, vol=0.02)

    # Dieselbe Reihe, nur der Schwanz skaliert. Ein kuenstlich angehaengter
    # konstanter Block waere kein GARCH-Prozess mehr und liesse die
    # Schaetzung an den Rand des stationaeren Bereichs laufen -- geprueft
    # wuerde dann die Sicherung, nicht der Startzustand.
    calm, turbulent = base.copy(), base.copy()
    calm[-40:] *= 0.3
    turbulent[-40:] *= 2.5

    first_bars_calm = garch.generate(calm, horizon=5, n_paths=400, seed=1).paths.std()
    first_bars_wild = garch.generate(turbulent, horizon=5, n_paths=400, seed=1).paths.std()

    assert first_bars_wild > first_bars_calm


# ---------------------------------------------------------------------------
# HMM: Zustandssortierung und Regimeerkennung
# ---------------------------------------------------------------------------


def test_hmm_states_are_sorted_by_variance(hmm):
    """`hmmlearn` nummeriert Zustaende beliebig.

    Ohne feste Ordnung ist jede Aussage der Form "Zustand 0 hat
    Wahrscheinlichkeit 0,8" nicht reproduzierbar interpretierbar -- nicht
    falsch, sondern bedeutungslos.
    """
    fit = hmm.fit(two_regime_series(seed=7))

    assert np.all(np.diff(fit.variances) > 0), (
        f"Varianzen nicht aufsteigend: {fit.variances}"
    )


def test_hmm_state_order_is_stable_across_fits():
    """Zwei Fits auf denselben Daten muessen dieselbe Zuordnung liefern."""
    series = two_regime_series(seed=8)
    a = HMMRegimePaths(min_history=64).fit(series)
    b = HMMRegimePaths(min_history=64).fit(series)

    np.testing.assert_allclose(a.variances, b.variances, rtol=1e-6)
    np.testing.assert_allclose(a.transmat, b.transmat, rtol=1e-6)


def test_hmm_separates_two_regimes_when_they_exist(hmm):
    """Konstruierte Reihe mit Vola 0,4% und 3,0% -- der Fit muss beide finden."""
    fit = hmm.fit(two_regime_series(seed=9))

    ratio = np.sqrt(fit.variances[-1] / fit.variances[0])
    assert ratio > 3.0, (
        f"Vola-Verhaeltnis der Zustaende nur {ratio:.1f}x -- die Regime "
        "wurden nicht getrennt"
    )


def test_hmm_regimes_persist_rather_than_flicker(hmm):
    """Ein Regime, das bei jedem Bar wechselt, ist kein Regime.

    Die Diagonale der Uebergangsmatrix muss deutlich ueber der
    Gleichverteilung liegen, sonst modelliert das HMM Rauschen.
    """
    fit = hmm.fit(two_regime_series(seed=10))

    assert np.all(np.diag(fit.transmat) > 0.7)


# ---------------------------------------------------------------------------
# Determinismus
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("generator", ["garch", "hmm"])
def test_same_seed_gives_identical_paths(generator, garch, hmm):
    """Ein Simulationsergebnis, das sich bei jedem Lauf aendert, ist als
    Entscheidungsgrundlage wertlos."""
    gen = garch if generator == "garch" else hmm
    history = garch_like(seed=11)

    a = gen.generate(history, horizon=HORIZON, n_paths=N_PATHS, seed=42)
    b = gen.generate(history, horizon=HORIZON, n_paths=N_PATHS, seed=42)

    np.testing.assert_array_equal(a.paths, b.paths)


@pytest.mark.parametrize("generator", ["garch", "hmm"])
def test_different_seed_gives_different_paths(generator, garch, hmm):
    gen = garch if generator == "garch" else hmm
    history = garch_like(seed=12)

    a = gen.generate(history, horizon=HORIZON, n_paths=N_PATHS, seed=1)
    b = gen.generate(history, horizon=HORIZON, n_paths=N_PATHS, seed=2)

    assert not np.array_equal(a.paths, b.paths)


@pytest.mark.parametrize("generator", ["garch", "hmm"])
def test_global_random_state_does_not_leak_in(generator, garch, hmm):
    """Kein Rueckgriff auf den globalen numpy-Zustand.

    Sonst haengt das Ergebnis davon ab, was vorher im Prozess passiert ist --
    reproduzierbar nur solange niemand die Reihenfolge aendert.
    """
    gen = garch if generator == "garch" else hmm
    history = garch_like(seed=13)

    np.random.seed(1)
    a = gen.generate(history, horizon=HORIZON, n_paths=N_PATHS, seed=7)
    np.random.seed(999)
    b = gen.generate(history, horizon=HORIZON, n_paths=N_PATHS, seed=7)

    np.testing.assert_array_equal(a.paths, b.paths)


# ---------------------------------------------------------------------------
# Ausfallverhalten
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("generator", ["garch", "hmm"])
def test_constant_series_is_rejected_with_a_speaking_error(generator):
    """Eine konstante Reihe hat keine Varianz -- da ist nichts zu schaetzen.

    Ein Optimierer, der darauf losgelassen wird, konvergiert nicht oder
    liefert Unsinn. Beides gehoert als sprechender Fehler gemeldet, nicht
    als NaN im Ensemble.
    """
    gen = GARCHPaths(min_history=64) if generator == "garch" else HMMRegimePaths(min_history=64)
    constant = np.full(N_HISTORY, 0.001)

    with pytest.raises(RegimeFitFailed):
        gen.generate(constant, horizon=HORIZON, n_paths=10, seed=1)


@pytest.mark.parametrize("generator", ["garch", "hmm"])
def test_too_short_history_is_rejected(generator):
    gen = GARCHPaths() if generator == "garch" else HMMRegimePaths()

    with pytest.raises(ValueError, match="Historie"):
        gen.generate(np.random.default_rng(0).normal(0, 0.02, 50), 10, 10, seed=1)


@pytest.mark.parametrize("generator", ["garch", "hmm"])
def test_gap_in_history_is_reported(generator):
    """Eine Datenluecke ist ein Befund, kein Filterfall -- wie ueberall sonst."""
    gen = GARCHPaths(min_history=64) if generator == "garch" else HMMRegimePaths(min_history=64)
    history = garch_like(seed=14)
    history[10] = np.nan

    with pytest.raises(ValueError, match="Datenluecke"):
        gen.generate(history, horizon=HORIZON, n_paths=10, seed=1)


def test_no_unfiltered_convergence_warnings_reach_the_caller(garch, hmm):
    """Konvergenzwarnungen der Pakete gehoeren uebersetzt oder unterdrueckt.

    Eine Testausgabe voller `ConvergenceWarning` trainiert einem an, Warnungen
    zu ueberlesen -- und dann geht auch die eine unter, die zaehlt.
    """
    history = garch_like(seed=15)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        garch.generate(history, horizon=HORIZON, n_paths=50, seed=1)
        hmm.generate(history, horizon=HORIZON, n_paths=50, seed=1)

    noisy = [w for w in caught if "converg" in str(w.message).lower()]
    assert not noisy, f"Ungefilterte Konvergenzwarnungen: {[str(w.message) for w in noisy]}"


# ---------------------------------------------------------------------------
# Vertrag und Parameter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("generator", ["garch", "hmm"])
def test_ensemble_shape_and_finiteness(generator, garch, hmm):
    gen = garch if generator == "garch" else hmm
    ensemble = gen.generate(garch_like(seed=16), horizon=HORIZON, n_paths=N_PATHS, seed=1)

    assert ensemble.paths.shape == (N_PATHS, HORIZON)
    assert np.all(np.isfinite(ensemble.paths))
    assert ensemble.weights.sum() == pytest.approx(1.0)


def test_invalid_parameters_are_rejected():
    with pytest.raises(ValueError, match="p >= 1"):
        GARCHPaths(p=0)
    with pytest.raises(ValueError, match="Verteilung"):
        GARCHPaths(dist="bauchgefuehl")
    with pytest.raises(ValueError, match="n_states"):
        HMMRegimePaths(n_states=1)
    with pytest.raises(ValueError, match="min_history"):
        GARCHPaths(min_history=8)


def test_fit_is_memoised(garch):
    """Der Fit ist teuer, das Sampling billig.

    Ohne Memoisierung kostet jeder weitere Lauf auf derselben Historie den
    vollen Optimierer -- in einer Schleife ueber Seeds ist das der
    Unterschied zwischen Sekunden und Minuten.
    """
    history = garch_like(seed=17)

    first = garch.fit(history)
    second = garch.fit(history)

    assert first is second
