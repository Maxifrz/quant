"""Baseline-Allokatoren -- der Massstab fuer den LLM-Allokator.

Diese Datei existiert absichtlich **vor** dem LLM-Allokator (ADR-004). Ohne
Vergleichsmassstab wird jedes LLM-Ergebnis als Erfolg gelesen: eine Kurve
steigt, also war die Allokation gut. Equal-Weight und Vol-Parity sind
erstaunlich schwer zu schlagen, und genau deshalb sind sie das Gate --
schlaegt das LLM sie out-of-sample nicht, gehoert es nicht in den Kreislauf.

Die vier Baselines decken bewusst unterschiedliche Haltungen ab:

    EqualWeight   -- keine Meinung. Der Massstab, den alles schlagen muss.
    VolParity     -- gleicher Risikobeitrag statt gleicher Kapitalanteil.
    BestSingle    -- dem juengsten Gewinner hinterherlaufen. Als *schlechte*
                     Idee gedacht: sie zeigt, was Performance-Chasing kostet.
    FixedWeights  -- vorgegebene Gewichte, fuer Referenzlaeufe und Tests.

Alle rechnen ausschliesslich auf `ctx.returns`, also auf bereits realisierten
Bar-Renditen. Keine der Baselines braucht mehr als das -- was auch das
Briefing des LLM-Allokators spaeter begrenzt: es darf nicht mehr sehen als
die Baselines, sonst vergleicht man zwei verschiedene Spiele.

Keine Baseline begrenzt das Einzelgewicht. Das ist kein Versehen: der Cap pro
Strategie gehoert in die Risk-Engine (ARCHITECTURE.md, "Der Allokator schlaegt
vor"). Zwei Stellen, die dasselbe Limit setzen, driften auseinander.
"""

from __future__ import annotations

import numpy as np

from qt.backtest import metrics
from qt.core.types import bars_per_year
from qt.features import ta
from qt.portfolio.base import Allocation, AllocationContext, Allocator

# Untergrenze fuer den Kapitalfaktor (1 + r) beim Bau der Pseudo-Equity.
# Eine Bar-Rendite von -100% oder schlimmer bedeutet, dass die Strategie
# ausgeloescht ist; der Logarithmus waere dann undefiniert. Statt an dieser
# Stelle mit nan zu explodieren, wird der Faktor abgeschnitten -- die Vola
# wird dadurch sehr gross, was zu einem sehr kleinen Gewicht fuehrt. Genau
# das ist die richtige Reaktion auf so eine Reihe.
_MIN_GROWTH = 1e-8


def _strategy_ids(ctx: AllocationContext) -> list[str]:
    """Ueber welche Strategien ueberhaupt verteilt wird.

    `strategy_ids` ist massgeblich; faellt es aus (leere Liste, aber Renditen
    vorhanden), wird auf die Renditereihen zurueckgegriffen. Immer sortiert,
    damit die Reihenfolge nie von der Iterationsreihenfolge eines Dicts
    abhaengt -- sonst ist der Backtest nicht bit-identisch reproduzierbar.
    """
    if ctx.strategy_ids:
        return sorted(ctx.strategy_ids)
    return sorted(ctx.returns)


def _equal_weight(strategy_ids: list[str]) -> Allocation:
    """Gleichverteilung -- der Rueckfall aller Baselines."""
    if not strategy_ids:
        return {}
    share = 1.0 / len(strategy_ids)
    return {sid: share for sid in strategy_ids}


def _normalise(raw: dict[str, float]) -> Allocation:
    """Auf Summe der Betraege = 1 skalieren.

    Vorzeichen bleiben erhalten (eine Strategie invertieren ist erlaubt),
    nur der Massstab wird gesetzt.
    """
    total = sum(abs(v) for v in raw.values())
    if total <= 0 or not np.isfinite(total):
        return {}
    return {sid: value / total for sid, value in raw.items()}


def _pseudo_equity(returns: np.ndarray) -> np.ndarray:
    """Renditereihe in eine Kapitalkurve verwandeln.

    Die TA-Bausteine arbeiten auf Preisreihen, `ctx.returns` liefert
    Renditen. Statt eine zweite Vola-Funktion zu schreiben, wird hier die
    Kurve rekonstruiert, deren Log-Renditen exakt `log(1 + r)` sind --
    `ta.realised_vol` bleibt damit die einzige Vola-Definition im System.
    """
    growth = np.clip(1.0 + np.asarray(returns, dtype=float), _MIN_GROWTH, None)
    return np.concatenate(([1.0], np.cumprod(growth)))


def _vol(returns: np.ndarray, lookback: int, timeframe: str) -> float:
    """Annualisierte Vola einer Strategie-Renditereihe, `nan` wenn unbekannt."""
    if returns is None or len(returns) < lookback:
        return float("nan")
    curve = _pseudo_equity(returns)
    return ta.realised_vol(curve, lookback, bars_per_year(timeframe))


def _sharpe(returns: np.ndarray, lookback: int, timeframe: str) -> float:
    """Annualisierter Sharpe der letzten `lookback` Bars, `nan` wenn unbekannt.

    Delegiert an `qt.backtest.metrics.sharpe`, damit es im System genau eine
    Sharpe-Definition gibt -- der Allokator muss dasselbe messen wie der
    Report, sonst optimiert er gegen eine andere Zahl als die, an der er
    spaeter beurteilt wird.
    """
    if returns is None or len(returns) < lookback or lookback < 2:
        return float("nan")
    return metrics.sharpe(np.asarray(returns[-lookback:], dtype=float), timeframe)


class EqualWeight(Allocator):
    """Jede Strategie gleich viel Kapital.

    Trivial und in der Praxis schwer zu uebertreffen: Gleichgewichtung
    schaetzt nichts und kann deshalb auch nichts falsch schaetzen. Jede
    kompliziertere Allokation muss zuerst erklaeren, warum sie das
    Schaetzrisiko wert ist.
    """

    name = "equal_weight"

    def allocate(self, ctx: AllocationContext) -> Allocation:
        return _equal_weight(_strategy_ids(ctx))


class VolParity(Allocator):
    """Gewicht invers zur realisierten Vola der Strategie.

    Ziel ist gleicher *Risikobeitrag* statt gleichem Kapitalanteil. Ohne das
    dominiert die volatilste Strategie das Portfolioergebnis, egal wie klein
    ihr Kapitalanteil aussieht.

    Umgang mit unbrauchbaren Vola-Schaetzungen (Vola = 0 oder `nan`): solche
    Strategien werden **ausgeschlossen** (Gewicht 0), nicht gleichgewichtet.
    Der Grund ist die Richtung des Fehlers: 1/0 ist unendlich, eine naive
    Umsetzung wuerde einer Strategie ohne belastbare Risikoschaetzung also
    das *gesamte* Kapital geben. Vola 0 heisst in der Praxis "war im Fenster
    durchgehend flat" -- so eine Strategie verliert durch den Ausschluss
    nichts, weil sie ohnehin keine Position haelt. `nan` heisst "zu wenig
    Historie", und eine frisch hinzugefuegte Strategie soll nicht vor ihrer
    ersten Messung Kapital bekommen. Bleibt keine einzige brauchbare
    Schaetzung uebrig, wird gleichgewichtet.
    """

    name = "vol_parity"

    def __init__(self, lookback: int = 168) -> None:
        self.lookback = int(lookback)

    @property
    def warmup_bars(self) -> int:
        return self.lookback

    def allocate(self, ctx: AllocationContext) -> Allocation:
        strategy_ids = _strategy_ids(ctx)
        if not strategy_ids:
            return {}

        inverse: dict[str, float] = {}
        for sid in strategy_ids:
            vol = _vol(ctx.returns.get(sid), self.lookback, ctx.timeframe)
            if np.isfinite(vol) and vol > 0:
                inverse[sid] = 1.0 / vol

        # Deckt den Warmup mit ab: solange keine Strategie das Fenster fuellt,
        # gibt es keine einzige brauchbare Schaetzung. Bewusst nicht ueber
        # `ctx.history_length()` geprueft -- das ist das Minimum ueber alle
        # Strategien, und eine einzige frisch hinzugefuegte Strategie wuerde
        # damit das ganze Portfolio fuer `lookback` Bars auf Gleichgewichtung
        # zuruecksetzen und dabei unnoetig umschichten.
        if not inverse:
            return _equal_weight(strategy_ids)

        weights = _normalise(inverse)
        return {sid: weights.get(sid, 0.0) for sid in strategy_ids}

    def describe(self) -> str:
        return f"{self.name}(lookback={self.lookback})"


class BestSingle(Allocator):
    """Alles auf die Strategie mit dem besten rollierenden Sharpe.

    Als Massstab bewusst eine schlechte Idee: rollierender Sharpe ueber ein
    kurzes Fenster ist zu grossen Teilen Rauschen, und wer dem juengsten
    Gewinner hinterherlaeuft, kauft systematisch nach der guten Phase. Genau
    deshalb gehoert die Baseline dazu -- sie beziffert, was Performance-
    Chasing kostet, statt es nur zu behaupten.

    Bei Gleichstand entscheidet die alphabetisch erste Strategie. Ohne diese
    Regel haengt das Ergebnis an der Dict-Reihenfolge und der Backtest ist
    nicht reproduzierbar.
    """

    name = "best_single"

    def __init__(self, lookback: int = 720) -> None:
        self.lookback = int(lookback)

    @property
    def warmup_bars(self) -> int:
        # Sharpe braucht mehr Stichprobe als eine Vola-Schaetzung: der
        # Mittelwert im Zaehler konvergiert deutlich langsamer als die
        # Streuung im Nenner.
        return self.lookback

    def allocate(self, ctx: AllocationContext) -> Allocation:
        strategy_ids = _strategy_ids(ctx)
        if not strategy_ids:
            return {}

        best: str | None = None
        best_sharpe = float("-inf")
        for sid in strategy_ids:  # bereits sortiert -> Gleichstand faellt auf den ersten
            sharpe = _sharpe(ctx.returns.get(sid), self.lookback, ctx.timeframe)
            if np.isfinite(sharpe) and sharpe > best_sharpe:
                best, best_sharpe = sid, sharpe

        if best is None:
            return _equal_weight(strategy_ids)
        return {sid: (1.0 if sid == best else 0.0) for sid in strategy_ids}

    def describe(self) -> str:
        return f"{self.name}(lookback={self.lookback})"


class FixedWeights(Allocator):
    """Vom Nutzer vorgegebene Kapitalanteile.

    Fuer Referenzlaeufe ("was haette 70/30 gebracht") und als Testwerkzeug,
    wenn ein Backtest eine bekannte Allokation braucht und nicht deren
    Schaetzung. Uebersteigt die Summe der Betraege 1, wird herunterskaliert
    statt abgeschnitten -- das Verhaeltnis der Gewichte zueinander ist die
    Aussage des Nutzers, die absolute Hoehe nur eine Nebenbedingung.
    """

    name = "fixed_weights"

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = dict(weights or {})

    def allocate(self, ctx: AllocationContext) -> Allocation:
        strategy_ids = _strategy_ids(ctx)
        if not strategy_ids:
            return {}

        selected = {
            sid: float(self.weights[sid])
            for sid in strategy_ids
            if sid in self.weights and np.isfinite(self.weights[sid])
        }
        if not selected:
            return {sid: 0.0 for sid in strategy_ids}

        total = sum(abs(v) for v in selected.values())
        if total > 1.0:
            selected = _normalise(selected)
        return {sid: selected.get(sid, 0.0) for sid in strategy_ids}

    def describe(self) -> str:
        body = ", ".join(f"{k}={v:g}" for k, v in sorted(self.weights.items()))
        return f"{self.name}({body})"


_BASELINES: dict[str, type[Allocator]] = {
    cls.name: cls for cls in (EqualWeight, VolParity, BestSingle, FixedWeights)
}


def get(name: str) -> type[Allocator]:
    """Baseline-Klasse nach Namen. Gleicher Zugriff wie qt.strategy.registry."""
    try:
        return _BASELINES[name]
    except KeyError:
        raise KeyError(
            f"Unbekannte Baseline {name!r}. Verfuegbar: {sorted(_BASELINES)}"
        ) from None


def names() -> list[str]:
    return sorted(_BASELINES)


def default_set() -> list[Allocator]:
    """Die Baselines, gegen die sich der LLM-Allokator messen lassen muss.

    `FixedWeights` ist nicht dabei: es hat ohne Vorgabe des Nutzers keine
    sinnvolle Instanz und ist kein Massstab, sondern ein Werkzeug.
    """
    return [EqualWeight(), VolParity(), BestSingle()]
