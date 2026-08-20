"""Vertraege der Portfolio-Schicht.

Diese Datei definiert die Schnittstellen, gegen die Allokatoren, die
Risk-Engine und der Walk-Forward-Lauf gebaut werden. Sie enthaelt bewusst
keine Logik -- nur die Begriffe, auf die sich alle einigen.

Die Kette (siehe ARCHITECTURE.md):

    Strategien -> Zielgewichte je Strategie
                    |
                    v
    Allokator  -> Kapitalanteil je Strategie   (LLM oder Baseline)
                    |
                    v
    Kombination-> Zielgewicht je Symbol
                    |
                    v
    Risk-Engine-> beschnittenes Zielgewicht    (hart, deterministisch)
                    |
                    v
    Engine     -> Orders

Der Allokator schlaegt vor, die Risk-Engine entscheidet. Diese Reihenfolge
ist nicht verhandelbar: ab Phase 3 sitzt an der Allokator-Stelle ein LLM,
und dessen Ausgabe darf das Konto unter keinen Umstaenden sprengen koennen.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

# strategy_id -> {symbol: Zielgewicht in [-1, +1]}
StrategyWeights = dict[str, dict[str, float]]

# symbol -> Zielgewicht des Gesamtportfolios
PortfolioWeights = dict[str, float]

# strategy_id -> Kapitalanteil, Summe der Betraege <= 1
Allocation = dict[str, float]


@dataclass(slots=True)
class AllocationContext:
    """Was ein Allokator zum Entscheidungszeitpunkt sehen darf.

    Alles hier ist strikt Point-in-Time: `returns` enthaelt ausschliesslich
    Bar-Renditen, die vor `ts` realisiert wurden. Ein Allokator, der mehr
    braucht, bekommt es nicht -- er ist die unzuverlaessige Komponente im
    System und wird entsprechend knapp gehalten.
    """

    ts: datetime
    strategy_ids: list[str]
    # strategy_id -> Zeitreihe realisierter Bar-Renditen dieser Strategie
    returns: dict[str, np.ndarray]
    equity: float
    current: Allocation = field(default_factory=dict)
    timeframe: str = "1h"

    def history_length(self) -> int:
        """Kuerzeste verfuegbare Historie ueber **alle** Strategien.

        Achtung, das ist selten das richtige Warmup-Gate: eine einzige neu
        hinzugefuegte Strategie zieht diesen Wert auf null und wuerde ein
        eingespieltes Portfolio komplett zurueck auf Gleichgewichtung
        werfen -- samt der Umschichtungskosten dafuer.

        Fuer Warmup-Entscheidungen gehoert die Historie **je Strategie**
        geprueft (`lengths()`), damit nur die neue Strategie wartet und
        nicht das ganze Portfolio.
        """
        if not self.returns:
            return 0
        return min((len(r) for r in self.returns.values()), default=0)

    def lengths(self) -> dict[str, int]:
        """Verfuegbare Historie je Strategie."""
        return {sid: len(self.returns.get(sid, ())) for sid in self.strategy_ids}


class Allocator(ABC):
    """Verteilt Kapital auf Strategien.

    Rueckgabe ist ein Anteil je Strategie. Vorzeichen sind erlaubt (eine
    Strategie invertieren), aber die Summe der Betraege sollte 1 nicht
    ueberschreiten -- die Risk-Engine erzwingt das ohnehin, der Allokator
    soll es aber gar nicht erst versuchen.
    """

    name: str = "unnamed"

    @property
    def warmup_bars(self) -> int:
        """Bars Historie, bevor die erste Allokation aussagekraeftig ist.

        Davor liefert `allocate` typischerweise Gleichgewichtung.
        """
        return 0

    @abstractmethod
    def allocate(self, ctx: AllocationContext) -> Allocation:
        """Kapitalanteil je Strategie zum Zeitpunkt `ctx.ts`."""

    def describe(self) -> str:
        return self.name


def combine(
    strategy_weights: StrategyWeights, allocation: Allocation
) -> PortfolioWeights:
    """Strategie-Gewichte und Kapitalanteile zu Portfolio-Gewichten verrechnen.

    Zwei Strategien, die dasselbe Symbol in dieselbe Richtung handeln,
    addieren sich; gegenlaeufige heben sich teilweise auf. Das ist gewollt:
    das Portfolio soll die Netto-Meinung handeln, nicht beide Seiten
    gleichzeitig und dabei zweimal Gebuehren zahlen.
    """
    out: PortfolioWeights = {}
    for strategy_id, weights in strategy_weights.items():
        share = allocation.get(strategy_id, 0.0)
        if share == 0.0:
            continue
        for symbol, weight in weights.items():
            if weight != weight:  # nan = keine Meinung
                continue
            out[symbol] = out.get(symbol, 0.0) + share * weight
    return out


@dataclass(slots=True)
class RiskState:
    """Was die Risk-Engine zum Beschneiden braucht."""

    ts: datetime
    equity: float
    peak_equity: float
    # symbol -> annualisierte realisierte Volatilitaet, `nan` wenn unbekannt
    realised_vol: dict[str, float] = field(default_factory=dict)
    halted: bool = False

    @property
    def drawdown(self) -> float:
        """Aktueller Abstand zum Hoechststand, negativ.

        Bei nicht-positivem Hoechststand ist das Konto zerstoert, nicht
        unauffaellig: die naheliegende Rueckgabe 0.0 hiesse "kein Drawdown"
        und liesse ein ruiniertes Konto durch jeden Kill-Switch, der sich auf
        diese Property verlaesst. Deshalb -1.0 -- der schlechteste
        darstellbare Wert, der garantiert jede Grenze reisst.
        """
        if self.peak_equity <= 0:
            return -1.0
        return self.equity / self.peak_equity - 1.0


class RiskLimits(ABC):
    """Beschneidet Portfolio-Gewichte. Deterministisch und ueberpruefbar."""

    @abstractmethod
    def apply(
        self, weights: PortfolioWeights, state: RiskState
    ) -> tuple[PortfolioWeights, list[str]]:
        """Beschnittene Gewichte plus Begruendungen der Eingriffe.

        Die Begruendungen landen im Report. Ein Risikoeingriff, den niemand
        sieht, wird nicht untersucht -- und ein staendig greifender Cap ist
        ein Hinweis darauf, dass die Strategie falsch dimensioniert ist.
        """
