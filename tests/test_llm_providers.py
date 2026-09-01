"""Der zweite Anbieter: NVIDIA NIM neben Anthropic.

Kein Test hier ruft ein Modell auf, und keiner braucht einen Schluessel.
Geprueft wird ausschliesslich, was zwischen Client und Endpunkt passiert --
also genau die Schicht, die man sonst erst im Ernstfall sieht, wenn ein Lauf
schon Geld gekostet hat.

Zwei Dinge sind hier wichtiger als die Uebersetzung selbst:

**Die Trennung im Cache.** Dieselbe Frage an Claude und an Nemotron sind zwei
Antworten. Ein Cache, der sie vermischt, schlaegt nicht fehl -- er liefert das
falsche Ergebnis, und zwar in einem Backtest, wo es niemand mehr findet.

**Die ehrliche Fehlermeldung.** Ein Reasoning-Modell kann sein gesamtes
Token-Budget im Gedankengang verbrauchen und eine leere Antwort liefern. Ohne
eigenen Satz dafuer sieht das aus wie ein Schema-Fehler und wird an der
falschen Stelle gesucht.
"""

from __future__ import annotations

import json

import pytest

from qt.llm.providers import (
    PROVIDERS,
    AnthropicProvider,
    LLMUnavailable,
    NimProvider,
    get_provider,
    resolve_model,
)
from qt.llm.schemas import CandidateCritique


# ---------------------------------------------------------------------------
# Attrappen: ein OpenAI-kompatibler Endpunkt, so weit er hier gebraucht wird
# ---------------------------------------------------------------------------


def _antwort(inhalt: str, finish: str = "stop") -> dict:
    """Eine Chat-Completion, wie NIM sie zurueckgibt -- als dict.

    Bewusst ein dict und kein Objekt: eine OpenAI-kompatible Bereitstellung
    liefert genau das, wenn man am SDK vorbei spricht, und der Provider muss
    mit beiden Formen umgehen.
    """
    return {
        "choices": [
            {"finish_reason": finish, "message": {"role": "assistant", "content": inhalt}}
        ]
    }


VERDIKT = {
    "recommendation": "proceed",
    "overfitting_risk": 0.1,
    "reasoning": "Keine Preiskonstante, drei Parameter.",
}


class _FakeCompletions:
    def __init__(self, owner: "_FakeNim") -> None:
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        fehler = self._owner.raise_on.pop(0) if self._owner.raise_on else None
        if fehler is not None:
            raise fehler
        return self._owner.antworten.pop(0)


class _FakeNim:
    """Steht an der Stelle von `openai.OpenAI(base_url=...)`.

    Zeichnet auf, was gesendet wurde, und gibt der Reihe nach vorbereitete
    Antworten oder Fehler heraus.
    """

    def __init__(self, antworten=None, raise_on=None) -> None:
        self.antworten = list(antworten or [_antwort(json.dumps(VERDIKT))])
        self.raise_on = list(raise_on or [])
        self.calls: list[dict] = []
        self.chat = type("_Chat", (), {"completions": _FakeCompletions(self)})()


def _nim(antworten=None, raise_on=None, guided: bool = False) -> tuple[NimProvider, _FakeNim]:
    fake = _FakeNim(antworten, raise_on)
    return NimProvider(client=fake, guided=guided), fake


def _parse(provider: NimProvider, effort: str = "low"):
    return provider.parse(
        system="Du pruefst Kandidaten.",
        prompt="Kandidat XY",
        schema=CandidateCritique,
        model="nvidia/nemotron-3-ultra-550b-a55b",
        max_tokens=1000,
        effort=effort,
    )


# ---------------------------------------------------------------------------
# Auswahl und Modellnamen
# ---------------------------------------------------------------------------


def test_die_liste_in_der_cli_deckt_sich_mit_den_anbietern():
    """Das Literal in `qt.cli` ist abgeschrieben -- hier steht, wovon.

    Gleicher Grund wie bei `EFFORT_LEVELS`: der Import von `qt.llm.providers`
    zieht pydantic nach und kostet jeden `qt`-Aufruf Zeit, auch die, die nie
    ein Modell anfassen. Der Preis dafuer ist ein Test, der die Kopie
    festnagelt.
    """
    from qt.cli import PROVIDER_NAMES

    assert set(PROVIDER_NAMES) == set(PROVIDERS)


def test_der_aufruf_passt_zur_installierten_bibliothek():
    """Die Parameternamen sind abgeschrieben -- hier steht, wovon.

    Gleiche Bauart wie `test_stufen_decken_sich_mit_der_installierten_
    bibliothek` fuer den Effort. `openai` zieht Hauptversionen in schnellem
    Takt; wird `max_tokens` eines Tages umbenannt, soll das hier auffallen und
    nicht am ersten bezahlten Aufruf. Die Attrappen in dieser Datei koennen
    das nicht fangen -- sie nehmen alles an, was man ihnen gibt.
    """
    import inspect

    pytest.importorskip("openai")
    import openai

    client = openai.OpenAI(base_url="https://example.invalid/v1", api_key="nvapi-test")
    parameter = inspect.signature(client.chat.completions.create).parameters

    for name in ("model", "messages", "max_tokens", "temperature", "top_p", "seed"):
        assert name in parameter, name
    assert "extra_body" in parameter, "Ohne extra_body kein Denk-Schalter"
    assert "response_format" in parameter, "Ohne response_format keine feste Form"


def test_ohne_modellangabe_kommt_der_default_des_anbieters():
    assert resolve_model("anthropic", None) == AnthropicProvider.default_model
    assert resolve_model("nim", None) == NimProvider.default_model
    assert resolve_model("nim", None).startswith("nvidia/")


@pytest.mark.parametrize(
    ("anbieter", "modell"),
    [
        ("nim", "claude-opus-5"),
        ("anthropic", "nvidia/nemotron-3-ultra-550b-a55b"),
    ],
    ids=["claude-an-nim", "nemotron-an-anthropic"],
)
def test_fehlpaarung_aus_anbieter_und_modell_faellt_sofort_auf(anbieter, modell):
    """Der teure Fall, wenn er nicht auffaellt.

    `--provider nim` ohne `--model` beim Anthropic-Default zu lassen, heisst:
    Daten laden, Briefings bauen, und erst der erste bezahlte Aufruf scheitert
    an einem 404. Die Meldung muss beide Namen nennen, sonst raet man, welcher
    von beiden falsch war.
    """
    with pytest.raises(LLMUnavailable) as fehler:
        resolve_model(anbieter, modell)

    text = str(fehler.value)
    assert modell in text
    assert anbieter in text


def test_ein_freier_modellname_bleibt_erlaubt():
    """Die Pruefung soll Verwechslungen fangen, nicht die Auswahl bevormunden."""
    assert resolve_model("nim", "irgendein/neues-modell") == "irgendein/neues-modell"


def test_unbekannter_anbieter_wird_benannt():
    with pytest.raises(LLMUnavailable) as fehler:
        get_provider("openai")

    assert "openai" in str(fehler.value)


# ---------------------------------------------------------------------------
# Der Cache trennt die Anbieter
# ---------------------------------------------------------------------------


def test_derselbe_prompt_bei_zwei_anbietern_ergibt_zwei_cache_keys(tmp_path):
    """Der Kern der Sache.

    Modell und Effort standen schon im Key (ADR-028). Der Anbieter fehlte --
    und ein Modellname allein hindert niemanden daran, denselben Namen auf
    zwei Endpunkten zu fahren.
    """
    from qt.llm.cache import LLMCache
    from qt.llm.client import AllocatorClient

    cache = LLMCache(tmp_path, model="gleiches-modell")
    anthropic = AllocatorClient(
        model="gleiches-modell", cache=cache, provider=AnthropicProvider()
    )
    nim = AllocatorClient(model="gleiches-modell", cache=cache, provider=NimProvider())

    assert anthropic._cache_key("brief") != nim._cache_key("brief")


def test_gefuehrt_und_ungefuehrt_teilen_sich_keinen_cache_eintrag(tmp_path):
    """Der dritte Fehler desselben Abends, und der heimtueckischste.

    Nach dem Abschalten der erzwungenen Form lief der Research-Lauf erneut --
    und lieferte Zeichen fuer Zeichen dasselbe kaputte Ergebnis, weil der
    Cache die zehn alten Antworten zurueckgab. Die Korrektur sah wirkungslos
    aus, obwohl sie wirkte. Derselbe Anbieter, dieselbe Frage, zwei
    Aufrufformen: das sind zwei Antworten (ADR-041).
    """
    from qt.llm.cache import LLMCache
    from qt.llm.client import GeneratorClient

    cache = LLMCache(tmp_path, model="gleiches-modell")
    frei = GeneratorClient(
        model="gleiches-modell", cache=cache, provider=NimProvider(guided=False)
    )
    gefuehrt = GeneratorClient(
        model="gleiches-modell", cache=cache, provider=NimProvider(guided=True)
    )

    assert frei._cache_key("brief") != gefuehrt._cache_key("brief")


def test_der_anbieter_steht_in_jedem_der_vier_cache_keys(tmp_path):
    """Vier Clients, vier Keys -- und einer davon vergessen faellt nie auf."""
    from qt.llm.cache import LLMCache
    from qt.llm.client import (
        AllocatorClient,
        CriticClient,
        GeneratorClient,
        ScenarioClient,
    )

    cache = LLMCache(tmp_path, model="gleiches-modell")
    for klasse in (AllocatorClient, ScenarioClient, GeneratorClient, CriticClient):
        a = klasse(model="gleiches-modell", cache=cache, provider=AnthropicProvider())
        n = klasse(model="gleiches-modell", cache=cache, provider=NimProvider())

        assert a._cache_key("brief") != n._cache_key("brief"), klasse.__name__


# ---------------------------------------------------------------------------
# Effort: fuenf Stufen der Kommandozeile auf drei Zustaende bei NIM
# ---------------------------------------------------------------------------


def test_low_schaltet_das_denken_ab():
    provider, fake = _nim()
    _parse(provider, "low")

    kwargs = fake.calls[0]["extra_body"]["chat_template_kwargs"]
    assert kwargs["enable_thinking"] is False


def test_medium_denkt_aber_kurz():
    provider, fake = _nim()
    _parse(provider, "medium")

    kwargs = fake.calls[0]["extra_body"]["chat_template_kwargs"]
    assert kwargs["enable_thinking"] is True
    assert kwargs["medium_effort"] is True


def test_xhigh_und_max_denken_nicht_tiefer_als_high_sondern_laenger():
    """Die unbequeme Wahrheit der Abbildung, als Test festgehalten.

    NIM hat drei Denkzustaende, die Kommandozeile fuenf Stufen. `xhigh` und
    `max` sind deshalb **dieselbe** Denkstufe wie `high` mit mehr Token. Wer
    das nicht weiss, glaubt, er habe etwas eingestellt, das es nicht gibt --
    deshalb steht es hier und nicht nur in einem Kommentar.
    """
    budgets = {}
    for stufe in ("high", "xhigh", "max"):
        provider, fake = _nim()
        _parse(provider, stufe)
        budgets[stufe] = fake.calls[0]["max_tokens"]
        assert fake.calls[0]["extra_body"]["chat_template_kwargs"] == {
            "enable_thinking": True
        }

    assert budgets["high"] < budgets["xhigh"] < budgets["max"]


def test_denken_hebt_das_token_budget_an():
    """Denk-Token zaehlen gegen `max_tokens`.

    Ohne Anhebung koennte ein Aufruf sein gesamtes Budget im Gedankengang
    verbrauchen und eine abgeschnittene Antwort liefern -- bei genau dem
    Budget, das fuer die Antwort allein gedacht war.
    """
    aus, fake_aus = _nim()
    _parse(aus, "low")
    an, fake_an = _nim()
    _parse(an, "high")

    assert fake_aus.calls[0]["max_tokens"] == 1000
    assert fake_an.calls[0]["max_tokens"] > 1000


def test_unbekannte_stufe_wird_benannt():
    provider, _ = _nim()
    with pytest.raises(LLMUnavailable) as fehler:
        _parse(provider, "turbo")

    assert "turbo" in str(fehler.value)


# ---------------------------------------------------------------------------
# Strukturierte Ausgabe
# ---------------------------------------------------------------------------


def test_die_form_wird_standardmaessig_nicht_erzwungen():
    """Der teuerste Befund dieses Projekts bisher, als Test festgehalten.

    Die grammatikgesteuerte Dekodierung des gehosteten Endpunkts kann keinen
    Zeilenumbruch in einem String erzeugen. Gefuehrt kam derselbe Prompt als
    einzeilige, syntaktisch tote Klasse zurueck, ungefuehrt mit 17 Zeilen
    (ADR-041). Betroffen waere jedes Freitextfeld, auch die Begruendungen --
    dort still, ohne Fehler. Deshalb ist der Schalter aus.
    """
    provider, fake = _nim()
    _parse(provider)

    assert "response_format" not in fake.calls[0]
    system = fake.calls[0]["messages"][0]["content"]
    assert "overfitting_risk" in system, "Ohne erzwungene Form traegt der Prompt die Form"


def test_guided_bleibt_als_bewusster_schalter():
    """Fuer eine Bereitstellung, die es besser kann -- aber nur auf Ansage."""
    fake = _FakeNim()
    provider = NimProvider(client=fake, guided=True)
    provider.parse(
        system="Du pruefst Kandidaten.",
        prompt="Kandidat XY",
        schema=CandidateCritique,
        model="nvidia/nemotron-3-ultra-550b-a55b",
        max_tokens=1000,
        effort="low",
    )

    rf = fake.calls[0]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "CandidateCritique"
    assert rf["json_schema"]["schema"]["title"] == "CandidateCritique"


def test_der_eingefrorene_systemprompt_bleibt_unveraendert():
    """Die Schema-Anweisung ist eine Zutat des Anbieters, keine Prompt-Aenderung.

    Der eingefrorene Prompt in `qt.llm.client` geht in den Cache-Key ein. Wuerde
    ihn der NIM-Pfad umschreiben, waere jede Aenderung an der Anbieter-Schicht
    zugleich eine Aenderung am Experiment.
    """
    from qt.llm.client import CRITIC_SYSTEM_PROMPT, CriticClient

    provider, fake = _nim()
    CriticClient(provider=provider, model="nvidia/nemotron-3-ultra-550b-a55b")._call(
        "Kandidat XY"
    )

    assert fake.calls[0]["messages"][0]["content"].startswith(CRITIC_SYSTEM_PROMPT)


def test_gedankengang_im_text_wird_entfernt():
    """Reasoning-Modelle schreiben ihren Entwurf je nach Bereitstellung inline.

    Der Entwurf enthaelt haeufig selbst geschweifte Klammern. Wer zuerst nach
    `{` sucht, parst den Entwurf statt der Antwort.
    """
    inhalt = (
        "<think>Vielleicht {\"recommendation\": \"reject\"}? Nein, doch nicht."
        "</think>\n" + json.dumps(VERDIKT)
    )
    provider, _ = _nim([_antwort(inhalt)])

    assert _parse(provider).recommendation == "proceed"


def test_code_zaeune_werden_entfernt():
    provider, _ = _nim([_antwort("```json\n" + json.dumps(VERDIKT) + "\n```")])

    assert _parse(provider).recommendation == "proceed"


def test_antwort_ohne_json_wird_benannt():
    provider, _ = _nim([_antwort("Ich habe darueber nachgedacht, aber lieber nicht.")])

    with pytest.raises(LLMUnavailable) as fehler:
        _parse(provider)

    assert "kein JSON" in str(fehler.value)


def test_json_das_nicht_zum_schema_passt_wird_benannt():
    # Dreimal dieselbe falsche Antwort: der Provider fragt zweimal
    # korrigierend nach (siehe unten) und meldet erst dann den Fehler.
    falsch = _antwort(json.dumps({"recommendation": "vielleicht"}))
    provider, _ = _nim([falsch] * (NimProvider.MALFORMED_RETRIES + 1))

    with pytest.raises(LLMUnavailable) as fehler:
        _parse(provider)

    assert "CandidateCritique" in str(fehler.value)


# ---------------------------------------------------------------------------
# Fehlerbilder, die genau ein Reasoning-Modell erzeugt
# ---------------------------------------------------------------------------


def test_abgeschnittene_antwort_nennt_das_token_budget():
    provider, _ = _nim([_antwort('{"recommendation": "pro', finish="length")])

    with pytest.raises(LLMUnavailable) as fehler:
        _parse(provider)

    text = str(fehler.value)
    assert "abgeschnitten" in text
    assert "Denk-Token" in text


def test_leere_antwort_ist_ein_eigener_fall():
    """Alles im Gedankengang verbraucht, nichts im Inhalt.

    Sieht ohne eigenen Satz aus wie ein Schema-Fehler und wird dann an der
    falschen Stelle gesucht.
    """
    provider, _ = _nim([_antwort("")])

    with pytest.raises(LLMUnavailable) as fehler:
        _parse(provider)

    assert "leeren Inhalt" in str(fehler.value)


def test_antwort_ohne_auswahl_wird_benannt():
    provider, _ = _nim([{"choices": []}])

    with pytest.raises(LLMUnavailable) as fehler:
        _parse(provider)

    assert "choices" in str(fehler.value)


# ---------------------------------------------------------------------------
# Abstieg, wenn der Endpunkt `nvext` nicht kennt
# ---------------------------------------------------------------------------


def test_abgelehntes_response_format_fuehrt_zu_genau_einem_abstieg():
    """Nicht jede NIM-Bereitstellung kennt `response_format`.

    Der zweite Versuch verlaesst sich auf die Anweisung im Systemprompt plus
    die pydantic-Validierung -- schwaecher, aber brauchbar. Ein Lauf soll
    daran nicht scheitern.
    """
    provider, fake = _nim(
        antworten=[_antwort(json.dumps(VERDIKT))],
        raise_on=[ValueError("unknown field `response_format`, expected one of ...")],
        guided=True,
    )

    assert _parse(provider).recommendation == "proceed"
    assert len(fake.calls) == 2
    assert "response_format" in fake.calls[0]
    assert "response_format" not in fake.calls[1]


def test_der_abstieg_wird_gemerkt():
    """Ein Lauf macht hunderte Aufrufe.

    Ohne das Merken zahlte jeder einzelne den abgelehnten Versuch erneut --
    doppelte Latenz auf jedem Aufruf, ohne dass irgendetwas fehlschlaegt.
    """
    provider, fake = _nim(
        antworten=[_antwort(json.dumps(VERDIKT)), _antwort(json.dumps(VERDIKT))],
        raise_on=[ValueError("unknown field `response_format`, expected one of ...")],
        guided=True,
    )

    _parse(provider)
    _parse(provider)

    assert len(fake.calls) == 3, "Der zweite Aufruf hat es erneut gefuehrt versucht"
    assert "response_format" not in fake.calls[2]


def test_ein_echter_fehler_loest_keinen_abstieg_aus():
    """Sonst verwandelt ein falscher Schluessel sich in einen zweiten,
    genauso aussichtslosen Aufruf -- und die Folgemeldung verdeckt die
    Ursache."""
    provider, fake = _nim(raise_on=[ValueError("401 Unauthorized")], guided=True)

    with pytest.raises(LLMUnavailable) as fehler:
        _parse(provider)

    assert len(fake.calls) == 1
    assert "401" in str(fehler.value)


# ---------------------------------------------------------------------------
# Die Kommandozeile
# ---------------------------------------------------------------------------


@pytest.fixture
def alloc_kandidat(monkeypatch):
    """Faengt den Allokator ab, den `qt alloc` baut -- ohne Daten und Gate.

    Wie in `tests/test_cli_effort.py`: geprueft wird der Client, den der
    Befehl tatsaechlich zusammensetzt, nicht der Hilfetext. Eine Option, die
    nirgends ankommt, sieht im `--help` genauso aus wie eine, die wirkt.
    """
    import pandas as pd
    import typer

    import qt.data.store as store
    import qt.portfolio.gate as gate

    ts = pd.date_range("2020-01-01", periods=300, freq="4h", tz="UTC")
    close = pd.Series(range(300), dtype=float) * 0.1 + 100.0
    frame = pd.DataFrame(
        {
            "ts": ts,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1.0,
        }
    )
    monkeypatch.setattr(store, "read_bars", lambda *a, **k: frame)
    monkeypatch.setattr(store, "to_bars", lambda symbol, tf, df: [])

    gefangen: dict = {}

    def fake_run_gate(candidate, **kwargs):
        gefangen["allocator"] = candidate()
        raise typer.Exit(code=0)

    monkeypatch.setattr(gate, "run_gate", fake_run_gate)
    return gefangen


def test_alloc_reicht_den_anbieter_bis_zum_client_durch(alloc_kandidat):
    from typer.testing import CliRunner

    from qt.cli import app

    ergebnis = CliRunner().invoke(
        app, ["alloc", "--compare-baselines", "--provider", "nim"]
    )

    assert ergebnis.exit_code == 0, ergebnis.output
    client = alloc_kandidat["allocator"].client
    assert client.provider.name == "nim"
    assert client.model == NimProvider.default_model
    assert client.cache.model == NimProvider.default_model


def test_alloc_bleibt_ohne_angabe_bei_anthropic(alloc_kandidat):
    """Der Default ist eine Entscheidung und darf nicht wandern: jede bisher
    gemessene Zahl und jeder Cache-Eintrag haengt an Anthropic."""
    from typer.testing import CliRunner

    from qt.cli import app

    ergebnis = CliRunner().invoke(app, ["alloc", "--compare-baselines"])

    assert ergebnis.exit_code == 0, ergebnis.output
    assert alloc_kandidat["allocator"].client.provider.name == "anthropic"


@pytest.mark.parametrize(
    "argv",
    [
        ["alloc", "--compare-baselines", "--provider", "nim", "--model", "claude-opus-5"],
        ["alloc", "--compare-baselines", "--provider", "netflix"],
        ["research", "--provider", "nim", "--model", "claude-opus-5"],
        ["sim", "--scenarios", "--provider", "netflix"],
    ],
    ids=["alloc-fehlpaarung", "alloc-unbekannt", "research-fehlpaarung", "sim-unbekannt"],
)
def test_unsinn_bricht_ab_bevor_daten_gelesen_werden(monkeypatch, argv):
    """Abbruch beim Aufruf, nicht erst am API-Aufruf.

    Der Datenzugriff wird zur Falle: laeuft der Befehl trotzdem an, schlaegt
    er hier fehl statt still weiterzulaufen.
    """
    from typer.testing import CliRunner

    import qt.data.store as store
    from qt.cli import app

    def falle(*args, **kwargs):
        raise AssertionError("Der Lauf haette gar nicht so weit kommen duerfen.")

    monkeypatch.setattr(store, "read_bars", falle)

    ergebnis = CliRunner().invoke(app, argv)

    assert ergebnis.exit_code != 0, ergebnis.output


def test_fehlendes_sdk_nennt_den_installationsbefehl(monkeypatch):
    """`openai` ist ein optionales Extra.

    Wer bei Anthropic bleibt -- dem Default -- soll dafuer kein zweites SDK
    installieren muessen. Der Preis ist, dass der NIM-Pfad selbst erklaeren
    muss, was fehlt. `sys.modules[...] = None` laesst `import openai`
    zuverlaessig scheitern, unabhaengig davon, ob das Paket da ist.
    """
    import sys

    monkeypatch.setitem(sys.modules, "openai", None)
    provider = NimProvider(api_key="nvapi-test")

    with pytest.raises(LLMUnavailable) as fehler:
        _parse(provider)

    text = str(fehler.value)
    assert "openai" in text
    assert "--extra nim" in text


def test_ohne_schluessel_nennt_die_meldung_die_variablen():
    """Der haeufigste Fall beim ersten Versuch, und er soll sich selbst
    erklaeren -- ein Schluessel im 'falschen' Namen sieht sonst aus wie gar
    keiner."""
    pytest.importorskip("openai")
    from qt.llm.providers import NIM_KEY_VARS

    provider = NimProvider(api_key=None)
    with pytest.MonkeyPatch.context() as mp:
        for name in NIM_KEY_VARS:
            mp.delenv(name, raising=False)
        with pytest.raises(LLMUnavailable) as fehler:
            _parse(provider)

    for name in NIM_KEY_VARS:
        assert name in str(fehler.value)


# ---------------------------------------------------------------------------
# Kaputtes JSON: korrigierend nachfragen statt still zurueckfallen
# ---------------------------------------------------------------------------

# Ein nicht maskiertes Anfuehrungszeichen mitten im Begruendungstext -- genau
# die Form, an der ein echter Kritiker-Aufruf im Vorlauf zum Gate-Lauf
# gescheitert ist.
KAPUTT = (
    '{"recommendation": "reject", "overfitting_risk": 0.9, '
    '"reasoning": "die Zeile "close > 42000" bindet an ein Kursniveau"}'
)


def test_kaputtes_json_wird_korrigierend_nachgefragt():
    """Ein unmaskiertes Anfuehrungszeichen darf den Lauf nicht kippen.

    Ohne Nachfrage wird daraus im Allokator ein Rueckfall auf
    Gleichgewichtung (ADR-018) -- der Lauf misst dann streckenweise eine
    Baseline gegen sich selbst. Ueber 145 Aufrufe ist ein sporadischer
    Fehler kein Restrisiko, sondern eine Gewissheit.
    """
    provider, fake = _nim([_antwort(KAPUTT), _antwort(json.dumps(VERDIKT))])
    ergebnis = _parse(provider)

    assert ergebnis.recommendation == "proceed"
    assert provider.malformed_retries == 1
    assert len(fake.calls) == 2, "es muss ein zweiter Aufruf stattgefunden haben"


def test_die_nachfrage_nennt_den_fehler_und_die_ursache():
    # Eine Nachfrage ohne Diagnose ist derselbe Aufruf noch einmal -- und
    # liefert dieselbe kaputte Antwort.
    provider, fake = _nim([_antwort(KAPUTT), _antwort(json.dumps(VERDIKT))])
    _parse(provider)

    zweiter = fake.calls[1]["messages"][-1]["content"]
    assert "Kandidat XY" in zweiter, "der urspruengliche Auftrag muss erhalten bleiben"
    assert "kein gueltiges JSON" in zweiter
    assert "maskiert" in zweiter


def test_nach_zwei_vergeblichen_nachfragen_wird_aufgegeben():
    # Ein Modell, das dreimal kaputtes JSON liefert, liefert auch beim
    # vierten Mal kaputtes -- und jeder Versuch kostet 40-155 Sekunden.
    provider, fake = _nim([_antwort(KAPUTT)] * 3)

    with pytest.raises(LLMUnavailable, match="kein gueltiges JSON"):
        _parse(provider)

    assert len(fake.calls) == 3, "genau MALFORMED_RETRIES + 1 Versuche"
    assert provider.malformed_retries == 3


def test_eine_gueltige_antwort_fragt_nicht_nach():
    provider, fake = _nim([_antwort(json.dumps(VERDIKT))])
    _parse(provider)
    assert len(fake.calls) == 1
    assert provider.malformed_retries == 0


def test_auch_ein_schema_verstoss_wird_nachgefragt():
    # Gueltiges JSON, falsche Felder: derselbe Umgang. Das Modell kann den
    # Fehler korrigieren, wenn man ihm sagt, welcher es war.
    falsch = json.dumps({"recommendation": "vielleicht"})
    provider, fake = _nim([_antwort(falsch), _antwort(json.dumps(VERDIKT))])
    ergebnis = _parse(provider)

    assert ergebnis.recommendation == "proceed"
    assert len(fake.calls) == 2
