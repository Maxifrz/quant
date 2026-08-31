"""Die Clients: was gefragt wird, und was davon in den Cache geht.

Duenn gehalten: die Datei uebersetzt ein Briefing in einen Aufruf und die
Antwort in ein validiertes Schema. Alles Fachliche steht anderswo.

**Wie** der Aufruf beim Modell landet, steht seit dem zweiten Anbieter nicht
mehr hier, sondern in `qt.llm.providers`. Diese Datei kennt nur noch
`provider.parse(...)`; ob dahinter Anthropic oder NVIDIA NIM steht, aendert
weder den Prompt noch den Cache-Aufbau. Was sich sehr wohl aendert, ist der
Cache-*Key*: der Anbieter steht darin, denn dieselbe Frage an zwei Modelle
sind zwei Antworten.

Drei Eigenschaften, die hier bewusst so gebaut sind:

**Offline-tauglich.** Ohne API-Zugang wirft die Datei einen sprechenden
Fehler, statt beim Import zu scheitern. Der gesamte Rest des Systems --
Tests, Backtests gegen Cache, das Gate -- laeuft damit ohne Schluessel. Das
ist keine Bequemlichkeit: eine Testsuite, die einen Netzwerkzugang braucht,
wird irgendwann uebersprungen.

**Cache vor Aufruf.** Ein Backtest ruft das Modell hunderte Male. Ohne Cache
waere er weder reproduzierbar noch bezahlbar (ADR-003).

**Stabiler Prompt-Praefix.** Die Systemanweisung ist eingefroren und wird
gecacht; nur das Briefing variiert. Ein Zeitstempel oder eine wandernde
Modell-ID im Praefix wuerde den Prompt-Cache bei jedem Aufruf entwerten.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import TYPE_CHECKING

from qt.core.config import DEFAULT_LLM_MODEL
from qt.llm.briefing import Briefing
from qt.llm.providers import (
    AnthropicProvider,
    LLMProvider,
    LLMUnavailable,
    resolve_model,
)
from qt.llm.schemas import AllocationProposal

# Weiterhin von hier importierbar: `LLMUnavailable` war die Ausnahme dieses
# Moduls, bevor es die Provider-Schicht gab, und wird an einem guten Dutzend
# Stellen so abgefangen. Der Umzug soll die Aufrufer nichts kosten.
__all__ = [
    "AllocatorClient",
    "CriticClient",
    "GeneratorClient",
    "LLMResponse",
    "LLMUnavailable",
    "ScenarioClient",
    "StubClient",
    "StubCriticClient",
    "StubGeneratorClient",
    "StubScenarioClient",
]

if TYPE_CHECKING:
    # Nur fuer die Typpruefung: die Methoden importieren diese Namen zur
    # Laufzeit selbst, direkt vor Gebrauch. Ohne diesen Block waeren die
    # Annotationen in Anfuehrungszeichen fuer jeden Pruefer unaufloesbare
    # Namen -- und ein `# noqa` daneben wuerde die Meldung verstecken statt
    # den Namen bekannt zu machen.
    #
    # Kein Ladezeit-Argument: `qt.llm.schemas` steht ohnehin oben im
    # Modulkopf, pydantic ist beim Import dieser Datei also bereits da.
    from qt.llm.cache import LLMCache
    from qt.llm.schemas import (
        CandidateCritique,
        ScenarioProposal,
        StrategyCandidateProposal,
    )

DEFAULT_MODEL = DEFAULT_LLM_MODEL

# Eingefroren -- jede Aenderung entwertet den gesamten Antwort-Cache und
# beendet die Vergleichbarkeit mit frueheren Laeufen. Wer sie aendert,
# aendert das Experiment.
SYSTEM_PROMPT = """Du verteilst Kapital auf mehrere Handelsstrategien.

Du bekommst je Strategie normalisierte Kennzahlen ueber mehrere Fenster:
Rendite, annualisierte Volatilitaet, Sharpe, Trefferquote, Zeit im Markt,
aktueller Abstand zum Hoechststand -- dazu die paarweisen Korrelationen und
die derzeitige Allokation.

Zeitraum, Maerkte und Strategienamen sind bewusst nicht angegeben. Rate
nicht, welche historische Periode das sein koennte; die Kennzahlen sind die
gesamte Grundlage. Eine Antwort, die auf einer vermuteten Jahreszahl beruht,
ist wertlos.

Regeln fuer deinen Vorschlag:
- Ein Eintrag je Strategie aus dem Briefing, mit ihrem Label.
- Gewichte liegen zwischen -1 und 1. Die Summe der Betraege darf 1 nicht
  ueberschreiten.
- Ein negatives Gewicht invertiert die Strategie. Nutze es nur, wenn die
  Kennzahlen eine dauerhaft negative Kante zeigen, nicht bei einer
  Verlustserie.
- Stark korrelierte Strategien sind zusammen eine groessere Position, keine
  Streuung. Gewichte sie entsprechend.
- Kurze Fenster rauschen. Ein guter Sharpe ueber 24 Bars ist kein Beleg.
- Umschichten kostet Gebuehren. Weiche von der derzeitigen Allokation nur ab,
  wenn die Kennzahlen es tragen.
- Wenn nichts fuer eine Abweichung spricht, ist Gleichgewichtung die richtige
  Antwort. Sie ist schwer zu schlagen, und das ist kein Eingestaendnis.

Begruende knapp und pruefbar: nenne die Kennzahl, die deine Entscheidung
traegt, nicht eine Erzaehlung darueber."""


@dataclass(slots=True)
class LLMResponse:
    proposal: AllocationProposal
    from_cache: bool
    model: str


class AllocatorClient:
    """Fragt das Modell nach einer Allokation.

    `cache` ist optional, aber im Backtest praktisch Pflicht. Ohne ihn ist
    ein Lauf nicht wiederholbar.
    """

    def __init__(
        self,
        model: str | None = None,
        cache: "LLMCache | None" = None,
        max_tokens: int = 4096,
        effort: str = "medium",
        provider: LLMProvider | None = None,
    ) -> None:
        # Default-Anbieter bleibt Anthropic. `model=None` heisst "nimm das
        # Standardmodell dieses Anbieters" -- ein fester Default hier wuerde
        # einem NIM-Lauf stillschweigend einen Claude-Namen unterschieben.
        self.provider = provider or AnthropicProvider()
        self.model = resolve_model(self.provider.name, model)
        self.cache = cache
        self.max_tokens = max_tokens
        # `medium` statt `high`: eine Allokation ueber eine Handvoll
        # Strategien ist keine schwere Denkaufgabe, und im Backtest faellt
        # der Aufruf hundertfach an. Bei mehr Strategien lohnt `high`.
        self.effort = effort

    # ------------------------------------------------------------------

    def propose(self, briefing: Briefing) -> LLMResponse:
        """Allokationsvorschlag holen -- aus dem Cache oder vom Modell."""
        prompt = briefing.to_prompt()
        key = self._cache_key(prompt)

        if key is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return LLMResponse(proposal=hit, from_cache=True, model=self.model)

        proposal = self._call(prompt)

        if key is not None:
            self.cache.put(key, proposal)

        return LLMResponse(proposal=proposal, from_cache=False, model=self.model)

    def _cache_key(self, prompt: str) -> str | None:
        """Cache-Key inklusive **des Modells dieses Clients**.

        Der Cache kennt ein eigenes Default-Modell. Wuerde man sich darauf
        verlassen, lieferte ein Client mit abweichendem Modell stillschweigend
        die Antwort eines anderen -- kein Fehlschlag, nur ein falsches
        Ergebnis. Effort gehoert aus demselben Grund in den Key: dieselbe
        Frage bei `low` und bei `max` sind zwei Antworten. Und der Anbieter
        aus genau demselben Grund noch einmal: `claude-opus-5` und
        `nvidia/nemotron-3-ultra-550b-a55b` sind zwar verschiedene Namen, aber
        ein Modellname allein hindert niemanden daran, denselben Namen auf
        zwei Endpunkten zu verwenden.
        """
        if self.cache is None:
            return None
        return self.cache.key(
            prompt,
            system=SYSTEM_PROMPT,
            model=self.model,
            effort=self.effort,
            provider=self.provider.name,
        )

    # ------------------------------------------------------------------

    def _call(self, prompt: str) -> AllocationProposal:
        """Ein Aufruf mit strukturierter Ausgabe.

        Der Anbieter legt das Modell auf das Schema fest, sodass die Antwort
        nicht aus freiem Text geraten werden muss. Trotzdem wird danach noch
        einmal validiert (in `AllocationProposal`) -- die zweite Pruefung
        faengt ab, was ein alter Cache-Eintrag einschleppen koennte.
        """
        return self.provider.parse(
            system=SYSTEM_PROMPT,
            prompt=prompt,
            schema=AllocationProposal,
            model=self.model,
            max_tokens=self.max_tokens,
            effort=self.effort,
        )


class StubClient:
    """Deterministischer Ersatz fuer Tests und fuer Laeufe ohne API-Zugang.

    Kein Mock im Testverzeichnis, sondern eine mitgelieferte Klasse: das
    Gate, die CLI und die Beispiel-Laeufe brauchen sie genauso. Sie erlaubt,
    die gesamte Phase-3-Kette end-to-end zu pruefen, ohne einen Schluessel
    und ohne Kosten -- und macht damit sichtbar, welche Fehler von der
    Verdrahtung kommen und welche vom Modell.
    """

    def __init__(self, proposal: AllocationProposal | None = None) -> None:
        self.proposal = proposal
        self.calls = 0

    def propose(self, briefing: Briefing) -> LLMResponse:
        self.calls += 1
        if self.proposal is not None:
            return LLMResponse(proposal=self.proposal, from_cache=False, model="stub")

        labels = briefing.labels
        share = 1.0 / len(labels) if labels else 0.0
        return LLMResponse(
            proposal=AllocationProposal(
                allocations=[
                    {"strategy_id": label, "weight": share, "reason": "Stub"}
                    for label in labels
                ],
                regime="stub",
                confidence=0.0,
                reasoning="Deterministischer Ersatz, kein Modellaufruf.",
            ),
            from_cache=False,
            model="stub",
        )


# ---------------------------------------------------------------------------
# Szenario-Priors (Phase 4)
# ---------------------------------------------------------------------------

# Eingefroren wie SYSTEM_PROMPT -- jede Aenderung entwertet den Antwort-Cache
# und beendet die Vergleichbarkeit mit frueheren Laeufen.
SCENARIO_SYSTEM_PROMPT = """Du schaetzt ein, welche Art von Marktverlauf gerade
wahrscheinlicher ist als sonst.

Du bekommst normalisierte Kennzahlen der juengsten Marktentwicklung sowie eine
Beschreibung der Verteilung simulierter Fortsetzungen. Zeitraum, Maerkte und
absolute Preise sind bewusst nicht angegeben.

**Du prognostizierst keinen Preis und keinen Zeitpunkt.** Das koenntest du
nicht, und eine solche Zahl waere unpruefbar. Was du tust: du verschiebst
Gewicht zwischen bereits simulierten Pfaden.

Ein Prior benennt genau drei Dinge:
- eine Eigenschaft eines Pfades: 'volatility' (wie stark er schwankt),
  'terminal_return' (wo er endet) oder 'max_drawdown' (wie tief sein
  groesster Ruecksetzer ist),
- eine Richtung: 'higher' oder 'lower',
- eine Staerke zwischen 0 und 1 -- deine Ueberzeugung, nicht die Verschiebung.
  Wie stark sich das auswirkt, wird nachtraeglich gedeckelt.

Regeln:
- **Keine Priors sind eine gute Antwort.** Wenn die Kennzahlen keine
  Abweichung vom Normalfall nahelegen, gib eine leere Liste zurueck. Ein
  gleichgewichtetes Ensemble ist der ehrliche Ausgangszustand, kein
  Eingestaendnis.
- Hoechstens zwei bis drei Priors. Wer alles gleichzeitig behauptet, sagt
  nichts.
- Widersprechende Priors heben sich auf -- gib sie nicht beide an, um dich
  abzusichern.
- Staerke ueber 0.7 nur, wenn eine konkrete Kennzahl das traegt.
- Begruende mit der Kennzahl, auf die du dich stuetzt, nicht mit einer
  Erzaehlung ueber den Markt."""


class ScenarioClient:
    """Fragt das Modell nach Szenario-Priors.

    Gleicher Aufbau wie `AllocatorClient` und aus demselben Grund: Cache vor
    Aufruf, strukturierte Ausgabe, sprechender Fehler statt Absturz ohne
    API-Zugang.
    """

    def __init__(
        self,
        model: str | None = None,
        cache: "LLMCache | None" = None,
        max_tokens: int = 4096,
        effort: str = "medium",
        provider: LLMProvider | None = None,
    ) -> None:
        self.provider = provider or AnthropicProvider()
        self.model = resolve_model(self.provider.name, model)
        self.cache = cache
        self.max_tokens = max_tokens
        self.effort = effort

    def propose(self, briefing: str) -> "ScenarioProposal":
        from qt.llm.schemas import ScenarioProposal

        key = self._cache_key(briefing)
        if key is not None:
            hit = self.cache.get(key, model=ScenarioProposal)
            if hit is not None:
                return hit

        proposal = self._call(briefing)

        if key is not None:
            self.cache.put(key, proposal)
        return proposal

    def _cache_key(self, briefing: str) -> str | None:
        if self.cache is None:
            return None
        return self.cache.key(
            briefing,
            system=SCENARIO_SYSTEM_PROMPT,
            model=self.model,
            effort=self.effort,
            provider=self.provider.name,
            kind="scenario",
        )

    def _call(self, briefing: str) -> "ScenarioProposal":
        from qt.llm.schemas import ScenarioProposal

        return self.provider.parse(
            system=SCENARIO_SYSTEM_PROMPT,
            prompt=briefing,
            schema=ScenarioProposal,
            model=self.model,
            max_tokens=self.max_tokens,
            effort=self.effort,
        )


class StubScenarioClient:
    """Deterministischer Ersatz: schlaegt **keine** Priors vor.

    Bewusst so und nicht mit erfundenen Priors: der neutrale Zustand des
    Systems ist das gleichgewichtete Ensemble. Ein Stub, der Priors
    erfaendet, wuerde in Laeufen ohne API-Zugang eine Verzerrung einbauen,
    die niemand beabsichtigt hat und die im Ergebnis wie eine
    Modellentscheidung aussaehe.
    """

    model = "stub"

    def __init__(self) -> None:
        self.calls = 0

    def propose(self, briefing: str) -> "ScenarioProposal":
        from qt.llm.schemas import ScenarioProposal

        self.calls += 1
        return ScenarioProposal(
            priors=[],
            regime="stub",
            confidence=0.0,
            reasoning="Deterministischer Ersatz, kein Modellaufruf.",
        )


# ---------------------------------------------------------------------------
# Research-Loop (Phase 5): Generator und Kritiker
# ---------------------------------------------------------------------------

# Eingefroren wie die Prompts oben, und aus demselben Grund: er geht in den
# Cache-Key ein. Wer ihn aendert, entwertet damit jede zuvor gecachte Antwort
# -- das ist richtig so, soll aber eine bewusste Handlung sein.
#
# Der Prompt zaehlt die Sandbox-Grenzen ausdruecklich auf. Das ist keine
# Hoeflichkeit gegenueber dem Modell, sondern Kostenrechnung: jeder Kandidat,
# den die Whitelist danach verwirft, war ein bezahlter Aufruf ohne Ergebnis.
# Die Liste stammt aus `qt.research.sandbox` -- laeuft sie auseinander, faellt
# das als Ablehnungsquote auf, nicht als Fehler.
GENERATOR_SYSTEM_PROMPT = """Du entwirfst Handelsstrategien fuer Krypto-Spot-Maerkte.

Schreibe genau eine Python-Klasse, die von `Strategy` erbt. Genau diese Form,
sie ist nicht verhandelbar:

    class ZReversion(Strategy):
        name = "z_reversion"
        LOOKBACK = 48
        THRESHOLD = 2.0
        warmup_bars = 50

        def on_bar(self, symbol, store):
            window = store.window(symbol, self.timeframe)
            if len(window) < self.warmup_bars:
                return math.nan
            closes = window.closes()
            z = ta.zscore(closes, self.LOOKBACK)
            if not math.isfinite(z):
                return math.nan
            if z > self.THRESHOLD:
                return -1.0
            if z < -self.THRESHOLD:
                return 1.0
            return 0.0

Drei Dinge daran sind ungewohnt und trotzdem zwingend:

- **Kein `__init__`, kein `super()`.** Die Sandbox verbietet beides. Parameter
  sind Klassenkonstanten in GROSSBUCHSTABEN, angesprochen ueber `self.NAME`.
  `self.params` gibt es hier nicht.
- **`warmup_bars` ist ein schlichtes Klassenattribut**, kein `@property`.
  Dekoratoren sind verboten. Setze eine Zahl, die zu deinen Konstanten passt.
- **`name` ist ein Klassenattribut** in Kleinbuchstaben mit Unterstrichen.

`on_bar` gibt ein Zielgewicht in [-1, +1] zurueck. `math.nan` heisst "keine
Meinung" -- die Engine behaelt dann das bestehende Gewicht bei. Das ist etwas
anderes als 0.0, was "geh flat" bedeutet.

DER CODE LAEUFT IN EINER SANDBOX. Diese Grenzen sind hart, ein Verstoss
verwirft den Kandidaten ungetestet:

- Keine Imports, ausnahmslos. Verfuegbar sind `np`, `math`, `ta`, `Strategy`
  und `clip_weight` -- sie sind bereits gebunden.
- Keine Schleifen: kein `for`, kein `while`, keine Comprehensions. Alles
  Fensterartige laeuft ueber `ta.*` und `np.*`.
- Keine Dekoratoren, kein `try`/`except`, kein `with`, kein `lambda`, kein
  `assert`, kein `global`.
- Nichts mit fuehrendem Doppel-Unterstrich -- weder definieren noch zugreifen.
- Kein `eval`, `exec`, `open`, `getattr`, `setattr`, `globals`, `type`,
  `super`, `object`, `dir`, `vars`.
- An eingebauten Funktionen gibt es nur: `len`, `abs`, `min`, `max`, `sum`,
  `round`, `float`, `int`, `bool`, `sorted`, `enumerate`, `range`.
  Insbesondere gibt es **kein** `all` und **kein** `any` -- pruefe mehrere
  Werte mit `and` statt mit `all(...)`.

Verfuegbare Indikatoren, alle aus `ta`:
- `ta.sma(values, n)` -> float
- `ta.stdev(values, n)` -> float
- `ta.zscore(values, n)` -> float
- `ta.donchian(highs, lows, n)` -> (hoch, tief)
- `ta.true_range(highs, lows, closes)` -> Array
- `ta.atr(highs, lows, closes, n)` -> float
- `ta.realised_vol(closes, n, bars_per_year)` -> float

Aus `window` kommen `window.closes()`, `.highs()`, `.lows()`, `.opens()`,
`.volumes()` als numpy-Arrays, aeltester Wert zuerst. `len(window)` ist die
Zahl der bisher gesehenen Bars.

Zustand ueber Bars hinweg haeltst du in `self._state` (ein dict, bereits da),
zum Beispiel `self._state.setdefault(symbol, {"weight": 0.0})`.

ZWEI REGELN, DIE UEBER DIE SANDBOX HINAUSGEHEN:

1. Keine absoluten Preiskonstanten. `close > 42000` ist an einen Kurs
   gebunden, den es nur in einem bestimmten Zeitraum gab -- das ist
   Ueberanpassung in Reinform und wird als solche erkannt. Erlaubt sind
   Bar-Zahlen, z-Scores, ATR-Vielfache, Prozent- und Verhaeltniswerte.
2. Wenige Parameter. Jeder Freiheitsgrad ist ein weiterer Blick auf dieselben
   Daten; drei Konstanten sind viel, fuenf sind zu viele.

Die Strategie wird mit 90 Basispunkten Round-Trip-Kosten getestet. Eine Idee,
die bei jedem Bar umschichtet, ist vor Kosten tot -- rechne damit, bevor du
sie einreichst.

`rationale` ist ein kurzer Absatz: welche Marktbeobachtung soll die Kante
tragen, und warum sollte sie fortbestehen. Keine Behauptung ueber Rendite."""


# Bewusst eine andere Rolle, nicht derselbe Prompt in Grau. Der Kritiker soll
# Gruende finden, den Kandidaten NICHT zu testen -- das ist die einzige
# Haltung, in der ein Vorfilter etwas leistet.
CRITIC_SYSTEM_PROMPT = """Du pruefst Strategie-Kandidaten, bevor sie einen teuren
Walk-Forward-Backtest bekommen. Deine Aufgabe ist, Gruende zu finden, ihn
nicht zu testen.

Suche nach:

- **Ueberanpassung.** Parameter, die nach Feinjustierung an einem bestimmten
  Verlauf aussehen. Schwellen mit drei Nachkommastellen. Sonderfaelle, die nur
  eine Marktphase beschreiben.
- **Magischen Preiskonstanten.** Zahlen, die an ein Kursniveau gebunden sind
  statt an eine Bar-Zahl, einen z-Score oder ein ATR-Vielfaches. Das ist der
  haerteste Befund: er macht die Strategie ausserhalb eines Zeitraums sinnlos.
- **Unrealistischem Umsatz.** Getestet wird mit 90 Basispunkten Round-Trip.
  Eine Strategie, die haeufig zwischen Gewichten springt, ist vor Kosten tot,
  egal wie gut das Signal ist.
- **Zu vielen Freiheitsgraden** fuer die genannte Zahl an Out-of-Sample-Bars.
- **Widerspruch zwischen Begruendung und Code.** Behauptet die Begruendung
  etwas, das der Code nicht tut?

ZWEI REGELN, OHNE DIE DU NUTZLOS BIST:

1. "proceed" ist die richtige Antwort, wenn nichts Konkretes dagegen spricht.
   Ein Kritiker, der jeden Kandidaten ablehnt, filtert nichts, er blockiert
   nur -- und dann wird er abgeschaltet.
2. Begruende mit der Stelle im Code, nicht mit einem allgemeinen Verdacht.
   "Zeile mit dem Vergleich gegen 42000" ist eine Begruendung. "wirkt
   ueberangepasst" ist keine.

Ein schwacher, aber ehrlicher Kandidat soll durchkommen. Der Backtest darf
ihn ablehnen -- dafuer ist er da. Du haeltst nur zurueck, was nachweislich
nicht testwuerdig ist."""


class GeneratorClient:
    """Laesst das Modell einen Strategie-Kandidaten schreiben.

    Aufbau wie `AllocatorClient` und `ScenarioClient`: Cache vor Aufruf,
    strukturierte Ausgabe, sprechender Fehler statt Absturz ohne API-Zugang.
    """

    def __init__(
        self,
        model: str | None = None,
        cache: "LLMCache | None" = None,
        max_tokens: int = 8192,
        effort: str = "medium",
        provider: LLMProvider | None = None,
    ) -> None:
        self.provider = provider or AnthropicProvider()
        self.model = resolve_model(self.provider.name, model)
        self.cache = cache
        # Groesser als bei den anderen Clients: hier entsteht Quelltext, keine
        # Handvoll Zahlen. Ein abgeschnittener Kandidat ist kein Kandidat.
        self.max_tokens = max_tokens
        self.effort = effort

    def propose(self, briefing: str) -> "StrategyCandidateProposal":
        from qt.llm.schemas import StrategyCandidateProposal

        key = self._cache_key(briefing)
        if key is not None:
            hit = self.cache.get(key, model=StrategyCandidateProposal)
            if hit is not None:
                return hit

        proposal = self._call(briefing)

        if key is not None:
            self.cache.put(key, proposal)
        return proposal

    def _cache_key(self, briefing: str) -> str | None:
        if self.cache is None:
            return None
        return self.cache.key(
            briefing,
            system=GENERATOR_SYSTEM_PROMPT,
            model=self.model,
            effort=self.effort,
            provider=self.provider.name,
            kind="candidate",
        )

    def _call(self, briefing: str) -> "StrategyCandidateProposal":
        from qt.llm.schemas import StrategyCandidateProposal

        return self.provider.parse(
            system=GENERATOR_SYSTEM_PROMPT,
            prompt=briefing,
            schema=StrategyCandidateProposal,
            model=self.model,
            max_tokens=self.max_tokens,
            effort=self.effort,
        )


class CriticClient:
    """Laesst das Modell einen Kandidaten adversarial pruefen.

    Eigener Client statt eines Parameters am Generator, weil beide getrennte
    Systemprompts, getrennte Cache-Eintraege und typischerweise getrennte
    Effort-Stufen haben: die Kritik ist als **billiger** Vorfilter gedacht.
    """

    def __init__(
        self,
        model: str | None = None,
        cache: "LLMCache | None" = None,
        max_tokens: int = 4096,
        effort: str = "low",
        provider: LLMProvider | None = None,
    ) -> None:
        self.provider = provider or AnthropicProvider()
        self.model = resolve_model(self.provider.name, model)
        self.cache = cache
        self.max_tokens = max_tokens
        # Default niedriger als beim Generator. Der Unterschied gehoert in den
        # Code und nicht nur in die Doku -- sonst wird aus dem billigen
        # Vorfilter beim naechsten Lauf unbemerkt ein teurer.
        self.effort = effort

    def critique(self, briefing: str) -> "CandidateCritique":
        from qt.llm.schemas import CandidateCritique

        key = self._cache_key(briefing)
        if key is not None:
            hit = self.cache.get(key, model=CandidateCritique)
            if hit is not None:
                return hit

        verdict = self._call(briefing)

        if key is not None:
            self.cache.put(key, verdict)
        return verdict

    def _cache_key(self, briefing: str) -> str | None:
        if self.cache is None:
            return None
        return self.cache.key(
            briefing,
            system=CRITIC_SYSTEM_PROMPT,
            model=self.model,
            effort=self.effort,
            provider=self.provider.name,
            kind="critique",
        )

    def _call(self, briefing: str) -> "CandidateCritique":
        from qt.llm.schemas import CandidateCritique

        return self.provider.parse(
            system=CRITIC_SYSTEM_PROMPT,
            prompt=briefing,
            schema=CandidateCritique,
            model=self.model,
            max_tokens=self.max_tokens,
            effort=self.effort,
        )


# Vorlage fuer den Stub-Kandidaten. Die Form ist gegen die echte Sandbox
# geprueft: keine Imports, keine Schleifen, kein __init__, kein Dekorator,
# `warmup_bars` als Klassenattribut. Ein Stub, der die eigene Sandbox nicht
# besteht, wuerde jeden Lauf ohne API-Zugang schon vor der Kritik abwuergen --
# und der Fehler saehe wie ein Befund ueber den Generator aus.
_STUB_CANDIDATE_TEMPLATE = '''class {class_name}(Strategy):
    name = "{name}"
    LOOKBACK = {lookback}
    THRESHOLD = {threshold}
    warmup_bars = {warmup}

    def on_bar(self, symbol, store):
        window = store.window(symbol, self.timeframe)
        if len(window) < self.warmup_bars:
            return math.nan
        closes = window.closes()
        z = ta.zscore(closes, self.LOOKBACK)
        if not math.isfinite(z):
            return math.nan
        if z > self.THRESHOLD:
            return -1.0
        if z < -self.THRESHOLD:
            return 1.0
        return 0.0
'''


class StubGeneratorClient:
    """Deterministischer Ersatz fuer den Generator.

    Liefert eine z-Score-Reversion mit variierendem Lookback. Bewusst eine
    langweilige, offensichtlich schwache Idee: der Stub soll die Verdrahtung
    beweisen, nicht so tun, als haette er etwas gefunden. Wer im Stub-Lauf
    einen Kandidaten die DSR-Schwelle reissen sieht, soll das der Schwelle
    zuschreiben und nicht dem Modell.

    Der Lookback variiert mit dem Aufrufzaehler, damit `--generate 20` nicht
    zwanzigmal denselben Kandidaten erzeugt -- die Pipeline soll auch offline
    mit verschiedenen Eingaben laufen. Deterministisch bleibt es trotzdem:
    derselbe Zaehlerstand ergibt bitgleich dieselbe Antwort.
    """

    LOOKBACKS = (24, 36, 48, 72, 96, 120, 168, 240)

    def __init__(self, proposal: "StrategyCandidateProposal | None" = None) -> None:
        # `proposal` setzt die Antwort fest -- so kann ein Test einen gezielt
        # gefaehrlichen Kandidaten durch die Pipeline schicken und pruefen,
        # dass die Sandbox ihn faengt.
        self.proposal = proposal
        self.model = "stub"
        self.effort = "stub"
        self.calls = 0

    def propose(self, briefing: str) -> "StrategyCandidateProposal":
        from qt.llm.schemas import StrategyCandidateProposal

        index = self.calls
        self.calls += 1
        if self.proposal is not None:
            return self.proposal

        lookback = self.LOOKBACKS[index % len(self.LOOKBACKS)]
        suffix = index + 1
        class_name = f"StubReversion{suffix}"
        code = _STUB_CANDIDATE_TEMPLATE.format(
            class_name=class_name,
            name=f"stub_reversion_{suffix}",
            lookback=lookback,
            threshold=2.0,
            warmup=lookback + 2,
        )
        return StrategyCandidateProposal(
            name=f"stub_reversion_{suffix}",
            class_name=class_name,
            code=code,
            rationale=(
                "Stub-Kandidat: z-Score-Reversion ueber "
                f"{lookback} Bars. Beweist die Verdrahtung, keine Kante."
            ),
        )


class StubCriticClient:
    """Deterministischer Ersatz fuer den Kritiker: laesst alles durch.

    Aus demselben Grund neutral wie `StubScenarioClient` keine Priors erfindet:
    der Vorfilter soll ohne API-Zugang **nicht** greifen. Ein Stub, der
    ablehnte, wuerde die Pipeline vor dem Screening abschneiden, und im
    Trichter saehe das aus wie eine Modellentscheidung statt wie ein fehlender
    Schluessel.
    """

    def __init__(self, verdict: "CandidateCritique | None" = None) -> None:
        self.verdict = verdict
        self.model = "stub"
        self.effort = "stub"
        self.calls = 0

    def critique(self, briefing: str) -> "CandidateCritique":
        from qt.llm.schemas import CandidateCritique

        self.calls += 1
        if self.verdict is not None:
            return self.verdict
        return CandidateCritique(
            recommendation="proceed",
            overfitting_risk=0.0,
            reasoning="Stub: keine Pruefung, nur Durchreichen.",
        )
