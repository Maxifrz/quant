"""Tests der Round-Trip-Verdichtung.

Diese Datei rechnet die Ergebnisse **von Hand** nach, statt sie gegen den Code
zu vergleichen, der sie erzeugt hat. Ein Test, der `net_pnl` gegen dieselbe
Formel prueft, die `net_pnl` berechnet, prueft nur, dass Python rechnen kann.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from qt.backtest.roundtrips import RoundTrip, round_trips, summary_table
from qt.core.types import Fill

TAG = timedelta(days=1)
START = datetime(2020, 1, 1, tzinfo=timezone.utc)


class _Ergebnis:
    """Das Minimum, das `round_trips` von einem BacktestResult braucht."""

    def __init__(self, fills: list[Fill], preise: list[float] | None = None) -> None:
        self.fills = fills
        n = len(preise) if preise else 0
        self.equity = pd.DataFrame(
            {
                "ts": [START + i * TAG for i in range(n)],
                "price_BTC/USD": preise or [],
            }
        )


def _fill(tag: int, qty: float, price: float, fee: float = 0.0) -> Fill:
    return Fill(
        symbol="BTC/USD",
        ts=START + tag * TAG,
        qty=qty,
        price=price,
        fee=fee,
        slippage_cost=0.0,
    )


# ---------------------------------------------------------------------------
# Positionsverlauf
# ---------------------------------------------------------------------------


def test_kauf_und_verkauf_ergeben_einen_round_trip():
    trades = round_trips(_Ergebnis([_fill(0, 1.0, 100.0), _fill(3, -1.0, 110.0)]))

    assert len(trades) == 1
    t = trades[0]
    assert t.direction == 1
    assert t.entry_price == 100.0
    assert t.exit_price == 110.0
    # Von Hand: 1 Stueck fuer 100 gekauft, fuer 110 verkauft -> 10.
    assert t.gross_pnl == pytest.approx(10.0)
    assert t.net_pnl == pytest.approx(10.0)
    assert t.return_pct == pytest.approx(0.10)


def test_eine_am_ende_offene_position_ist_kein_round_trip():
    """Sie mitzuzaehlen hiesse, Unrealisiertes als realisiert auszuweisen."""
    trades = round_trips(_Ergebnis([_fill(0, 1.0, 100.0)]))
    assert trades == []


def test_teilausstiege_werden_zu_einem_trade_zusammengefasst():
    trades = round_trips(
        _Ergebnis(
            [
                _fill(0, 2.0, 100.0),
                _fill(2, -1.0, 110.0),
                _fill(4, -1.0, 120.0),
            ]
        )
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.qty == pytest.approx(2.0)
    # Von Hand: -200 + 110 + 120 = 30.
    assert t.gross_pnl == pytest.approx(30.0)
    assert t.exit_price == pytest.approx(115.0)


def test_ein_vorzeichenwechsel_schliesst_und_eroeffnet():
    """Ein Fill, der von long nach short dreht, ist zwei Trades.

    Wuerde er als einer gezaehlt, verschmoelzen zwei Entscheidungen zu einer,
    und die Trefferquote waere systematisch zu niedrig.
    """
    trades = round_trips(
        _Ergebnis(
            [
                _fill(0, 1.0, 100.0),
                _fill(2, -2.0, 110.0),  # schliesst long, eroeffnet short
                _fill(4, 1.0, 105.0),  # schliesst short
            ]
        )
    )
    assert len(trades) == 2
    lang, kurz = trades
    assert lang.direction == 1
    assert kurz.direction == -1
    # Long: -100 + 110 = 10. Short: +110 - 105 = 5.
    assert lang.gross_pnl == pytest.approx(10.0)
    assert kurz.gross_pnl == pytest.approx(5.0)


def test_die_gebuehr_eines_drehenden_fills_wird_geteilt():
    """Sonst traegt einer der beiden Trades Kosten, die er nicht verursacht hat."""
    trades = round_trips(
        _Ergebnis(
            [
                _fill(0, 1.0, 100.0, fee=1.0),
                _fill(2, -2.0, 110.0, fee=6.0),  # haelftig auf beide
                _fill(4, 1.0, 105.0, fee=2.0),
            ]
        )
    )
    lang, kurz = trades
    assert lang.fees == pytest.approx(1.0 + 3.0)
    assert kurz.fees == pytest.approx(3.0 + 2.0)
    assert lang.net_pnl == pytest.approx(10.0 - 4.0)
    assert kurz.net_pnl == pytest.approx(5.0 - 5.0)


# ---------------------------------------------------------------------------
# MAE und MFE
# ---------------------------------------------------------------------------


def test_mae_und_mfe_kommen_aus_dem_kursverlauf():
    """Ein Trade, der zwischendurch tief im Minus stand, muss das zeigen.

    Ohne diese Spalten sieht ein Trade, der 20% unter Wasser war und flach
    schloss, genauso aus wie einer, der nie im Minus stand.
    """
    preise = [100.0, 80.0, 130.0, 100.0]
    trades = round_trips(
        _Ergebnis([_fill(0, 1.0, 100.0), _fill(3, -1.0, 100.0)], preise)
    )
    t = trades[0]
    assert t.mae == pytest.approx(-0.20)
    assert t.mfe == pytest.approx(0.30)
    assert t.net_pnl == pytest.approx(0.0), "flach geschlossen"


def test_ohne_kursreihe_gibt_es_nan_und_nicht_null():
    """"Nicht gemessen" und "war nie im Minus" sind verschiedene Aussagen."""
    trades = round_trips(_Ergebnis([_fill(0, 1.0, 100.0), _fill(3, -1.0, 110.0)]))
    assert math.isnan(trades[0].mae)
    assert math.isnan(trades[0].mfe)


def test_mae_bei_short_zaehlt_steigende_kurse_als_verlust():
    preise = [100.0, 120.0, 90.0, 95.0]
    trades = round_trips(
        _Ergebnis([_fill(0, -1.0, 100.0), _fill(3, 1.0, 95.0)], preise)
    )
    t = trades[0]
    assert t.direction == -1
    assert t.mae == pytest.approx(-0.20), "der Anstieg auf 120 ist der Buchverlust"
    assert t.mfe == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Kennzahlen
# ---------------------------------------------------------------------------


def test_kostenanteil_ueber_eins_heisst_gebuehren_haben_gedreht():
    t = RoundTrip(
        symbol="BTC/USD",
        entry_ts=START,
        exit_ts=START + TAG,
        direction=1,
        qty=1.0,
        entry_price=100.0,
        exit_price=101.0,
        bars_held=1,
        gross_pnl=1.0,
        fees=3.0,
        net_pnl=-2.0,
        entry_notional=100.0,
        mae=0.0,
        mfe=0.01,
    )
    assert t.cost_share == pytest.approx(3.0)
    assert not t.won


def test_die_tabelle_trennt_gewinner_und_verlierer():
    trades = round_trips(
        _Ergebnis(
            [
                _fill(0, 1.0, 100.0),
                _fill(1, -1.0, 120.0),  # Gewinner
                _fill(2, 1.0, 120.0),
                _fill(3, -1.0, 110.0),  # Verlierer
            ]
        )
    )
    text = summary_table(trades)
    assert "Gewinner" in text and "Verlierer" in text
    assert "Anzahl" in text
    # Der Konzentrationshinweis ist der eigentliche Zweck der Tabelle.
    assert "Stichprobe" in text


def test_leere_liste_sagt_das_statt_zu_rechnen():
    assert "Keine abgeschlossenen" in summary_table([])


def test_trades_sind_chronologisch():
    trades = round_trips(
        _Ergebnis(
            [
                _fill(4, 1.0, 100.0),
                _fill(5, -1.0, 110.0),
                _fill(0, 1.0, 90.0),
                _fill(1, -1.0, 95.0),
            ]
        )
    )
    assert [t.entry_ts for t in trades] == sorted(t.entry_ts for t in trades)
