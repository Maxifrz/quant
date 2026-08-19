"""Datenschicht: Store, Resampling, Integritaetspruefung."""

from __future__ import annotations

import pandas as pd
import pytest

from qt.data.ingest import resample
from qt.data.integrity import check
from qt.data.store import read_bars, to_bars, write_bars


def _frame(n: int, freq: str = "1h") -> pd.DataFrame:
    ts = pd.date_range("2020-01-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1.0] * n,
        }
    )


def test_write_is_idempotent(tmp_path):
    """Zweimal denselben Zeitraum schreiben darf nichts verdoppeln.

    Ein erneuter Pull mit ueberlappendem Fenster ist der Normalfall, kein
    Sonderfall -- er muss unschaedlich sein.
    """
    df = _frame(50)
    write_bars("BTC/USD", "1h", df, tmp_path)
    write_bars("BTC/USD", "1h", df, tmp_path)

    stored = read_bars("BTC/USD", "1h", data_dir=tmp_path)
    assert len(stored) == 50
    assert stored["ts"].is_monotonic_increasing
    assert not stored["ts"].duplicated().any()


def test_overlapping_writes_merge(tmp_path):
    write_bars("BTC/USD", "1h", _frame(30), tmp_path)
    later = _frame(50).iloc[20:]
    write_bars("BTC/USD", "1h", later, tmp_path)

    assert len(read_bars("BTC/USD", "1h", data_dir=tmp_path)) == 50


def test_read_missing_symbol_explains_the_fix(tmp_path):
    with pytest.raises(FileNotFoundError, match="qt data pull"):
        read_bars("DOGE/USD", "1h", data_dir=tmp_path)


def test_resample_drops_incomplete_bucket():
    """Ein halb gefuellter 4h-Bar haette ein High, das die Zukunft kennt."""
    resampled = resample(_frame(9), "4h")

    assert len(resampled) == 2, "unvollstaendiger dritter Bucket muss wegfallen"
    assert resampled["open"].iloc[0] == 100.0
    assert resampled["close"].iloc[0] == 103.5
    assert resampled["high"].iloc[0] == 104.0
    assert resampled["volume"].iloc[0] == 4.0


def test_integrity_finds_gaps():
    df = pd.concat([_frame(10), _frame(20).iloc[15:]], ignore_index=True)
    report = check("BTC/USD", "1h", df)

    assert len(report.gaps) == 1
    assert report.gaps[0].missing_bars == 5
    assert report.coverage < 1.0
    assert report.ok, "eine Luecke allein macht die Daten nicht kaputt"


def test_integrity_flags_broken_bars():
    df = _frame(10)
    df.loc[5, "high"] = -1.0
    report = check("BTC/USD", "1h", df)

    assert report.ohlc_violations > 0
    assert not report.ok


def test_to_bars_preserves_close_time():
    bars = to_bars("BTC/USD", "1h", _frame(3))
    assert len(bars) == 3
    assert (bars[0].close_ts - bars[0].ts).total_seconds() == 3600
    assert bars[0].close_ts == bars[1].ts
