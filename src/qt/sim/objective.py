"""Zielfunktion der Pfad-Simulation -- welche Allokation ist die beste?

Gegeben ein Ensemble moeglicher Zukuenfte (`qt.sim.base.PathEnsemble`),
beantwortet diese Datei die einzige Frage, die daraus eine Entscheidung
macht: **wie viel Exposure?**

Die naive Antwort -- "das Exposure mit der hoechsten erwarteten Rendite" --
ist falsch, und zwar auf eine Weise, die im Backtest gut aussieht und live
ruiniert. Der Erwartungswert ueber ein Renditeensemble wird von wenigen
extrem guten Pfaden dominiert; die Allokation, die ihn maximiert, ist
typischerweise die mit dem groessten Hebel und dem groessten Ruinrisiko.
Genau dieselbe Zahl, die im Mittel glaenzt, kann in der Mehrzahl der Faelle
das Konto halbieren.

Deshalb: **Median-Rendite maximieren unter einer CVaR-Nebenbedingung.**

* **Median statt Mittelwert**, weil der Median nicht von Ausreissern
  getrieben wird. Er beantwortet "wie laeuft es typischerweise", nicht "wie
  laeuft es im Durchschnitt ueber eine Handvoll Gluecksfaelle".
* **CVaR (Expected Shortfall) statt VaR**, weil VaR nur sagt *wo* die
  Verlustschwelle liegt und nichts darueber, *wie schlimm* es dahinter wird.
  CVaR ist der Erwartungswert der schlimmsten alpha-Faelle -- und anders als
  VaR subadditiv, also eine kohaerente Risikomasszahl. Eine Grenze auf VaR
  laesst sich durch eine Verteilung mit duennem, aber bodenlosem Ende
  einhalten; eine Grenze auf CVaR nicht.

--------------------------------------------------------------------------
VORZEICHENKONVENTION -- einmal lesen, ueberall gueltig
--------------------------------------------------------------------------
**CVaR und VaR sind Renditen, keine Verlusthoehen. Negativ heisst Verlust.**

    CVaR = -0.18   ->  die schlimmsten 5% der Pfade verlieren im Mittel 18%
    cvar_limit = -0.20  ->  "mehr als 20% Verlust im Mittel der schlimmsten
                            5% ist nicht erlaubt"
    zulaessig  <=>  cvar >= cvar_limit

--------------------------------------------------------------------------
Worauf sich die Grenze bezieht: Drawdown statt Endrendite
--------------------------------------------------------------------------

Naheliegend waere, den CVaR auf die **Endrendite** des Horizonts zu
beziehen. Der Default ist trotzdem der **groesste zwischenzeitliche
Ruecksetzer** -- aus zwei Gruenden.

Der praktische: ein Konto wird nicht am Ende des Horizonts liquidiert,
sondern unterwegs. Ein Pfad, der zwischenzeitlich 60% verliert und am Ende
bei -10% landet, ist real ein Totalschaden (Margin Call, Kill-Switch,
aufgegebener Anleger) und keine milde Enttaeuschung. Die Endrendite sieht
diesen Pfad nicht.

Der empirische, und er war eine Ueberraschung: eine Messung beim Bau des
Bootstraps zeigte, dass sich Block- und i.i.d.-Resampling im 5%-Quantil der
Endrendite ueber lange Horizonte **kaum unterscheiden** (-50,5% gegen
-51,1%), waehrend sie sich beim kurzfristigen Drawdown deutlich
unterscheiden (-25% gegen -22% im Median des schlimmsten 20-Bar-Verlusts).
Die Vol-Mischung eines Pfades mittelt sich ueber viele Bars wieder aus.

Eine Grenze auf der Endrendite misst damit ausgerechnet die Groesse, bei der
das ganze Vol-Clustering -- der Grund, warum ueberhaupt ein Block-Bootstrap
gebaut wurde -- keinen Unterschied macht. Sie waere nicht falsch, aber sie
liesse die Modellwahl folgenlos.

`risk_basis="terminal"` stellt das andere Verhalten her.

--------------------------------------------------------------------------

Damit haben CVaR, VaR, `median_return`, `mean_return` und
`max_drawdown_median` **alle dasselbe Vorzeichen wie eine Rendite** -- so
wie `Metrics.max_drawdown` in `qt.backtest.metrics`. Die Alternative (CVaR
positiv als Verlusthoehe) ist ebenso gebraeuchlich und genau deshalb
gefaehrlich: wer die beiden Konventionen mischt, dreht mit einem
Vergleichsoperator eine Risikogrenze in ihr Gegenteil, ohne dass etwas
fehlschlaegt. Ein Test haelt das Vorzeichen fest.

--------------------------------------------------------------------------
Die Entscheidungsvariable: ein skalares Exposure in [0, 1]
--------------------------------------------------------------------------
Nicht ein Gewichtsvektor ueber Symbole. Die Richtung und die relative
Aufteilung kommen aus der bestehenden Kette -- Strategien liefern
Zielgewichte, der Allokator verteilt Kapital. Diese Zielfunktion beantwortet
nur die verbleibende Frage "wie viel Risiko insgesamt", und ihr Ergebnis ist
ein Faktor auf das Gesamt-Exposure.

Warum **nicht** -1..1: ein negatives Exposure wuerde die Richtung der
Strategien umdrehen. Eine Schicht, die von sich aus eine Position eroeffnet,
die niemand vorgeschlagen hat, ist keine Dimensionierung mehr, sondern eine
zweite Strategie -- dieselbe Grundregel, aus der heraus `qt.portfolio.risk`
nur verkleinern darf. Warum die Obergrenze 1: kein Hebel, wie
`RiskConfig.max_gross_exposure` (Default 1.0). Beide Grenzen sind
konfigurierbar; die Defaults sind die konservative Wahl.

Die Untergrenze des Gitters ist bewusst **groesser als null**
(`min_exposure`, Default 5%). Nur so kann die Antwort "auch das kleinste
sinnvolle Exposure verletzt die Grenze -- kein Trade" ueberhaupt entstehen.
Ein Gitter, das bei 0 beginnt, findet immer eine zulaessige Allokation, und
die Nebenbedingung waere Dekoration.

--------------------------------------------------------------------------
Kosten sind in der Zielfunktion -- Entscheidung, nicht Versehen
--------------------------------------------------------------------------
Ein Exposure aufzubauen und wieder abzubauen kostet rund 90 bps Round-Trip
(ADR-009, `qt.backtest.costs.round_trip_bps`). Diese Kosten stehen hier
drin, obwohl die Zielfunktion sie nicht bezahlen muss: eine Zielfunktion
ohne Kosten empfiehlt systematisch zu viel Handel, weil jedes noch so
duenne positive Median-Signal besser aussieht als flat zu bleiben. Mit
Kosten muss ein Exposure seine eigene Aufbau- und Abbaugebuehr erst
verdienen -- und ein Ensemble ohne Kante fuehrt korrekterweise zu "kein
Trade" statt zu einer Minimalposition.

Modelliert wird **ein** Round-Trip, proportional zum Exposure, als
Kapitalabschlag ueber den ganzen Horizont. Bewusst nicht modelliert:
Umschichtungen innerhalb des Horizonts -- das Ensemble enthaelt keinen
Rebalancing-Fahrplan, und eine erfundene Umschlagshaeufigkeit waere geraten.
Die Annahme ist damit die guenstigste realistische; wer haeufiger handelt,
zahlt mehr, nie weniger. `round_trip_bps=0` schaltet den Abschlag fuer
Analysezwecke ab.
"""

from __future__ import annotations

from typing import Literal

from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel, Field, model_validator

from qt.backtest.costs import BPS, round_trip_bps
from qt.core.config import CostConfig
from qt.sim.base import PathEnsemble, weighted_quantile

# Die Grenze, gegen die im Zweifel gerechnet wird. -20% deckt sich mit
# `RiskConfig.max_drawdown`: es waere unstimmig, ein Exposure zu waehlen,
# dessen erwarteter Verlust im schlechten Fall den Kill-Switch der
# Risk-Engine ohnehin ausloesen wuerde.
DEFAULT_CVAR_LIMIT = -0.20

# Toleranz zugunsten der Grenze, nicht zugunsten des Trades: liegt der CVaR
# rechnerisch exakt auf dem Limit, gilt er als eingehalten ("<=" heisst
# "<="). Ohne die Toleranz haengt diese Aussage am letzten Bit einer
# Fliesskommasumme.
_LIMIT_EPS = 1e-12


class ObjectiveConfig(BaseModel):
    """Parameter der Zielfunktion. Defaults konservativ.

    Die CVaR-*Grenze* steht bewusst **nicht** hier, sondern ist Argument von
    `score_allocation` und `optimise_allocation`. Sie ist die eigentliche
    Risikoentscheidung und gehoert an die Aufrufstelle, wo man sie sieht --
    nicht in ein Config-Objekt, das man einmal baut und dann durchreicht.
    """

    alpha: float = Field(
        default=0.05,
        gt=0,
        lt=1,
        description="Tail-Anteil fuer VaR/CVaR. 0.05 = die schlimmsten 5%.",
    )
    min_exposure: float = Field(
        default=0.05,
        gt=0,
        description="Kleinstes Exposure im Gitter. Strikt > 0, damit 'kein "
        "Trade' eine eigene Antwort ist und nicht als Exposure 0 durch die "
        "Nebenbedingung rutscht.",
    )
    max_exposure: float = Field(
        default=1.0,
        gt=0,
        description="Groesstes Exposure im Gitter. 1.0 = kein Hebel.",
    )
    n_grid: int = Field(
        default=20,
        ge=2,
        description="Stuetzstellen zwischen min_exposure und max_exposure.",
    )
    round_trip_bps: float = Field(
        default=round_trip_bps(CostConfig()),
        ge=0,
        description="Kosten eines vollen Round-Trips in Basispunkten, "
        "proportional zum Exposure verrechnet. Default aus dem Kostenmodell "
        "(~90 bps, ADR-009). 0 schaltet Kosten fuer Analysen ab.",
    )
    risk_basis: Literal["terminal", "drawdown"] = Field(
        default="drawdown",
        description=(
            "Worauf sich die CVaR-Grenze bezieht: auf die Endrendite des "
            "Horizonts ('terminal') oder auf den groessten zwischenzeitlichen "
            "Ruecksetzer ('drawdown'). Siehe ADR-025 zur Wahl des Defaults."
        ),
    )

    @model_validator(mode="after")
    def _check_bounds(self) -> "ObjectiveConfig":
        if self.max_exposure < self.min_exposure:
            raise ValueError(
                f"max_exposure ({self.max_exposure}) liegt unter min_exposure "
                f"({self.min_exposure}) -- das Gitter waere leer."
            )
        return self

    def grid(self) -> np.ndarray:
        """Aufsteigendes Exposure-Gitter.

        Aufsteigend ist keine Kosmetik: `optimise_allocation` begruendet ein
        "kein Trade" mit dem *kleinsten* Exposure, und das ist nur dann der
        erste Eintrag.
        """
        return np.linspace(self.min_exposure, self.max_exposure, self.n_grid)


@dataclass(frozen=True, slots=True)
class AllocationScore:
    """Bewertung eines Exposures ueber das ganze Ensemble.

    Alle Renditegroessen sind Endrenditen ueber den Horizont des Ensembles,
    nicht annualisiert -- annualisieren wuerde eine Bar-Frequenz
    voraussetzen, die das Ensemble selbst nicht kennt.

    Vorzeichen: `cvar`, `var` und `max_drawdown_median` sind Renditen,
    negativ = Verlust (siehe Modul-Docstring).
    """

    exposure: float
    median_return: float
    mean_return: float
    cvar: float
    var: float
    prob_loss: float
    max_drawdown_median: float
    drawdown_cvar: float
    cvar_limit: float
    risk_basis: str
    feasible: bool

    def describe(self) -> str:
        mark = "ok" if self.feasible else "VERLETZT"
        return (
            f"Exposure {self.exposure:>5.0%} | Median {self.median_return:+.2%} "
            f"Mittel {self.mean_return:+.2%} | VaR {self.var:+.2%} "
            f"CVaR {self.cvar:+.2%} (Grenze {self.cvar_limit:+.2%}, {mark}) | "
            f"P(Verlust) {self.prob_loss:.1%} | "
            f"MaxDD median {self.max_drawdown_median:+.2%} "
            f"CVaR {self.drawdown_cvar:+.2%}"
        )


@dataclass(frozen=True, slots=True)
class OptimisationResult:
    """Ergebnis der Suche, inklusive der Moeglichkeit, nicht zu handeln.

    `scores` enthaelt das komplette ausgewertete Gitter, auch die
    unzulaessigen Punkte. Eine Optimierung, die nur ihr Optimum
    zurueckgibt, laesst sich nicht nachpruefen -- und die Frage "wie knapp
    war das" ist genau die, die man spaeter stellt.
    """

    exposure: float
    score: AllocationScore
    scores: tuple[AllocationScore, ...]
    cvar_limit: float
    reason: str

    @property
    def traded(self) -> bool:
        return self.exposure > 0.0

    def describe(self) -> str:
        head = "kein Trade" if not self.traded else f"Exposure {self.exposure:.0%}"
        return f"{head} -- {self.reason}"


# ----------------------------------------------------------------------
# Risikomasse
# ----------------------------------------------------------------------


def value_at_risk(
    terminal_returns: np.ndarray,
    weights: np.ndarray | None = None,
    alpha: float = 0.05,
) -> float:
    """Gewichteter VaR als Rendite (negativ = Verlust).

    Duennes Mapping auf `qt.sim.base.weighted_quantile` -- bewusst keine
    zweite Quantil-Definition. Zwei Quantilbegriffe im selben System laufen
    auseinander, und dann misst die Nebenbedingung etwas anderes als der
    Report, der sie erklaeren soll.

    Steht hier trotzdem als eigene Funktion, weil `VaR` der Begriff ist, den
    man sucht, und niemand ein Quantil sucht.
    """
    values, w = _clean(terminal_returns, weights)
    _check_alpha(alpha)
    return float(weighted_quantile(values, w, alpha))


def cvar(
    terminal_returns: np.ndarray,
    weights: np.ndarray | None = None,
    alpha: float = 0.05,
) -> float:
    """Gewichteter Expected Shortfall als Rendite (negativ = Verlust).

    Definiert als **gewichteter Mittelwert der schlechtesten `alpha`
    Gewichtsmasse**. Nicht als "Mittelwert der Werte unterhalb des VaR": bei
    ungleichen Gewichten und wenigen Pfaden liegt am Quantil ein Atom, das
    teils drin und teils draussen liegt. Wer es ganz mitnimmt oder ganz
    weglaesst, bekommt einen CVaR, der von der Ensemblegroesse abhaengt --
    und in extremen Faellen ueber dem VaR liegt, was per Definition nicht
    sein kann. Die anteilige Verrechnung ist exakt und kommt ohne Sonderfall
    fuer "kein Pfad im Tail" aus.

    Die Gewichte sind der eigentliche Punkt dieser Funktion. Ein CVaR, der
    sie ignoriert, macht jede Szenario-Umgewichtung (`qt.sim.scenarios`)
    stillschweigend wirkungslos -- dieselbe Fehlerklasse wie in ADR-016:
    eine Zahl, die plausibel aussieht und etwas anderes misst, als ihr Name
    behauptet.
    """
    values, w = _clean(terminal_returns, weights)
    _check_alpha(alpha)

    order = np.argsort(values, kind="stable")
    v, w = values[order], w[order]

    # Gewichtsmasse *vor* jedem Wert; davon faellt in den Tail, was noch in
    # das Budget `alpha` passt -- der letzte Wert eben nur anteilig.
    before = np.cumsum(w) - w
    taken = np.clip(alpha - before, 0.0, w)

    mass = taken.sum()
    if mass <= 0:
        # Nur erreichbar, wenn alpha unter die Fliesskomma-Aufloesung faellt.
        # Dann ist der schlechteste Pfad die einzige sinnvolle Antwort --
        # nicht 0.0, das waere ein Risiko von null.
        return float(v[0])
    return float(np.dot(taken, v) / mass)


# ----------------------------------------------------------------------
# Bewertung eines Exposures
# ----------------------------------------------------------------------


def score_allocation(
    ensemble: PathEnsemble,
    exposure: float,
    cvar_limit: float = DEFAULT_CVAR_LIMIT,
    cfg: ObjectiveConfig | None = None,
) -> AllocationScore:
    """Ein Exposure ueber das ganze Ensemble bewerten.

    Das Exposure wird auf die **Pfade** angewendet und alle Kennzahlen
    werden daraus neu gerechnet. Es waere billiger, einmal bei Exposure 1
    zu rechnen und das Ergebnis linear zu skalieren -- und falsch: doppeltes
    Exposure verdoppelt die Bar-Renditen, aber nicht das Endergebnis. Eine
    Kette +20%/-20% ergibt bei halbem Exposure -1,0% und bei vollem -4,0%,
    also das Vierfache. Dieser Volatilitaetszug ist genau das, was der CVaR
    messen soll; wer linear hochskaliert, rechnet ihn weg.
    """
    cfg = cfg or ObjectiveConfig()
    exposure = float(exposure)
    if not np.isfinite(exposure):
        raise ValueError(f"Exposure muss endlich sein, ist aber {exposure}.")
    if exposure < 0:
        raise ValueError(
            "Exposure darf nicht negativ sein. Die Richtung kommt aus den "
            "Zielgewichten der Strategien; diese Zielfunktion dimensioniert "
            "nur, sie dreht nicht um."
        )
    if cvar_limit > 0:
        raise ValueError(
            f"cvar_limit ist eine Rendite und muss <= 0 sein, ist aber "
            f"{cvar_limit}. Ein positives Limit hiesse 'die schlimmsten "
            f"{cfg.alpha:.0%} muessen im Mittel Gewinn machen' -- vermutlich "
            f"ein Vorzeichenfehler."
        )

    weights = np.asarray(ensemble.weights, dtype=float)
    terminal, drawdowns = _exposed_outcomes(
        ensemble.paths, exposure, cfg.round_trip_bps
    )

    terminal_cvar = cvar(terminal, weights, cfg.alpha)
    dd_cvar = cvar(drawdowns, weights, cfg.alpha)
    risk = dd_cvar if cfg.risk_basis == "drawdown" else terminal_cvar

    return AllocationScore(
        exposure=exposure,
        median_return=float(weighted_quantile(terminal, weights, 0.5)),
        mean_return=float(np.dot(weights, terminal)),
        cvar=terminal_cvar,
        var=value_at_risk(terminal, weights, cfg.alpha),
        prob_loss=float(weights[terminal < 0.0].sum()),
        max_drawdown_median=float(weighted_quantile(drawdowns, weights, 0.5)),
        drawdown_cvar=dd_cvar,
        cvar_limit=float(cvar_limit),
        risk_basis=cfg.risk_basis,
        feasible=bool(risk >= cvar_limit - _LIMIT_EPS),
    )


def optimise_allocation(
    ensemble: PathEnsemble,
    cvar_limit: float = DEFAULT_CVAR_LIMIT,
    cfg: ObjectiveConfig | None = None,
) -> OptimisationResult:
    """Median-Rendite maximieren unter der CVaR-Grenze.

    Gittersuche statt Solver. Das Problem ist eindimensional, beschraenkt
    und nicht garantiert konvex (der Median ueber ein Ensemble ist eine
    Treppenfunktion des Exposures). Ein Gradientenverfahren haette hier
    nichts zu greifen, und ein Gitter ist ausserdem das, was man hinterher
    ausdrucken und nachlesen kann -- siehe `OptimisationResult.scores`.

    **Kein Trade ist ein moegliches Ergebnis.** Zwei Wege dorthin:

    1. Kein Gitterpunkt haelt die CVaR-Grenze ein. Die Nebenbedingung
       bindet, und die einzige zulaessige Antwort ist flat zu bleiben.
    2. Das beste zulaessige Exposure hat nach Kosten keine positive
       Median-Rendite. Flat zu bleiben liefert exakt 0 und ist damit strikt
       besser -- inklusive der 90 bps, die man sich spart.

    Bei Gleichstand im Median gewinnt das **kleinere** Exposure: gleiche
    typische Rendite bei weniger Risiko und weniger Kosten ist keine
    Geschmacksfrage.
    """
    cfg = cfg or ObjectiveConfig()
    grid = cfg.grid()

    scores = tuple(
        score_allocation(ensemble, float(e), cvar_limit, cfg) for e in grid
    )
    flat = score_allocation(ensemble, 0.0, cvar_limit, cfg)
    allowed = [s for s in scores if s.feasible]

    if not allowed:
        worst_case = scores[0]
        return OptimisationResult(
            exposure=0.0,
            score=flat,
            scores=scores,
            cvar_limit=float(cvar_limit),
            reason=(
                f"Kein Trade: schon das kleinste Exposure "
                f"{worst_case.exposure:.0%} verletzt die CVaR-Grenze "
                f"(CVaR {worst_case.cvar:+.2%} < {cvar_limit:+.2%})."
            ),
        )

    best = max(allowed, key=lambda s: (s.median_return, -s.exposure))
    if best.median_return <= 0.0:
        return OptimisationResult(
            exposure=0.0,
            score=flat,
            scores=scores,
            cvar_limit=float(cvar_limit),
            reason=(
                f"Kein Trade: bestes zulaessiges Exposure {best.exposure:.0%} "
                f"hat Median-Rendite {best.median_return:+.2%} nach Kosten -- "
                f"flat zu bleiben ist strikt besser."
            ),
        )

    return OptimisationResult(
        exposure=best.exposure,
        score=best,
        scores=scores,
        cvar_limit=float(cvar_limit),
        reason=(
            f"Median {best.median_return:+.2%} ist das Maximum ueber "
            f"{len(allowed)} von {len(scores)} zulaessigen Exposures "
            f"(CVaR {best.cvar:+.2%} gegen Grenze {cvar_limit:+.2%})."
        ),
    )


# ----------------------------------------------------------------------
# Interna
# ----------------------------------------------------------------------


def _exposed_outcomes(
    paths: np.ndarray, exposure: float, rt_bps: float
) -> tuple[np.ndarray, np.ndarray]:
    """Endrenditen und Max-Drawdowns je Pfad bei gegebenem Exposure.

    Vollstaendig vektorisiert ueber die Pfade: ein Gitter aus zwanzig
    Exposures ueber 10.000 Pfade sind zwanzig Array-Operationen, keine
    200.000 Schleifendurchlaeufe. Ueber das Gitter wird bewusst geschleift
    statt ein drittes Array-Achse aufzuspannen -- das Gitter hat zwanzig
    Punkte, das 3D-Array haette nur Speicher gekostet.

    Der Wachstumsfaktor wird bei 0 abgeschnitten: ein Pfad, der mit Hebel
    unter -100% faellt, ist ruiniert und bleibt es. Ohne das Abschneiden
    wuerde ein negativer Kontostand mit dem naechsten Verlust wieder
    *steigen* -- ein Vorzeichenfehler, der ausgerechnet die schlimmsten
    Pfade beschoenigt, also genau die, auf die es beim CVaR ankommt.

    **Die Kosten sind genau ein Round-Trip ueber den ganzen Horizont**,
    proportional zum Exposure. Das unterstellt Kaufen und Liegenlassen; eine
    Strategie, die im Horizont mehrfach umschichtet, zahlt in Wirklichkeit ein
    Vielfaches. Fuer die Frage dieser Zielfunktion -- wieviel Exposure haelt
    die Verlustgrenze -- ist das vertretbar, aber es heisst auch: die Kosten
    unterscheiden die Gitterpunkte kaum, und verschieden aktive Strategien
    sind hier nicht vergleichbar abgebildet (ADR-053).
    """
    growth = np.maximum(1.0 + exposure * paths, 0.0)
    curves = np.cumprod(growth, axis=1)

    # Ein Round-Trip, proportional zum Exposure. Als konstanter Faktor auf
    # das Kapital: der Drawdown ist skaleninvariant und bleibt davon
    # unberuehrt, die Endrendite nicht -- was genau richtig ist, denn die
    # Gebuehr aendert nicht den Verlauf, sondern das Ergebnis.
    cost = min(exposure * rt_bps * BPS, 1.0)
    terminal = curves[:, -1] * (1.0 - cost) - 1.0

    start = np.ones((curves.shape[0], 1))
    full = np.hstack([start, curves])
    peaks = np.maximum.accumulate(full, axis=1)
    drawdowns = (full / peaks - 1.0).min(axis=1)

    return terminal, drawdowns


def _clean(
    values: np.ndarray, weights: np.ndarray | None
) -> tuple[np.ndarray, np.ndarray]:
    """Werte und Gewichte pruefen und normieren.

    Wiederholt die Pruefungen aus `PathEnsemble.__post_init__`, weil `cvar`
    und `value_at_risk` auch auf rohen Arrays aufgerufen werden -- etwa auf
    einer Renditereihe aus dem Backtest, die nie ein Ensemble war.
    """
    v = np.asarray(values, dtype=float).ravel()
    if v.size == 0:
        raise ValueError("Leere Stichprobe -- kein Risikomass bestimmbar.")
    if not np.all(np.isfinite(v)):
        raise ValueError(
            "Nicht-endliche Renditen. Ein einziges nan macht jedes Quantil "
            "darueber unbrauchbar."
        )

    if weights is None:
        return v, np.full(v.size, 1.0 / v.size)

    w = np.asarray(weights, dtype=float).ravel()
    if w.shape != v.shape:
        raise ValueError(
            f"Gewichte haben {w.shape}, Werte {v.shape} -- passt nicht."
        )
    if not np.all(np.isfinite(w)) or np.any(w < 0):
        raise ValueError("Gewichte muessen endlich und nicht-negativ sein.")
    total = w.sum()
    if total <= 0:
        raise ValueError("Gewichte summieren sich zu null.")
    return v, w / total


def _check_alpha(alpha: float) -> None:
    if not (0.0 < alpha < 1.0):
        raise ValueError(
            f"alpha muss echt zwischen 0 und 1 liegen, ist aber {alpha}. "
            f"alpha ist der Tail-Anteil (0.05 = schlimmste 5%), nicht ein "
            f"Konfidenzniveau (0.95)."
        )
