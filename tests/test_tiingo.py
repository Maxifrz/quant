"""Tests der Tiingo-Anbindung und der Anlageklassen-Erweiterung.

Der wichtigste Test ist `test_nur_adjustierte_felder_werden_uebernommen`.
Tiingo liefert rohe **und** adjustierte Preise in derselben Antwort, und die
Verwechslung faellt nirgends auf: eine Dividende ist ein Uebernachtsprung von
wenigen Zehntelprozent, den ein Trendfolger als Signal handelt -- obwohl dem
Halter nichts genommen wurde. Der Backtest waere dann nicht kaputt, nur falsch.

Die uebrigen halten fest, was mit einer zweiten Anlageklasse zwangslaeufig
schiefgeht: 24/7-Annahmen in der Annualisierung, Wochenenden als "Luecken",
und ein Kostensatz, der fuer beide Klassen gleichzeitig gilt.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from qt.backtest.costs import FillContext, FlatFillModel, SizeAwareFillModel, round_trip_bps
from qt.backtest.metrics import compute, observed_periods_per_year, sharpe
from qt.core.config import US_ETF_COSTS, BacktestConfig, CostConfig
from qt.data import tiingo
from qt.data.integrity import check, find_gaps
from qt.data.store import read_bars

START = datetime(2020, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Adjustierte Preise
# ---------------------------------------------------------------------------


def _antwort(n: int = 5, roh: float = 100.0, adjustiert: float = 90.0) -> list[dict]:
    """Tiingo-Punkte, bei denen sich roh und adjustiert klar unterscheiden."""
    tage = pd.date_range("2020-01-02", periods=n, freq="B", tz="UTC")
    return [
        {
            "date": ts.isoformat().replace("+00:00", "Z"),
            "open": roh, "high": roh * 1.01, "low": roh * 0.99, "close": roh,
            "volume": 1_000_000,
            "adjOpen": adjustiert, "adjHigh": adjustiert * 1.01,
            "adjLow": adjustiert * 0.99, "adjClose": adjustiert,
            "adjVolume": 1_100_000,
            "divCash": 0.0, "splitFactor": 1.0,
        }
        for ts in tage
    ]


class _Antwort:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload) if isinstance(payload, list) else str(payload)

    def json(self):
        return self._payload


def test_nur_adjustierte_felder_werden_uebernommen(monkeypatch):
    """Roh und adjustiert stehen in derselben Antwort -- die Wahl ist stumm."""
    monkeypatch.setattr(
        tiingo.requests, "get", lambda *a, **k: _Antwort(_antwort())
    )
    df = tiingo.fetch_daily("SPY", START, token="x")

    assert np.allclose(df["close"], 90.0), (
        "die rohen Preise sind uebernommen worden -- jede Dividende waere ein Signal"
    )
    assert np.allclose(df["open"], 90.0)
    assert df["high"].iloc[0] == pytest.approx(90.0 * 1.01)
    assert df["low"].iloc[0] == pytest.approx(90.0 * 0.99)
    assert df["volume"].iloc[0] == pytest.approx(1_100_000)


def test_eine_antwort_ohne_adjustierte_hochs_wird_abgelehnt(monkeypatch):
    """Nur `adjClose` reicht nicht: Donchian und Pivots rechnen auf Extremen."""
    verstuemmelt = [
        {"date": "2020-01-02T00:00:00Z", "adjClose": 90.0, "close": 100.0}
    ]
    monkeypatch.setattr(
        tiingo.requests, "get", lambda *a, **k: _Antwort(verstuemmelt)
    )
    with pytest.raises(tiingo.TiingoUnavailable, match="adjustierte"):
        tiingo.fetch_daily("SPY", START, token="x")


def test_ein_unbekannter_ticker_meldet_sich_sprechend(monkeypatch):
    monkeypatch.setattr(
        tiingo.requests, "get", lambda *a, **k: _Antwort({"detail": "not found"}, 404)
    )
    with pytest.raises(tiingo.TiingoUnavailable, match="404"):
        tiingo.fetch_daily("GIBTSNICHT", START, token="x")


def test_ohne_schluessel_gibt_es_einen_hinweis_und_keinen_absturz(monkeypatch):
    for name in tiingo.KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(tiingo.TiingoUnavailable, match="tiingo.com"):
        tiingo._token(None)


# ---------------------------------------------------------------------------
# Meta: Adjustierung ist retroaktiv
# ---------------------------------------------------------------------------


def test_der_abzug_legt_herkunft_und_abrufdatum_daneben(monkeypatch, tmp_path):
    """Eine adjustierte Reihe von heute ist eine andere als die von letztem Jahr."""
    monkeypatch.setattr(
        tiingo.requests, "get", lambda *a, **k: _Antwort(_antwort(6))
    )
    tiingo.pull(["SPY"], START, token="x", data_dir=tmp_path)

    meta = tiingo.read_meta("SPY", "1d", tmp_path)
    assert meta is not None
    assert meta.source == "tiingo"
    assert meta.adjusted is True
    assert meta.calendar == "sessions"
    assert meta.pulled_at  # ISO-Zeitstempel, nicht leer


def test_ohne_meta_gilt_der_krypto_kalender(tmp_path):
    """Die alten Reihen aus `qt.data.ingest` sind 24/7 -- und bleiben es."""
    assert tiingo.calendar_of("BTC/USD", "1d", tmp_path) == "24-7"


def test_ein_zweiter_abzug_ersetzt_statt_zu_vereinigen(monkeypatch, tmp_path):
    """Eine adjustierte Reihe ist eine Funktion, kein Zuwachs (ADR-053)."""
    monkeypatch.setattr(tiingo.requests, "get", lambda *a, **k: _Antwort(_antwort(5, adjustiert=90.0)))
    tiingo.pull(["SPY"], START, token="x", data_dir=tmp_path)
    zuerst = read_bars("SPY", "1d", data_dir=tmp_path)

    # Neue Dividende -> die ganze Vergangenheit wird neu adjustiert.
    monkeypatch.setattr(tiingo.requests, "get", lambda *a, **k: _Antwort(_antwort(5, adjustiert=89.0)))
    tiingo.pull(["SPY"], START, token="x", data_dir=tmp_path)
    danach = read_bars("SPY", "1d", data_dir=tmp_path)

    assert len(danach) == len(zuerst), "die alte Adjustierung steht noch daneben"
    assert np.allclose(danach["close"], 89.0)


# ---------------------------------------------------------------------------
# Annualisierung: gemessen statt angenommen
# ---------------------------------------------------------------------------


def _kurve(n: int, freq: str, start: str = "2020-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    rng = np.random.default_rng(4)
    return pd.Series(100 * np.cumprod(1 + rng.normal(0.0004, 0.01, n)), index=idx)


def test_handelstage_werden_nicht_wie_kalendertage_annualisiert():
    """Der Fund: 24/7-Annahme auf einer Boersenreihe = Faktor 1,20 zu viel."""
    boerse = _kurve(1200, "B")
    gemessen = observed_periods_per_year(boerse.index, 365.25)

    assert 250 < gemessen < 265, f"gemessen {gemessen:.1f}, erwartet rund 252"
    assert math.isclose(math.sqrt(365.25 / gemessen), 1.20, abs_tol=0.02)


def test_fuer_krypto_aendert_sich_praktisch_nichts():
    """Gegenprobe: eine durchgehende Tagesreihe bleibt bei 365."""
    krypto = _kurve(1200, "D")
    assert observed_periods_per_year(krypto.index, 365.25) == pytest.approx(
        365.25, rel=0.01
    )


def test_der_sharpe_einer_boersenreihe_wird_nicht_aufgeblasen():
    """Ende zu Ende: dieselben Renditen, einmal als Boerse, einmal als Krypto."""
    rng = np.random.default_rng(11)
    renditen = rng.normal(0.0005, 0.01, 1200)
    werte = 100 * np.cumprod(1 + renditen)

    boerse = compute(
        pd.Series(werte, index=pd.date_range("2020-01-01", periods=1200, freq="B", tz="UTC")),
        "1d",
    )
    krypto = compute(
        pd.Series(werte, index=pd.date_range("2020-01-01", periods=1200, freq="D", tz="UTC")),
        "1d",
    )
    assert krypto.sharpe / boerse.sharpe == pytest.approx(1.20, abs=0.03), (
        "die Annualisierung haengt nicht mehr an der Zeitachse"
    )


def test_ohne_zeitindex_gilt_weiter_der_timeframe():
    """Rueckfallebene: ein blosses Array hat keine Bar-Dichte."""
    assert observed_periods_per_year(pd.RangeIndex(100), 365.25) == 365.25
    assert sharpe(np.array([0.01, -0.005, 0.02, 0.0]), "1d") == pytest.approx(
        sharpe(np.array([0.01, -0.005, 0.02, 0.0]), "1d", periods_per_year=365.25)
    )


# ---------------------------------------------------------------------------
# Handelskalender in der Integritaetspruefung
# ---------------------------------------------------------------------------


def _boersenreihe(n: int = 60) -> pd.DataFrame:
    ts = pd.date_range("2020-01-02", periods=n, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "ts": ts, "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0, "volume": 1.0,
        }
    )


def test_wochenenden_sind_keine_luecken():
    """Sonst meldet jeder ETF rund 400 Luecken im Jahr und niemand liest mehr hin."""
    df = _boersenreihe()
    assert len(find_gaps(df["ts"], "1d", calendar="sessions")) == 0
    assert len(find_gaps(df["ts"], "1d", calendar="24-7")) > 10, (
        "Gegenprobe: im 24/7-Modus *muessen* die Wochenenden auffallen"
    )


def test_ein_echtes_loch_faellt_auch_im_boersenmodus_auf():
    df = _boersenreihe()
    mit_loch = pd.concat([df.iloc[:20], df.iloc[40:]], ignore_index=True)
    assert len(find_gaps(mit_loch["ts"], "1d", calendar="sessions")) == 1


def test_abdeckung_wird_ohne_handelskalender_nicht_erfunden():
    """Eine geratene Zahl in einer Integritaetspruefung ist schlimmer als keine."""
    bericht = check("SPY", "1d", _boersenreihe(), calendar="sessions")
    assert math.isnan(bericht.coverage)
    assert "--" in bericht.summary()
    assert bericht.ok

    krypto = check("BTC/USD", "1d", _boersenreihe(), calendar="24-7")
    assert not math.isnan(krypto.coverage)


# ---------------------------------------------------------------------------
# Kosten je Symbol
# ---------------------------------------------------------------------------


def test_etf_und_krypto_zahlen_verschiedene_kosten():
    """90 bps auf einen ETF waeren absurd, 5 bps auf Krypto ebenso."""
    modell = FlatFillModel(CostConfig(), by_symbol={"SPY": US_ETF_COSTS})
    assert round_trip_bps(modell.costs_for("BTC/USD")) == pytest.approx(130.0)
    assert round_trip_bps(modell.costs_for("SPY")) == pytest.approx(5.2)

    teuer = modell.fill(100.0, 10.0, FillContext(symbol="BTC/USD"))
    billig = modell.fill(100.0, 10.0, FillContext(symbol="SPY"))
    assert teuer.total > billig.total * 5


def test_ein_unbekanntes_symbol_faellt_auf_den_default():
    modell = FlatFillModel(CostConfig(), by_symbol={"SPY": US_ETF_COSTS})
    assert modell.costs_for("VOLLIG/NEU").taker_fee_bps == 60.0
    assert modell.costs_for(None).taker_fee_bps == 60.0


def test_auch_das_groessenabhaengige_modell_trennt_die_saetze():
    modell = SizeAwareFillModel(CostConfig(), by_symbol={"SPY": US_ETF_COSTS})
    ctx = FillContext(bar_volume=1000.0, symbol="SPY")
    kosten = modell.fill(100.0, 10.0, ctx)
    # Der Impact kommt oben drauf, die Grundgebuehr bleibt die des ETF.
    # Nicht null: Aufsichtsgebuehren fallen auch beim provisionsfreien Broker
    # an. Aber um Groessenordnungen unter dem Krypto-Taker (ADR-056).
    assert 0.0 < kosten.fee < 0.05, "ETF-Gebuehr ist winzig, nicht null und nicht 60 bps"


def test_der_broker_reicht_das_symbol_durch():
    """Ohne das waere die Trennung oben wirkungslos."""
    from qt.backtest.broker_sim import SimBroker
    from qt.core.types import Order

    cfg = BacktestConfig(costs_by_symbol={"SPY": US_ETF_COSTS})
    broker = SimBroker(cfg)
    ts = datetime(2020, 1, 2, tzinfo=timezone.utc)

    broker.submit(Order(symbol="SPY", qty=10.0))
    broker.execute_pending("SPY", 100.0, ts)
    etf_gebuehr = broker.fees_paid

    broker.submit(Order(symbol="BTC/USD", qty=10.0))
    broker.execute_pending("BTC/USD", 100.0, ts)
    krypto_gebuehr = broker.fees_paid - etf_gebuehr

    assert 0.0 < etf_gebuehr < 0.05
    assert krypto_gebuehr > 3.0
    assert krypto_gebuehr > 100 * etf_gebuehr


# ---------------------------------------------------------------------------
# Der Korb
# ---------------------------------------------------------------------------


def test_der_korb_deckt_sechs_anlageklassen_ab():
    """Ein Effekt in nur einer Klasse ist deren Beta, nicht der Edge (ZIEL.md).

    Von vier auf sechs Klassen gewachsen (ADR-061): Volatilitaet und
    Immobilien sind dazugekommen, weil vier nicht reichten, um n_eff ueber 4
    zu bringen.
    """
    klassen = {klasse for klasse, _ in tiingo.BASKET.values()}
    assert klassen == {
        "Aktien", "Anleihen", "Rohstoffe", "FX", "Volatilitaet", "Immobilien",
    }
    assert len(tiingo.BASKET) == 25
    assert "Rohstoffe" in tiingo.describe_basket()


def test_der_korb_enthaelt_nichts_was_n_eff_billig_macht():
    """Die Ausschlussregeln aus ADR-061, als Test statt als Vorsatz.

    `n_eff = n/(1+(n-1)*rho)` laesst sich schlagen, ohne dass ein einziger
    Standardfehler kleiner wird -- und die Wege dahin sind naheliegend genug,
    dass ein Kommentar sie nicht aufhaelt:

    * **Inverse Produkte.** `SH` ist rechnerisch -SPY. Korrelation -1 zum
      Bestand, druckt rho kraeftig, traegt null neue Information.
    * **Gehebelte Produkte.** `TQQQ` ist 3x QQQ -- dieselbe Wette, lauter.
    * **Geldmarktnahe Reihen.** `BIL` hat kaum Varianz und damit Korrelation
      nahe null zu allem. Ein Markt ohne Bewegung ist kein Test.

    Der Test kann nur benannte Faelle fangen, nicht die Regel beweisen. Er
    steht hier, weil die Versuchung genau dann kommt, wenn n_eff das naechste
    Mal knapp unter der Schwelle liegt.
    """
    verboten = {
        "SH": "invers zu SPY",
        "PSQ": "invers zu QQQ",
        "SDS": "invers und gehebelt",
        "TBF": "invers zu langlaufenden Staatsanleihen",
        "SVXY": "invers zu VIX-Futures",
        "TQQQ": "3x QQQ",
        "SPXL": "3x S&P 500",
        "UVXY": "1,5x VIX-Futures",
        "BIL": "Geldmarkt, kaum Varianz",
        "SGOV": "Geldmarkt, kaum Varianz",
        "SHV": "Geldmarkt, kaum Varianz",
    }
    treffer = {t: grund for t, grund in verboten.items() if t in tiingo.BASKET}
    assert not treffer, (
        f"Diese Ticker heben n_eff, ohne Evidenz zu liefern: {treffer}"
    )
