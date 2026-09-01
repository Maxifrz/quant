"""Parquet-Store fuer OHLCV-Daten.

Layout: data/ohlcv/{symbol}/{timeframe}.parquet
Symbol-Slashes werden zu '-' (BTC/USD -> BTC-USD), damit es Verzeichnisnamen
sein koennen.

Bewusst quellenagnostisch: ein Exchange-Wechsel beruehrt nur ingest.py.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from qt.core.config import DATA_DIR
from qt.core.types import Bar, timeframe_seconds

COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def symbol_to_path(symbol: str) -> str:
    return symbol.replace("/", "-")


def path_to_symbol(name: str) -> str:
    return name.replace("-", "/")


def parquet_path(symbol: str, timeframe: str, data_dir: Path | None = None) -> Path:
    root = data_dir or DATA_DIR
    return root / "ohlcv" / symbol_to_path(symbol) / f"{timeframe}.parquet"


def write_bars(
    symbol: str, timeframe: str, df: pd.DataFrame, data_dir: Path | None = None
) -> Path:
    """Bars schreiben und dabei mit vorhandenen zusammenfuehren.

    Bestehende Daten werden nicht ueberschrieben, sondern vereinigt und
    dedupliziert -- ein zweiter Pull mit ueberlappendem Zeitraum ist damit
    unschaedlich und idempotent.
    """
    path = parquet_path(symbol, timeframe, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    df = _normalise(df)
    if path.exists():
        df = _normalise(pd.concat([pd.read_parquet(path), df], ignore_index=True))

    df.to_parquet(path, index=False)
    return path


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """UTC-Zeitstempel, sortiert, dedupliziert, feste Spaltenreihenfolge."""
    df = df.loc[:, COLUMNS].copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.drop_duplicates(subset="ts", keep="last")
    df = df.sort_values("ts", ignore_index=True)
    for col in COLUMNS[1:]:
        df[col] = df[col].astype("float64")
    return df


def read_bars(
    symbol: str,
    timeframe: str,
    start: pd.Timestamp | str | None = None,
    end: pd.Timestamp | str | None = None,
    data_dir: Path | None = None,
) -> pd.DataFrame:
    """Bars lesen, optional auf ein Zeitfenster eingegrenzt.

    `start`/`end` beziehen sich auf die **Open-Zeit** des Bars und sind
    beidseitig inklusive.
    """
    path = parquet_path(symbol, timeframe, data_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"Keine Daten fuer {symbol} {timeframe} unter {path}. "
            f"Erst ziehen: qt data pull --symbols '{symbol}' --tf {timeframe}"
        )
    df = _normalise(pd.read_parquet(path))
    if start is not None:
        df = df[df["ts"] >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df["ts"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


def to_bars(symbol: str, timeframe: str, df: pd.DataFrame) -> list[Bar]:
    """DataFrame in Bar-Objekte umwandeln, wie die Engine sie erwartet."""
    return [
        Bar(
            symbol=symbol,
            timeframe=timeframe,
            ts=row.ts.to_pydatetime(),
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
        )
        for row in df.itertuples(index=False)
    ]


def available(data_dir: Path | None = None) -> list[tuple[str, str]]:
    """Alle vorhandenen (Symbol, Timeframe)-Paare im Store."""
    root = (data_dir or DATA_DIR) / "ohlcv"
    if not root.exists():
        return []
    out = [
        (path_to_symbol(sym_dir.name), pq.stem)
        for sym_dir in sorted(root.iterdir())
        if sym_dir.is_dir()
        for pq in sorted(sym_dir.glob("*.parquet"))
    ]
    return out


def expected_bar_count(
    start: pd.Timestamp, end: pd.Timestamp, timeframe: str
) -> int:
    """Wieviele Bars zwischen zwei Zeitpunkten liegen muessten.

    Krypto handelt 24/7, deshalb ohne Handelskalender: jede Abweichung ist
    eine echte Luecke und keine Wochenendpause.
    """
    span = (end - start).total_seconds()
    return int(span // timeframe_seconds(timeframe)) + 1
