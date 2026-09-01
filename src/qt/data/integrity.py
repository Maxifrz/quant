"""Datenintegritaet.

Ein Backtest auf luecken- oder fehlerhaften Daten produziert Zahlen, die
plausibel aussehen und falsch sind. Diese Pruefungen laufen nach jedem Pull
und ihr Ergebnis gehoert in jeden Report -- nicht als optionaler Check,
sondern als Teil des Ergebnisses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from qt.core.types import timeframe_seconds


@dataclass(slots=True)
class Gap:
    start: pd.Timestamp
    end: pd.Timestamp
    missing_bars: int

    def __str__(self) -> str:
        return (
            f"{self.start:%Y-%m-%d %H:%M} -> {self.end:%Y-%m-%d %H:%M} "
            f"({self.missing_bars} Bars)"
        )


@dataclass(slots=True)
class IntegrityReport:
    symbol: str
    timeframe: str
    n_bars: int
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    gaps: list[Gap] = field(default_factory=list)
    duplicates: int = 0
    non_monotonic: int = 0
    ohlc_violations: int = 0
    non_positive: int = 0

    @property
    def missing_bars(self) -> int:
        return sum(g.missing_bars for g in self.gaps)

    @property
    def coverage(self) -> float:
        """Anteil vorhandener Bars am erwarteten Zeitraum."""
        total = self.n_bars + self.missing_bars
        return self.n_bars / total if total else 0.0

    @property
    def ok(self) -> bool:
        """Sauber genug fuer einen Backtest.

        Luecken allein sind kein K.o. -- Exchanges haben Ausfaelle. Kaputte
        Bars sind einer: sie erzeugen falsche Signale statt fehlender.
        """
        return (
            self.duplicates == 0
            and self.non_monotonic == 0
            and self.ohlc_violations == 0
            and self.non_positive == 0
        )

    def summary(self) -> str:
        if self.n_bars == 0:
            return f"{self.symbol:>10} {self.timeframe:>3}  LEER"
        flag = "ok " if self.ok else "!! "
        return (
            f"{flag}{self.symbol:>10} {self.timeframe:>3}  "
            f"{self.n_bars:>7,} Bars  "
            f"{self.start:%Y-%m-%d} .. {self.end:%Y-%m-%d}  "
            f"Abdeckung {self.coverage:6.2%}  "
            f"Luecken {len(self.gaps):>3} ({self.missing_bars:,} Bars)"
        )


def check(symbol: str, timeframe: str, df: pd.DataFrame) -> IntegrityReport:
    """Einen Datensatz auf die vier Fehlerklassen pruefen, die real vorkommen."""
    if df.empty:
        return IntegrityReport(symbol, timeframe, 0, None, None)

    ts = df["ts"]
    report = IntegrityReport(
        symbol=symbol,
        timeframe=timeframe,
        n_bars=len(df),
        start=ts.iloc[0],
        end=ts.iloc[-1],
        duplicates=int(ts.duplicated().sum()),
        non_monotonic=int((ts.diff().dropna() <= pd.Timedelta(0)).sum()),
    )

    # OHLC-Plausibilitaet: High muss alles dominieren, Low alles unterschreiten.
    body_max = df[["open", "close"]].max(axis=1)
    body_min = df[["open", "close"]].min(axis=1)
    report.ohlc_violations = int(
        ((df["high"] < body_max) | (df["low"] > body_min) | (df["high"] < df["low"])).sum()
    )

    price_cols = ["open", "high", "low", "close"]
    report.non_positive = int(
        ((df[price_cols] <= 0).any(axis=1) | (df["volume"] < 0)).sum()
    )

    report.gaps = find_gaps(ts, timeframe)
    return report


def find_gaps(ts: pd.Series, timeframe: str) -> list[Gap]:
    """Fehlende Bars finden.

    Krypto handelt 24/7 -- jeder Abstand groesser als ein Timeframe ist eine
    echte Luecke und keine Handelspause.
    """
    step = pd.Timedelta(seconds=timeframe_seconds(timeframe))
    deltas = ts.diff()
    gaps: list[Gap] = []
    for idx in deltas.index[deltas > step]:
        prev = ts.loc[idx] - deltas.loc[idx]
        gaps.append(
            Gap(
                start=prev,
                end=ts.loc[idx],
                missing_bars=int(deltas.loc[idx] / step) - 1,
            )
        )
    return gaps
