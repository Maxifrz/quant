"""Einzeltrades mit Aggressor-Seite -- die Rohdaten fuer Order Flow.

**Warum Kraken und nicht Coinbase.** Die Bar-Daten dieses Projekts kommen von
Coinbase (ADR-006). Fuer Einzeltrades geht das nicht: Coinbase ignoriert den
`since`-Parameter und liefert immer die juengsten Trades zurueck -- nachgemessen
mit `since = jetzt - 30 Tage`, zurueck kamen Trades von heute. Damit ist dort
keine Historie beschaffbar. Binance haette beides, ist aus dieser Umgebung aber
weiterhin 451-gesperrt. Kraken respektiert `since` und liefert die
Aggressor-Seite mit.

**Was daraus folgt und offen bleiben muss.** Der Order Flow stammt damit von
einer *anderen* Boerse als die Kursreihe und als die spaeteren Fills. Das ist
eine stille Annahme -- dass der Fluss der einen Boerse den Preis der anderen
erklaert -- und sie ist plausibel, aber unbewiesen. Sie gehoert gemessen und
nicht vorausgesetzt; siehe ADR-034.

**Datenmenge.** Eine Anfrage liefert 1000 Trades und deckt bei BTC/USD rund
74 Minuten ab. Ein Tag Historie kostet also etwa 19 Anfragen, 30 Tage rund
580. Das ist der Grund, warum hier paginiert und zwischengespeichert wird
statt bei jedem Lauf neu zu holen.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from qt.core.config import DataConfig
from qt.data.store import symbol_to_path

COLUMNS = ["ts", "price", "amount", "side"]

# Boerse fuer Einzeltrades, bewusst getrennt von `DataConfig.exchange`.
#
# Zwei Konstanten statt einer, weil es zwei verschiedene Entscheidungen sind:
# welche Boerse die Kursreihe liefert, und welche die Trades. Sie faellt hier
# auf Kraken, weil nur Kraken beides kann, was gebraucht wird -- Historie und
# Aggressor-Seite.
TRADES_EXCHANGE = "kraken"

# Kraken liefert maximal 1000 Trades je Anfrage.
PAGE_LIMIT = 1000


def trades_path(symbol: str, data_dir: Path | None = None) -> Path:
    """Parquet-Pfad. Getrennt von den Bars, weil es eine andere Kornung ist."""
    root = (data_dir or DataConfig().data_dir) / "trades"
    return root / f"{symbol_to_path(symbol)}.parquet"


def make_trades_exchange(cfg: DataConfig | None = None):
    """Boerse fuer Trades. `trust_env` wie bei den Bars, siehe `qt.data.ingest`."""
    import ccxt

    cfg = cfg or DataConfig()
    exchange = getattr(ccxt, TRADES_EXCHANGE)({"enableRateLimit": True})
    exchange.session.trust_env = True
    return exchange


def fetch_trades(
    exchange,
    symbol: str,
    since: datetime,
    until: datetime | None = None,
    rate_limit_ms: int = 1200,
    on_page=None,
    sink=None,
    flush_every: int = 50,
) -> pd.DataFrame:
    """Trades ab `since` paginiert holen.

    `sink` ist der wichtigste der optionalen Parameter: ein Aufruf, der alle
    `flush_every` Seiten die bis dahin gesammelten Trades bekommt und
    wegschreibt. Ohne ihn haelt die Funktion alles im Speicher und gibt es erst
    am Ende zurueck -- und ein Abzug ueber vierzig Minuten, den ein Zeitlimit
    auf der vorletzten Seite erwischt, hat dann **nichts** geliefert.

    Genau das ist beim ersten 30-Tage-Lauf beinahe passiert (1,14 Mio. Trades
    im Speicher, Zeitlimit in Sicht). Ein langer Abzug muss unterwegs
    Zwischenstaende ablegen, sonst ist seine Laufzeit zugleich sein Risiko.

    Der Fortschritt laeuft ueber den Zeitstempel des **letzten** Trades einer
    Seite plus eine Millisekunde. Wuerde man stattdessen den Zeitstempel
    unveraendert weiterreichen, liefe die Schleife auf einem Trade fest, sobald
    mehrere Trades dieselbe Millisekunde tragen -- und das ist bei Krypto der
    Normalfall, nicht die Ausnahme.

    Bricht die Boerse ab, wird das Bisherige zurueckgegeben statt zu werfen.
    Ein Abzug ueber Stunden, der an der vorletzten Seite alles verliert, ist
    schlimmer als einer, der unvollstaendig endet und beim naechsten Lauf
    weiterlaeuft.
    """
    until = until or datetime.now(timezone.utc)
    cursor = int(since.timestamp() * 1000)
    end = int(until.timestamp() * 1000)

    rows: list[dict] = []
    seen_ids: set[str] = set()
    pages = 0

    while cursor < end:
        try:
            batch = exchange.fetch_trades(symbol, since=cursor, limit=PAGE_LIMIT)
        except Exception as exc:  # noqa: BLE001 -- siehe Docstring
            if on_page is not None:
                on_page(pages, len(rows), f"Abbruch: {type(exc).__name__}: {exc}")
            break

        if not batch:
            break

        for trade in batch:
            ts = trade.get("timestamp")
            side = trade.get("side")
            if ts is None or side is None or ts > end:
                continue
            # Kraken vergibt keine stabilen Trade-IDs; der Schluessel aus
            # Zeit, Preis und Menge reicht, um Ueberlappungen zwischen zwei
            # Seiten zu erkennen.
            key = f"{ts}:{trade['price']}:{trade['amount']}:{side}"
            if key in seen_ids:
                continue
            seen_ids.add(key)
            rows.append(
                {
                    "ts": ts,
                    "price": float(trade["price"]),
                    "amount": float(trade["amount"]),
                    "side": side,
                }
            )

        pages += 1
        if sink is not None and pages % flush_every == 0 and rows:
            sink(_frame(rows))
            rows = []

        last_ts = batch[-1]["timestamp"]
        if last_ts is None or last_ts + 1 <= cursor:
            # Kein Fortschritt: sonst laeuft die Schleife ewig.
            break
        cursor = last_ts + 1

        if on_page is not None:
            on_page(pages, len(rows), "")
        time.sleep(rate_limit_ms / 1000)

    rest = _frame(rows)
    if sink is not None and not rest.empty:
        sink(rest)
    return rest


def _frame(rows: list[dict]) -> pd.DataFrame:
    """Zeilen in eine sortierte Tabelle mit UTC-Zeitstempeln giessen."""
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.sort_values("ts").reset_index(drop=True)[COLUMNS]


def write_trades(
    symbol: str, df: pd.DataFrame, data_dir: Path | None = None
) -> Path:
    """Trades schreiben, mit bestehenden zusammenfuehren und entdoppeln."""
    path = trades_path(symbol, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        alt = pd.read_parquet(path)
        df = pd.concat([alt, df], ignore_index=True)

    df = (
        df.drop_duplicates(subset=["ts", "price", "amount", "side"])
        .sort_values("ts")
        .reset_index(drop=True)
    )
    df.to_parquet(path, index=False)
    return path


def read_trades(
    symbol: str,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    data_dir: Path | None = None,
) -> pd.DataFrame:
    """Trades lesen, optional auf einen Zeitraum beschnitten."""
    path = trades_path(symbol, data_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"Keine Trades fuer {symbol}. Erst `qt data trades --symbol {symbol}` laufen lassen."
        )
    df = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    if start is not None:
        df = df[df["ts"] >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df["ts"] <= pd.Timestamp(end, tz="UTC")]
    return df.sort_values("ts").reset_index(drop=True)


def coverage(df: pd.DataFrame) -> dict:
    """Kennzahlen fuer den Datenreport.

    `max_gap_s` ist die wichtigste Zahl: eine Luecke von Stunden heisst, dass
    der Abzug abgebrochen ist. Order-Flow-Kennzahlen ueber einer Luecke sind
    nicht bloss ungenau, sie sind erfunden -- die fehlenden Trades haetten die
    Volumendifferenz in jede Richtung drehen koennen.
    """
    if df.empty:
        return {"n": 0, "start": None, "end": None, "max_gap_s": None, "buy_share": None}
    deltas = df["ts"].diff().dt.total_seconds().dropna()
    return {
        "n": len(df),
        "start": df["ts"].iloc[0],
        "end": df["ts"].iloc[-1],
        "max_gap_s": float(deltas.max()) if len(deltas) else 0.0,
        "buy_share": float((df["side"] == "buy").mean()),
    }
