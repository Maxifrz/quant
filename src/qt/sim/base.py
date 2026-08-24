"""Vertraege der Pfad-Simulation.

Die urspruengliche Projektidee war, "den wahrscheinlichsten Verlauf zu
traden". Genau so formuliert ist das der klassische Weg, Geld zu verlieren:
ein einzelner prognostizierter Pfad ist eine Wette, keine Kante. Wer ihn
trifft, hatte Glueck; wer ihn verfehlt, hat nichts, worauf er zurueckfaellt.

Die tragfaehige Version derselben Idee steht hier: **ein Ensemble** moeglicher
Pfade erzeugen und eine Allokation waehlen, die ueber das ganze Ensemble
hinweg gut abschneidet. Aus "wahrscheinlichster Verlauf" wird damit etwas
Rechenbares -- und zwar eine Verteilung statt einer Zahl.

Die Kette:

    historische Renditen
            |
            v
    PathGenerator      -> PathEnsemble        (viele moegliche Zukuenfte)
            |
            v
    Szenario-Priors    -> umgewichtetes Ensemble  (LLM gewichtet Moeglichkeiten,
            |                                      es prognostiziert keinen Preis)
            v
    Zielfunktion       -> Allokation          (Median-Rendite unter CVaR-Grenze)

Was hier bewusst **nicht** steht: eine Methode, die einen einzelnen "besten"
Pfad zurueckgibt. Das Interface laesst diese Antwort gar nicht erst zu.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass(slots=True)
class PathEnsemble:
    """Viele moegliche Fortsetzungen einer Renditereihe.

    `paths` hat die Form (n_paths, horizon) und enthaelt **einfache
    Bar-Renditen**, keine Log-Renditen und keine Preise. Einfache Renditen,
    weil sie sich ueber Symbole hinweg zu einem Portfolio addieren lassen --
    Log-Renditen tun das nicht, und genau diese Verwechslung ist eine der
    haeufigsten stillen Fehlerquellen in Portfolio-Rechnungen.

    `weights` gewichtet die Pfade untereinander. Standard ist Gleichgewichtung;
    Szenario-Priors (Phase 4, `qt.sim.scenarios`) veraendern sie. Die Gewichte
    summieren sich immer auf 1 -- das erzwingt `__post_init__`, weil eine
    unnormierte Gewichtung jede Quantil-Rechnung stillschweigend verfaelscht.
    """

    paths: np.ndarray
    weights: np.ndarray | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.paths = np.asarray(self.paths, dtype=float)
        if self.paths.ndim != 2:
            raise ValueError(
                f"paths muss die Form (n_paths, horizon) haben, hat aber "
                f"{self.paths.shape}."
            )
        if self.paths.size == 0:
            raise ValueError("Leeres Ensemble -- nichts zu bewerten.")
        if not np.all(np.isfinite(self.paths)):
            raise ValueError(
                "Ensemble enthaelt nicht-endliche Renditen. Ein einziges nan "
                "macht jede Quantil-Rechnung darueber unbrauchbar, deshalb "
                "wird es hier abgefangen und nicht spaeter."
            )

        if self.weights is None:
            self.weights = np.full(self.n_paths, 1.0 / self.n_paths)
            return

        w = np.asarray(self.weights, dtype=float)
        if w.shape != (self.n_paths,):
            raise ValueError(
                f"weights muss {self.n_paths} Eintraege haben, hat aber {w.shape}."
            )
        if not np.all(np.isfinite(w)) or np.any(w < 0):
            raise ValueError("Gewichte muessen endlich und nicht-negativ sein.")
        total = w.sum()
        if total <= 0:
            raise ValueError("Gewichte summieren sich zu null.")
        self.weights = w / total

    @property
    def n_paths(self) -> int:
        return int(self.paths.shape[0])

    @property
    def horizon(self) -> int:
        return int(self.paths.shape[1])

    def growth_factors(self) -> np.ndarray:
        """Wachstumsfaktor je Bar, bei null abgeschnitten.

        **Ruin ist absorbierend.** Ohne das Abschneiden rechnet
        `prod(1 + r)` durch negatives Kapital hindurch: zwei Bars mit -150%
        ergeben (-0.5)*(-0.5) = +0.25, also gemeldete -75% statt -100% --
        und eine Kapitalkurve, die von -0.5 auf +0.25 "steigt".

        Das ist kein akademischer Randfall: es trifft ausgerechnet die
        schlimmsten Pfade, also genau die, auf die es beim CVaR ankommt. Ein
        Ensemble mit gehebeltem Exposure erzeugt sie regelmaessig, und der
        Fehler beschoenigt dort das Tail-Risiko -- die eine Richtung, in die
        eine Risikorechnung nicht danebenliegen darf.
        """
        return np.maximum(1.0 + self.paths, 0.0)

    def terminal_returns(self) -> np.ndarray:
        """Kumulierte Rendite je Pfad ueber den ganzen Horizont.

        Multiplikativ verkettet, nicht addiert: eine Verdopplung gefolgt von
        einer Halbierung ist eine Nullrendite, keine +50%. Untergrenze -100%,
        siehe `growth_factors`.
        """
        return np.prod(self.growth_factors(), axis=1) - 1.0

    def equity_curves(self, initial: float = 1.0) -> np.ndarray:
        """Kapitalkurven aller Pfade, Form (n_paths, horizon + 1).

        Faellt eine Kurve auf null, bleibt sie dort -- siehe `growth_factors`.
        """
        curves = np.cumprod(self.growth_factors(), axis=1)
        return initial * np.hstack([np.ones((self.n_paths, 1)), curves])

    def reweighted(self, weights: np.ndarray) -> "PathEnsemble":
        """Dasselbe Ensemble mit anderer Gewichtung.

        Gibt eine neue Instanz zurueck statt zu mutieren: ein umgewichtetes
        Ensemble ist eine andere Annahme ueber die Welt, und die alte soll
        daneben vergleichbar bleiben.

        `paths` wird dabei kopiert. Ein geteiltes Array waere die halbe
        Miete: `PathEnsemble` ist nicht frozen, eine Aenderung an einer der
        beiden Instanzen wuerde still auch die andere treffen -- und damit
        genau die Vergleichbarkeit zerstoeren, die diese Methode herstellen
        soll. Der Speicher dafuer ist bei den hier ueblichen Groessen
        vertretbar; Umgewichtungen passieren pro Lauf einmal, nicht pro Bar.
        """
        return PathEnsemble(
            paths=self.paths.copy(), weights=weights, meta=dict(self.meta)
        )

    def quantile(self, q: float | np.ndarray) -> np.ndarray:
        """Gewichtetes Quantil der Endrenditen.

        `np.quantile` kennt keine Gewichte. Wuerde man es hier verwenden,
        waere jede Umwichtung durch Szenario-Priors stillschweigend
        wirkungslos -- die Zahlen saehen identisch aus, und niemand haette
        einen Grund nachzusehen.
        """
        return weighted_quantile(self.terminal_returns(), self.weights, q)

    def describe(self) -> str:
        p05, median, p95 = self.quantile([0.05, 0.5, 0.95])
        return (
            f"{self.n_paths:,} Pfade x {self.horizon} Bars | "
            f"Endrendite p05 {p05:+.1%} median {median:+.1%} p95 {p95:+.1%}"
        )


class PathGenerator(ABC):
    """Erzeugt ein Ensemble moeglicher Fortsetzungen.

    Jede Umsetzung nimmt eine historische Renditereihe und gibt viele
    moegliche Zukuenfte zurueck. Was sie dabei aus der Historie erhaelt --
    Autokorrelation, Vol-Clustering, Regimewechsel -- ist der eigentliche
    Unterschied zwischen den Verfahren.

    `seed` ist Pflicht und nicht optional: ein Simulationsergebnis, das sich
    bei jedem Lauf aendert, ist als Entscheidungsgrundlage wertlos, und ein
    Backtest darueber waere nicht reproduzierbar.
    """

    name: str = "unnamed"

    @abstractmethod
    def generate(
        self, returns: np.ndarray, horizon: int, n_paths: int, seed: int
    ) -> PathEnsemble:
        """Ensemble aus einfachen Bar-Renditen erzeugen."""

    def describe(self) -> str:
        return self.name


def weighted_quantile(
    values: np.ndarray, weights: np.ndarray, q: float | np.ndarray
) -> np.ndarray:
    """Quantile einer gewichteten Stichprobe.

    Steht hier und nicht in `qt.sim.objective`, weil schon `PathEnsemble`
    selbst gewichtete Quantile braucht -- und zwei Umsetzungen desselben
    Quantilbegriffs im System wuerden frueher oder spaeter auseinanderlaufen.

    Verwendet die kumulierte Gewichtssumme mit Halbschritt-Korrektur
    (`cum - 0.5 * w`) -- in der ueblichen Zaehlung die Quantil-Definition
    "Typ 5". Ohne die Korrektur haette das Ergebnis einen systematischen
    Versatz von einem halben Gewicht.

    **Nicht identisch mit `np.quantile`.** Numpys Standard ist Typ 7; beide
    stimmen im Median exakt ueberein und weichen an den Raendern um bis zu
    einem halben Gewicht ab (bei 100 gleichgewichteten Werten liefert Typ 5
    fuer q=0.05 den Wert 4.5, Typ 7 den Wert 4.95). Bei den hier ueblichen
    Ensemblegroessen (Tausende Pfade) ist der Unterschied bedeutungslos; die
    Wahl faellt trotzdem bewusst auf Typ 5, weil er bei ungleichen Gewichten
    der uebliche und erwartungstreue Ansatz ist -- und weil eine Formel, die
    nur zufaellig mit numpy uebereinstimmt, spaeter jemanden dazu verleitet,
    sie durch `np.quantile` zu ersetzen und damit die Gewichte zu verlieren.
    """
    q_arr = np.asarray(q, dtype=float)
    if not np.all(np.isfinite(q_arr)) or np.any(q_arr < 0) or np.any(q_arr > 1):
        # `np.interp` klemmt Werte ausserhalb des Bereichs stillschweigend auf
        # die Raender. Ein versehentliches q=5 statt q=0.05 laeferte damit das
        # Maximum -- eine Zahl, die plausibel aussieht und das Gegenteil des
        # Gemeinten ist.
        raise ValueError(f"Quantile muessen in [0, 1] liegen, sind {q!r}.")

    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    order = np.argsort(values)
    v, w = values[order], weights[order]

    total = w.sum()
    if total <= 0:
        raise ValueError("Gewichte summieren sich zu null.")
    cum = (np.cumsum(w) - 0.5 * w) / total

    return np.interp(q_arr, cum, v)


def validate_history(
    returns: np.ndarray, min_len: int = 32, allow_gaps: bool = False
) -> np.ndarray:
    """Historische Renditen pruefen.

    Gemeinsam genutzt von allen Generatoren, damit sie sich in ihren
    Anforderungen an die Eingabe nicht unbemerkt auseinanderentwickeln.

    **Nicht-endliche Werte sind per Default ein Fehler, kein Filterfall.**
    Ein `nan` in einer Renditereihe bedeutet fast immer eine Datenluecke --
    also einen Integritaetsbefund, fuer den es in `qt.data.integrity` eine
    eigene Pruefung gibt. Ihn hier stillschweigend herauszuwerfen klebt die
    Nachbarn der Luecke aneinander und erzeugt einen Uebergang, den es im
    Markt nie gab.

    Fuer einen Block-Bootstrap ist das besonders folgenreich: er lebt genau
    von der zeitlichen Nachbarschaft, und die kuenstliche Klebestelle wandert
    in jeden Block, der ueber sie laeuft. Bei einem i.i.d.-Verfahren waere es
    harmlos -- aber ein gemeinsamer Validator darf sich nicht am harmlosesten
    Aufrufer ausrichten.

    `allow_gaps=True` stellt das alte Verhalten wieder her, fuer Faelle in
    denen die Luecke bekannt und bewusst hingenommen ist.
    """
    values = np.asarray(returns, dtype=float).ravel()
    n_bad = int((~np.isfinite(values)).sum())

    if n_bad and not allow_gaps:
        raise ValueError(
            f"{n_bad} nicht-endliche Rendite(n) in der Historie. Das ist fast "
            "immer eine Datenluecke -- pruefe sie mit `qt data report`, statt "
            "sie zu ueberspringen. Bewusst hinnehmen: allow_gaps=True."
        )

    finite = values[np.isfinite(values)]
    if len(finite) < min_len:
        raise ValueError(
            f"Zu wenig Historie: {len(finite)} brauchbare Renditen, "
            f"mindestens {min_len} noetig."
        )
    return finite
