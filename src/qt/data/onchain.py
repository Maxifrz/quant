"""On-Chain-Reihen von blockchain.info.

Warum dieses Modul ueberhaupt existiert: es liefert die einzige Groesse im
Projekt, die **nur** Bitcoin haben kann. Ethereum ist seit 2022 Proof-of-Stake
-- es gibt keine ETH-Miner, keine Hashrate, keine Kapitulation. Ein Signal aus
der Hashrate ist damit BTC-spezifisch per Konstruktion und nicht per
Erzaehlung. Zwei vorher geprueften Erzaehlungen ("BTC ist berechenbarer",
"BTC ist der Zufluchtsort") hielten der Messung nicht stand (ADR-048).

Drei Eigenheiten der Quelle, alle gemessen und nicht aus der Doku abgeschrieben:

1. **Lange Zeitraeume werden ausgeduennt.** `timespan=8years` liefert ein
   2-Tages-Raster, kein taegliches. Erst `timespan=1year&start=...` gibt
   Tagesaufloesung. Deshalb wird jahrweise geblaettert, obwohl ein einziger
   Request bequemer waere -- der bequeme Weg liefert stillschweigend die
   halbe Aufloesung.
2. **Die Stempel stehen auf 00:00 UTC** und der Wert beschreibt den Tag, der
   dort beginnt.
3. **Die Hashrate ist eine Schaetzung**, abgeleitet aus Blockintervallen. Die
   juengsten Tage koennen sich noch verschieben, wenn Bloecke nachlaufen. Wer
   sie wie eine Messung behandelt, baut einen Backtest auf Zahlen, die es zum
   Entscheidungszeitpunkt so nicht gab.

Zu Punkt 3 gehoert eine Grenze, die man nicht wegprogrammieren kann: wir
ziehen die **heutige** Fassung der Historie, nicht das, was das API an einem
vergangenen Tag gesagt haette. Fuer die Hashrate ist das vertretbar, weil sie
eine deterministische Funktion unveraenderlicher Blockzeitstempel ist. Die
Absicherung dagegen sitzt nicht hier, sondern in der Strategie: sie benutzt
den Wert von D-1, nie den von D.

Ablage: `data/onchain/{reihe}.parquet` mit den Spalten `ts` (UTC) und `value`.
`/data/` ist gitignored.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from qt.core.config import DATA_DIR

BASE_URL = "https://api.blockchain.info/charts"

# Reihen, die hier bewusst unterstuetzt werden. Keine offene Liste: jede
# zusaetzliche Reihe ist ein weiterer Freiheitsgrad, an dem sich eine
# Strategie festhalten kann, und damit ein Beitrag zum Mehrfachtestproblem
# (ADR-005). Wer eine braucht, traegt sie ein und begruendet es.
SERIES = {
    "hash-rate": "Geschaetzte Netzwerk-Hashrate in TH/s",
    "difficulty": "Mining-Schwierigkeit",
    "miners-revenue": "Miner-Einnahmen in USD",
}

COLUMNS = ["ts", "value"]

# Ein Request je Jahr. Kuerzer waere unnoetig, laenger duennt aus (Punkt 1).
_SPAN = "1year"


def parquet_path(series: str, data_dir: Path | None = None) -> Path:
    root = data_dir or DATA_DIR
    return root / "onchain" / f"{series}.parquet"


def fetch_window(series: str, start: datetime, timeout: float = 60.0) -> pd.DataFrame:
    """Ein Jahr einer Reihe holen.

    Bewusst ohne Wiederholungslogik: ein Fehlschlag soll hier sichtbar sein
    und nicht in einer halben Reihe enden, die spaeter wie eine Datenluecke
    aussieht.
    """
    response = requests.get(
        f"{BASE_URL}/{series}",
        params={"timespan": _SPAN, "start": start.date().isoformat(), "format": "json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()

    values = payload.get("values") or []
    if not values:
        return pd.DataFrame(columns=COLUMNS)

    return pd.DataFrame(
        {
            "ts": [
                datetime.fromtimestamp(point["x"], tz=timezone.utc) for point in values
            ],
            "value": [float(point["y"]) for point in values],
        }
    )


def pull(
    series: str,
    start: datetime,
    end: datetime | None = None,
    data_dir: Path | None = None,
) -> int:
    """Eine Reihe jahrweise ziehen und in den Store schreiben.

    Gibt die Zahl der gespeicherten Punkte zurueck.
    """
    if series not in SERIES:
        raise ValueError(
            f"Unbekannte Reihe {series!r}. Bekannt: {sorted(SERIES)}. "
            "Neue Reihen bitte in SERIES eintragen -- siehe Kommentar dort."
        )

    end = end or datetime.now(tz=timezone.utc)
    teile: list[pd.DataFrame] = []
    cursor = start
    while cursor < end:
        teile.append(fetch_window(series, cursor))
        cursor += timedelta(days=365)

    if not teile:
        return 0

    df = _normalise(pd.concat(teile, ignore_index=True))
    df = df[(df.ts >= start) & (df.ts <= end)]
    return len(_write(series, df, data_dir))


def _write(series: str, df: pd.DataFrame, data_dir: Path | None = None) -> pd.DataFrame:
    """Schreiben und mit Vorhandenem vereinigen -- wie `store.write_bars`.

    Ein zweiter Abzug mit ueberlappendem Zeitraum ist damit unschaedlich.
    """
    path = parquet_path(series, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        df = _normalise(pd.concat([pd.read_parquet(path), df], ignore_index=True))

    df.to_parquet(path, index=False)
    return df


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Sortieren, deduplizieren, UTC erzwingen."""
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    out = df[COLUMNS].copy()
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    out = out.dropna(subset=["ts", "value"])
    out = out.drop_duplicates(subset="ts", keep="last")
    return out.sort_values("ts", ignore_index=True)


def load_series(series: str, data_dir: Path | None = None) -> dict[datetime, float]:
    """Reihe als Nachschlagetabelle, Zeitstempel -> Wert.

    Ein dict und keine DataFrame, aus demselben Grund wie bei
    `qt.features.orderflow.load_flow_table`: die Strategie schlaegt je Bar
    genau einen Zeitstempel nach, und das soll ein Hash-Lookup sein und keine
    Filterung ueber eine Tabelle. Eine Filterung waere die Stelle, an der
    versehentlich "alles bis jetzt" gelesen wird -- also eine zweite Uhr.
    """
    path = parquet_path(series, data_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"Keine On-Chain-Daten fuer {series!r} unter {path}. "
            f"Erst ziehen: qt data onchain --series {series}"
        )
    df = pd.read_parquet(path)
    return {
        ts.to_pydatetime(): float(value)
        for ts, value in zip(df["ts"], df["value"], strict=True)
        if math.isfinite(float(value))
    }


def describe(series: str, data_dir: Path | None = None) -> str:
    """Eine Zeile Bestand und Integritaet -- Vorbild ist `qt.data.integrity`."""
    path = parquet_path(series, data_dir)
    if not path.exists():
        return f"{series:>16}  fehlt"

    df = pd.read_parquet(path).sort_values("ts")
    if df.empty:
        return f"{series:>16}  leer"

    spanne = (df.ts.iloc[-1] - df.ts.iloc[0]).days + 1
    luecken = int((df.ts.diff().dt.days.dropna() > 1).sum())
    marke = "" if luecken == 0 else f"  (!!) {luecken} Luecke(n)"
    return (
        f"{series:>16}  {len(df):>6,} Punkte  "
        f"{df.ts.iloc[0].date()} bis {df.ts.iloc[-1].date()}  "
        f"Abdeckung {len(df) / spanne:.1%}{marke}"
    )
