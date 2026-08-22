"""Das Gate: der Vergleich, der ueber den LLM-Allokator entscheidet.

Ein Gate, das zu leicht durchlaesst, ist schlimmer als keines -- es liefert
dem LLM ein Guetesiegel, dem danach alle glauben. Die Wege, auf denen so ein
Gate leise zu leicht wird, sind alle hier abgedeckt: ein In-Sample-Vergleich,
unterschiedliche Bars oder Fenster je Allokator, Zustand, der ueber eine
Fenstergrenze leckt, ein Kandidat, der nur die schwaechste Baseline schlaegt,
und ein Allokator, der wirft und dabei den ganzen Vergleich mitreisst.

Keine echten LLM-Aufrufe, keine Netzwerkzugriffe: die Kandidaten hier sind
absichtlich gute und absichtlich schlechte Allokatoren mit bekanntem Ergebnis.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qt.backtest.metrics import Metrics
from qt.backtest.walkforward import InsufficientDataError
from qt.core.config import BacktestConfig, CostConfig
from qt.features.registry import FeatureStore
from qt.portfolio.base import Allocation, AllocationContext, Allocator
from qt.portfolio.baselines import EqualWeight, FixedWeights, VolParity
from qt.portfolio.gate import GateResult, _beats, run_gate
from qt.strategy.base import Strategy
from tests.conftest import make_bars

FREE = BacktestConfig(costs=CostConfig(taker_fee_bps=0, half_spread_bps=0, slippage_bps=0))

UP = "UP/USD"
DOWN = "DOWN/USD"
N_BARS = 500

# Fenstergeometrie aller Tests: 4 Fenster, klein genug fuer schnelle Laeufe.
TRAIN, TEST, EMBARGO = 40, 100, 0


def _bars(n: int = N_BARS) -> dict[tuple[str, str], list]:
    """Ein steigendes und ein fallendes Symbol.

    Bewusst eindeutig: dann ist bekannt, welche Allokation die gute ist, und
    ein Test, der das Gegenteil behauptet, ist ein Fehler im Gate und nicht
    Pech mit den Daten.
    """
    rng_up = np.random.default_rng(11)
    rng_down = np.random.default_rng(12)
    up = 100 * np.cumprod(1 + rng_up.normal(0.002, 0.01, n))
    down = 100 * np.cumprod(1 + rng_down.normal(-0.002, 0.01, n))
    return {
        (UP, "1h"): make_bars(n, symbol=UP, timeframe="1h", prices=up),
        (DOWN, "1h"): make_bars(n, symbol=DOWN, timeframe="1h", prices=down),
    }


class Long(Strategy):
    """Immer voll long im eigenen Symbol."""

    name = "long"

    @property
    def warmup_bars(self) -> int:
        return 5

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        return 1.0


def _strategies() -> dict[str, Strategy]:
    """Eine Strategie auf dem Gewinner, eine auf dem Verlierer.

    Der Allokator entscheidet damit alles -- genau das soll das Gate messen.
    """
    return {"up": Long([UP], "1h"), "down": Long([DOWN], "1h")}


def _good() -> Allocator:
    """Alles auf die Strategie, die verdient."""
    return FixedWeights({"up": 1.0, "down": 0.0})


def _bad() -> Allocator:
    """Alles auf die Strategie, die verliert."""
    return FixedWeights({"up": 0.0, "down": 1.0})


def _gate(candidate, baselines, **kwargs) -> GateResult:
    return run_gate(
        candidate,
        _strategies,
        _bars(),
        train_bars=TRAIN,
        test_bars=TEST,
        baselines=baselines,
        cfg=FREE,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Das Urteil
# ---------------------------------------------------------------------------


def test_good_candidate_passes():
    result = _gate(_good, [EqualWeight(), VolParity(lookback=20)])

    assert result.passed(), result.verdict()
    assert result.n_windows == 4
    assert result.candidate_entry.metrics.sharpe > max(
        b.metrics.sharpe for b in result.baselines
    )
    assert "BESTANDEN" in result.verdict()


def test_bad_candidate_fails_and_says_against_whom():
    result = _gate(_bad, [EqualWeight(), VolParity(lookback=20)])

    assert not result.passed()
    verdict = result.verdict()
    assert "DURCHGEFALLEN" in verdict
    assert "equal_weight" in verdict
    # Ein Durchfallen ist ein Ergebnis, kein Fehler -- das muss dastehen.
    assert "ADR-004" in verdict


def test_passed_is_strict_two_of_three_is_not_enough():
    """Der Kandidat muss JEDE Baseline schlagen, nicht die schlechteste.

    Aufbau: der Kandidat schlaegt Equal-Weight und die absichtlich schlechte
    Baseline deutlich -- und tritt gegen eine dritte an, die exakt dasselbe
    allokiert. Gleichstand heisst nicht geschlagen, die Beweislast liegt beim
    Kandidaten. Wer hier auf "die Mehrheit der Baselines" lockert, hat das
    Gate abgeschafft und nur den Namen behalten.
    """
    result = _gate(
        _good,
        [EqualWeight(), FixedWeights({"up": 0.0, "down": 1.0}), FixedWeights({"up": 1.0})],
    )

    beaten = [
        b.allocator
        for b in result.baselines
        if _beats(result.candidate_entry.metrics, b.metrics)
    ]
    assert len(beaten) == 2, "Aufbau kaputt: es sollten genau zwei geschlagen werden"
    assert not result.passed()
    assert any("fixed_weights" in reason for reason in result.blockers())


def test_dispersion_over_windows_counts_not_just_the_total():
    """Ein Vorsprung aus einem einzelnen Fenster reicht nicht.

    Der Kandidat ist in genau einem von vier Fenstern brillant und sonst
    schlecht -- ein Aufbau, der den Gesamtsharpe hochziehen kann, waehrend
    das Verfahren nichts taugt.
    """

    class OneGoodWindow(Allocator):
        name = "one_good_window"

        def __init__(self) -> None:
            self.seen = 0

        def allocate(self, ctx: AllocationContext) -> Allocation:
            self.seen += 1
            # Innerhalb eines Fensters konstant; welches Fenster es ist,
            # entscheidet der Zeitstempel.
            good = ctx.ts.timetuple().tm_yday == 6
            return {"up": 1.0, "down": 0.0} if good else {"up": 0.0, "down": 1.0}

    result = _gate(OneGoodWindow, [EqualWeight()])
    wins = result.window_wins()["equal_weight"]

    assert wins[0] <= 2, "Aufbau kaputt: der Kandidat gewinnt zu viele Fenster"
    assert not result.passed()
    assert any("Fenstern gegen" in reason for reason in result.blockers())


def test_time_in_market_keeps_incomparable_candidates_out():
    """Ein Allokator, dessen Portfolio fast nie positioniert ist, ist etwas anderes.

    ADR-016: annualisierte Kennzahlen ueber eine ueberwiegend flache Reihe
    messen vor allem Untaetigkeit. So einen Kandidaten mit einem dauernd
    investierten Feld zu vergleichen, beantwortet eine andere Frage -- und
    genau dieser Fehlschluss ist im Projekt schon einmal passiert.
    """

    class Sleeper(Long):
        """Haelt nur jeden 20. Bar eine Position, sonst flat."""

        name = "sleeper"

        def on_bar(self, symbol: str, store: FeatureStore) -> float:
            seen = self._state.get("seen", 0) + 1
            self._state["seen"] = seen
            return 1.0 if seen % 20 == 0 else 0.0

    def strategies() -> dict[str, Strategy]:
        return {"up": Long([UP], "1h"), "sleeper": Sleeper([UP], "1h")}

    result = run_gate(
        FixedWeights({"up": 0.0, "sleeper": 1.0}),
        strategies,
        _bars(),
        train_bars=TRAIN,
        test_bars=TEST,
        baselines=[EqualWeight()],
        cfg=FREE,
    )

    assert result.candidate_entry.metrics.time_in_market < 0.5
    assert not result.passed()
    assert any("Zeit im Markt" in reason for reason in result.blockers())


def test_turnover_decides_a_sharpe_tie():
    """Gleicher Sharpe, halber Umsatz -- der guenstigere gewinnt (ADR-009)."""
    base = dict(
        n_bars=100, total_return=0.1, cagr=0.1, ann_vol=0.2, sortino=1.0,
        max_drawdown=-0.1, calmar=1.0, hit_rate=0.5, time_in_market=1.0,
        ann_vol_active=0.2, n_trades=10, fees_paid=0.0,
    )
    cheap = Metrics(sharpe=1.0, turnover=50_000.0, **base)
    expensive = Metrics(sharpe=1.0, turnover=100_000.0, **base)

    assert _beats(cheap, expensive)
    assert not _beats(expensive, cheap)
    # Gleichstand in beidem heisst nicht geschlagen.
    assert not _beats(cheap, cheap)


def test_a_gate_without_baselines_is_refused():
    with pytest.raises(ValueError, match="Massstab"):
        _gate(_good, [])


# ---------------------------------------------------------------------------
# Out-of-Sample
# ---------------------------------------------------------------------------


def test_only_the_test_window_is_scored():
    """Bewertet wird ausschliesslich der Teil ab Testbeginn.

    Der Vorlauf davor existiert nur, damit Strategien und Allokatoren warm
    sind. Zaehlte er mit, waere der Vergleich in-sample -- der Kandidat hat
    diese Bars zur Entscheidungsfindung bereits gesehen.
    """
    result = _gate(_good, [EqualWeight()])

    hour = pd.Timedelta(hours=1)
    for entry in result.entries:
        for window in entry.windows:
            # Referenzpunkt plus genau `test_bars` bewertete Bars.
            assert len(window.oos_equity) == TEST + 1
            # Die Kurve ist nach Close-Zeiten indiziert: der Referenzpunkt ist
            # der Close des Bars **vor** dem Testfenster, und der faellt auf
            # dessen Open-Zeit. Ein Bar zu frueh oder zu spaet waere genau der
            # Off-by-one, der einen Bar Lookahead in jedes Fenster traegt.
            assert window.oos_equity.index.min() == pd.Timestamp(window.test_start)
            assert window.oos_equity.index.max() == pd.Timestamp(window.test_end) + hour


def test_windows_do_not_overlap():
    """Ueberlappende Testfenster wuerden denselben Zeitraum doppelt zaehlen."""
    result = _gate(_good, [EqualWeight()])
    windows = result.candidate_entry.windows

    for earlier, later in zip(windows, windows[1:]):
        assert earlier.test_end < later.test_start

    with pytest.raises(ValueError, match="ueberlappende"):
        run_gate(
            _good, _strategies, _bars(), train_bars=TRAIN, test_bars=TEST,
            step_bars=TEST // 2, baselines=[EqualWeight()], cfg=FREE,
        )


def test_insufficient_data_is_loud():
    """Ein stiller Leerlauf saehe aus wie 'nichts gehandelt', nicht wie 'zu kurz'."""
    with pytest.raises(InsufficientDataError):
        run_gate(
            _good, _strategies, _bars(120), train_bars=TRAIN, test_bars=TEST,
            baselines=[EqualWeight()], cfg=FREE,
        )


def test_warmup_covers_the_slowest_allocator():
    """Der Vorlauf muss auch den Lookback der Allokatoren fassen.

    Sonst tritt ein Allokator an, dessen Schaetzung im gesamten Testfenster
    noch gar nicht greift -- er verliert dann gegen den Kandidaten, ohne je
    gerechnet zu haben.
    """
    result = _gate(_good, [EqualWeight(), VolParity(lookback=20)])
    assert result.warmup_bars >= 20 + Long([UP], "1h").warmup_bars

    with pytest.raises(InsufficientDataError, match="Warmup"):
        run_gate(
            _good, _strategies, _bars(), train_bars=10, test_bars=TEST,
            baselines=[VolParity(lookback=200)], cfg=FREE,
        )


# ---------------------------------------------------------------------------
# Fairness
# ---------------------------------------------------------------------------


class Recorder(Allocator):
    """Gleichgewichtung, die protokolliert, was sie zu sehen bekommt.

    Gleichgewichtung, damit alle Aufzeichner identisch handeln: bliebe ein
    Unterschied im Protokoll, kaeme er aus dem Gate und nicht aus dem
    Allokator.
    """

    name = "recorder"

    def __init__(self, log: list, tag: str) -> None:
        self.log = log
        self.tag = tag

    def allocate(self, ctx: AllocationContext) -> Allocation:
        self.log.append(
            (
                self.tag,
                ctx.ts,
                round(ctx.equity, 9),
                tuple(sorted(ctx.strategy_ids)),
                tuple(np.asarray(ctx.returns[sid]).tobytes() for sid in sorted(ctx.returns)),
            )
        )
        return {sid: 1.0 / len(ctx.strategy_ids) for sid in ctx.strategy_ids}

    def describe(self) -> str:
        return f"recorder({self.tag})"


def test_every_allocator_sees_identical_bars_and_windows():
    """Fairness ist die halbe Aufgabe -- hier wird sie festgenagelt.

    Verglichen wird nicht nur die Fensterfolge, sondern auch, was in jedem
    Fenster ankommt: die Papier-Renditen aller Strategien und das
    Eigenkapital. Saehe ein Allokator andere Bars, andere Fenstergrenzen,
    einen anderen Vorlauf, eine andere Config oder einen anderen Takt,
    wichen diese Protokolle voneinander ab.
    """
    log: list = []
    result = run_gate(
        lambda: Recorder(log, "kandidat"),
        _strategies,
        _bars(),
        train_bars=TRAIN,
        test_bars=TEST,
        baselines=[lambda: Recorder(log, "a"), lambda: Recorder(log, "b")],
        cfg=FREE,
    )

    seen = {tag: [row[1:] for row in log if row[0] == tag] for tag in ("kandidat", "a", "b")}
    assert seen["kandidat"], "Der Allokator wurde nie aufgerufen"
    assert seen["kandidat"] == seen["a"] == seen["b"]

    # Gegenprobe: identische Eingaben muessen auch identische Ergebnisse geben.
    sharpes = [entry.metrics.sharpe for entry in result.entries]
    assert len(set(sharpes)) == 1


def test_identical_allocators_are_not_hidden_behind_one_name():
    """Zwei Teilnehmer mit gleichem Namen bleiben in der Tabelle unterscheidbar."""
    result = _gate(_good, [FixedWeights({"up": 1.0})])

    names = [entry.allocator for entry in result.entries]
    assert len(set(names)) == len(names)
    assert result.candidate_entry is result.entries[0]


# ---------------------------------------------------------------------------
# Zustand
# ---------------------------------------------------------------------------


class Counting(Allocator):
    """Allokator, dessen Ausgabe an einem eigenen Zaehler haengt.

    Genau die Bauform, die ueber eine geteilte Instanz leckt: das Ergebnis
    haengt daran, wieviele Fenster die Instanz vorher gesehen hat.
    """

    name = "counting"

    def __init__(self, log: list) -> None:
        self.log = log
        self.calls = 0

    def allocate(self, ctx: AllocationContext) -> Allocation:
        self.calls += 1
        self.log.append(self.calls)
        return {sid: 1.0 / len(ctx.strategy_ids) for sid in ctx.strategy_ids}


class CountingStrategy(Long):
    """Strategie, die mitzaehlt, wieviele Bars ihre Instanz gesehen hat."""

    name = "counting_strategy"

    def __init__(self, symbols, timeframe, log: list) -> None:
        super().__init__(symbols, timeframe)
        self.log = log

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        seen = self._state.get("seen", 0) + 1
        self._state["seen"] = seen
        self.log.append(seen)
        return 1.0


def test_no_allocator_state_leaks_across_windows():
    """Jedes Fenster bekommt einen frischen Allokator."""
    log: list = []
    result = _gate(lambda: Counting(log), [EqualWeight()])

    assert log.count(1) == result.n_windows, (
        "Der Zaehler startet nicht in jedem Fenster bei 1 -- der Allokator wird "
        "ueber die Fenstergrenze hinweg wiederverwendet"
    )


def test_no_strategy_state_leaks_across_windows_or_allocators():
    """Auch die Strategien sind je Fenster **und** je Allokator frisch.

    Zwei Allokatoren, die sich eine Strategie-Instanz teilen, waeren nicht
    nur ein Leck ueber Fenstergrenzen, sondern eine Kopplung zwischen den
    Teilnehmern des Vergleichs: der zweite baute auf dem Zustand auf, den der
    erste hinterlassen hat.
    """
    log: list = []
    allocators = 3  # Kandidat plus zwei Baselines
    result = run_gate(
        _good,
        lambda: {
            "up": CountingStrategy([UP], "1h", log),
            "down": CountingStrategy([DOWN], "1h", log),
        },
        _bars(),
        train_bars=TRAIN,
        test_bars=TEST,
        baselines=[EqualWeight(), VolParity(lookback=20)],
        cfg=FREE,
    )

    # Je Lauf zwei Strategien, die je bei 1 zu zaehlen beginnen.
    assert log.count(1) == result.n_windows * allocators * 2


def test_factories_that_share_instances_are_refused():
    """`lambda: instanz` sieht aus wie eine Fabrik und ist keine.

    Der Effekt eines geteilten Objekts waere kein Fehler, sondern nur ein zu
    gutes Ergebnis -- deshalb wird das beim Start abgelehnt und nicht spaeter
    beklagt.
    """
    shared = EqualWeight()
    with pytest.raises(ValueError, match="dieselbe Instanz"):
        _gate(lambda: shared, [EqualWeight()])

    strategies = _strategies()
    with pytest.raises(ValueError, match="teilt Instanzen"):
        run_gate(
            _good, lambda: dict(strategies), _bars(), train_bars=TRAIN,
            test_bars=TEST, baselines=[EqualWeight()], cfg=FREE,
        )


def test_passing_instances_is_allowed_and_still_isolated():
    """Instanzen statt Fabriken sind erlaubt -- sie werden je Lauf kopiert."""
    candidate = FixedWeights({"up": 1.0, "down": 0.0})
    result = run_gate(
        candidate, _strategies(), _bars(), train_bars=TRAIN, test_bars=TEST,
        baselines=[EqualWeight()], cfg=FREE,
    )

    assert result.passed(), result.verdict()
    assert candidate.weights == {"up": 1.0, "down": 0.0}, "Die Vorlage wurde veraendert"


def test_deterministic_across_runs():
    """Zwei Laeufe, identisches Ergebnis -- sonst ist kein Urteil belastbar."""
    first = _gate(_good, [EqualWeight(), VolParity(lookback=20)])
    second = _gate(_good, [EqualWeight(), VolParity(lookback=20)])

    assert first.table() == second.table()
    assert first.verdict() == second.verdict()
    for a, b in zip(first.entries, second.entries):
        assert a.metrics == b.metrics
        np.testing.assert_array_equal(
            a.equity["equity"].to_numpy(), b.equity["equity"].to_numpy()
        )


# ---------------------------------------------------------------------------
# Ausfaelle
# ---------------------------------------------------------------------------


class Exploding(Allocator):
    """Ein Allokator, der wirft. Ab Phase 3 ein Normalfall, kein Sonderfall."""

    name = "exploding"

    def allocate(self, ctx: AllocationContext) -> Allocation:
        raise RuntimeError("Modellantwort unbrauchbar")


def test_throwing_candidate_fails_instead_of_crashing_the_gate():
    result = _gate(Exploding, [EqualWeight(), VolParity(lookback=20)])

    assert not result.passed()
    assert result.candidate_entry.failed
    assert "Modellantwort unbrauchbar" in result.candidate_entry.error
    assert "geflogen" in result.verdict()
    # Die Baselines sind trotzdem sauber durchgelaufen -- ihr Ergebnis ist
    # nicht mit dem Kandidaten mitgerissen worden.
    assert all(not b.failed for b in result.baselines)
    assert result.table()  # Darstellung darf an einem Fehler nicht scheitern


def test_a_broken_baseline_blocks_the_verdict():
    """Ein fehlender Massstab macht das Feld unvollstaendig.

    Sonst kaeme ein Kandidat leichter durch, weil eine Baseline ausgefallen
    ist -- die bequemste denkbare Art, das Gate zu bestehen.
    """
    result = _gate(_good, [EqualWeight(), Exploding])

    assert not result.passed()
    assert any("Baseline" in reason for reason in result.blockers())


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------


def test_table_shows_more_than_sharpe():
    """Sharpe allein reicht nicht: Umsatz und Zeit im Markt gehoeren daneben."""
    result = _gate(_good, [EqualWeight(), VolParity(lookback=20)])
    table = result.table()

    for column in ("Sharpe", "Rendite", "MaxDD", "Calmar", "Zeit i.M.", "Umsatz"):
        assert column in table
    for entry in result.entries:
        assert entry.allocator in table
    # Streuung ueber die Fenster, nicht nur der Gesamtwert.
    assert "Sharpe je Fenster" in table
    assert "Fenster, die der Kandidat gewinnt" in table
    assert f"{result.candidate_entry.metrics.sharpe:.2f}" in table


def test_verdict_names_the_numbers_it_judges_on():
    result = _gate(_bad, [EqualWeight()])
    verdict = result.verdict()

    assert f"{result.candidate_entry.metrics.sharpe:.2f}" in verdict
    assert f"{result.baselines[0].metrics.sharpe:.2f}" in verdict


def test_window_frame_has_one_row_per_window_and_one_column_per_allocator():
    result = _gate(_good, [EqualWeight(), VolParity(lookback=20)])
    frame = result.window_frame()

    assert len(frame) == result.n_windows
    for entry in result.entries:
        assert entry.allocator in frame.columns
