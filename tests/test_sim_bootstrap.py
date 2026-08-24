"""Bootstrap-Tests.

Der Zweck des stationaeren Bootstraps ist eine einzige Eigenschaft: er
erhaelt die zeitliche Struktur der Historie, waehrend ein naives Ziehen sie
zerstoert. Genau das wird hier gemessen und nicht behauptet -- deshalb steht
in fast jedem inhaltlichen Test der `IIDBootstrap` als Gegenprobe daneben.
Ohne ihn waere "erhaelt Autokorrelation" eine Zahl ohne Massstab.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from qt.sim.base import PathEnsemble
from qt.sim.bootstrap import IIDBootstrap, StationaryBootstrap


# ---------------------------------------------------------------------------
# Hilfsreihen und Kennzahlen
# ---------------------------------------------------------------------------


def ar1_returns(n: int = 1500, phi: float = 0.7, seed: int = 0) -> np.ndarray:
    """AR(1)-Reihe mit sehr starker Autokorrelation.

    Kuenstlich uebertrieben (echte Renditen haben |phi| < 0.1), damit der
    Unterschied zwischen den Verfahren deutlich ueber dem Stichprobenrauschen
    liegt und der Test nicht an Zufall haengt.
    """
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0.0, 0.01, n)
    out = np.empty(n)
    out[0] = shocks[0]
    for i in range(1, n):
        out[i] = phi * out[i - 1] + shocks[i]
    return out


def clustered_returns(n: int = 1500, seed: int = 0) -> np.ndarray:
    """Reihe ohne Autokorrelation im Vorzeichen, aber mit Vol-Clustering.

    Ruhige und turbulente Abschnitte wechseln sich in langen Bloecken ab --
    die Karikatur dessen, was GARCH modelliert. Wichtig fuer den Test: die
    Renditen selbst sind unkorreliert, nur ihre *Betraege* haengen zusammen.
    Ein Verfahren, das nur die Randverteilung trifft, faellt hier durch.
    """
    rng = np.random.default_rng(seed)
    vol = np.where((np.arange(n) // 60) % 2 == 0, 0.004, 0.030)
    return rng.normal(0.0, 1.0, n) * vol


def lag1_autocorr(paths: np.ndarray) -> float:
    """Autokorrelation zum Lag 1, ueber alle Pfade gepoolt.

    Je Pfad zentriert, damit ein Pfad mit zufaellig hohem Mittelwert keine
    Autokorrelation vortaeuscht.
    """
    centred = paths - paths.mean(axis=1, keepdims=True)
    return float((centred[:, 1:] * centred[:, :-1]).mean() / (centred**2).mean())


def worst_window_loss(paths: np.ndarray, window: int) -> np.ndarray:
    """Schlimmster zusammenhaengender Verlust ueber `window` Bars, je Pfad.

    In Log-Rendite, weil sich Teilstrecken darin addieren -- damit wird aus
    dem rollierenden Fenster eine Differenz zweier Kumulierter statt einer
    Schleife.
    """
    cumulative = np.cumsum(np.log1p(paths), axis=1)
    windows = cumulative[:, window:] - cumulative[:, :-window]
    return windows.min(axis=1)


def marked_history(n: int = 120) -> np.ndarray:
    """Historie aus lauter verschiedenen Werten.

    Damit laesst sich jede gezogene Rendite eindeutig ihrer Position in der
    Historie zuordnen -- die Voraussetzung fuer die Tests zum zirkulaeren
    Ziehen.
    """
    return (np.arange(n) + 1) * 1e-4


# ---------------------------------------------------------------------------
# Form, Vertrag, Determinismus
# ---------------------------------------------------------------------------


def test_returns_a_valid_ensemble_of_the_requested_shape():
    ens = StationaryBootstrap().generate(ar1_returns(), horizon=50, n_paths=200, seed=1)

    assert isinstance(ens, PathEnsemble)
    assert ens.paths.shape == (200, 50)
    assert ens.n_paths == 200 and ens.horizon == 50
    assert ens.weights.sum() == pytest.approx(1.0)
    assert np.all(np.isfinite(ens.paths))
    assert ens.meta["generator"] == "stationary_bootstrap"
    assert ens.meta["seed"] == 1


def test_horizon_may_exceed_the_history():
    """Ein Pfad darf laenger sein als die Historie, aus der er gezogen wird.

    Nur wegen des zirkulaeren Ziehens; ohne Wrap waere hier Schluss.
    """
    ens = StationaryBootstrap(mean_block=5).generate(
        marked_history(60), horizon=500, n_paths=20, seed=2
    )
    assert ens.paths.shape == (20, 500)


@pytest.mark.parametrize("cls", [StationaryBootstrap, IIDBootstrap])
def test_same_seed_is_bit_identical(cls):
    """Bitgleich, nicht nur statistisch gleich.

    Ein Ensemble, das sich zwischen zwei Laeufen aendert, macht jede
    Allokationsentscheidung darueber unreproduzierbar -- und einen Backtest
    ueber solche Entscheidungen wertlos.
    """
    hist = ar1_returns()
    a = cls().generate(hist, horizon=64, n_paths=300, seed=99)
    b = cls().generate(hist, horizon=64, n_paths=300, seed=99)
    np.testing.assert_array_equal(a.paths, b.paths)


def test_different_seed_gives_a_different_ensemble():
    hist = ar1_returns()
    a = StationaryBootstrap().generate(hist, horizon=64, n_paths=300, seed=1)
    b = StationaryBootstrap().generate(hist, horizon=64, n_paths=300, seed=2)
    assert not np.array_equal(a.paths, b.paths)


def test_no_dependence_on_global_random_state():
    """Ein Aufruf davor darf das Ergebnis nicht verschieben.

    Faengt die Rueckkehr zu `np.random.seed()` bzw. `np.random.*` ab -- der
    Fehler waere sonst unsichtbar, solange niemand die Aufrufreihenfolge
    aendert.
    """
    hist = ar1_returns()
    first = StationaryBootstrap().generate(hist, horizon=32, n_paths=100, seed=5)
    np.random.seed(12345)
    np.random.random(1000)
    second = StationaryBootstrap().generate(hist, horizon=32, n_paths=100, seed=5)
    np.testing.assert_array_equal(first.paths, second.paths)


# ---------------------------------------------------------------------------
# Der Kern: bleibt die zeitliche Struktur erhalten?
# ---------------------------------------------------------------------------


def test_stationary_bootstrap_preserves_autocorrelation_iid_destroys_it():
    """Der zentrale Test dieser Datei.

    Auf einer AR(1)-Reihe mit phi = 0.7 muss der stationaere Bootstrap den
    Grossteil der Autokorrelation in die simulierten Pfade tragen, waehrend
    der i.i.d.-Bootstrap sie vollstaendig verliert. Ohne diese Eigenschaft
    waere jede Trend- oder Mean-Reversion-Strategie im Ensemble per
    Konstruktion wirkungslos -- und das Ensemble damit als Testumgebung fuer
    genau die Strategien untauglich, die es bewerten soll.
    """
    hist = ar1_returns(phi=0.7)
    hist_ac = float(np.corrcoef(hist[1:], hist[:-1])[0, 1])

    stationary = StationaryBootstrap(mean_block=20).generate(hist, 500, 1000, seed=7)
    iid = IIDBootstrap().generate(hist, 500, 1000, seed=7)

    ac_stationary = lag1_autocorr(stationary.paths)
    ac_iid = lag1_autocorr(iid.paths)

    assert hist_ac > 0.6, "Testreihe ist nicht so autokorreliert wie angenommen"
    assert ac_stationary > 0.5 * hist_ac, (
        f"Stationaerer Bootstrap erhaelt nur {ac_stationary:.3f} von {hist_ac:.3f}"
    )
    assert abs(ac_iid) < 0.05, f"i.i.d.-Bootstrap zeigt Struktur ({ac_iid:.3f})"
    assert ac_stationary > ac_iid + 0.3


def test_stationary_bootstrap_preserves_volatility_clustering():
    """Autokorrelation der *Betraege* -- das ist Vol-Clustering.

    Die eigentliche Risikofrage: kumulieren sich grosse Bewegungen? Ein
    Ensemble, das die grossen Tage gleichmaessig ueber den Horizont verteilt,
    kennt keine Drawdown-Serien und meldet deshalb ein zu freundliches CVaR.
    """
    hist = clustered_returns()
    hist_ac_abs = float(np.corrcoef(np.abs(hist[1:]), np.abs(hist[:-1]))[0, 1])

    stationary = StationaryBootstrap(mean_block=20).generate(hist, 500, 1000, seed=11)
    iid = IIDBootstrap().generate(hist, 500, 1000, seed=11)

    ac_stationary = lag1_autocorr(np.abs(stationary.paths))
    ac_iid = lag1_autocorr(np.abs(iid.paths))

    assert hist_ac_abs > 0.3, "Testreihe zeigt kein Vol-Clustering"
    assert ac_stationary > 0.7 * hist_ac_abs, (
        f"Vol-Clustering nur zu {ac_stationary / hist_ac_abs:.0%} erhalten"
    )
    assert abs(ac_iid) < 0.05, f"i.i.d.-Bootstrap zeigt Clustering ({ac_iid:.3f})"


def test_block_resampling_does_not_invent_autocorrelation():
    """Gegenprobe zum Kerntest: was nicht da ist, darf nicht entstehen.

    Die Reihe aus `clustered_returns` hat unkorrelierte Vorzeichen. Wuerde
    der Block-Bootstrap hier Autokorrelation erzeugen, waere der Kerntest
    oben wertlos -- er wuerde dann ein Artefakt des Verfahrens messen statt
    einer Eigenschaft der Historie.
    """
    ens = StationaryBootstrap(mean_block=20).generate(
        clustered_returns(), 500, 500, seed=13
    )
    assert abs(lag1_autocorr(ens.paths)) < 0.05


def test_iid_ensemble_understates_short_horizon_tail_risk():
    """Der Preis des naiven Verfahrens, in einer Zahl -- und wo er auftaucht.

    Vol-Clustering wirkt sich dort aus, wo Verluste sich zusammendraengen:
    im schlimmsten 20-Bar-Abschnitt eines Pfades. Gemessen ueber vier Seeds
    liegt dessen Median beim Block-Ensemble bei rund -25%, beim
    i.i.d.-Ensemble bei rund -22% -- ein Achtel des Risikos faellt allein
    dadurch weg, dass die grossen Tage gleichmaessig verteilt werden.

    **Beim 5%-Quantil der Endrendite ueber 250 Bars ist der Effekt nicht zu
    sehen** -- dort liegen beide Ensembles bei -51%, das Block-Ensemble sogar
    einen Hauch besser. Das ist gemessen, nicht vermutet, und es hat einen
    Grund: ueber einen langen Horizont mittelt sich die Vol-Mischung jedes
    Pfades wieder aus, und ein Pfad, der viele ruhige Bloecke erwischt,
    verliert zugleich weniger an Volatilitaets-Drag. Die Aussage "i.i.d.
    unterschaetzt Tails" gilt also fuer kurzfristige Verlustserien, nicht
    pauschal fuer jede Kennzahl. Der Test haelt genau diese Einschraenkung
    fest, damit sie nicht spaeter zur pauschalen Behauptung wird.
    """
    hist = clustered_returns()
    stationary = StationaryBootstrap(mean_block=20).generate(hist, 250, 4000, seed=17)
    iid = IIDBootstrap().generate(hist, 250, 4000, seed=17)

    worst_stationary = np.median(worst_window_loss(stationary.paths, 20))
    worst_iid = np.median(worst_window_loss(iid.paths, 20))

    assert worst_stationary < worst_iid * 1.05, (
        f"Block-Ensemble {worst_stationary:.3f} vs i.i.d. {worst_iid:.3f} -- "
        f"das Clustering schlaegt nicht auf die Verlustserien durch"
    )


# ---------------------------------------------------------------------------
# Randverteilung und Drift
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [StationaryBootstrap, IIDBootstrap])
def test_marginal_distribution_matches_the_history(cls):
    """Beide Verfahren ziehen aus derselben Menge -- nur in anderer Ordnung.

    Mittelwert und Streuung der Einzelrenditen muessen deshalb der Historie
    entsprechen. Weicht das ab, wird irgendwo unbeabsichtigt umgewichtet.
    """
    hist = ar1_returns(seed=4)
    ens = cls().generate(hist, horizon=250, n_paths=2000, seed=23)

    assert ens.paths.mean() == pytest.approx(hist.mean(), abs=0.1 * hist.std())
    assert ens.paths.std() == pytest.approx(hist.std(), rel=0.05)


def test_demean_centres_the_ensemble_without_flattening_it():
    """`demean=True` nimmt die Drift heraus, sonst nichts.

    Der Zweck: eine Historie aus einem Bullenmarkt traegt ihre Aufwaertsdrift
    sonst in jeden simulierten Pfad, und jede Allokation darueber ist
    optimistisch verzerrt. Die Streuung -- also das Risiko -- darf die
    Zentrierung nicht anfassen.
    """
    rng = np.random.default_rng(0)
    bull = rng.normal(0.004, 0.01, 1000)  # klare positive Drift

    plain = StationaryBootstrap().generate(bull, 250, 1000, seed=31)
    centred = StationaryBootstrap(demean=True).generate(bull, 250, 1000, seed=31)

    assert plain.paths.mean() == pytest.approx(bull.mean(), rel=0.15)
    assert abs(centred.paths.mean()) < 0.05 * bull.std()
    assert centred.paths.std() == pytest.approx(plain.paths.std(), rel=0.02)

    # Der entfernte Mittelwert bleibt nachvollziehbar statt spurlos zu sein.
    assert centred.meta["demean"] is True
    assert centred.meta["history_mean"] == pytest.approx(bull.mean())


def test_demean_is_off_by_default():
    """Nagelt die Entscheidung aus dem Docstring fest.

    Ein spaeterer Wechsel des Defaults wuerde jede gespeicherte Simulation
    still veraendern -- er soll hier auffallen und nicht im Backtest.
    """
    assert StationaryBootstrap().demean is False
    assert IIDBootstrap().demean is False


# ---------------------------------------------------------------------------
# Zirkulaeres Ziehen
# ---------------------------------------------------------------------------


def test_every_observation_is_drawn_equally_often():
    """Auch die letzten Beobachtungen -- das ist der Zweck des Wrap-Around.

    Ohne zirkulaeres Ziehen kann die letzte Beobachtung nur als letztes
    Element eines Blocks auftauchen und waere um ein Vielfaches
    untergewichtet. Untergewichtet waere damit ausgerechnet die juengste
    Marktphase, also die fuer eine Allokationsentscheidung relevanteste.
    """
    hist = marked_history(120)
    ens = StationaryBootstrap(mean_block=20).generate(hist, 200, 2000, seed=41)

    counts = np.array([(ens.paths == value).sum() for value in hist], dtype=float)
    share = counts / counts.sum()
    expected = 1.0 / len(hist)

    assert share.min() > 0.7 * expected, "Manche Beobachtung wird kaum gezogen"
    # Die letzten zehn sind der interessante Teil: hier schluege ein fehlender
    # Wrap als ansteigender Abfall der Haeufigkeit durch.
    assert share[-10:].mean() == pytest.approx(expected, rel=0.15)


def test_blocks_actually_wrap_around_the_end():
    """Direkter Nachweis: auf die letzte Beobachtung folgt die erste.

    Der Haeufigkeitstest oben ist ein Indiz, dieser Test ist der Beweis --
    er faengt auch eine Umsetzung, die den Rand durch Zurechtstutzen statt
    durch Fortsetzen behandelt.
    """
    hist = marked_history(60)
    ens = StationaryBootstrap(mean_block=20).generate(hist, 200, 500, seed=43)

    was_last = ens.paths[:, :-1] == hist[-1]
    next_is_first = ens.paths[:, 1:] == hist[0]
    assert int((was_last & next_is_first).sum()) > 0


# ---------------------------------------------------------------------------
# Parameter und Fehlerfaelle
# ---------------------------------------------------------------------------


def test_mean_block_one_degenerates_to_iid():
    """Dokumentiert den Grenzfall als Eigenschaft, nicht als Zufall.

    Bei p = 1 beginnt jeder Schritt einen neuen Block -- das *ist* der
    i.i.d.-Bootstrap. Faellt dieser Test, stimmt die Blocklogik nicht.
    """
    hist = ar1_returns()
    degenerate = StationaryBootstrap(mean_block=1).generate(hist, 64, 200, seed=53)
    iid = IIDBootstrap().generate(hist, 64, 200, seed=53)
    np.testing.assert_array_equal(degenerate.paths, iid.paths)

    assert abs(lag1_autocorr(degenerate.paths)) < 0.05


def test_too_short_history_raises_a_useful_error():
    """Kein stilles Weiterrechnen auf zu duenner Historie.

    30 Renditen wuerden ein formal gueltiges Ensemble ergeben, dessen
    Streuung aber nur die Zufaelligkeit dieser 30 Werte widerspiegelt. Die
    Fehlermeldung nennt beide Zahlen, damit klar ist, wie viel fehlt.
    """
    with pytest.raises(ValueError, match="Zu wenig Historie"):
        StationaryBootstrap().generate(np.zeros(30), 50, 10, seed=1)


def test_history_must_cover_several_block_lengths():
    """Eine grosse mittlere Blocklaenge verlangt mehr Historie.

    Sonst deckt ein einzelner Block schon einen Grossteil der Reihe ab, alle
    Pfade bestehen aus wenigen fast identischen Stuecken -- und das Ensemble
    taeuscht eine Streuung vor, die es nicht hat.
    """
    hist = ar1_returns(n=100)
    StationaryBootstrap(mean_block=20).generate(hist, 50, 10, seed=1)  # 100 >= 60, ok
    with pytest.raises(ValueError, match="Zu wenig Historie"):
        StationaryBootstrap(mean_block=60).generate(hist, 50, 10, seed=1)


def test_nan_in_history_is_reported_not_silently_glued_over():
    """Eine Datenluecke ist ein Befund, kein Filterfall.

    Ein `nan` still herauszuwerfen klebt die Nachbarn der Luecke aneinander
    und erzeugt einen Uebergang, den es im Markt nie gab -- und der wandert
    beim Block-Bootstrap in jeden Block, der ueber ihn laeuft. Genau dieses
    Verfahren lebt von der zeitlichen Nachbarschaft.
    """
    hist = ar1_returns(n=200)
    hist[5] = np.nan

    with pytest.raises(ValueError, match="Datenluecke"):
        StationaryBootstrap(mean_block=10).generate(hist, 50, 100, seed=1)


def test_known_gap_can_be_accepted_explicitly():
    """Wer die Luecke kennt und hinnimmt, sagt das im Aufruf."""
    hist = ar1_returns(n=200)
    hist[5] = np.nan
    ens = StationaryBootstrap(mean_block=10, allow_gaps=True).generate(
        hist, 50, 100, seed=1
    )

    assert np.all(np.isfinite(ens.paths))
    assert ens.meta["n_history"] == 199


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"horizon": 0, "n_paths": 10}, "horizon"),
        ({"horizon": 10, "n_paths": 0}, "n_paths"),
    ],
)
def test_nonsense_request_is_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        StationaryBootstrap().generate(ar1_returns(n=200), seed=1, **kwargs)


def test_mean_block_below_one_is_rejected():
    with pytest.raises(ValueError, match="mean_block"):
        StationaryBootstrap(mean_block=0)


# ---------------------------------------------------------------------------
# Laufzeit
# ---------------------------------------------------------------------------


def test_generation_is_vectorised_enough_for_large_ensembles():
    """Bewusst klein gehalten, misst aber genau das Richtige.

    2.000 x 720 sind 1,44 Mio Ziehungen. Eine Python-Schleife pro Pfad
    schafft das nicht in Sekunden -- die Schranke faellt also, sobald jemand
    die Vektorisierung aufgibt, bleibt aber locker genug, um nicht an der
    Tagesform der CI-Maschine zu haengen. Der Zielfall `qt sim --paths 10000`
    ist der fuenffache Aufwand.
    """
    hist = ar1_returns(n=2000)
    start = time.perf_counter()
    ens = StationaryBootstrap().generate(hist, horizon=720, n_paths=2000, seed=61)
    elapsed = time.perf_counter() - start

    assert ens.paths.shape == (2000, 720)
    assert elapsed < 5.0, f"1,44 Mio Ziehungen dauerten {elapsed:.2f}s"
