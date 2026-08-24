"""Das Blind Briefing -- was das LLM sehen darf.

Ein Sprachmodell kennt die Vergangenheit. Fragt man es "wie haettest du im
Maerz 2020 allokiert", weiss es die Antwort. Der Backtest des Allokators
waere dann wertlos, **ohne dass irgendwo ein Bug ist** -- das ist die
subtilste Fehlerquelle im ganzen Entwurf und der Grund, warum diese Datei
existiert.

Deshalb enthaelt ein Briefing:

    KEINE Datumsangaben, keine Zeitstempel, keine Jahreszahlen
    KEINE Asset-Namen (BTC/USD wird zu ASSET_1)
    KEINE Strategienamen (trend wird zu STRAT_A)
    KEINE absoluten Preise oder Kontostaende
    KEINE News, keine externen Ereignisse

Was es enthaelt, sind normalisierte Kennzahlen: Verhaeltnisse, z-Scores,
Prozentwerte. Das Modell sieht ein **Regime**, keinen Zeitpunkt.

Das entfernt das Problem nicht vollstaendig. Ein Einbruch von -50% bei
verdreifachter Volatilitaet ist auch anonymisiert wiedererkennbar, und ein
Modell mit gutem Gedaechtnis fuer Marktgeschichte kann raten. Deshalb gilt
zusaetzlich und unabhaengig davon: **der Allokator wird primaer am
Forward-Paper-Trading gemessen, nicht am Backtest** (ADR-003).

Zweiter Zweck derselben Bauweise: das Briefing ist die Grundlage des
Cache-Keys. Es muss deshalb fuer denselben Zustand bitgleich sein --
gerundete Zahlen, sortierte Schluessel, keine Zeitstempel. Ein Briefing mit
`datetime.now()` darin waere ein Cache mit 0% Trefferquote.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np

from qt.backtest import metrics
from qt.core.types import bars_per_year
from qt.portfolio.base import AllocationContext

# Auf wieviele Nachkommastellen Kennzahlen gerundet werden.
#
# Rundung ist hier kein Schoenheitsfehler, sondern Absicht: exakte
# Fliesskommawerte sind ein Fingerabdruck, ueber den sich eine historische
# Periode identifizieren liesse. Drei Stellen tragen jede Information, die
# fuer eine Allokationsentscheidung zaehlt.
ROUND = 3

# Fenster, ueber die je Strategie berichtet wird, in Bars.
# Bewusst als Vielfache statt in Tagen -- "30 Tage" waere eine Zeitangabe.
WINDOWS: tuple[int, ...] = (24, 96, 384)


def label_for(index: int) -> str:
    """Anonymes Strategie-Label: STRAT_A, STRAT_B, ... STRAT_Z, STRAT_AA."""
    letters = ""
    index += 1
    while index > 0:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return f"STRAT_{letters}"


@dataclass(slots=True)
class Briefing:
    """Ein fertiges Briefing samt Rueckuebersetzung der Labels.

    `mapping` bleibt **im Prozess** und geht nie ins Prompt. Es dient nur
    dazu, die Antwort des Modells wieder auf echte Strategien abzubilden.
    """

    payload: dict
    mapping: dict[str, str] = field(default_factory=dict)

    @property
    def labels(self) -> list[str]:
        return sorted(self.mapping)

    def to_prompt(self) -> str:
        """Als kompaktes, deterministisches JSON.

        `sort_keys=True` ist nicht kosmetisch: ohne feste Schluesselreihenfolge
        aendert sich der Cache-Key bei jedem Lauf, und der Cache liefe leer.
        """
        return json.dumps(self.payload, sort_keys=True, separators=(",", ": "))

    def resolve(self, allocation: dict[str, float]) -> dict[str, float]:
        """Labels zurueck auf echte Strategie-IDs abbilden."""
        return {
            self.mapping[label]: weight
            for label, weight in allocation.items()
            if label in self.mapping
        }


def build(ctx: AllocationContext) -> Briefing:
    """Blind Briefing aus einem Allokations-Kontext bauen.

    Die Zuordnung Strategie -> Label laeuft ueber die **sortierte** Liste der
    Strategie-IDs. Deterministisch zu sein ist Pflicht, weil sonst der
    Cache-Key wandert; dass die Zuordnung damit alphabetisch ist, ist
    unkritisch, weil das Modell die echten Namen nie sieht.
    """
    ids = sorted(ctx.strategy_ids)
    mapping = {label_for(i): sid for i, sid in enumerate(ids)}
    reverse = {sid: label for label, sid in mapping.items()}

    strategies = {
        reverse[sid]: _strategy_view(ctx.returns.get(sid), ctx.timeframe)
        for sid in ids
    }

    payload = {
        "strategies": strategies,
        "correlations": _correlations(ctx, reverse, ids),
        "portfolio": _portfolio_view(ctx, reverse),
        "windows": list(WINDOWS),
        "note": (
            "Zeitraum, Maerkte und Strategienamen sind bewusst nicht angegeben. "
            "Entscheide anhand der Kennzahlen, nicht anhand einer Vermutung, "
            "welche Periode das sein koennte."
        ),
    }
    return Briefing(payload=payload, mapping=mapping)


def _strategy_view(returns: np.ndarray | None, timeframe: str) -> dict:
    """Kennzahlen einer Strategie ueber mehrere Fenster.

    Alle Werte sind Verhaeltnisse oder annualisierte Groessen -- nichts davon
    laesst auf einen Zeitpunkt schliessen. Zu kurze Historie ergibt `None`
    statt einer erfundenen Zahl: dem Modell zu sagen "unbekannt" ist ehrlich,
    ihm eine Null zu zeigen waere eine Luege ueber die Datenlage.
    """
    if returns is None or len(returns) == 0:
        return {"history_bars": 0, "windows": {}, "state": "keine Historie"}

    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]

    windows: dict[str, dict] = {}
    for size in WINDOWS:
        if len(values) < size:
            windows[str(size)] = None
            continue
        chunk = values[-size:]
        windows[str(size)] = {
            "return": _round(float(np.prod(1 + chunk) - 1)),
            "vol_annualised": _round(
                float(chunk.std(ddof=1) * math.sqrt(bars_per_year(timeframe)))
            ),
            "sharpe": _round(metrics.sharpe(chunk, timeframe)),
            "hit_rate": _round(
                float((chunk[chunk != 0] > 0).mean()) if (chunk != 0).any() else None
            ),
            "time_in_market": _round(float((chunk != 0).mean())),
        }

    return {
        "history_bars": len(values),
        "windows": windows,
        "drawdown_from_peak": _round(_drawdown(values)),
        "bars_since_peak": _bars_since_peak(values),
    }


def _correlations(
    ctx: AllocationContext, reverse: dict[str, str], ids: list[str]
) -> dict[str, float | None]:
    """Paarweise Korrelation der Strategie-Renditen.

    Der wichtigste Einzelwert fuer eine Allokationsentscheidung: zwei
    Strategien mit Korrelation 0,95 sind eine Strategie in doppelter
    Groesse, egal wie unterschiedlich sie heissen.
    """
    out: dict[str, float | None] = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            ra, rb = ctx.returns.get(a), ctx.returns.get(b)
            key = f"{reverse[a]}|{reverse[b]}"
            if ra is None or rb is None:
                out[key] = None
                continue
            n = min(len(ra), len(rb))
            if n < 32:
                out[key] = None
                continue
            xa, xb = np.asarray(ra[-n:], float), np.asarray(rb[-n:], float)
            if xa.std() == 0 or xb.std() == 0:
                out[key] = None
                continue
            out[key] = _round(float(np.corrcoef(xa, xb)[0, 1]))
    return out


def _portfolio_view(ctx: AllocationContext, reverse: dict[str, str]) -> dict:
    """Zustand des Portfolios -- relativ, nie absolut.

    Der Kontostand steht bewusst nicht drin. Er sagt nichts ueber die
    richtige Allokation aus, waere aber ueber die Groessenordnung ein Hinweis
    auf den Zeitpunkt.
    """
    current = {
        reverse[sid]: _round(weight)
        for sid, weight in ctx.current.items()
        if sid in reverse
    }
    return {
        "current_allocation": current,
        "gross_allocated": _round(sum(abs(v) for v in current.values())),
        "n_strategies": len(reverse),
    }


def _drawdown(returns: np.ndarray) -> float:
    """Aktueller Abstand zum Hoechststand der Papier-Kurve."""
    if len(returns) == 0:
        return 0.0
    curve = np.cumprod(1 + returns)
    return float(curve[-1] / np.maximum.accumulate(curve)[-1] - 1)


def _bars_since_peak(returns: np.ndarray) -> int:
    """Wie lange der Hoechststand her ist -- in Bars, nicht in Tagen."""
    if len(returns) == 0:
        return 0
    curve = np.cumprod(1 + returns)
    return int(len(curve) - 1 - int(np.argmax(curve)))


def _round(value: float | None) -> float | None:
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return round(float(value), ROUND)


# ---------------------------------------------------------------------------
# Szenario-Briefing (Phase 4)
# ---------------------------------------------------------------------------


def build_scenario_briefing(returns: np.ndarray, ensemble, timeframe: str) -> str:
    """Blind Briefing fuer die Szenario-Einschaetzung.

    Denselben Regeln unterworfen wie das Allokations-Briefing (ADR-017):
    keine Datumsangaben, keine Asset-Namen, keine absoluten Preise, Fenster
    in Bars statt in Tagen, Zahlen gerundet, Schluessel sortiert.

    Was zusaetzlich drinsteht, ist die **Beschreibung des simulierten
    Ensembles** -- das Modell soll wissen, was die Simulation ohnehin schon
    fuer moeglich haelt, bevor es Gewicht verschiebt. Ohne diesen Bezugspunkt
    wuesste es nicht, ob "erhoehte Volatilitaet" gegenueber dem Ensemble eine
    Verschiebung waere oder bereits dessen Normalfall.
    """
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]

    recent: dict[str, dict | None] = {}
    for size in WINDOWS:
        if len(values) < size:
            recent[str(size)] = None
            continue
        chunk = values[-size:]
        recent[str(size)] = {
            "return": _round(float(np.prod(1 + chunk) - 1)),
            "vol_annualised": _round(
                float(chunk.std(ddof=1) * math.sqrt(bars_per_year(timeframe)))
            ),
            "hit_rate": _round(float((chunk > 0).mean())),
        }

    # Vola des juengsten Fensters relativ zum laengsten -- die eine Zahl, die
    # ein Regime am ehesten beschreibt, ohne einen Zeitpunkt zu verraten.
    vol_ratio = None
    short, long = recent.get(str(WINDOWS[0])), recent.get(str(WINDOWS[-1]))
    if short and long and long["vol_annualised"]:
        vol_ratio = _round(short["vol_annualised"] / long["vol_annualised"])

    terminal = ensemble.terminal_returns()
    payload = {
        "recent_market": recent,
        "vol_short_vs_long": vol_ratio,
        "simulated_ensemble": {
            "n_paths": ensemble.n_paths,
            "horizon_bars": ensemble.horizon,
            "terminal_return_p05": _round(float(ensemble.quantile(0.05))),
            "terminal_return_median": _round(float(ensemble.quantile(0.5))),
            "terminal_return_p95": _round(float(ensemble.quantile(0.95))),
            "path_vol_median": _round(float(np.median(ensemble.paths.std(axis=1, ddof=1)))),
            "prob_loss": _round(float((terminal < 0).mean())),
        },
        "windows": list(WINDOWS),
        "note": (
            "Zeitraum, Maerkte und absolute Preise sind bewusst nicht "
            "angegeben. Verschiebe Gewicht zwischen simulierten Pfaden; "
            "prognostiziere keinen Preis. Keine Priors sind eine gute Antwort."
        ),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ": "))
