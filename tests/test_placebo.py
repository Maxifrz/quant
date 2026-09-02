"""Tests der Negativkontrollen.

Zwei Tests tragen hier alles andere:

`test_der_abspieler_reproduziert_die_strategie_exakt` ist die Kalibrierprobe.
Der Vergleich "echte Strategie gegen gewuerfelte Fassung" ist nur dann ein
Vergleich, wenn der Abspieler mit den echten Gewichten dieselbe Kennzahl
liefert wie die Strategie selbst. Faellt dieser Test, misst die ganze
Kontrolle zwei verschiedene Dinge.

`test_die_permutation_verschiebt_wirklich_etwas` ist die Gegenprobe. Eine
Permutation, die alles erhaelt und nichts bewegt, wuerde jede Strategie
bestehen lassen -- und zwar lautlos.
"""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pytest

from qt.backtest.walkforward import walk_forward
from qt.core.clock import BacktestClock
from qt.features.registry import FeatureStore
from qt.research.placebo import (
    PlaybackStrategy,
    cross_market_control,
    mean_pairwise_correlation,
    permutation_control,
    shuffle_episodes,
    signal_series,
)
from qt.strategy.registry import get, load_library
from tests.conftest import make_bars

load_library()


def _bars(n: int = 900, seed: int = 3, symbol: str = "BTC/USD"):
    rng = np.random.default_rng(seed)
    preise = 100 * np.cumprod(1 + rng.normal(0.0008, 0.02, n))
    return make_bars(n, symbol, "1d", prices=preise)


def _macross(symbols=("BTC/USD",)):
    return get("macross")(list(symbols), "1d", fast=5, slow=20)


# ---------------------------------------------------------------------------
# Der Abspieler
# ---------------------------------------------------------------------------


def test_der_abspieler_reproduziert_die_strategie_exakt():
    """Die Kalibrierprobe -- ohne sie ist der Vergleich keiner."""
    bars = {"BTC/USD": _bars()}
    echt = walk_forward(_macross, bars, train_bars=300, test_bars=150, embargo_bars=10)

    signale = signal_series(_macross, bars)
    abspieler = walk_forward(
        lambda: PlaybackStrategy(
            ["BTC/USD"], "1d", signals=signale, warmup_bars=_macross().warmup_bars
        ),
        bars, train_bars=300, test_bars=150, embargo_bars=10,
    )

    assert abspieler.metrics.sharpe == pytest.approx(echt.metrics.sharpe, abs=1e-12)
    assert abspieler.metrics.n_trades == echt.metrics.n_trades
    assert abspieler.metrics.total_return == pytest.approx(
        echt.metrics.total_return, abs=1e-12
    )


def test_der_abspieler_liest_keine_zukunft():
    """Eintraege fuer spaetere Bars duerfen das Ergebnis nicht beruehren.

    Dieselbe Bauart wie der Beiwagen-Test in `test_orderflow.py`: die Tabelle
    bekommt Gewichte fuer Zeitpunkte **nach** dem Ende der Bars. Wuerde der
    Abspieler ueber die Tabelle iterieren statt ueber `window.timestamps`,
    faenden sie Eingang.
    """
    bars = _bars(200)
    signale = {(b.symbol, b.ts): (1.0 if i % 2 else 0.0) for i, b in enumerate(bars)}

    schritt = bars[1].ts - bars[0].ts
    zukunft = dict(signale)
    for k in range(1, 60):
        zukunft[("BTC/USD", bars[-1].ts + k * schritt)] = -1.0

    def lauf(karte):
        strategie = PlaybackStrategy(["BTC/USD"], "1d", signals=karte, warmup_bars=2)
        clock = BacktestClock(bars[0].ts)
        store = FeatureStore(clock, maxlen=500)
        out = []
        for bar in bars:
            clock.advance(bar.close_ts)
            store.on_bar(bar)
            out.append(strategie.on_bar("BTC/USD", store))
        return out

    assert lauf(signale) == lauf(zukunft)


def test_ein_fehlender_eintrag_ist_keine_meinung():
    """`nan` laesst das Gewicht stehen, 0.0 wuerde die Position schliessen."""
    bars = _bars(60)
    strategie = PlaybackStrategy(["BTC/USD"], "1d", signals={}, warmup_bars=2)
    clock = BacktestClock(bars[0].ts)
    store = FeatureStore(clock, maxlen=200)
    for bar in bars:
        clock.advance(bar.close_ts)
        store.on_bar(bar)
    assert math.isnan(strategie.on_bar("BTC/USD", store))


def test_signal_series_zeichnet_open_zeiten_auf():
    """Die Equity-Kurve traegt Close-Zeiten -- ein Off-by-one waere Lookahead.

    Bei zusammenhaengenden Bars ist die Close-Zeit von Bar i zugleich die
    Open-Zeit von Bar i+1; die beiden Mengen ueberschneiden sich also fast
    vollstaendig und taugen nicht zur Unterscheidung. Was sie unterscheidet,
    ist genau ein Punkt: der **letzte** Bar. Seine Close-Zeit liegt hinter
    jeder Open-Zeit der Reihe.
    """
    bars = _bars(300)
    signale = signal_series(_macross, {"BTC/USD": bars})
    stempel = {ts for _, ts in signale}

    assert stempel <= {bar.ts for bar in bars}
    assert max(stempel) == bars[-1].ts
    assert bars[-1].close_ts not in stempel, (
        "aufgezeichnet wurden Close-Zeiten -- jede Ziehung waere um einen Bar "
        "verschoben"
    )


# ---------------------------------------------------------------------------
# Die Permutation
# ---------------------------------------------------------------------------


def _laeufe(werte):
    """(Wert, Laenge) je maximalem Block -- zum Nachrechnen der Erhaltung."""
    out = []
    for w in werte:
        schluessel = "nan" if w != w else w
        if out and out[-1][0] == schluessel:
            out[-1][1] += 1
        else:
            out.append([schluessel, 1])
    return [tuple(x) for x in out]


def test_die_permutation_erhaelt_zeit_episoden_und_trades():
    """Alles ausser der Lage bleibt gleich -- sonst ist der Vergleich unfair."""
    rng = np.random.default_rng(0)
    werte = [0.0] * 10 + [1.0] * 30 + [0.0] * 5 + [1.0] * 12 + [0.0] * 40 + [1.0] * 3
    getauscht = shuffle_episodes(werte, rng)

    assert len(getauscht) == len(werte)
    assert sorted(getauscht) == sorted(werte), "Zeit je Gewicht hat sich veraendert"
    assert len(_laeufe(getauscht)) == len(_laeufe(werte)), "Episodenzahl veraendert"
    # Abwechslungsmuster: dieselbe Folge von Gewichten, nur andere Laengen.
    assert [w for w, _ in _laeufe(getauscht)] == [w for w, _ in _laeufe(werte)]


def test_die_permutation_verschiebt_wirklich_etwas():
    """Gegenprobe: eine Permutation, die nichts bewegt, laesst alles bestehen."""
    rng = np.random.default_rng(1)
    werte = [0.0] * 10 + [1.0] * 30 + [0.0] * 5 + [1.0] * 12 + [0.0] * 40 + [1.0] * 3
    verschieden = sum(
        shuffle_episodes(werte, rng) != werte for _ in range(20)
    )
    assert verschieden >= 15, (
        f"nur {verschieden} von 20 Ziehungen unterscheiden sich vom Original -- "
        "die Kontrolle wuerde jede Strategie bestehen lassen"
    )


def test_die_permutation_haelt_nan_als_eigene_klasse():
    """`nan` ist ein Zustand, kein Gewicht -- es darf nicht mit 0.0 tauschen."""
    rng = np.random.default_rng(2)
    werte = [math.nan] * 5 + [0.0] * 3 + [1.0] * 7 + [0.0] * 2
    getauscht = shuffle_episodes(werte, rng)
    assert sum(1 for w in getauscht if w != w) == 5
    assert getauscht[:5] == pytest.approx([math.nan] * 5, nan_ok=True)


def test_eine_einzelne_episode_bleibt_unveraendert():
    rng = np.random.default_rng(3)
    assert shuffle_episodes([1.0] * 8, rng) == [1.0] * 8


def test_die_kontrolle_meldet_kalibrierung_und_verteilung():
    bars = {"BTC/USD": _bars(700)}
    ergebnis = permutation_control(
        _macross, bars, train_bars=250, test_bars=120, embargo_bars=10,
        draws=12, seed=7,
    )
    assert ergebnis.kalibrierfehler < 1e-9, (
        "der Abspieler bildet die Strategie nicht ab -- Vergleich ungueltig"
    )
    assert len(ergebnis.ziehungen) == 12
    assert 0.0 <= ergebnis.perzentil <= 1.0
    assert "Perzentil" in ergebnis.table()


def test_die_kontrolle_ist_bei_gleichem_seed_reproduzierbar():
    bars = {"BTC/USD": _bars(700)}
    kwargs = dict(train_bars=250, test_bars=120, embargo_bars=10, draws=6, seed=42)
    a = permutation_control(_macross, bars, **kwargs)
    b = permutation_control(_macross, bars, **kwargs)
    assert a.ziehungen == b.ziehungen


def test_ein_rauschsignal_besteht_die_kontrolle_nicht():
    """Die entscheidende Gegenprobe: die Kontrolle muss auch ablehnen koennen.

    Ein zufaellig gewuerfeltes Signal ist von seinen eigenen Permutationen
    definitionsgemaess nicht zu unterscheiden. Sein Perzentil muss deshalb
    irgendwo im Mittelfeld landen -- nicht oben.
    """
    bars = _bars(700)
    rng = np.random.default_rng(5)
    # Ein Signal mit derselben Bauform wie macross, aber ohne jeden Bezug
    # zum Kurs: lange Bloecke, zufaellig gesetzt.
    roh = rng.choice([0.0, 1.0], size=len(bars), p=[0.5, 0.5])
    geglaettet = np.repeat(roh[:: 20], 20)[: len(bars)]
    signale = {(b.symbol, b.ts): float(w) for b, w in zip(bars, geglaettet, strict=True)}

    def mach():
        return PlaybackStrategy(["BTC/USD"], "1d", signals=signale, warmup_bars=22)

    ergebnis = permutation_control(
        mach, {"BTC/USD": bars}, train_bars=250, test_bars=120, embargo_bars=10,
        draws=40, seed=11,
    )
    assert ergebnis.perzentil < 0.95, (
        f"ein Rauschsignal erreicht Perzentil {ergebnis.perzentil:.0%} -- "
        "die Kontrolle laesst alles durch"
    )


# ---------------------------------------------------------------------------
# Der Querschnitt
# ---------------------------------------------------------------------------


def test_zu_kurze_maerkte_werden_mit_grund_ausgewiesen():
    """Ein leiser Ausschluss macht einen unvollstaendigen Test vollstaendig."""
    maerkte = {"BTC/USD": _bars(700), "WINZ/USD": _bars(40, symbol="WINZ/USD")}
    ergebnis = cross_market_control(
        get("macross"), maerkte, "1d", train_bars=250, test_bars=120, embargo_bars=10,
    )
    nach_symbol = {lauf.symbol: lauf for lauf in ergebnis.laeufe}
    assert nach_symbol["BTC/USD"].testbar
    assert not nach_symbol["WINZ/USD"].testbar
    assert "zu wenig Historie" in nach_symbol["WINZ/USD"].grund
    assert "WINZ/USD" in ergebnis.table()


def test_korrelierte_maerkte_zaehlen_nicht_als_unabhaengige():
    """Wiederholung ist keine Bestaetigung (ADR-052)."""
    from qt.research.placebo import CrossMarketResult, MarketRun

    laeufe = [MarketRun(symbol=f"M{i}", sharpe=0.3, n_windows=3) for i in range(13)]
    hoch = CrossMarketResult(laeufe=laeufe, mittlere_korrelation=0.7)
    keine = CrossMarketResult(laeufe=laeufe, mittlere_korrelation=0.0)

    assert hoch.effektive_maerkte < 2.0
    assert keine.effektive_maerkte == pytest.approx(13.0)


def test_die_korrelation_erkennt_gleichlauf():
    n = 400
    rng = np.random.default_rng(9)
    schritte = rng.normal(0, 0.02, n)
    gleich = 100 * np.cumprod(1 + schritte)
    maerkte = {
        "A/USD": make_bars(n, "A/USD", "1d", prices=gleich),
        "B/USD": make_bars(n, "B/USD", "1d", prices=gleich * 3.0),
    }
    assert mean_pairwise_correlation(maerkte) == pytest.approx(1.0, abs=1e-6)

    maerkte["B/USD"] = make_bars(
        n, "B/USD", "1d", prices=100 * np.cumprod(1 + rng.normal(0, 0.02, n))
    )
    assert abs(mean_pairwise_correlation(maerkte)) < 0.3


def test_ohne_gemeinsames_fenster_gibt_es_keine_korrelation():
    """Zwei Maerkte ohne Ueberschneidung sind nicht vergleichbar -- nan, nicht 0."""
    a = make_bars(300, "A/USD", "1d")
    b = make_bars(300, "B/USD", "1d")
    versetzt = [
        type(bar)(
            symbol=bar.symbol, timeframe=bar.timeframe,
            ts=bar.ts + timedelta(days=5000), open=bar.open, high=bar.high,
            low=bar.low, close=bar.close, volume=bar.volume,
        )
        for bar in b
    ]
    assert math.isnan(mean_pairwise_correlation({"A/USD": a, "B/USD": versetzt}))


def test_die_effektive_marktzahl_ist_exakt_und_keine_naeherung():
    """Der eigentliche Fund aus ADR-055 -- und er widerlegt meine Vermutung.

    `n/(1+(n-1)*rho)` sieht nach einer Naeherung fuer gleich korrelierte
    Reihen aus. Sie ist exakt `n^2 / 1'C1`, also die effektive
    Stichprobengroesse eines gleichgewichteten Mittels, und gilt fuer **jede**
    Struktur. Geprueft wird das an einer ausgepraegten Blockstruktur, also
    genau dort, wo eine Gleichkorrelations-Naeherung auseinanderfallen muesste.
    """
    n = 600
    rng = np.random.default_rng(21)
    gemeinsam = rng.normal(0, 0.02, n)
    maerkte: dict[str, list] = {}
    # Block A: sechs Maerkte, die fast dasselbe tun.
    for i in range(6):
        r = 0.9 * gemeinsam + 0.1 * rng.normal(0, 0.02, n)
        maerkte[f"A{i}/USD"] = make_bars(
            n, f"A{i}/USD", "1d", prices=100 * np.cumprod(1 + r)
        )
    # Block B: sechs weitgehend eigenstaendige.
    for i in range(6):
        r = rng.normal(0, 0.02, n)
        maerkte[f"B{i}/USD"] = make_bars(
            n, f"B{i}/USD", "1d", prices=100 * np.cumprod(1 + r)
        )

    from qt.research.placebo import CrossMarketResult, MarketRun, correlation_matrix

    ergebnis = CrossMarketResult(
        laeufe=[MarketRun(symbol=s, sharpe=0.1, n_windows=2) for s in maerkte],
        mittlere_korrelation=mean_pairwise_correlation(maerkte),
        korrelationsmatrix=correlation_matrix(maerkte),
    )

    C = ergebnis.korrelationsmatrix
    n = C.shape[0]
    exakt = n**2 / C.sum()
    assert ergebnis.effektive_maerkte == pytest.approx(exakt, rel=1e-9), (
        "die Formel ist nicht exakt -- dann waere sie tatsaechlich nur eine "
        "Naeherung, und ADR-055 haette recht gehabt"
    )
    # Die Teilnahmequote misst etwas anderes und darf abweichen.
    assert ergebnis.unabhaengige_richtungen != pytest.approx(exakt, rel=1e-3)


def test_ohne_gemeinsames_fenster_gibt_es_keine_matrix():
    """Eigenwerte einer aus Fragmenten zusammengesetzten Matrix sind keine."""
    from qt.research.placebo import correlation_matrix

    assert correlation_matrix({"A/USD": make_bars(300, "A/USD", "1d")}) is None
    assert correlation_matrix({}) is None


def test_bei_unkorrelierten_maerkten_stimmen_beide_schaetzer_ueberein():
    """Gegenprobe: ohne Struktur darf der neue Schaetzer nichts erfinden."""
    n = 800
    rng = np.random.default_rng(5)
    maerkte = {
        f"U{i}/USD": make_bars(
            n, f"U{i}/USD", "1d", prices=100 * np.cumprod(1 + rng.normal(0, 0.02, n))
        )
        for i in range(6)
    }
    from qt.research.placebo import CrossMarketResult, MarketRun, correlation_matrix

    ergebnis = CrossMarketResult(
        laeufe=[MarketRun(symbol=s, sharpe=0.1) for s in maerkte],
        mittlere_korrelation=mean_pairwise_correlation(maerkte),
        korrelationsmatrix=correlation_matrix(maerkte),
    )
    assert ergebnis.unabhaengige_richtungen == pytest.approx(6.0, abs=0.6)
    assert ergebnis.effektive_maerkte == pytest.approx(6.0, abs=0.6)
