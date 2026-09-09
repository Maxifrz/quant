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


# ---------------------------------------------------------------------------
# Kaltstart: der Fall, an dem das Konto in der Praxis stehengeblieben ist
# ---------------------------------------------------------------------------


def test_bei_kaltem_store_wird_genug_historie_gezogen(tmp_path, monkeypatch):
    """Ein leerer Store darf den Tick nicht stoppen, sondern muss ihn fuellen.

    Das ist kein hypothetischer Fall. `data/ohlcv/` ist gitignored, der
    Container wird neu gebaut, und `REFRESH_BARS = 5` reicht fuer keinen
    Warmup -- `macross` braucht 52. Frueher endete der Tick hier mit "erst
    `qt data pull` laufen lassen", also mit einem Handgriff, den ein
    geplanter Job nicht tun kann. Genau daran ist das Paper-Konto beim
    letzten Containerwechsel stehengeblieben.
    """
    from qt.live import runner

    gezogen: list[tuple[list[str], float]] = []

    def fake_pull(symbols, timeframes, since, cfg=None):
        spanne = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(since)).total_seconds()
        gezogen.append((list(symbols), spanne / 86400))
        # Der echte Pull wuerde schreiben; hier reicht die Aufzeichnung.
        return {}

    monkeypatch.setattr("qt.data.ingest.pull", fake_pull)

    runner._refresh_recent_bars(["BTC/USD"], "1d", tmp_path, warmup_bars=52)

    assert len(gezogen) == 1, "genau ein Pull fuer ein kaltes Symbol"
    symbole, tage = gezogen[0]
    assert symbole == ["BTC/USD"]
    assert tage >= 52, (
        f"nur {tage:.0f} Tage gezogen -- das reicht nicht fuer 52 Bars Warmup"
    )


def test_bei_warmem_store_bleibt_es_beim_kleinen_fenster(tmp_path, monkeypatch):
    """Sonst fragt jeder taegliche Tick Jahre an Historie neu ab."""
    from qt.live import runner

    bars = make_bars(200, symbol="BTC/USD", timeframe="1d")
    write_bars(
        "BTC/USD",
        "1d",
        pd.DataFrame(
            {
                "ts": [b.ts for b in bars],
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
                "volume": [b.volume for b in bars],
            }
        ),
        data_dir=tmp_path,
    )

    gezogen: list[float] = []

    def fake_pull(symbols, timeframes, since, cfg=None):
        spanne = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(since)).total_seconds()
        gezogen.append(spanne / 86400)
        return {}

    monkeypatch.setattr("qt.data.ingest.pull", fake_pull)
    runner._refresh_recent_bars(["BTC/USD"], "1d", tmp_path, warmup_bars=52)

    assert gezogen == [pytest.approx(runner.REFRESH_BARS, abs=1)]


def test_kaltes_und_warmes_symbol_werden_getrennt_gezogen(tmp_path, monkeypatch):
    """Ein neu dazugenommenes Symbol darf die anderen nicht mitziehen."""
    from qt.live import runner

    bars = make_bars(200, symbol="BTC/USD", timeframe="1d")
    write_bars(
        "BTC/USD",
        "1d",
        pd.DataFrame(
            {
                "ts": [b.ts for b in bars],
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
                "volume": [b.volume for b in bars],
            }
        ),
        data_dir=tmp_path,
    )

    gezogen: dict[str, float] = {}

    def fake_pull(symbols, timeframes, since, cfg=None):
        spanne = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(since)).total_seconds()
        for s in symbols:
            gezogen[s] = spanne / 86400
        return {}

    monkeypatch.setattr("qt.data.ingest.pull", fake_pull)
    runner._refresh_recent_bars(["BTC/USD", "ETH/USD"], "1d", tmp_path, warmup_bars=52)

    assert gezogen["BTC/USD"] < 10, "warmes Symbol nur auffrischen"
    assert gezogen["ETH/USD"] >= 52, "kaltes Symbol braucht die volle Historie"


# --------------------------------------------------------------------------
# Die Risk-Engine im Paper-Konto (ADR-053)
# --------------------------------------------------------------------------


def test_paper_default_handelt_dieselbe_groesse_wie_der_backtest():
    """Der Fund, der die laufende Evidenz verdorben hat.

    `qt backtest` und `qt wf` rufen **keine** Risk-Engine auf -- jede
    gemessene macross-Zahl des Projekts stammt von einem Gewicht 1,0. Mit den
    Portfolio-Defaults (Vol-Targeting, 25% je Symbol) handelte das Paper-Konto
    rund ein Viertel davon und damit eine andere Strategie.

    Geprueft wird hier die Konfiguration, die `qt paper run` setzt, nicht die
    CLI selbst -- der Wert soll festliegen, egal wer ihn baut.
    """
    from datetime import datetime, timezone

    from qt.portfolio.base import RiskState
    from qt.portfolio.risk import RiskEngine

    cfg = RiskConfig(max_drawdown=0.20, vol_targeting=False, max_weight_per_symbol=1.0)
    state = RiskState(
        ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        equity=100_000.0,
        peak_equity=100_000.0,
        realised_vol={"BTC/USD": 0.44},  # gemessene BTC-Tagesvola
    )
    angepasst, gruende = RiskEngine(cfg).apply({"BTC/USD": 1.0}, state)

    assert angepasst["BTC/USD"] == pytest.approx(1.0), (
        "das Paper-Konto handelt nicht die Groesse, gegen die es verglichen wird"
    )
    assert not gruende, "kein Eingriff, also auch keine Begruendung"


def test_der_kill_switch_bleibt_trotz_abgeschalteter_formung():
    """Formung aus heisst nicht Sicherung aus.

    Ohne diesen Test waere `vol_targeting=False` ein Weg, versehentlich auch
    den Kill-Switch stillzulegen.
    """
    from datetime import datetime, timezone

    from qt.portfolio.base import RiskState
    from qt.portfolio.risk import RiskEngine

    cfg = RiskConfig(max_drawdown=0.20, vol_targeting=False, max_weight_per_symbol=1.0)
    state = RiskState(
        ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        equity=79_000.0,
        peak_equity=100_000.0,
        realised_vol={"BTC/USD": 0.44},
    )
    angepasst, gruende = RiskEngine(cfg).apply({"BTC/USD": 1.0}, state)

    assert angepasst["BTC/USD"] == 0.0
    assert any("Kill-Switch" in g for g in gruende)


def test_das_brutto_limit_gilt_fuer_das_konto_und_nicht_je_symbol(tmp_path):
    """A2: die Engine muss alle Symbole auf einmal sehen.

    Der Tick hat die Engine frueher je Bar mit einem Ein-Symbol-Dict
    gefuettert. `max_gross_exposure` ist aber eine Grenze fuer das Konto als
    Ganzes -- so geprueft haetten drei Symbole in Summe das Dreifache
    durchgelassen, ohne dass irgendetwas fehlschlaegt.
    """
    warmup, trend = _historie(trend_n=40)
    for symbol in ("BTC/USD", "ETH/USD"):
        _schreibe(tmp_path, symbol, warmup)

    def factory():
        return MovingAverageCross(["BTC/USD", "ETH/USD"], "1d", fast=5, slow=20)

    risk_cfg = RiskConfig(
        vol_targeting=False, max_weight_per_symbol=1.0, max_gross_exposure=1.0
    )
    run_paper_tick(
        factory, ["BTC/USD", "ETH/USD"], "1d", data_dir=tmp_path,
        state_dir=tmp_path, refresh=False, risk_cfg=risk_cfg,
    )
    for symbol in ("BTC/USD", "ETH/USD"):
        _schreibe(tmp_path, symbol, trend)
    run_paper_tick(
        factory, ["BTC/USD", "ETH/USD"], "1d", data_dir=tmp_path,
        state_dir=tmp_path, refresh=False, risk_cfg=risk_cfg,
    )

    state = PaperState.load(
        state_path("macross", ["BTC/USD", "ETH/USD"], "1d", tmp_path)
    )
    brutto = sum(abs(w) for w in state.target.values())
    assert brutto > 1.0, (
        "Testaufbau kaputt: beide Strategien muessen long sein, sonst prueft "
        "der Test die Grenze gar nicht"
    )
    positionswert = sum(
        abs(p["qty"]) * p["avg_price"] for p in state.positions.values()
    )
    assert positionswert <= state.peak_equity * 1.05, (
        f"Brutto-Exposure {positionswert:,.0f} ueber dem Konto "
        f"{state.peak_equity:,.0f} -- die Grenze wurde je Symbol geprueft "
        f"statt fuer das Konto"
    )


def test_routine_eingriffe_landen_nicht_in_den_halt_gruenden(tmp_path):
    """C4: `halt_reasons` ist fuer Halts, nicht fuer den Alltag.

    Ein greifender Symbol-Cap meldet sich in **jedem** Bar mit Position.
    Landete er in `halt_reasons`, stuende beim naechsten echten Halt dort die
    letzte Cap-Meldung -- und genau diese drei Zeilen zeigt der Tagesreport.
    """
    warmup, trend = _historie(trend_n=40)
    _schreibe(tmp_path, "BTC/USD", warmup)
    # Formung an, damit der Cap ueberhaupt greift.
    risk_cfg = RiskConfig(max_weight_per_symbol=0.25, vol_targeting=True)
    run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
        refresh=False, risk_cfg=risk_cfg,
    )
    _schreibe(tmp_path, "BTC/USD", trend)
    report = run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
        refresh=False, risk_cfg=risk_cfg,
    )

    state = PaperState.load(state_path("macross", ["BTC/USD"], "1d", tmp_path))
    assert not report.halted
    assert state.risk_notes, (
        "Testaufbau kaputt: der Cap muss greifen, sonst prueft der Test nichts"
    )
    assert any("begrenzt" in n for n in state.risk_notes)
    assert state.halt_reasons == [], (
        f"Routine-Meldung in den Halt-Gruenden: {state.halt_reasons}"
    )


def test_ein_alter_kontostand_ohne_neue_felder_laedt_weiter(tmp_path):
    """Ein Konto laeuft ueber Wochen, der Code aendert sich dabei."""
    import json

    path = tmp_path / "alt.json"
    path.write_text(json.dumps({"cash": 100_000.0, "peak_equity": 100_000.0}))
    state = PaperState.load(path)
    assert state.cash == 100_000.0
    assert state.risk_notes == []

    # Und umgekehrt: ein Feld, das diese Fassung nicht kennt, wirft nicht.
    path.write_text(json.dumps({"cash": 1.0, "erfundenes_feld": 42}))
    assert PaperState.load(path).cash == 1.0


# --------------------------------------------------------------------------
# Der Tagesreport bewertet mit Preisen, nicht mit dem Einstand (ADR-053)
# --------------------------------------------------------------------------


def _konto_mit_position() -> PaperState:
    state = PaperState.fresh(100_000.0)
    state.cash = 50_000.0
    state.positions = {"BTC/USD": {"qty": 1.0, "avg_price": 50_000.0}}
    state.peak_equity = 100_000.0
    return state


def test_der_report_bewertet_positionen_mit_dem_letzten_kurs():
    """Frueher stand hier die Kostenbasis unter der Ueberschrift 'Preise'.

    Eine verdoppelte Position bewegte die Zahl nicht -- und neben dem korrekt
    gefuehrten Hoechststand sah das aus wie ein Drawdown, den es nicht gab.
    """
    text = render(
        _konto_mit_position(), "macross", ["BTC/USD"], prices={"BTC/USD": 100_000.0}
    )
    assert "150,000.00" in text, (
        "Position nicht zum Marktpreis bewertet -- der Report zeigt den Einstand"
    )
    assert "letzte Schlusskurse" in text


def test_der_report_erfindet_kein_eigenkapital_ohne_preise():
    """Lieber keine Zahl als eine, die etwas anderes misst als ihr Etikett."""
    text = render(_konto_mit_position(), "macross", ["BTC/USD"], prices=None)
    assert "nicht bewertbar" in text
    assert "100,000.00" not in text.split("Hoechststand")[0], (
        "ohne Preise darf kein Eigenkapital ausgewiesen werden"
    )


def test_ein_flaches_konto_braucht_keine_preise():
    state = PaperState.fresh(100_000.0)
    text = render(state, "macross", ["BTC/USD"], prices=None)
    assert "Eigenkapital: 100,000.00" in text
    assert "nicht bewertbar" not in text


# --------------------------------------------------------------------------
# bars_seen
# --------------------------------------------------------------------------


def test_bars_seen_zaehlt_bars_und_nicht_ticks(tmp_path):
    """`bars_seen` war ein Laufzettel und keine Zahl.

    Der Zaehler wurde aus dem Zustand geladen **und** bei jedem Bar des Ticks
    hochgezaehlt -- auch bei den bereits verarbeiteten, die nur den
    Feature-Store fuellen. Da jeder Tick die ganze Historie neu einspielt,
    wuchs er pro Tick um die volle Storegroesse. Gemessen am echten
    Paper-Konto: 2.805 -> 5.611 -> 8.418 an drei aufeinanderfolgenden Tagen,
    bei je einem neuen Bar.

    Folgenlos war das nur zufaellig: `bars_seen` traegt die Warmup-Pruefung,
    und ein zu **grosser** Wert meldet ein Konto zu frueh als warm. Bei einem
    laengst laufenden Konto faellt das nicht auf; bei einem frisch
    aufgesetzten oder einem beschnittenen Store waere es der Unterschied
    zwischen "handelt mit genug Historie" und "handelt".
    """
    warmup, trend = _historie()
    _schreibe(tmp_path, "BTC/USD", warmup)
    run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False
    )

    gemessen = []
    for n in (1, 2, 3):
        _schreibe(tmp_path, "BTC/USD", warmup + trend[:n])
        run_paper_tick(
            _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path,
            refresh=False,
        )
        state = PaperState.load(state_path("macross", ["BTC/USD"], "1d", tmp_path))
        gemessen.append(state.bars_seen["BTC/USD"])

    assert gemessen == [len(warmup) + n for n in (1, 2, 3)], (
        f"bars_seen lief auf {gemessen} statt auf die Zahl der Bars im Store "
        f"({[len(warmup) + n for n in (1, 2, 3)]}) -- der Zaehler summiert "
        "Ticks statt Bars"
    )


def test_ein_aufgeblaehter_zaehler_heilt_sich_beim_naechsten_tick(tmp_path):
    """Bestehende Konten tragen den falschen Wert in ihrer Zustandsdatei.

    Eine Migration braucht es dafuer nicht: der Zaehler wird pro Tick neu
    gebildet, also steht nach dem ersten Tick der richtige Wert da. Dieser
    Test haelt fest, dass das wirklich so ist und nicht nur so gedacht war.
    """
    warmup, trend = _historie()
    _schreibe(tmp_path, "BTC/USD", warmup)
    run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False
    )

    pfad = state_path("macross", ["BTC/USD"], "1d", tmp_path)
    state = PaperState.load(pfad)
    state.bars_seen = {"BTC/USD": 999_999}
    state.save(pfad)

    _schreibe(tmp_path, "BTC/USD", warmup + trend[:1])
    run_paper_tick(
        _factory, ["BTC/USD"], "1d", data_dir=tmp_path, state_dir=tmp_path, refresh=False
    )

    assert PaperState.load(pfad).bars_seen["BTC/USD"] == len(warmup) + 1
