"""Tests fuer Schemas, Clients und die Pipeline-Orchestrierung des Research-Loops.

Der wichtigste Test in dieser Datei ist
`test_gefaehrlicher_kandidat_erreicht_weder_kritik_noch_screening`: er belegt
auf Orchestrierungsebene, was `sandbox.py` fuer sich allein schon belegt --
dass die Whitelist vor allem anderen steht. Beide Belege sind noetig, weil sie
verschiedene Fehler ausschliessen: der eine, dass die Sandbox eine Luecke hat,
der andere, dass jemand sie in der Pipeline an die falsche Stelle setzt.
"""

from __future__ import annotations

import pathlib
import tempfile

import pydantic
import pytest

from qt.llm.cache import LLMCache
from qt.llm.client import (
    CRITIC_SYSTEM_PROMPT,
    GENERATOR_SYSTEM_PROMPT,
    CriticClient,
    GeneratorClient,
    StubCriticClient,
    StubGeneratorClient,
)
from qt.llm.schemas import CandidateCritique, StrategyCandidateProposal
from qt.research import sandbox
from qt.research.critic import build_critique_briefing, critique
from qt.research.generator import build_generation_briefing, generate_batch
from qt.research.loop import ResearchTelemetry, run_research_loop
from qt.research.registry import ResearchRegistry

# --------------------------------------------------------------------------
# Hilfsmittel
# --------------------------------------------------------------------------

GEFAEHRLICH = """class Boese(Strategy):
    name = "boese"
    warmup_bars = 5

    def on_bar(self, symbol, store):
        return ().__class__.__base__.__subclasses__()
"""


class ZaehlenderKritiker:
    """Kritiker, der mitschreibt, ob er ueberhaupt gefragt wurde."""

    def __init__(self, recommendation: str = "proceed") -> None:
        self.calls = 0
        self.model = "zaehler"
        self.effort = "low"
        self._recommendation = recommendation

    def critique(self, briefing: str) -> CandidateCritique:
        self.calls += 1
        return CandidateCritique(
            recommendation=self._recommendation, reasoning="Testurteil"
        )


class FesterGenerator:
    """Generator, der immer denselben Kandidaten liefert."""

    def __init__(self, code: str, class_name: str, name: str = "fest") -> None:
        self.model = "fest"
        self.effort = "stub"
        self.calls = 0
        self._proposal = StrategyCandidateProposal(
            name=name, class_name=class_name, code=code, rationale="Testkandidat"
        )

    def propose(self, briefing: str) -> StrategyCandidateProposal:
        self.calls += 1
        return self._proposal


@pytest.fixture
def registry():
    with tempfile.TemporaryDirectory() as tmp:
        reg = ResearchRegistry.open(pathlib.Path(tmp) / "registry.duckdb")
        yield reg
        reg.close()


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


def test_kandidat_mit_klassenname_der_im_code_fehlt_wird_abgelehnt():
    """Zwei verschiedene Fehler sollen zwei verschiedene Meldungen ergeben.

    Ohne diese Pruefung faellt eine Verwechslung erst beim Laden auf, und zwar
    als `SandboxRejected` -- eine Meldung, die nach einem Sicherheitsproblem
    klingt, obwohl das Modell nur zwei Felder nicht abgeglichen hat.
    """
    with pytest.raises(pydantic.ValidationError, match="kommt im Code nicht vor"):
        StrategyCandidateProposal(
            name="x", class_name="Fehlt", code="class Andere:\n    pass\n"
        )


def test_kandidatenname_muss_klein_geschrieben_sein():
    with pytest.raises(pydantic.ValidationError):
        StrategyCandidateProposal(
            name="GrossGeschrieben", class_name="X", code="class X:\n    pass\n"
        )


def test_leerer_code_wird_abgelehnt():
    with pytest.raises(pydantic.ValidationError):
        StrategyCandidateProposal(name="x", class_name="X", code="   \n  ")


def test_unbekannte_empfehlung_wird_abgelehnt():
    with pytest.raises(pydantic.ValidationError):
        CandidateCritique(recommendation="vielleicht")


def test_widerspruechliche_kritik_wird_als_solche_erkannt():
    """Durchwinken trotz mehrerer Maengel ist kein Fehler, aber ein Befund."""
    verdict = CandidateCritique(
        recommendation="proceed",
        magic_price_constants=True,
        unrealistic_turnover=True,
    )
    assert verdict.contradictory
    assert len(verdict.flags) == 2

    sauber = CandidateCritique(recommendation="proceed")
    assert not sauber.contradictory


def test_ueberlanger_code_wird_abgelehnt():
    with pytest.raises(pydantic.ValidationError):
        StrategyCandidateProposal(
            name="x", class_name="X", code="class X:\n    pass\n" + "#" * 9000
        )


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------


def test_stub_generator_erzeugt_code_der_die_echte_sandbox_besteht():
    """Ein Stub, der die eigene Sandbox nicht besteht, blockiert jeden Offline-Lauf.

    Und schlimmer: der Fehler saehe im Trichter wie ein Befund ueber den
    Generator aus, statt wie ein kaputter Stub.
    """
    client = StubGeneratorClient()
    for _ in range(len(StubGeneratorClient.LOOKBACKS) + 2):
        proposal = client.propose("brief")
        report = sandbox.check(proposal.code)
        assert report.ok, f"{proposal.name}: {report.reasons}"


def test_stub_generator_ist_deterministisch_aber_nicht_eintoenig():
    erste = StubGeneratorClient()
    zweite = StubGeneratorClient()
    a1, a2 = erste.propose("x"), erste.propose("x")
    b1, b2 = zweite.propose("x"), zweite.propose("x")

    assert a1.code == b1.code and a2.code == b2.code, "gleicher Index, gleiche Antwort"
    assert a1.code != a2.code, "verschiedene Indizes muessen sich unterscheiden"


def test_stub_kritiker_laesst_durch_und_setzt_keine_flags():
    """Der neutrale Zustand ist "nicht blockieren" -- siehe StubScenarioClient."""
    verdict = StubCriticClient().critique("brief")
    assert verdict.recommendation == "proceed"
    assert verdict.flags == []


def test_stub_generator_kann_einen_festen_kandidaten_liefern():
    fest = StrategyCandidateProposal(
        name="fest", class_name="X", code="class X:\n    pass\n"
    )
    client = StubGeneratorClient(proposal=fest)
    assert client.propose("a").code == client.propose("b").code == fest.code


# --------------------------------------------------------------------------
# Cache-Key
# --------------------------------------------------------------------------


def test_effort_und_rolle_stehen_im_cache_key(tmp_path):
    """Sonst bekaeme ein Aufruf die Antwort einer anderen Einstellung.

    Derselbe Fehler war schon einmal echt: ein Cache-Key, der das Modell des
    Caches trug statt das des Clients.
    """
    cache = LLMCache(path=tmp_path)
    briefing = "derselbe Kandidat"

    billig = GeneratorClient(cache=cache, effort="low")._cache_key(briefing)
    teuer = GeneratorClient(cache=cache, effort="max")._cache_key(briefing)
    assert billig != teuer

    generator = GeneratorClient(cache=cache, effort="low")._cache_key(briefing)
    kritiker = CriticClient(cache=cache, effort="low")._cache_key(briefing)
    assert generator != kritiker, "Generator und Kritik duerfen sich nie teilen"


def test_ohne_cache_gibt_es_keinen_key():
    assert GeneratorClient(cache=None)._cache_key("x") is None
    assert CriticClient(cache=None)._cache_key("x") is None


def test_kritik_ist_per_default_billiger_als_der_generator():
    """Der Unterschied gehoert in den Code, nicht nur in die Doku.

    Sonst wird aus dem billigen Vorfilter beim naechsten Lauf unbemerkt ein
    teurer, und die Kostenrechnung aus ADR-027/028 stimmt still nicht mehr.
    """
    assert CriticClient().effort == "low"
    assert GeneratorClient().effort == "medium"


# --------------------------------------------------------------------------
# Briefings
# --------------------------------------------------------------------------


def test_generator_briefing_enthaelt_keine_marktdaten():
    """Der Generator sieht keine einzige datenabgeleitete Zahl (ADR-003/017).

    Was er nicht sieht, kann er nicht hineinoptimieren.
    """
    briefing = build_generation_briefing(0, 20, [])
    verboten = ("BTC", "ETH", "USD", "2024", "2023", "Sharpe", "Rendite", "Kurs")
    for token in verboten:
        assert token not in briefing, f"{token!r} gehoert nicht ins Generator-Briefing"


def test_generator_briefing_nennt_bisherige_ansaetze():
    ohne = build_generation_briefing(0, 5, [])
    mit = build_generation_briefing(3, 5, ["a: Idee A", "b: Idee B"])
    assert "a: Idee A" in mit and "b: Idee B" in mit
    assert "a: Idee A" not in ohne


def test_kritik_briefing_nennt_bars_und_kein_datum():
    """Bars statt Kalenderzeit -- dieselbe Konvention wie ADR-017.

    Fuer die Freiheitsgrade-Einschaetzung reicht die Groessenordnung, fuer das
    Wiedererkennen eines Zeitraums nicht.
    """
    proposal = StrategyCandidateProposal(
        name="x", class_name="X", code="class X:\n    LOOKBACK = 20\n    pass\n"
    )
    briefing = build_critique_briefing(proposal, oos_bars=8800)
    assert "Bars" in briefing
    for token in ("2024", "2023", "Januar", "BTC", "ETH"):
        assert token not in briefing


def test_kritik_briefing_zaehlt_konstanten_selbst():
    """Deterministisch ermittelt, nicht vom Modell geschaetzt.

    Die Zahl der Freiheitsgrade ist genau die Groesse, ueber die der Kritiker
    urteilen soll -- ihn selbst zaehlen zu lassen, hiesse den Massstab von dem
    ableiten, was gemessen wird.
    """
    code = "class X(Strategy):\n    LOOKBACK = 20\n    THRESHOLD = 2.0\n    SPAN = 5\n"
    proposal = StrategyCandidateProposal(name="x", class_name="X", code=code)
    assert "3 Klassenkonstanten" in build_critique_briefing(proposal)


# --------------------------------------------------------------------------
# Kritik-Ausfall
# --------------------------------------------------------------------------


def test_ausfall_der_kritik_laesst_durch_statt_zu_blockieren():
    """Ein Vorfilter, der bei einem Netzwerkfehler alles anhaelt, hat aus einer
    Sparmassnahme einen Single Point of Failure gemacht."""

    class KaputterKritiker:
        model = "kaputt"
        effort = "low"

        def critique(self, briefing):
            raise RuntimeError("Netz weg")

    proposal = StrategyCandidateProposal(
        name="x", class_name="X", code="class X:\n    pass\n"
    )
    verdict, error = critique(KaputterKritiker(), proposal)
    assert verdict.recommendation == "proceed"
    assert error is not None and "RuntimeError" in error


# --------------------------------------------------------------------------
# Generator-Charge
# --------------------------------------------------------------------------


def test_eine_charge_stirbt_nicht_am_ausfall_eines_einzelnen_aufrufs():
    """Zwanzig Kandidaten wegzuwerfen, weil der neunzehnte Aufruf scheiterte,
    waere die teuerste denkbare Reaktion auf einen Netzwerkfehler."""

    class ManchmalKaputt(StubGeneratorClient):
        def propose(self, briefing):
            if self.calls == 1:
                self.calls += 1
                raise RuntimeError("Netz weg")
            return super().propose(briefing)

    kandidaten = generate_batch(ManchmalKaputt(), 4)
    assert len(kandidaten) == 3


# --------------------------------------------------------------------------
# Die Pipeline
# --------------------------------------------------------------------------


def _bars(n: int = 4000):
    from qt.data.store import read_bars, to_bars

    return {"BTC/USD": to_bars("BTC/USD", "4h", read_bars("BTC/USD", "4h"))[:n]}


@pytest.mark.slow
def test_gefaehrlicher_kandidat_erreicht_weder_kritik_noch_screening(registry):
    """Der End-to-End-Beleg fuer "Sandbox zuerst, ausnahmslos".

    `sandbox.py` belegt fuer sich, dass die Whitelist haelt. Dieser Test
    belegt, dass die Pipeline sie auch an der richtigen Stelle aufruft -- zwei
    verschiedene Fehler, zwei verschiedene Belege.
    """
    kritiker = ZaehlenderKritiker()
    telemetry = run_research_loop(
        1,
        _bars(),
        ["BTC/USD"],
        "4h",
        FesterGenerator(GEFAEHRLICH, "Boese", name="boese"),
        kritiker,
        registry=registry,
        train_bars=1500,
        test_bars=400,
        embargo_bars=20,
    )

    assert telemetry.sandbox_rejected == 1
    assert telemetry.screened == 0
    assert kritiker.calls == 0, "die Kritik darf gefaehrlichen Code nie sehen"
    assert registry.trial_count() == 0, "eine Ablehnung ist kein Versuch"


@pytest.mark.slow
def test_kritik_ablehnung_ueberspringt_das_screening_verwirft_aber_nichts(registry):
    """Eine Ablehnung ist ein protokollierter Skip, kein Loeschen.

    Der Kandidat liegt samt Begruendung in der Registry -- wer die Begruendung
    fuer falsch haelt, kann ihn mit --no-critic erneut laufen lassen.
    """
    telemetry = run_research_loop(
        1,
        _bars(),
        ["BTC/USD"],
        "4h",
        StubGeneratorClient(),
        ZaehlenderKritiker(recommendation="reject"),
        registry=registry,
        train_bars=1500,
        test_bars=400,
        embargo_bars=20,
    )

    assert telemetry.critic_rejected == 1
    assert telemetry.screened == 0
    assert registry.trial_count() == 0, "wer nie gegen Daten lief, ist kein Versuch"

    abgelehnt = registry.history()
    assert len(abgelehnt) == 1, "der Kandidat ist erfasst, nicht verschwunden"
    assert abgelehnt.iloc[0]["critic_recommendation"] == "reject"
    assert abgelehnt.iloc[0]["code"], "der Quelltext bleibt lesbar"


@pytest.mark.slow
def test_no_critic_ueberspringt_die_stufe_ganz(registry):
    kritiker = ZaehlenderKritiker(recommendation="reject")
    telemetry = run_research_loop(
        1,
        _bars(),
        ["BTC/USD"],
        "4h",
        StubGeneratorClient(),
        kritiker,
        registry=registry,
        train_bars=1500,
        test_bars=400,
        embargo_bars=20,
        use_critic=False,
    )

    assert kritiker.calls == 0
    assert telemetry.critic_rejected == 0
    assert telemetry.screened == 1, "ohne Kritik geht der Kandidat direkt weiter"


@pytest.mark.slow
def test_versuchszaehler_waechst_ueber_mehrere_laeufe(registry):
    """Die DSR muss gegen alle je getesteten Kandidaten korrigieren, nicht nur
    gegen die dieser Sitzung -- sonst faellt der Schutz bei jedem Neustart auf
    null zurueck und taeuscht Sicherheit vor."""
    bars = _bars()
    for _ in range(2):
        run_research_loop(
            1,
            bars,
            ["BTC/USD"],
            "4h",
            StubGeneratorClient(),
            StubCriticClient(),
            registry=registry,
            train_bars=1500,
            test_bars=400,
            embargo_bars=20,
        )

    assert registry.trial_count() == 2
    zeilen = registry.history()
    gezaehlt = sorted(int(v) for v in zeilen["trial_count_at_screening"])
    assert gezaehlt == [1, 2], "jeder Kandidat zaehlt sich selbst mit"


def test_ohne_registry_verweigert_der_loop_den_dienst():
    """Ein Versuchszaehler im Arbeitsspeicher waere schlimmer als keiner."""
    with pytest.raises(ValueError, match="persistenten Versuchszaehler"):
        run_research_loop(
            1, {}, ["BTC/USD"], "4h", StubGeneratorClient(), None, registry=None
        )


def test_telemetrie_tabelle_nennt_jede_stufe():
    """Ein Loop, dessen Ausfaelle niemand sieht, wird fuer gut gehalten."""
    tabelle = ResearchTelemetry(generated=20, sandbox_rejected=19, screened=1).table()
    for label in ("erzeugt", "Sandbox verworfen", "Kritik abgelehnt", "DSR bestanden"):
        assert label in tabelle


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------


def test_generator_prompt_lehrt_die_form_die_die_sandbox_akzeptiert():
    """Ein Prompt, der eine verbotene Form lehrt, erzeugt bezahlte Ablehnungen.

    Genau das war der erste Entwurf: er zeigte `__init__` mit `super()` und
    `@property` -- alle drei von der Sandbox verboten.
    """
    prompt = GENERATOR_SYSTEM_PROMPT.lower()
    assert "kein `__init__`" in prompt and "kein `super()`" in prompt
    assert "warmup_bars = " in GENERATOR_SYSTEM_PROMPT
    # Im Beispielblock selbst darf kein Dekorator stehen -- er waere die Form,
    # die das Modell nachahmt, und genau die verbietet die Sandbox.
    assert "@property" not in GENERATOR_SYSTEM_PROMPT.split("Drei Dinge")[0]
    assert "`all`" in prompt and "`any`" in prompt


def test_das_beispiel_im_generator_prompt_besteht_die_sandbox():
    """Der staerkste Test fuer den Prompt: sein eigenes Beispiel durchschicken.

    Laeuft die Sandbox dem Prompt davon, faellt es hier auf und nicht erst an
    einer Ablehnungsquote von hundert Prozent.
    """
    beginn = GENERATOR_SYSTEM_PROMPT.index("    class ZReversion(Strategy):")
    ende = GENERATOR_SYSTEM_PROMPT.index("Drei Dinge daran")
    block = GENERATOR_SYSTEM_PROMPT[beginn:ende]
    code = "\n".join(
        zeile[4:] if zeile.startswith("    ") else zeile
        for zeile in block.rstrip().splitlines()
    )
    report = sandbox.check(code)
    assert report.ok, f"das eigene Prompt-Beispiel wird abgelehnt: {report.reasons}"


def test_kritik_prompt_haelt_den_kritiker_vom_dauerablehnen_ab():
    """Ein Kritiker, der jeden Kandidaten ablehnt, filtert nichts."""
    assert "proceed" in CRITIC_SYSTEM_PROMPT
    assert "blockiert" in CRITIC_SYSTEM_PROMPT


# --------------------------------------------------------------------------
# Uebernahme-Ausgabe
# --------------------------------------------------------------------------


def test_show_gibt_ein_modul_aus_das_wirklich_laeuft():
    """`--show` druckt einen Block zum Einfuegen -- er muss auch importierbar sein.

    Ohne diesen Test druckt der Befehl etwas, das beim Einfuegen bricht, und
    das faellt erst dem Menschen auf, der gerade eine Freigabe erteilen wollte.
    """
    import textwrap

    from qt.cli import _promotable_module
    from qt.strategy.base import Strategy

    row = {
        "id": "abc-123",
        "class_name": "StubReversion1",
        "rationale": "Testkandidat",
        "dsr": 0.42,
        "trial_count_at_screening": 7,
        "sharpe": -1.5,
        "n_windows": 8,
        "oos_bars": 6400,
        "code": StubGeneratorClient().propose("x").code,
    }
    modul = _promotable_module(row)

    namespace: dict = {}
    exec(compile(modul, "<uebernahme>", "exec"), namespace)  # noqa: S102
    cls = namespace["StubReversion1"]
    assert issubclass(cls, Strategy)

    # Der Kopf muss die Herkunft tragen: ohne Kandidaten-ID und Versuchszahl
    # ist ein halbes Jahr spaeter nicht mehr einzuordnen, wie ernst der Sharpe
    # zu nehmen ist.
    assert "abc-123" in modul
    assert "gegen 7 Versuche" in modul
    assert textwrap.dedent(modul).startswith('"""')


def test_modulname_folgt_dem_klassennamen():
    from qt.cli import _module_name

    assert _module_name({"class_name": "DonchianTrend"}) == "donchian_trend"
    assert _module_name({"class_name": "StubReversion1"}) == "stub_reversion1"


# ---------------------------------------------------------------------------
# Die letzte Stufe der Kette (ADR-065)
# ---------------------------------------------------------------------------


def _kandidat_mit_signal(signale):
    """Strategie-Klasse, die einen vorgegebenen Gewichtsverlauf abspielt."""
    from qt.research.placebo import PlaybackStrategy

    class _Abspieler(PlaybackStrategy):
        def __init__(self, symbols, timeframe, **kw):
            super().__init__(symbols, timeframe, signals=signale, warmup_bars=22)

    return _Abspieler


def test_die_kette_endet_nicht_bei_der_dsr():
    """Phase C.3 verlangt die Negativkontrolle **nach** der DSR.

    Bis ADR-065 hoerte `screen_candidate` nach der DSR auf, und die Kette
    stand nur in `docs/ZIEL.md`. Bemerkt hat es niemand, weil nie ein
    Kandidat bis dorthin kam -- eine fehlende Stufe hinter einer nie
    genommenen Huerde sieht aus wie eine vorhandene.
    """
    import inspect

    from qt.research import screening

    quelle = inspect.getsource(screening.screen_candidate)
    assert "permutation_control" in quelle
    assert quelle.index("permutation_control") > quelle.index("dsr_from_returns"), (
        "die Kontrolle muss nach der DSR laufen -- 200 Ziehungen kosten ein "
        "Vielfaches des Screenings"
    )


def test_ein_kandidat_faellt_an_der_negativkontrolle_durch():
    """Der Fall, fuer den die Stufe da ist: DSR bestanden, Placebo nicht."""
    import numpy as np

    from qt.research.screening import screen_candidate
    from tests.conftest import make_bars

    bars = make_bars(700, "BTC/USD", "1d", seed=3)
    rng = np.random.default_rng(5)
    roh = rng.choice([0.0, 1.0], size=len(bars))
    geglaettet = np.repeat(roh[::20], 20)[: len(bars)]
    signale = {
        (b.symbol, b.ts): float(w) for b, w in zip(bars, geglaettet, strict=True)
    }

    ergebnis = screen_candidate(
        _kandidat_mit_signal(signale),
        {"BTC/USD": bars},
        ["BTC/USD"],
        "1d",
        trial_count=1,
        train_bars=250,
        test_bars=120,
        embargo_bars=10,
        dsr_threshold=0.001,  # DSR faktisch aushebeln, damit die Kontrolle drankommt
    )

    assert ergebnis.placebo_perzentil is not None, "die Kontrolle ist gar nicht gelaufen"
    assert not ergebnis.passed
    assert "Placebo-Perzentil" in ergebnis.reason
    assert "Placebo" in ergebnis.summary()


def test_ohne_bestandene_dsr_laeuft_die_teure_kontrolle_nicht():
    """Nach Kosten sortiert, wie der ganze uebrige Trichter."""
    from qt.research.screening import screen_candidate
    from tests.conftest import make_bars

    bars = make_bars(700, "BTC/USD", "1d", seed=3)
    signale = {(b.symbol, b.ts): 1.0 for b in bars}

    ergebnis = screen_candidate(
        _kandidat_mit_signal(signale),
        {"BTC/USD": bars},
        ["BTC/USD"],
        "1d",
        trial_count=50,
        train_bars=250,
        test_bars=120,
        embargo_bars=10,
        dsr_threshold=0.95,
    )

    assert not ergebnis.passed
    assert ergebnis.placebo_perzentil is None, (
        "ein an der DSR gescheiterter Kandidat ist tot -- 200 Ziehungen "
        "waeren verschwendet"
    )
