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
    too_fine: int = 0
    # "24-7" (Krypto) oder "sessions" (Boerse mit Handelskalender).
    calendar: str = "24-7"

    @property
    def missing_bars(self) -> int:
        return sum(g.missing_bars for g in self.gaps)

    @property
    def coverage(self) -> float:
        """Anteil vorhandener Bars am erwarteten Zeitraum.

        **Nur im 24/7-Modus definiert.** Wieviele Bars eine Boersenreihe haben
        *muesste*, weiss nur ein echter Handelskalender: Feiertage sind je
        Boerse verschieden, verschieben sich jaehrlich, und halbe Handelstage
        gibt es auch noch. Ohne diesen Kalender waere jede Abdeckungszahl
        geraten -- und eine geratene Zahl in einer Integritaetspruefung ist
        schlimmer als keine (ADR-055). Deshalb `nan` statt einer Erfindung.
        """
        if self.calendar != "24-7":
            return float("nan")
        total = self.n_bars + self.missing_bars
        return self.n_bars / total if total else 0.0

    @property
    def ok(self) -> bool:
        """Sauber genug fuer einen Backtest.

        Luecken allein sind kein K.o. -- Exchanges haben Ausfaelle. Kaputte
        Bars sind einer: sie erzeugen falsche Signale statt fehlender.

        `too_fine` ebenfalls: eine Reihe, deren Bars enger stehen als ihr
        Timeframe, ist keine Reihe dieses Timeframes.
        """
        return (
            self.duplicates == 0
            and self.non_monotonic == 0
            and self.ohlc_violations == 0
            and self.non_positive == 0
            and self.too_fine == 0
        )

    def summary(self) -> str:
        if self.n_bars == 0:
            return f"{self.symbol:>10} {self.timeframe:>3}  LEER"
        flag = "ok " if self.ok else "!! "
        abdeckung = (
            f"{self.coverage:6.2%}" if self.calendar == "24-7" else "     --"
        )
        zeile = (
            f"{flag}{self.symbol:>10} {self.timeframe:>3}  "
            f"{self.n_bars:>7,} Bars  "
            f"{self.start:%Y-%m-%d} .. {self.end:%Y-%m-%d}  "
            f"Abdeckung {abdeckung}  "
            f"Luecken {len(self.gaps):>3} ({self.missing_bars:,} Bars)"
        )
        if self.too_fine:
            zeile += f"  !! {self.too_fine:,} Bars enger als {self.timeframe}"
        return zeile


def check(
    symbol: str, timeframe: str, df: pd.DataFrame, calendar: str = "24-7"
) -> IntegrityReport:
    """Einen Datensatz auf die fuenf Fehlerklassen pruefen, die real vorkommen.

    Die fuenfte -- `too_fine`, Bars enger als ihr Timeframe -- kam spaet dazu
    und aus einem konkreten Anlass: `find_gaps` sucht ausschliesslich nach
    Abstaenden, die zu **gross** sind. Eine 2d-Datei, in die versehentlich
    eine zweite, um einen Tag verschobene Reihe gemischt wurde, hatte damit
    lauter 1-Tages-Abstaende und bekam ein makelloses Zeugnis: "ok, 100.00%
    Abdeckung, 0 Luecken" (ADR-053). Ein Pruefer, der nur in eine Richtung
    schaut, uebersieht die andere zuverlaessig.
    """
    if df.empty:
        return IntegrityReport(symbol, timeframe, 0, None, None, calendar=calendar)

    ts = df["ts"]
    report = IntegrityReport(
        symbol=symbol,
        timeframe=timeframe,
        n_bars=len(df),
        start=ts.iloc[0],
        end=ts.iloc[-1],
        duplicates=int(ts.duplicated().sum()),
        non_monotonic=int((ts.diff().dropna() <= pd.Timedelta(0)).sum()),
        calendar=calendar,
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

    report.gaps = find_gaps(ts, timeframe, calendar=calendar)
    report.too_fine = count_too_fine(ts, timeframe)
    return report


def count_too_fine(ts: pd.Series, timeframe: str) -> int:
    """Bars, die enger auf ihren Vorgaenger folgen als der Timeframe erlaubt.

    Duplikate (Abstand exakt null) zaehlen hier nicht mit -- die haben ihre
    eigene Kennzahl und ihre eigene Ursache.
    """
    step = pd.Timedelta(seconds=timeframe_seconds(timeframe))
    deltas = ts.diff().dropna()
    return int(((deltas > pd.Timedelta(0)) & (deltas < step)).sum())


# Groesster Abstand, der in einer Boersenreihe noch normal ist, in
# Timeframe-Schritten. Ein langes Wochenende sind drei Tage, ein Feiertag
# davor oder danach macht vier. Bewusst eine **Heuristik und als solche
# benannt**: der exakte Wert braeuchte einen echten Handelskalender je Boerse,
# und den hat dieses Projekt nicht. Sie faengt, was sie fangen soll -- ein
# mehrtaegiges Loch im Abzug -- und laesst Wochenenden durch (ADR-055).
SESSION_GAP_FACTOR = 4


def find_gaps(ts: pd.Series, timeframe: str, calendar: str = "24-7") -> list[Gap]:
    """Fehlende Bars finden.

    Krypto handelt 24/7 -- dort ist jeder Abstand groesser als ein Timeframe
    eine echte Luecke und keine Handelspause.

    Bei `calendar="sessions"` gilt das nicht: eine Aktienreihe hat an jedem
    Wochenende zwangslaeufig einen Abstand von drei Tagen. Ohne diese
    Unterscheidung meldete `qt data report` fuer jeden ETF rund 400 "Luecken"
    im Jahr, und die Meldung waere damit wertlos -- man ueberliest sie, und
    genau dann faellt das echte Loch nicht mehr auf.
    """
    step = pd.Timedelta(seconds=timeframe_seconds(timeframe))
    if calendar != "24-7":
        step = step * SESSION_GAP_FACTOR
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
