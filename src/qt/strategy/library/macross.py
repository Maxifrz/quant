"""Gleitender-Durchschnitt-Kreuzung auf Tagesbasis, long oder flach.

**Warum diese Strategie hier steht, obwohl sie die aelteste und langweiligste
Idee der technischen Analyse ist.** Dieses Projekt hat vier Strategien
gebaut und alle vier verloren -- aber alle vier liefen auf 1h- oder
4h-Bars, und alle vier durften short gehen. Beide Entscheidungen sind teuer:

1. **Frequenz ist Kosten.** Bei 90 Basispunkten Round-Trip muss jedes Signal
   ueber 0,9% Bewegung vorhersagen, nur um bei null herauszukommen. Eine
   4h-Strategie mit 900 Trades zahlt das 900-mal. Diese hier handelt ueber
   siebeneinhalb Jahre rund 30-mal.
2. **Short kaempft gegen die Drift.** BTC hat im Datenfenster Faktor 16,8
   gemacht. Wer in diesem Markt die Gegenrichtung handelt, muss dafuer
   bezahlen, dass er gegen den staerksten Effekt im Datensatz steht.

Die Literatur stuetzt beides: Han/Kang/Ryu (2023) finden ueber Krypto hinweg
**starke** Belege fuer Zeitreihen-Momentum und schwache fuer
Querschnitts-Momentum; Grayscale berichtet fuer einen 20/100-Tage-Crossover
auf BTC einen Sharpe von 1,7 gegen 1,3 bei Buy-and-Hold (2012-2023).

**Was die Strategie wirklich tut, und das ist nicht "den Markt schlagen".**
Sie ist rund die Haelfte der Zeit flach. Ihr Beitrag ist nicht ein besseres
Signal, sondern **weniger Teilnahme an Abstuerzen** -- Trendfolge als
Absicherung gegen den linken Rand, nicht als Prognose. Wer sie an der
Gesamtrendite misst, misst das Falsche; die Frage ist, ob dieselbe Rendite
mit weniger Drawdown herauskommt.

**Was daran ehrlich offen ist.** Die Parameter 10/50 stammen aus einem
Vergleich, den ich selbst auf diesen Daten gefahren habe -- das ist Selektion,
und sie faellt der Deflated Sharpe Ratio zur Last (ADR-005). Die Zahl, auf die
es ankommt, steht deshalb nicht in diesem Docstring, sondern kommt aus
`qt wf` und dem DSR-Screening.

Bewusst **zustandslos**: keine Stops, kein Trailing, kein `self._state`. Das
Gewicht ergibt sich allein aus den beiden Durchschnitten des aktuellen
Fensters. Damit gibt es keine Pfadabhaengigkeit, die ein Walk-Forward-Fenster
anders behandeln koennte als der Gesamtlauf -- und nichts, was man versehentlich
ueber eine Fenstergrenze traegt.
"""

from __future__ import annotations

import math

from qt.features import ta
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy
from qt.strategy.registry import register


@register
class MovingAverageCross(Strategy):
    """Long, solange der kurze Durchschnitt ueber dem langen liegt."""

    name = "macross"

    def __init__(
        self,
        symbols: list[str],
        timeframe: str,
        fast: int = 10,
        slow: int = 50,
        allow_short: bool = False,
    ) -> None:
        if fast >= slow:
            raise ValueError(
                f"fast={fast} muss kleiner sein als slow={slow} -- sonst ist die "
                "Kreuzung keine Trendaussage, sondern eine Umkehraussage mit "
                "vertauschten Vorzeichen."
            )
        super().__init__(
            symbols, timeframe, fast=fast, slow=slow, allow_short=allow_short
        )

    @property
    def warmup_bars(self) -> int:
        # Der lange Durchschnitt braucht `slow` Bars, plus einen Puffer, damit
        # das erste Signal nicht auf dem allerersten vollstaendigen Fenster
        # steht.
        return self.params["slow"] + 2

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        window = store.window(symbol, self.timeframe)
        if len(window) < self.warmup_bars:
            return math.nan

        closes = window.closes()
        fast = ta.sma(closes, self.params["fast"])
        slow = ta.sma(closes, self.params["slow"])
        if not math.isfinite(fast) or not math.isfinite(slow):
            return math.nan

        if fast > slow:
            return 1.0
        # Flach statt short, sofern nicht ausdruecklich erlaubt: in einem Markt
        # mit starker Aufwaertsdrift ist die Gegenrichtung kein neutraler
        # Zustand, sondern eine Wette gegen den staerksten Effekt im Datensatz.
        return -1.0 if self.params["allow_short"] else 0.0
