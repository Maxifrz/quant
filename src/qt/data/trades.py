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


def trades_dir(symbol: str, data_dir: Path | None = None) -> Path:
    """Verzeichnis der Tagesteile. Getrennt von den Bars -- andere Kornung."""
    root = (data_dir or DataConfig().data_dir) / "trades"
    return root / symbol_to_path(symbol)


def trades_path(symbol: str, data_dir: Path | None = None) -> Path:
    """Der alte Einzeldatei-Pfad. Nur noch zum Lesen von Altbestaenden.

    Bis ADR-063 lag hier alles in **einer** Datei je Symbol, und
    `write_trades` schrieb sie bei jedem Anhaengen komplett neu. Bei 1,26 MB
    Trades je Tag (BTC/USD, gemessen 2026-09-03) heisst das im Jahresverlauf
    rund 84 GB geschriebene Bytes fuer 460 MB Ergebnis -- der Abzug ist
    quadratisch in seinem eigenen Ausgabevolumen.
    """
    root = (data_dir or DataConfig().data_dir) / "trades"
    return root / f"{symbol_to_path(symbol)}.parquet"


def partition_path(symbol: str, tag, data_dir: Path | None = None) -> Path:
    """Ein Tagesteil. Der Dateiname **ist** der Index.

    `2026-09-03.parquet` laesst sich ohne Oeffnen aus einem Zeitraum
    ausschliessen -- ein Lauf ueber einen Monat liest zwoelf Dateien statt
    einer 460-MB-Datei.
    """
    return trades_dir(symbol, data_dir) / f"{tag:%Y-%m-%d}.parquet"


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
    """Trades in **Tagesteile** schreiben, je Teil zusammenfuehren und entdoppeln.

    Angefasst wird nur, was der neue Block beruehrt. Ein Abzug, der bei Tag
    200 angekommen ist, schreibt die ersten 199 Tage nicht noch einmal --
    genau daran hing bis ADR-063 die Laufzeit langer Abzuege.

    Ein abgeschlossener Tag wird danach nie wieder geschrieben. Das ist auch
    der Grund, warum diese Ablage sich ueberhaupt versionieren **liesse**:
    unveraenderliche Dateien speichert git einmal. Dass sie trotzdem nicht ins
    Repository gehoert, ist eine andere Frage -- siehe ADR-063.
    """
    if df.empty:
        return trades_dir(symbol, data_dir)

    ziel = trades_dir(symbol, data_dir)
    ziel.mkdir(parents=True, exist_ok=True)

    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    for tag, teil in df.groupby(df["ts"].dt.date, sort=True):
        pfad = partition_path(symbol, tag, data_dir)
        if pfad.exists():
            teil = pd.concat([pd.read_parquet(pfad), teil], ignore_index=True)
            teil["ts"] = pd.to_datetime(teil["ts"], utc=True)
        teil = (
            teil.drop_duplicates(subset=["ts", "price", "amount", "side"])
            .sort_values("ts")
            .reset_index(drop=True)
        )
        teil.to_parquet(pfad, index=False, compression="zstd")

    return ziel


def read_trades(
    symbol: str,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    data_dir: Path | None = None,
) -> pd.DataFrame:
    """Trades lesen, optional auf einen Zeitraum beschnitten.

    Tagesteile ausserhalb des Zeitraums werden **am Dateinamen** aussortiert,
    nicht nach dem Oeffnen. Ein Lauf ueber einen Monat liest damit rund
    dreissig kleine Dateien statt der ganzen Historie (ADR-063).
    """
    ziel = trades_dir(symbol, data_dir)
    von = pd.Timestamp(start, tz="UTC") if start is not None else None
    bis = pd.Timestamp(end, tz="UTC") if end is not None else None

    teile = sorted(ziel.glob("*.parquet")) if ziel.is_dir() else []
    if von is not None or bis is not None:
        teile = [t for t in teile if _teil_im_zeitraum(t, von, bis)]

    rahmen = [pd.read_parquet(t) for t in teile]

    # Altbestand aus der Zeit vor der Aufteilung. Kann es nur auf einem
    # persistenten Volume geben; still zu ignorieren waere der Weg, auf dem
    # Daten verschwinden, ohne dass etwas fehlschlaegt.
    alt = trades_path(symbol, data_dir)
    if alt.exists():
        rahmen.append(pd.read_parquet(alt))

    if not rahmen:
        raise FileNotFoundError(
            f"Keine Trades fuer {symbol}. Erst `qt data trades --symbol {symbol}` laufen lassen."
        )

    df = pd.concat(rahmen, ignore_index=True) if len(rahmen) > 1 else rahmen[0]
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    if von is not None:
        df = df[df["ts"] >= von]
    if bis is not None:
        df = df[df["ts"] <= bis]
    return (
        df.drop_duplicates(subset=["ts", "price", "amount", "side"])
        .sort_values("ts")
        .reset_index(drop=True)
    )


def _teil_im_zeitraum(pfad: Path, von, bis) -> bool:
    """Ueberschneidet der Tag dieses Teils den Zeitraum?

    Beidseitig grosszuegig um einen Tag: der Dateiname nennt den UTC-Tag, und
    ein Zeitraum, der um 23:50 beginnt, braucht trotzdem den Vortag nicht --
    aber ein Off-by-one an dieser Stelle liesse Trades verschwinden, ohne
    dass etwas fehlschlaegt. Die Datei wird dann eben gelesen und die Zeile
    danach filtert sie weg.
    """
    try:
        tag = pd.Timestamp(pfad.stem, tz="UTC")
    except ValueError:
        return True  # unbekannter Name -> lieber lesen als uebersehen
    if von is not None and tag < von.normalize() - pd.Timedelta(days=1):
        return False
    if bis is not None and tag > bis.normalize() + pd.Timedelta(days=1):
        return False
    return True


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


# Ab welcher Luecke ein gespeicherter Block als abgerissen gilt.
#
# Bei BTC/USD auf Kraken vergehen selbst nachts selten mehr als ein paar
# Minuten zwischen zwei Trades. Eine Stunde Stille ist deshalb kein ruhiger
# Markt, sondern ein abgebrochener Abzug.
RESUME_GAP_S = 3600.0


def resume_point(
    symbol: str,
    since: datetime,
    max_gap_s: float = RESUME_GAP_S,
    data_dir: Path | None = None,
) -> datetime:
    """Ab wo ein unterbrochener Abzug weitermachen sollte.

    Gibt das Ende des **zusammenhaengenden** Blocks zurueck, der bei `since`
    beginnt -- oder `since` selbst, wenn dort noch nichts liegt.

    Warum nicht einfach der juengste gespeicherte Trade: Der Store kann
    mehrere getrennte Bloecke enthalten. Nach einem 30-Tage-Abzug liegen dort
    die letzten 30 Tage; wer danach ein Jahr holen will und beim juengsten
    Trade ansetzt, ueberspringt die elf Monate davor und merkt es nicht. Die
    Luecke faellt erst auf, wenn eine Strategie ueber ihr eine Kennzahl
    bildet -- und die ist dann nicht ungenau, sondern erfunden.

    Ein Abzug ueber sieben Stunden **muss** fortsetzbar sein. Ohne das ist
    seine Laufzeit zugleich sein Risiko: jeder Abbruch fuehrt zurueck auf Null.
    """
    try:
        df = read_trades(symbol, data_dir=data_dir)
    except FileNotFoundError:
        return since
    if df.empty:
        return since
    df = df.sort_values("ts")

    start = pd.Timestamp(since)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")

    ab_start = df[df["ts"] >= start]
    if ab_start.empty:
        return since

    # Beginnt der gespeicherte Bestand deutlich spaeter als `since`, liegt die
    # Luecke ganz am Anfang -- dann von vorn.
    if (ab_start["ts"].iloc[0] - start).total_seconds() > max_gap_s:
        return since

    deltas = ab_start["ts"].diff().dt.total_seconds()
    luecken = deltas[deltas > max_gap_s]
    if luecken.empty:
        # Durchgehend bis zum Ende: dort weitermachen.
        return ab_start["ts"].iloc[-1].to_pydatetime()

    # Bis zur ersten Luecke ist es zusammenhaengend.
    erste = luecken.index[0]
    return ab_start.loc[:erste]["ts"].iloc[-2].to_pydatetime()
