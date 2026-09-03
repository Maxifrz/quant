"""Point-in-Time-Feature-Store.

Die zentrale Regel des Systems: **eine Strategie sieht nie Daten, die zu
ihrem Entscheidungszeitpunkt noch nicht existierten.**

Lookahead-Bias ist der haeufigste Grund, warum ein Backtest grossartig
aussieht und live nichts uebrig bleibt. Und er ist heimtueckisch: er
entsteht durch eine Zeile wie `df['close'].rolling(20).mean()`, die
versehentlich auf dem ganzen DataFrame statt auf der Historie bis `now`
rechnet -- kein Absturz, keine Warnung, nur zu gute Zahlen.

Deshalb ist die Regel hier kein Review-Thema, sondern ein Wachhund: der
Store haelt fuer jedes Symbol nur die bereits geschlossenen Bars und wirft
`LookaheadError`, wenn jemand darueber hinaus greift.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import numpy as np

from qt.core.clock import Clock
from qt.core.types import Bar


class LookaheadError(RuntimeError):
    """Es wurde auf Daten jenseits der aktuellen Uhrzeit zugegriffen."""


class BarWindow:
    """Rollierendes Fenster geschlossener Bars eines Symbol/Timeframe-Paars.

    Haelt nur `maxlen` Bars: ein mehrjaehriger Minutendatensatz passt sonst
    nicht sinnvoll in den Speicher, und keine Strategie braucht die volle
    Historie in jedem Bar.
    """

    __slots__ = ("symbol", "timeframe", "maxlen", "_ts", "_o", "_h", "_l", "_c", "_v")

    def __init__(self, symbol: str, timeframe: str, maxlen: int = 1000) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.maxlen = maxlen
        self._ts: list[datetime] = []
        self._o: list[float] = []
        self._h: list[float] = []
        self._l: list[float] = []
        self._c: list[float] = []
        self._v: list[float] = []

    def append(self, bar: Bar) -> None:
        self._ts.append(bar.ts)
        self._o.append(bar.open)
        self._h.append(bar.high)
        self._l.append(bar.low)
        self._c.append(bar.close)
        self._v.append(bar.volume)
        if len(self._ts) > self.maxlen:
            for buf in (self._ts, self._o, self._h, self._l, self._c, self._v):
                del buf[0]

    def __len__(self) -> int:
        return len(self._ts)

    @property
    def timestamps(self) -> list[datetime]:
        return list(self._ts)

    def opens(self) -> np.ndarray:
        return np.asarray(self._o, dtype=float)

    def highs(self) -> np.ndarray:
        return np.asarray(self._h, dtype=float)

    def lows(self) -> np.ndarray:
        return np.asarray(self._l, dtype=float)

    def closes(self) -> np.ndarray:
        return np.asarray(self._c, dtype=float)

    def volumes(self) -> np.ndarray:
        return np.asarray(self._v, dtype=float)

    @property
    def last_close(self) -> float:
        return self._c[-1]


class FeatureStore:
    """Verwaltet die Fenster aller Symbole und bewacht die Zeitgrenze.

    Bars werden ueber `on_bar` eingespeist -- ausschliesslich von der Engine,
    ausschliesslich nachdem der Bar geschlossen hat. Strategien lesen nur.
    """

    def __init__(self, clock: Clock, maxlen: int = 1000) -> None:
        self._clock = clock
        self._maxlen = maxlen
        self._windows: dict[tuple[str, str], BarWindow] = {}
        self._latest_close: dict[tuple[str, str], datetime] = {}
        self._counts: dict[str, int] = defaultdict(int)

    def on_bar(self, bar: Bar) -> None:
        """Einen geschlossenen Bar aufnehmen.

        Wirft, wenn der Bar noch gar nicht geschlossen haben kann. Das faengt
        einen falsch sortierten Event-Strom sofort ab, statt ihn stillschweigend
        in zu gute Backtest-Zahlen zu verwandeln.
        """
        if bar.close_ts > self._clock.now:
            raise LookaheadError(
                f"Bar {bar.symbol} {bar.timeframe} schliesst "
                f"{bar.close_ts.isoformat()}, Uhr steht auf "
                f"{self._clock.now.isoformat()}. Der Bar ist noch nicht bekannt."
            )
        key = (bar.symbol, bar.timeframe)
        window = self._windows.get(key)
        if window is None:
            window = self._windows[key] = BarWindow(bar.symbol, bar.timeframe, self._maxlen)
        window.append(bar)
        self._latest_close[key] = bar.close_ts
        self._counts[bar.symbol] += 1

    def window(self, symbol: str, timeframe: str) -> BarWindow:
        """Fenster geschlossener Bars.

        Wirft `LookaheadError`, wenn der neueste enthaltene Bar in der Zukunft
        liegt -- das kann nur passieren, wenn die Uhr manipuliert wurde.
        """
        key = (symbol, timeframe)
        window = self._windows.get(key)
        if window is None:
            return BarWindow(symbol, timeframe, self._maxlen)
        latest = self._latest_close.get(key)
        if latest is not None and latest > self._clock.now:
            raise LookaheadError(
                f"Fenster {symbol} {timeframe} enthaelt einen Bar mit Close "
                f"{latest.isoformat()} jenseits der Uhr {self._clock.now.isoformat()}."
            )
        return window

    def has(self, symbol: str, timeframe: str, min_bars: int = 1) -> bool:
        """Genug Historie vorhanden, um sinnvoll zu rechnen?"""
        return len(self.window(symbol, timeframe)) >= min_bars

    @property
    def now(self) -> datetime:
        return self._clock.now
