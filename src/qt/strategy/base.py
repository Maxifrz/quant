"""Strategie-Interface.

Eine Strategie gibt **Zielgewichte** aus, keine Orders (ADR-002). Sie weiss
nichts ueber Kontogroesse, Hebel oder andere Strategien -- nur darueber,
wohin der Markt aus ihrer Sicht zeigt.

Das trennt drei Verantwortungen, die sonst verschmelzen:

    "Wohin zeigt der Markt?"      -> Strategie
    "Wieviel Kapital darauf?"     -> Allokator (Phase 3)
    "Was ist maximal erlaubt?"    -> Risk-Engine (Phase 2)

Dadurch kann man eine Strategie austauschen, ohne das Risikomodell
anzufassen -- und umgekehrt.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from qt.features.registry import FeatureStore

# Zielgewicht je Symbol, jeweils in [-1, +1].
Weights = dict[str, float]


class Strategy(ABC):
    """Basisklasse aller Strategien.

    Unterklassen setzen `name` und implementieren `on_bar`. Parameter werden
    als Keyword-Argumente uebergeben und in `self.params` festgehalten --
    damit sie spaeter in Reports und in der Research-Registry auftauchen,
    ohne dass jede Strategie das selbst verdrahten muss.
    """

    name: str = "unnamed"

    def __init__(self, symbols: list[str], timeframe: str, **params: Any) -> None:
        self.symbols = list(symbols)
        self.timeframe = timeframe
        self.params: dict[str, Any] = dict(params)
        self._state: dict[str, Any] = {}

    @property
    @abstractmethod
    def warmup_bars(self) -> int:
        """Bars, die vor dem ersten gueltigen Signal noetig sind.

        Die Engine ruft `on_bar` **erst** auf, wenn so viele Bars des jeweiligen
        Symbols vorliegen. Eine Strategie wird also nie nach einer Meinung
        gefragt, die sie nicht bilden kann.

        Zustandsaufbau geht dadurch nicht verloren: der Feature-Store enthaelt
        beim ersten Aufruf bereits die gesamte bis dahin gesehene Historie,
        inklusive der Warmup-Bars. Wer mehr Vorlauf braucht, gibt hier eine
        groessere Zahl an.

        Ohne diese Grenze haengt das Ergebnis davon ab, wo der Datensatz
        zufaellig beginnt.
        """

    @abstractmethod
    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        """Zielgewicht fuer `symbol` nach dem Close des aktuellen Bars.

        Rueckgabe in [-1, +1]. `nan` bedeutet "keine Meinung" -- die Engine
        behaelt dann das bestehende Gewicht bei, statt die Position zu schliessen.
        Der Unterschied ist wichtig: "kein Signal" ist nicht "geh flat".

        Der Store liefert ausschliesslich bereits geschlossene Bars. Ein
        Zugriff darueber hinaus wirft `LookaheadError`.
        """

    def describe(self) -> str:
        params = ", ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.name}({params})"


def clip_weight(weight: float) -> float:
    """Gewicht auf [-1, +1] begrenzen, `nan` durchreichen."""
    if weight != weight:  # nan
        return weight
    return max(-1.0, min(1.0, float(weight)))
