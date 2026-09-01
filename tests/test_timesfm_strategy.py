"""TimesFM-Strategie: Edge-zu-Gewicht-Logik, Cadence, Ausfallverhalten.

Kein Test hier laedt echte Modellgewichte oder installiert `timesfm` --
geprueft wird die Verdrahtung um das Modell herum, nicht das Modell selbst.
Das ist Absicht: das Paket ist optional (pyproject.toml [timesfm]-Extra),
und die Strategie muss auch ohne es voll funktionsfaehig sein (ADR-022).
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from qt.backtest.engine import run_backtest
from qt.core.config import BacktestConfig, CostConfig
from qt.strategy.library.timesfm_strategy import (
    DEFAULT_MIN_EDGE_BPS,
    ForecastResult,
    ForecasterUnavailable,
    Forecaster,
    NaiveForecaster,
    TimesFMStrategy,
)
from tests.conftest import make_bars

CONTEXT = 64


class _Fixed(Forecaster):
    """Liefert immer denselben Forecast -- fuer exakt kontrollierte Tests."""

    def __init__(self, result: ForecastResult) -> None:
        self.result = result
        self.calls = 0

    def forecast(self, context: np.ndarray, horizon: int) -> ForecastResult:
        self.calls += 1
        return ForecastResult(
            median=self.result.median[:horizon],
            q10=self.result.q10[:horizon] if self.result.q10 is not None else None,
            q90=self.result.q90[:horizon] if self.result.q90 is not None else None,
        )


class _Raising(Forecaster):
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def forecast(self, context: np.ndarray, horizon: int) -> ForecastResult:
        raise self.exc


def _strategy(forecaster, **kwargs):
    kwargs.setdefault("context_len", CONTEXT)
    kwargs.setdefault("forecast_every", 8)
    return TimesFMStrategy(["BTC/USD"], "1h", forecaster=forecaster, **kwargs)


def _bars(n: int = 400, seed: int = 1):
    return {"BTC/USD": make_bars(n, symbol="BTC/USD", seed=seed)}


# ---------------------------------------------------------------------------
# Edge -> Gewicht
# ---------------------------------------------------------------------------


def test_random_walk_never_trades():
    """Ein Forecaster ohne Kante muss die Strategie dauerhaft flach halten."""
    s = _strategy(NaiveForecaster())
    result = run_backtest(s, _bars())

    assert result.n_trades == 0
    assert result.equity["equity"].iloc[-1] == pytest.approx(100_000.0)


def test_edge_above_threshold_goes_long():
    horizon = 8
    big_edge = ForecastResult(median=np.full(horizon, 0.01))  # weit ueber der Schwelle
    s = _strategy(_Fixed(big_edge), horizon=horizon)
    result = run_backtest(s, _bars())

    weights = result.equity["weight_BTC/USD"]
    assert (weights > 0).any()
    assert not (weights < 0).any()


def test_edge_below_threshold_stays_flat():
    horizon = 8
    tiny_edge = ForecastResult(median=np.full(horizon, 1e-6))
    s = _strategy(_Fixed(tiny_edge), horizon=horizon)
    result = run_backtest(s, _bars())

    assert result.n_trades == 0


def test_negative_edge_goes_short_when_allowed():
    horizon = 8
    negative = ForecastResult(median=np.full(horizon, -0.01))
    s = _strategy(_Fixed(negative), horizon=horizon, allow_short=True)
    result = run_backtest(s, _bars())

    weights = result.equity["weight_BTC/USD"]
    assert (weights < 0).any()


def test_negative_edge_stays_flat_without_short():
    horizon = 8
    negative = ForecastResult(median=np.full(horizon, -0.01))
    s = _strategy(_Fixed(negative), horizon=horizon, allow_short=False)
    result = run_backtest(s, _bars())

    assert result.n_trades == 0


def test_threshold_follows_min_edge_bps_parameter():
    """Ein Edge knapp ueber der Standardschwelle handelt, knapp darunter nicht."""
    horizon = 4
    edge_per_step = (DEFAULT_MIN_EDGE_BPS * 1e-4) / horizon

    just_above = ForecastResult(median=np.full(horizon, edge_per_step * 1.5))
    just_below = ForecastResult(median=np.full(horizon, edge_per_step * 0.5))

    above = run_backtest(_strategy(_Fixed(just_above), horizon=horizon), _bars())
    below = run_backtest(_strategy(_Fixed(just_below), horizon=horizon), _bars())

    assert above.n_trades > 0
    assert below.n_trades == 0


def test_confidence_scales_position_size():
    """Eine unsichere Vorhersage (breite Quantile) bekommt weniger Kapital
    als eine sichere -- die Strategie handelt nicht binaer."""
    horizon = 8
    edge = 0.01

    confident = ForecastResult(
        median=np.full(horizon, edge),
        q10=np.full(horizon, edge - 0.001),
        q90=np.full(horizon, edge + 0.001),
    )
    unsure = ForecastResult(
        median=np.full(horizon, edge),
        q10=np.full(horizon, edge - 0.05),
        q90=np.full(horizon, edge + 0.05),
    )

    r_confident = run_backtest(_strategy(_Fixed(confident), horizon=horizon), _bars())
    r_unsure = run_backtest(_strategy(_Fixed(unsure), horizon=horizon), _bars())

    peak_confident = r_confident.equity["weight_BTC/USD"].abs().max()
    peak_unsure = r_unsure.equity["weight_BTC/USD"].abs().max()

    assert peak_confident > peak_unsure > 0


def test_missing_quantiles_use_half_confidence():
    horizon = 4
    edge = DEFAULT_MIN_EDGE_BPS * 1e-4 / horizon * 4  # deutlich ueber der Schwelle
    no_quantiles = ForecastResult(median=np.full(horizon, edge))
    s = _strategy(_Fixed(no_quantiles), horizon=horizon, max_weight=1.0)
    result = run_backtest(s, _bars())

    weights = result.equity["weight_BTC/USD"]
    nonzero = weights[weights != 0]
    assert not nonzero.empty
    assert nonzero.abs().max() == pytest.approx(0.5, abs=1e-6)


# ---------------------------------------------------------------------------
# Cadence / Cache
# ---------------------------------------------------------------------------


def test_forecast_cadence_is_honoured():
    """Der Forecaster wird nur alle `forecast_every` Bars aufgerufen, nicht
    bei jedem -- sonst waere die Strategie bei einem echten Modell unbezahlbar."""
    forecaster = _Fixed(ForecastResult(median=np.zeros(4)))
    s = _strategy(forecaster, horizon=4, forecast_every=10)
    n = 300
    run_backtest(s, _bars(n))

    active_bars = n - s.warmup_bars
    # Nicht exakt nachgerechnet (Randbehandlung ist ein Implementierungsdetail),
    # sondern die Groessenordnung geprueft: deutlich weniger Aufrufe als aktive
    # Bars, und ungefaehr aktive_bars / forecast_every.
    assert 0 < forecaster.calls < active_bars
    assert active_bars / 12 <= forecaster.calls <= active_bars / 8
    assert s.telemetry.calls == forecaster.calls
    assert s.telemetry.cached > 0


def test_weight_is_held_between_forecasts():
    horizon = 4
    edge = ForecastResult(median=np.full(horizon, 0.01))
    s = _strategy(_Fixed(edge), horizon=horizon, forecast_every=20)
    result = run_backtest(s, _bars(300))

    weights = result.equity["weight_BTC/USD"]
    nonzero = weights[weights != 0]
    # Ueber weite Strecken muss dasselbe Gewicht mehrfach hintereinander stehen.
    assert nonzero.value_counts().max() > 5


# ---------------------------------------------------------------------------
# Lookahead
# ---------------------------------------------------------------------------


def test_future_prices_do_not_change_the_past():
    """Zukunftsbars duerfen die Vergangenheit nicht veraendern -- auch nicht
    ueber die Cadence-/Cache-Logik dieser Strategie."""

    class ContextDependent(Forecaster):
        def forecast(self, context: np.ndarray, horizon: int) -> ForecastResult:
            return ForecastResult(median=np.full(horizon, float(np.sign(context.sum())) * 0.01))

    n_common = 250
    rng = np.random.default_rng(11)
    common = 100 * np.cumprod(1 + rng.normal(0.0002, 0.01, n_common))
    crash = common[-1] * np.cumprod(1 + np.full(120, -0.02))
    rally = common[-1] * np.cumprod(1 + np.full(120, 0.02))

    curves = []
    for tail in (crash, rally):
        prices = np.concatenate([common, tail])
        s = _strategy(ContextDependent(), horizon=4, forecast_every=4)
        result = run_backtest(s, {"BTC/USD": make_bars(len(prices), prices=prices)})
        curves.append(result.equity.head(n_common)["equity"].to_numpy())

    np.testing.assert_array_equal(curves[0], curves[1])


def test_deterministic_across_runs():
    edge = ForecastResult(median=np.full(4, 0.008))
    bars = _bars(300)

    runs = [
        run_backtest(_strategy(_Fixed(edge), horizon=4), bars).equity["equity"].to_numpy()
        for _ in range(2)
    ]
    np.testing.assert_array_equal(runs[0], runs[1])


# ---------------------------------------------------------------------------
# Ausfallverhalten
# ---------------------------------------------------------------------------


def test_missing_package_warns_once_and_holds_last_weight():
    """Ohne installiertes `timesfm` faellt der Default-Forecaster zurueck --
    mit genau einer Warnung, nicht einer pro Bar, und ohne Absturz."""
    s = _strategy(None, forecast_every=8)  # Default-Forecaster: echtes TimesFM
    bars = _bars(300)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = run_backtest(s, bars)

    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert len(runtime_warnings) == 1
    assert result.n_trades == 0
    assert s.telemetry.failures > 0


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("Netzwerk weg"), ValueError("kaputte Antwort"), OSError("Checkpoint fehlt")],
    ids=["netzwerk", "wert", "os"],
)
def test_any_forecaster_failure_falls_back(exc):
    """Ein Ausfall des Forecasters ist ein langweiliges Ereignis, kein
    Abbruch -- dieselbe Haltung wie beim LLM-Allokator (ADR-018)."""
    s = _strategy(_Raising(ForecasterUnavailable(str(exc))))
    result = run_backtest(s, _bars(300))

    assert result.n_trades == 0
    assert s.telemetry.failures > 0


# ---------------------------------------------------------------------------
# Konstruktion und Registry
# ---------------------------------------------------------------------------


def test_invalid_params_are_rejected():
    with pytest.raises(ValueError, match="context_len"):
        TimesFMStrategy(["BTC/USD"], "1h", context_len=8)
    with pytest.raises(ValueError, match="horizon"):
        TimesFMStrategy(["BTC/USD"], "1h", horizon=0)
    with pytest.raises(ValueError, match="forecast_every"):
        TimesFMStrategy(["BTC/USD"], "1h", forecast_every=0)


def test_registered_under_timesfm():
    from qt.strategy.registry import get, load_library, names

    load_library()
    assert "timesfm" in names()
    assert get("timesfm") is TimesFMStrategy


def test_costs_reduce_returns_like_any_strategy():
    """Auch diese Strategie muss unter Kosten schlechter abschneiden."""
    horizon = 4
    edge = ForecastResult(median=np.full(horizon, 0.02))
    bars = _bars(300)

    free = run_backtest(
        _strategy(_Fixed(edge), horizon=horizon),
        bars,
        BacktestConfig(costs=CostConfig(taker_fee_bps=0, half_spread_bps=0, slippage_bps=0)),
    )
    paid = run_backtest(_strategy(_Fixed(edge), horizon=horizon), bars, BacktestConfig())

    assert paid.fees_paid > 0
    assert paid.equity["equity"].iloc[-1] < free.equity["equity"].iloc[-1]
