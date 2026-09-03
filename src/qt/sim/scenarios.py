"""Szenario-Priors: das LLM gewichtet Moeglichkeiten, es prognostiziert keine.

Der Unterschied ist der Kern dieser Datei und der Grund, warum es sie
ueberhaupt gibt.

Ein Sprachmodell nach einem Kursziel zu fragen, ist die schlechteste
denkbare Verwendung: es waere eine Punktprognose ohne Fehlerbalken, aus
einem Modell, das keine Preisreihen rechnet, sondern Text fortsetzt. Selbst
wenn die Zahl gut klaenge, waere sie unpruefbar.

Was ein Sprachmodell dagegen leisten kann: eine **Lageeinschaetzung**, die
sich in Gewichte uebersetzen laesst. Nicht "BTC steht in 30 Tagen bei X",
sondern "das Marktbild spricht eher fuer erhoehte Volatilitaet als fuer
Beruhigung". Das ist eine Aussage ueber die *Verteilung* moeglicher
Zukuenfte -- und genau die ist als Umgewichtung eines Pfad-Ensembles
darstellbar.

    Ensemble (gleichgewichtet)   ->  jeder Pfad gleich plausibel
              +
    Szenario-Prior               ->  "Vol-Spike-Regime hoeher gewichten"
              =
    Ensemble (umgewichtet)       ->  Tail-Pfade zaehlen mehr,
                                     CVaR wird entsprechend schlechter

Drei Eigenschaften, die diese Bauform gegenueber einer direkten Prognose hat:

1. **Sie kann nicht ins Unendliche danebenliegen.** Ein Prior verschiebt
   Gewichte innerhalb eines Ensembles, das aus echten historischen
   Eigenschaften erzeugt wurde. Ein Pfad, den die Simulation nicht erzeugt
   hat, kann auch kein Prior herbeireden.
2. **Sie ist beschraenkbar.** `max_tilt` begrenzt, wie stark ein Prior die
   Verteilung verzerren darf. Eine Punktprognose hat keine solche Bremse.
3. **Sie ist pruefbar.** Ein Prior sagt vorher, welche Region des Ensembles
   wahrscheinlicher wird. Ob das eintrat, laesst sich hinterher messen --
   im Gegensatz zu "der Markt war unsicher".

## Was ein Prior formal ist

Ein Prior benennt eine **messbare Eigenschaft eines Pfades** (seine
realisierte Volatilitaet, seine Endrendite, sein groesster Ruecksetzer) und
verschiebt Gewicht in Richtung eines Bereichs dieser Eigenschaft. Er nennt
weder einen Preis noch einen Zeitpunkt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from qt.sim.base import PathEnsemble

# Obergrenze fuer die Verzerrung, die ein einzelner Prior erzeugen darf.
#
# Ohne Deckel koennte ein selbstsicheres Modell das gesamte Gewicht auf eine
# Handvoll Pfade legen -- das Ensemble waere dann faktisch wieder eine
# Punktprognose, nur mit mehr Rechenaufwand davor. Der Wert ist als Faktor
# zwischen groesstem und kleinstem Pfadgewicht zu lesen.
DEFAULT_MAX_TILT = 5.0


class PathFeature(str, Enum):
    """Eigenschaften eines Pfades, auf die sich ein Prior beziehen darf.

    Bewusst eine geschlossene Aufzaehlung und kein freier Ausdruck: ein
    Prior, der beliebigen Code auf die Pfade anwenden darf, waere eine
    Sandbox-Frage (siehe Phase 5) statt einer Gewichtungsfrage. Jede
    Erweiterung hier ist eine bewusste Entscheidung.
    """

    VOLATILITY = "volatility"
    TERMINAL_RETURN = "terminal_return"
    MAX_DRAWDOWN = "max_drawdown"


class Direction(str, Enum):
    """In welche Richtung der Prior Gewicht verschiebt."""

    HIGHER = "higher"
    LOWER = "lower"


@dataclass(frozen=True, slots=True)
class ScenarioPrior:
    """Eine einzelne Lageeinschaetzung.

    `strength` liegt in [0, 1] und ist die Ueberzeugung, nicht die
    Verschiebung: wie stark sich das in Gewichten niederschlaegt, deckelt
    `max_tilt` beim Anwenden. Damit kann ein Modell "sehr sicher" sagen,
    ohne dass daraus eine unbeschraenkte Verzerrung wird.
    """

    feature: PathFeature
    direction: Direction
    strength: float = 0.5
    reason: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.strength) or not 0.0 <= self.strength <= 1.0:
            raise ValueError(
                f"strength muss endlich und in [0, 1] liegen, ist {self.strength!r}."
            )


@dataclass(slots=True)
class TiltReport:
    """Was die Umgewichtung tatsaechlich bewirkt hat.

    Ein Prior, dessen Wirkung niemand sieht, wird nie hinterfragt. Diese
    Zahlen gehoeren in jeden Report, in dem ein umgewichtetes Ensemble
    verwendet wurde -- besonders `effective_sample_size`: sie sagt, wieviele
    Pfade nach der Umgewichtung noch praktisch beitragen. Faellt sie stark,
    ist aus dem Ensemble eine Handvoll Pfade geworden, und jede Quantil-
    Aussage darueber ist entsprechend duenn.
    """

    priors: list[ScenarioPrior] = field(default_factory=list)
    max_weight_ratio: float = 1.0
    effective_sample_size: float = 0.0
    n_paths: int = 0
    clipped: bool = False

    @property
    def ess_fraction(self) -> float:
        return self.effective_sample_size / self.n_paths if self.n_paths else 0.0

    def summary(self) -> str:
        if not self.priors:
            return "Keine Szenario-Priors -- Ensemble gleichgewichtet."
        parts = ", ".join(
            f"{p.feature.value} {p.direction.value} ({p.strength:.0%})"
            for p in self.priors
        )
        clip = " [auf max_tilt begrenzt]" if self.clipped else ""
        return (
            f"{len(self.priors)} Prior(s): {parts}{clip} | "
            f"Gewichtsspanne {self.max_weight_ratio:.2f}x | "
            f"effektive Stichprobe {self.effective_sample_size:,.0f} von "
            f"{self.n_paths:,} ({self.ess_fraction:.0%})"
        )


def path_feature(ensemble: PathEnsemble, feature: PathFeature) -> np.ndarray:
    """Die gewaehlte Eigenschaft je Pfad, Form (n_paths,)."""
    if feature is PathFeature.TERMINAL_RETURN:
        return ensemble.terminal_returns()
    if feature is PathFeature.VOLATILITY:
        # Standardabweichung ueber den Pfad, nicht annualisiert: fuer eine
        # *relative* Gewichtung zwischen Pfaden desselben Ensembles ist der
        # gemeinsame Annualisierungsfaktor ohne Wirkung, und ihn wegzulassen
        # spart eine Stelle, an der ein falscher Timeframe hineingeraten
        # koennte.
        return ensemble.paths.std(axis=1, ddof=1)
    if feature is PathFeature.MAX_DRAWDOWN:
        curves = ensemble.equity_curves()
        peaks = np.maximum.accumulate(curves, axis=1)
        return (curves / peaks - 1.0).min(axis=1)
    raise ValueError(f"Unbekannte Pfad-Eigenschaft: {feature!r}")


def apply_priors(
    ensemble: PathEnsemble,
    priors: list[ScenarioPrior],
    max_tilt: float = DEFAULT_MAX_TILT,
) -> tuple[PathEnsemble, TiltReport]:
    """Priors auf ein Ensemble anwenden.

    Verfahren: je Prior wird die betreffende Pfad-Eigenschaft in einen
    z-Score umgerechnet und daraus ein exponentielles Gewicht gebildet
    (`exp(lambda * z)`). Das ist die uebliche Form des exponentiellen
    Tiltings -- sie veraendert die Verteilung glatt und monoton, statt Pfade
    hart in "passt" und "passt nicht" zu sortieren. Eine harte Auswahl waere
    einfacher, wuerfe aber den Grossteil des Ensembles weg und liesse die
    Quantile an wenigen Pfaden haengen.

    Mehrere Priors multiplizieren sich. Widersprechen sie sich, heben sie
    sich entsprechend teilweise auf -- das ist gewollt: zwei gegenlaeufige
    Einschaetzungen sollen keine willkuerliche Auswahl erzwingen, sondern in
    Richtung Gleichgewichtung zuruecklaufen.

    `max_tilt` begrenzt anschliessend das Verhaeltnis zwischen groesstem und
    kleinstem Gewicht. Ohne diesen Deckel koennte ein selbstsicherer Prior
    das Ensemble auf wenige Pfade zusammenschnueren, und die Simulation
    waere wieder eine Punktprognose.
    """
    if max_tilt < 1.0:
        raise ValueError("max_tilt muss mindestens 1.0 sein (1.0 = keine Verzerrung).")

    report = TiltReport(priors=list(priors), n_paths=ensemble.n_paths)
    if not priors:
        report.max_weight_ratio = 1.0
        report.effective_sample_size = float(ensemble.n_paths)
        return ensemble, report

    log_weights = np.zeros(ensemble.n_paths)
    for prior in priors:
        values = path_feature(ensemble, prior.feature)
        spread = values.std(ddof=1)
        if not math.isfinite(spread) or spread <= 0:
            # Alle Pfade gleich in dieser Eigenschaft -- der Prior hat nichts
            # zu gewichten. Stillschweigend ueberspringen ist hier richtig:
            # es ist kein Fehler des Modells, sondern eine Eigenschaft des
            # Ensembles.
            continue
        z = (values - values.mean()) / spread
        sign = 1.0 if prior.direction is Direction.HIGHER else -1.0
        log_weights += sign * prior.strength * z

    log_weights, clipped = _limit_tilt(log_weights, max_tilt)

    # Stabilisieren vor dem Exponenzieren: ohne Abzug des Maximums laeuft
    # exp() bei grossen z-Scores in einen Ueberlauf, und das Ergebnis waere
    # inf/nan statt einer Gewichtung.
    weights = np.exp(log_weights - log_weights.max())
    weights = weights / weights.sum()

    report.clipped = clipped
    report.max_weight_ratio = float(weights.max() / weights.min()) if weights.min() > 0 else float("inf")
    report.effective_sample_size = float(1.0 / np.sum(weights**2))
    return ensemble.reweighted(weights), report


def _limit_tilt(log_weights: np.ndarray, max_tilt: float) -> tuple[np.ndarray, bool]:
    """Gewichtsspanne begrenzen, indem die **Staerke** skaliert wird.

    Die naheliegende Umsetzung -- Gewichte kappen oder den Boden anheben --
    ist falsch, und zwar auf eine Weise die im Ergebnis wie "der Prior wirkt
    eben kaum" aussieht statt wie ein Fehler: sie setzt einen Grossteil der
    Pfade auf exakt denselben Wert. Bei einem Prior mit Staerke 0.8 und
    z-Scores ueber +/-4 spannen die Rohgewichte einen Faktor von rund 600;
    ein Deckel bei 5 traefe damit fast alle Pfade und liesse nur die extreme
    Spitze unterscheidbar. Die Verteilung waere praktisch wieder
    gleichgewichtet, obwohl der Report eine Spanne von 5.00x meldet.

    Stattdessen wird der gesamte Tilt im Logarithmus linear herunterskaliert,
    bis die Spanne passt. Das erhaelt die **Form** der Gewichtung
    vollstaendig -- jeder Pfad behaelt seine relative Position -- und
    schwaecht nur ihre Auspraegung ab. Genau das ist gemeint mit "der Prior
    darf die Verteilung nur begrenzt verzerren".
    """
    span = float(log_weights.max() - log_weights.min())
    allowed = math.log(max_tilt)
    if span <= allowed or span <= 0:
        return log_weights, False
    return log_weights * (allowed / span), True


# ---------------------------------------------------------------------------
# Uebersetzung: LLM-Vorschlag -> Priors
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TranslationReport:
    """Was beim Uebersetzen des Modellvorschlags verworfen wurde.

    Ein Modell, dessen Vorschlaege stillschweigend zur Haelfte im Filter
    haengenbleiben, sieht aus wie ein zurueckhaltendes Modell. Erst diese
    Zahlen unterscheiden "hatte keine Meinung" von "hat Unsinn geliefert".
    """

    accepted: int = 0
    unknown_feature: list[str] = field(default_factory=list)
    unknown_direction: list[str] = field(default_factory=list)
    zero_strength: int = 0

    @property
    def rejected(self) -> int:
        return len(self.unknown_feature) + len(self.unknown_direction) + self.zero_strength

    def summary(self) -> str:
        if not self.rejected:
            return f"{self.accepted} Prior(s) uebernommen, nichts verworfen."
        parts = [f"{self.accepted} uebernommen", f"{self.rejected} verworfen"]
        if self.unknown_feature:
            parts.append(f"unbekannte Eigenschaft: {', '.join(self.unknown_feature)}")
        if self.unknown_direction:
            parts.append(f"unbekannte Richtung: {', '.join(self.unknown_direction)}")
        if self.zero_strength:
            parts.append(f"{self.zero_strength} ohne Staerke")
        return " | ".join(parts)


def from_proposal(proposal) -> tuple[list[ScenarioPrior], TranslationReport]:
    """Einen `ScenarioProposal` des Modells in Priors uebersetzen.

    Erfundene Eigenschaften und Richtungen werden **verworfen, nicht
    geraten** -- dieselbe Haltung wie bei halluzinierten Strategie-Labels in
    Phase 3 (ADR-018): ein Fehler des Modells wird zu nichts, nicht zu einer
    beliebigen gueltigen Alternative. Ein Prior mit Staerke 0 faellt
    ebenfalls weg; er wuerde nichts bewirken und nur den Report aufblaehen.
    """
    report = TranslationReport()
    priors: list[ScenarioPrior] = []

    for entry in getattr(proposal, "priors", []) or []:
        feature_raw = str(getattr(entry, "feature", "")).strip().lower()
        direction_raw = str(getattr(entry, "direction", "")).strip().lower()

        try:
            feature = PathFeature(feature_raw)
        except ValueError:
            report.unknown_feature.append(feature_raw or "<leer>")
            continue
        try:
            direction = Direction(direction_raw)
        except ValueError:
            report.unknown_direction.append(direction_raw or "<leer>")
            continue

        strength = float(getattr(entry, "strength", 0.0))
        if not math.isfinite(strength) or strength <= 0.0:
            report.zero_strength += 1
            continue

        priors.append(
            ScenarioPrior(
                feature=feature,
                direction=direction,
                strength=min(1.0, max(0.0, strength)),
                reason=str(getattr(entry, "reason", "")),
            )
        )
        report.accepted += 1

    return priors, report
