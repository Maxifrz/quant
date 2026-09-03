"""Strategien, die Maerkte gegeneinander stellen statt jeden fuer sich.

Alle sieben bisherigen Strategien dieses Projekts sind **Timing**-Strategien:
sie sehen einen Markt an und entscheiden long, flat oder short. Ueber 27
Maerkte laufen sie 27-mal getrennt, und weil die Maerkte zu 0,26 korrelieren,
sind das 3,4 effektive Tests (ADR-055). Die Korrelation ist dabei reiner
Verlust.

Eine **Querschnittsstrategie** stellt die Frage anders: welche Maerkte laufen
gerade besser als die uebrigen? Sie kauft die obere Haelfte und verkauft die
untere, in Summe marktneutral. Was allen gemeinsam ist -- der Krypto-Zyklus,
die Aktienrallye -- faellt heraus. Damit wird dieselbe Korrelation, die 26
Einzeltests entwertet, zu dem, was neutralisiert wird.

**Das ist eine Familie, die dieses Projekt nie getestet hat**, und sie ist erst
seit Phase A ueberhaupt moeglich: vorher lagen nur Krypto-Paare im Store, und
ein Querschnitt aus dreizehn Wetten auf dieselbe Sache ist keiner.

Bauform
-------
Die Engine fragt je Symbol (`on_bar(symbol, store)`), eine Querschnitts-
entscheidung braucht aber alle gleichzeitig. Geloest ohne neuen
Engine-Vertrag: `on_bar` baut das Panel aus dem Store -- der haelt alle
Symbole und wacht selbst ueber den Zeitbezug (`LookaheadError`) -- und gibt
daraus das Gewicht des gefragten Symbols zurueck.

Das Panel wird je Zeitstempel **einmal** berechnet und fuer die uebrigen
Symbole desselben Zeitpunkts wiederverwendet. Ohne das rechnete eine
27-Markt-Strategie jeden Bar 27-mal dasselbe.

**Nur gemeinsame Zeitstempel.** Krypto handelt taeglich, ETFs an Boersentagen.
Ein Querschnitt an einem Sonntag verglich sonst 15 Krypto-Maerkte mit zwoelf
Werten von Freitag. Gefordert sind deshalb `min_namen` Symbole mit einem Bar
zu **genau diesem** Zeitstempel; sonst gibt es kein Signal, und die Engine
behaelt das bestehende Gewicht (`nan`).
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy
from qt.strategy.registry import register

# Unter dieser Zahl handelnder Namen ist eine Rangfolge keine Aussage.
MIN_NAMEN = 8


class CrossSectionalStrategy(Strategy):
    """Basis fuer Strategien, die aus einem Panel eine Rangfolge bilden.

    Unterklassen implementieren `score`: Panel rein, eine Kennzahl je Symbol
    und Zeitpunkt raus. Hoehere Werte heissen "eher long". Aus den Werten der
    **letzten Zeile** macht diese Klasse Gewichte.
    """

    #: Wieviele Bars das Panel tief sein muss, damit `score` rechnen kann.
    lookback: int = 252

    #: Abstand zweier Umschichtungen in Bars. 21 ist der Handelsmonat und die
    #: Frequenz, mit der die Querschnittsliteratur seit Jegadeesh/Titman
    #: rechnet -- keine an diesen Daten gewaehlte Zahl.
    #:
    #: Sie ist nicht kosmetisch: taeglich umgeschichtet schlaegt `crossmom`
    #: ueber 27 Maerkte **79-mal** sein Eigenkapital pro Jahr um, gegen ein
    #: Budget von 7 (ADR-056). Ein Querschnittssignal ohne Umschichtrhythmus
    #: ist von den Kosten erledigt, bevor das Signal etwas sagen darf.
    rebalance_every: int = 21

    def __init__(self, symbols: list[str], timeframe: str, **params: Any) -> None:
        super().__init__(symbols, timeframe, **params)
        self._panel_ts: datetime | None = None
        self._gewichte: dict[str, float] = {}
        self._letzte_umschichtung: datetime | None = None
        self._bars_seit_umschichtung = 0

    @property
    def warmup_bars(self) -> int:
        return self.lookback + 1

    @abstractmethod
    def score(self, panel: pd.DataFrame) -> pd.Series:
        """Kennzahl je Symbol fuer den **letzten** Zeitpunkt des Panels.

        `panel` hat Zeitstempel als Index und Symbole als Spalten und enthaelt
        nur Schlusskurse bis einschliesslich jetzt. Rueckgabe: eine Reihe ueber
        die Spalten; `nan` heisst "fuer dieses Symbol keine Aussage".
        """

    # -- Vom Score zu Gewichten ---------------------------------------------

    def gewichte_aus_score(self, werte: pd.Series) -> dict[str, float]:
        """Obere Haelfte long, untere short, Bruttoexposure 1.

        Zentriert auf den Median statt auf den Mittelwert: ein einzelner
        Ausreisser -- und in Krypto ist jede Woche einer dabei -- verschoebe
        sonst die gesamte Nulllinie und damit jedes Gewicht.
        """
        gueltig = werte.dropna()
        if len(gueltig) < MIN_NAMEN:
            return {}

        raenge = gueltig.rank(pct=True)
        roh = raenge - raenge.median()
        brutto = roh.abs().sum()
        if brutto <= 0:
            return {}
        return (roh / brutto).to_dict()

    # -- Engine-Schnittstelle -----------------------------------------------

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        jetzt = store.now
        if self._panel_ts != jetzt:
            self._panel_ts = jetzt
            # Der Zaehler laeuft ueber **Zeitpunkte**, nicht ueber Aufrufe:
            # bei 27 Maerkten kaeme `on_bar` sonst 27-mal je Bar und der
            # Monat waere nach anderthalb Tagen um.
            self._bars_seit_umschichtung += 1
            faellig = (
                self._letzte_umschichtung is None
                or self._bars_seit_umschichtung >= self.rebalance_every
            )
            if faellig:
                panel = self._panel(store, jetzt)
                if panel is not None:
                    self._gewichte = self.gewichte_aus_score(self.score(panel))
                    self._letzte_umschichtung = jetzt
                    self._bars_seit_umschichtung = 0

        if not self._gewichte:
            # Kein Querschnitt zustande gekommen: keine Meinung, nicht flat.
            return float("nan")
        return float(self._gewichte.get(symbol, 0.0))

    def _panel(self, store: FeatureStore, jetzt: datetime) -> pd.DataFrame | None:
        """Schlusskurse bis **einschliesslich des letzten gemeinsamen** Bars.

        Der entscheidende Punkt, und er hat einen Lauf gekostet: die Engine
        arbeitet die Bars eines Zeitpunkts **nacheinander** ab, Symbol fuer
        Symbol. Fragt sie das erste Symbol zur Zeit T, haben die uebrigen
        ihren T-Bar noch nicht im Store. Eine Bedingung "alle muessen einen
        Bar bei T haben" ist damit fuer alle ausser dem zuletzt bearbeiteten
        Symbol unerfuellbar -- der erste Entwurf lieferte deshalb ueber die
        ganze Historie **null** Ausfuehrungen.

        Gerechnet wird darum auf dem letzten Zeitpunkt **vor** `jetzt`, den
        genug Symbole gemeinsam haben. Der ist vollstaendig, weil die Engine
        ihn abgeschlossen hat, bevor der erste Bar von `jetzt` ankam. Der
        Preis ist ein Bar Verzoegerung; das ist die konservative Richtung,
        und sie kostet weniger als ein Signal, das von der Reihenfolge im
        Ereignisstrom abhaengt.

        Der gemeinsame Zeitpunkt loest zugleich das Kalenderproblem: Krypto
        handelt sonntags, ETFs nicht. Ohne diese Bedingung verglich der
        Querschnitt Sonntagskurse mit denen von Freitag.
        """
        reihen: dict[str, pd.Series] = {}
        for sym in self.symbols:
            fenster = store.window(sym, self.timeframe)
            if len(fenster) < self.lookback + 2:
                continue
            stempel = pd.DatetimeIndex(fenster.timestamps)
            reihe = pd.Series(fenster.closes(), index=stempel)
            reihe = reihe[reihe.index < jetzt]
            if len(reihe) >= self.lookback + 1:
                reihen[sym] = reihe

        if len(reihen) < MIN_NAMEN:
            return None

        # Der juengste Zeitpunkt, den mindestens MIN_NAMEN Symbole teilen.
        letzte = pd.Series([r.index[-1] for r in reihen.values()])
        haeufig = letzte.value_counts()
        passend = haeufig[haeufig >= MIN_NAMEN]
        if passend.empty:
            return None
        stichtag = max(passend.index)

        spalten = {
            sym: reihe for sym, reihe in reihen.items() if reihe.index[-1] == stichtag
        }
        if len(spalten) < MIN_NAMEN:
            return None
        panel = pd.DataFrame(spalten).sort_index()
        return panel if len(panel) >= self.lookback + 1 else None


@register
class CrossMomentum(CrossSectionalStrategy):
    """12-1-Momentum im Querschnitt: kaufe die Starken, verkaufe die Schwachen.

    Die Parameter sind **nicht** an diesen Daten gewaehlt, und das ist der
    Punkt. 252 Tage Rueckschau ohne den juengsten Monat ist die Fassung aus
    Jegadeesh/Titman (1993) und seither in hunderten Papieren dieselbe. Ein
    Kandidat mit vorab feststehenden Parametern kostet genau einen Versuch --
    einer, dessen Fenster man sucht, kostet so viele, wie man probiert hat
    (ADR-032).

    Erwartungshaltung, vorher notiert: gemessen ueber die 27 Maerkte dieses
    Stores liegt der Rank IC bei +0,037 mit korrigiertem t = 1,42, also
    **nicht signifikant** (ADR-058). Das hier ist kein Kandidat, sondern das
    Arbeitsbeispiel, an dem die Familie ueberhaupt lauffaehig wird.
    """

    name = "crossmom"

    def __init__(
        self,
        symbols: list[str],
        timeframe: str,
        lookback: int = 252,
        skip: int = 21,
        **params: Any,
    ) -> None:
        if lookback <= skip:
            raise ValueError(f"lookback={lookback} muss groesser als skip={skip} sein.")
        super().__init__(symbols, timeframe, lookback=lookback, skip=skip, **params)
        self.lookback = lookback
        self.skip = skip

    def score(self, panel: pd.DataFrame) -> pd.Series:
        spaet = panel.iloc[-1 - self.skip]
        frueh = panel.iloc[-1 - self.lookback]
        return spaet / frueh.where(frueh > 0) - 1.0


@register
class CrossReversal(CrossSectionalStrategy):
    """Kurzfrist-Umkehr: kaufe, was zuletzt am schlechtesten lief.

    Das Gegenstueck zu `CrossMomentum` und aus derselben Literatur (Lehmann
    1990, Lo/MacKinlay 1990). Steht hier, weil eine Familie mit genau einem
    Mitglied nicht pruefbar ist: erst zwei Strategien mit **entgegengesetztem**
    Vorzeichen zeigen, ob die Mechanik das Vorzeichen ueberhaupt durchreicht.
    """

    name = "crossrev"

    def __init__(
        self, symbols: list[str], timeframe: str, lookback: int = 21, **params: Any
    ) -> None:
        super().__init__(symbols, timeframe, lookback=lookback, **params)
        self.lookback = lookback

    def score(self, panel: pd.DataFrame) -> pd.Series:
        frueh = panel.iloc[-1 - self.lookback]
        rendite = panel.iloc[-1] / frueh.where(frueh > 0) - 1.0
        return -rendite


def panel_from_store(
    symbols: list[str], timeframe: str, feld: str = "close"
) -> pd.DataFrame:
    """Panel direkt aus dem Parquet-Store, fuer Analysen ausserhalb der Engine.

    Ausdruecklich **nicht** fuer Strategien: hier gibt es keine Uhr und damit
    keinen Lookahead-Schutz. Wer das in `on_bar` benutzt, umgeht den
    FeatureStore und damit die einzige Zusage, auf der alles andere steht.
    """
    from qt.data.store import read_bars

    spalten: dict[str, pd.Series] = {}
    for sym in symbols:
        try:
            df = read_bars(sym, timeframe)
        except FileNotFoundError:
            continue
        if feld not in df.columns:
            continue
        spalten[sym] = df.set_index("ts")[feld]
    if not spalten:
        return pd.DataFrame()
    return pd.DataFrame(spalten).sort_index()


def score_panel(strategie: CrossSectionalStrategy, panel: pd.DataFrame) -> pd.DataFrame:
    """`score` ueber jeden Zeitpunkt des Panels -- die Signalmatrix fuer den IC.

    Rollt bewusst Zeile fuer Zeile und uebergibt jeweils nur die Historie bis
    dahin. Eine vektorisierte Fassung waere schneller und wuerde die Frage
    offenlassen, ob sie irgendwo nach vorn schaut.
    """
    tief = strategie.lookback + 1
    if len(panel) < tief:
        return pd.DataFrame(columns=panel.columns)

    zeilen: dict[Any, pd.Series] = {}
    for i in range(tief, len(panel) + 1):
        ausschnitt = panel.iloc[:i]
        werte = strategie.score(ausschnitt)
        if isinstance(werte, pd.Series) and np.isfinite(werte.to_numpy(dtype=float)).any():
            zeilen[ausschnitt.index[-1]] = werte
    if not zeilen:
        return pd.DataFrame(columns=panel.columns)
    return pd.DataFrame(zeilen).T.reindex(columns=panel.columns)
