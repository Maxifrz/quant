"""Tests fuer den Paper-Tick: Crash-Sicherheit vor allem anderen.

Der wichtigste Test ist `test_ueberlebt_neustart_zwischen_jedem_bar`. Diese
Umgebung hat in derselben Sitzung mehrfach demonstriert, dass ein Container
mitten in einem mehrstuendigen Hintergrundlauf verschwinden kann. Ein
Paper-Konto, das Wochen laufen soll, muss diese Eigenschaft nicht nur
"meistens" haben, sondern beweisbar: derselbe Datensatz, einmal in einem
Rutsch und einmal mit einem erzwungenen Neustart nach jedem einzelnen Bar,
muss bitgleich enden.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qt.data.store import write_bars
from qt.live import killswitch
from qt.live.runner import run_paper_tick
from qt.live.state import PaperState, state_path
from qt.portfolio.risk import RiskConfig
from qt.report.daily import render
from qt.strategy.library.macross import MovingAverageCross
from tests.conftest import make_bars


def _schreibe(tmp_path: Path, symbol: str, bars: list) -> None:
    df = pd.DataFrame(
        [
            {
                "ts": b.ts,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
    )
    write_bars(symbol, "1d", df, data_dir=tmp_path)


def _verschieben(bars, ab_ts):
    """Eine Bar-Liste zeitlich anschliessen, Preise unveraendert."""
    offset = ab_ts - bars[0].ts
    return [
        b.__class__(
            symbol=b.symbol,
            timeframe=b.timeframe,
            ts=b.ts + offset,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
        )
        for b in bars
    ]


def _factory():
    return MovingAverageCross(["BTC/USD"], "1d", fast=5, slow=20, allow_short=False)


def _historie(warmup_n=30, trend_n=30, warmup_preis=100.0, trend_rate=0.03):
    warmup = make_bars(warmup_n, "BTC/USD", "1d", prices=np.full(warmup_n, warmup_preis))
    trend_preise = warmup_preis * np.cumprod(1 + np.full(trend_n, trend_rate))
    trend = make_bars(trend_n, "BTC/USD", "1d", prices=trend_preise)
    trend = _verschieben(trend, warmup[-1].close_ts)
    return warmup, trend


# --------------------------------------------------------------------------
# Grundverhalten
# --------------------------------------------------------------------------


def test_frisches_konto_startet_flach(tmp_path):
    """Kein rueckwirkendes Handeln der gesamten Historie beim ersten Tick.

    Ohne diese Eigenschaft wuerde ein neues Paper-Konto sofort Hunderte Fills
    ausloesen -- das Gegenteil von "beobachten, was ab jetzt passiert".
    """
    warmup, _ = _historie()
    _schreibe(tmp_path, "BTC/USD", warmup)

    report = run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False
    )
    assert report.new_bars == 0
    assert report.new_fills == []

    state = PaperState.load(state_path("macross", ["BTC/USD"], "1d", tmp_path))
    assert state.cash == pytest.approx(100_000.0)


def test_ein_zweiter_tick_ohne_neue_bars_aendert_nichts(tmp_path):
    warmup, _ = _historie()
    _schreibe(tmp_path, "BTC/USD", warmup)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False)

    vorher = PaperState.load(state_path("macross", ["BTC/USD"], "1d", tmp_path))
    report = run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False
    )
    nachher = PaperState.load(state_path("macross", ["BTC/USD"], "1d", tmp_path))

    assert report.new_bars == 0
    assert vorher.cash == nachher.cash
    assert vorher.last_processed_ts == nachher.last_processed_ts


def test_ein_klarer_trend_erzeugt_fills(tmp_path):
    """Der Beleg, dass die Order-Pipeline wirklich feuert, nicht nur nichts
    abstuerzt: Strategie -> Risk-Engine -> Order -> Fill -> Position."""
    warmup, trend = _historie()
    _schreibe(tmp_path, "BTC/USD", warmup)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False)

    _schreibe(tmp_path, "BTC/USD", trend)
    report = run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False
    )

    assert report.new_bars == len(trend)
    assert len(report.new_fills) > 0
    state = PaperState.load(state_path("macross", ["BTC/USD"], "1d", tmp_path))
    assert state.positions["BTC/USD"]["qty"] > 0, "long-Trend muss zu einer Long-Position fuehren"


def test_ohne_neue_daten_wirft_ein_leerer_store():
    with pytest.raises(ValueError, match="qt data pull"):
        run_paper_tick(
            _factory, ["BTC/USD"], "1d", data_dir=Path("/nichts/hier"), refresh=False
        )


# --------------------------------------------------------------------------
# Die Eigenschaft, um die es geht: Absturz-Sicherheit
# --------------------------------------------------------------------------


def test_ueberlebt_neustart_zwischen_jedem_bar(tmp_path):
    """Derselbe Datensatz, einmal in einem Tick und einmal mit einem
    erzwungenen Neustart nach jedem einzelnen Bar -- das Ergebnis muss
    bitgleich sein.

    Das ist keine Bequemlichkeit, sondern die Eigenschaft, die ein
    Paper-Konto ueber Wochen ueberhaupt vertrauenswuerdig macht: ein
    Container-Neustart mitten im Lauf darf nicht mehr kosten als die Zeit
    bis zum naechsten Aufruf.
    """
    warmup, trend = _historie()

    # Fall A: ein einziger Tick verarbeitet den ganzen Trend.
    a = tmp_path / "a"
    _schreibe(a, "BTC/USD", warmup)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=a, state_dir=a, refresh=False)
    _schreibe(a, "BTC/USD", trend)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=a, state_dir=a, refresh=False)

    # Fall B: nach jedem einzelnen neuen Bar wird neu "gestartet" -- hier
    # simuliert durch einen frischen Aufruf von run_paper_tick, der Zustand
    # kommt ausschliesslich aus der Datei, nicht aus dem Prozessspeicher.
    b = tmp_path / "b"
    _schreibe(b, "BTC/USD", warmup)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=b, state_dir=b, refresh=False)
    for bar in trend:
        _schreibe(b, "BTC/USD", [bar])
        run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=b, state_dir=b, refresh=False)

    sa = PaperState.load(state_path("macross", ["BTC/USD"], "1d", a))
    sb = PaperState.load(state_path("macross", ["BTC/USD"], "1d", b))

    assert sa.cash == sb.cash
    assert sa.positions == sb.positions
    assert sa.n_fills == sb.n_fills
    assert sa.fees_paid == pytest.approx(sb.fees_paid)


def test_ein_tick_mit_mehreren_neuen_bars_verarbeitet_sie_in_reihenfolge(tmp_path):
    """Mehrere neue Bars in einem Aufruf sind der Normalfall, kein Sonderfall
    -- z.B. wenn zwischen zwei Ticks laenger Zeit verging als ein Bar dauert."""
    warmup, trend = _historie(trend_n=10)
    _schreibe(tmp_path, "BTC/USD", warmup)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False)
    _schreibe(tmp_path, "BTC/USD", trend)

    report = run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False
    )
    assert report.new_bars == 10
    assert report.last_bar_ts == trend[-1].close_ts


# --------------------------------------------------------------------------
# Kill-Switch
# --------------------------------------------------------------------------


def test_kill_switch_schliesst_die_position_und_haelt_an(tmp_path):
    warmup, hoch = _historie(trend_n=40)
    _schreibe(tmp_path, "BTC/USD", warmup)
    risk_cfg = RiskConfig(max_drawdown=0.05, max_weight_per_symbol=1.0)
    run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
        refresh=False, risk_cfg=risk_cfg,
    )
    _schreibe(tmp_path, "BTC/USD", hoch)
    run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
        refresh=False, risk_cfg=risk_cfg,
    )

    absturz_preise = hoch[-1].close * np.cumprod(1 - np.full(5, 0.10))
    absturz = make_bars(5, "BTC/USD", "1d", prices=absturz_preise)
    absturz = _verschieben(absturz, hoch[-1].close_ts)
    _schreibe(tmp_path, "BTC/USD", absturz)

    report = run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
        refresh=False, risk_cfg=risk_cfg,
    )
    assert report.halted
    state = PaperState.load(state_path("macross", ["BTC/USD"], "1d", tmp_path))
    assert state.positions == {}, "der Kill-Switch muss die Position schliessen"


def test_kill_switch_bleibt_ueber_ticks_hinweg_aktiv_trotz_erholung(tmp_path):
    """Klebrig: eine Erholung der Equity darf den Halt nicht selbst aufheben.

    Ein Kill-Switch, der sich selbst zuruecksetzt, ist keiner -- siehe
    RiskEngine.reset().
    """
    warmup, hoch = _historie(trend_n=40)
    _schreibe(tmp_path, "BTC/USD", warmup)
    risk_cfg = RiskConfig(max_drawdown=0.05, max_weight_per_symbol=1.0)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
                   refresh=False, risk_cfg=risk_cfg)
    _schreibe(tmp_path, "BTC/USD", hoch)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
                   refresh=False, risk_cfg=risk_cfg)

    absturz_preise = hoch[-1].close * np.cumprod(1 - np.full(5, 0.10))
    absturz = _verschieben(make_bars(5, "BTC/USD", "1d", prices=absturz_preise), hoch[-1].close_ts)
    _schreibe(tmp_path, "BTC/USD", absturz)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
                   refresh=False, risk_cfg=risk_cfg)

    erholung_preise = absturz[-1].close * np.cumprod(1 + np.full(5, 0.10))
    erholung = _verschieben(make_bars(5, "BTC/USD", "1d", prices=erholung_preise), absturz[-1].close_ts)
    _schreibe(tmp_path, "BTC/USD", erholung)
    report = run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
                             refresh=False, risk_cfg=risk_cfg)

    assert report.halted
    assert report.new_fills == [], "waehrend des Halts duerfen keine neuen Positionen entstehen"


def test_manueller_reset_loest_den_kill_switch(tmp_path):
    warmup, hoch = _historie(trend_n=40)
    _schreibe(tmp_path, "BTC/USD", warmup)
    risk_cfg = RiskConfig(max_drawdown=0.05, max_weight_per_symbol=1.0)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
                   refresh=False, risk_cfg=risk_cfg)
    _schreibe(tmp_path, "BTC/USD", hoch)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
                   refresh=False, risk_cfg=risk_cfg)
    absturz = _verschieben(
        make_bars(5, "BTC/USD", "1d", prices=hoch[-1].close * np.cumprod(1 - np.full(5, 0.10))),
        hoch[-1].close_ts,
    )
    _schreibe(tmp_path, "BTC/USD", absturz)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
                   refresh=False, risk_cfg=risk_cfg)

    state = killswitch.reset("macross", ["BTC/USD"], "1d", note="geprueft", state_dir=tmp_path)
    assert state.halted is False
    assert "geprueft" in state.halt_reasons[-1]


def test_reset_ohne_konto_wirft_sprechenden_fehler(tmp_path):
    with pytest.raises(killswitch.NoPaperAccount, match="qt paper run"):
        killswitch.reset("macross", ["BTC/USD"], "1d", state_dir=tmp_path)


# --------------------------------------------------------------------------
# Persistenz
# --------------------------------------------------------------------------


def test_state_datei_ist_valides_json_und_atomar_geschrieben(tmp_path):
    """Ein Kontostand, den man nicht mit einem Texteditor lesen kann, wenn
    etwas schiefgeht, verfehlt den Zweck von Paper-Trading."""
    warmup, trend = _historie()
    _schreibe(tmp_path, "BTC/USD", warmup)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False)
    _schreibe(tmp_path, "BTC/USD", trend)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False)

    path = state_path("macross", ["BTC/USD"], "1d", tmp_path)
    assert not path.with_suffix(".json.tmp").exists(), "keine Temp-Datei darf liegenbleiben"
    import json

    raw = json.loads(path.read_text())
    assert "cash" in raw and "positions" in raw


def test_zwei_symbole_teilen_sich_nicht_denselben_kontopfad(tmp_path):
    p1 = state_path("macross", ["BTC/USD"], "1d", tmp_path)
    p2 = state_path("macross", ["ETH/USD"], "1d", tmp_path)
    assert p1 != p2


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def test_daily_report_zeigt_den_kill_switch_zuerst(tmp_path):
    warmup, hoch = _historie(trend_n=40)
    _schreibe(tmp_path, "BTC/USD", warmup)
    risk_cfg = RiskConfig(max_drawdown=0.05, max_weight_per_symbol=1.0)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
                   refresh=False, risk_cfg=risk_cfg)
    _schreibe(tmp_path, "BTC/USD", hoch)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
                   refresh=False, risk_cfg=risk_cfg)
    absturz = _verschieben(
        make_bars(5, "BTC/USD", "1d", prices=hoch[-1].close * np.cumprod(1 - np.full(5, 0.10))),
        hoch[-1].close_ts,
    )
    _schreibe(tmp_path, "BTC/USD", absturz)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
                   refresh=False, risk_cfg=risk_cfg)

    state = PaperState.load(state_path("macross", ["BTC/USD"], "1d", tmp_path))
    text = render(state, "macross", ["BTC/USD"])
    zeilen = text.splitlines()
    assert "KILL-SWITCH" in zeilen[1]


def test_daily_report_ohne_halt_ist_unauffaellig(tmp_path):
    warmup, _ = _historie()
    _schreibe(tmp_path, "BTC/USD", warmup)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False)
    state = PaperState.load(state_path("macross", ["BTC/USD"], "1d", tmp_path))
    text = render(state, "macross", ["BTC/USD"])
    assert "KILL-SWITCH" not in text
    assert "flach" in text


# --------------------------------------------------------------------------
# Die reale Uhr: ein Bar, dessen Close in der Zukunft liegt, ist unbekannt
# --------------------------------------------------------------------------


def test_ein_bar_mit_zukuenftiger_close_zeit_wird_ignoriert(tmp_path):
    """Genau der Fall, der gegen echte Coinbase-Daten auftrat: die Exchange
    gab einen Bar zurueck, dessen `close_ts` einen Tag in der Zukunft lag --
    die gerade erst offene, sich noch aendernde Kerze von heute.

    Der Store heilt sich selbst (write_bars dedupliziert mit keep='last'),
    aber bis zum naechsten Pull darf diese Engine nicht auf einem Wert
    entscheiden, der sich noch aendern kann.
    """
    warmup, _ = _historie()
    _schreibe(tmp_path, "BTC/USD", warmup)
    jetzt = warmup[-1].close_ts

    # Ein Bar, der laut Zeitstempel erst morgen schliesst -- die Exchange
    # gibt ihn trotzdem schon zurueck, wie beobachtet.
    unfertig = _verschieben(
        make_bars(1, "BTC/USD", "1d", prices=np.array([999.0])), jetzt
    )
    _schreibe(tmp_path, "BTC/USD", unfertig)

    report = run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
        refresh=False, now=jetzt,
    )
    assert report.new_bars == 0
    state = PaperState.load(state_path("macross", ["BTC/USD"], "1d", tmp_path))
    assert state.last_processed_ts == jetzt.isoformat()


def test_der_unfertige_bar_wird_verarbeitet_sobald_die_zeit_ihn_einholt(tmp_path):
    warmup, _ = _historie()
    _schreibe(tmp_path, "BTC/USD", warmup)
    jetzt = warmup[-1].close_ts

    unfertig = _verschieben(
        make_bars(1, "BTC/USD", "1d", prices=np.array([999.0])), jetzt
    )
    _schreibe(tmp_path, "BTC/USD", unfertig)
    run_paper_tick(_factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
                    refresh=False, now=jetzt)

    spaeter = unfertig[0].close_ts
    report = run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
        refresh=False, now=spaeter,
    )
    assert report.new_bars == 1
    assert report.last_bar_ts == unfertig[0].close_ts
