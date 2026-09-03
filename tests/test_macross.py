"""Tests fuer die Durchschnittskreuzung.

Die Strategie ist absichtlich die simpelste im Repo -- sie hat zwei Parameter
und keinen Zustand. Genau deshalb sind die Tests hier nicht auf Logikfehler
gerichtet (davon gibt es kaum Platz), sondern auf die zwei Zusagen, die sie
gegenueber den anderen Strategien einloest: **kein Zustand** und **kein
Blick nach vorn**.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from qt.core.clock import BacktestClock
from qt.features.registry import FeatureStore
from qt.strategy.library.macross import MovingAverageCross
from tests.conftest import make_bars


def _lauf(closes, **params):
    prices = np.asarray(closes, dtype=float)
    bars = make_bars(len(prices), "BTC/USD", "1d", prices=prices)
    clock = BacktestClock(bars[0].ts)
    store = FeatureStore(clock, maxlen=3000)
    strategy = MovingAverageCross(["BTC/USD"], "1d", **params)

    gewichte = []
    for bar in bars:
        clock.advance(bar.close_ts)
        store.on_bar(bar)
        gewichte.append(strategy.on_bar("BTC/USD", store))
    return gewichte


# --------------------------------------------------------------------------
# Vertrag
# --------------------------------------------------------------------------


def test_schnell_muss_kleiner_sein_als_langsam():
    """Vertauscht waere die Kreuzung eine Umkehraussage mit falschem Vorzeichen.

    Der Fehler faellt sonst nirgends auf: die Strategie liefe durch und
    handelte konsequent das Gegenteil dessen, was der Name verspricht.
    """
    with pytest.raises(ValueError, match="muss kleiner sein"):
        MovingAverageCross(["BTC/USD"], "1d", fast=50, slow=10)
    with pytest.raises(ValueError, match="muss kleiner sein"):
        MovingAverageCross(["BTC/USD"], "1d", fast=50, slow=50)


def test_vor_dem_warmup_gibt_es_keine_meinung():
    """`nan` heisst "keine Meinung" und ist etwas anderes als 0.0."""
    gewichte = _lauf([100.0 + i for i in range(20)], fast=5, slow=50)
    assert all(math.isnan(w) for w in gewichte)


def test_steigender_markt_ergibt_long():
    gewichte = _lauf([100.0 * 1.01**i for i in range(200)], fast=5, slow=50)
    assert gewichte[-1] == 1.0


def test_fallender_markt_ergibt_flach_statt_short():
    """In einem Markt mit starker Aufwaertsdrift ist die Gegenrichtung kein
    neutraler Zustand, sondern eine Wette gegen den staerksten Effekt im
    Datensatz."""
    gewichte = _lauf([100.0 * 0.99**i for i in range(200)], fast=5, slow=50)
    assert gewichte[-1] == 0.0
    assert all(w >= 0 for w in gewichte if not math.isnan(w))


def test_mit_allow_short_wird_aus_flach_ein_short():
    gewichte = _lauf(
        [100.0 * 0.99**i for i in range(200)], fast=5, slow=50, allow_short=True
    )
    assert gewichte[-1] == -1.0


# --------------------------------------------------------------------------
# Die zwei Zusagen
# --------------------------------------------------------------------------


def test_die_strategie_ist_zustandslos():
    """Dasselbe Fenster ergibt dieselbe Antwort, egal wie man dorthin kam.

    Das ist der Grund, warum ein Walk-Forward-Fenster hier nichts anders
    behandeln kann als der Gesamtlauf: es gibt nichts, was ueber eine
    Fenstergrenze getragen wuerde.
    """
    rng = np.random.default_rng(5)
    closes = list(100 + np.cumsum(rng.normal(0.1, 1.5, 400)))

    # Einmal ueber die ganze Reihe, einmal nur ueber den zweiten Teil --
    # der letzte Bar sieht in beiden Faellen dasselbe 50er-Fenster.
    voll = _lauf(closes, fast=5, slow=50)
    teil = _lauf(closes[-120:], fast=5, slow=50)
    assert voll[-1] == teil[-1]


def test_zukuenftige_bars_aendern_kein_vergangenes_signal():
    """Der Lookahead-Test: die Vergangenheit bleibt bitgleich."""
    rng = np.random.default_rng(11)
    closes = list(100 + np.cumsum(rng.normal(0.05, 1.2, 500)))

    kurz = _lauf(closes[:300], fast=10, slow=50)
    lang = _lauf(closes, fast=10, slow=50)
    assert [repr(w) for w in kurz] == [repr(w) for w in lang[:300]]


def test_zwei_identische_laeufe_ergeben_identische_gewichte():
    rng = np.random.default_rng(23)
    closes = list(100 + np.cumsum(rng.normal(0.05, 1.2, 300)))
    assert [repr(w) for w in _lauf(closes)] == [repr(w) for w in _lauf(closes)]


def test_der_warmup_folgt_der_langsamen_linie():
    """Sonst rechnet die Engine mit einem Vorlauf, den die Strategie nicht hat."""
    for slow in (50, 100, 200):
        s = MovingAverageCross(["BTC/USD"], "1d", fast=5, slow=slow)
        assert s.warmup_bars == slow + 2


# --------------------------------------------------------------------------
# Der Effekt, um den es geht
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_die_strategie_ist_seltener_im_markt_als_buy_and_hold():
    """Ihr Beitrag ist nicht ein besseres Signal, sondern weniger Teilnahme
    an Abstuerzen. Wer sie an der Gesamtrendite misst, misst das Falsche."""
    from qt.data.store import read_bars, to_bars

    try:
        df = read_bars("BTC/USD", "1d")
    except FileNotFoundError:  # pragma: no cover -- ohne Datenbestand
        pytest.skip("keine 1d-Bars im Store")

    from qt.backtest.engine import run_backtest
    from qt.backtest.metrics import compute

    bars = {"BTC/USD": to_bars("BTC/USD", "1d", df)}
    result = run_backtest(MovingAverageCross(["BTC/USD"], "1d"), bars)
    metrics = compute(
        result.equity.set_index("ts")["equity"],
        "1d",
        n_trades=result.n_trades,
        fees_paid=result.fees_paid,
    )
    assert 0.3 < metrics.time_in_market < 0.8, "rund die Haelfte der Zeit flach"

    close = df["close"].to_numpy()
    bh_dd = float((close / np.maximum.accumulate(close) - 1).min())
    assert metrics.max_drawdown > bh_dd, "der Drawdown muss kleiner sein als B&H"
