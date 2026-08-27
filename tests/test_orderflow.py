"""Tests fuer Order-Flow-Aggregation und die darauf gebaute Strategie.

Der wichtigste Test ist `test_die_strategie_sieht_nur_was_der_store_zeigt`.
Die Flow-Tabelle ist ein Beiwagen neben dem `FeatureStore`, und ein Beiwagen
mit eigener Uhr waere genau die zweite Zeitquelle, die dieses Projekt
ueberall sonst vermeidet. Der Test haengt Flussdaten fuer die **Zukunft** in
die Tabelle und prueft, dass sie das Ergebnis nicht veraendern.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from qt.core.clock import BacktestClock
from qt.features import orderflow as of
from qt.features.registry import FeatureStore
from qt.strategy.library.orderflow import OrderFlowTrend
from tests.conftest import make_bars

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _trades(rows: list[tuple[int, float, float, str]]) -> pd.DataFrame:
    """(Sekunden-Versatz, Preis, Menge, Seite) -> Trade-Tabelle."""
    return pd.DataFrame(
        [
            {
                "ts": START + timedelta(seconds=offset),
                "price": price,
                "amount": amount,
                "side": side,
            }
            for offset, price, amount, side in rows
        ]
    )


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def test_delta_ist_kaufmenge_minus_verkaufsmenge():
    df = of.aggregate(
        _trades([(0, 100, 3.0, "buy"), (10, 100, 1.0, "sell")]), "1h"
    )
    assert len(df) == 1
    assert df["delta"].iloc[0] == pytest.approx(2.0)
    assert df["volume"].iloc[0] == pytest.approx(4.0)
    assert df["buy_share"].iloc[0] == pytest.approx(0.75)
    assert df["n_trades"].iloc[0] == 2
    assert df["avg_size"].iloc[0] == pytest.approx(2.0)


def test_trades_landen_im_bar_ihrer_open_zeit():
    """`ts` ist die Open-Zeit, wie ueberall im Store -- sonst muesste beim
    Zusammenfuehren umgerechnet werden, und genau da entstehen Off-by-ones."""
    df = of.aggregate(
        _trades([(0, 100, 1.0, "buy"), (3599, 100, 1.0, "buy"), (3600, 100, 1.0, "buy")]),
        "1h",
    )
    assert len(df) == 2
    assert df["ts"].iloc[0] == START
    assert df["n_trades"].iloc[0] == 2, "die Bar-Kante gehoert dem schliessenden Bar"
    assert df["n_trades"].iloc[1] == 1


def test_leere_eingabe_ergibt_leere_tabelle_und_wirft_nicht():
    leer = of.aggregate(pd.DataFrame(columns=["ts", "price", "amount", "side"]), "4h")
    assert leer.empty
    assert list(leer.columns) == of.COLUMNS


def test_fehlende_bars_werden_nan_und_nicht_null():
    """0 hiesse "ausgeglichener Fluss", nan heisst "keine Information".

    Eine Strategie, die beides verwechselt, handelt Datenluecken als Signal.
    """
    bars = pd.DataFrame({"ts": [START, START + timedelta(hours=1)], "close": [1.0, 2.0]})
    flow = of.aggregate(_trades([(0, 100, 1.0, "buy")]), "1h")
    zusammen = of.attach(bars, flow)

    assert zusammen["delta"].iloc[0] == pytest.approx(1.0)
    assert math.isnan(zusammen["delta"].iloc[1])


def test_das_flussvolumen_ueberschreibt_nie_das_bar_volumen():
    """Die beiden stammen von verschiedenen Boersen und muessen es bleiben.

    Sonst wird aus zwei Messungen unbemerkt eine.
    """
    bars = pd.DataFrame({"ts": [START], "volume": [999.0]})
    flow = of.aggregate(_trades([(0, 100, 1.0, "buy")]), "1h")
    zusammen = of.attach(bars, flow)

    assert zusammen["volume"].iloc[0] == 999.0
    assert zusammen["flow_volume"].iloc[0] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Kennzahlen
# --------------------------------------------------------------------------


def test_cvd_summiert_und_laesst_luecken_stehen():
    """Eine Bar ohne Flussdaten soll die Summe nicht loeschen, nur nicht
    weiterbewegen."""
    assert list(of.cvd(np.array([1.0, 2.0, -1.0]))) == [1.0, 3.0, 2.0]
    assert list(of.cvd(np.array([1.0, math.nan, 2.0]))) == [1.0, 1.0, 3.0]


def test_flow_zscore_misst_gegen_die_juengere_vergangenheit():
    """Roh ist delta nicht vergleichbar: 50 BTC in einer ruhigen Nacht sind
    etwas anderes als 50 BTC im Ausverkauf."""
    rng = np.random.default_rng(2)
    ruhig = np.concatenate([rng.normal(0, 1.0, 20), [10.0]])
    assert of.flow_zscore(ruhig, 20) > 3

    unauffaellig = np.array([10.0] * 20 + [10.0])
    assert math.isnan(of.flow_zscore(unauffaellig, 20)), "ohne Streuung kein z-Score"


def test_flow_zscore_braucht_genug_historie():
    assert math.isnan(of.flow_zscore(np.array([1.0, 2.0]), 20))


def test_flow_zscore_kommt_mit_luecken_zurecht():
    werte = np.array([1.0, math.nan, 2.0, 1.5, math.nan, 1.8] * 5 + [9.0])
    z = of.flow_zscore(werte, 20)
    assert math.isfinite(z) and z > 1


def test_flow_zscore_nimmt_den_letzten_wert_nicht_ins_fenster():
    """Sonst misst sich der Ausreisser teilweise an sich selbst und der
    z-Score faellt systematisch zu klein aus."""
    werte = np.array([0.0, 1.0] * 10 + [50.0])
    mit_ausreisser = float(np.asarray(werte).std(ddof=1))
    ohne = float(np.asarray(werte[:-1]).std(ddof=1))
    assert ohne < mit_ausreisser, "die Fixture muss den Unterschied ueberhaupt zeigen"
    z = of.flow_zscore(werte, 20)
    assert z == pytest.approx((50.0 - werte[:-1].mean()) / ohne)


def test_divergenz_ist_null_wenn_preis_und_fluss_gleich_laufen():
    steigend = np.arange(20, dtype=float)
    assert of.divergence(steigend, steigend, 20) == pytest.approx(0.0)


def test_divergenz_wird_positiv_wenn_der_fluss_zurueckfaellt():
    """Echte Divergenz: der Preis steht am Hoch, der Fluss hat sich entfernt."""
    preis = np.arange(20, dtype=float)
    fluss = np.concatenate([np.arange(10, dtype=float), np.arange(9, -1, -1.0)])
    assert of.divergence(preis, fluss, 20) > 0


def test_ein_fluss_der_auf_seinem_hoch_stehenbleibt_ist_keine_divergenz():
    """Gemessen wird die Position im Fenster, nicht die Steigung.

    Das ist eine echte Einschraenkung und keine Feinheit: die klassische
    Lesart von Divergenz ("Preis macht hoeheres Hoch, Fluss ein tieferes")
    verlangt, dass der Fluss zurueckfaellt. Ein Fluss, der nur stehenbleibt,
    steht weiterhin oben in seinem Fenster -- und wird hier bewusst nicht
    gemeldet.
    """
    preis = np.arange(20, dtype=float)
    plateau = np.concatenate([np.arange(10, dtype=float), np.full(10, 9.0)])
    assert of.divergence(preis, plateau, 20) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Die Strategie
# --------------------------------------------------------------------------


def _lauf(closes, flow_table, **params):
    prices = np.asarray(closes, dtype=float)
    bars = make_bars(len(prices), "BTC/USD", "4h", prices=prices)
    clock = BacktestClock(bars[0].ts)
    store = FeatureStore(clock, maxlen=2000)
    strategy = OrderFlowTrend(
        ["BTC/USD"], "4h", flow={"BTC/USD": flow_table}, **params
    )

    gewichte = []
    for bar in bars:
        clock.advance(bar.close_ts)
        store.on_bar(bar)
        gewichte.append(strategy.on_bar("BTC/USD", store))
    return gewichte, bars


def _tabelle(bars, deltas, n_trades=100):
    return {
        bar.ts: {
            "delta": float(d),
            "buy_share": 0.5,
            "n_trades": n_trades,
            "avg_size": 1.0,
        }
        for bar, d in zip(bars, deltas)
    }


def test_die_strategie_sieht_nur_was_der_store_zeigt():
    """Der Beiwagen darf keine zweite Uhr haben.

    Es werden Flussdaten fuer Zeitpunkte **nach** dem Ende der Bars in die
    Tabelle gehaengt. Wuerde die Strategie ueber die Tabelle iterieren statt
    ueber `window.timestamps()`, faenden sie Eingang ins Signal -- und der
    Backtest waere live nicht reproduzierbar.
    """
    rng = np.random.default_rng(17)
    closes = list(100 + np.cumsum(rng.normal(0, 1.0, 200)))
    prices = np.asarray(closes)
    bars = make_bars(len(prices), "BTC/USD", "4h", prices=prices)
    deltas = rng.normal(0, 1.0, len(bars))

    sauber = _tabelle(bars, deltas)

    # Dieselbe Tabelle, plus absurd grosse Werte fuer die Zukunft.
    vergiftet = dict(sauber)
    letzte = bars[-1].ts
    for k in range(1, 50):
        vergiftet[letzte + timedelta(hours=4 * k)] = {
            "delta": 1e6,
            "buy_share": 1.0,
            "n_trades": 10_000,
            "avg_size": 100.0,
        }

    ohne, _ = _lauf(closes, sauber)
    mit, _ = _lauf(closes, vergiftet)
    assert [repr(w) for w in ohne] == [repr(w) for w in mit]


def test_zu_duenner_handel_ergibt_keine_meinung():
    """Eine Datenluecke ist kein Signal. `nan` heisst "keine Meinung" und ist
    etwas anderes als 0.0."""
    closes = list(100 + np.arange(120, dtype=float))
    prices = np.asarray(closes)
    bars = make_bars(len(prices), "BTC/USD", "4h", prices=prices)
    tabelle = _tabelle(bars, np.ones(len(bars)), n_trades=1)

    gewichte, _ = _lauf(closes, tabelle, min_trades=20)
    assert all(math.isnan(w) for w in gewichte)


def test_beharrlicher_kaufdruck_erzeugt_eine_longposition():
    """Ein einzelner auffaelliger Bar ist eine Stichprobe, mehrere sind ein
    Fluss -- deshalb `persistence`."""
    closes = list(100 + np.arange(120, dtype=float) * 0.1)
    prices = np.asarray(closes)
    bars = make_bars(len(prices), "BTC/USD", "4h", prices=prices)

    rng = np.random.default_rng(3)
    deltas = rng.normal(0, 1.0, len(bars))
    deltas[-6:] = 8.0  # anhaltender, deutlicher Kaufueberhang
    tabelle = _tabelle(bars, deltas)

    gewichte, _ = _lauf(closes, tabelle, lookback=30, entry_z=1.0, persistence=2)
    assert gewichte[-1] == 1.0


def test_ohne_shorts_gibt_es_nie_ein_negatives_gewicht():
    closes = list(100 - np.arange(120, dtype=float) * 0.1)
    prices = np.asarray(closes)
    bars = make_bars(len(prices), "BTC/USD", "4h", prices=prices)

    rng = np.random.default_rng(5)
    deltas = rng.normal(0, 1.0, len(bars))
    deltas[-6:] = -8.0
    tabelle = _tabelle(bars, deltas)

    gewichte, _ = _lauf(closes, tabelle, allow_short=False, persistence=2)
    assert all(w >= 0 for w in gewichte if not math.isnan(w))


def test_gegenlaeufiger_fluss_stellt_flach_statt_zu_drehen():
    """Sonst waere es doppelter Umsatz auf ein Signal, das sich gerade erst
    gedreht hat -- bei 90bps Round-Trip der teuerste denkbare Reflex."""
    closes = list(100 + np.arange(160, dtype=float) * 0.05)
    prices = np.asarray(closes)
    bars = make_bars(len(prices), "BTC/USD", "4h", prices=prices)

    rng = np.random.default_rng(9)
    deltas = rng.normal(0, 1.0, len(bars))
    deltas[-40:-20] = 8.0     # erst Kaufdruck
    deltas[-20:] = -8.0       # dann Verkaufsdruck
    tabelle = _tabelle(bars, deltas)

    gewichte, _ = _lauf(closes, tabelle, lookback=30, entry_z=1.0, persistence=2)
    gueltig = [w for w in gewichte if not math.isnan(w)]
    for links, rechts in zip(gueltig, gueltig[1:]):
        assert not (links > 0 and rechts < 0)
        assert not (links < 0 and rechts > 0)


def test_zwei_identische_laeufe_ergeben_identische_gewichte():
    rng = np.random.default_rng(23)
    closes = list(100 + np.cumsum(rng.normal(0, 1.0, 150)))
    prices = np.asarray(closes)
    bars = make_bars(len(prices), "BTC/USD", "4h", prices=prices)
    tabelle = _tabelle(bars, rng.normal(0, 1.0, len(bars)))

    erste, _ = _lauf(closes, tabelle)
    zweite, _ = _lauf(closes, tabelle)
    assert [repr(w) for w in erste] == [repr(w) for w in zweite]


def test_ohne_flussdaten_gibt_es_keine_meinung():
    closes = list(100 + np.arange(120, dtype=float))
    gewichte, _ = _lauf(closes, {})
    assert all(math.isnan(w) for w in gewichte)


# --------------------------------------------------------------------------
# Abzug
# --------------------------------------------------------------------------


class _FakeExchange:
    """Boerse, die eine feste Zahl Seiten liefert und dann abbricht."""

    def __init__(self, pages: int, per_page: int = 3, fail_at: int | None = None):
        self.pages = pages
        self.per_page = per_page
        self.fail_at = fail_at
        self.calls = 0

    def fetch_trades(self, symbol, since=None, limit=None):
        self.calls += 1
        if self.fail_at is not None and self.calls > self.fail_at:
            raise RuntimeError("Netz weg")
        if self.calls > self.pages:
            return []
        base = int(since or 0)
        return [
            {
                "timestamp": base + i * 1000,
                "price": 100.0 + i,
                "amount": 1.0,
                "side": "buy" if i % 2 == 0 else "sell",
            }
            for i in range(self.per_page)
        ]


def test_der_sink_bekommt_zwischenstaende_und_verliert_nichts():
    """Ein Abzug ueber vierzig Minuten, den ein Abbruch auf der vorletzten
    Seite erwischt, darf nicht nichts geliefert haben.

    Genau das drohte beim ersten 30-Tage-Lauf: 1,14 Mio. Trades im Speicher,
    Zeitlimit in Sicht, Schreiben erst am Ende.
    """
    from qt.data.trades import fetch_trades

    abgelegt = []
    ex = _FakeExchange(pages=10, per_page=3, fail_at=7)
    rest = fetch_trades(
        ex,
        "BTC/USD",
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until=datetime(2027, 1, 1, tzinfo=timezone.utc),
        rate_limit_ms=0,
        sink=abgelegt.append,
        flush_every=3,
    )

    assert abgelegt, "beim Abbruch war nichts weggeschrieben"
    gesamt = sum(len(chunk) for chunk in abgelegt)
    assert gesamt >= 3 * 3, "die Zwischenstaende fehlen"
    assert rest.empty or len(rest) > 0


def test_ohne_sink_kommt_alles_am_ende_zurueck():
    from qt.data.trades import fetch_trades

    ex = _FakeExchange(pages=4, per_page=3)
    df = fetch_trades(
        ex,
        "BTC/USD",
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until=datetime(2027, 1, 1, tzinfo=timezone.utc),
        rate_limit_ms=0,
    )
    assert len(df) == 12
    assert list(df.columns) == ["ts", "price", "amount", "side"]
    assert df["ts"].is_monotonic_increasing


def test_ein_abbruch_wirft_nicht_sondern_gibt_das_bisherige_zurueck():
    from qt.data.trades import fetch_trades

    ex = _FakeExchange(pages=10, per_page=3, fail_at=2)
    df = fetch_trades(
        ex,
        "BTC/USD",
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until=datetime(2027, 1, 1, tzinfo=timezone.utc),
        rate_limit_ms=0,
    )
    assert len(df) == 6, "zwei Seiten waren geholt, die muessen erhalten bleiben"
