"""Elliott-Wellen als mechanische Regel -- mit offenem Vorbehalt.

**Was hier ehrlich gesagt werden muss:** Die Elliott-Wellen-Theorie ist in
ihrer ueblichen Form *nicht* mechanisch. Ein Wellenzaehler betrachtet einen
Chart, vergibt Nummern, und wenn der Markt die Zaehlung widerlegt, wird
umnummeriert. Genau das macht sie im Rueckblick so ueberzeugend und im Voraus
so wenig pruefbar: eine Zaehlung, die nach jedem neuen Hoch neu vergeben
werden darf, kann gar nicht falsch sein.

Dieses Modul ist deshalb **eine** Mechanisierung, nicht *die* Theorie. Drei
Entscheidungen machen sie ueberhaupt erst pruefbar, und alle drei sind
Einschraenkungen gegenueber dem, was ein menschlicher Zaehler tun wuerde:

1. **Pivots werden bestaetigt, nicht erkannt.** Ein Swing-Hoch bei Bar `i`
   steht erst fest, wenn `confirm_bars` weitere Bars vergangen sind, ohne es
   zu ueberbieten. Bis dahin gibt es dort keinen Pivot. Das kostet Reaktions-
   zeit und ist der einzige Weg, ohne Blick in die Zukunft zu zaehlen.
2. **Es wird nie umnummeriert.** Eine Zaehlung gilt, bis der Markt sie nach
   Elliotts eigener Regel 1 bricht -- dann ist sie ungueltig und die Position
   wird geschlossen. Sie wird nicht zu einer anderen Zaehlung umgedeutet, die
   den Verlust wegerklaert.
3. **Nur die drei harten Regeln zaehlen**, nicht die Leitlinien. Regel 1 bis 3
   sind die einzigen Aussagen der Theorie, die eine Zaehlung eindeutig
   ausschliessen koennen; alles Weitere (Wellengleichheit, Kanaele,
   Fibonacci-Ziele als Vorhersage) ist Interpretation.

Die drei Regeln, in dieser Reihenfolge geprueft:

    R1  Welle 2 laeuft nie ueber den Anfang von Welle 1 zurueck.
    R2  Welle 3 ist nie die kuerzeste von 1, 3 und 5.
    R3  Welle 4 dringt nicht in das Preisgebiet von Welle 1 ein.

Regel 1 ist zugleich die Ausstiegsmarke. Das ist der eigentliche Grund, warum
diese Strategie ueberhaupt handelbar ist: die Theorie liefert ihre eigene
Widerlegung mit, und zwar als Preis, nicht als Meinung.

**Gehandelt wird die dritte Welle**, nicht die fuenfte. Nach einem
Impulsbein und einer Korrektur, die im ueblichen Fibonacci-Band liegt, wird
in Richtung des Impulses eingestiegen; die Position laeuft, bis entweder
Regel 1 bricht oder eine vollstaendige Fuenferstruktur steht. Die dritte
Welle ist die einzige, die die Theorie mit einer nachpruefbaren Aussage
versieht (sie ist nie die kuerzeste) -- bei der fuenften waere der Einstieg
eine reine Vorhersage.

Wie bei `timesfm_strategy` (ADR-022) steht die Strategie hier, weil sie
pruefbar ist, nicht weil sie profitabel waere. Ob sie Geld verdient,
entscheidet `qt wf`, nicht dieser Docstring.
"""

from __future__ import annotations

import math

import numpy as np

from qt.features import ta
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy
from qt.strategy.registry import register

# Uebliches Rueckzugsband einer zweiten Welle, als Anteil der ersten.
#
# Unter 38,2% ist der Rueckzug zu flach, um eine Korrekturwelle zu sein --
# dann laeuft der Impuls vermutlich noch. Ueber 78,6% ist er so tief, dass
# Regel 1 kurz vor dem Bruch steht und der Einstieg praktisch keinen Abstand
# zur Ungueltigkeit mehr haette. Beides sind Leitlinien, keine Regeln; sie
# stehen deshalb als Parameter hier und nicht als Konstante im Code.
DEFAULT_FIB_MIN = 0.382
DEFAULT_FIB_MAX = 0.786


class Pivot:
    """Ein bestaetigter Wendepunkt: Index, Preis, Richtung.

    `index` zeigt auf den Bar, an dem der Extremwert liegt -- **nicht** auf
    den Bar, an dem er bestaetigt wurde. Der Unterschied ist genau
    `confirm_bars` und der Grund, warum diese Strategie nicht in die Zukunft
    schaut.
    """

    __slots__ = ("index", "price", "is_high")

    def __init__(self, index: int, price: float, is_high: bool) -> None:
        self.index = index
        self.price = price
        self.is_high = is_high

    def __repr__(self) -> str:  # pragma: no cover -- nur fuer die Fehlersuche
        art = "H" if self.is_high else "T"
        return f"{art}@{self.index}={self.price:.2f}"


def confirmed_pivots(
    highs: np.ndarray,
    lows: np.ndarray,
    confirm_bars: int,
    min_move: float,
) -> list[Pivot]:
    """Bestaetigte Swing-Punkte, aelteste zuerst.

    Ein Hoch bei `i` gilt, wenn `highs[i]` das Maximum ueber
    `[i - confirm_bars, i + confirm_bars]` ist. Damit ist es fruehestens bei
    Bar `i + confirm_bars` bekannt -- die Schleife laeuft deshalb nur bis
    `len - confirm_bars`. **Das ist die Stelle, an der jede
    ZigZag-Implementierung schummelt**: wer bis `len - 1` laeuft, benutzt fuer
    den jeuengsten Pivot Bars, die es zum Entscheidungszeitpunkt noch nicht
    gab, und baut sich damit einen Backtest, der live nicht reproduzierbar ist.

    `min_move` filtert Rauschpivots: ein Wendepunkt zaehlt nur, wenn er sich
    vom vorherigen um mindestens diesen Betrag unterscheidet. Ohne den Filter
    zerfaellt jede Seitwaertsphase in Dutzende Miniwellen, und die
    Wellenzaehlung findet ueberall Muster -- was der klassische Vorwurf gegen
    die Methode ist und hier wenigstens nicht noch verstaerkt werden soll.

    Aufeinanderfolgende Pivots wechseln sich zwingend ab. Zwei Hochs
    hintereinander werden zum hoeheren zusammengefasst statt beide zu
    behalten: eine Wellenzaehlung braucht abwechselnde Extrema, alles andere
    waere kein Zickzack.
    """
    n = len(highs)
    if n < 2 * confirm_bars + 1:
        return []

    raw: list[Pivot] = []
    # Bis `n - confirm_bars` (exklusiv): alles darueber ist unbestaetigt.
    for i in range(confirm_bars, n - confirm_bars):
        lo, hi = i - confirm_bars, i + confirm_bars + 1
        if highs[i] == highs[lo:hi].max() and highs[i] > highs[lo:i].max(initial=-math.inf):
            raw.append(Pivot(i, float(highs[i]), True))
        elif lows[i] == lows[lo:hi].min() and lows[i] < lows[lo:i].min(initial=math.inf):
            raw.append(Pivot(i, float(lows[i]), False))

    if not raw:
        return []

    # Abwechselnd machen und Rauschen filtern.
    out: list[Pivot] = [raw[0]]
    for pivot in raw[1:]:
        last = out[-1]
        if pivot.is_high == last.is_high:
            # Gleiche Richtung: den extremeren behalten.
            better = pivot.price > last.price if pivot.is_high else pivot.price < last.price
            if better:
                out[-1] = pivot
            continue
        if abs(pivot.price - last.price) < min_move:
            continue
        out.append(pivot)
    return out


def wave_two_setup(
    pivots: list[Pivot], fib_min: float, fib_max: float
) -> tuple[int, float, float] | None:
    """Sucht eine abgeschlossene zweite Welle in den letzten drei Pivots.

    Gibt `(richtung, ungueltig_ab, wellen_eins_laenge)` zurueck oder `None`.
    `richtung` ist +1 fuer eine aufwaerts gerichtete Zaehlung, -1 fuer abwaerts.
    `ungueltig_ab` ist der Preis, an dem Regel 1 bricht -- also zugleich der
    Ausstieg.

    Geprueft wird genau eine Sache mehr als Regel 1: dass der Rueckzug im
    ueblichen Band liegt. Das ist eine Leitlinie und keine Regel, und sie
    steht deshalb als Parameter zur Verfuegung, nicht als Wahrheit.
    """
    if len(pivots) < 3:
        return None

    p0, p1, p2 = pivots[-3], pivots[-2], pivots[-1]

    # Aufwaerts: Tief, Hoch, Tief. Abwaerts: Hoch, Tief, Hoch.
    if not p0.is_high and p1.is_high and not p2.is_high:
        direction = 1
    elif p0.is_high and not p1.is_high and p2.is_high:
        direction = -1
    else:
        return None

    wave_one = abs(p1.price - p0.price)
    if wave_one <= 0:
        return None

    # R1: Welle 2 laeuft nie ueber den Anfang von Welle 1 zurueck.
    if direction > 0 and p2.price <= p0.price:
        return None
    if direction < 0 and p2.price >= p0.price:
        return None

    retrace = abs(p1.price - p2.price) / wave_one
    if not fib_min <= retrace <= fib_max:
        return None

    return direction, p0.price, wave_one


def impulse_complete(pivots: list[Pivot], direction: int) -> bool:
    """Steht eine vollstaendige Fuenferstruktur in Richtung `direction`?

    Prueft alle drei harten Regeln an den letzten sechs Pivots (P0 bis P5).
    Nur wenn alle drei halten, gilt der Impuls als abgeschlossen -- und dann
    ist der Grund fuer die Position weg, denn nach einer Fuenf folgt laut
    Theorie eine Korrektur.

    Die Rueckgabe ist bewusst ein `bool` und keine Wellennummer. Eine
    Funktion, die "wir sind in Welle 3" zurueckgaebe, waere eine Behauptung
    ueber die Gegenwart, die sich erst im Rueckblick pruefen laesst.
    """
    if len(pivots) < 6:
        return False

    p0, p1, p2, p3, p4, p5 = pivots[-6:]
    up = direction > 0

    # Die Struktur muss abwechseln und in der richtigen Richtung starten.
    expected_high = up
    for pivot in (p1, p3, p5):
        if pivot.is_high != expected_high:
            return False
    for pivot in (p0, p2, p4):
        if pivot.is_high == expected_high:
            return False

    w1 = abs(p1.price - p0.price)
    w3 = abs(p3.price - p2.price)
    w5 = abs(p5.price - p4.price)

    # R1: Welle 2 nicht ueber den Anfang von Welle 1 zurueck.
    if up and p2.price <= p0.price:
        return False
    if not up and p2.price >= p0.price:
        return False

    # R2: Welle 3 ist nie die kuerzeste.
    if w3 < w1 and w3 < w5:
        return False

    # R3: Welle 4 dringt nicht in Welle 1 ein.
    if up and p4.price <= p1.price:
        return False
    if not up and p4.price >= p1.price:
        return False

    return True


@register
class ElliottWave(Strategy):
    """Einstieg in der dritten Welle, Ausstieg wenn Regel 1 bricht."""

    name = "elliott"

    def __init__(
        self,
        symbols: list[str],
        timeframe: str,
        confirm_bars: int = 5,
        atr_period: int = 14,
        min_move_atr: float = 1.0,
        fib_min: float = DEFAULT_FIB_MIN,
        fib_max: float = DEFAULT_FIB_MAX,
        lookback: int = 300,
        allow_short: bool = True,
    ) -> None:
        super().__init__(
            symbols,
            timeframe,
            confirm_bars=confirm_bars,
            atr_period=atr_period,
            min_move_atr=min_move_atr,
            fib_min=fib_min,
            fib_max=fib_max,
            lookback=lookback,
            allow_short=allow_short,
        )

    @property
    def warmup_bars(self) -> int:
        # Genug Bars fuer mehrere bestaetigte Pivots plus den ATR-Vorlauf.
        # Sechs Pivots brauchen im guenstigsten Fall 6 * (confirm_bars + 1)
        # Bars -- realistisch mehr, aber darunter ist eine Zaehlung sicher
        # unmoeglich.
        pivot_room = 6 * (self.params["confirm_bars"] + 1)
        return max(pivot_room, self.params["atr_period"] + 2) + 2

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        window = store.window(symbol, self.timeframe)
        if len(window) < self.warmup_bars:
            return math.nan

        lookback = self.params["lookback"]
        highs = window.highs()[-lookback:]
        lows = window.lows()[-lookback:]
        closes = window.closes()[-lookback:]
        close = float(closes[-1])

        unit = ta.atr(highs, lows, closes, self.params["atr_period"])
        if not math.isfinite(unit) or unit <= 0:
            return math.nan

        pivots = confirmed_pivots(
            highs,
            lows,
            self.params["confirm_bars"],
            self.params["min_move_atr"] * unit,
        )

        state = self._state.setdefault(symbol, {"weight": 0.0, "invalid": math.nan})
        weight = state["weight"]
        invalid = state["invalid"]

        # --- Bestehende Zaehlung zuerst pruefen -------------------------
        #
        # Regel 1 hat Vorrang vor jedem Einstiegssignal. Sie ist der einzige
        # Punkt, an dem die Theorie selbst sagt, dass die Zaehlung falsch war
        # -- und eine falsche Zaehlung wird hier nicht umgedeutet, sondern
        # geschlossen.
        if weight != 0.0 and math.isfinite(invalid):
            broken = close <= invalid if weight > 0 else close >= invalid
            if broken:
                state["weight"], state["invalid"] = 0.0, math.nan
                return 0.0

        # Vollstaendige Fuenf: der Grund fuer die Position ist weg.
        if weight != 0.0 and impulse_complete(pivots, 1 if weight > 0 else -1):
            state["weight"], state["invalid"] = 0.0, math.nan
            return 0.0

        if weight != 0.0:
            return weight

        # --- Neue Zaehlung suchen ---------------------------------------
        setup = wave_two_setup(pivots, self.params["fib_min"], self.params["fib_max"])
        if setup is None:
            return 0.0

        direction, invalid_at, _wave_one = setup
        if direction < 0 and not self.params["allow_short"]:
            return 0.0

        # Der Markt muss die Korrektur bereits verlassen haben. Ohne diese
        # Bedingung waere der Einstieg eine Vorhersage, dass die dritte Welle
        # beginnt -- mit ihr ist er die Feststellung, dass sie begonnen hat.
        if direction > 0 and close <= pivots[-1].price:
            return 0.0
        if direction < 0 and close >= pivots[-1].price:
            return 0.0

        state["weight"] = float(direction)
        state["invalid"] = invalid_at
        return float(direction)
