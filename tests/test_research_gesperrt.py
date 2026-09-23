"""Seit ADR-080 erzeugt dieses Projekt keine LLM-Kandidaten auf Preisdaten mehr.

Gemessen, nicht vermutet: 16 LLM-Kandidaten kamen bis zum Screening, keiner
hat bestanden, 15 davon lagen ueber dem Umschlagbudget, und keinen davon hat
die Kritik markiert. Jeder hat die DSR-Latte fuer alle kuenftigen Hypothesen
dauerhaft angehoben.

Die Sperre ist eine Konstante und keine Option -- wie die Gate-Schwellen
(ADR-057). Diese Tests halten fest, dass sie greift, **bevor** irgendetwas
geoeffnet wird, dass Lesen und Stub-Laeufe erlaubt bleiben, und dass sie im
Repo geschlossen steht.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from qt import cli
from qt.research import loop as loop_mod
from tests.conftest import make_bars

runner = CliRunner()


@pytest.fixture
def pfade(monkeypatch, tmp_path):
    from qt.data import store
    from qt.research import registry

    echt = tmp_path / "registry.duckdb"
    stub = tmp_path / "registry_stub.duckdb"
    monkeypatch.setattr(registry, "DEFAULT_PATH", echt)
    monkeypatch.setattr(registry, "STUB_PATH", stub)
    monkeypatch.setattr(store, "read_bars", lambda *a, **k: None)
    monkeypatch.setattr(store, "to_bars", lambda sym, tf, df: make_bars(1500, sym, tf))

    def kein_modell(name: str):
        raise AssertionError("gesperrt -- es darf kein Anbieter gebaut werden")

    monkeypatch.setattr(cli, "_build_provider", kein_modell)
    return echt, stub


def test_die_sperre_steht_im_repo_geschlossen():
    """Wer sie aufhebt, aendert eine Konstante und diesen Test: zwei Diffs."""
    assert loop_mod.LLM_KANDIDATEN_FREIGEGEBEN is False


def test_ein_echter_lauf_endet_vor_dem_ersten_modellaufruf(pfade):
    echt, _ = pfade

    ergebnis = runner.invoke(cli.app, ["research", "--generate", "3"])

    assert ergebnis.exit_code == 1
    assert "ADR-080" in ergebnis.output
    assert "qt research --stub" in ergebnis.output, "die Meldung nennt, was noch geht"
    assert not echt.exists(), "nicht einmal die Registry wird geoeffnet"


def test_auch_resume_ist_gesperrt(pfade):
    echt, _ = pfade

    ergebnis = runner.invoke(cli.app, ["research", "--resume"])

    assert ergebnis.exit_code == 1
    assert "ADR-080" in ergebnis.output
    assert not echt.exists()


def test_lesen_bleibt_erlaubt(pfade):
    ergebnis = runner.invoke(cli.app, ["research", "--show", "gibt-es-nicht"])

    assert "ADR-080" not in ergebnis.output
    assert "Kein Kandidat mit der ID gibt-es-nicht" in ergebnis.output


def test_der_stub_laeuft_weiter(pfade):
    _, stub = pfade

    ergebnis = runner.invoke(
        cli.app,
        ["research", "--stub", "--generate", "1", "--symbols", "BTC/USD", "--tf", "4h",
         "--train", "600", "--test", "200", "--embargo", "10"],
    )

    assert ergebnis.exit_code == 0, ergebnis.output
    assert "ADR-080" not in ergebnis.output
    assert stub.exists()
