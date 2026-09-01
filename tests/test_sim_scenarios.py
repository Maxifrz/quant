"""Szenario-Priors: gewichtet das LLM Moeglichkeiten, oder prognostiziert es?

Kein Test hier ruft ein Sprachmodell auf. Geprueft wird die Mechanik der
Umgewichtung -- vor allem, dass sie *wirkt* und dass sie *begrenzt* ist.
Beides kann still ausfallen: eine wirkungslose Umgewichtung sieht aus wie
"der Prior war eben schwach", eine unbegrenzte macht aus dem Ensemble
heimlich wieder eine Punktprognose.
"""

from __future__ import annotations

import numpy as np
import pytest

from qt.sim.base import PathEnsemble
from qt.sim.scenarios import (
    DEFAULT_MAX_TILT,
    Direction,
    PathFeature,
    ScenarioPrior,
    apply_priors,
    path_feature,
)


def _ensemble(n_paths: int = 4000, horizon: int = 60, seed: int = 0) -> PathEnsemble:
    rng = np.random.default_rng(seed)
    return PathEnsemble(paths=rng.normal(0.001, 0.02, (n_paths, horizon)))


# ---------------------------------------------------------------------------
# Wirkung: der Prior muss die Verteilung tatsaechlich verschieben
# ---------------------------------------------------------------------------


def test_bearish_prior_shifts_the_whole_distribution_down():
    """Der zentrale Wirkungstest.

    Ein Prior, der die Quantile nicht bewegt, ist Dekoration -- und faellt
    ohne diesen Test nicht auf, weil das Ergebnis wie eine schwache
    Einschaetzung aussieht statt wie ein Fehler. Genau das ist beim Bau
    dieser Datei passiert (Gewichte kappen statt Staerke skalieren).
    """
    e = _ensemble()
    bearish = ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.LOWER, 0.9)

    tilted, _ = apply_priors(e, [bearish])

    base = e.quantile([0.05, 0.5, 0.95])
    after = tilted.quantile([0.05, 0.5, 0.95])
    assert np.all(after < base), f"Prior hat nichts verschoben: {base} -> {after}"


def test_bullish_prior_shifts_up():
    e = _ensemble()
    bullish = ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.HIGHER, 0.9)

    tilted, _ = apply_priors(e, [bullish])

    assert np.all(tilted.quantile([0.05, 0.5, 0.95]) > e.quantile([0.05, 0.5, 0.95]))


def test_volatility_prior_raises_the_ensembles_volatility():
    """Ein Prior verschiebt Gewicht in seiner **eigenen** Eigenschaft.

    Naheliegend, aber falsch waere zu pruefen, ob ein Vol-Prior die
    Endrendite-Verteilung verbreitert: bei unabhaengigen Gauss-Renditen ist
    die realisierte Vola eines Pfades von seiner Endrendite unabhaengig, und
    der Test schluege fehl, obwohl der Prior korrekt arbeitet. Geprueft wird
    deshalb die Groesse, die der Prior tatsaechlich anfasst.
    """
    e = _ensemble()
    vol_up = ScenarioPrior(PathFeature.VOLATILITY, Direction.HIGHER, 0.9)

    tilted, _ = apply_priors(e, [vol_up])

    vols = path_feature(e, PathFeature.VOLATILITY)
    assert float(tilted.weights @ vols) > float(e.weights @ vols)


def test_volatility_prior_widens_the_tails_when_vol_drives_outcomes():
    """Auf einem Ensemble, in dem hohe Vola tatsaechlich extreme Ausgaenge
    erzeugt, muss der Vol-Prior die Endrendite-Verteilung verbreitern.

    Gegenstueck zum vorigen Test: dort war die Unabhaengigkeit der Grund,
    warum nichts passiert -- hier gibt es sie nicht, also muss etwas passieren.
    """
    rng = np.random.default_rng(3)
    # Halb ruhige, halb turbulente Pfade -- Vola und Ergebnisstreuung haengen
    # hier per Konstruktion zusammen.
    calm = rng.normal(0.0, 0.005, (2000, 60))
    wild = rng.normal(0.0, 0.04, (2000, 60))
    e = PathEnsemble(paths=np.vstack([calm, wild]))
    vol_up = ScenarioPrior(PathFeature.VOLATILITY, Direction.HIGHER, 0.9)

    tilted, _ = apply_priors(e, [vol_up])

    base_p05, base_p95 = e.quantile([0.05, 0.95])
    after_p05, after_p95 = tilted.quantile([0.05, 0.95])
    assert (after_p95 - after_p05) > (base_p95 - base_p05)


def test_drawdown_prior_worsens_the_left_tail():
    e = _ensemble()
    ugly = ScenarioPrior(PathFeature.MAX_DRAWDOWN, Direction.LOWER, 0.9)

    tilted, _ = apply_priors(e, [ugly])

    assert tilted.quantile(0.05) < e.quantile(0.05)


def test_stronger_prior_shifts_further():
    """Die Staerke muss monoton wirken, sonst ist sie ein Zierrat."""
    e = _ensemble()
    weak = ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.LOWER, 0.2)
    strong = ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.LOWER, 0.9)

    # max_tilt gross genug, dass beide Staerken darunter bleiben und die
    # Begrenzung den Vergleich nicht einebnet.
    m_weak, _ = apply_priors(e, [weak], max_tilt=1e6)
    m_strong, _ = apply_priors(e, [strong], max_tilt=1e6)

    assert m_strong.quantile(0.5) < m_weak.quantile(0.5) < e.quantile(0.5)


# ---------------------------------------------------------------------------
# Begrenzung: der Prior darf das Ensemble nicht zur Punktprognose schrumpfen
# ---------------------------------------------------------------------------


def test_max_tilt_bounds_the_weight_spread():
    e = _ensemble()
    extreme = ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.LOWER, 1.0)

    tilted, report = apply_priors(e, [extreme], max_tilt=3.0)

    assert report.clipped
    assert report.max_weight_ratio == pytest.approx(3.0, rel=1e-6)
    assert tilted.weights.max() / tilted.weights.min() == pytest.approx(3.0, rel=1e-6)


def test_tilt_limit_scales_rather_than_ties_paths_together():
    """Die Begrenzung darf die Rangfolge nicht einebnen.

    Ein Deckel auf die Gewichte wuerde einen Grossteil der Pfade auf exakt
    denselben Wert setzen -- die Spanne saehe im Report korrekt aus, die
    Gewichtung waere trotzdem praktisch verschwunden.
    """
    e = _ensemble()
    extreme = ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.LOWER, 1.0)

    tilted, report = apply_priors(e, [extreme], max_tilt=3.0)

    assert report.clipped
    # Nach dem Skalieren ist praktisch jedes Gewicht verschieden.
    assert len(np.unique(tilted.weights)) > 0.9 * e.n_paths
    # Und die Wirkung bleibt erhalten.
    assert tilted.quantile(0.5) < e.quantile(0.5)


def test_effective_sample_size_is_reported():
    """Ohne diese Zahl merkt niemand, wenn aus 10.000 Pfaden faktisch 50 wurden."""
    e = _ensemble()
    strong = ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.LOWER, 1.0)

    _, mild = apply_priors(e, [strong], max_tilt=2.0)
    _, wild = apply_priors(e, [strong], max_tilt=1e6)

    assert mild.effective_sample_size > wild.effective_sample_size
    assert 0 < wild.ess_fraction < 1
    assert "effektive Stichprobe" in mild.summary()


def test_max_tilt_of_one_means_no_tilt():
    e = _ensemble()
    prior = ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.LOWER, 1.0)

    tilted, _ = apply_priors(e, [prior], max_tilt=1.0)

    np.testing.assert_allclose(tilted.weights, e.weights)


def test_max_tilt_below_one_is_rejected():
    with pytest.raises(ValueError, match="max_tilt"):
        apply_priors(_ensemble(100), [], max_tilt=0.5)


# ---------------------------------------------------------------------------
# Zusammenspiel und Randfaelle
# ---------------------------------------------------------------------------


def test_opposing_priors_cancel_out():
    """Zwei gegenlaeufige Einschaetzungen duerfen keine willkuerliche Auswahl
    erzwingen, sondern muessen zur Gleichgewichtung zurueckfuehren."""
    e = _ensemble()
    down = ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.LOWER, 0.8)
    up = ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.HIGHER, 0.8)

    tilted, _ = apply_priors(e, [down, up])

    np.testing.assert_allclose(tilted.weights, e.weights, atol=1e-12)


def test_no_priors_leaves_the_ensemble_untouched():
    e = _ensemble()
    tilted, report = apply_priors(e, [])

    assert tilted is e
    assert report.max_weight_ratio == 1.0
    assert report.ess_fraction == 1.0
    assert "Keine Szenario-Priors" in report.summary()


def test_degenerate_feature_is_skipped_not_fatal():
    """Identische Pfade haben keine Streuung -- der Prior hat dann nichts zu
    gewichten. Das ist kein Fehler des Modells, sondern eine Eigenschaft des
    Ensembles."""
    flat = PathEnsemble(paths=np.full((100, 20), 0.001))
    prior = ScenarioPrior(PathFeature.VOLATILITY, Direction.HIGHER, 1.0)

    tilted, report = apply_priors(flat, [prior])

    np.testing.assert_allclose(tilted.weights, flat.weights)
    assert report.effective_sample_size == pytest.approx(100.0)


def test_reweighting_never_produces_invalid_weights():
    e = _ensemble()
    priors = [
        ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.LOWER, 1.0),
        ScenarioPrior(PathFeature.VOLATILITY, Direction.HIGHER, 1.0),
        ScenarioPrior(PathFeature.MAX_DRAWDOWN, Direction.LOWER, 1.0),
    ]
    tilted, _ = apply_priors(e, priors)

    assert np.all(np.isfinite(tilted.weights))
    assert np.all(tilted.weights > 0)
    assert tilted.weights.sum() == pytest.approx(1.0)


def test_original_ensemble_is_not_mutated():
    """Ein umgewichtetes Ensemble ist eine andere Annahme ueber die Welt --
    die alte muss danebenstehen bleiben und vergleichbar sein."""
    e = _ensemble()
    before = e.weights.copy()

    apply_priors(e, [ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.LOWER, 1.0)])

    np.testing.assert_array_equal(e.weights, before)


def test_deterministic():
    e = _ensemble()
    prior = ScenarioPrior(PathFeature.VOLATILITY, Direction.HIGHER, 0.7)

    a, _ = apply_priors(e, [prior])
    b, _ = apply_priors(e, [prior])

    np.testing.assert_array_equal(a.weights, b.weights)


@pytest.mark.parametrize("strength", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_strength_rejected(strength):
    with pytest.raises(ValueError, match="strength"):
        ScenarioPrior(PathFeature.VOLATILITY, Direction.HIGHER, strength)


@pytest.mark.parametrize("feature", list(PathFeature))
def test_every_feature_is_computable(feature):
    e = _ensemble(500, 30)
    values = path_feature(e, feature)

    assert values.shape == (500,)
    assert np.all(np.isfinite(values))


def test_default_max_tilt_is_a_real_constraint():
    """Der Default muss tatsaechlich binden, sonst ist er Zierrat."""
    e = _ensemble()
    strong = ScenarioPrior(PathFeature.TERMINAL_RETURN, Direction.LOWER, 1.0)

    _, report = apply_priors(e, [strong])

    assert report.clipped
    assert report.max_weight_ratio == pytest.approx(DEFAULT_MAX_TILT, rel=1e-6)


# ---------------------------------------------------------------------------
# Uebersetzung vom Modellvorschlag zu Priors
# ---------------------------------------------------------------------------


def test_hallucinated_feature_is_dropped_not_guessed():
    """Ein erfundener Eigenschaftsname ist ein Fehler des Modells -- und ein
    Fehler wird zu nichts, nicht zu einer beliebigen gueltigen Alternative."""
    from qt.llm.schemas import ScenarioProposal
    from qt.sim.scenarios import from_proposal

    proposal = ScenarioProposal.model_validate(
        {
            "priors": [
                {"feature": "volatility", "direction": "higher", "strength": 0.7},
                {"feature": "mondphase", "direction": "higher", "strength": 0.9},
                {"feature": "terminal_return", "direction": "seitwaerts", "strength": 0.5},
                {"feature": "max_drawdown", "direction": "lower", "strength": 0.0},
            ]
        }
    )

    priors, report = from_proposal(proposal)

    assert len(priors) == 1
    assert priors[0].feature is PathFeature.VOLATILITY
    assert report.accepted == 1
    assert report.rejected == 3
    assert "mondphase" in report.unknown_feature
    assert "seitwaerts" in report.unknown_direction
    assert report.zero_strength == 1


def test_translation_report_distinguishes_silence_from_nonsense():
    """Ohne diese Unterscheidung sieht ein Modell, dessen Vorschlaege alle im
    Filter haengenbleiben, aus wie ein zurueckhaltendes Modell."""
    from qt.llm.schemas import ScenarioProposal
    from qt.sim.scenarios import from_proposal

    silent, silent_report = from_proposal(ScenarioProposal())
    nonsense, nonsense_report = from_proposal(
        ScenarioProposal.model_validate(
            {"priors": [{"feature": "x", "direction": "y", "strength": 0.9}]}
        )
    )

    assert silent == nonsense == []
    assert silent_report.rejected == 0
    assert nonsense_report.rejected == 1
    assert "nichts verworfen" in silent_report.summary()
    assert "verworfen" in nonsense_report.summary()


def test_case_and_whitespace_are_tolerated():
    """Das Modell schreibt manchmal 'Volatility' oder ' higher '. Das ist
    keine Halluzination, sondern Formatierung -- und sie zu verwerfen waere
    unnoetig streng."""
    from qt.llm.schemas import ScenarioProposal
    from qt.sim.scenarios import from_proposal

    proposal = ScenarioProposal.model_validate(
        {"priors": [{"feature": " Volatility ", "direction": "HIGHER", "strength": 0.5}]}
    )

    priors, report = from_proposal(proposal)

    assert report.accepted == 1
    assert priors[0].feature is PathFeature.VOLATILITY
    assert priors[0].direction is Direction.HIGHER


# ---------------------------------------------------------------------------
# Szenario-Briefing: dieselben Regeln wie ADR-017
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden", ["2020", "2026", "BTC", "USD", "Maerz", "January", "Januar"]
)
def test_scenario_briefing_leaks_neither_time_nor_names(forbidden):
    from qt.llm.briefing import build_scenario_briefing

    rng = np.random.default_rng(0)
    returns = rng.normal(0.0005, 0.02, 800)
    briefing = build_scenario_briefing(returns, _ensemble(1000, 60), "4h")

    assert forbidden not in briefing


def test_scenario_briefing_is_byte_stable():
    """Voraussetzung dafuer, dass der Antwort-Cache ueberhaupt trifft."""
    from qt.llm.briefing import build_scenario_briefing

    rng = np.random.default_rng(0)
    returns = rng.normal(0.0005, 0.02, 800)
    e = _ensemble(1000, 60)

    assert build_scenario_briefing(returns, e, "4h") == build_scenario_briefing(
        returns, e, "4h"
    )


def test_scenario_briefing_describes_the_ensemble():
    """Ohne Bezugspunkt wuesste das Modell nicht, ob 'erhoehte Volatilitaet'
    gegenueber dem Ensemble eine Verschiebung waere oder dessen Normalfall."""
    from qt.llm.briefing import build_scenario_briefing

    rng = np.random.default_rng(0)
    briefing = build_scenario_briefing(rng.normal(0, 0.02, 800), _ensemble(1000, 60), "4h")

    for key in ("terminal_return_median", "prob_loss", "path_vol_median", "n_paths"):
        assert key in briefing


def test_stub_scenario_client_proposes_nothing():
    """Der neutrale Zustand ist das gleichgewichtete Ensemble.

    Ein Stub, der Priors erfaende, wuerde in Laeufen ohne API-Zugang eine
    Verzerrung einbauen, die im Ergebnis wie eine Modellentscheidung aussaehe.
    """
    from qt.llm.client import StubScenarioClient

    client = StubScenarioClient()
    proposal = client.propose("irgendein briefing")

    assert proposal.priors == []
    assert client.calls == 1
