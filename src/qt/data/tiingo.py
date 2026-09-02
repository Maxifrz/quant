"""Tages-Bars von Tiingo -- die zweite Anlageklasse.

**Warum es diese Datei ueberhaupt gibt.** Bei 7,7 Jahren Historie und 1,4
effektiv unabhaengigen Maerkten (13 Krypto-Paare, mittlere Korrelation 0,67,
ADR-054) ist erst ein Sharpe ab 0,67 beweisbar. Alles, was dieses Projekt je
gemessen hat, liegt darunter. Weitere Krypto-Maerkte helfen kaum -- der 14.
bringt bei dieser Korrelation rund 2% zusaetzliche effektive Beobachtung. Eine
zweite Anlageklasse bringt fast eine ganze (docs/ZIEL.md).

`ccxt` kann nur Krypto. Tiingo deckt US-Aktien und ETFs ab, und darueber --
per ETF -- auch Anleihen, Rohstoffe und Waehrungen.

**Warum ETFs und nicht Spot-Feeds.** Zwei Gruende, und der zweite wiegt mehr:

1. Eine Quelle, eine Konvention, eine Adjustierungsmethode. ADR-034 hat den
   Preis von Quellenmischung gezeigt -- Kraken-Fluss auf Coinbase-Kurse ist
   eine "plausible und unbewiesene" Annahme, die bis heute nicht gemessen ist.
   Drei Feeds fuer drei Anlageklassen waeren drei davon.
2. **Der ETF ist das handelbare Instrument, der Spotkurs nicht.** Man kann GLD
   kaufen, nicht "Gold". Die gesamte Disziplin dieses Projekts -- Kosten,
   Fills, Point-in-Time -- dreht sich um Handelbarkeit; einen Kurs zu
   backtesten, den man nicht handeln kann, waere eine neue Fiktion.

**Adjustierte OHLC, nicht nur adjClose.** `qt.strategy.library.trend` rechnet
Donchian auf Hochs und Tiefs, `elliott` setzt Pivots darauf. Eine Reihe mit
adjustiertem Schluss und rohen Extremen waere fuer die halbe Bibliothek
unbrauchbar -- und der Fehler faellt nicht auf, er verschiebt nur die Signale.
Tiingo liefert `adjOpen`, `adjHigh`, `adjLow`, `adjClose` und `adjVolume`.

**Adjustierung ist retroaktiv, und das bricht eine Zusage.** Jede Dividende
schreibt die gesamte Vergangenheit der Reihe neu. Eine heute gezogene Reihe
ist damit eine *andere* als dieselbe Reihe vor einem Jahr -- und ein Backtest
darauf ist nicht reproduzierbar, ohne dass irgendetwas fehlschlaegt. Deshalb
schreibt dieses Modul neben jede Reihe eine Meta-Datei mit Quelle, Abrufdatum
und Adjustierungsflag. Wer eine Zahl aus dem Log nachrechnet und ein anderes
Ergebnis bekommt, sieht dort zuerst nach.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from qt.core.config import DATA_DIR
from qt.data.store import COLUMNS, parquet_path, write_bars

BASE_URL = "https://api.tiingo.com/tiingo/daily"

# Reihenfolge der Variablen, in der nach dem Schluessel gesucht wird.
KEY_VARS = ("TIINGO_API_KEY", "TIINGO_TOKEN")

# Der Korb aus docs/ZIEL.md Phase A. Alle US-gelistet, alle mit 15+ Jahren
# Historie, vier Anlageklassen -- und damit die einzige Stellschraube, die die
# beweisbare Sharpe-Schwelle wirklich senkt.
BASKET: dict[str, tuple[str, str]] = {
    "SPY": ("Aktien", "S&P 500"),
    "QQQ": ("Aktien", "Nasdaq 100"),
    "EFA": ("Aktien", "Industrielaender ex USA"),
    "EEM": ("Aktien", "Schwellenlaender"),
    "TLT": ("Anleihen", "US-Staatsanleihen 20+ Jahre"),
    "IEF": ("Anleihen", "US-Staatsanleihen 7-10 Jahre"),
    "GLD": ("Rohstoffe", "Gold"),
    "SLV": ("Rohstoffe", "Silber"),
    "DBC": ("Rohstoffe", "Rohstoffkorb"),
    "USO": ("Rohstoffe", "Rohoel"),
    "UUP": ("FX", "US-Dollar-Index"),
    "FXE": ("FX", "Euro"),
    "FXY": ("FX", "Japanischer Yen"),
}

CALENDAR = "sessions"


class TiingoUnavailable(RuntimeError):
    """Kein Schluessel, kein Netz, oder eine unbrauchbare Antwort."""


@dataclass(frozen=True, slots=True)
class SeriesMeta:
    """Herkunft einer Reihe -- wegen der retroaktiven Adjustierung.

    Ohne diese vier Angaben laesst sich eine spaeter abweichende Zahl nicht
    einordnen: war der Code anders, oder war es die Reihe?
    """

    source: str
    pulled_at: str
    adjusted: bool
    calendar: str

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "pulled_at": self.pulled_at,
            "adjusted": self.adjusted,
            "calendar": self.calendar,
        }


def meta_path(symbol: str, timeframe: str, data_dir: Path | None = None) -> Path:
    """Neben der Parquet-Datei, mit demselben Stamm."""
    pfad = parquet_path(symbol, timeframe, data_dir)
    return pfad.with_suffix(".meta.json")


def write_meta(
    symbol: str, timeframe: str, meta: SeriesMeta, data_dir: Path | None = None
) -> Path:
    pfad = meta_path(symbol, timeframe, data_dir)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(meta.as_dict(), indent=2, sort_keys=True))
    return pfad


def read_meta(
    symbol: str, timeframe: str, data_dir: Path | None = None
) -> SeriesMeta | None:
    """Meta einer Reihe, oder `None` fuer die aelteren Krypto-Reihen.

    `None` heisst nicht "unbekannt", sondern "vor dieser Konvention entstanden"
    -- und das sind ausschliesslich 24/7-Reihen aus `qt.data.ingest`.
    """
    pfad = meta_path(symbol, timeframe, data_dir)
    if not pfad.exists():
        return None
    try:
        roh = json.loads(pfad.read_text())
        return SeriesMeta(
            source=str(roh.get("source", "")),
            pulled_at=str(roh.get("pulled_at", "")),
            adjusted=bool(roh.get("adjusted", False)),
            calendar=str(roh.get("calendar", "24-7")),
        )
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def calendar_of(symbol: str, timeframe: str, data_dir: Path | None = None) -> str:
    """Welcher Kalender fuer diese Reihe gilt. Ohne Meta: 24/7."""
    meta = read_meta(symbol, timeframe, data_dir)
    return meta.calendar if meta else "24-7"


def _token(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    for name in KEY_VARS:
        wert = os.environ.get(name)
        if wert:
            return wert
    raise TiingoUnavailable(
        f"Kein Tiingo-Schluessel. Setze eine der Variablen {', '.join(KEY_VARS)}. "
        "Kostenlos unter app.tiingo.com/account/api/token -- der freie Tarif "
        "erlaubt 1000 Aufrufe am Tag und ist ausdruecklich auf private Nutzung "
        "beschraenkt."
    )


def fetch_daily(
    symbol: str,
    start: datetime,
    end: datetime | None = None,
    token: str | None = None,
    timeout: float = 60.0,
) -> pd.DataFrame:
    """Adjustierte Tages-Bars eines Tickers.

    Genommen werden ausschliesslich die `adj*`-Felder. Die rohen Preise stehen
    in derselben Antwort und sind bewusst **nicht** die Quelle: eine Dividende
    ist ein Uebernachtsprung von mehreren Zehntelprozent, den ein Trendfolger
    als Signal handelt -- ein Ereignis, das dem Halter nichts genommen hat.
    """
    antwort = requests.get(
        f"{BASE_URL}/{symbol}/prices",
        params={
            "startDate": start.date().isoformat(),
            "endDate": (end or datetime.now(timezone.utc)).date().isoformat(),
            "format": "json",
            "resampleFreq": "daily",
            "token": _token(token),
        },
        timeout=timeout,
    )
    if antwort.status_code == 404:
        raise TiingoUnavailable(f"Tiingo kennt {symbol!r} nicht (HTTP 404).")
    if antwort.status_code != 200:
        raise TiingoUnavailable(
            f"Tiingo antwortete mit HTTP {antwort.status_code} fuer {symbol}: "
            f"{antwort.text[:200]}"
        )

    punkte = antwort.json()
    if not isinstance(punkte, list) or not punkte:
        return pd.DataFrame(columns=COLUMNS)

    fehlend = {"date", "adjOpen", "adjHigh", "adjLow", "adjClose"} - set(punkte[0])
    if fehlend:
        raise TiingoUnavailable(
            f"Antwort fuer {symbol} ohne adjustierte Felder: {sorted(fehlend)} "
            "fehlen. Ohne adjustierte Hochs und Tiefs sind Donchian und die "
            "Pivot-Suche nicht rechenbar."
        )

    return pd.DataFrame(
        {
            "ts": pd.to_datetime([p["date"] for p in punkte], utc=True),
            "open": [float(p["adjOpen"]) for p in punkte],
            "high": [float(p["adjHigh"]) for p in punkte],
            "low": [float(p["adjLow"]) for p in punkte],
            "close": [float(p["adjClose"]) for p in punkte],
            "volume": [float(p.get("adjVolume") or 0.0) for p in punkte],
        }
    )


def pull(
    symbols: list[str],
    start: datetime,
    end: datetime | None = None,
    token: str | None = None,
    data_dir: Path | None = None,
    on_symbol=None,
) -> dict[str, int]:
    """Mehrere Ticker ziehen, in den Store schreiben, Meta danebenlegen.

    Geschrieben wird mit `replace=True`: eine adjustierte Reihe ist eine
    **Funktion** der Kurshistorie und der seither gezahlten Dividenden, kein
    Zuwachs. Vereinigen wuerde die alte Adjustierung neben der neuen stehen
    lassen -- derselbe Fehler, der die abgeleiteten Timeframes zerstoert hat
    (ADR-053), nur unsichtbarer, weil die Zeitstempel diesmal zusammenfallen
    und `keep="last"` gaebe, was gerade zuletzt kam.
    """
    geschrieben: dict[str, int] = {}
    jetzt = datetime.now(timezone.utc).isoformat()

    for symbol in symbols:
        df = fetch_daily(symbol, start, end, token=token)
        if df.empty:
            if on_symbol is not None:
                on_symbol(symbol, 0, "keine Daten")
            continue
        write_bars(symbol, "1d", df, data_dir=data_dir, replace=True)
        write_meta(
            symbol,
            "1d",
            SeriesMeta(
                source="tiingo",
                pulled_at=jetzt,
                adjusted=True,
                calendar=CALENDAR,
            ),
            data_dir=data_dir,
        )
        geschrieben[symbol] = len(df)
        if on_symbol is not None:
            on_symbol(symbol, len(df), "")
    return geschrieben


def describe_basket() -> str:
    """Der Korb nach Anlageklassen -- was gezogen wird und warum."""
    nach_klasse: dict[str, list[str]] = {}
    for ticker, (klasse, name) in BASKET.items():
        nach_klasse.setdefault(klasse, []).append(f"{ticker} ({name})")
    return "\n".join(
        f"  {klasse:<10} {', '.join(sorted(eintraege))}"
        for klasse, eintraege in sorted(nach_klasse.items())
    )


def basket_data_dir(data_dir: Path | None = None) -> Path:
    return data_dir or DATA_DIR
