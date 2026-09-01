"""Donchian-Breakout mit ATR-Stop.

Die aelteste bekannte systematische Trendstrategie (Turtle Traders, 1983).
Sie steht hier nicht, weil sie profitabel waere, sondern weil ihr Verhalten
gut verstanden ist: sie muss in langen Trends verdienen und in Seitwaerts-
maerkten durch wiederholte Fehlausbrueche verlieren. Zeigt der Backtest
etwas anderes, ist die Engine kaputt -- nicht der Markt interessant.
"""

from __future__ import annotations

import math

from qt.features import ta
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy
from qt.strategy.registry import register


@register
class DonchianTrend(Strategy):
    """Long ueber dem n-Bar-Hoch, short unter dem n-Bar-Tief, ATR-Stop dazwischen."""

    name = "trend"

    def __init__(
        self,
        symbols: list[str],
        timeframe: str,
        entry_lookback: int = 55,
        exit_lookback: int = 20,
        atr_period: int = 20,
        atr_stop: float = 2.0,
        allow_short: bool = True,
    ) -> None:
        super().__init__(
            symbols,
            timeframe,
            entry_lookback=entry_lookback,
            exit_lookback=exit_lookback,
            atr_period=atr_period,
            atr_stop=atr_stop,
            allow_short=allow_short,
        )

    @property
    def warmup_bars(self) -> int:
        return max(self.params["entry_lookback"], self.params["atr_period"]) + 2

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        window = store.window(symbol, self.timeframe)
        if len(window) < self.warmup_bars:
            return math.nan

        highs, lows, closes = window.highs(), window.lows(), window.closes()
        close = closes[-1]

        entry_high, entry_low = ta.donchian(highs, lows, self.params["entry_lookback"])
        exit_high, exit_low = ta.donchian(highs, lows, self.params["exit_lookback"])
        atr = ta.atr(highs, lows, closes, self.params["atr_period"])

        if not all(math.isfinite(v) for v in (entry_high, entry_low, exit_high, exit_low, atr)):
            return math.nan

        state = self._state.setdefault(symbol, {"weight": 0.0, "stop": math.nan})
        weight, stop = state["weight"], state["stop"]

        # Stop zuerst pruefen: er hat Vorrang vor jedem Einstiegssignal.
        if weight > 0 and math.isfinite(stop) and close < stop:
            weight, stop = 0.0, math.nan
        elif weight < 0 and math.isfinite(stop) and close > stop:
            weight, stop = 0.0, math.nan

        if weight == 0.0:
            if close > entry_high:
                weight, stop = 1.0, close - self.params["atr_stop"] * atr
            elif self.params["allow_short"] and close < entry_low:
                weight, stop = -1.0, close + self.params["atr_stop"] * atr
        elif weight > 0:
            if close < exit_low:
                weight, stop = 0.0, math.nan
            else:
                # Trailing: der Stop wandert nur mit, nie zurueck.
                stop = max(stop, close - self.params["atr_stop"] * atr)
        else:
            if close > exit_high:
                weight, stop = 0.0, math.nan
            else:
                stop = min(stop, close + self.params["atr_stop"] * atr)

        state["weight"], state["stop"] = weight, stop
        return weight
