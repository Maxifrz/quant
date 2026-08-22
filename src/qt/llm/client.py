"""Anthropic-Client fuer den Allokator.

Duenn gehalten: die Datei uebersetzt ein Briefing in einen Aufruf und die
Antwort in ein validiertes Schema. Alles Fachliche steht anderswo.

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
from qt.llm.schemas import AllocationProposal

if TYPE_CHECKING:
    from qt.llm.cache import LLMCache

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


class LLMUnavailable(RuntimeError):
    """Kein API-Zugang oder das SDK fehlt."""


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
        model: str = DEFAULT_MODEL,
        cache: "LLMCache | None" = None,
        max_tokens: int = 4096,
        effort: str = "medium",
    ) -> None:
        self.model = model
        self.cache = cache
        self.max_tokens = max_tokens
        # `medium` statt `high`: eine Allokation ueber eine Handvoll
        # Strategien ist keine schwere Denkaufgabe, und im Backtest faellt
        # der Aufruf hundertfach an. Bei mehr Strategien lohnt `high`.
        self.effort = effort
        self._client = None

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
        Frage bei `low` und bei `max` sind zwei Antworten.
        """
        if self.cache is None:
            return None
        return self.cache.key(
            prompt,
            system=SYSTEM_PROMPT,
            model=self.model,
            effort=self.effort,
        )

    # ------------------------------------------------------------------

    def _call(self, prompt: str) -> AllocationProposal:
        """Ein Aufruf gegen die Messages API mit strukturierter Ausgabe.

        `output_format` legt das Modell auf das Schema fest, sodass die
        Antwort nicht aus freiem Text geparst werden muss. Trotzdem wird
        danach noch einmal validiert (in `AllocationProposal`) -- die zweite
        Pruefung faengt ab, was ein alter Cache-Eintrag einschleppen koennte.
        """
        client = self._ensure_client()
        try:
            response = self._request(client, prompt)
        except LLMUnavailable:
            raise
        except Exception as exc:
            # Das SDK entscheidet erst beim Aufruf ueber die Authentifizierung,
            # nicht beim Bau des Clients. Ein fehlender Schluessel taucht
            # deshalb hier auf und nicht in `_ensure_client` -- und zwar als
            # TypeError, was ohne diese Uebersetzung wie ein Programmierfehler
            # aussaehe statt wie eine fehlende Konfiguration.
            raise LLMUnavailable(
                f"Aufruf fehlgeschlagen ({type(exc).__name__}): {exc}. "
                "Ohne API-Zugang laeuft nur ein Backtest gegen gefuellten Cache "
                "oder mit StubClient."
            ) from exc

        if response.stop_reason == "refusal":
            raise LLMUnavailable(
                "Das Modell hat die Anfrage abgelehnt "
                f"({getattr(response.stop_details, 'category', 'ohne Kategorie')})."
            )

        parsed = response.parsed_output
        if parsed is None:
            raise LLMUnavailable("Antwort enthielt kein auswertbares Schema.")
        return parsed

    def _request(self, client, prompt: str):
        return client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            # Der eingefrorene Praefix wird gecacht, das Briefing nicht.
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": prompt}],
            output_format=AllocationProposal,
        )

    def _ensure_client(self):
        """SDK-Client bauen, mit sprechendem Fehler statt Absturz.

        Import und Schluesselpruefung passieren erst hier, nicht beim
        Modulimport: sonst braeuchte jeder Testlauf und jeder Backtest gegen
        Cache einen API-Schluessel.
        """
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:
            raise LLMUnavailable(
                "Das Paket `anthropic` fehlt. Installieren mit: "
                "uv add anthropic"
            ) from exc
        try:
            self._client = anthropic.Anthropic()
        except Exception as exc:
            raise LLMUnavailable(
                "Kein API-Zugang. Setze ANTHROPIC_API_KEY oder melde dich mit "
                "`ant auth login` an. Ein Backtest gegen einen gefuellten "
                "Cache laeuft auch ohne."
            ) from exc
        return self._client


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
