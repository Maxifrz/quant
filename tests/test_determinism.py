"""Determinismus.

Ein Backtest, der bei zwei Laeufen unterschiedliche Zahlen liefert, ist als
Entscheidungsgrundlage wertlos -- und in Phase 3 waere er es doppelt, weil
LLM-Allokationen dann nicht mehr reproduzierbar bewertbar waeren.
"""

from __future__ import annotations

import numpy as np
import pytest

from qt.backtest.engine import run_backtest
from qt.core.events import merge_bar_streams
from qt.strategy.registry import get, load_library
from tests.conftest import make_bars

load_library()


@pytest.mark.parametrize("strategy_name", ["trend", "meanrev"])
def test_two_runs_are_bit_identical(strategy_name):
    bars = {"BTC/USD": make_bars(600, seed=11)}
    runs = [
        run_backtest(get(strategy_name)(["BTC/USD"], "1h"), bars).equity["equity"].to_numpy()
        for _ in range(2)
    ]
    np.testing.assert_array_equal(runs[0], runs[1])


def test_strategy_instances_do_not_share_state():
    """Zwei Instanzen derselben Strategie duerfen sich nicht beeinflussen.

    Zustand auf der Klasse statt auf der Instanz ist ein klassischer Fehler
    und faellt sonst erst auf, wenn Phase 2 mehrere Strategien parallel
    laufen laesst.
    """
    bars = {"BTC/USD": make_bars(600, seed=5)}
    first = run_backtest(get("trend")(["BTC/USD"], "1h"), bars)

    warm_up_other = get("trend")(["BTC/USD"], "1h")
    run_backtest(warm_up_other, {"BTC/USD": make_bars(600, seed=99)})

    second = run_backtest(get("trend")(["BTC/USD"], "1h"), bars)
    np.testing.assert_array_equal(
        first.equity["equity"].to_numpy(), second.equity["equity"].to_numpy()
    )


def test_event_ordering_is_stable_across_symbols():
    """Gleichzeitig schliessende Bars muessen deterministisch sortiert sein.

    Ohne festen Tiebreak haengt die Reihenfolge von der Dict-Iteration ab und
    der Backtest waere nicht reproduzierbar.
    """
    a = make_bars(50, symbol="BTC/USD", seed=1)
    b = make_bars(50, symbol="ETH/USD", seed=2)

    forward = [(e.ts, e.symbol) for e in merge_bar_streams([a, b])]
    reversed_input = [(e.ts, e.symbol) for e in merge_bar_streams([b, a])]

    assert forward == reversed_input
