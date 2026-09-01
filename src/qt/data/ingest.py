"""OHLCV-Ingest via ccxt.

Zwei Eigenheiten der Umgebung, die hier geloest werden:

1. ccxt setzt `session.trust_env = False` und ignoriert damit CA-Bundle und
   Proxy aus der Umgebung. Wir schalten es ein -- das *aktiviert* die
   Standard-Vertrauenskonfiguration, es schwaecht keine TLS-Pruefung ab.
2. Coinbase kennt kein natives 4h (nur 1m/5m/15m/1h/6h/1d). Abgeleitete
   Timeframes werden lokal aus dem naechstkleineren nativen resampled
   (siehe ADR-007).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import ccxt
import pandas as pd

from qt.core.config import DataConfig
from qt.core.types import timeframe_seconds
from qt.data.store import COLUMNS, write_bars

# Timeframes, die keine Exchange nativ liefert -> lokal aus der Quelle bauen.
DERIVED: dict[str, str] = {"4h": "1h"}

# Coinbase liefert max. 300 Bars pro Request.
CHUNK = 300


def make_exchange(cfg: DataConfig | None = None) -> ccxt.Exchange:
    """ccxt-Exchange, konfiguriert fuer diese Umgebung."""
    cfg = cfg or DataConfig()
    exchange = getattr(ccxt, cfg.exchange)({"enableRateLimit": True})
    # Laesst requests das CA-Bundle und den Proxy aus der Umgebung lesen.
    exchange.session.trust_env = True
    return exchange


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since: datetime,
    until: datetime | None = None,
    rate_limit_ms: int = 350,
) -> pd.DataFrame:
    """Nativen Timeframe paginiert abholen.

    Laeuft vorwaerts von `since`. Bricht ab, wenn die Exchange nichts Neues
    mehr liefert -- ohne diesen Abbruch dreht die Schleife am Ende der
    Historie endlos.
    """
    if timeframe not in exchange.timeframes:
        raise ValueError(
            f"{exchange.id} kennt {timeframe!r} nicht. "
            f"Nativ: {sorted(exchange.timeframes)}"
        )

    step_ms = timeframe_seconds(timeframe) * 1000
    cursor = int(since.timestamp() * 1000)
    end_ms = int((until or datetime.now(timezone.utc)).timestamp() * 1000)

    rows: list[list] = []
    while cursor < end_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=CHUNK)
        if not batch:
            break

        # Nur echt neue Bars behalten: manche Exchanges liefern den
        # `since`-Bar erneut mit, was sonst zu einer Endlosschleife fuehrt.
        batch = [b for b in batch if b[0] >= cursor]
        if not batch:
            break

        rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts < cursor:
            break
        cursor = last_ts + step_ms
        time.sleep(rate_limit_ms / 1000)

    if not rows:
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop_duplicates(subset="ts", keep="last").sort_values("ts", ignore_index=True)
    return df[df["ts"] <= pd.Timestamp(end_ms, unit="ms", tz="UTC")].reset_index(drop=True)


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Feinere Bars zu groeberen aggregieren.

    Der letzte Bucket wird verworfen, wenn er unvollstaendig ist: ein halb
    gefuellter 4h-Bar aus zwei 1h-Bars haette ein High, das die restlichen
    zwei Stunden noch gar nicht kennen -- also exakt der Lookahead-Fehler,
    den das ganze System vermeiden soll.
    """
    if df.empty:
        return df

    seconds = timeframe_seconds(timeframe)
    indexed = df.set_index("ts")
    agg = indexed.resample(f"{seconds}s", origin="epoch", label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    agg = agg.dropna(subset=["open"])

    # Bucket-Vollstaendigkeit ueber die Anzahl der Quell-Bars pruefen.
    counts = indexed["close"].resample(
        f"{seconds}s", origin="epoch", label="left", closed="left"
    ).count()
    source_seconds = _infer_source_seconds(df)
    expected = seconds // source_seconds
    agg = agg[counts.reindex(agg.index).fillna(0) >= expected]

    return agg.reset_index()[COLUMNS]


def _infer_source_seconds(df: pd.DataFrame) -> int:
    """Timeframe der Quelldaten aus dem haeufigsten Zeitabstand ableiten."""
    deltas = df["ts"].diff().dropna()
    if deltas.empty:
        raise ValueError("Zu wenige Bars, um den Quell-Timeframe zu bestimmen.")
    return int(deltas.mode().iloc[0].total_seconds())


def pull(
    symbols: list[str],
    timeframes: list[str],
    since: datetime,
    until: datetime | None = None,
    cfg: DataConfig | None = None,
) -> dict[tuple[str, str], int]:
    """Alle Symbol/Timeframe-Kombinationen ziehen und in den Store schreiben.

    Abgeleitete Timeframes werden aus ihrer Quelle gebaut; die Quelle wird
    dafuer bei Bedarf mit gezogen, auch wenn sie nicht angefordert war.
    """
    cfg = cfg or DataConfig()
    exchange = make_exchange(cfg)
    exchange.load_markets()

    # Quellen fuer abgeleitete Timeframes mit einplanen.
    needed = set(timeframes)
    for tf in timeframes:
        if tf in DERIVED:
            needed.add(DERIVED[tf])

    written: dict[tuple[str, str], int] = {}
    native_cache: dict[tuple[str, str], pd.DataFrame] = {}

    for symbol in symbols:
        if symbol not in exchange.markets:
            raise ValueError(
                f"{symbol!r} gibt es auf {exchange.id} nicht. "
                f"Beispiele: {sorted(exchange.markets)[:5]}"
            )

        for tf in sorted(needed - set(DERIVED)):
            df = fetch_ohlcv(
                exchange, symbol, tf, since, until, rate_limit_ms=cfg.rate_limit_ms
            )
            native_cache[(symbol, tf)] = df
            if tf in timeframes and not df.empty:
                write_bars(symbol, tf, df, cfg.data_dir)
                written[(symbol, tf)] = len(df)

        for tf in sorted(t for t in timeframes if t in DERIVED):
            source = native_cache.get((symbol, DERIVED[tf]))
            if source is None or source.empty:
                continue
            df = resample(source, tf)
            if not df.empty:
                write_bars(symbol, tf, df, cfg.data_dir)
                written[(symbol, tf)] = len(df)

    return written


def resample_store(
    symbols: list[str],
    source: str,
    targets: list[str],
    data_dir=None,
) -> dict[tuple[str, str], int]:
    """Groebere Bars aus gespeicherten feineren ableiten und zurueckschreiben.

    Duenner Aufsatz auf `resample` -- die Aggregation und die Regel zum
    unvollstaendigen letzten Bucket stehen dort und bleiben dort. Eine zweite
    Implementierung waere eine zweite Stelle, an der der Lookahead-Schutz
    kaputtgehen kann.

    Gedacht fuer Timeframes oberhalb von `1d`: `2d` und `3d` liefert keine
    Boerse, und eine Wochenkerze der Boerse begaenne moeglicherweise an einem
    anderen Wochentag als unsere -- ein stiller Unterschied genau in dem
    Vergleich, fuer den die Daten da sind.
    """
    from qt.data.store import read_bars, write_bars

    source_seconds = timeframe_seconds(source)
    written: dict[tuple[str, str], int] = {}

    for symbol in symbols:
        raw = read_bars(symbol, source, data_dir=data_dir)
        for target in targets:
            target_seconds = timeframe_seconds(target)
            if target_seconds <= source_seconds or target_seconds % source_seconds:
                raise ValueError(
                    f"{target} ist kein echtes ganzzahliges Vielfaches von "
                    f"{source} -- ein Bucket laege dann nicht auf Bar-Grenzen."
                )
            coarse = resample(raw, target)
            write_bars(symbol, target, coarse, data_dir=data_dir)
            written[(symbol, target)] = len(coarse)

    return written
