"""Die Uhr -- der Unterschied zwischen Backtest, Paper und Live.

Die Engine fragt die Clock nach `now` und danach, welcher Bar als naechstes
kommt. Ob dieses `now` aus einem Parquet-Replay oder aus der Systemzeit
stammt, ist der Engine egal. Genau diese Austauschbarkeit ist der Grund,
warum es nur einen Codepfad gibt (ADR-001).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone


class Clock(ABC):
    """Gemeinsames Interface aller Uhren."""

    @property
    @abstractmethod
    def now(self) -> datetime:
        """Aktueller Zeitpunkt. Nur bis hierher darf Wissen existieren."""

    @abstractmethod
    def advance(self, ts: datetime) -> None:
        """Uhr auf `ts` stellen."""


class BacktestClock(Clock):
    """Uhr fuer den Replay historischer Daten.

    Laeuft ausschliesslich vorwaerts. Ein Rueckwaertssprung waere fast immer
    ein Bug in der Event-Reihenfolge, deshalb ist er ein harter Fehler und
    kein stilles Zuruecksetzen.
    """

    def __init__(self, start: datetime) -> None:
        from qt.core.types import utc

        self._now = utc(start)

    @property
    def now(self) -> datetime:
        return self._now

    def advance(self, ts: datetime) -> None:
        from qt.core.types import utc

        ts = utc(ts)
        if ts < self._now:
            raise ValueError(
                f"Uhr laeuft rueckwaerts: {self._now.isoformat()} -> {ts.isoformat()}. "
                "Vermutlich sind die Bar-Events nicht chronologisch sortiert."
            )
        self._now = ts


class LiveClock(Clock):
    """Uhr fuer Paper- und Live-Betrieb: die Systemzeit in UTC.

    `advance` ist absichtlich ein No-op -- live kann man die Zeit nicht setzen.
    """

    @property
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def advance(self, ts: datetime) -> None:
        return None
