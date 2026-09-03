"""Gemeinsame Testbausteine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from qt.core.types import Bar, timeframe_seconds

START = datetime(2020, 1, 1, tzinfo=timezone.utc)


def make_bars(
    n: int,
    symbol: str = "BTC/USD",
    timeframe: str = "1h",
    prices: np.ndarray | None = None,
    seed: int = 0,
) -> list[Bar]:
    """Synthetische Bars mit reproduzierbarem Zufallspfad."""
    if prices is None:
        rng = np.random.default_rng(seed)
        prices = 100 * np.cumprod(1 + rng.normal(0.0002, 0.01, n))

    step = timedelta(seconds=timeframe_seconds(timeframe))
    bars = []
    for i, price in enumerate(prices):
        price = float(price)
        bars.append(
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                ts=START + i * step,
                open=price,
                high=price * 1.005,
                low=price * 0.995,
                close=price,
                volume=1.0,
            )
        )
    return bars


@pytest.fixture
def bars() -> list[Bar]:
    return make_bars(500)
