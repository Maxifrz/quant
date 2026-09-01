"""Lookahead-Tests -- die wichtigsten Tests im Projekt.

Lookahead-Bias laesst einen Backtest grossartig aussehen und hinterlaesst
live nichts. Er erzeugt keinen Absturz und keine Warnung, nur zu gute
Zahlen. Deshalb wird die Regel hier getestet und nicht nur dokumentiert.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from qt.backtest.engine import run_backtest
from qt.core.clock import BacktestClock
from qt.features.registry import FeatureStore, LookaheadError
from qt.strategy.registry import get, load_library
from tests.conftest import START, make_bars

load_library()


def test_store_rejects_unclosed_bar():
    """Ein Bar, der noch nicht geschlossen hat, darf nicht in den Store."""
    clock = BacktestClock(START)
    store = FeatureStore(clock)
    bar = make_bars(1)[0]  # schliesst um START + 1h

    with pytest.raises(LookaheadError, match="noch nicht bekannt"):
        store.on_bar(bar)

    clock.advance(bar.close_ts)
    store.on_bar(bar)  # jetzt erlaubt
    assert len(store.window("BTC/USD", "1h")) == 1


def test_clock_cannot_run_backwards():
    """Rueckwaerts laufende Zeit deutet auf falsch sortierte Events hin."""
    clock = BacktestClock(START)
    clock.advance(START + timedelta(hours=5))
    with pytest.raises(ValueError, match="rueckwaerts"):
        clock.advance(START + timedelta(hours=1))


@pytest.mark.parametrize("strategy_name", ["trend", "meanrev", "macross", "elliott"])
def test_future_data_cannot_change_the_past(strategy_name):
    """Der Kern: Bars der Zukunft aendern die Vergangenheit nicht.

    Zwei Laeufe ueber dieselbe Historie, einmal mit voellig anderem
    Kursverlauf *danach*. Der gemeinsame Zeitraum muss bitidentisch sein.
    Wuerde irgendwo ueber den ganzen Datensatz statt ueber die Historie bis
    `now` gerechnet, faellt das hier auf.

    **Die Liste hat lange nur `trend` und `meanrev` enthalten** -- also
    ausgerechnet die beiden Strategien, die das Projekt als unbrauchbar
    verworfen hat, waehrend `macross` (die einzige, die live auf einem
    Paper-Konto laeuft) und `elliott` (Grundlage des Timeframe-Vergleichs
    in ADR-047) ungeprueft blieben. Der wichtigste Test des Projekts deckte
    die wichtigsten Strategien nicht ab (ADR-053). Wer hier eine Strategie
    hinzufuegt, kostet Sekunden; wer eine weglaesst, verliert die Zusage
    stillschweigend.
    """
    n_common = 400
    rng = np.random.default_rng(7)
    common = 100 * np.cumprod(1 + rng.normal(0.0002, 0.01, n_common))

    # Zwei radikal verschiedene Fortsetzungen: Crash und Rally.
    crash = common[-1] * np.cumprod(1 + np.full(200, -0.02))
    rally = common[-1] * np.cumprod(1 + np.full(200, +0.02))

    results = []
    for tail in (crash, rally):
        prices = np.concatenate([common, tail])
        strategy = get(strategy_name)(["BTC/USD"], "1h")
        result = run_backtest(strategy, {"BTC/USD": make_bars(len(prices), prices=prices)})
        results.append(result.equity.head(n_common)["equity"].to_numpy())

    np.testing.assert_array_equal(
        results[0],
        results[1],
        err_msg=f"{strategy_name}: Zukunftsdaten haben die Vergangenheit veraendert -- Lookahead!",
    )


def test_execution_happens_after_the_signal():
    """Fills liegen nie auf dem Bar, der das Signal erzeugt hat.

    Ein Fill auf dem Signal-Close waere Handel zu einem Preis, den man zum
    Entscheidungszeitpunkt nicht kannte -- Lookahead in seiner teuersten Form.
    """
    prices = np.concatenate([np.full(60, 100.0), np.linspace(100, 130, 40)])
    strategy = get("trend")(["BTC/USD"], "1h", entry_lookback=20, exit_lookback=10, atr_period=10)
    result = run_backtest(strategy, {"BTC/USD": make_bars(len(prices), prices=prices)})

    assert result.fills, "Test braucht mindestens einen Fill"
    weights = result.equity.set_index("ts")["weight_BTC/USD"]
    first_signal_ts = weights[weights != 0].index[0]
    assert result.fills[0].ts >= first_signal_ts, (
        "Fill liegt vor dem Signal, das ihn ausgeloest hat"
    )
