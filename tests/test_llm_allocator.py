"""Blind Briefing, Schemas und LLM-Allokator.

Kein Test hier ruft ein Sprachmodell auf. Das ist Absicht: geprueft wird die
**Verdrahtung**, nicht das Modell. Ob das Modell gut allokiert, entscheidet
das Gate (`tests/test_gate.py`); ob ein schlechtes Modell das System
beschaedigen kann, entscheidet sich hier.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from qt.llm.briefing import build, label_for
from qt.llm.client import LLMUnavailable, StubClient
from qt.llm.schemas import AllocationProposal
from qt.portfolio.base import AllocationContext
from qt.portfolio.llm_allocator import LLMAllocator

TS = dt.datetime(2020, 3, 15, 8, 0, tzinfo=dt.timezone.utc)


def make_ctx(n: int = 500, seed: int = 0, equity: float = 87_432.19) -> AllocationContext:
    rng = np.random.default_rng(seed)
    return AllocationContext(
        ts=TS,
        strategy_ids=["trend", "meanrev"],
        returns={
            "trend": rng.normal(0.0003, 0.01, n),
            "meanrev": rng.normal(-0.0001, 0.02, n),
        },
        equity=equity,
        current={"trend": 0.6, "meanrev": 0.4},
        timeframe="4h",
    )


# ---------------------------------------------------------------------------
# Blind Briefing -- der Kern von ADR-003
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    ["2020", "03-15", "March", "trend", "meanrev", "87432", "87,432", "BTC", "USD"],
)
def test_briefing_leaks_neither_time_nor_names(forbidden):
    """Der wichtigste Test der Phase.

    Ein Sprachmodell kennt die Vergangenheit. Steht im Briefing ein Datum,
    ein Asset- oder ein Strategiename, kann es mit Rueckschau allokieren --
    und der Backtest waere wertlos, ohne dass irgendwo ein Bug ist.
    """
    prompt = build(make_ctx()).to_prompt()

    assert forbidden not in prompt


def test_briefing_leaks_no_absolute_equity():
    """Der Kontostand sagt nichts ueber die richtige Allokation, waere aber
    ueber die Groessenordnung ein Hinweis auf den Zeitpunkt."""
    small = build(make_ctx(equity=1_000.0)).to_prompt()
    large = build(make_ctx(equity=50_000_000.0)).to_prompt()

    assert small == large, "Der Kontostand veraendert das Briefing"


def test_briefing_is_byte_stable_for_the_same_state():
    """Voraussetzung fuer den Cache.

    Ein Briefing mit wandernden Zahlen oder unsortierten Schluesseln erzeugt
    bei jedem Lauf einen anderen Cache-Key -- der Cache liefe dann mit 0%
    Trefferquote, ohne dass etwas fehlschlaegt.
    """
    assert build(make_ctx()).to_prompt() == build(make_ctx()).to_prompt()


def test_briefing_changes_when_the_market_changes():
    """Gegenprobe zur Stabilitaet: anonym heisst nicht inhaltsleer."""
    assert build(make_ctx(seed=1)).to_prompt() != build(make_ctx(seed=2)).to_prompt()


def test_briefing_reports_unknown_instead_of_zero():
    """Zu kurze Historie ergibt `None`, nicht 0.

    Dem Modell eine Null zu zeigen waere eine Luege ueber die Datenlage --
    es wuerde eine Strategie fuer renditelos halten statt fuer unbekannt.
    """
    ctx = make_ctx(n=30)
    windows = build(ctx).payload["strategies"]["STRAT_A"]["windows"]

    assert windows["24"] is not None
    assert windows["384"] is None


def test_labels_are_deterministic_and_reversible():
    briefing = build(make_ctx())

    assert briefing.labels == ["STRAT_A", "STRAT_B"]
    assert briefing.resolve({"STRAT_A": 0.7, "STRAT_B": 0.3}) == {
        "meanrev": 0.7,
        "trend": 0.3,
    }
    assert label_for(0) == "STRAT_A"
    assert label_for(25) == "STRAT_Z"
    assert label_for(26) == "STRAT_AA"


def test_correlation_is_reported():
    """Zwei Strategien mit Korrelation 0,95 sind eine Strategie in doppelter
    Groesse -- ohne diesen Wert kann das Modell nicht sinnvoll streuen."""
    payload = build(make_ctx()).payload

    assert "STRAT_A|STRAT_B" in payload["correlations"]


# ---------------------------------------------------------------------------
# Schemas -- was das Modell zurueckgeben darf
# ---------------------------------------------------------------------------


def test_hallucinated_strategy_is_dropped_not_guessed():
    """Ein erfundenes Label ist ein Fehler des Modells, und ein Fehler wird
    zu nichts -- nicht zu einer Position in irgendeiner echten Strategie."""
    proposal = AllocationProposal(
        allocations=[
            {"strategy_id": "STRAT_A", "weight": 0.5},
            {"strategy_id": "STRAT_ERFUNDEN", "weight": 0.5},
        ]
    )

    assert proposal.as_allocation(["STRAT_A", "STRAT_B"]) == {
        "STRAT_A": 0.5,
        "STRAT_B": 0.0,
    }
    assert proposal.unknown_labels(["STRAT_A", "STRAT_B"]) == ["STRAT_ERFUNDEN"]


@pytest.mark.parametrize("weight", [5.0, -3.0, float("inf"), float("nan")])
def test_out_of_range_weights_are_rejected(weight):
    with pytest.raises(Exception):
        AllocationProposal(allocations=[{"strategy_id": "STRAT_A", "weight": weight}])


def test_empty_allocation_list_is_rejected():
    with pytest.raises(Exception):
        AllocationProposal(allocations=[])


# ---------------------------------------------------------------------------
# Allokator -- Verhalten bei Stoerungen
# ---------------------------------------------------------------------------


def test_no_call_before_warmup():
    """Das Modell zu fragen, bevor es ueber irgendeine Strategie etwas weiss,
    erzeugt eine geratene Antwort und kostet trotzdem Geld."""
    stub = StubClient()
    allocator = LLMAllocator(client=stub, min_history=96)

    result = allocator.allocate(make_ctx(n=10))

    assert stub.calls == 0
    assert result == {"meanrev": 0.5, "trend": 0.5}


class _Raising:
    """Client, der genau so ausfaellt, wie es live passiert."""

    model = "kaputt"

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def propose(self, briefing):
        raise self.exc


@pytest.mark.parametrize(
    "exc",
    [
        LLMUnavailable("kein Zugang"),
        RuntimeError("Netzwerk weg"),
        ValueError("Schema kaputt"),
        KeyError("unerwartet"),
    ],
    ids=["nicht-verfuegbar", "netzwerk", "schema", "unerwartet"],
)
def test_any_failure_falls_back_to_equal_weight(exc):
    """Ein Ausfall des Modells muss ein langweiliges Ereignis sein.

    Ein Allokator, der bei einem Netzwerkfehler wirft, reisst im Livebetrieb
    das System mit -- und zwar genau dann, wenn die Verbindung ohnehin
    schlecht ist.
    """
    allocator = LLMAllocator(client=_Raising(exc), min_history=10)

    result = allocator.allocate(make_ctx(n=300))

    assert result == {"meanrev": 0.5, "trend": 0.5}
    assert allocator.telemetry.fallbacks == 1
    assert allocator.telemetry.fallback_rate == 1.0


def test_explicit_flat_is_honoured():
    """"Ich sehe gerade keine Kante" ist eine legitime Meinung.

    Wuerde sie wie eine verstuemmelte Antwort behandelt, koennte der Allokator
    nie aussteigen -- und ein Modell, das aussteigen will, bekaeme
    ausgerechnet volle Gleichgewichtung.
    """
    flat = AllocationProposal(
        allocations=[
            {"strategy_id": "STRAT_A", "weight": 0.0},
            {"strategy_id": "STRAT_B", "weight": 0.0},
        ]
    )
    allocator = LLMAllocator(client=StubClient(flat), min_history=10)

    assert allocator.allocate(make_ctx(n=300)) == {"meanrev": 0.0, "trend": 0.0}
    assert allocator.telemetry.fallbacks == 0
    assert allocator.telemetry.deliberate_flats == 1


def test_partial_zero_proposal_falls_back():
    """Nennt das Modell nur einen Teil der Strategien, bleibt es mehrdeutig.

    Von einer halben oder abgeschnittenen Antwort ist das nicht zu
    unterscheiden -- und im Zweifel gehoert das Portfolio nicht flat.
    """
    partial = AllocationProposal(
        allocations=[{"strategy_id": "STRAT_A", "weight": 0.0}]
    )
    allocator = LLMAllocator(client=StubClient(partial), min_history=10)

    assert allocator.allocate(make_ctx(n=300)) == {"meanrev": 0.5, "trend": 0.5}
    assert allocator.telemetry.fallbacks == 1
    assert allocator.telemetry.deliberate_flats == 0


def test_valid_proposal_is_applied_and_resolved():
    proposal = AllocationProposal(
        allocations=[
            {"strategy_id": "STRAT_A", "weight": 0.25},
            {"strategy_id": "STRAT_B", "weight": 0.75},
        ]
    )
    allocator = LLMAllocator(client=StubClient(proposal), min_history=10)

    # STRAT_A ist meanrev (alphabetisch), STRAT_B ist trend.
    assert allocator.allocate(make_ctx(n=300)) == {"meanrev": 0.25, "trend": 0.75}
    assert allocator.telemetry.fallbacks == 0


def test_telemetry_makes_silent_fallbacks_visible():
    """Ein Allokator, der dauerhaft zurueckfaellt, ist heimlich eine Baseline.

    Ohne diese Zahl haelt man ihn fuer gut, weil er nie auffaellt.
    """
    allocator = LLMAllocator(client=_Raising(RuntimeError("weg")), min_history=10)
    for _ in range(4):
        allocator.allocate(make_ctx(n=300))

    assert allocator.telemetry.fallback_rate == 1.0
    assert "Rueckfaelle" in allocator.telemetry.summary()


# ---------------------------------------------------------------------------
# Client und Cache im Zusammenspiel
# ---------------------------------------------------------------------------


class _Response:
    stop_reason = "end_turn"
    stop_details = None
    parsed_output = AllocationProposal(
        allocations=[
            {"strategy_id": "STRAT_A", "weight": 0.3},
            {"strategy_id": "STRAT_B", "weight": 0.7},
        ]
    )


class _FakeMessages:
    def __init__(self, owner: "_CountingClient") -> None:
        self._owner = owner

    def parse(self, **kwargs):
        self._owner.requests += 1
        self._owner.last_kwargs = kwargs
        return _Response()


class _FakeSDK:
    """Was `anthropic.Anthropic()` an dieser Stelle sein muss: `.messages.parse`."""

    def __init__(self, owner: "_CountingClient") -> None:
        self.messages = _FakeMessages(owner)


class _CountingClient:
    """Client, der das Modell zaehlt statt es zu rufen.

    Ersetzt ausschliesslich das SDK-Objekt hinter dem Provider -- der gesamte
    Rest des Codepfads (Cache-Key, Aufrufaufbau, Auswertung von `stop_reason`
    und `parsed_output`, Fehleruebersetzung) laeuft echt. Ein Stub, der den
    ganzen Client ersetzt, wuerde genau die Verdrahtung ungeprueft lassen.
    """

    def __init__(self, cache, model: str = "claude-opus-5", effort: str = "medium"):
        from qt.llm.client import AllocatorClient
        from qt.llm.providers import AnthropicProvider

        self.requests = 0
        self.last_kwargs: dict = {}
        self.inner = AllocatorClient(
            model=model,
            cache=cache,
            effort=effort,
            provider=AnthropicProvider(client=_FakeSDK(self)),
        )


def test_second_call_is_served_from_cache(tmp_path):
    """Ohne Cache waere ein Backtest ueber den Allokator weder reproduzierbar
    noch bezahlbar -- er ruft das Modell hunderte Male."""
    from qt.llm.cache import LLMCache

    cache = LLMCache(tmp_path)
    counting = _CountingClient(cache)
    brief = build(make_ctx())

    first = counting.inner.propose(brief)
    second = counting.inner.propose(brief)

    assert counting.requests == 1, "Zweiter Aufruf ging ans Modell statt an den Cache"
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.proposal.as_allocation(brief.labels) == first.proposal.as_allocation(
        brief.labels
    )


def test_cache_does_not_serve_another_models_answer(tmp_path):
    """Der Cache-Key muss das Modell des **Clients** enthalten.

    Sonst liefert ein Client mit abweichendem Modell stillschweigend die
    Antwort eines anderen -- kein Fehlschlag, nur ein falsches Ergebnis.
    """
    from qt.llm.cache import LLMCache

    cache = LLMCache(tmp_path)
    opus = _CountingClient(cache, model="claude-opus-5")
    sonnet = _CountingClient(cache, model="claude-sonnet-5")
    brief = build(make_ctx())

    opus.inner.propose(brief)
    result = sonnet.inner.propose(brief)

    assert result.from_cache is False
    assert sonnet.requests == 1


def test_telemetrie_summiert_ueber_fenster_statt_nur_das_letzte_zu_zeigen():
    """Die Rueckfallquote muss den ganzen Lauf beschreiben, nicht ein Fenster.

    Der Gate-Lauf baut pro Walk-Forward-Fenster einen frischen Allokator.
    Frueher merkte sich `qt alloc` nur das zuletzt gebaute Telemetrie-Objekt
    -- bei 29 Fenstern zu je 5 Aufrufen beschrieb die gemeldete Quote also
    5 von 145 Aufrufen. Genau der Fall unten: 28 Fenster fallen komplett
    zurueck, das letzte nicht. Die Einzelansicht meldet 0%, der Lauf lag bei
    97%. Das ist die Zahl, an der man heimliche Gleichgewichtung erkennt
    (ADR-018) -- sie darf nicht am Stichprobenfenster haengen.
    """
    from qt.portfolio.llm_allocator import AllocatorTelemetry

    fenster = []
    for _ in range(28):
        t = AllocatorTelemetry()
        t.calls, t.fallbacks = 5, 5
        fenster.append(t)
    letztes = AllocatorTelemetry()
    letztes.calls, letztes.fallbacks = 5, 0
    fenster.append(letztes)

    assert letztes.fallback_rate == 0.0, "Ausgangslage: das letzte Fenster ist sauber"

    gesamt = AllocatorTelemetry()
    for t in fenster:
        gesamt.merge(t)

    assert gesamt.calls == 145
    assert gesamt.fallbacks == 140
    assert gesamt.fallback_rate > 0.96
