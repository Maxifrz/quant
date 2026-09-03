"""Negativkontrollen -- schlaegt eine Strategie ihre eigene gewuerfelte Fassung?

Dieses Projekt hat sechs Hypothesen geprueft und sechs verworfen, und in drei
Faellen war es **diese** Art Test, die entschieden: `hashribbon` fiel, weil
eine von fuenf permutierten Hashraten die echte Reihe schlug (ADR-048); das
ML-Modell fiel an vertauschten Labels (ADR-050); `elliott` bekam denselben
Placebo nachtraeglich (ADR-033).

`macross` -- die einzige ueberlebende Strategie, die seit ADR-037 auf zwei
Paper-Konten laeuft -- hatte nie eine. Sie hat ein Parameterfeld und eine
Replikation auf ETH, aber beides beantwortet die eigentliche Frage nicht:

    Die Strategie ist 54% der Zeit long in einem Markt, der Faktor 16 gemacht
    hat. Traegt das **Timing** etwas bei, oder misst der Sharpe nur "viel long
    im Bullenmarkt"?

Buy-and-Hold beantwortet das nicht, denn das ist 100% Zeit im Markt. Der
richtige Vergleich ist ein Signal mit **derselben** Zeit im Markt, derselben
Zahl an Episoden und derselben Gebuehrenlast -- nur zufaellig platziert.

**Wie die Kontrolle gebaut ist.** Der Gewichtsverlauf der echten Strategie
wird in Laeufe zerlegt (maximale Bloecke konstanten Gewichts). Innerhalb jeder
Gewichtsklasse werden die *Laengen* der Laeufe untereinander getauscht. Damit
bleiben exakt erhalten:

    Zahl der Episoden      -> gleiche Trade-Zahl, gleiche Gebuehren
    Zeit im Markt          -> gleiche Marktexposition
    Abwechslung flach/long -> gleiche Struktur

Zufaellig wird ausschliesslich, **wann** die Episoden liegen. Genau das ist
die Groesse, die eine Strategie behauptet zu koennen.

Eine naheliegende Alternative waere, die Kursreihe zu permutieren statt des
Signals. Fuer `hashribbon` war das richtig -- dort ist die Hashrate die
Signalquelle und der Kurs bleibt unangetastet. Bei `macross` ist der Kurs
*selbst* die Signalquelle; eine permutierte Kursreihe hat keinen Trend mehr,
und ein Trendfolger verliert dort per Konstruktion. Dieser Test waere nicht
streng, sondern nur bequem.

**Die Kalibrierprobe ist Teil der Kontrolle, nicht Beiwerk.** Der Abspieler
mit den *echten* Gewichten muss dieselbe Kennzahl liefern wie die Strategie
selbst. Tut er das nicht, misst der Vergleich zwei verschiedene Dinge und das
Ergebnis ist wertlos -- deshalb rechnet `permutation_control` sie mit und
weist die Abweichung aus, statt sie zu unterstellen.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from qt.backtest.walkforward import InsufficientDataError, walk_forward
from qt.core.clock import BacktestClock
from qt.core.config import BacktestConfig
from qt.core.events import merge_bar_streams
from qt.core.types import Bar
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy, clip_weight
from qt.strategy.cross_sectional import CrossSectionalStrategy

__all__ = [
    "CrossMarketResult",
    "MarketRun",
    "PermutationResult",
    "PlaybackStrategy",
    "correlation_matrix",
    "cross_market_control",
    "permutation_control",
    "shuffle_cross_section",
    "shuffle_episodes",
    "signal_series",
]

# Schluessel des Abspielers: Symbol plus **Open-Zeit** des Bars, genau wie
# `window.timestamps` sie herausgibt.
SignalMap = dict[tuple[str, datetime], float]


class PlaybackStrategy(Strategy):
    """Spielt einen vorgegebenen Gewichtsverlauf ab, nach Zeitstempel.

    Point-in-Time-sicher aus demselben Grund wie die Hashrate-Tabelle in
    `qt.strategy.library.hashribbon`: nachgeschlagen wird ausschliesslich ueber
    `window.timestamps`, also ueber Bars, die der Store bereits herausgegeben
    hat. Die Tabelle darf Eintraege fuer die Zukunft enthalten -- sie werden nie
    gelesen, und ein Test haelt das fest.

    `warmup_bars` wird von aussen gesetzt und nicht aus der Tabelle abgeleitet:
    die Fenstergeometrie des Walk-Forward haengt daran, und ein Vergleich
    zwischen zwei Strategien mit verschiedenem Warmup vergleicht verschiedene
    Zeitraeume.
    """

    name = "playback"

    def __init__(
        self,
        symbols: list[str],
        timeframe: str,
        signals: SignalMap | None = None,
        warmup_bars: int = 2,
        label: str = "playback",
    ) -> None:
        super().__init__(symbols, timeframe, label=label)
        self._signals: SignalMap = dict(signals or {})
        self._warmup = int(warmup_bars)
        if self._warmup < 1:
            raise ValueError(f"warmup_bars muss mindestens 1 sein, ist {warmup_bars}.")

    @property
    def warmup_bars(self) -> int:
        return self._warmup

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        window = store.window(symbol, self.timeframe)
        if len(window) < self._warmup:
            return math.nan
        # Nur der Zeitstempel, den der Store gerade herausgegeben hat.
        return self._signals.get((symbol, window.timestamps[-1]), math.nan)


def signal_series(
    make_strategy: Callable[[], Strategy], bars: dict[str, list[Bar]]
) -> SignalMap:
    """Den Gewichtsverlauf einer Strategie ueber die ganze Historie aufzeichnen.

    Bildet Schritt 3 und 4 aus `qt.backtest.engine.run_backtest` nach -- Bar in
    den Store, dann entscheiden -- und **nichts** darueber hinaus: kein Broker,
    keine Orders, keine Kosten. Aufgezeichnet wird die Meinung, nicht ihr
    Ergebnis.

    Bewusst nicht aus `BacktestResult.equity` gelesen: deren Zeilen tragen die
    **Close**-Zeit des Bars, der Abspieler braucht die **Open**-Zeit. Ein
    Off-by-one an dieser Stelle waere ein Bar Lookahead in jeder Ziehung.
    """
    strategy = make_strategy()
    events = merge_bar_streams(list(bars.values()))
    if not events:
        return {}

    clock = BacktestClock(events[0].ts)
    store = FeatureStore(clock, maxlen=max(1000, strategy.warmup_bars * 3))
    seen: dict[str, int] = dict.fromkeys(bars, 0)
    out: SignalMap = {}

    for event in events:
        bar = event.bar
        clock.advance(event.ts)
        store.on_bar(bar)
        seen[bar.symbol] += 1
        if seen[bar.symbol] < strategy.warmup_bars:
            continue
        out[(bar.symbol, bar.ts)] = clip_weight(strategy.on_bar(bar.symbol, store))
    return out


# ---------------------------------------------------------------------------
# Die Permutation
# ---------------------------------------------------------------------------


def _runs(values: list[float]) -> list[list]:
    """Maximale Bloecke konstanten Gewichts als [Schluessel, Laenge].

    `nan` bekommt einen eigenen Schluessel: es heisst "keine Meinung" und ist
    damit ein eigener Zustand, nicht ein Gewicht wie jedes andere. Ueber
    `nan != nan` liesse es sich sonst nie gruppieren.
    """
    runs: list[list] = []
    for value in values:
        key = "nan" if value != value else float(value)
        if runs and runs[-1][0] == key:
            runs[-1][1] += 1
        else:
            runs.append([key, 1])
    return runs


def shuffle_episodes(values: list[float], rng: np.random.Generator) -> list[float]:
    """Episodenlaengen innerhalb jeder Gewichtsklasse tauschen.

    Erhalten bleiben Zahl der Episoden, Zeit je Gewicht, Abwechslungsmuster und
    damit Trade-Zahl und Gebuehren. Zufaellig wird nur die **Lage** in der Zeit.

    Getauscht wird ausdruecklich *innerhalb* einer Gewichtsklasse und nicht
    ueber alle Laeufe hinweg: sonst koennten zwei Long-Bloecke nebeneinander zu
    liegen kommen, wuerden zu einem verschmelzen, und die gewuerfelte Fassung
    haette weniger Trades als das Original. Sie waere dann guenstiger und der
    Vergleich unfair -- zugunsten der Kontrolle, also gegen die Strategie.
    """
    runs = _runs(list(values))
    nach_klasse: dict[object, list[int]] = {}
    for i, (key, _n) in enumerate(runs):
        nach_klasse.setdefault(key, []).append(i)

    getauscht = [list(run) for run in runs]
    for stellen in nach_klasse.values():
        laengen = [runs[i][1] for i in stellen]
        rng.shuffle(laengen)
        for i, laenge in zip(stellen, laengen, strict=True):
            getauscht[i][1] = laenge

    out: list[float] = []
    for key, laenge in getauscht:
        wert = math.nan if key == "nan" else float(key)
        out.extend([wert] * laenge)
    return out


# ---------------------------------------------------------------------------
# Die Permutation im Querschnitt
# ---------------------------------------------------------------------------
#
# Fuer eine Timing-Strategie ist die Behauptung "ich weiss **wann**", und die
# Kontrolle wuerfelt entsprechend die Lage der Episoden. Eine Querschnitts-
# strategie behauptet etwas anderes: "ich weiss **welcher Markt**". Ihre
# Kontrolle muss deshalb die Zuordnung wuerfeln, nicht die Zeit.
#
# Die Episoden-Permutation auf einen Querschnitt loszulassen waere kein
# strengerer, sondern ein **kaputter** Test: sie tauscht je Symbol getrennt
# und zerstoert damit genau die Eigenschaft, die eine Querschnittsstrategie
# ausmacht -- dass die Gewichte eines Zeitpunkts sich zu null summieren. Die
# gewuerfelte Fassung haette eine Nettoposition, die die echte nie hat, und
# der Vergleich liefe gegen eine andere Wette.


def _nach_zeit(signals: SignalMap) -> dict[datetime, dict[str, float]]:
    karte: dict[datetime, dict[str, float]] = {}
    for (symbol, ts), wert in signals.items():
        karte.setdefault(ts, {})[symbol] = wert
    return karte


def shuffle_cross_section(signals: SignalMap, rng: np.random.Generator) -> SignalMap:
    """Die Maerkte umbenennen: **eine** Permutation fuer die ganze Historie.

    Der ganze Gewichtsverlauf von Markt A geht an Markt `pi(A)`. Erhalten
    bleibt damit alles, was den Preis des Handelns ausmacht -- jede
    Gewichtsaenderung, ihre Groesse, ihr Zeitpunkt, und je Zeitpunkt Brutto
    wie Netto. Zufaellig wird ausschliesslich, **welcher** Markt gemeint ist.
    Genau das ist die Behauptung einer Querschnittsstrategie.

    **Die naheliegende Variante ist gemessen und verworfen.** Zuerst stand
    hier eine Auslosung je Halteblock: gleiche Zahl der Bloecke, aber jeder
    Monat eine neue Zuordnung. Sie sieht strenger aus und ist unbrauchbar --
    `crossmom` handelte echt 352-mal bei 2,4 Mio. Umsatz, die Ziehungen
    3.452-mal bei 29,1 Mio. Der Grund steckt im Rebalancing-Band (ADR-008):
    die Rangfolge einer Querschnittsstrategie wandert von Monat zu Monat
    langsam, die meisten Gewichtsaenderungen bleiben unter dem Band und
    kosten nichts. Eine frei ausgeloste Zuordnung springt dagegen jedes Mal
    ueber das Band. Die Kontrolle verlor damit an den Gebuehren statt an der
    Information -- und `crossmom` haette mit Perzentil 100 % als erste
    Strategie dieses Projekts eine Negativkontrolle bestanden, ohne dass das
    Signal etwas dazu beigetragen haette (ADR-059).

    Der Preis dieser Fassung ist Trennschaerfe: eine Ziehung ist **eine**
    Auslosung, nicht sechzig. Die Verteilung ist entsprechend breit. Das ist
    dieselbe Grenze wie in ADR-054 -- ein Perzentil unter 95 % heisst "nicht
    gezeigt", nicht "gezeigt, dass nichts da ist".
    """
    alle = sorted({sym for sym, _ in signals})
    ziel = [alle[i] for i in rng.permutation(len(alle))]
    zuordnung = dict(zip(alle, ziel, strict=True))
    return {(zuordnung[sym], ts): wert for (sym, ts), wert in signals.items()}


def _shuffled_map(signals: SignalMap, rng: np.random.Generator) -> SignalMap:
    """Je Symbol die Episoden tauschen, Zeitachse unveraendert lassen."""
    out: SignalMap = {}
    symbole = sorted({sym for sym, _ in signals})
    for symbol in symbole:
        stempel = sorted(ts for sym, ts in signals if sym == symbol)
        werte = [signals[(symbol, ts)] for ts in stempel]
        for ts, wert in zip(stempel, shuffle_episodes(werte, rng), strict=True):
            out[(symbol, ts)] = wert
    return out


@dataclass(slots=True)
class PermutationResult:
    """Wo liegt die echte Strategie in der Verteilung ihrer gewuerfelten Fassungen?

    Verglichen wird gegen `kalibriert` und nicht gegen `echt`, und der
    Unterschied ist nicht kosmetisch. `kalibriert` ist der Abspieler mit den
    **echten** Gewichten -- dieselbe Bauform wie jede Ziehung, nur ohne
    Wuerfel. Nur diese beiden Groessen sind konstruktionsgleich.

    Fuer eine zustandslose Strategie fallen beide Zahlen zusammen (`macross`:
    Abstand 0,0000, ADR-054), und die Wahl ist folgenlos. Fuer eine
    pfadabhaengige nicht: der Walk-Forward setzt die Strategie in jedem
    Fenster neu auf, der Abspieler laeuft durch. Wer dort `echt` gegen die
    Ziehungen haelt, vergleicht zwei Groessen, die sich schon ohne jeden
    Wuerfel unterscheiden.
    """

    echt: float
    kalibriert: float
    ziehungen: list[float] = field(default_factory=list)
    kennzahl: str = "Sharpe"
    n_windows: int = 0
    #: Trades und Umsatz des Abspielers mit echten Gewichten.
    reibung_echt: tuple[int, float] = (0, 0.0)
    #: Dieselben zwei Zahlen je Ziehung -- die Probe, ob die Kontrolle
    #: dieselbe Reibung zahlt wie das Original (ADR-054).
    reibung_ziehungen: list[tuple[int, float]] = field(default_factory=list)

    @property
    def perzentil(self) -> float:
        """Anteil der Ziehungen, die der Abspieler mit echten Gewichten schlaegt."""
        if not self.ziehungen:
            return float("nan")
        return float(np.mean([z < self.kalibriert for z in self.ziehungen]))

    @property
    def pfadabhaengigkeit(self) -> float:
        """Abstand zwischen Strategie und Abspieler mit echten Gewichten.

        Zustandslose Strategie: muss null sein, jede Abweichung ist ein
        Kalibrierfehler und macht das Ergebnis wertlos. Pfadabhaengige
        Strategie: das ist der Preis der Fensterschnitte des Walk-Forward --
        eine Eigenschaft der Strategie, kein Fehler der Kontrolle.
        """
        return abs(self.echt - self.kalibriert)

    def bestanden(self, schwelle: float = 0.95) -> bool:
        return bool(self.perzentil >= schwelle)

    def _reibungszeilen(self) -> list[str]:
        if not self.reibung_ziehungen:
            return []
        trades = np.array([t for t, _ in self.reibung_ziehungen], dtype=float)
        umsatz = np.array([u for _, u in self.reibung_ziehungen], dtype=float)
        et, eu = self.reibung_echt
        return [
            "",
            "  Zahlt die Kontrolle dieselbe Reibung? (ADR-054)",
            f"    Trades   echt {et:>10,}   Ziehungen Median {np.median(trades):>10,.0f}"
            f"  ({trades.min():.0f}-{trades.max():.0f})",
            f"    Umsatz   echt {eu:>10,.0f}   Ziehungen Median {np.median(umsatz):>10,.0f}",
        ]

    def table(self) -> str:
        z = np.asarray(self.ziehungen, dtype=float)
        besser = int((z >= self.kalibriert).sum())
        zeilen = [
            f"  Kennzahl              {self.kennzahl} ueber {self.n_windows} OOS-Fenster",
            f"  Strategie selbst      {self.echt:+.3f}",
            f"  Abspieler (Referenz)  {self.kalibriert:+.3f}  "
            f"Pfadabhaengigkeit {self.pfadabhaengigkeit:.4f}",
            "",
            f"  {len(z)} Ziehungen:",
            f"    Median   {np.median(z):+.3f}",
            f"    p05/p95  {np.quantile(z, 0.05):+.3f} / {np.quantile(z, 0.95):+.3f}",
            f"    Maximum  {z.max():+.3f}",
        ]
        zeilen += self._reibungszeilen()
        zeilen += [
            "",
            f"  Ziehungen mindestens so gut wie die echte: {besser} von {len(z)}",
            f"  Perzentil der echten Strategie: {self.perzentil:.1%}",
        ]
        return "\n".join(zeilen)


def permutation_control(
    make_strategy: Callable[[], Strategy],
    bars: dict[str, list[Bar]],
    train_bars: int,
    test_bars: int,
    embargo_bars: int = 0,
    draws: int = 200,
    seed: int = 0,
    cfg: BacktestConfig | None = None,
    on_draw: Callable[[int, float], None] | None = None,
    quer: bool | None = None,
) -> PermutationResult:
    """Die Strategie gegen `draws` gewuerfelte Fassungen ihrer selbst.

    Ablauf, und die Reihenfolge ist Absicht:

    1. Echten Gewichtsverlauf ueber die ganze Historie aufzeichnen.
    2. **Referenz:** denselben Verlauf durch den Abspieler schicken. Diese
       Zahl, nicht die der Strategie, ist der Vergleichspunkt -- sie ist die
       einzige, die mit den Ziehungen konstruktionsgleich ist. Der Abstand zur
       Strategie steht als `pfadabhaengigkeit` daneben.
    3. `draws` Ziehungen.

    `quer` waehlt, **was** gewuerfelt wird: die Lage der Episoden in der Zeit
    (Timing-Strategie) oder die Zuordnung der Gewichte zu Maerkten
    (Querschnittsstrategie). Ohne Angabe entscheidet der Typ der Strategie --
    die falsche Kontrolle ist kein schwaecherer Test, sondern ein anderer.

    Der Warmup des Abspielers wird auf den der Strategie gesetzt, damit alle
    Laeufe **dieselbe** Fenstergeometrie sehen.
    """
    if draws < 1:
        raise ValueError(f"draws muss mindestens 1 sein, ist {draws}.")

    probe = make_strategy()
    warmup = probe.warmup_bars
    timeframe = probe.timeframe
    symbols = list(probe.symbols)
    if quer is None:
        quer = isinstance(probe, CrossSectionalStrategy)

    echt = walk_forward(
        make_strategy, bars, train_bars=train_bars, test_bars=test_bars,
        embargo_bars=embargo_bars, cfg=cfg,
    )

    signale = signal_series(make_strategy, bars)
    if not signale:
        raise InsufficientDataError(
            "Die Strategie hat ueber die ganze Historie kein einziges Signal "
            "geliefert -- es gibt nichts zu permutieren."
        )
    if not any(wert == wert and wert != 0.0 for wert in signale.values()):
        # Ohne diese Zeile beantwortet die Kontrolle eine Frage, die niemand
        # gestellt hat: null gegen null ist Perzentil 0 %, und das las sich
        # bis ADR-059 wie ein Durchfallen. Eine Querschnittsstrategie auf
        # einem einzelnen Markt landet genau hier.
        raise InsufficientDataError(
            "Die Strategie war ueber die ganze Historie flach oder ohne "
            "Meinung -- es gibt nichts zu permutieren. Bei einer "
            "Querschnittsstrategie heisst das meist: zu wenige Maerkte "
            "uebergeben."
        )

    def _lauf(karte: SignalMap):
        return walk_forward(
            lambda: PlaybackStrategy(
                symbols, timeframe, signals=karte, warmup_bars=warmup
            ),
            bars, train_bars=train_bars, test_bars=test_bars,
            embargo_bars=embargo_bars, cfg=cfg,
        )

    referenz = _lauf(signale)
    if referenz.metrics.n_trades == 0:
        raise InsufficientDataError(
            "Der Abspieler hat out-of-sample keinen einzigen Trade gemacht -- "
            "ein Vergleich gegen Ziehungen waere null gegen null."
        )

    mischen = shuffle_cross_section if quer else _shuffled_map
    rng = np.random.default_rng(seed)
    ziehungen: list[float] = []
    reibung: list[tuple[int, float]] = []
    for i in range(draws):
        ergebnis = _lauf(mischen(signale, rng))
        wert = float(ergebnis.metrics.sharpe)
        ziehungen.append(wert)
        reibung.append((int(ergebnis.metrics.n_trades), float(ergebnis.metrics.turnover)))
        if on_draw is not None:
            on_draw(i + 1, wert)

    return PermutationResult(
        echt=float(echt.metrics.sharpe),
        kalibriert=float(referenz.metrics.sharpe),
        ziehungen=ziehungen,
        n_windows=echt.n_windows,
        reibung_echt=(
            int(referenz.metrics.n_trades),
            float(referenz.metrics.turnover),
        ),
        reibung_ziehungen=reibung,
    )


# ---------------------------------------------------------------------------
# Der Querschnitt
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MarketRun:
    """Ergebnis eines Marktes im Querschnitt."""

    symbol: str
    sharpe: float = float("nan")
    total_return: float = float("nan")
    n_windows: int = 0
    n_bars: int = 0
    grund: str = ""

    @property
    def testbar(self) -> bool:
        return not self.grund


@dataclass(slots=True)
class CrossMarketResult:
    """Dieselbe Strategie, unveraendert, auf vielen Maerkten."""

    laeufe: list[MarketRun] = field(default_factory=list)
    mittlere_korrelation: float = float("nan")
    korrelationsmatrix: np.ndarray | None = None

    @property
    def getestet(self) -> list[MarketRun]:
        return [lauf for lauf in self.laeufe if lauf.testbar]

    @property
    def median_sharpe(self) -> float:
        werte = [lauf.sharpe for lauf in self.getestet if np.isfinite(lauf.sharpe)]
        return float(np.median(werte)) if werte else float("nan")

    @property
    def anteil_positiv(self) -> float:
        werte = [lauf.sharpe for lauf in self.getestet if np.isfinite(lauf.sharpe)]
        return float(np.mean([w > 0 for w in werte])) if werte else float("nan")

    @property
    def effektive_maerkte(self) -> float:
        """Effektive Stichprobengroesse ueber die Maerkte (ADR-052).

        `n / (1 + (n-1) * rho)` sieht nach einer Naeherung fuer gleich
        korrelierte Reihen aus. Sie ist aber **exakt**, und zwar fuer jede
        Korrelationsstruktur: die Varianz eines gleichgewichteten Mittels ist
        `(1/n^2) * 1'C1`, und mit `1'C1 = n + n(n-1)*rho_quer` faellt die
        Formel unmittelbar heraus. Nachgerechnet an den echten 26 Maerkten:
        3,2412 gegen 3,2412, Differenz 4e-16 (ADR-055).

        Das ist festgehalten, weil ich das Gegenteil vermutet habe, nachdem
        diese Zahl ein vorab registriertes Kriterium verfehlte -- und die
        Vermutung war falsch. Wer hier eine "bessere" Formel sucht, sucht
        vermutlich aus demselben Grund.
        """
        n = len(self.getestet)
        rho = self.mittlere_korrelation
        if n < 2 or not np.isfinite(rho):
            return float(n)
        nenner = 1.0 + (n - 1) * max(rho, 0.0)
        return float(n / nenner) if nenner > 0 else float(n)

    @property
    def unabhaengige_richtungen(self) -> float:
        """Zahl der unabhaengigen *Richtungen* in den Daten -- nicht n_eff.

        Teilnahmequote der Eigenwerte, `(Summe lambda)^2 / Summe lambda^2`.
        Sie beantwortet eine andere Frage als `effektive_maerkte`: wieviele
        voneinander unabhaengige Faktoren die Maerkte aufspannen, nicht
        wieviele unabhaengige Beobachtungen ein Mittel ueber sie wert ist.

        **Kein Ersatz und keine Grundlage fuer Kriterien.** Sie liegt fuer die
        26 Maerkte bei 4,8 gegen 3,2 -- und genau dieser Abstand macht sie
        verlockend, sobald 3,2 eine Schwelle verfehlt. Sie steht hier als
        Beschreibung der Struktur, nicht als Messlatte (ADR-055).
        """
        if self.korrelationsmatrix is None or len(self.korrelationsmatrix) < 2:
            return float(len(self.getestet))
        ew = np.linalg.eigvalsh(self.korrelationsmatrix)
        nenner = float((ew ** 2).sum())
        return float(ew.sum() ** 2 / nenner) if nenner > 0 else float(len(ew))

    def table(self) -> str:
        zeilen = [
            f"  {'Markt':<12}{'Sharpe':>9}{'Rendite':>11}{'Fenster':>9}{'Bars':>8}",
            "  " + "-" * 47,
        ]
        for lauf in sorted(
            self.laeufe, key=lambda x: (not x.testbar, -_sortierbar(x.sharpe))
        ):
            if not lauf.testbar:
                zeilen.append(f"  {lauf.symbol:<12}{'--':>9}   {lauf.grund}")
                continue
            zeilen.append(
                f"  {lauf.symbol:<12}{lauf.sharpe:>9.2f}"
                f"{lauf.total_return:>10.1%}{lauf.n_windows:>9}{lauf.n_bars:>8,}"
            )
        zeilen += [
            "",
            f"  Median-Sharpe ueber {len(self.getestet)} Maerkte: "
            f"{self.median_sharpe:+.2f}",
            f"  davon positiv: {self.anteil_positiv:.0%}",
            f"  mittlere paarweise Korrelation der Maerkte: "
            f"{self.mittlere_korrelation:.2f}",
            f"  -> effektiv {self.effektive_maerkte:.1f} unabhaengige Maerkte "
            f"(ADR-052), nicht {len(self.getestet)}",
            f"     nachrichtlich: {self.unabhaengige_richtungen:.1f} "
            f"unabhaengige Richtungen -- eine andere Frage, keine Messlatte",
        ]
        return "\n".join(zeilen)


def _sortierbar(wert: float) -> float:
    return wert if np.isfinite(wert) else -1e9


def cross_market_control(
    strategy_cls,
    markets: dict[str, list[Bar]],
    timeframe: str,
    train_bars: int,
    test_bars: int,
    embargo_bars: int = 0,
    cfg: BacktestConfig | None = None,
    on_market: Callable[[MarketRun], None] | None = None,
) -> CrossMarketResult:
    """Dieselbe Strategie, **unveraendert**, auf jedem Markt einzeln.

    Kein Parameter wird je Markt neu gewaehlt -- genau das waere die Selektion,
    gegen die der Test gerichtet ist. Maerkte mit zu kurzer Historie werden mit
    Grund ausgewiesen statt still uebersprungen: ein leiser Ausschluss
    verwandelt einen unvollstaendigen Test in einen, der vollstaendig aussieht.
    """
    laeufe: list[MarketRun] = []
    for symbol in sorted(markets):
        bars = markets[symbol]
        lauf = MarketRun(symbol=symbol, n_bars=len(bars))
        try:
            ergebnis = walk_forward(
                lambda s=symbol: strategy_cls([s], timeframe),
                {symbol: bars}, train_bars=train_bars, test_bars=test_bars,
                embargo_bars=embargo_bars, cfg=cfg,
            )
        except InsufficientDataError:
            lauf.grund = f"zu wenig Historie ({len(bars)} Bars)"
        except Exception as exc:  # noqa: BLE001 -- ein Markt darf den Lauf nicht kippen
            lauf.grund = f"{type(exc).__name__}: {exc}"
        else:
            lauf.sharpe = float(ergebnis.metrics.sharpe)
            lauf.total_return = float(ergebnis.metrics.total_return)
            lauf.n_windows = ergebnis.n_windows
        laeufe.append(lauf)
        if on_market is not None:
            on_market(lauf)

    getestete = {lauf.symbol: markets[lauf.symbol] for lauf in laeufe if lauf.testbar}
    return CrossMarketResult(
        laeufe=laeufe,
        mittlere_korrelation=mean_pairwise_correlation(getestete),
        korrelationsmatrix=correlation_matrix(getestete),
    )


def correlation_matrix(markets: dict[str, list[Bar]]) -> np.ndarray | None:
    """Korrelationsmatrix der Bar-Renditen im **gemeinsamen** Fenster.

    Gemeinsam und nicht paarweise: die Eigenwerte einer Matrix, deren Zellen
    aus verschiedenen Zeitraeumen stammen, sind keine Eigenwerte einer
    Korrelationsmatrix -- sie kann sogar negative haben und damit eine
    effektive Marktzahl jenseits von n liefern.
    """
    if len(markets) < 2:
        return None
    reihen: dict[str, pd.Series] = {}
    for symbol, bars in markets.items():
        if len(bars) < 2:
            continue
        s = pd.Series(
            [bar.close for bar in bars],
            index=pd.to_datetime([bar.ts for bar in bars], utc=True),
        )
        reihen[symbol] = s.pct_change().dropna()
    if len(reihen) < 2:
        return None
    panel = pd.DataFrame(reihen).dropna()
    if len(panel) < 64 or panel.shape[1] < 2:
        return None
    return panel.corr().to_numpy()


def mean_pairwise_correlation(markets: dict[str, list[Bar]]) -> float:
    """Mittlere paarweise Korrelation der Bar-Renditen im gemeinsamen Fenster.

    Der Massstab dafuer, wieviele *unabhaengige* Beobachtungen ein Querschnitt
    wirklich liefert. Gerechnet wird auf den Maerkten selbst und nicht auf den
    Strategie-Kurven: die Frage ist, wieviel Neues ein weiterer Markt ueberhaupt
    beitragen kann, und das entscheidet der Markt, nicht die Strategie.
    """
    reihen: dict[str, dict[datetime, float]] = {}
    for symbol, bars in markets.items():
        closes = [(bar.ts, bar.close) for bar in bars]
        renditen: dict[datetime, float] = {}
        for (_, vorher), (ts, jetzt) in zip(closes, closes[1:], strict=False):
            if vorher > 0:
                renditen[ts] = jetzt / vorher - 1.0
        reihen[symbol] = renditen

    symbole = sorted(reihen)
    werte: list[float] = []
    for i, a in enumerate(symbole):
        for b in symbole[i + 1 :]:
            gemeinsam = sorted(set(reihen[a]) & set(reihen[b]))
            if len(gemeinsam) < 64:
                continue
            xa = np.array([reihen[a][ts] for ts in gemeinsam])
            xb = np.array([reihen[b][ts] for ts in gemeinsam])
            if xa.std() == 0 or xb.std() == 0:
                continue
            werte.append(float(np.corrcoef(xa, xb)[0, 1]))
    return float(np.mean(werte)) if werte else float("nan")
