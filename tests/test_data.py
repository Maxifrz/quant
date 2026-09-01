"""Datenschicht: Store, Resampling, Integritaetspruefung."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qt.data.ingest import resample, resample_store
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


def test_resample_store_schreibt_groebere_bars_in_den_store(tmp_path):
    """`qt data resample` fuer Timeframes ueber 1d.

    Der Test existiert vor allem wegen der Namensfalle: es gab bereits ein
    `resample(df, timeframe)`, und eine zweite Funktion desselben Namens hat
    sie beim Anlegen dieses Features still ueberschrieben. Aufgefallen ist
    das nur, weil `test_resample_drops_incomplete_bucket` sofort brach.
    """
    from qt.data.ingest import resample_store

    write_bars("BTC/USD", "1d", _frame(14, freq="1D"), data_dir=tmp_path)
    geschrieben = resample_store(["BTC/USD"], "1d", ["2d"], data_dir=tmp_path)

    assert geschrieben[("BTC/USD", "2d")] == 7
    grob = read_bars("BTC/USD", "2d", data_dir=tmp_path)
    fein = read_bars("BTC/USD", "1d", data_dir=tmp_path)

    # Kantentreu: Open des groben Bars ist das erste Open, High das Maximum.
    assert grob["open"].iloc[0] == fein["open"].iloc[0]
    assert grob["high"].iloc[0] == fein["high"].iloc[:2].max()
    assert grob["volume"].iloc[0] == fein["volume"].iloc[:2].sum()


def test_resample_store_lehnt_krumme_vielfache_ab(tmp_path):
    # 3d aus 2d waere ein Bucket, der nicht auf Bar-Grenzen liegt -- die
    # Aggregation ergaebe stillschweigend falsche Hochs und Tiefs.
    from qt.data.ingest import resample_store

    write_bars("BTC/USD", "2d", _frame(10, freq="2D"), data_dir=tmp_path)
    with pytest.raises(ValueError, match="Vielfaches"):
        resample_store(["BTC/USD"], "2d", ["3d"], data_dir=tmp_path)


# ---------------------------------------------------------------------------
# Abgeleitete Reihen: ersetzen statt vereinigen (ADR-053)
# ---------------------------------------------------------------------------


def _tagesreihe(tmp_path, n=40, symbol="X/USD"):
    ts = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": np.arange(n, dtype=float) + 100.0,
            "high": np.arange(n, dtype=float) + 101.0,
            "low": np.arange(n, dtype=float) + 99.0,
            "close": np.arange(n, dtype=float) + 100.5,
            "volume": np.ones(n),
        }
    )
    write_bars(symbol, "1d", df, data_dir=tmp_path)
    return symbol


def test_resample_store_ist_idempotent(tmp_path):
    """Zweimal ableiten muss dasselbe ergeben wie einmal.

    Frueher vereinigte `write_bars` auch abgeleitete Reihen. Verschob sich das
    Bucket-Raster zwischen zwei Laeufen -- was real passiert ist --, entstanden
    zwei ineinandergelegte Reihen unter einem Namen: aus 1.396 2d-Bars wurden
    2.796 im Ein-Tages-Abstand.
    """
    symbol = _tagesreihe(tmp_path)
    resample_store([symbol], "1d", ["2d"], data_dir=tmp_path)
    einmal = read_bars(symbol, "2d", data_dir=tmp_path)
    resample_store([symbol], "1d", ["2d"], data_dir=tmp_path)
    zweimal = read_bars(symbol, "2d", data_dir=tmp_path)

    pd.testing.assert_frame_equal(einmal, zweimal)


def test_resample_store_raeumt_ein_altes_raster_weg(tmp_path):
    """Der Befehl muss reparieren koennen, nicht nur anhaengen."""
    symbol = _tagesreihe(tmp_path)
    korrekt = resample(read_bars(symbol, "1d", data_dir=tmp_path), "2d")

    # Ein um einen Tag verschobenes Raster, wie es real im Store lag.
    verschoben = korrekt.copy()
    verschoben["ts"] = verschoben["ts"] + pd.Timedelta(days=1)
    write_bars(symbol, "2d", verschoben, data_dir=tmp_path)
    assert len(read_bars(symbol, "2d", data_dir=tmp_path)) == len(korrekt)

    resample_store([symbol], "1d", ["2d"], data_dir=tmp_path)
    danach = read_bars(symbol, "2d", data_dir=tmp_path)

    assert len(danach) == len(korrekt), (
        "das alte Raster steht noch in der Datei -- vereinigt statt ersetzt"
    )
    assert set(danach["ts"]) == set(korrekt["ts"])


def test_write_bars_vereinigt_weiterhin_wenn_nicht_ersetzt_wird(tmp_path):
    """`replace` darf nur dort greifen, wo es ausdruecklich gesetzt ist.

    Gezogene Bars sind ein Zuwachs, keine Funktion -- ein zweiter Pull mit
    ueberlappendem Zeitraum muss unschaedlich bleiben.
    """
    symbol = _tagesreihe(tmp_path, n=10)
    weitere = pd.DataFrame(
        {
            "ts": pd.date_range("2020-01-11", periods=5, freq="D", tz="UTC"),
            "open": np.full(5, 1.0), "high": np.full(5, 2.0),
            "low": np.full(5, 0.5), "close": np.full(5, 1.5),
            "volume": np.ones(5),
        }
    )
    write_bars(symbol, "1d", weitere, data_dir=tmp_path)
    assert len(read_bars(symbol, "1d", data_dir=tmp_path)) == 15


def test_integritaet_erkennt_bars_die_enger_stehen_als_ihr_timeframe(tmp_path):
    """Die Luecke, die die Korruption unsichtbar gemacht hat.

    `find_gaps` sucht nur nach Abstaenden, die zu **gross** sind. Eine
    1d-Reihe unter dem Etikett `2d` bekam deshalb ein makelloses Zeugnis:
    "ok, 100.00% Abdeckung, 0 Luecken".
    """
    symbol = _tagesreihe(tmp_path, n=20)
    tages_reihe = read_bars(symbol, "1d", data_dir=tmp_path)

    falsch = check(symbol, "2d", tages_reihe)
    assert falsch.too_fine == 19
    assert not falsch.ok
    assert "enger als 2d" in falsch.summary()

    richtig = check(symbol, "1d", tages_reihe)
    assert richtig.too_fine == 0
    assert richtig.ok, "die Gegenprobe muss sauber durchgehen"


def test_duplikate_zaehlen_nicht_als_zu_feine_abstaende(tmp_path):
    """Sie haben ihre eigene Kennzahl und ihre eigene Ursache."""
    ts = pd.Timestamp("2020-01-01", tz="UTC")
    df = pd.DataFrame(
        {
            "ts": [ts, ts, ts + pd.Timedelta(days=1)],
            "open": [1.0, 1.0, 1.0], "high": [1.0, 1.0, 1.0],
            "low": [1.0, 1.0, 1.0], "close": [1.0, 1.0, 1.0],
            "volume": [1.0, 1.0, 1.0],
        }
    )
    report = check("X/USD", "1d", df)
    assert report.duplicates == 1
    assert report.too_fine == 0
