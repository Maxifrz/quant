"""Die Positionsgroessen-Schicht fuer generierte Kandidaten (ADR-069).

Warum es sie braucht: der Research-Loop hatte keine. Ein Kandidat, der fuer
jeden Markt unabhaengig ein Gewicht von 1,0 meldet, laesst das
Bruttoexposure ueber 38 Maerkte auf 38x auflaufen und ruiniert jedes Konto;
auf 1 normiert handelt keiner mehr. Zwischen Bankrott und Untaetigkeit lag
keine Einstellung, die das Ergebnis der **Idee** gezeigt haette (ADR-065).

Die Regel: **proportional auf das Bruttobudget skalieren, sonst nichts.**

    w_i  ->  w_i * grenze / Sigma|w|   , falls Sigma|w| > grenze
    w_i  ->  w_i                       , sonst

Damit teilen sich die Maerkte, in denen der Kandidat eine Meinung hat, das
Konto gleichmaessig -- und ein Kandidat mit einer Meinung in einem einzigen
Markt bekommt dort das volle Gewicht. Selektivitaet wird nicht bestraft.

Zwei Alternativen standen zur Wahl und sind vorab verworfen, damit die
Entscheidung nicht davon abhaengt, wie der naechste Lauf ausgeht:

*Vol-Targeting* braucht Schaetzer, Rueckschaufenster und Zielvolatilitaet --
drei zusaetzliche Freiheitsgrade in einem Projekt, das jeden einzelnen im
DSR-Nenner bezahlt (ADR-005). Es hat ausserdem einen eigenen, dokumentierten
Effekt auf den Sharpe und beantwortet damit eine andere Frage als die, um die
es hier geht: **traegt das Signal Information?** Ein Ergebnis aus Signal *und*
Groessensteuerung beantwortet keine von beiden.

*Gleichgewichtung mit 1/n ueber alle Maerkte* bestraft Selektivitaet: ein
Kandidat mit einer Position in 1 von 38 Maerkten bekaeme 1/38 Exposure, und
sein Ergebnis waere von Rauschen nicht zu unterscheiden, egal wie gut das
Signal ist.

**Warum hier und nicht in der Engine.** Der naheliegende Ort waere
`rebalance_order`, wo die Grenze schon je Symbol geprueft wird. Zwei
Anlaeufe dort sind gemessen gescheitert:

1. *Skalierung des Ziel-Dicts der Engine.* Die Engine arbeitet die Bars
   eines Zeitpunkts nacheinander ab; waehrenddessen ist das Ziel-Dict eine
   Mischung aus alten und neuen Gewichten. Bei `crossmom` liegt diese
   Mischung im Median bei 1,09 Brutto, obwohl weder der alte noch der neue
   Stand 1,0 reisst. Die Grenze griff in **77,7 %** der Bars, sparte
   Gebuehren und hob den Sharpe von 0,21 auf 0,38 -- eine Verbesserung aus
   einem Messartefakt, also genau die Sorte Fund, vor der ADR-066 warnt.
2. *Budget gegen die gehaltenen Positionen.* Kein Artefakt mehr, aber
   `crossmom` zielt konstruktionsbedingt auf Brutto genau 1,0. Eine harte
   Grenze auf demselben Wert liegt dauernd auf der Kante: jede Kursbewegung
   beschneidet die naechste Order, das erzeugt eine Gegenbewegung, und die
   Ausfuehrungen stiegen von 308 auf 923 bei Sharpe 0,21 -> -0,21.

Beide Male war die Ursache dieselbe: die Engine sieht nie einen **kohaerenten**
Zielvektor. Die Querschnittsfamilie loest das seit ADR-058 selbst
(`gewichte_aus_score` teilt durch Sigma|w|), und die Bibliotheksstrategien
halten je ein Symbol. Uebrig bleibt genau der Fall, fuer den diese Schicht da
ist -- ein Kandidat, dessen Gewichte je Symbol unabhaengig entstehen. Fuer den
ist die Mischung aus alten und neuen Werten kein Artefakt, sondern der
Zustand: 38 Maerkte mit je 1,0 summieren sich zu 38, egal aus welchem Bar der
einzelne Wert stammt.

**Die Grenze dieser Schicht, offen benannt:** ein generierter Kandidat, der
seinen ganzen Vektor auf einmal umschichtet, faellt in dieselbe Falle wie
Anlauf 1. Ein solcher Kandidat ist bisher nicht aufgetreten; taucht einer auf,
gehoert die Frage neu entschieden und nicht hier stillschweigend geloest.
"""

from __future__ import annotations

import math
from typing import Any

from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy


def normiere(gewichte: dict[str, float], grenze: float) -> dict[str, float]:
    """Proportional auf `grenze` Brutto skalieren, wenn noetig.

    `nan` heisst "keine Meinung" und zaehlt weder in die Summe noch wird es
    skaliert -- ein Kandidat ohne Aussage soll durch die Schicht keine
    bekommen.
    """
    if grenze <= 0:
        raise ValueError("Bruttogrenze muss positiv sein.")
    brutto = sum(abs(w) for w in gewichte.values() if not math.isnan(w))
    if brutto <= grenze or brutto == 0.0:
        return dict(gewichte)
    faktor = grenze / brutto
    return {
        sym: w if math.isnan(w) else w * faktor for sym, w in gewichte.items()
    }


class BruttoNormiert(Strategy):
    """Huelle, die die Gewichte eines Kandidaten auf das Bruttobudget bringt.

    Sie merkt sich das zuletzt gemeldete Gewicht je Symbol und gibt fuer das
    gerade gefragte Symbol den skalierten Wert zurueck. Die Engine sieht damit
    dieselbe Schnittstelle wie sonst; der Kandidat merkt von der Skalierung
    nichts und kann sie deshalb auch nicht ausnutzen.
    """

    def __init__(
        self,
        inner: Strategy,
        grenze: float = 1.0,
        **params: Any,
    ) -> None:
        super().__init__(inner.symbols, inner.timeframe, **params)
        self.inner = inner
        self.grenze = grenze
        self._roh: dict[str, float] = {}
        # Der Name bleibt der des Kandidaten. Die Huelle ist eine
        # Ausfuehrungsregel und keine andere Hypothese -- ein zweiter Name
        # in Registry und Versuchszaehler waere genau die Doppelzaehlung,
        # die ADR-032 vermeiden soll.
        self.name = inner.name

    @property
    def warmup_bars(self) -> int:
        return self.inner.warmup_bars

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        self._roh[symbol] = float(self.inner.on_bar(symbol, store))
        return normiere(self._roh, self.grenze)[symbol]

    def describe(self) -> str:
        return f"{self.inner.describe()} +brutto<={self.grenze:g}"


def mit_groessenschicht(strategy_cls: type[Strategy], grenze: float = 1.0):
    """Klassenfabrik: `cls(symbols, timeframe)` liefert den umhuellten Kandidaten.

    Der Loop und das Screening reichen ueberall die **Klasse** herum und
    instanziieren selbst. Eine Fabrik mit derselben Signatur passt deshalb
    ohne Aenderung an den Aufrufstellen.
    """

    def bauen(symbols: list[str], timeframe: str, **params: Any) -> Strategy:
        return BruttoNormiert(
            strategy_cls(symbols, timeframe, **params), grenze=grenze
        )

    bauen.name = getattr(strategy_cls, "name", strategy_cls.__name__)
    return bauen
