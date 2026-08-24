"""Was das LLM zurueckgeben darf.

Streng typisiert und validiert, weil an dieser Stelle die unzuverlaessigste
Komponente des Systems sitzt. Ein Sprachmodell kann Felder erfinden, Zahlen
ausserhalb jedes Bereichs liefern oder Strategien nennen, die es nicht gibt.
Nichts davon darf ungeprueft weiter.

Die Schemas dienen doppelt: als `output_config.format` fuer die API (das
Modell wird dadurch auf die Struktur festgelegt) und als zweite Pruefung
danach. Die zweite Pruefung ist nicht redundant -- sie faengt ab, was ein
Cache-Treffer aus einer aelteren Version einschleppen koennte.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field, field_validator


class StrategyAllocation(BaseModel):
    """Kapitalanteil fuer genau eine Strategie."""

    strategy_id: str = Field(
        description="Anonymes Label aus dem Briefing, z.B. STRAT_A."
    )
    weight: float = Field(
        ge=-1.0,
        le=1.0,
        description="Kapitalanteil. Negativ invertiert die Strategie.",
    )
    reason: str = Field(
        default="",
        max_length=280,
        description="Ein Satz, warum dieses Gewicht.",
    )

    @field_validator("weight")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Gewicht muss endlich sein.")
        return value


class AllocationProposal(BaseModel):
    """Der vollstaendige Vorschlag eines Allokations-Durchgangs.

    `regime` und `reasoning` haben keinen Einfluss auf die Ausfuehrung. Sie
    stehen hier, weil ein Allokator, dessen Begruendungen niemand lesen kann,
    sich auch nicht widerlegen laesst -- und weil ein Vergleich zwischen
    behaupteter Begruendung und tatsaechlichem Ergebnis das einzige ist, was
    ueber die reine Performance hinaus Erkenntnis liefert.
    """

    allocations: list[StrategyAllocation] = Field(
        min_length=1, description="Ein Eintrag je Strategie aus dem Briefing."
    )
    regime: str = Field(
        default="",
        max_length=120,
        description="Wie das Modell die aktuelle Marktlage einordnet.",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Wie sicher sich das Modell ist. Rein informativ.",
    )
    reasoning: str = Field(
        default="",
        max_length=2000,
        description="Begruendung des Gesamtvorschlags.",
    )

    def as_allocation(self, known_ids: list[str]) -> dict[str, float]:
        """In das Format der Portfolio-Schicht umwandeln.

        Unbekannte Labels werden **verworfen**, nicht auf eine bestehende
        Strategie geraten: ein halluziniertes `STRAT_Z` ist ein Fehler des
        Modells, und ein Fehler wird zu nichts, nicht zu einer Position.
        Fehlende Labels werden zu 0 -- keine Meinung heisst kein Kapital.
        """
        known = set(known_ids)
        proposed = {
            entry.strategy_id: entry.weight
            for entry in self.allocations
            if entry.strategy_id in known
        }
        return {sid: proposed.get(sid, 0.0) for sid in known_ids}

    def is_deliberate_flat(self, known_ids: list[str]) -> bool:
        """Wollte das Modell wirklich aussteigen -- oder ist die Antwort Muell?

        "Ich sehe gerade keine Kante" ist eine legitime Meinung und muss
        umsetzbar sein. Sie ist aber von einer leeren oder verstuemmelten
        Antwort nur dann unterscheidbar, wenn das Modell **jede** bekannte
        Strategie ausdruecklich mit 0 nennt. Fehlende Eintraege bleiben
        mehrdeutig und zaehlen nicht als Ausstieg.
        """
        named = {e.strategy_id for e in self.allocations if e.strategy_id in known_ids}
        if named != set(known_ids):
            return False
        return all(
            e.weight == 0.0 for e in self.allocations if e.strategy_id in known_ids
        )

    def unknown_labels(self, known_ids: list[str]) -> list[str]:
        """Labels, die das Modell erfunden hat. Gehoeren in den Report."""
        known = set(known_ids)
        return sorted({e.strategy_id for e in self.allocations if e.strategy_id not in known})


# ---------------------------------------------------------------------------
# Szenario-Priors (Phase 4)
# ---------------------------------------------------------------------------


class ScenarioPriorProposal(BaseModel):
    """Eine Lageeinschaetzung, uebersetzt in eine Gewichtsverschiebung.

    Bewusst so eng geschnitten, dass eine **Preisprognose gar nicht
    ausdrueckbar** ist: das Modell kann nur sagen, welche Eigenschaft eines
    Pfades wahrscheinlicher wird und wie stark -- keinen Kurs, keinen
    Zeitpunkt, keine Richtung des Marktes im ueblichen Sinn.

    Das ist keine Bequemlichkeit, sondern der Zweck. Ein Sprachmodell nach
    einem Kursziel zu fragen, ergaebe eine unpruefbare Punktprognose aus
    einem Modell, das keine Preisreihen rechnet. Eine Aussage ueber die
    *Verteilung* moeglicher Zukuenfte kann es dagegen sinnvoll treffen -- und
    sie ist als Umgewichtung eines Ensembles darstellbar und im Nachhinein
    ueberpruefbar.
    """

    feature: str = Field(
        description=(
            "Eigenschaft eines Pfades: 'volatility', 'terminal_return' oder "
            "'max_drawdown'."
        )
    )
    direction: str = Field(description="'higher' oder 'lower'.")
    strength: float = Field(
        ge=0.0,
        le=1.0,
        description="Ueberzeugung, nicht Verschiebung. 0 = keine Meinung.",
    )
    reason: str = Field(
        default="", max_length=280, description="Ein Satz, worauf sich das stuetzt."
    )

    @field_validator("strength")
    @classmethod
    def finite_strength(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("strength muss endlich sein.")
        return value


class ScenarioProposal(BaseModel):
    """Alle Priors eines Durchgangs plus die Einordnung dahinter."""

    priors: list[ScenarioPriorProposal] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "Leer ist eine gueltige Antwort: wenn nichts fuer eine "
            "Verschiebung spricht, ist Gleichgewichtung richtig."
        ),
    )
    regime: str = Field(default="", max_length=120)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reasoning: str = Field(default="", max_length=2000)
