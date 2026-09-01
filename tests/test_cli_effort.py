"""Die Effort-Stufe an der Kommandozeile.

Effort ist der groesste einzelne Hebel auf Laufzeit und Ausgabe-Token eines
Laufs. Steuerbar ist er aber nur dann etwas wert, wenn der gesetzte Wert auch
wirklich am gebauten Client ankommt -- eine Option, die nirgends landet, sieht
im `--help` genauso aus wie eine, die wirkt. Deshalb pruefen die Tests hier den
Client, den der Befehl tatsaechlich konstruiert, und nicht den Hilfetext.

Zweiter Punkt, aus demselben Grund: Modell und Cache-Modell muessen im
`sim`-Pfad zusammenpassen. Dort stand `LLMCache()` ohne Modell; dass daraus
kein falscher Treffer wurde, lag allein daran, dass der Client sein Modell
zusaetzlich in den Key schreibt.
"""

from __future__ import annotations

import pandas as pd
import pytest
import typer
from typer.testing import CliRunner

from qt.cli import DEFAULT_EFFORT, DEFAULT_MODEL, EFFORT_LEVELS, app

runner = CliRunner()


def _frame(n: int = 300) -> pd.DataFrame:
    """Bar-Tabelle in der Form, die `read_bars` liefert.

    Die Preise sind ein glatter Anstieg: geprueft wird die Verdrahtung, und
    ein Zufallspfad wuerde nur eine Fehlerquelle hinzufuegen, die mit der
    Frage nichts zu tun hat.
    """
    ts = pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC")
    close = pd.Series(range(n), dtype=float) * 0.1 + 100.0
    return pd.DataFrame(
        {
            "ts": ts,
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": 1.0,
        }
    )


@pytest.fixture
def alloc_kandidat(monkeypatch):
    """Faengt den Allokator ab, den `qt alloc` baut -- ohne Daten und Gate.

    Der Gate-Lauf selbst ist hier uninteressant und teuer; interessant ist
    ausschliesslich, welchen Client die Fabrik im Befehl zusammensetzt.
    """
    import qt.data.store as store
    import qt.portfolio.gate as gate

    monkeypatch.setattr(store, "read_bars", lambda *args, **kwargs: _frame())
    monkeypatch.setattr(store, "to_bars", lambda symbol, tf, df: [])

    gefangen: dict = {}

    def fake_run_gate(candidate, **kwargs):
        gefangen["allocator"] = candidate()
        raise typer.Exit(code=0)

    monkeypatch.setattr(gate, "run_gate", fake_run_gate)
    return gefangen


@pytest.fixture
def sim_client(monkeypatch):
    """Faengt den Szenario-Client ab, den `qt sim --scenarios` baut.

    Der Recorder erbt vom echten `ScenarioClient`, damit die Konstruktion
    dieselbe bleibt wie im Ernstfall -- ein Attrappen-Client wuerde genau die
    Zuordnung von Modell und Effort ungeprueft lassen, um die es geht. Nur der
    Aufruf selbst faellt aus, und zwar auf dem Weg, den der Befehl ohnehin
    abfangen kann.
    """
    import qt.data.store as store
    import qt.llm.client as llm_client

    monkeypatch.setattr(store, "read_bars", lambda *args, **kwargs: _frame())

    gefangen: dict = {}

    class Recorder(llm_client.ScenarioClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            gefangen["client"] = self

        def propose(self, briefing):
            raise llm_client.LLMUnavailable("Testlauf ohne API-Zugang")

    monkeypatch.setattr(llm_client, "ScenarioClient", Recorder)
    return gefangen


def _sim(*extra: str) -> list[str]:
    """Kleinster Simulationslauf, der bis zu den Szenario-Priors kommt."""
    return [
        "sim",
        "--scenarios",
        "--paths",
        "200",
        "--horizon",
        "10",
        "--lookback",
        "100",
        *extra,
    ]


# -- Die zulaessige Menge ---------------------------------------------------


def test_stufen_decken_sich_mit_der_installierten_bibliothek():
    """Die Liste in `qt.cli` ist abgeschrieben -- hier steht, wovon.

    Ein SDK-Update, das eine Stufe hinzufuegt oder streicht, soll hier
    auffallen und nicht erst dadurch, dass ein zulaessiger Wert abgelehnt
    oder ein unzulaessiger durchgelassen wird.
    """
    from typing import get_args, get_type_hints

    pytest.importorskip("anthropic")
    from anthropic.types.output_config_param import OutputConfigParam

    hints = get_type_hints(OutputConfigParam)
    aus_der_bibliothek = {
        wert for arg in get_args(hints["effort"]) for wert in get_args(arg)
    }
    assert aus_der_bibliothek == set(EFFORT_LEVELS)


def test_default_modell_entspricht_der_konfiguration():
    """Das Literal in `qt.cli` darf nicht von `qt.core.config` abweichen.

    Zwei Stellen mit demselben Default laufen frueher oder spaeter
    auseinander -- und weil das Modell im Cache-Key steht, waere die Folge ein
    stiller Verlust aller Treffer.
    """
    from qt.core.config import DEFAULT_LLM_MODEL

    assert DEFAULT_MODEL == DEFAULT_LLM_MODEL


# -- Durchgereicht bis zum Client -------------------------------------------


@pytest.mark.parametrize("stufe", ["low", "high", "max"])
def test_alloc_reicht_den_effort_bis_zum_client_durch(alloc_kandidat, stufe):
    ergebnis = runner.invoke(app, ["alloc", "--compare-baselines", "--effort", stufe])

    assert ergebnis.exit_code == 0, ergebnis.output
    assert alloc_kandidat["allocator"].client.effort == stufe


def test_alloc_bleibt_ohne_angabe_bei_medium(alloc_kandidat):
    """Der Default ist eine Kostenentscheidung und darf nicht wandern."""
    ergebnis = runner.invoke(app, ["alloc", "--compare-baselines"])

    assert ergebnis.exit_code == 0, ergebnis.output
    assert alloc_kandidat["allocator"].client.effort == DEFAULT_EFFORT == "medium"


def test_alloc_setzt_dasselbe_modell_an_client_und_cache(alloc_kandidat):
    ergebnis = runner.invoke(
        app, ["alloc", "--compare-baselines", "--model", "claude-sonnet-5"]
    )

    assert ergebnis.exit_code == 0, ergebnis.output
    client = alloc_kandidat["allocator"].client
    assert client.model == "claude-sonnet-5"
    assert client.cache.model == "claude-sonnet-5"


@pytest.mark.parametrize("stufe", ["low", "xhigh"])
def test_sim_reicht_den_effort_bis_zum_szenario_client_durch(sim_client, stufe):
    ergebnis = runner.invoke(app, _sim("--effort", stufe))

    assert ergebnis.exit_code == 0, ergebnis.output
    assert sim_client["client"].effort == stufe


def test_sim_setzt_dasselbe_modell_an_client_und_cache(sim_client):
    """Der Fall, der vorher offen war: `LLMCache()` ohne Modell.

    Solange beide Defaults aus derselben Konstante stammen, faellt das nicht
    auf. Sobald sie es nicht mehr tun, haengt die Trefferquote des Caches an
    einem Zufall -- der Key traegt dann ein anderes Modell als der Aufruf.
    """
    ergebnis = runner.invoke(app, _sim("--model", "claude-sonnet-5"))

    assert ergebnis.exit_code == 0, ergebnis.output
    client = sim_client["client"]
    assert client.model == "claude-sonnet-5"
    assert client.cache.model == "claude-sonnet-5"


def test_sim_effort_steht_im_cache_key(sim_client):
    """Zwei Effort-Stufen sind zwei Antworten und duerfen sich nicht teilen."""
    assert runner.invoke(app, _sim("--effort", "low")).exit_code == 0
    key_low = sim_client["client"]._cache_key("briefing")

    assert runner.invoke(app, _sim("--effort", "max")).exit_code == 0
    key_max = sim_client["client"]._cache_key("briefing")

    assert key_low != key_max


# -- Unsinn faellt vor dem Lauf auf -----------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["alloc", "--compare-baselines", "--effort", "turbo"],
        ["sim", "--scenarios", "--effort", "turbo"],
    ],
    ids=["alloc", "sim"],
)
def test_unsinnige_stufe_bricht_ab_bevor_daten_gelesen_werden(monkeypatch, argv):
    """Abbruch beim Aufruf, nicht erst am API-Aufruf.

    Der Datenzugriff wird zur Falle: laeuft der Befehl trotz Tippfehler an,
    schlaegt er hier fehl statt still weiterzulaufen -- und im `--stub`-Pfad
    faellt der falsche Wert sonst nie auf.
    """
    import qt.data.store as store

    def falle(*args, **kwargs):
        raise AssertionError("Der Lauf haette gar nicht so weit kommen duerfen.")

    monkeypatch.setattr(store, "read_bars", falle)

    ergebnis = runner.invoke(app, argv)

    assert ergebnis.exit_code != 0
    assert "turbo" in ergebnis.output
    for stufe in EFFORT_LEVELS:
        assert stufe in ergebnis.output
