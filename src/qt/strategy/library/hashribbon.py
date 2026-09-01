"""Miner-Kapitulation aus der Hashrate -- das einzige Signal, das nur BTC hat.

**Warum diese Strategie existiert, und was vorher scheiterte.** Der Auftrag
war eine Strategie, die auf BTCs Unterschiede zu anderen Kryptos zugeschnitten
ist. Zwei naheliegende Begruendungen wurden vorher gemessen und beide
widerlegt (ADR-048):

1. "BTC ist berechenbarer" -- ueber 23 gepaarte Walk-Forwards gewinnt BTC 11,
   die mittlere Differenz ist -0,04 Sharpe. Kein Vorteil.
2. "BTC ist der Zufluchtsort bei Krypto-Risk-off" -- der BTC/ETH-Dominanz-
   Filter trennt BTCs Vorwaertsrendite um 4,29 Prozentpunkte, ETHs aber um
   5,52. Waere der Mechanismus BTC-spezifisch, duerfte er ETH nicht helfen.

Beide Male war die Erzaehlung ueberzeugend und die Messung dagegen. Was
uebrig bleibt, ist kein besseres Argument, sondern ein **struktureller**
Unterschied: Ethereum ist seit 2022 Proof-of-Stake. Es gibt keine ETH-Miner,
keine Hashrate, keine Kapitulation. Ein Signal aus der Hashrate ist damit
BTC-spezifisch per Konstruktion und nicht per Behauptung.

**Die Idee.** Miner haben Grenzkosten. Faellt der Preis unter sie, schalten
die teuersten Maschinen ab und die Hashrate sinkt -- sichtbar als kurzes
Mittel unter dem langen. Das ist Kapitulation.

**Die handelbare Behauptung ist eine Unterlassung, keine Prognose:** haltet
BTC nicht, solange die Miner kapitulieren. Die Strategie ist long, wann immer
das kurze Mittel ueber dem langen liegt, und flach sonst. Sie sagt nichts
darueber voraus, wann ein Boden kommt -- sie stellt nur fest, dass die
Kostenbasis der Produzenten gerade bricht.

Der Beitrag ist damit derselbe wie bei `macross`: **weniger Teilnahme an
Abstuerzen**, nicht ein besseres Einstiegssignal. Wer die Strategie an der
Gesamtrendite misst, misst das Falsche; die Frage ist, ob dieselbe Rendite
mit weniger Drawdown herauskommt.

**Zwei Parameter, mehr nicht.** `fast` und `slow`, wie bei `macross`.
`elliott` hat sechs und streut ueber BTCs hoehere Timeframes um 0,99 Sharpe,
waehrend `macross` mit zweien um 0,12 streut (ADR-047). Jeder Freiheitsgrad
ist ein weiterer Blick auf dieselben Daten.

**Long oder flach, nie short** -- dieselbe Entscheidung, die `macross` als
einzige Strategie des Projekts ins Plus gebracht hat (ADR-035). Die
Gegenrichtung kaempft gegen die staerkste Drift im Datensatz.

**Die Point-in-Time-Regel dieser Datei** steht in `on_bar` und folgt
`qt.strategy.library.orderflow`: nachgeschlagen wird ausschliesslich ueber
`window.timestamps`, also ueber Bars, die der Store bereits herausgegeben hat.
Wer stattdessen ueber die Hashrate-Tabelle iterierte und nach Zeitstempeln
"bis jetzt" filterte, haette eine zweite Uhr im System -- und zwei Uhren gehen
auseinander.

Dazu kommt **ein Tag zusaetzlicher Abstand**. Der Wert fuer Tag D ist auf
00:00 UTC gestempelt und beschreibt Tag D; der 1d-Bar fuer D schliesst um
D+1 00:00, der Wert waere also gerade eben zulaessig. Benutzt wird trotzdem
D-1, weil die Hashrate eine **Schaetzung** aus Blockintervallen ist und die
juengsten Tage nachlaufen koennen. Der Abstand kostet einen Tag Reaktionszeit
und ist der Preis dafuer, keine Zahl zu benutzen, die es zum
Entscheidungszeitpunkt in dieser Form noch nicht gab.

**Was hier ehrlich offen ist.** Ob das Signal traegt, entscheidet `qt wf` und
nicht dieser Docstring. Die Kriterien standen vor dem ersten Lauf fest
(ADR-048), einschliesslich des Placebos: dasselbe, aus BTC-Hashrate
abgeleitete Signal auf ETHs Kurs. Wirkt es dort genauso, misst es nicht
BTC-Miner, sondern ein krypto-weites Regime -- ein Befund, aber ein anderer
als der gesuchte.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from qt.data.onchain import load_series
from qt.features import ta
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy
from qt.strategy.registry import register

# Die Reihe, aus der das Signal kommt. Als Konstante und nicht als Parameter:
# eine waehlbare Reihe waere ein weiterer Freiheitsgrad, und die Begruendung
# dieser Strategie haengt genau an dieser einen.
SERIES = "hash-rate"

# Abstand in Bars zwischen dem Wert und seiner Verwendung. Siehe Docstring --
# nicht verhandelbar, deshalb Konstante und kein Parameter.
LAG_BARS = 1


@register
class HashRibbon(Strategy):
    """Flach, solange die Miner kapitulieren -- sonst long."""

    name = "hashribbon"

    def __init__(
        self,
        symbols: list[str],
        timeframe: str,
        fast: int = 30,
        slow: int = 60,
        data_dir: Path | None = None,
        hashrate: dict | None = None,
    ) -> None:
        if fast >= slow:
            raise ValueError(
                f"fast={fast} muss kleiner sein als slow={slow} -- sonst ist die "
                "Kreuzung keine Aussage ueber Kapitulation, sondern dieselbe "
                "Aussage mit vertauschten Vorzeichen."
            )
        super().__init__(symbols, timeframe, fast=fast, slow=slow)

        # Beim Bau geladen, nicht im Bar-Loop. Ein Dateizugriff je Bar waere
        # nicht nur langsam, er waere auch die Stelle, an der sich unbemerkt
        # ein Blick auf spaetere Daten einschleicht (wie in orderflow.py).
        self._hash = load_series(SERIES, data_dir) if hashrate is None else hashrate

    @property
    def warmup_bars(self) -> int:
        # Genug Bars, damit das lange Mittel ueberhaupt definiert ist, plus
        # der Versatz aus LAG_BARS und ein Bar Reserve.
        return self.params["slow"] + LAG_BARS + 2

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        window = store.window(symbol, self.timeframe)
        if len(window) < self.warmup_bars:
            return math.nan

        slow = self.params["slow"]
        fast = self.params["fast"]

        # **Hier sitzt die Point-in-Time-Regel.** Nur Zeitstempel, die der
        # Store schon herausgegeben hat -- und davon die letzten ohne die
        # jeweils juengsten LAG_BARS.
        stamps = window.timestamps[-(slow + LAG_BARS + 1) :]
        erlaubt = stamps[:-LAG_BARS] if LAG_BARS else stamps

        werte = np.array(
            [self._hash.get(ts, math.nan) for ts in erlaubt], dtype=float
        )

        # Eine Luecke in der Reihe ist keine Aussage ueber den Markt. `nan`
        # heisst "keine Meinung" und laesst das bestehende Gewicht stehen;
        # 0.0 hiesse "geh flat" und waere eine Entscheidung, die auf
        # fehlenden Daten beruht.
        if not np.isfinite(werte).all():
            return math.nan

        ma_fast = ta.sma(werte, fast)
        ma_slow = ta.sma(werte, slow)
        if not math.isfinite(ma_fast) or not math.isfinite(ma_slow):
            return math.nan

        # Das ist die ganze Regel: waehrend der Kapitulation flach, sonst
        # long. Bewusst **zustandslos** -- das Gewicht ergibt sich allein aus
        # dem aktuellen Fenster, wie bei `macross`. Damit gibt es keine
        # Pfadabhaengigkeit, die ein Walk-Forward-Fenster anders behandeln
        # koennte als der Gesamtlauf.
        #
        # Ein frueherer Entwurf wollte zusaetzlich den Kreuzungs-Zeitpunkt
        # abfragen ("long erst ab der Erholung"). Das war eine Verzweigung
        # ohne Wirkung: ausserhalb der Kapitulation gilt ohnehin 1.0, beide
        # Zweige gaben dasselbe zurueck. Eine Struktur, die ein Verhalten
        # suggeriert, das sie nicht hat, ist schlechter als keine.
        return 0.0 if ma_fast < ma_slow else 1.0
