"""z-Score-Mean-Reversion mit Vol-Filter.

Das Gegenstueck zur Trendstrategie: sie muss dort verdienen, wo Trend
verliert, und umgekehrt. Genau deshalb sind es diese beiden -- ein
Strategiepaar mit gegenlaeufigem Regime-Profil ist der einfachste
sinnvolle Testfall fuer einen Allokator (Phase 3). Mit zwei Trend-
strategien haette der Allokator nichts zu entscheiden.

Der Vol-Filter setzt aus, wenn die Volatilitaet ungewoehnlich hoch ist:
Mean-Reversion in einem Vol-Spike bedeutet, in ein fallendes Messer zu
greifen.
"""

from __future__ import annotations

import math

from qt.core.types import bars_per_year
from qt.features import ta
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy
from qt.strategy.registry import register


@register
class ZScoreMeanReversion(Strategy):
    """Gegen die Abweichung vom gleitenden Mittel, solange die Vola normal ist."""

    name = "meanrev"

    def __init__(
        self,
        symbols: list[str],
        timeframe: str,
        lookback: int = 48,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
        vol_lookback: int = 96,
        vol_cap: float = 1.5,
        allow_short: bool = True,
    ) -> None:
        super().__init__(
            symbols,
            timeframe,
            lookback=lookback,
            entry_z=entry_z,
            exit_z=exit_z,
            vol_lookback=vol_lookback,
            vol_cap=vol_cap,
            allow_short=allow_short,
        )

    @property
    def warmup_bars(self) -> int:
        return max(self.params["lookback"], self.params["vol_lookback"]) * 2 + 2

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        window = store.window(symbol, self.timeframe)
        if len(window) < self.warmup_bars:
            return math.nan

        closes = window.closes()
        z = ta.zscore(closes, self.params["lookback"])
        if not math.isfinite(z):
            return math.nan

        state = self._state.setdefault(symbol, {"weight": 0.0})
        weight = state["weight"]

        # Vol-Filter: bei aussergewoehnlicher Vola nur noch schliessen,
        # nicht neu eroeffnen.
        py = bars_per_year(self.timeframe)
        vol_short = ta.realised_vol(closes, self.params["vol_lookback"], py)
        vol_long = ta.realised_vol(closes, self.params["vol_lookback"] * 2, py)
        calm = (
            math.isfinite(vol_short)
            and math.isfinite(vol_long)
            and vol_long > 0
            and vol_short / vol_long <= self.params["vol_cap"]
        )

        if weight != 0.0 and abs(z) <= self.params["exit_z"]:
            weight = 0.0
        elif weight == 0.0 and calm:
            if z <= -self.params["entry_z"]:
                weight = 1.0
            elif self.params["allow_short"] and z >= self.params["entry_z"]:
                weight = -1.0

        state["weight"] = weight
        return weight
