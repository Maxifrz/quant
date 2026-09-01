"""Der Vorlauf wird nicht bewertet -- und soll deshalb nichts kosten.

Im Gate laeuft jedes Fenster ab `test_start - warmup_slots`, bewertet wird
aber erst ab `test_start`. Der Vorlauf richtet sich nach der langsamsten
Komponente im Feld, nicht nach dem Kandidaten: ein LLM-Allokator, der selbst
96 Bars braucht, wurde ueber 914 Slots befragt, weil eine Baseline so lange
warmlaeuft. Sechs von zehn Modellaufrufen wurden bezahlt und weggeworfen.

Geprueft wird hier das **Verhalten**, nicht die Verdrahtung:

    * das Flag ist per Default aus und aendert an bestehendem Code nichts
    * es steht genau im Vorlauf und sonst nirgends
    * der LLM-Allokator fragt das Modell im Vorlauf nicht
    * die Ersparnis ist beziffert, nicht behauptet
    * und -- der wichtigste Test -- die Baselines rechnen bitgenau dasselbe
      wie vorher. Bewegt sich dort etwas, ist die Zeitachse verrutscht, und
      dann ist jeder Gate-Vergleich seit dieser Aenderung wertlos.
"""

from __future__ import annotations

import datetime as dt
import re
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from qt.backtest.portfolio_engine import run_portfolio_backtest
from qt.core.config import BacktestConfig, CostConfig
from qt.features.registry import FeatureStore
from qt.llm.client import StubClient
from qt.portfolio.base import Allocation, AllocationContext, Allocator
from qt.portfolio.baselines import EqualWeight, VolParity
from qt.portfolio.llm_allocator import LLMAllocator
from qt.strategy.base import Strategy
from tests.conftest import START, make_bars

FREE = BacktestConfig(costs=CostConfig(taker_fee_bps=0, half_spread_bps=0, slippage_bps=0))

UP = "UP/USD"
DOWN = "DOWN/USD"
TF = "1h"
N_BARS = 500

# Fenstergeometrie der Gate-Tests unten. Bewusst so gewaehlt, dass eine
# Baseline (`VolParity(lookback=120)`) einen laengeren Vorlauf erzwingt als
# das Testfenster lang ist -- das ist genau die Lage, die den Befund
# ausgeloest hat, nur klein genug fuer einen Test.
TRAIN, TEST, EMBARGO = 140, 100, 12
SLOW_LOOKBACK = 120


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------


class Long(Strategy):
    """Immer voll long im eigenen Symbol."""

    name = "long"

    @property
    def warmup_bars(self) -> int:
        return 5

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        return 1.0


def _bars(n: int = N_BARS) -> dict[tuple[str, str], list]:
    rng_up = np.random.default_rng(11)
    rng_down = np.random.default_rng(12)
    up = 100 * np.cumprod(1 + rng_up.normal(0.002, 0.01, n))
    down = 100 * np.cumprod(1 + rng_down.normal(-0.002, 0.01, n))
    return {
        (UP, TF): make_bars(n, symbol=UP, timeframe=TF, prices=up),
        (DOWN, TF): make_bars(n, symbol=DOWN, timeframe=TF, prices=down),
    }


def _strategies() -> dict[str, Strategy]:
    return {"up": Long([UP], TF), "down": Long([DOWN], TF)}


class Spy(Allocator):
    """Gleichgewichtet und schreibt mit, was er zum Zeitpunkt gesehen hat.

    Nur so laesst sich pruefen, dass das Flag genau im Vorlauf steht: von
    aussen ist ein Allokator, der es ignoriert, nicht von einem zu
    unterscheiden, der es nie gesetzt bekommt.
    """

    name = "spy"

    def __init__(self) -> None:
        self.seen: list[tuple[dt.datetime, bool]] = []

    def allocate(self, ctx: AllocationContext) -> Allocation:
        self.seen.append((ctx.ts, ctx.is_warmup))
        n = len(ctx.strategy_ids)
        return {sid: 1.0 / n for sid in ctx.strategy_ids}


class CountingClient:
    """Zaehlt Modellaufrufe, antwortet sonst wie `StubClient`.

    Kein Mock des Allokators: gezaehlt wird die Stelle, die im Ernstfall Geld
    kostet -- der Aufruf beim Anbieter.
    """

    model = "counting-stub"

    def __init__(self) -> None:
        self._inner = StubClient()
        self.calls = 0

    def propose(self, briefing):
        self.calls += 1
        return self._inner.propose(briefing)


class RecordingLLM(LLMAllocator):
    """LLM-Allokator, der mitschreibt, zu welchem `ts` das Modell wirklich lief."""

    def __init__(self, client: CountingClient, log: list[dt.datetime]) -> None:
        super().__init__(client=client, min_history=4)
        self._log = log

    def allocate(self, ctx: AllocationContext) -> Allocation:
        before = self.client.calls
        out = super().allocate(ctx)
        if self.client.calls > before:
            self._log.append(ctx.ts)
        return out


# ---------------------------------------------------------------------------
# 1 -- das Flag aendert an bestehendem Code nichts
# ---------------------------------------------------------------------------


def test_is_warmup_defaults_to_false_and_old_construction_still_works():
    """Ein Kontext, der das Feld nicht kennt, verhaelt sich wie bisher.

    Wuerde der Default kippen, faellt jeder bestehende Allokator ohne
    Fehlermeldung in seinen Sparmodus -- und das faellt erst auf der
    Ergebnisseite auf.
    """
    ctx = AllocationContext(
        ts=START,
        strategy_ids=["a", "b"],
        returns={"a": np.zeros(10), "b": np.zeros(10)},
        equity=10_000.0,
    )

    assert ctx.is_warmup is False
    assert ctx.history_length() == 10
    assert ctx.lengths() == {"a": 10, "b": 10}


def test_llm_allocator_asks_the_model_when_the_flag_is_absent():
    """Ohne gesetztes Flag bleibt der teure Pfad der Normalfall."""
    client = CountingClient()
    allocator = LLMAllocator(client=client, min_history=4)

    allocator.allocate(
        AllocationContext(
            ts=START,
            strategy_ids=["a", "b"],
            returns={"a": np.zeros(10), "b": np.zeros(10)},
            equity=10_000.0,
        )
    )

    assert client.calls == 1
    assert allocator.telemetry.warmup_skips == 0


# ---------------------------------------------------------------------------
# 2 -- das Flag steht genau im Vorlauf
# ---------------------------------------------------------------------------


def test_without_evaluate_from_the_flag_is_never_set():
    spy = Spy()

    run_portfolio_backtest(_strategies(), _bars(200), spy, cfg=FREE)

    assert spy.seen, "Aufbau kaputt: der Allokator wurde nie befragt"
    assert not any(flag for _, flag in spy.seen)


def test_with_evaluate_from_the_flag_marks_exactly_the_run_up():
    """Genau `ts < evaluate_from` -- keine Bar mehr, keine weniger.

    Ein Off-by-one waere hier folgenlos fuer die Buchhaltung und deshalb
    unsichtbar: er verschoebe nur, ab wann der Allokator sich Muehe gibt.
    """
    spy = Spy()
    bars = _bars(200)
    cut = START + timedelta(hours=120)

    run_portfolio_backtest(_strategies(), bars, spy, cfg=FREE, evaluate_from=cut)

    assert spy.seen
    for ts, flag in spy.seen:
        assert flag == (ts < cut), f"{ts} falsch einsortiert"
    # Beide Seiten muessen vorkommen, sonst prueft die Schleife nichts.
    assert any(flag for _, flag in spy.seen)
    assert any(not flag for _, flag in spy.seen)


def test_evaluate_from_before_the_first_bar_marks_nothing():
    """Ein Lauf ohne Vorlauf hat keinen Vorlauf -- und keine Sonderbehandlung."""
    spy = Spy()

    run_portfolio_backtest(
        _strategies(), _bars(200), spy, cfg=FREE, evaluate_from=START
    )

    assert spy.seen
    assert not any(flag for _, flag in spy.seen)


# ---------------------------------------------------------------------------
# 3 -- der LLM-Allokator fragt das Modell im Vorlauf nicht
# ---------------------------------------------------------------------------


def test_llm_allocator_asks_nothing_before_evaluate_from():
    """Null Aufrufe davor, mindestens einer danach.

    "Weniger Aufrufe" waere zu schwach: die interessante Aussage ist, dass im
    Vorlauf **gar** nichts beim Anbieter landet.
    """
    client = CountingClient()
    log: list[dt.datetime] = []
    allocator = RecordingLLM(client, log)
    cut = START + timedelta(hours=150)

    run_portfolio_backtest(
        _strategies(), _bars(300), allocator, cfg=FREE, evaluate_from=cut
    )

    assert client.calls >= 1, "Aufbau kaputt: das Modell lief nie"
    assert log == sorted(log)
    assert all(ts >= cut for ts in log)
    assert allocator.telemetry.warmup_skips > 0
    # Der uebersprungene Aufruf darf die Kennzahl nicht verwaessern, an der
    # man erkennt, ob der Allokator heimlich eine Baseline ist.
    assert allocator.telemetry.calls == client.calls
    assert allocator.telemetry.fallback_rate == 0.0


def test_warmup_skips_show_up_in_the_summary():
    """Eine Einsparung, die niemand sieht, wird versehentlich zurueckgebaut."""
    allocator = LLMAllocator(client=CountingClient(), min_history=4)
    ctx = AllocationContext(
        ts=START,
        strategy_ids=["a", "b"],
        returns={"a": np.zeros(10), "b": np.zeros(10)},
        equity=10_000.0,
        is_warmup=True,
    )

    assert allocator.allocate(ctx) == {"a": 0.5, "b": 0.5}
    summary = allocator.telemetry.summary()

    assert re.search(r"Vorlauf\s+1\b", summary), summary


# ---------------------------------------------------------------------------
# 4 und 5 -- der Gate-Lauf
# ---------------------------------------------------------------------------


def _gate_with_baselines(candidate, monkeypatch, *, plumbing: bool):
    """Gate-Lauf, wahlweise mit oder ohne durchgereichtes `evaluate_from`.

    Der Vergleich "vorher/nachher" braucht beide Zustaende im selben Lauf.
    Ohne `plumbing` verhaelt sich das Gate exakt wie vor der Aenderung: der
    Allokator erfaehrt nie, dass er im Vorlauf steht.
    """
    from qt.portfolio import gate as gate_mod

    # `undo()` statt "einfach nichts tun": beide Zustaende kommen im selben
    # Test vor, und ein stehengebliebener Patch aus dem vorherigen Aufruf
    # liesse den Vergleich zweimal dasselbe messen.
    monkeypatch.undo()
    if not plumbing:
        original = gate_mod.run_portfolio_backtest

        def blind(*args, evaluate_from=None, **kwargs):
            return original(*args, **kwargs)

        monkeypatch.setattr(gate_mod, "run_portfolio_backtest", blind)

    return gate_mod.run_gate(
        candidate,
        _strategies,
        _bars(),
        train_bars=TRAIN,
        test_bars=TEST,
        embargo_bars=EMBARGO,
        baselines=[EqualWeight(), VolParity(lookback=SLOW_LOOKBACK)],
        cfg=FREE,
        candidate_name="kandidat",
    )


@pytest.mark.slow
def test_gate_run_stops_paying_for_the_run_up(monkeypatch):
    """Beziffert: die Modellaufrufe sinken, und keiner liegt mehr im Vorlauf.

    Der Vorlauf ist hier laenger als das Testfenster, weil eine Baseline ihn
    diktiert. Genau dafuer wurde bisher ein Sprachmodell bezahlt.
    """
    # Je Fenster eine frische Instanz -- das Gate verlangt das, also wird je
    # Instanz gezaehlt statt global. Anders liesse sich ein Aufruf nicht dem
    # Fenster zuordnen, dessen Vorlauf er trifft.
    made: dict[str, list[tuple[CountingClient, list[dt.datetime]]]] = {}

    def make(tag: str):
        def factory():
            client = CountingClient()
            log: list[dt.datetime] = []
            made.setdefault(tag, []).append((client, log))
            return RecordingLLM(client, log)

        return factory

    before = _gate_with_baselines(make("before"), monkeypatch, plumbing=False)
    n_before = sum(c.calls for c, _ in made["before"])

    after = _gate_with_baselines(make("after"), monkeypatch, plumbing=True)
    n_after = sum(c.calls for c, _ in made["after"])

    assert before.candidate_entry.error is None, before.candidate_entry.error
    assert after.candidate_entry.error is None, after.candidate_entry.error
    assert n_before > 0
    assert n_after < n_before, f"keine Einsparung: {n_before} -> {n_after}"

    # Und zwar nicht irgendwo gespart, sondern genau im Vorlauf: jeder
    # verbliebene Aufruf liegt im bewerteten Teil seines Fensters.
    starts = [w.test_start for w in after.candidate_entry.windows]
    runs = [log for _, log in made["after"] if log]
    assert len(runs) == len(starts), (len(runs), len(starts))
    for log, test_start in zip(runs, starts, strict=True):
        assert all(ts >= test_start for ts in log)

    # Vorher lag der ueberwiegende Teil davor -- sonst misst der Test nichts.
    runs_before = [log for _, log in made["before"] if log]
    early = sum(
        sum(1 for ts in log if ts < start)
        for log, start in zip(runs_before, starts, strict=True)
    )
    assert early > 0
    assert n_before - n_after == early


@pytest.mark.slow
def test_baselines_are_bit_identical_with_and_without_evaluate_from(monkeypatch):
    """Der wichtigste Test: an den Baselines darf sich **nichts** bewegen.

    Sie ignorieren `is_warmup`, also muss ihr Ergebnis mit und ohne
    durchgereichtes `evaluate_from` bitgleich sein. Weicht hier etwas ab, ist
    nicht der Allokator sparsam geworden, sondern die Zeitachse verrutscht --
    und dann sind alle Gate-Vergleiche danach wertlos, ohne dass irgendwo ein
    Fehler auftaucht.
    """
    before = _gate_with_baselines(EqualWeight, monkeypatch, plumbing=False)
    after = _gate_with_baselines(EqualWeight, monkeypatch, plumbing=True)

    baselines_before = {b.allocator: b for b in before.baselines}
    baselines_after = {b.allocator: b for b in after.baselines}
    assert baselines_before.keys() == baselines_after.keys()
    assert baselines_before, "Aufbau kaputt: keine Baselines im Lauf"

    for name, entry in baselines_before.items():
        other = baselines_after[name]
        pd.testing.assert_frame_equal(entry.equity, other.equity, check_exact=True)
        assert entry.metrics == other.metrics, name
        assert [w.test_start for w in entry.windows] == [
            w.test_start for w in other.windows
        ]
        for w_before, w_after in zip(entry.windows, other.windows, strict=True):
            pd.testing.assert_series_equal(
                w_before.oos_equity, w_after.oos_equity, check_exact=True
            )


def test_baseline_engine_run_is_bit_identical_with_and_without_evaluate_from():
    """Dasselbe eine Ebene tiefer, ohne Gate und ohne Monkeypatch.

    Wenn `evaluate_from` die Buchhaltung anfasst statt nur den Allokator zu
    informieren, faellt es hier auf -- unabhaengig davon, ob der Test oben
    das Gate richtig nachbaut.
    """
    bars = _bars(300)
    cut = START + timedelta(hours=150)

    plain = run_portfolio_backtest(_strategies(), bars, EqualWeight(), cfg=FREE)
    marked = run_portfolio_backtest(
        _strategies(), bars, EqualWeight(), cfg=FREE, evaluate_from=cut
    )

    pd.testing.assert_frame_equal(plain.equity, marked.equity, check_exact=True)
    pd.testing.assert_frame_equal(
        plain.allocations, marked.allocations, check_exact=True
    )
    assert plain.fills == marked.fills
    assert plain.warmup_end == marked.warmup_end
