"""Baseline-Allokatoren.

Diese Baselines sind das Gate fuer den LLM-Allokator (ADR-004). Ein Gate,
dem man nicht trauen kann, ist schlimmer als keins: waere Vol-Parity still
falsch, koennte ein schlechteres LLM als besser erscheinen. Deshalb werden
die Gewichte hier gegen analytisch konstruierte Faelle geprueft, nicht nur
auf Plausibilitaet.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from qt.portfolio import baselines
from qt.portfolio.base import AllocationContext

TS = datetime(2020, 1, 1, tzinfo=timezone.utc)


def make_ctx(returns: dict[str, np.ndarray], timeframe: str = "1h") -> AllocationContext:
    return AllocationContext(
        ts=TS,
        strategy_ids=list(returns),
        returns=returns,
        equity=100_000.0,
        timeframe=timeframe,
    )


def alternating_log_returns(n: int, size: float) -> np.ndarray:
    """Reihe, deren Log-Renditen exakt +-`size` sind.

    Ueber einfache Renditen +-x waere die Vola zweier Reihen nie ein exaktes
    Vielfaches, weil log(1+x) und log(1-x) nicht symmetrisch sind. So wird
    der Vergleich analytisch exakt statt nur ungefaehr.
    """
    signs = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    return np.exp(signs * size) - 1.0


def gross(allocation: dict[str, float]) -> float:
    return sum(abs(v) for v in allocation.values())


def test_equal_weight_splits_evenly():
    alloc = baselines.EqualWeight().allocate(
        make_ctx({"a": np.zeros(10), "b": np.zeros(10), "c": np.zeros(10)})
    )

    assert set(alloc) == {"a", "b", "c"}
    assert all(w == pytest.approx(1 / 3) for w in alloc.values())
    assert sum(alloc.values()) == pytest.approx(1.0)


def test_vol_parity_halves_weight_of_double_vol():
    """Doppelte Vola -> halbes Gewicht. Der Kern der Baseline.

    Analytisch: 1/v gegen 1/2v, normiert also 2/3 zu 1/3.
    """
    n = 100
    ctx = make_ctx(
        {
            "calm": alternating_log_returns(n, 0.01),
            "wild": alternating_log_returns(n, 0.02),
        }
    )
    alloc = baselines.VolParity(lookback=n).allocate(ctx)

    assert alloc["calm"] == pytest.approx(2 / 3)
    assert alloc["wild"] == pytest.approx(1 / 3)
    assert alloc["calm"] == pytest.approx(2 * alloc["wild"])


def test_vol_parity_excludes_zero_vol_strategy():
    """Vola 0 darf nicht zu 1/0 werden.

    Eine durchgehend flache Strategie haelt kein Risiko -- sie bekommt kein
    Kapital, statt per Division durch null das gesamte zu bekommen.
    """
    n = 60
    ctx = make_ctx(
        {
            "flat": np.zeros(n),
            "active": alternating_log_returns(n, 0.01),
        }
    )
    alloc = baselines.VolParity(lookback=n).allocate(ctx)

    assert alloc["flat"] == 0.0
    assert alloc["active"] == pytest.approx(1.0)
    assert all(np.isfinite(w) for w in alloc.values())


def test_vol_parity_excludes_strategy_without_history():
    """Zu kurze Einzelhistorie -> kein Kapital, aber auch kein nan."""
    n = 60
    ctx = AllocationContext(
        ts=TS,
        strategy_ids=["established", "fresh"],
        returns={
            "established": alternating_log_returns(n, 0.01),
            "fresh": alternating_log_returns(5, 0.01),
        },
        equity=100_000.0,
    )
    alloc = baselines.VolParity(lookback=20).allocate(ctx)

    assert alloc["fresh"] == 0.0
    assert alloc["established"] == pytest.approx(1.0)


def test_vol_parity_all_degenerate_falls_back_to_equal_weight():
    n = 40
    alloc = baselines.VolParity(lookback=n).allocate(
        make_ctx({"a": np.zeros(n), "b": np.zeros(n)})
    )

    assert alloc == {"a": pytest.approx(0.5), "b": pytest.approx(0.5)}


def test_vol_parity_before_warmup_is_equal_weight():
    """Vor dem Warmup ist jede Vola-Schaetzung Rauschen."""
    allocator = baselines.VolParity(lookback=200)
    ctx = make_ctx(
        {
            "calm": alternating_log_returns(50, 0.01),
            "wild": alternating_log_returns(50, 0.05),
        }
    )
    assert ctx.history_length() < allocator.warmup_bars

    alloc = allocator.allocate(ctx)
    assert alloc["calm"] == pytest.approx(0.5)
    assert alloc["wild"] == pytest.approx(0.5)


def test_vol_parity_survives_total_loss_bar():
    """Eine Rendite von -100% darf keine nan-Gewichte erzeugen."""
    n = 40
    wiped = alternating_log_returns(n, 0.01)
    wiped[-1] = -1.5

    alloc = baselines.VolParity(lookback=n).allocate(
        make_ctx({"wiped": wiped, "ok": alternating_log_returns(n, 0.01)})
    )

    assert all(np.isfinite(w) for w in alloc.values())
    assert alloc["ok"] > alloc["wiped"]


def test_best_single_picks_highest_sharpe():
    n = 50
    rng = np.random.default_rng(7)
    noise = rng.normal(0.0, 0.01, n)

    alloc = baselines.BestSingle(lookback=n).allocate(
        make_ctx({"good": noise + 0.005, "bad": noise - 0.005})
    )

    assert alloc == {"good": 1.0, "bad": 0.0}
    assert gross(alloc) == pytest.approx(1.0)


def test_best_single_breaks_ties_alphabetically():
    """Identische Reihen -> immer dieselbe Wahl, egal in welcher Reihenfolge
    sie im Dict stehen. Sonst ist der Backtest nicht reproduzierbar."""
    n = 50
    series = alternating_log_returns(n, 0.01) + 0.001
    allocator = baselines.BestSingle(lookback=n)

    first = allocator.allocate(make_ctx({"zulu": series.copy(), "alpha": series.copy()}))
    second = allocator.allocate(make_ctx({"alpha": series.copy(), "zulu": series.copy()}))

    assert first == second
    assert first["alpha"] == 1.0


def test_best_single_before_warmup_is_equal_weight():
    alloc = baselines.BestSingle(lookback=500).allocate(
        make_ctx({"a": alternating_log_returns(30, 0.01), "b": np.zeros(30)})
    )

    assert alloc["a"] == pytest.approx(0.5)
    assert alloc["b"] == pytest.approx(0.5)


def test_fixed_weights_are_passed_through():
    alloc = baselines.FixedWeights({"a": 0.7, "b": 0.3}).allocate(
        make_ctx({"a": np.zeros(10), "b": np.zeros(10)})
    )

    assert alloc == {"a": pytest.approx(0.7), "b": pytest.approx(0.3)}


def test_fixed_weights_scale_down_when_over_budget():
    """Uebersteigt die Vorgabe das Budget, zaehlt das Verhaeltnis, nicht die Hoehe."""
    alloc = baselines.FixedWeights({"a": 2.0, "b": 1.0, "c": 1.0}).allocate(
        make_ctx({"a": np.zeros(10), "b": np.zeros(10), "c": np.zeros(10)})
    )

    assert gross(alloc) == pytest.approx(1.0)
    assert alloc["a"] == pytest.approx(0.5)


def test_fixed_weights_ignores_unknown_strategies():
    alloc = baselines.FixedWeights({"a": 0.5, "ghost": 0.5}).allocate(
        make_ctx({"a": np.zeros(10), "b": np.zeros(10)})
    )

    assert set(alloc) == {"a", "b"}
    assert alloc["b"] == 0.0


def test_fixed_weights_allow_short_side():
    """Vorzeichen sind erlaubt; nur die Summe der Betraege ist begrenzt."""
    alloc = baselines.FixedWeights({"a": 1.0, "b": -1.0}).allocate(
        make_ctx({"a": np.zeros(10), "b": np.zeros(10)})
    )

    assert alloc["b"] < 0
    assert gross(alloc) == pytest.approx(1.0)


def all_allocators() -> list:
    return [
        baselines.EqualWeight(),
        baselines.VolParity(lookback=20),
        baselines.BestSingle(lookback=20),
        baselines.FixedWeights({"a": 0.6, "b": 0.4}),
    ]


@pytest.mark.parametrize("allocator", all_allocators(), ids=lambda a: a.name)
def test_gross_exposure_never_exceeds_one(allocator):
    """Der Vertrag aus qt.portfolio.base: Summe der Betraege <= 1."""
    rng = np.random.default_rng(3)
    ctx = make_ctx(
        {
            "a": rng.normal(0.0, 0.01, 200),
            "b": rng.normal(0.0, 0.03, 200),
            "c": rng.normal(0.0, 0.001, 200),
        }
    )

    alloc = allocator.allocate(ctx)
    assert gross(alloc) <= 1.0 + 1e-9
    assert all(np.isfinite(w) for w in alloc.values())


@pytest.mark.parametrize("allocator", all_allocators(), ids=lambda a: a.name)
def test_empty_context_does_not_crash(allocator):
    """Vor der ersten Strategie oder nach dem Abschalten aller Strategien."""
    ctx = AllocationContext(ts=TS, strategy_ids=[], returns={}, equity=100_000.0)

    assert allocator.allocate(ctx) == {}


@pytest.mark.parametrize("allocator", all_allocators(), ids=lambda a: a.name)
def test_degenerate_input_does_not_crash(allocator):
    """Leere Reihen, konstante Reihen, nan -- alles kommt in echten Laeufen vor."""
    ctx = make_ctx(
        {
            "empty": np.array([]),
            "constant": np.zeros(50),
            "nan": np.full(50, np.nan),
        }
    )

    alloc = allocator.allocate(ctx)
    assert set(alloc) <= {"empty", "constant", "nan"}
    assert all(np.isfinite(w) for w in alloc.values())
    assert gross(alloc) <= 1.0 + 1e-9


def test_registry_lists_and_resolves_baselines():
    assert baselines.names() == [
        "best_single",
        "equal_weight",
        "fixed_weights",
        "vol_parity",
    ]
    assert baselines.get("vol_parity") is baselines.VolParity

    with pytest.raises(KeyError):
        baselines.get("nope")


def test_default_set_is_the_gate():
    """Das Gate aus ADR-004 -- FixedWeights ist Werkzeug, kein Massstab."""
    gate = {a.name for a in baselines.default_set()}

    assert gate == {"equal_weight", "vol_parity", "best_single"}
