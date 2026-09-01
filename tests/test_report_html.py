"""Das Cockpit als HTML: was es zeigen muss, und was es nicht darf.

Drei Eigenschaften traegt dieser Report, und alle drei sind hier
festgehalten statt nur beabsichtigt:

1. **Er ist in sich geschlossen.** Keine externe Anfrage -- ein Report, der
   ohne Netz halb leer ist, ist kein Beleg.
2. **Er zaehlt Rueckstand so, wie der Tick ihn abarbeiten wuerde.** Eine
   Zahl, die kein `qt paper run` je einloest, waere schlimmer als keine.
3. **Er kann nichts ausloesen.** Befehle stehen als Text da.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from qt.data.integrity import check
from qt.data.store import write_bars
from qt.live.state import PaperState, state_path
from qt.report.cockpit import collect, strip_segments
from qt.report.html import render

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _frame(n: int, start: str = "2026-01-01", freq: str = "1D", price: float = 100.0):
    ts = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": [price] * n,
            "high": [price * 1.01] * n,
            "low": [price * 0.99] * n,
            "close": [price] * n,
            "volume": [1.0] * n,
        }
    )


def _konto(tmp_path: Path, **felder) -> PaperState:
    state = PaperState.fresh(100_000.0)
    for key, value in felder.items():
        setattr(state, key, value)
    state.save(state_path("macross", ["BTC/USD"], "1d", tmp_path))
    return state


# --------------------------------------------------------------------------
# Die Abdeckungsleiste
# --------------------------------------------------------------------------


def test_leiste_setzt_die_luecke_an_die_richtige_stelle():
    """Der Anlass fuer die Leiste ist das 301-Tage-Loch im Order-Flow-Bestand.

    Eine Abdeckungszahl von 99% verbirgt genau den Fall, der ein
    Train/Test/Embargo-Fenster unmoeglich macht. Die Leiste muss ihn an der
    Stelle zeigen, an der er liegt -- nicht nur, dass es ihn gibt.
    """
    frame = pd.concat(
        [_frame(10, "2026-01-01"), _frame(10, "2026-04-01")], ignore_index=True
    )
    report = check("BTC/USD", "1d", frame)
    segments = strip_segments(report)

    luecken = [s for s in segments if s.is_gap]
    assert len(luecken) == 1
    luecke = luecken[0]

    # Zehn Tage Daten, ~81 Tage Loch, zehn Tage Daten: die Luecke beginnt im
    # ersten Zehntel und endet im letzten.
    assert 0.05 < luecke.x0 < 0.15
    assert 0.85 < luecke.x1 < 0.95
    assert luecke.missing_bars == report.missing_bars


def test_leere_reihe_hat_keine_leiste():
    report = check("BTC/USD", "1d", _frame(0))
    assert strip_segments(report) == []


def test_luecke_ohne_luecken_ist_ein_stueck():
    report = check("BTC/USD", "1d", _frame(30))
    segments = strip_segments(report)
    assert len(segments) == 1 and not segments[0].is_gap


# --------------------------------------------------------------------------
# Rueckstand: dieselbe Grenze wie der Tick
# --------------------------------------------------------------------------


def test_rueckstand_zaehlt_nach_close_zeit_nicht_nach_open_zeit(tmp_path):
    """`last_processed_ts` ist eine **Close**-Zeit, `read_bars` gibt Open-Zeiten.

    Wer den Rueckstand aus Open-Zeiten zaehlt, ist um genau einen Bar
    daneben -- und zwar dauerhaft, nicht sichtbar und immer in dieselbe
    Richtung.
    """
    write_bars("BTC/USD", "1d", _frame(5, "2026-08-01"), tmp_path)
    # Vier Bars verarbeitet: Close des vierten ist Open des vierten + 1 Tag.
    _konto(tmp_path, last_processed_ts=datetime(2026, 8, 5, tzinfo=timezone.utc).isoformat())

    cockpit = collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path)

    assert cockpit.paper is not None
    assert cockpit.paper.unprocessed_bars == 1
    assert "1 Bar(s) nicht verarbeitet" in render(cockpit)


def test_bar_dessen_close_in_der_zukunft_liegt_zaehlt_nicht(tmp_path):
    """ADR-038: eine noch offene Kerze ist kein Rueckstand, sie ist keine Kerze.

    Der Tick verwirft sie vollstaendig. Ein Report, der sie mitzaehlt,
    verlangt einen Lauf, der nichts tun wuerde.
    """
    write_bars("BTC/USD", "1d", _frame(3, "2026-08-31"), tmp_path)
    _konto(tmp_path, last_processed_ts=datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat())

    cockpit = collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path)

    # Bars: Open 31.08./01.09./02.09. -> Close 01.09./02.09./03.09.
    # Bei now = 01.09. 12:00 ist nur der erste Close vergangen, und der ist
    # verarbeitet.
    assert cockpit.paper is not None
    assert cockpit.paper.unprocessed_bars == 0


def test_alter_wird_relativ_gezeigt(tmp_path):
    write_bars("BTC/USD", "1d", _frame(3, "2026-08-01"), tmp_path)
    _konto(
        tmp_path,
        last_processed_ts=(NOW - timedelta(days=9)).isoformat(),
    )

    seite = render(collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path))

    assert "vor 9 Tagen" in seite


# --------------------------------------------------------------------------
# Kill-Switch
# --------------------------------------------------------------------------


def test_killswitch_steht_oben_und_nennt_den_befehl(tmp_path):
    write_bars("BTC/USD", "1d", _frame(3, "2026-08-01"), tmp_path)
    _konto(tmp_path, halted=True, halt_reasons=["Drawdown 24% ueber Grenze 20%"])

    seite = render(collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path))

    assert "AUSGELOEST" in seite
    assert "qt paper reset-killswitch" in seite
    assert seite.index("AUSGELOEST") < seite.index("Konto</h2>")


def test_ueberschrittene_schwelle_ohne_ausloesung_wird_nicht_gruen(tmp_path):
    """`halted` beschreibt den letzten Tick, der Drawdown die aktuellen Preise.

    Zwischen beidem liegt Zeit -- im Zweifel Tage, wenn niemand getickt hat.
    Ein gruenes "laeuft" ueber einem Konto, das rechnerisch laengst unter der
    Schwelle liegt, ist die eine Zeile, wegen der jemand nicht hinsieht.
    """
    write_bars("BTC/USD", "1d", _frame(3, "2026-08-01", price=50.0), tmp_path)
    _konto(
        tmp_path,
        cash=0.0,
        positions={"BTC/USD": {"qty": 1_000.0, "avg_price": 100.0}},
        peak_equity=100_000.0,
        halted=False,
    )

    cockpit = collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path)
    seite = render(cockpit)

    assert cockpit.paper is not None
    assert cockpit.paper.drawdown == pytest.approx(0.50)
    assert "rechnerisch ueberschritten" in seite
    assert "status ok'>Laeuft" not in seite
    assert "qt paper run" in seite


def test_ueberschreitung_wird_als_ueberschreitung_beziffert(tmp_path):
    """"Noch 0,00% Luft" ist bei 39% Drawdown gegen 20% keine Beschreibung."""
    write_bars("BTC/USD", "1d", _frame(3, "2026-08-01"), tmp_path)
    _konto(tmp_path, cash=60_000.0, peak_equity=100_000.0)

    seite = render(
        collect(
            "macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path, max_drawdown=0.20
        )
    )

    assert "20.00% darueber" in seite
    assert "Luft" not in seite.split("Datenbestand")[0].split("Drawdown 40.00%")[1]


def test_halt_grund_wird_escaped(tmp_path):
    """Der Grund kommt aus `--note` und ist damit freier Text eines Menschen."""
    write_bars("BTC/USD", "1d", _frame(3, "2026-08-01"), tmp_path)
    _konto(tmp_path, halted=True, halt_reasons=["<script>alert(1)</script>"])

    seite = render(collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path))

    assert "<script>alert(1)</script>" not in seite
    assert "&lt;script&gt;" in seite


# --------------------------------------------------------------------------
# Eigenkapital
# --------------------------------------------------------------------------


def test_eigenkapital_wird_zum_markt_bewertet_nicht_zum_einstand(tmp_path):
    """Der Kill-Switch misst am Markt, also muss der Report es auch.

    `qt.report.daily` zeigt Cash plus Positionen zum **Einstand**. Fuer eine
    Kachel, die neben dem Drawdown-Balken steht, waere das die falsche
    Groesse: der Balken bezoege sich dann auf eine Zahl, die sich nie
    bewegt.
    """
    write_bars("BTC/USD", "1d", _frame(5, "2026-08-01", price=200.0), tmp_path)
    _konto(
        tmp_path,
        cash=50_000.0,
        positions={"BTC/USD": {"qty": 100.0, "avg_price": 100.0}},
        peak_equity=100_000.0,
        last_processed_ts=datetime(2026, 8, 6, tzinfo=timezone.utc).isoformat(),
    )

    cockpit = collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path)

    # 50.000 Cash + 100 Stueck zu 200 = 70.000, nicht 60.000 zum Einstand.
    assert cockpit.paper is not None
    assert cockpit.paper.equity == pytest.approx(70_000.0)


def test_ohne_preis_im_store_wird_der_einstand_angesetzt_und_gesagt(tmp_path):
    """Kein Preis heisst unbekannt -- nicht null, und nicht stillschweigend."""
    write_bars("ETH/USD", "1d", _frame(3, "2026-08-01"), tmp_path)
    _konto(tmp_path, cash=1_000.0, positions={"BTC/USD": {"qty": 2.0, "avg_price": 500.0}})

    cockpit = collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path)

    assert cockpit.paper is not None
    assert cockpit.paper.equity == pytest.approx(2_000.0)
    assert any("Kein Preis im Store" in w for w in cockpit.warnings)


def test_drawdown_bezieht_sich_auf_den_hoechststand(tmp_path):
    write_bars("BTC/USD", "1d", _frame(3, "2026-08-01"), tmp_path)
    _konto(tmp_path, cash=80_000.0, peak_equity=100_000.0)

    cockpit = collect(
        "macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path, max_drawdown=0.25
    )

    assert cockpit.paper is not None
    assert cockpit.paper.drawdown == pytest.approx(0.20)
    assert cockpit.paper.headroom == pytest.approx(0.05)


# --------------------------------------------------------------------------
# Leerzustaende und die Eigenschaften der Datei
# --------------------------------------------------------------------------


def test_ohne_konto_nennt_die_seite_den_befehl(tmp_path):
    write_bars("BTC/USD", "1d", _frame(3, "2026-08-01"), tmp_path)

    cockpit = collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path)
    seite = render(cockpit)

    assert cockpit.paper is None
    assert "qt paper run" in seite


def test_leerer_store_nennt_den_befehl(tmp_path):
    seite = render(collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path))

    assert "Store ist leer" in seite
    assert "qt data pull" in seite


def test_seite_ist_in_sich_geschlossen(tmp_path):
    """Kein CDN, kein Font, kein Bild von aussen.

    Der Report wird heruntergeladen und spaeter angesehen, moeglicherweise
    ohne Netz. Was dann fehlt, fehlt unbemerkt.
    """
    write_bars("BTC/USD", "1d", _frame(30, "2026-01-01"), tmp_path)
    _konto(tmp_path)

    seite = render(collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path))

    for verboten in ("http://", "https://", "//cdn", "<script"):
        assert verboten not in seite, f"{verboten!r} macht die Datei abhaengig"


def test_seite_traegt_ihre_herkunft(tmp_path):
    write_bars("BTC/USD", "1d", _frame(3, "2026-08-01"), tmp_path)
    _konto(tmp_path)

    seite = render(
        collect(
            "macross",
            ["BTC/USD"],
            "1d",
            now=NOW,
            data_dir=tmp_path,
            command="qt report html --strategy macross",
        )
    )

    assert "qt report html --strategy macross" in seite
    assert "Commit" in seite
    assert "2026-09-01 12:00 UTC" in seite


def test_collect_schreibt_nichts(tmp_path):
    """Ein Report, der den Zustand anfasst, den er beschreibt, ist keiner."""
    write_bars("BTC/USD", "1d", _frame(3, "2026-08-01"), tmp_path)
    _konto(tmp_path)
    pfad = state_path("macross", ["BTC/USD"], "1d", tmp_path)
    vorher = pfad.read_bytes()

    collect("macross", ["BTC/USD"], "1d", now=NOW, data_dir=tmp_path)

    assert pfad.read_bytes() == vorher
