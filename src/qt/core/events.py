"""Events, die durch die Engine laufen.

Aktuell gibt es nur BarEvent. Der Typ existiert trotzdem als eigene Ebene,
weil Phase 6 Fill- und Reconcile-Events dazustellt und der Engine-Loop dann
nicht umgebaut werden muss.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from qt.core.types import Bar


@dataclass(frozen=True, slots=True)
class BarEvent:
    """Ein Bar hat geschlossen und ist ab jetzt bekannt.

    `ts` ist die **Close-Zeit** des Bars, nicht die Open-Zeit: das ist der
    Zeitpunkt, zu dem die Information tatsaechlich vorliegt.
    """

    ts: datetime
    bar: Bar

    @property
    def symbol(self) -> str:
        return self.bar.symbol

    @property
    def timeframe(self) -> str:
        return self.bar.timeframe


def merge_bar_streams(streams: list[list[Bar]]) -> list[BarEvent]:
    """Bars mehrerer Symbole/Timeframes zu einem chronologischen Strom mischen.

    Sortiert nach Close-Zeit, denn danach richtet sich, wann eine Information
    verfuegbar wird. Ein 1d-Bar und ein 4h-Bar, die zur selben Zeit schliessen,
    werden deterministisch nach (Symbol, Timeframe) gebrochen -- ohne festen
    Tiebreak waere der Backtest nicht reproduzierbar.
    """
    events = [BarEvent(ts=bar.close_ts, bar=bar) for stream in streams for bar in stream]
    events.sort(key=lambda e: (e.ts, e.bar.symbol, e.bar.timeframe))
    return events
