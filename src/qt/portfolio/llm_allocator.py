"""Der LLM-Allokator.

Hier greift das Sprachmodell ins Portfolio -- und zwar ausschliesslich als
**Vorschlagender**. Was danach kommt (`qt.portfolio.risk`), entscheidet.

Die Datei ist bewusst misstrauisch gebaut. Jede Stufe zwischen Modell und
Portfolio nimmt an, dass die vorherige Unsinn geliefert haben koennte:

    1. `output_format` legt das Modell auf ein Schema fest
    2. `AllocationProposal` validiert die Antwort noch einmal
    3. erfundene Strategie-Labels werden verworfen, nicht geraten
    4. jeder Fehler -- Netzwerk, Schema, Ablehnung -- faellt auf
       Gleichgewichtung zurueck statt den Lauf abzubrechen
    5. die Risk-Engine beschneidet, was uebrig bleibt

Punkt 4 ist der wichtigste und der am leichtesten zu uebersehende: ein
Allokator, der bei einem Netzwerkfehler eine Exception wirft, reisst im
Live-Betrieb das ganze System mit -- und zwar zu dem Zeitpunkt, an dem die
Verbindung ohnehin schon schlecht ist. Ein Ausfall des Modells muss ein
langweiliges Ereignis sein.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from qt.llm import briefing as briefing_mod
from qt.llm.client import AllocatorClient, LLMUnavailable
from qt.portfolio.base import Allocation, AllocationContext, Allocator


@dataclass(slots=True)
class AllocatorTelemetry:
    """Was bei den Aufrufen passiert ist.

    Ein Allokator, dessen Ausfaelle niemand sieht, wird fuer gut gehalten,
    weil er nie auffaellt -- dabei kann er dauerhaft auf Gleichgewichtung
    zurueckgefallen sein und damit heimlich eine Baseline sein.
    """

    calls: int = 0
    cache_hits: int = 0
    fallbacks: int = 0
    deliberate_flats: int = 0
    hallucinated_labels: int = 0
    # Aufrufe im Vorlauf, die gar nicht erst ans Modell gingen.
    #
    # Steht in `summary()`, obwohl es keine Fehlfunktion beschreibt: eine
    # Einsparung, die niemand sieht, wird beim naechsten Refactor
    # versehentlich rueckgaengig gemacht -- und faellt dann nur auf der
    # Rechnung auf, nicht im Ergebnis.
    warmup_skips: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def fallback_rate(self) -> float:
        return self.fallbacks / self.calls if self.calls else 0.0

    def merge(self, other: "AllocatorTelemetry") -> None:
        """Zahlen eines weiteren Allokators dazuzaehlen.

        Der Gate-Lauf baut pro Walk-Forward-Fenster einen **frischen**
        Allokator (sonst truege Zustand aus Fenster n nach n+1 -- genau das
        Leck, gegen das Purging und Embargo gebaut sind). Damit gibt es am
        Ende so viele Telemetrie-Objekte wie Fenster, und wer nur eines davon
        anschaut, sieht bei 29 Fenstern 1/29 des Laufs.

        Das ist bei der Rueckfallquote gefaehrlich: sie ist die Zahl, an der
        man erkennt, ob der Allokator heimlich Gleichgewichtung war
        (ADR-018). Eine Quote ueber ein einzelnes Fenster kann null sein,
        waehrend der Lauf zur Haelfte zurueckgefallen ist.
        """
        self.calls += other.calls
        self.cache_hits += other.cache_hits
        self.fallbacks += other.fallbacks
        self.deliberate_flats += other.deliberate_flats
        self.hallucinated_labels += other.hallucinated_labels
        self.warmup_skips += other.warmup_skips
        self.reasons.extend(other.reasons)

    def summary(self) -> str:
        return (
            f"Aufrufe {self.calls}, davon aus Cache {self.cache_hits}, "
            f"Rueckfaelle auf Gleichgewichtung {self.fallbacks} "
            f"({self.fallback_rate:.1%}), bewusste Ausstiege "
            f"{self.deliberate_flats}, halluzinierte Labels "
            f"{self.hallucinated_labels}, uebersprungen im Vorlauf "
            f"{self.warmup_skips}"
        )


class LLMAllocator(Allocator):
    """Verteilt Kapital nach dem Vorschlag eines Sprachmodells."""

    name = "llm"

    def __init__(
        self,
        client=None,
        min_history: int = 96,
        model: str = "claude-opus-5",
        cache=None,
    ) -> None:
        # `client` ist einspritzbar, damit Tests und Laeufe ohne API-Zugang
        # denselben Codepfad nehmen wie der Ernstfall -- ein separater
        # Testpfad wuerde genau die Verdrahtung ungeprueft lassen, um die es
        # geht.
        self.client = client or AllocatorClient(model=model, cache=cache)
        self._min_history = min_history
        self.telemetry = AllocatorTelemetry()

    @property
    def warmup_bars(self) -> int:
        """Vor so vielen Bars ist ein Briefing nicht aussagekraeftig.

        Das Modell nach einer Allokation zu fragen, bevor es ueber irgendeine
        Strategie etwas weiss, erzeugt eine geratene Antwort und kostet
        trotzdem. Bis dahin gleichgewichten.
        """
        return self._min_history

    def describe(self) -> str:
        return f"{self.name}({getattr(self.client, 'model', 'stub')})"

    # ------------------------------------------------------------------

    def allocate(self, ctx: AllocationContext) -> Allocation:
        ids = sorted(ctx.strategy_ids)
        if not ids:
            return {}

        equal = {sid: 1.0 / len(ids) for sid in ids}
        if ctx.history_length() < self._min_history:
            return equal

        if ctx.is_warmup:
            # Der Vorlauf wird nicht bewertet -- ihn zu bezahlen heisst, ein
            # Sprachmodell dafuer zu bezahlen, dass eine Baseline warmlaeuft.
            #
            # Der Preis dafuer, offen: der Kandidat startet jedes Testfenster
            # gleichgewichtet statt LLM-geformt und zahlt beim ersten
            # bewerteten Aufruf eine Umschichtung, die die Baselines nicht
            # zahlen. Das verzerrt **gegen** den Kandidaten -- fuer ein Gate
            # die richtige Richtung, aber wer die Zahlen liest, muss es
            # wissen: ein knapp gescheiterter Kandidat ist knapper
            # gescheitert, als die Tabelle zeigt.
            #
            # `telemetry.calls` bleibt bewusst unberuehrt. Sonst verwaessern
            # die uebersprungenen Aufrufe die `fallback_rate`, und genau an
            # der erkennt man, ob der Allokator heimlich eine Baseline ist.
            self.telemetry.warmup_skips += 1
            return equal

        self.telemetry.calls += 1
        brief = briefing_mod.build(ctx)

        try:
            response = self.client.propose(brief)
        except LLMUnavailable as exc:
            return self._fallback(equal, f"LLM nicht verfuegbar: {exc}")
        except Exception as exc:
            # Bewusst breit: was hier durchkaeme, wuerde den Lauf abbrechen.
            # Ein unerwarteter Fehler im Allokator ist ein Grund, konservativ
            # zu allokieren, kein Grund, das Handeln einzustellen.
            return self._fallback(equal, f"unerwarteter Fehler: {type(exc).__name__}: {exc}")

        if response.from_cache:
            self.telemetry.cache_hits += 1

        proposal = response.proposal
        unknown = proposal.unknown_labels(brief.labels)
        if unknown:
            self.telemetry.hallucinated_labels += len(unknown)
            self.telemetry.reasons.append(f"erfundene Labels: {', '.join(unknown)}")

        by_label = proposal.as_allocation(brief.labels)
        allocation = brief.resolve(by_label)

        total = sum(abs(v) for v in allocation.values())
        if total == 0:
            # "Ich sehe gerade keine Kante" ist eine legitime Meinung und muss
            # umsetzbar sein -- sonst kann der Allokator nie aussteigen, und
            # ein Modell, das aussteigen will, bekommt ausgerechnet volle
            # Gleichgewichtung.
            #
            # Unterscheidbar ist das nur, wenn das Modell **jede** Strategie
            # ausdruecklich mit 0 nennt. Eine leere oder halbe Antwort bleibt
            # mehrdeutig, und im Zweifel gehoert das Portfolio nicht flat.
            if proposal.is_deliberate_flat(brief.labels):
                self.telemetry.deliberate_flats += 1
                return {sid: 0.0 for sid in ids}
            return self._fallback(equal, "Vorschlag summierte sich zu null")

        return {sid: allocation.get(sid, 0.0) for sid in ids}

    def _fallback(self, equal: Allocation, reason: str) -> Allocation:
        self.telemetry.fallbacks += 1
        self.telemetry.reasons.append(reason)
        return equal
