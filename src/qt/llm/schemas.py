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
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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


# ---------------------------------------------------------------------------
# Research-Loop (Phase 5)
# ---------------------------------------------------------------------------

# Obergrenze fuer generierten Code. Eine Strategie, die laenger ist als die
# beiden Referenzstrategien zusammen, ist keine Idee mehr, sondern ein
# Sammelsurium -- und je mehr Code, desto mehr Freiheitsgrade fuer dieselbe
# Datenmenge.
MAX_CANDIDATE_CODE = 8_000

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class StrategyCandidateProposal(BaseModel):
    """Ein vom Modell geschriebener Strategie-Kandidat.

    **Das hier ist Form-, nicht Sicherheitsvalidierung.** Die Sicherheitsgrenze
    ist ausschliesslich `qt.research.sandbox`. Diese Unterscheidung steht so
    deutlich hier, weil ein Schema, das aussieht als pruefe es Code auf
    Gefaehrlichkeit, genau den Gedanken naehrt, die echte Pruefung sei
    redundant -- und dann wird sie irgendwann uebersprungen.

    Was hier geprueft wird, ist nur: laesst sich mit dieser Antwort ueberhaupt
    weiterarbeiten. Ein Kandidat mit leerem Code oder einem Klassennamen, der
    im Code gar nicht vorkommt, ist nicht gefaehrlich -- er ist unbrauchbar,
    und das faellt besser hier auf als drei Stufen spaeter.
    """

    name: str = Field(
        max_length=60,
        description="Kurzer Bezeichner in Kleinbuchstaben, z.B. vol_breakout.",
    )
    class_name: str = Field(
        max_length=60, description="Name der Strategie-Klasse im Code."
    )
    code: str = Field(
        max_length=MAX_CANDIDATE_CODE,
        description="Vollstaendiger Quelltext, genau eine Strategie-Klasse.",
    )
    rationale: str = Field(
        default="",
        max_length=1200,
        description="Warum diese Idee eine Kante haben koennte.",
    )

    @field_validator("name")
    @classmethod
    def lowercase_identifier(cls, value: str) -> str:
        if not _NAME_PATTERN.match(value):
            raise ValueError(
                f"name {value!r} muss klein anfangen und darf nur "
                "Kleinbuchstaben, Ziffern und Unterstriche enthalten."
            )
        return value

    @field_validator("code")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("code ist leer.")
        return value

    @model_validator(mode="after")
    def class_name_occurs_in_code(self) -> "StrategyCandidateProposal":
        """Der genannte Klassenname muss im Code auch vorkommen.

        Ohne diese Pruefung faellt eine Verwechslung erst beim Laden auf, und
        zwar als `SandboxRejected` -- also mit einer Meldung, die nach einem
        Sicherheitsproblem klingt, obwohl das Modell nur zwei Felder nicht in
        Uebereinstimmung gebracht hat. Zwei verschiedene Fehler sollen zwei
        verschiedene Meldungen ergeben.
        """
        if not self.class_name.isidentifier():
            raise ValueError(
                f"class_name {self.class_name!r} ist kein gueltiger Bezeichner."
            )
        if f"class {self.class_name}" not in self.code:
            raise ValueError(
                f"class_name {self.class_name!r} kommt im Code nicht vor."
            )
        return self


class CandidateCritique(BaseModel):
    """Das adversariale Urteil ueber einen Kandidaten, vor dem teuren Backtest.

    Die Flags sind Befunde, `recommendation` ist die Entscheidung. Beides
    getrennt zu fuehren ist Absicht: ein Modell, das jeden Flag auf True setzt
    und trotzdem "proceed" sagt, hat sich widersprochen. Das Schema definiert
    diesen Widerspruch **nicht** weg -- wer sich auf die Empfehlung verlaesst,
    soll die Befunde daneben sehen und den Widerspruch bemerken koennen.

    Die Kritik entscheidet ausserdem nichts endgueltig: eine Ablehnung
    ueberspringt den Walk-Forward-Lauf, verwirft den Kandidaten aber nicht --
    er liegt samt Begruendung in der Registry. Dieselbe Haltung wie bei
    ADR-018/ADR-021: eine LLM-Entscheidung faellt auf einen sichtbaren,
    ueberpruefbaren Zustand zurueck, nie auf ein stilles Verschwinden.
    """

    recommendation: Literal["proceed", "reject"] = Field(
        description="proceed = testen, reject = Walk-Forward ueberspringen."
    )
    overfitting_risk: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Wie stark der Code nach Anpassung an Vergangenes aussieht.",
    )
    magic_price_constants: bool = Field(
        default=False,
        description="Enthaelt der Code Zahlen, die an ein Kursniveau gebunden sind?",
    )
    unrealistic_turnover: bool = Field(
        default=False,
        description="Schichtet die Strategie so oft um, dass Kosten sie toeten?",
    )
    excess_degrees_of_freedom: bool = Field(
        default=False,
        description="Zu viele Parameter fuer die verfuegbare Datenmenge?",
    )
    rationale_code_mismatch: bool = Field(
        default=False,
        description="Tut der Code etwas anderes, als die Begruendung behauptet?",
    )
    reasoning: str = Field(
        default="",
        max_length=2000,
        description="Begruendung, mit Bezug auf die Stelle im Code.",
    )

    @field_validator("overfitting_risk")
    @classmethod
    def finite_risk(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("overfitting_risk muss endlich sein.")
        return value

    @property
    def flags(self) -> list[str]:
        """Die gesetzten Befunde, fuer Report und Registry."""
        named = (
            ("magische Preiskonstanten", self.magic_price_constants),
            ("unrealistischer Umsatz", self.unrealistic_turnover),
            ("zu viele Freiheitsgrade", self.excess_degrees_of_freedom),
            ("Begruendung passt nicht zum Code", self.rationale_code_mismatch),
        )
        return [label for label, is_set in named if is_set]

    @property
    def contradictory(self) -> bool:
        """Empfehlung und Befunde passen nicht zusammen.

        Kein Fehler und kein Grund, die Antwort zu verwerfen -- aber es gehoert
        in den Report. Ein Kritiker, der Maengel auflistet und trotzdem
        durchwinkt, hat entweder die Maengel oder die Empfehlung nicht ernst
        gemeint, und beides sollte man sehen.
        """
        return self.recommendation == "proceed" and len(self.flags) >= 2
