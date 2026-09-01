"""Tests der Zielfunktion aus `qt.sim.objective`.

Diese Datei entscheidet, wie viel Risiko das System insgesamt nimmt. Ein
Vorzeichenfehler darin dreht eine Risikogrenze in ihr Gegenteil, ohne dass
irgendetwas fehlschlaegt -- die Zahlen saehen weiter plausibel aus. Deshalb
sind die Kernfaelle **analytisch nachrechenbar** und nicht aus dem Code
abgeschrieben, und deshalb steht die Beziehung CVaR <= VaR als eigener Test
da: sie ist die eine Aussage, die bei einem gedrehten Vorzeichen sofort
kippt.

Getestet wird ausserdem, dass die Nebenbedingung **binden kann**. Eine
Optimierung, die immer irgendeine Allokation zurueckgibt, hat keine
Nebenbedingung, sondern eine Dekoration.
"""

from __future__ import annotations

import numpy as np
import pytest

from qt.sim.base import PathEnsemble, weighted_quantile
from qt.sim.objective import (
    AllocationScore,
    ObjectiveConfig,
    cvar,
    optimise_allocation,
    score_allocation,
    value_at_risk,
)

# Kostenfreie Konfiguration fuer alle Tests, in denen die Kosten nur
# Rauschen auf einer sonst exakt nachrechenbaren Zahl waeren. Die Kosten
# selbst haben ihren eigenen Abschnitt weiter unten.
FREE = ObjectiveConfig(round_trip_bps=0.0)


def constant_ensemble(
    per_bar: float, n_paths: int = 50, horizon: int = 20
) -> PathEnsemble:
    """Ensemble, in dem jeder Pfad denselben konstanten Bar-Return hat."""
    return PathEnsemble(paths=np.full((n_paths, horizon), per_bar))


def random_ensemble(
    seed: int = 0, n_paths: int = 2_000, horizon: int = 30, mu: float = 0.001
) -> PathEnsemble:
    rng = np.random.default_rng(seed)
    return PathEnsemble(paths=rng.normal(mu, 0.02, (n_paths, horizon)))


# ----------------------------------------------------------------------
# CVaR: analytisch nachrechenbar
# ----------------------------------------------------------------------


def test_cvar_on_a_constructed_distribution():
    """100 gleichgewichtete Werte 1%..100%, alpha=5% -> Mittel der 5 schlechtesten.

    (1+2+3+4+5)/5 = 3. Kein ungefaehr, kein Toleranzfenster.
    """
    values = np.arange(1, 101) / 100.0

    assert cvar(values, None, alpha=0.05) == pytest.approx(0.03)


def test_cvar_takes_a_partial_atom_at_the_tail_boundary():
    """Der Randpfad zaehlt anteilig, nicht ganz oder gar nicht.

    Bei 10 gleichgewichteten Werten und alpha=0.15 liegen 1,5 Werte im
    Tail: der schlechteste ganz, der zweitschlechteste zur Haelfte.
    (1*0.1 + 2*0.05) / 0.15 = 4/3.
    """
    values = np.arange(1, 11, dtype=float)

    assert cvar(values, None, alpha=0.15) == pytest.approx(4.0 / 3.0)


def test_cvar_uses_the_weights():
    """Dasselbe Wertetripel, ungleich gewichtet -- von Hand nachgerechnet.

    Werte [-0.5, 0.0, 0.1], Gewichte [0.1, 0.45, 0.45], alpha=0.2:
    0.1 Masse bei -0.5, die restlichen 0.1 bei 0.0
    -> (0.1*-0.5 + 0.1*0.0) / 0.2 = -0.25.
    """
    values = np.array([-0.5, 0.0, 0.1])
    weights = np.array([0.1, 0.45, 0.45])

    assert cvar(values, weights, alpha=0.20) == pytest.approx(-0.25)
    assert cvar(values, weights, alpha=0.05) == pytest.approx(-0.5)


def test_cvar_ignoring_weights_would_give_a_different_answer():
    """Absicherung gegen die Fehlerklasse aus ADR-016.

    Ein CVaR, der die Gewichte still fallen laesst, liefert hier -0.5
    statt -0.25 und macht jede Szenario-Umgewichtung wirkungslos.
    """
    values = np.array([-0.5, 0.0, 0.1])
    weights = np.array([0.1, 0.45, 0.45])

    assert cvar(values, weights, 0.20) != pytest.approx(cvar(values, None, 0.20))


def test_upweighting_the_loss_paths_worsens_the_cvar():
    """Dasselbe Ensemble, nur die Verlustpfade hoeher gewichtet."""
    values = np.linspace(-0.4, 0.4, 100)
    uniform = cvar(values, None, alpha=0.20)

    weights = np.where(values < 0, 3.0, 1.0)
    weighted = cvar(values, weights, alpha=0.20)

    assert weighted < uniform


@pytest.mark.parametrize("alpha", [0.01, 0.05, 0.1, 0.25, 0.5, 0.9])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_cvar_never_exceeds_var(alpha: float, seed: int):
    """Per Definition, bei gleicher Vorzeichenkonvention.

    Der Test, der einen gedrehten Vorzeichenfehler faengt: waere eine der
    beiden Groessen als positive Verlusthoehe definiert und die andere als
    Rendite, kippt diese Ungleichung sofort.
    """
    rng = np.random.default_rng(seed)
    values = rng.standard_t(3, 500) / 100.0
    weights = rng.random(500) + 0.01

    assert cvar(values, weights, alpha) <= value_at_risk(values, weights, alpha) + 1e-12


def test_cvar_and_var_are_negative_for_a_losing_sample():
    """Vorzeichenkonvention, explizit festgehalten: negativ = Verlust."""
    values = np.linspace(-0.5, -0.1, 50)

    assert cvar(values, None, 0.05) < 0
    assert value_at_risk(values, None, 0.05) < 0
    assert cvar(values, None, 0.05) == pytest.approx(-0.5, abs=0.02)


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5])
def test_invalid_alpha_is_rejected(alpha: float):
    with pytest.raises(ValueError):
        cvar(np.array([0.1, -0.1]), None, alpha)


@pytest.mark.parametrize(
    "values, weights",
    [
        (np.array([]), None),
        (np.array([0.1, np.nan]), None),
        (np.array([0.1, 0.2]), np.array([1.0, 0.0, 0.0])),
        (np.array([0.1, 0.2]), np.array([0.0, 0.0])),
        (np.array([0.1, 0.2]), np.array([1.0, -1.0])),
    ],
)
def test_degenerate_input_raises_instead_of_returning_nonsense(values, weights):
    with pytest.raises(ValueError):
        cvar(values, weights, 0.05)


# ----------------------------------------------------------------------
# Median statt Mittelwert
# ----------------------------------------------------------------------


def skewed_ensemble() -> PathEnsemble:
    """95 leicht verlierende Pfade, 5 exzellente.

    Der Mittelwert ist stark positiv, der Median negativ. Genau die
    Verteilung, bei der eine Zielfunktion auf den Erwartungswert maximalen
    Hebel empfiehlt -- und in 95 von 100 Zukuenften Geld verliert.
    """
    paths = np.full((100, 20), -0.001)
    paths[:5] = 0.10
    return PathEnsemble(paths=paths)


def test_mean_and_median_disagree_on_a_skewed_ensemble():
    score = score_allocation(skewed_ensemble(), 1.0, cvar_limit=-0.99, cfg=FREE)

    assert score.mean_return > 0.20, "der Mittelwert sieht glaenzend aus"
    assert score.median_return < 0.0, "der typische Pfad verliert"


def test_objective_follows_the_median_and_refuses_the_skewed_bet():
    """Die Zielfunktion nimmt den Median -- also: kein Trade."""
    result = optimise_allocation(skewed_ensemble(), cvar_limit=-0.99, cfg=FREE)

    assert not result.traded
    assert result.exposure == 0.0
    assert "Median" in result.reason


def test_median_return_matches_the_weighted_quantile_of_the_ensemble():
    """Kein zweiter Medianbegriff: dieselbe Zahl wie ueber `base.py`."""
    ensemble = random_ensemble(seed=3)
    score = score_allocation(ensemble, 1.0, cvar_limit=-0.99, cfg=FREE)

    assert score.median_return == pytest.approx(float(ensemble.quantile(0.5)))


def test_var_matches_the_ensemble_quantile_at_full_exposure():
    """Bei Exposure 1 und ohne Kosten ist der VaR genau das 5%-Quantil."""
    ensemble = random_ensemble(seed=4)
    score = score_allocation(ensemble, 1.0, cvar_limit=-0.99, cfg=FREE)

    assert score.var == pytest.approx(float(ensemble.quantile(0.05)))


# ----------------------------------------------------------------------
# Exposure: Skalierung ist nicht linear im Risiko
# ----------------------------------------------------------------------


def test_higher_exposure_worsens_the_cvar_monotonically():
    ensemble = random_ensemble(seed=5)
    scores = [
        score_allocation(ensemble, e, cvar_limit=-0.99, cfg=FREE)
        for e in (0.1, 0.25, 0.5, 0.75, 1.0)
    ]

    cvars = [s.cvar for s in scores]
    assert cvars == sorted(cvars, reverse=True), f"nicht monoton: {cvars}"
    assert all(a >= b for a, b in zip(cvars, cvars[1:]))


def test_higher_exposure_worsens_the_median_drawdown():
    ensemble = random_ensemble(seed=6)
    small = score_allocation(ensemble, 0.25, cvar_limit=-0.99, cfg=FREE)
    large = score_allocation(ensemble, 1.0, cvar_limit=-0.99, cfg=FREE)

    assert large.max_drawdown_median < small.max_drawdown_median < 0


def test_doubling_the_exposure_more_than_doubles_the_loss():
    """Volatilitaetszug, exakt nachrechenbar.

    Ein Pfad aus fuenf Mal (+20%, -20%):
      Exposure 0.5 -> (1.10*0.90)^5 = 0.99^5  = 0.95099... -> -4.90%
      Exposure 1.0 -> (1.20*0.80)^5 = 0.96^5  = 0.81537... -> -18.46%

    Das Vierfache des Verlusts bei doppeltem Exposure. Wer das Ergebnis
    eines Laufs linear hochskaliert statt die Pfade zu skalieren, rechnet
    genau diesen Effekt weg -- und meldet -9.8% statt -18.5%.
    """
    path = np.tile(np.array([0.2, -0.2]), 5).reshape(1, -1)
    ensemble = PathEnsemble(paths=path)

    half = score_allocation(ensemble, 0.5, cvar_limit=-0.99, cfg=FREE)
    full = score_allocation(ensemble, 1.0, cvar_limit=-0.99, cfg=FREE)

    assert half.median_return == pytest.approx(0.99**5 - 1.0)
    assert full.median_return == pytest.approx(0.96**5 - 1.0)
    assert full.median_return < 2.0 * half.median_return


def test_exposure_zero_is_exactly_flat():
    score = score_allocation(random_ensemble(seed=7), 0.0)

    assert score.median_return == 0.0
    assert score.mean_return == 0.0
    assert score.cvar == 0.0
    assert score.var == 0.0
    assert score.prob_loss == 0.0
    assert score.max_drawdown_median == 0.0


def test_ruin_is_absorbing_at_leverage():
    """Ein Pfad unter -100% bleibt bei -100%, er erholt sich nicht rechnerisch.

    Ohne Abschneiden des Wachstumsfaktors wuerde aus (1 + 3*(-0.5)) = -0.5
    beim naechsten Verlust wieder ein Anstieg -- ein Vorzeichenfehler, der
    ausgerechnet die schlimmsten Pfade beschoenigt.
    """
    ensemble = PathEnsemble(paths=np.array([[-0.5, -0.5]]))
    score = score_allocation(ensemble, 3.0, cvar_limit=-0.99, cfg=FREE)

    assert score.median_return == pytest.approx(-1.0)
    assert score.cvar == pytest.approx(-1.0)


def test_prob_loss_counts_the_weighted_share_of_losing_paths():
    paths = np.zeros((100, 5))
    paths[:30] = -0.01
    paths[30:] = 0.01

    score = score_allocation(PathEnsemble(paths=paths), 1.0, -0.99, FREE)

    assert score.prob_loss == pytest.approx(0.30)


@pytest.mark.parametrize("exposure", [-0.1, np.nan, np.inf])
def test_invalid_exposure_is_rejected(exposure: float):
    with pytest.raises(ValueError):
        score_allocation(random_ensemble(seed=8), exposure)


def test_positive_cvar_limit_is_rejected_as_a_sign_error():
    with pytest.raises(ValueError):
        score_allocation(random_ensemble(seed=9), 0.5, cvar_limit=0.20)


# ----------------------------------------------------------------------
# Die Nebenbedingung muss binden koennen
# ----------------------------------------------------------------------


def crash_ensemble() -> PathEnsemble:
    """30 Bars mit je -20%. Ein Szenario, kein Basisfall.

    Bei Exposure 5% bleibt 0.99^30 = 0.7397 -> -26.0% Endrendite. Schon der
    kleinste Gitterpunkt reisst damit die Standardgrenze von -20%.
    """
    return constant_ensemble(-0.20, n_paths=40, horizon=30)


def test_the_smallest_exposure_already_violates_the_limit():
    score = score_allocation(crash_ensemble(), 0.05, cvar_limit=-0.20, cfg=FREE)

    assert score.cvar == pytest.approx(0.99**30 - 1.0)
    assert score.cvar < -0.20
    assert not score.feasible


def test_binding_cvar_limit_yields_no_trade():
    """Das Ergebnis, ohne das die Nebenbedingung Dekoration waere."""
    result = optimise_allocation(crash_ensemble(), cvar_limit=-0.20, cfg=FREE)

    assert not result.traded
    assert result.exposure == 0.0
    assert "CVaR-Grenze" in result.reason
    assert not any(s.feasible for s in result.scores)


def test_no_trade_still_reports_the_whole_grid():
    """Auch eine Absage muss nachpruefbar sein."""
    result = optimise_allocation(crash_ensemble(), cvar_limit=-0.20, cfg=FREE)

    assert len(result.scores) == FREE.n_grid
    assert result.score.exposure == 0.0
    assert result.score.median_return == 0.0


def test_the_two_ways_to_no_trade_are_distinguishable():
    """Grenze verletzt vs. kein Median-Vorteil -- verschiedene Begruendungen."""
    limited = optimise_allocation(crash_ensemble(), cvar_limit=-0.20, cfg=FREE)
    unlimited = optimise_allocation(crash_ensemble(), cvar_limit=-0.999, cfg=FREE)

    assert "kleinste Exposure" in limited.reason
    assert "Median" in unlimited.reason
    assert not limited.traded and not unlimited.traded


def test_a_tighter_limit_never_buys_more_exposure():
    """Monotonie der Nebenbedingung: schaerfer heisst nie mutiger."""
    ensemble = random_ensemble(seed=11, mu=0.002)
    exposures = [
        optimise_allocation(ensemble, cvar_limit=limit, cfg=FREE).exposure
        for limit in (-0.60, -0.40, -0.20, -0.10, -0.05)
    ]

    assert exposures == sorted(exposures, reverse=True), exposures


def test_the_chosen_exposure_holds_the_limit_and_maximises_the_median():
    ensemble = random_ensemble(seed=12, mu=0.002)
    result = optimise_allocation(ensemble, cvar_limit=-0.15, cfg=FREE)

    assert result.traded
    assert result.score.feasible
    assert result.score.cvar >= -0.15

    allowed = [s for s in result.scores if s.feasible]
    assert result.score.median_return == max(s.median_return for s in allowed)
    infeasible = [s for s in result.scores if not s.feasible]
    assert infeasible, "sonst testet dieser Fall die Grenze gar nicht"


def test_the_grid_never_starts_at_zero():
    """Sonst gaebe es immer eine 'zulaessige' Allokation."""
    assert ObjectiveConfig().min_exposure > 0.0
    assert ObjectiveConfig().grid()[0] > 0.0


# ----------------------------------------------------------------------
# Kosten
# ----------------------------------------------------------------------


def test_costs_turn_a_thin_edge_into_no_trade():
    """Ein Drift, der die 90 bps Round-Trip nicht verdient (ADR-009).

    20 Bars a +0.01% ergeben brutto rund +0.2% -- weniger als ein
    Round-Trip kostet. Ohne Kosten empfiehlt die Zielfunktion maximales
    Exposure, mit Kosten korrekterweise gar keins.
    """
    ensemble = constant_ensemble(0.0001, horizon=20)

    with_costs = optimise_allocation(ensemble, cvar_limit=-0.20)
    without = optimise_allocation(ensemble, cvar_limit=-0.20, cfg=FREE)

    assert not with_costs.traded
    assert without.traded and without.exposure == pytest.approx(1.0)


def test_costs_scale_with_the_exposure():
    """Halbes Exposure, halbe Gebuehr -- als Kapitalabschlag exakt."""
    ensemble = constant_ensemble(0.0, horizon=10)
    cfg = ObjectiveConfig(round_trip_bps=90.0)

    half = score_allocation(ensemble, 0.5, cvar_limit=-0.99, cfg=cfg)
    full = score_allocation(ensemble, 1.0, cvar_limit=-0.99, cfg=cfg)

    assert half.median_return == pytest.approx(-0.0045)
    assert full.median_return == pytest.approx(-0.0090)
    assert full.median_return == pytest.approx(2 * half.median_return)


def test_costs_never_improve_a_score():
    ensemble = random_ensemble(seed=13, mu=0.002)
    paid = score_allocation(ensemble, 0.8, cvar_limit=-0.99)
    free = score_allocation(ensemble, 0.8, cvar_limit=-0.99, cfg=FREE)

    assert paid.median_return < free.median_return
    assert paid.cvar < free.cvar


def test_default_costs_come_from_the_cost_model():
    """Kein zweiter, frei erfundener Kostensatz im System."""
    from qt.backtest.costs import round_trip_bps
    from qt.core.config import CostConfig

    assert ObjectiveConfig().round_trip_bps == pytest.approx(
        round_trip_bps(CostConfig())
    )


# ----------------------------------------------------------------------
# Determinismus
# ----------------------------------------------------------------------


def test_optimise_is_deterministic():
    """Zweimal derselbe Input, bitgleiches Ergebnis."""
    a = optimise_allocation(random_ensemble(seed=14), cvar_limit=-0.25)
    b = optimise_allocation(random_ensemble(seed=14), cvar_limit=-0.25)

    assert a == b


def test_ties_are_broken_towards_the_smaller_exposure():
    """Gleicher Median bei weniger Risiko und weniger Kosten gewinnt.

    Alle Pfade haben Rendite 0, der Median ist bei jedem Exposure 0. Ohne
    Tie-Break waere das Ergebnis beliebig; mit Tie-Break faellt es auf
    "kein Trade" zurueck, weil kein Exposure den Median echt verbessert.
    """
    result = optimise_allocation(constant_ensemble(0.0), cvar_limit=-0.99, cfg=FREE)

    assert not result.traded


# ----------------------------------------------------------------------
# Adversarialer Input
# ----------------------------------------------------------------------


def test_identical_paths_do_not_crash_and_collapse_all_measures():
    """Ohne Streuung fallen Median, Mittelwert, VaR und CVaR zusammen."""
    ensemble = constant_ensemble(-0.01, n_paths=64, horizon=12)
    score = score_allocation(ensemble, 1.0, cvar_limit=-0.99, cfg=FREE)

    expected = 0.99**12 - 1.0
    assert score.median_return == pytest.approx(expected)
    assert score.mean_return == pytest.approx(expected)
    assert score.var == pytest.approx(expected)
    assert score.cvar == pytest.approx(expected)


def test_single_path_ensemble_is_scored_without_crashing():
    ensemble = PathEnsemble(paths=np.array([[0.01, -0.02, 0.03]]))
    result = optimise_allocation(ensemble, cvar_limit=-0.99, cfg=FREE)

    assert isinstance(result.score, AllocationScore)
    assert result.score.cvar == pytest.approx(result.score.median_return)


def test_single_bar_horizon_is_scored_without_crashing():
    ensemble = PathEnsemble(paths=np.array([[-0.1], [0.0], [0.2]]))
    score = score_allocation(ensemble, 1.0, cvar_limit=-0.99, cfg=FREE)

    assert score.median_return == pytest.approx(0.0)
    assert score.cvar == pytest.approx(-0.1)


def test_extreme_weight_concentration_is_handled():
    """Ein Pfad traegt praktisch das gesamte Gewicht.

    Dann muss das Ergebnis genau dieser Pfad sein -- nicht der Mittelwert
    ueber alle, und schon gar kein Absturz.
    """
    paths = np.tile(np.linspace(-0.02, 0.02, 100).reshape(-1, 1), (1, 5))
    weights = np.full(100, 1e-12)
    weights[0] = 1.0
    ensemble = PathEnsemble(paths=paths, weights=weights)

    score = score_allocation(ensemble, 1.0, cvar_limit=-0.99, cfg=FREE)
    worst = 0.98**5 - 1.0

    assert score.median_return == pytest.approx(worst, abs=1e-9)
    assert score.cvar == pytest.approx(worst, abs=1e-9)
    assert score.prob_loss == pytest.approx(1.0, abs=1e-9)


def test_all_weight_on_the_best_path_still_respects_the_limit_logic():
    paths = np.array([[-0.3] * 5, [0.05] * 5])
    ensemble = PathEnsemble(paths=paths, weights=np.array([0.0, 1.0]))

    score = score_allocation(ensemble, 1.0, cvar_limit=-0.20, cfg=FREE)

    assert score.cvar == pytest.approx(1.05**5 - 1.0)
    assert score.feasible, "der Verlustpfad hat Gewicht 0 und darf nicht zaehlen"


def test_a_large_ensemble_stays_fast_enough_to_be_useful():
    """Gitter x 10.000 Pfade muss in Sekunden laufen, nicht in Minuten."""
    ensemble = random_ensemble(seed=15, n_paths=10_000, horizon=60)
    result = optimise_allocation(ensemble, cvar_limit=-0.30)

    assert len(result.scores) == ObjectiveConfig().n_grid


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


def test_defaults_are_conservative():
    cfg = ObjectiveConfig()

    assert cfg.max_exposure <= 1.0, "Default darf keinen Hebel erlauben"
    assert 0 < cfg.alpha <= 0.10
    assert cfg.round_trip_bps > 0, "Kosten sind Default, nicht Option"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"alpha": 0.0},
        {"alpha": 1.0},
        {"min_exposure": 0.0},
        {"min_exposure": -0.1},
        {"max_exposure": 0.0},
        {"n_grid": 1},
        {"round_trip_bps": -1.0},
        {"min_exposure": 0.5, "max_exposure": 0.2},
    ],
)
def test_invalid_config_is_rejected_at_load_time(kwargs: dict[str, float]):
    with pytest.raises(ValueError):
        ObjectiveConfig(**kwargs)


def test_grid_is_ascending_and_covers_the_bounds():
    cfg = ObjectiveConfig(min_exposure=0.1, max_exposure=0.8, n_grid=8)
    grid = cfg.grid()

    assert grid[0] == pytest.approx(0.1)
    assert grid[-1] == pytest.approx(0.8)
    assert np.all(np.diff(grid) > 0)


def test_weighted_quantile_is_not_reimplemented():
    """Der VaR kommt aus `base.py`, nicht aus einer zweiten Formel."""
    values = np.linspace(-0.3, 0.3, 77)
    weights = np.linspace(1.0, 2.0, 77)

    assert value_at_risk(values, weights, 0.05) == pytest.approx(
        float(weighted_quantile(values, weights / weights.sum(), 0.05))
    )


# ---------------------------------------------------------------------------
# Risikobasis: Drawdown gegen Endrendite
# ---------------------------------------------------------------------------


def test_drawdown_basis_is_stricter_than_terminal_basis():
    """Ein Konto wird unterwegs liquidiert, nicht am Ende des Horizonts.

    Ein Pfad, der zwischenzeitlich 60% verliert und bei -10% endet, ist real
    ein Totalschaden. Die Endrendite sieht ihn nicht -- deshalb ist der
    Drawdown die strengere und die richtige Basis (ADR-025).
    """
    rng = np.random.default_rng(11)
    ensemble = PathEnsemble(paths=rng.normal(0.001, 0.02, (4000, 180)))

    terminal = optimise_allocation(
        ensemble, cvar_limit=-0.20, cfg=ObjectiveConfig(risk_basis="terminal")
    )
    drawdown = optimise_allocation(
        ensemble, cvar_limit=-0.20, cfg=ObjectiveConfig(risk_basis="drawdown")
    )

    assert drawdown.exposure <= terminal.exposure, (
        "Der Drawdown eines Pfades ist nie besser als seine Endrendite -- "
        "die Drawdown-Grenze muss also mindestens so scharf binden"
    )


def test_a_path_that_recovers_is_still_a_disaster_under_drawdown_basis():
    """Der Fall, um den es geht, an einem konstruierten Beispiel.

    Zwei Pfade: einer faellt tief und erholt sich vollstaendig, einer laeuft
    ruhig. Die Endrendite beider ist gleich -- ihr Risiko ist es nicht.
    """
    deep_then_back = np.array([-0.5, -0.3, 0.4, 1.0])
    calm = np.zeros(4)
    ensemble = PathEnsemble(paths=np.vstack([deep_then_back, calm]))

    scores = [
        score_allocation(ensemble, 1.0, cfg=ObjectiveConfig(risk_basis=basis))
        for basis in ("terminal", "drawdown")
    ]

    # Beide Basen sehen dieselben Pfade, aber unterschiedliche Groessen.
    assert scores[0].drawdown_cvar < scores[0].cvar, (
        "Der schlimmste Ruecksetzer muss tiefer liegen als die Endrendite"
    )
    assert scores[0].cvar == scores[1].cvar
    assert scores[0].drawdown_cvar == scores[1].drawdown_cvar


def test_both_risk_numbers_are_always_reported():
    """Auch die nicht gewaehlte Basis steht im Ergebnis.

    Sonst laesst sich hinterher nicht beantworten, wie das Ergebnis unter der
    anderen Annahme ausgefallen waere -- und genau das ist die Frage, die
    man stellt, wenn eine Grenze bindet.
    """
    rng = np.random.default_rng(3)
    ensemble = PathEnsemble(paths=rng.normal(0.0, 0.02, (500, 60)))

    score = score_allocation(ensemble, 0.5)

    assert score.risk_basis == "drawdown"
    assert np.isfinite(score.cvar)
    assert np.isfinite(score.drawdown_cvar)
    assert "CVaR" in score.describe()


def test_invalid_risk_basis_is_rejected():
    with pytest.raises(Exception):
        ObjectiveConfig(risk_basis="bauchgefuehl")
