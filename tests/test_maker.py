"""Waeren die Orders eines Laufs passiv gefuellt worden?

Die Tests hier pruefen die Dreiteilung an Baren, deren Antwort man von Hand
ausrechnen kann -- und einen Fall, den die erste Fassung falsch gemacht
haette: eine Order im allerersten Bar hat keinen Vorgaenger und damit keinen
Signalpreis.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from qt.backtest.maker import klassifiziere, zusammenfassen
from qt.core.types import Bar, Fill

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def bar(i: int, o: float, h: float, lo: float, c: float, sym: str = "X/USD") -> Bar:
    ts = T0 + timedelta(days=i)
    return Bar(
        symbol=sym,
        timeframe="1d",
        ts=ts,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=1_000.0,
    )


def fill(i: int, qty: float) -> Fill:
    return Fill(
        symbol="X/USD",
        ts=T0 + timedelta(days=i),
        qty=qty,
        price=100.0,
        fee=0.0,
        slippage_cost=0.0,
    )


def test_kauf_dessen_limit_beim_open_schon_erreichbar_ist_zahlt_taker():
    """Bar 0 schliesst bei 100, Bar 1 oeffnet bei 99: das Kauflimit ist drin.

    Die Order geht durch -- aber sie nimmt Liquiditaet. Als Maker zaehlt sie
    nicht, und genau diese Unterscheidung ist der Punkt des Moduls.
    """
    bars = {"X/USD": [bar(0, 100, 101, 99, 100), bar(1, 99, 100, 98, 99)]}
    (q,) = klassifiziere([fill(1, +10)], bars)
    assert (q.marktnah, q.passiv_gefuellt, q.nicht_gefuellt) == (1, 0, 0)


def test_kauf_der_passiv_liegt_und_zurueckkommt_ist_ein_maker_fill():
    """Bar 1 oeffnet ueber dem Limit, faellt im Tagesverlauf aber darunter."""
    bars = {"X/USD": [bar(0, 100, 101, 99, 100), bar(1, 102, 103, 99, 101)]}
    (q,) = klassifiziere([fill(1, +10)], bars)
    assert (q.marktnah, q.passiv_gefuellt, q.nicht_gefuellt) == (0, 1, 0)
    assert q.maker_quote == 1.0


def test_kauf_dem_der_kurs_davonlaeuft_wird_nie_gefuellt():
    """Der Fall, um den es geht: Trendfolge kauft, der Kurs kommt nicht zurueck."""
    bars = {"X/USD": [bar(0, 100, 101, 99, 100), bar(1, 102, 105, 101, 104)]}
    (q,) = klassifiziere([fill(1, +10)], bars)
    assert (q.marktnah, q.passiv_gefuellt, q.nicht_gefuellt) == (0, 0, 1)
    assert q.ausfallquote == 1.0


def test_verkauf_spiegelt_den_kauf():
    """Beim Verkauf liegt das passive Limit oben, nicht unten."""
    bars = {"X/USD": [bar(0, 100, 101, 99, 100), bar(1, 98, 101, 97, 99)]}
    (q,) = klassifiziere([fill(1, -10)], bars)
    assert (q.marktnah, q.passiv_gefuellt, q.nicht_gefuellt) == (0, 1, 0)

    # Kurs faellt weg, das Verkaufslimit wird nie erreicht.
    bars_weg = {"X/USD": [bar(0, 100, 101, 99, 100), bar(1, 98, 99, 95, 96)]}
    (q2,) = klassifiziere([fill(1, -10)], bars_weg)
    assert (q2.marktnah, q2.passiv_gefuellt, q2.nicht_gefuellt) == (0, 0, 1)


def test_fill_im_ersten_bar_hat_keinen_signalpreis_und_wird_uebersprungen():
    """Ohne Vorgaengerbar gibt es kein Limit -- raten waere hier falsch."""
    bars = {"X/USD": [bar(0, 100, 101, 99, 100)]}
    assert klassifiziere([fill(0, +10)], bars) == []


def test_notional_zaehlt_mit_dem_limit_nicht_mit_dem_fillpreis():
    """Sonst haengt der Gegenwert am Ausfuehrungspreis, den es gar nicht gab."""
    # Bar 0 schliesst bei 50 -> Limit 50. Bar 1 oeffnet bei 60 (also passiv)
    # und faellt auf 40, das Limit wird erreicht. Der Fill-Preis im Fill-Objekt
    # ist 100 und darf im Gegenwert nicht auftauchen.
    bars = {"X/USD": [bar(0, 100, 101, 99, 50), bar(1, 60, 61, 40, 55)]}
    (q,) = klassifiziere([fill(1, +10)], bars)
    assert q.notional_passiv == pytest.approx(500.0)  # 10 * 50, nicht 10 * 100


def test_zusammenfassen_addiert_ueber_symbole():
    bars = {
        "A/USD": [
            bar(0, 100, 101, 99, 100, "A/USD"),
            bar(1, 102, 105, 101, 104, "A/USD"),
        ],
        "B/USD": [
            bar(0, 100, 101, 99, 100, "B/USD"),
            bar(1, 102, 103, 99, 101, "B/USD"),
        ],
    }
    fills = [
        Fill(symbol="A/USD", ts=T0 + timedelta(days=1), qty=10, price=1, fee=0,
             slippage_cost=0),
        Fill(symbol="B/USD", ts=T0 + timedelta(days=1), qty=10, price=1, fee=0,
             slippage_cost=0),
    ]
    gesamt = zusammenfassen(klassifiziere(fills, bars))
    assert gesamt.n == 2
    assert gesamt.nicht_gefuellt == 1  # A laeuft davon
    assert gesamt.passiv_gefuellt == 1  # B kommt zurueck


def test_leere_liste_wirft_statt_nan_zurueckzugeben():
    with pytest.raises(ValueError, match="Keine Quoten"):
        zusammenfassen([])
