"""Das statistische Gate: Walk-Forward, dann Deflated Sharpe Ratio.

Zwei Stufen, absichtlich in dieser Reihenfolge:

1. Ein einzelner In-Sample-Lauf als Plausibilitaetspruefung. Er kostet einen
   Bruchteil des Walk-Forward-Laufs und faengt ab, was gar nicht erst
   handelt -- eine Strategie ohne einen einzigen Trade braucht keine 17
   Fenster, um als unbrauchbar zu gelten.
2. Der Walk-Forward-Lauf ueber `qt.backtest.walkforward`, dann die Deflated
   Sharpe Ratio gegen die Zahl **aller je getesteten** Kandidaten (ADR-005).

Der Sanity-Check bewertet ausdruecklich **nicht** die Qualitaet. Er darf nur
verwerfen, was technisch nichts liefert -- kein Trade, kein endlicher Sharpe.
Eine In-Sample-Schwelle auf die Rendite waere eine Vorauswahl auf denselben
Daten, gegen die spaeter out-of-sample geprueft wird, und damit genau die
Form von Selektion, die die DSR bestrafen soll.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from qt.backtest.engine import run_backtest
from qt.backtest.metrics import compute
from qt.backtest.walkforward import InsufficientDataError, walk_forward
from qt.core.config import BacktestConfig
from qt.core.types import Bar
from qt.research.dsr import DEFAULT_THRESHOLD, DSRResult, dsr_from_returns


#: Ziehungen der Permutationskontrolle im Screening. Weniger als in ADR-054
#: (dort 1000), weil sie hier nur ueber Bestehen oder Nichtbestehen
#: entscheidet und nicht ueber eine berichtete Zahl.
PLACEBO_ZIEHUNGEN = 200

#: Dieselbe Schwelle wie in `qt placebo shuffle` und in Gate 1 (ADR-054).
PLACEBO_SCHWELLE = 0.95


@dataclass(slots=True)
class SanityResult:
    """Ergebnis der billigen Vorpruefung."""

    ok: bool
    reason: str = ""
    n_trades: int = 0
    sharpe: float = float("nan")


@dataclass(slots=True)
class ScreeningResult:
    """Ergebnis des Walk-Forward-Screenings samt DSR."""

    passed: bool
    reason: str
    n_windows: int = 0
    oos_bars: int = 0
    sharpe: float = float("nan")
    dsr: float | None = None
    dsr_threshold: float = DEFAULT_THRESHOLD
    trial_count: int = 0
    detail: DSRResult | None = None
    #: Perzentil der Permutationskontrolle -- nur gesetzt, wenn die DSR
    #: bestanden wurde und die Kontrolle deshalb ueberhaupt lief.
    placebo_perzentil: float | None = None

    def summary(self) -> str:
        if self.dsr is None:
            return f"kein Ergebnis: {self.reason}"
        text = (
            f"Sharpe {self.sharpe:+.2f} ueber {self.n_windows} Fenster "
            f"({self.oos_bars} OOS-Bars), DSR {self.dsr:.3f} gegen "
            f"{self.trial_count} Versuche, Schwelle {self.dsr_threshold:.2f}"
        )
        if self.placebo_perzentil is not None:
            text += f", Placebo-Perzentil {self.placebo_perzentil:.1%}"
        return text


def quick_sanity_check(
    strategy_cls,
    bars: dict[str, list[Bar]],
    symbols: list[str],
    timeframe: str,
    cfg: BacktestConfig | None = None,
) -> SanityResult:
    """Ein einzelner Lauf: handelt der Kandidat ueberhaupt?

    Bewusst ohne Qualitaetsurteil (siehe Modul-Docstring). Geprueft wird nur,
    ob ueberhaupt etwas passiert und ob die Kennzahlen endlich sind.
    """
    try:
        strategy = strategy_cls(symbols, timeframe)
        result = run_backtest(strategy, bars, cfg=cfg)
    except Exception as exc:  # noqa: BLE001 -- ein Kandidat darf scheitern
        return SanityResult(False, f"Lauf fehlgeschlagen: {type(exc).__name__}: {exc}")

    # `BacktestResult` traegt keine Kennzahlen, die werden aus der Kurve
    # gerechnet -- dieselbe Trennung wie im Tearsheet und im Gate.
    metrics = compute(
        result.equity.set_index("ts")["equity"],
        timeframe,
        n_trades=result.n_trades,
        fees_paid=result.fees_paid,
    )
    if metrics.n_trades == 0:
        return SanityResult(
            False, "kein einziger Trade -- der Kandidat handelt nie", 0
        )
    if not np.isfinite(metrics.sharpe):
        return SanityResult(
            False,
            "Sharpe ist nicht endlich -- die Kapitalkurve ist konstant oder kaputt",
            metrics.n_trades,
        )
    return SanityResult(True, "", metrics.n_trades, metrics.sharpe)


def screen_candidate(
    strategy_cls,
    bars: dict[str, list[Bar]],
    symbols: list[str],
    timeframe: str,
    trial_count: int,
    train_bars: int = 3000,
    test_bars: int = 800,
    embargo_bars: int = 50,
    dsr_threshold: float = DEFAULT_THRESHOLD,
    cfg: BacktestConfig | None = None,
) -> ScreeningResult:
    """Walk-Forward-OOS, dann DSR gegen `trial_count`.

    `trial_count` **schliesst diesen Kandidaten ein** -- er ist einer der
    Blicke auf die Daten, und die Korrektur soll ihn mitzaehlen. Die Zahl
    stammt aus der Registry und nicht aus einem Zaehler dieses Laufs: wer
    heute den einundzwanzigsten Kandidaten testet, hat einundzwanzig Blicke
    getan, auch wenn zwanzig davon aus der Sitzung von letzter Woche stammen.

    Die DSR wird auf der **verketteten Out-of-Sample-Renditereihe** gerechnet,
    nicht auf einer In-Sample-Kurve -- sonst korrigierte man eine Zahl, die
    ohnehin schon geschoent ist, und das Ergebnis saehe strenger aus, als es
    ist.
    """
    try:
        result = walk_forward(
            lambda: strategy_cls(symbols, timeframe),
            bars,
            train_bars=train_bars,
            test_bars=test_bars,
            embargo_bars=embargo_bars,
            cfg=cfg,
        )
    except InsufficientDataError as exc:
        return ScreeningResult(False, f"zu wenig Daten: {exc}", trial_count=trial_count)
    except Exception as exc:  # noqa: BLE001 -- ein Kandidat darf scheitern
        return ScreeningResult(
            False,
            f"Walk-Forward fehlgeschlagen: {type(exc).__name__}: {exc}",
            trial_count=trial_count,
        )

    equity = result.equity
    if equity.empty or len(equity) < 3:
        return ScreeningResult(
            False, "Kapitalkurve zu kurz fuer eine Statistik", trial_count=trial_count
        )

    returns = equity["equity"].pct_change().dropna().to_numpy(dtype=float)
    n_windows = result.n_windows
    oos_bars = result.oos_bars

    try:
        detail = dsr_from_returns(returns, n_trials=trial_count)
    except ValueError as exc:
        return ScreeningResult(
            False,
            f"DSR nicht berechenbar: {exc}",
            n_windows=n_windows,
            oos_bars=oos_bars,
            sharpe=float(result.metrics.sharpe),
            trial_count=trial_count,
        )

    passed = detail.passed(dsr_threshold)
    reason = (
        "DSR ueber der Schwelle"
        if passed
        else f"DSR {detail.dsr:.3f} unter der Schwelle {dsr_threshold:.2f}"
    )

    # Die letzte Stufe der Kette aus docs/ZIEL.md Phase C.3. Sie lief bis
    # ADR-065 nicht: `screen_candidate` hoerte nach der DSR auf, und die
    # Kette stand nur im Plan. Bemerkt hat es niemand, weil bisher kein
    # Kandidat je bis hierher kam -- eine fehlende Stufe hinter einer nie
    # genommenen Huerde sieht genauso aus wie eine vorhandene.
    #
    # Erst **nach** der DSR, nicht davor: 200 Ziehungen Walk-Forward kosten
    # ein Vielfaches des Screenings, und ein Kandidat, der an der DSR
    # scheitert, ist ohnehin tot. Die Reihenfolge ist dieselbe wie im
    # uebrigen Trichter -- nach Kosten sortiert.
    placebo_perzentil = None
    if passed:
        try:
            from qt.research.placebo import permutation_control

            kontrolle = permutation_control(
                lambda: strategy_cls(symbols, timeframe),
                bars,
                train_bars=train_bars,
                test_bars=test_bars,
                embargo_bars=embargo_bars,
                draws=PLACEBO_ZIEHUNGEN,
                cfg=cfg,
            )
            placebo_perzentil = kontrolle.perzentil
            if not kontrolle.bestanden(PLACEBO_SCHWELLE):
                passed = False
                reason = (
                    f"Placebo-Perzentil {kontrolle.perzentil:.1%} unter "
                    f"{PLACEBO_SCHWELLE:.0%} -- die Lage der Episoden traegt "
                    "das Ergebnis, nicht das Signal"
                )
        except Exception as exc:  # noqa: BLE001 -- ein Kandidat darf scheitern
            passed = False
            reason = (
                f"Negativkontrolle nicht auswertbar: {type(exc).__name__}: {exc}. "
                "Ungeprueft ist nicht bestanden."
            )

    return ScreeningResult(
        passed=passed,
        reason=reason,
        n_windows=n_windows,
        oos_bars=oos_bars,
        sharpe=float(result.metrics.sharpe),
        dsr=detail.dsr,
        dsr_threshold=dsr_threshold,
        trial_count=trial_count,
        detail=detail,
        placebo_perzentil=placebo_perzentil,
    )
