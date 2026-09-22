"""`qt research` ohne API-Zugang: ein Ausfall darf sich nicht als Ergebnis melden.

Der Loop faengt Generator-Fehler je Kandidat ab -- "eine Charge stirbt nicht
am Netz". Fuer einen einzelnen Wackler ist das richtig. Fehlt aber der
Schluessel, scheitert **jeder** Kandidat, und am Ende stand bisher:

    Kein Kandidat hat bestanden. Das ist der Normalfall und kein Fehler.

mit Exit 0. Ein Lauf, der nichts erzeugt hat, hat nichts geprueft -- "nicht
bestanden" und "nicht gelaufen" sind verschiedene Aussagen. Dieselbe
Fehlerklasse wie ADR-073 (die Routine meldete SUCCEEDED ohne Arbeitskopie).

Nachgestellt wird der echte Pfad: `AnthropicProvider` mit einem Client, der
so scheitert wie das SDK ohne Schluessel -- beim Aufruf, als TypeError.
Registry, Cache und Daten liegen im Temp-Verzeichnis; kein echter Versuch
wird gezaehlt.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from qt import cli
from qt.llm.providers import AnthropicProvider, get_provider
from tests.conftest import make_bars

runner = CliRunner()


class _SdkOhneSchluessel:
    """Verhaelt sich wie `anthropic.Anthropic()` ohne ANTHROPIC_API_KEY."""

    class messages:  # noqa: N801 -- bildet die SDK-Form nach
        @staticmethod
        def parse(**kwargs):
            # Wortlaut des echten SDK, am 2026-09-22 hier gemessen.
            raise TypeError(
                "Could not resolve authentication method. Expected one of "
                "api_key, auth_token, or credentials to be set."
            )


_ANTHROPIC_QUELLEN = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
)
_NIM_QUELLEN = ("NVIDIA_API_KEY", "NIM_API_KEY", "NVIDIA_NIM_API_KEY")


@pytest.fixture
def leere_umgebung(monkeypatch, tmp_path):
    """Keine Zugangsquelle, egal was im Container gesetzt ist.

    HOME zeigt ins Temp-Verzeichnis, damit ein `ant auth login`-Profil des
    Rechners das Ergebnis nicht still umdreht.
    """
    from qt.llm import providers

    for name in _ANTHROPIC_QUELLEN + _NIM_QUELLEN:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Unabhaengig davon, welche Extras im Container gerade installiert sind --
    # der Fall "Paket fehlt" hat einen eigenen Test.
    monkeypatch.setattr(providers, "_paket_da", lambda paket: True)
    return tmp_path


@pytest.fixture
def ohne_zugang(monkeypatch, tmp_path, leere_umgebung):
    from qt.data import store
    from qt.llm import cache
    from qt.research import registry

    monkeypatch.setattr(registry, "DEFAULT_PATH", tmp_path / "registry.duckdb")
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "llm_cache")
    monkeypatch.setattr(store, "read_bars", lambda *a, **k: None)
    monkeypatch.setattr(
        store, "to_bars", lambda sym, tf, df: make_bars(400, sym, tf)
    )

    def bauen(name: str):
        if name == "anthropic":
            return AnthropicProvider(client=_SdkOhneSchluessel())
        return get_provider(name)

    monkeypatch.setattr(cli, "_build_provider", bauen)
    return tmp_path


def _lauf(*extra: str):
    return runner.invoke(
        cli.app,
        ["research", "--generate", "2", "--symbols", "BTC/USD", "--tf", "4h", *extra],
    )


def test_ein_lauf_ohne_einen_einzigen_kandidaten_ist_kein_normalfall(ohne_zugang):
    ergebnis = _lauf()

    assert "Normalfall" not in ergebnis.output, (
        "Ein Lauf ohne erzeugten Kandidaten wurde als normales Ergebnis "
        "gemeldet:\n" + ergebnis.output
    )
    assert ergebnis.exit_code == 1, (
        f"Exit {ergebnis.exit_code} -- ein Aufrufer (Routine, Skript) haelt "
        "den Lauf damit fuer gelungen"
    )


def test_der_abbruch_nennt_den_grund_und_den_ausweg(ohne_zugang, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")

    ergebnis = _lauf()

    assert "ANTHROPIC_API_KEY" in ergebnis.output
    assert "--provider nim" in ergebnis.output, (
        "In dieser Umgebung funktioniert ein anderer Anbieter -- die Meldung "
        "soll ihn nennen, statt nur den fehlenden zu beklagen"
    )


def test_ohne_zugang_wird_kein_versuch_gezaehlt(ohne_zugang):
    from qt.research.registry import ResearchRegistry

    _lauf()

    reg = ResearchRegistry.open(ohne_zugang / "registry.duckdb")
    try:
        assert reg.trial_count() == 0
    finally:
        reg.close()


def test_ein_teilweise_gescheiterter_lauf_sagt_wieviel_geprueft_wurde(ohne_zugang, monkeypatch):
    """Ein Wackler bleibt ein Wackler -- Exit 0, aber mit der Zahl dazu."""
    from qt.llm.client import StubGeneratorClient

    echt = StubGeneratorClient()
    zaehler = {"n": 0}

    class Wackelnd:
        model, effort = "stub", "low"

        def propose(self, briefing):
            zaehler["n"] += 1
            if zaehler["n"] == 1:
                raise ConnectionError("Netz weg")
            return echt.propose(briefing)

    monkeypatch.setattr("qt.llm.client.GeneratorClient", lambda **kw: Wackelnd())
    ergebnis = _lauf("--no-critic")

    assert ergebnis.exit_code == 0, ergebnis.output
    assert "GESCHEITERT" not in ergebnis.output
    assert "nur 1 von 2" in ergebnis.output


# ---------------------------------------------------------------------------
# Die Zugangserkennung selbst
# ---------------------------------------------------------------------------


def test_ohne_jede_quelle_gibt_es_keinen_zugang(leere_umgebung):
    from qt.llm.providers import zugang_vorhanden

    assert not zugang_vorhanden("anthropic")
    assert not zugang_vorhanden("nim")


@pytest.mark.parametrize("quelle", _ANTHROPIC_QUELLEN)
def test_ein_leerer_api_key_ist_kein_beweis_fuer_fehlenden_zugang(
    leere_umgebung, monkeypatch, quelle
):
    """Das SDK kennt mehr Wege als ANTHROPIC_API_KEY.

    Genau diese Verkuerzung stand in der ersten Fassung von Befund 1: aus
    einem leeren ANTHROPIC_API_KEY wurde "kein Zugang" geschlossen, ohne
    `ant auth login`-Profil und Token zu pruefen.
    """
    from qt.llm.providers import zugang_vorhanden

    monkeypatch.setenv(quelle, "x")
    assert zugang_vorhanden("anthropic")


def test_ein_ant_login_profil_zaehlt_als_zugang(leere_umgebung):
    from qt.llm.providers import zugang_vorhanden

    (leere_umgebung / "home" / ".config" / "anthropic").mkdir(parents=True)
    assert zugang_vorhanden("anthropic")


def test_ein_schluessel_ohne_sdk_ist_kein_zugang(leere_umgebung, monkeypatch):
    """Der Fall vom 2026-09-22: NVIDIA_API_KEY gesetzt, `openai` fehlt.

    Der frische Container hatte das `nim`-Extra nicht installiert. Eine
    Pruefung, die nur den Schluessel ansieht, haette `--provider nim` als
    Ausweg empfohlen -- einen Weg, der sofort an `ModuleNotFoundError`
    scheitert.
    """
    from qt.llm import providers

    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr(providers, "_paket_da", lambda paket: paket != "openai")

    assert not providers.zugang_vorhanden("nim")
    assert "uv sync --extra nim" in providers.zugangs_hinweis("nim")
    assert "--provider nim" not in providers.zugangs_hinweis("anthropic"), (
        "ein Anbieter ohne SDK darf nicht als Ausweg empfohlen werden"
    )


def test_mit_zugang_bleibt_der_hinweis_stumm(leere_umgebung, monkeypatch):
    from qt.llm.providers import zugangs_hinweis

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert zugangs_hinweis("anthropic") == ""


def test_der_hinweis_liest_nie_einen_wert(leere_umgebung, monkeypatch):
    from qt.llm.providers import zugangs_hinweis

    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-GEHEIM-1234")
    hinweis = zugangs_hinweis("anthropic")

    assert "GEHEIM" not in hinweis and "1234" not in hinweis
    assert "--provider nim" in hinweis


# ---------------------------------------------------------------------------
# qt alloc: 100 % Rueckfall ist kein Urteil ueber einen Allokator
# ---------------------------------------------------------------------------


class _Urteil:
    def table(self):
        return "(Tabelle)"

    def verdict(self):
        return "NICHT BESTANDEN"

    def passed(self):
        return False


@pytest.fixture
def alloc_lauf(monkeypatch, ohne_zugang):
    from qt.portfolio import gate

    def fake_run_gate(candidate, **kwargs):
        allokator = candidate()
        # Was `LLMAllocator.allocate` ohne Zugang je Aufruf verbucht.
        allokator.telemetry.calls = 5
        allokator.telemetry.fallbacks = 5
        return _Urteil()

    monkeypatch.setattr(gate, "run_gate", fake_run_gate)

    def lauf():
        return runner.invoke(
            cli.app,
            ["alloc", "--compare-baselines", "--strategies", "macross",
             "--symbols", "BTC/USD"],
        )

    return lauf


def test_alloc_ohne_einen_einzigen_llm_aufruf_ist_gescheitert(alloc_lauf):
    ergebnis = alloc_lauf()

    assert ergebnis.exit_code == 1, (
        f"Exit {ergebnis.exit_code} -- 2 hiesse 'Allokator geprueft, nicht "
        "bestanden', gepruefter Allokator gab es aber keinen\n" + ergebnis.output
    )
    assert "GESCHEITERT" in ergebnis.output
