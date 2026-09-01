"""Tests fuer On-Chain-Ingest und die Hashrate-Strategie.

Der wichtigste Test dieser Datei ist `test_ein_wert_von_heute_aendert_die_
heutige_entscheidung_nicht`. Alles andere ist Beiwerk: eine Strategie, die
eine Zahl benutzt, die es zum Entscheidungszeitpunkt noch nicht gab, liefert
im Backtest gute Ergebnisse und live keine -- und man merkt es erst, wenn
Geld weg ist.

Bewusst **kein Netzzugriff** in den Tests. Die Blaetter-Logik wird gegen eine
Attrappe geprueft; ob der echte Endpunkt antwortet, ist eine Frage an die
Umgebung und nicht an den Code.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from qt.backtest.engine import run_backtest
from qt.core.types import Bar
from qt.data import onchain
from qt.strategy.library.hashribbon import LAG_BARS, HashRibbon

TAG = timedelta(days=1)
START = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _bars(n: int, preis: float = 100.0) -> list[Bar]:
    """Flache Kursreihe -- der Kurs soll das Signal nicht mitbestimmen."""
    return [
        Bar(
            symbol="BTC/USD",
            timeframe="1d",
            ts=START + i * TAG,
            open=preis,
            high=preis,
            low=preis,
            close=preis,
            volume=1.0,
        )
        for i in range(n)
    ]


def _hashrate(n: int, werte=None) -> dict[datetime, float]:
    """Hashrate-Tabelle, auf dieselben Zeitstempel wie die Bars gestempelt."""
    if werte is None:
        werte = [100.0 + i for i in range(n)]
    return {START + i * TAG: float(werte[i]) for i in range(n)}


# ---------------------------------------------------------------------------
# Point in Time -- der Kern
# ---------------------------------------------------------------------------


def test_ein_wert_von_heute_aendert_die_heutige_entscheidung_nicht():
    """Der Wert fuer Tag D darf die Entscheidung an Bar D nicht beruehren.

    Aufbau wie der bestehende Lookahead-Test: derselbe Lauf zweimal, einmal
    mit veraendertem juengsten Hashrate-Wert. Faende der Wert Eingang, wichen
    die Gewichte ab. Sie muessen bitidentisch bleiben.

    Das ist strenger als noetig -- der Wert waere formal schon zulaessig,
    weil er um 00:00 UTC gestempelt ist und der Tagesbar zu diesem Zeitpunkt
    schliesst. Die Strenge ist Absicht: die Hashrate ist eine Schaetzung aus
    Blockintervallen, deren juengste Werte nachlaufen.
    """
    n = 120
    bars = _bars(n)

    # **Die Basisreihe muss fallen.** Ein erster Entwurf nahm eine steigende
    # Reihe -- die Strategie war dann ohnehin long, und ein noch hoeherer
    # letzter Wert aenderte nichts. Der Test war gruen und haette einen
    # echten Lookahead durchgelassen. Gegengeprueft mit LAG_BARS = 0: so
    # herum schlaegt er fehl, vorher nicht.
    basis = _hashrate(n, [100.0 + (n - i) for i in range(n)])

    # Ein Ausreisser nach oben kippt das kurze Mittel ueber das lange. Wuerde
    # der Wert gelesen, spraenge das Gewicht von flach auf long.
    manipuliert = dict(basis)
    manipuliert[START + (n - 1) * TAG] = 1e12

    gewichte = []
    for tabelle in (basis, manipuliert):
        strategie = HashRibbon(["BTC/USD"], "1d", fast=5, slow=10, hashrate=tabelle)
        ergebnis = run_backtest(strategie, {"BTC/USD": bars})
        gewichte.append(ergebnis.equity["weight_BTC/USD"].to_numpy())

    assert np.array_equal(gewichte[0], gewichte[1]), (
        "Ein veraenderter Hashrate-Wert von heute hat die heutige Entscheidung "
        "veraendert -- die Strategie schaut in die Zukunft."
    )


def test_der_versatz_ist_wirklich_ein_bar_und_nicht_null():
    """Gegenprobe: **ohne** den Versatz wuerde derselbe Test fehlschlagen.

    Ohne diese Gegenprobe koennte der Test oben gruen sein, weil der Wert
    zufaellig nichts aendert -- statt weil er nicht gelesen wird.
    """
    n = 120
    stamps = [START + i * TAG for i in range(n)]
    strategie = HashRibbon(["BTC/USD"], "1d", fast=5, slow=10, hashrate=_hashrate(n))

    # Genau die Auswahl nachbauen, die on_bar trifft.
    fenster = stamps[-(10 + LAG_BARS + 1) :]
    erlaubt = fenster[:-LAG_BARS]

    assert LAG_BARS == 1
    assert stamps[-1] not in erlaubt, "der juengste Bar darf nicht ausgewertet werden"
    assert stamps[-2] in erlaubt, "der vorletzte Bar muss ausgewertet werden"
    assert strategie.warmup_bars == 10 + LAG_BARS + 2


# ---------------------------------------------------------------------------
# Datenluecken
# ---------------------------------------------------------------------------


def test_eine_luecke_ist_keine_meinung_und_kein_flat():
    """Fehlender Wert ergibt nan, nicht 0.0.

    Der Unterschied ist nicht kosmetisch: `nan` laesst das bestehende Gewicht
    stehen, `0.0` verkauft die Position. Eine Datenluecke wuerde damit zu
    einer Handelsentscheidung, die auf nichts beruht -- und die echte Reihe
    hat gemessen zwei Luecken.
    """
    n = 120
    tabelle = _hashrate(n)
    del tabelle[START + (n - 5) * TAG]

    strategie = HashRibbon(["BTC/USD"], "1d", fast=5, slow=10, hashrate=tabelle)
    store = _store_mit(_bars(n))
    assert math.isnan(strategie.on_bar("BTC/USD", store))


def test_vor_dem_warmup_gibt_es_keine_meinung():
    strategie = HashRibbon(["BTC/USD"], "1d", fast=5, slow=10, hashrate=_hashrate(20))
    store = _store_mit(_bars(5))
    assert math.isnan(strategie.on_bar("BTC/USD", store))


def _store_mit(bars: list[Bar]):
    from qt.core.clock import BacktestClock
    from qt.features.registry import FeatureStore

    clock = BacktestClock(bars[0].close_ts)
    store = FeatureStore(clock)
    for bar in bars:
        clock.advance(bar.close_ts)
        store.on_bar(bar)
    return store


# ---------------------------------------------------------------------------
# Das Signal selbst
# ---------------------------------------------------------------------------


def test_fallende_hashrate_geht_flach_steigende_geht_long():
    n = 120
    steigend = _hashrate(n, [100.0 + i for i in range(n)])
    fallend = _hashrate(n, [100.0 + (n - i) for i in range(n)])

    lang = HashRibbon(["BTC/USD"], "1d", fast=5, slow=10, hashrate=steigend)
    flach = HashRibbon(["BTC/USD"], "1d", fast=5, slow=10, hashrate=fallend)
    store = _store_mit(_bars(n))

    assert lang.on_bar("BTC/USD", store) == 1.0
    assert flach.on_bar("BTC/USD", store) == 0.0


def test_die_strategie_geht_nie_short():
    """Long oder flach -- wie macross (ADR-035)."""
    n = 120
    rng = np.random.default_rng(0)
    zufall = _hashrate(n, list(100 + rng.normal(0, 10, n).cumsum()))
    strategie = HashRibbon(["BTC/USD"], "1d", fast=5, slow=10, hashrate=zufall)
    ergebnis = run_backtest(strategie, {"BTC/USD": _bars(n)})
    assert ergebnis.equity["weight_BTC/USD"].min() >= 0.0


def test_fast_muss_kleiner_sein_als_slow():
    with pytest.raises(ValueError, match="muss kleiner sein"):
        HashRibbon(["BTC/USD"], "1d", fast=60, slow=30, hashrate={})


def test_die_strategie_ist_registriert():
    from qt.strategy.registry import get

    assert get("hashribbon") is HashRibbon


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def test_blaettern_setzt_die_jahre_lueckenlos_zusammen(monkeypatch, tmp_path):
    """Jahrweise ziehen, weil lange Zeitraeume ausgeduennt zurueckkommen.

    Gemessen am echten Endpunkt: `timespan=8years` liefert ein 2-Tages-Raster.
    Der Test haelt die Blaetter-Logik fest, ohne das Netz zu brauchen.
    """
    gesehen: list[str] = []

    def fake_fetch(series, start, timeout=60.0):
        gesehen.append(start.date().isoformat())
        return pd.DataFrame(
            {
                "ts": [start + i * TAG for i in range(365)],
                "value": [1.0 + i for i in range(365)],
            }
        )

    monkeypatch.setattr(onchain, "fetch_window", fake_fetch)
    n = onchain.pull(
        "hash-rate",
        START,
        end=START + timedelta(days=730),
        data_dir=tmp_path,
    )

    assert len(gesehen) == 2, f"zwei Jahre, zwei Requests -- gesehen: {gesehen}"
    tabelle = onchain.load_series("hash-rate", data_dir=tmp_path)
    stempel = sorted(tabelle)
    assert n == len(tabelle)
    abstaende = {(b - a).days for a, b in zip(stempel, stempel[1:], strict=False)}
    assert abstaende == {1}, f"Reihe ist nicht taeglich lueckenlos: {abstaende}"


def test_ein_zweiter_abzug_ist_unschaedlich(monkeypatch, tmp_path):
    """Ueberlappende Abzuege duerfen nicht duplizieren -- wie bei write_bars."""

    def fake_fetch(series, start, timeout=60.0):
        return pd.DataFrame(
            {
                "ts": [start + i * TAG for i in range(365)],
                "value": [1.0 + i for i in range(365)],
            }
        )

    monkeypatch.setattr(onchain, "fetch_window", fake_fetch)
    ende = START + timedelta(days=365)
    erst = onchain.pull("hash-rate", START, end=ende, data_dir=tmp_path)
    nochmal = onchain.pull("hash-rate", START, end=ende, data_dir=tmp_path)
    assert erst == nochmal


def test_unbekannte_reihe_wird_benannt(tmp_path):
    with pytest.raises(ValueError, match="Unbekannte Reihe"):
        onchain.pull("erfunden", START, data_dir=tmp_path)


def test_fehlende_datei_sagt_wie_man_sie_bekommt(tmp_path):
    with pytest.raises(FileNotFoundError, match="qt data onchain"):
        onchain.load_series("hash-rate", data_dir=tmp_path)
