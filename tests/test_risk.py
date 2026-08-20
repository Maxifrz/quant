"""Tests der Risk-Engine.

Diese Komponente ist die Sicherung, nicht das Feature: ab Phase 3 sitzt
davor ein LLM, dessen Output ein Vorschlag ist. Getestet wird deshalb nicht
nur, dass die Limits im Normalfall greifen, sondern vor allem, dass sie im
Missbrauchsfall greifen -- `inf`, `nan`, absurde Betraege, und ein
Kill-Switch, der nach der Ausloesung nicht von selbst aufgeht.

Alle Zahlen sind analytisch nachrechenbar. Ein Risikotest, dessen
Erwartungswert aus dem Code abgeschrieben wurde, testet nichts.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pytest

from qt.core.types import bars_per_year
from qt.features import ta
from qt.portfolio.base import RiskState
from qt.portfolio.risk import RiskConfig, RiskEngine, realised_vol_map

TS = datetime(2020, 1, 1, tzinfo=timezone.utc)


def make_state(
    vols: dict[str, float] | None = None,
    equity: float = 100_000.0,
    peak: float = 100_000.0,
    halted: bool = False,
) -> RiskState:
    return RiskState(
        ts=TS,
        equity=equity,
        peak_equity=peak,
        realised_vol=vols or {},
        halted=halted,
    )


def loose_cfg(**overrides: object) -> RiskConfig:
    """Config, bei der ausser dem getesteten Limit nichts greift.

    Damit misst jeder Test genau ein Limit -- sonst weiss man bei einem
    roten Test nicht, welcher Cap zugeschlagen hat.
    """
    base: dict[str, object] = {
        "max_weight_per_symbol": 100.0,
        "max_gross_exposure": 100.0,
        "max_drawdown": 0.99,
        "max_vol_scale": 1e6,
    }
    base.update(overrides)
    return RiskConfig(**base)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Vol-Targeting
# ----------------------------------------------------------------------


def test_vol_targeting_scales_inverse_to_volatility():
    """Doppelte Ziel-Vola -> halbes Gewicht. Exakt, nicht ungefaehr."""
    engine = RiskEngine(loose_cfg(target_vol=0.20))
    out, reasons = engine.apply({"BTC/USD": 0.2}, make_state({"BTC/USD": 0.40}))

    assert out["BTC/USD"] == pytest.approx(0.1)
    assert reasons == [], "Routine-Skalierung im Rahmen ist kein Eingriff"


def test_vol_targeting_scales_up_calm_symbols_within_the_cap():
    engine = RiskEngine(loose_cfg(target_vol=0.20, max_vol_scale=4.0))
    out, _ = engine.apply({"BTC/USD": 0.1}, make_state({"BTC/USD": 0.10}))

    assert out["BTC/USD"] == pytest.approx(0.2)  # Faktor 2.0


def test_vol_scale_cap_binds_on_very_low_volatility():
    """Bei 1% Vola waere der Faktor 20 -- der Cap ist Pflicht, kein Detail."""
    engine = RiskEngine(loose_cfg(target_vol=0.20, max_vol_scale=1.5))
    out, reasons = engine.apply({"BTC/USD": 0.1}, make_state({"BTC/USD": 0.01}))

    assert out["BTC/USD"] == pytest.approx(0.15), "Cap 1.5x, nicht 20x"
    assert any("gedeckelt" in r for r in reasons)


@pytest.mark.parametrize("vol", [math.nan, 0.0, -0.3, math.inf])
def test_unknown_or_degenerate_vol_never_scales_up(vol: float):
    """Unbekannt ist kein Synonym fuer niedrig.

    Der gefaehrlichste Fehler an dieser Stelle waere, eine fehlende Vola
    als 0 zu lesen und die Position dann maximal hochzuhebeln.
    """
    engine = RiskEngine(loose_cfg(target_vol=0.20))
    out, reasons = engine.apply({"BTC/USD": 0.3}, make_state({"BTC/USD": vol}))

    assert out["BTC/USD"] == pytest.approx(0.3)
    assert any("Vola unbekannt" in r for r in reasons)


def test_missing_symbol_in_vol_map_is_treated_as_unknown():
    engine = RiskEngine(loose_cfg())
    out, reasons = engine.apply({"ETH/USD": 0.3}, make_state({"BTC/USD": 0.2}))

    assert out["ETH/USD"] == pytest.approx(0.3)
    assert any("Vola unbekannt" in r for r in reasons)


def test_realised_vol_map_reuses_ta():
    """Die Engine darf keine zweite Vola-Definition mitbringen."""
    rng = np.random.default_rng(7)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.01, 400))
    cfg = RiskConfig(vol_lookback=96)

    got = realised_vol_map({"BTC/USD": closes}, "1h", cfg)
    want = ta.realised_vol(closes, 96, bars_per_year("1h"))

    assert got["BTC/USD"] == want


def test_realised_vol_map_gives_nan_on_short_history():
    short = np.array([100.0, 101.0, 102.0])
    got = realised_vol_map({"BTC/USD": short}, "1h", RiskConfig(vol_lookback=96))

    assert math.isnan(got["BTC/USD"])


# ----------------------------------------------------------------------
# Max-Gewicht je Symbol
# ----------------------------------------------------------------------


def test_per_symbol_cap_binds_and_keeps_the_sign():
    cfg = loose_cfg(max_weight_per_symbol=0.25)
    engine = RiskEngine(cfg)
    vols = {"BTC/USD": 0.20, "ETH/USD": 0.20}  # Skalierung exakt 1.0
    out, reasons = engine.apply({"BTC/USD": 0.9, "ETH/USD": -0.9}, make_state(vols))

    assert out["BTC/USD"] == pytest.approx(0.25)
    assert out["ETH/USD"] == pytest.approx(-0.25), "Short bleibt short"
    assert len([r for r in reasons if "Symbol-Cap" in r]) == 2


def test_per_symbol_cap_leaves_small_weights_alone():
    engine = RiskEngine(loose_cfg(max_weight_per_symbol=0.25))
    out, reasons = engine.apply({"BTC/USD": 0.1}, make_state({"BTC/USD": 0.20}))

    assert out["BTC/USD"] == pytest.approx(0.1)
    assert reasons == []


def test_vol_targeting_runs_before_the_symbol_cap():
    """Reihenfolge-Test: hochskaliert wird zuerst, gecappt danach.

    Liefe der Cap zuerst, koennte das Vol-Targeting ihn hinterher wieder
    durchbrechen -- die Grenze waere dann keine.
    """
    cfg = loose_cfg(target_vol=0.20, max_vol_scale=4.0, max_weight_per_symbol=0.25)
    engine = RiskEngine(cfg)
    # Vol 0.05 -> Faktor 4.0 -> 0.2 * 4 = 0.8, danach Cap auf 0.25.
    out, _ = engine.apply({"BTC/USD": 0.2}, make_state({"BTC/USD": 0.05}))

    assert out["BTC/USD"] == pytest.approx(0.25)


# ----------------------------------------------------------------------
# Brutto-Exposure
# ----------------------------------------------------------------------


def test_gross_exposure_is_scaled_proportionally():
    """Ueberschreitung wird skaliert, nicht abgeschnitten.

    Abschneiden wuerde die relative Gewichtung des Allokators verfaelschen:
    die Risikoschicht darf das Budget kuerzen, nicht die Meinung
    umschreiben.
    """
    cfg = loose_cfg(max_gross_exposure=1.0)
    engine = RiskEngine(cfg)
    vols = dict.fromkeys(["BTC/USD", "ETH/USD", "SOL/USD"], 0.20)
    proposal = {"BTC/USD": 1.0, "ETH/USD": 0.5, "SOL/USD": -0.5}  # brutto 2.0

    out, reasons = engine.apply(proposal, make_state(vols))

    assert sum(abs(w) for w in out.values()) == pytest.approx(1.0)
    assert out["BTC/USD"] == pytest.approx(0.5)
    assert out["ETH/USD"] == pytest.approx(0.25)
    assert out["SOL/USD"] == pytest.approx(-0.25)
    # Relative Gewichtung unveraendert.
    assert out["ETH/USD"] / out["BTC/USD"] == pytest.approx(
        proposal["ETH/USD"] / proposal["BTC/USD"]
    )
    assert any("Brutto-Exposure" in r for r in reasons)


def test_gross_exposure_below_limit_is_untouched():
    engine = RiskEngine(loose_cfg(max_gross_exposure=1.0))
    vols = {"BTC/USD": 0.20, "ETH/USD": 0.20}
    out, reasons = engine.apply({"BTC/USD": 0.3, "ETH/USD": -0.2}, make_state(vols))

    assert out == pytest.approx({"BTC/USD": 0.3, "ETH/USD": -0.2})
    assert reasons == []


def test_gross_limit_holds_after_every_other_step():
    """Das Brutto-Limit muss beim Verlassen der Funktion gelten -- immer."""
    cfg = RiskConfig(
        target_vol=0.20,
        max_vol_scale=3.0,
        max_weight_per_symbol=0.5,
        max_gross_exposure=0.8,
    )
    engine = RiskEngine(cfg)
    vols = {"BTC/USD": 0.02, "ETH/USD": 0.02, "SOL/USD": math.nan}
    out, _ = engine.apply(
        {"BTC/USD": 0.9, "ETH/USD": 0.9, "SOL/USD": 0.9}, make_state(vols)
    )

    assert sum(abs(w) for w in out.values()) <= 0.8 + 1e-12
    assert all(abs(w) <= 0.5 + 1e-12 for w in out.values())


# ----------------------------------------------------------------------
# Kill-Switch -- der wichtigste Teil dieser Datei
# ----------------------------------------------------------------------


def test_kill_switch_triggers_at_the_limit():
    engine = RiskEngine(RiskConfig(max_drawdown=0.20))
    state = make_state({"BTC/USD": 0.20}, equity=80_000.0, peak=100_000.0)

    out, reasons = engine.apply({"BTC/USD": 0.2}, state)

    assert out == {"BTC/USD": 0.0}
    assert engine.halted
    assert state.halted, "Der Halt muss auch im State sichtbar sein"
    assert any("Kill-Switch" in r for r in reasons)


def test_kill_switch_does_not_trigger_just_above_the_limit():
    engine = RiskEngine(RiskConfig(max_drawdown=0.20, max_weight_per_symbol=1.0))
    state = make_state({"BTC/USD": 0.20}, equity=80_001.0, peak=100_000.0)

    out, _ = engine.apply({"BTC/USD": 0.2}, state)

    assert out["BTC/USD"] == pytest.approx(0.2)
    assert not engine.halted


def test_kill_switch_stays_active_when_equity_recovers():
    """Der wichtigste Test dieser Datei.

    Ein Kill-Switch, der sich bei erholter Equity selbst zurueckstellt, ist
    keiner: er laesst genau in dem Moment wieder handeln, in dem noch
    niemand geklaert hat, warum das Konto ueberhaupt so weit gefallen ist.
    Zurueckgesetzt wird ausschliesslich manuell.
    """
    engine = RiskEngine(RiskConfig(max_drawdown=0.20))
    engine.apply({"BTC/USD": 0.2}, make_state({"BTC/USD": 0.20}, equity=70_000.0))

    assert engine.halted

    # Frischer State, volle Erholung, sogar neuer Hoechststand.
    recovered = make_state({"BTC/USD": 0.20}, equity=120_000.0, peak=120_000.0)
    out, reasons = engine.apply({"BTC/USD": 0.2}, recovered)

    assert recovered.drawdown == 0.0, "Testaufbau: kein Drawdown mehr"
    assert out == {"BTC/USD": 0.0}, "Kill-Switch hat sich selbst geloest"
    assert engine.halted
    assert any("Kill-Switch" in r for r in reasons)


def test_reset_releases_the_kill_switch():
    engine = RiskEngine(RiskConfig(max_drawdown=0.20, max_weight_per_symbol=1.0))
    engine.apply({"BTC/USD": 0.2}, make_state({"BTC/USD": 0.20}, equity=70_000.0))
    assert engine.halted

    engine.reset()

    assert not engine.halted
    out, _ = engine.apply(
        {"BTC/USD": 0.2}, make_state({"BTC/USD": 0.20}, equity=100_000.0)
    )
    assert out["BTC/USD"] == pytest.approx(0.2)


def test_externally_set_halt_is_respected_and_latched():
    """Ein Halt von aussen (z.B. Live-Runner) gilt und bleibt kleben."""
    engine = RiskEngine(RiskConfig())
    out, reasons = engine.apply({"BTC/USD": 0.2}, make_state(halted=True))

    assert out == {"BTC/USD": 0.0}
    assert any("Kill-Switch" in r for r in reasons)
    # Auch ohne den externen Halt bleibt die Engine gesperrt.
    out, _ = engine.apply({"BTC/USD": 0.2}, make_state())
    assert out == {"BTC/USD": 0.0}


def test_halt_returns_explicit_zeros_for_every_symbol():
    """Fehlende Schluessel liessen sich als 'Position stehen lassen' lesen."""
    engine = RiskEngine(RiskConfig(max_drawdown=0.20))
    proposal = {"BTC/USD": 0.2, "ETH/USD": -0.3, "SOL/USD": math.nan}
    out, _ = engine.apply(proposal, make_state(equity=50_000.0))

    assert set(out) == set(proposal)
    assert all(w == 0.0 for w in out.values())


@pytest.mark.parametrize(
    ("equity", "peak"),
    [(math.nan, 100_000.0), (100_000.0, math.nan), (math.inf, 100_000.0)],
)
def test_unusable_equity_halts(equity: float, peak: float):
    """Wer den Drawdown nicht kennt, weiss nicht ob er handeln darf."""
    engine = RiskEngine(RiskConfig())
    out, reasons = engine.apply({"BTC/USD": 0.2}, make_state(equity=equity, peak=peak))

    assert out == {"BTC/USD": 0.0}
    assert engine.halted
    assert any("Kill-Switch" in r for r in reasons)


@pytest.mark.parametrize(
    ("equity", "peak"),
    [(-5_000.0, 0.0), (0.0, 100_000.0), (50_000.0, 0.0)],
)
def test_non_positive_equity_halts(equity: float, peak: float):
    """Deckt eine Luecke in `RiskState.drawdown` ab.

    Die Property liefert bei `peak_equity <= 0` eine 0.0 -- also "kein
    Drawdown". Ein ruiniertes Konto kaeme damit ungebremst durch. Die
    Sicherung darf sich nicht darauf verlassen, dass der Aufrufer saubere
    Zahlen liefert.
    """
    engine = RiskEngine(RiskConfig())
    state = make_state({"BTC/USD": 0.20}, equity=equity, peak=peak)

    out, reasons = engine.apply({"BTC/USD": 0.2}, state)

    assert out == {"BTC/USD": 0.0}
    assert engine.halted
    assert any("Kill-Switch" in r for r in reasons)


# ----------------------------------------------------------------------
# Positionszahl-Cap
# ----------------------------------------------------------------------


def test_position_count_cap_keeps_the_largest_positions():
    cfg = loose_cfg(max_positions=2)
    engine = RiskEngine(cfg)
    vols = dict.fromkeys(["A", "B", "C", "D"], 0.20)
    out, reasons = engine.apply(
        {"A": 0.1, "B": -0.4, "C": 0.3, "D": 0.05}, make_state(vols)
    )

    assert out["B"] == pytest.approx(-0.4)
    assert out["C"] == pytest.approx(0.3)
    assert out["A"] == 0.0 and out["D"] == 0.0
    assert any("Positionszahl" in r for r in reasons)


def test_position_count_cap_is_silent_when_not_binding():
    engine = RiskEngine(loose_cfg(max_positions=3))
    vols = {"A": 0.20, "B": 0.20}
    out, reasons = engine.apply({"A": 0.1, "B": 0.2}, make_state(vols))

    assert out == pytest.approx({"A": 0.1, "B": 0.2})
    assert reasons == []


def test_position_count_cap_is_deterministic_on_ties():
    """Gleichstand darf nicht von der Dict-Reihenfolge abhaengen."""
    cfg = loose_cfg(max_positions=1)
    vols = dict.fromkeys(["A", "B"], 0.20)
    first, _ = RiskEngine(cfg).apply({"A": 0.2, "B": 0.2}, make_state(vols))
    second, _ = RiskEngine(cfg).apply({"B": 0.2, "A": 0.2}, make_state(vols))

    assert first == second


# ----------------------------------------------------------------------
# Adversarialer Input -- der Fall, fuer den die Engine existiert
# ----------------------------------------------------------------------


def test_garbage_weights_never_reach_the_output():
    """Simuliert ein LLM, das entgleist ist.

    `inf`, `nan` und ein Gewicht von 1e9 duerfen weder durchkommen noch die
    Rechnung der uebrigen Symbole vergiften.
    """
    cfg = RiskConfig(max_weight_per_symbol=0.25, max_gross_exposure=1.0)
    engine = RiskEngine(cfg)
    proposal = {
        "BTC/USD": math.inf,
        "ETH/USD": -math.inf,
        "SOL/USD": math.nan,
        "DOGE/USD": 1e9,
        "XRP/USD": -1e300,
        "LTC/USD": 0.2,
    }
    vols = dict.fromkeys(proposal, 0.20)

    out, reasons = engine.apply(proposal, make_state(vols))

    assert all(math.isfinite(w) for w in out.values())
    assert all(abs(w) <= 0.25 + 1e-12 for w in out.values())
    assert sum(abs(w) for w in out.values()) <= 1.0 + 1e-12
    for symbol in ("BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "XRP/USD"):
        assert out[symbol] == 0.0, f"{symbol} haette verworfen werden muessen"
    assert out["LTC/USD"] == pytest.approx(0.2), "Der gesunde Vorschlag bleibt"
    # Jeder verworfene Betrag ausser dem stillen nan wird protokolliert.
    assert len([r for r in reasons if "verworfen" in r]) == 4


def test_absurd_weight_is_dropped_not_capped():
    """1e9 ist kein zu grosser Vorschlag, sondern ein Fehler.

    Ein Fehler wird zu 0, nicht zum Maximum -- sonst belohnt die Engine
    Unsinn mit der groesstmoeglichen Position.
    """
    engine = RiskEngine(RiskConfig(max_weight_per_symbol=0.25))
    out, _ = engine.apply({"BTC/USD": 1e9}, make_state({"BTC/USD": 0.20}))

    assert out["BTC/USD"] == 0.0


def test_plausible_overshoot_is_capped_not_dropped():
    """Die Abgrenzung zum Test darueber: 2.0 ist zu gross, aber gemeint."""
    engine = RiskEngine(RiskConfig(max_weight_per_symbol=0.25))
    out, _ = engine.apply({"BTC/USD": 2.0}, make_state({"BTC/USD": 0.20}))

    assert out["BTC/USD"] == pytest.approx(0.25)


def test_nan_weight_is_silently_zero():
    """`nan` heisst systemweit 'keine Meinung' -- kein Befund, kein Rauschen."""
    engine = RiskEngine(RiskConfig())
    out, reasons = engine.apply({"BTC/USD": math.nan}, make_state())

    assert out == {"BTC/USD": 0.0}
    assert reasons == []


@pytest.mark.parametrize("junk", [None, "stark long", [0.3], {}])
def test_non_numeric_weight_is_dropped_instead_of_raising(junk: object):
    """LLM-Output, der durch eine lueckenhafte Validierung gerutscht ist.

    Die Risikoschicht ist der letzte Halt vor dem Broker -- sie darf an
    kaputtem Input nicht sterben, sondern muss flach gehen.
    """
    engine = RiskEngine(RiskConfig())
    proposal: dict[str, float] = {"BTC/USD": junk}  # type: ignore[dict-item]
    out, reasons = engine.apply(proposal, make_state())

    assert out == {"BTC/USD": 0.0}
    assert any("keine Zahl" in r for r in reasons)


def test_empty_proposal_does_not_crash():
    engine = RiskEngine(RiskConfig())
    out, reasons = engine.apply({}, make_state())

    assert out == {}
    assert reasons == []


def test_all_zero_proposal_stays_flat():
    engine = RiskEngine(RiskConfig())
    out, reasons = engine.apply({"BTC/USD": 0.0}, make_state({"BTC/USD": 0.20}))

    assert out == {"BTC/USD": 0.0}
    assert reasons == []


# ----------------------------------------------------------------------
# Transparenz und Determinismus
# ----------------------------------------------------------------------


def test_every_intervention_is_reported():
    """Jeder Eingriff muss eine Begruendung erzeugen.

    Ein Risikoeingriff, den niemand sieht, wird nie untersucht.
    """
    cfg = RiskConfig(
        target_vol=0.20,
        max_vol_scale=1.5,
        max_weight_per_symbol=0.25,
        max_gross_exposure=0.4,
        max_positions=3,
    )
    engine = RiskEngine(cfg)
    proposal = {
        "CALM": 0.2,  # Vol-Cap
        "BIG": 0.9,  # Symbol-Cap
        "JUNK": math.inf,  # verworfen
        "UNKNOWN": 0.1,  # Vola unbekannt
        "SMALL": 0.05,  # faellt dem Positionszahl-Cap zum Opfer
    }
    vols = {"CALM": 0.01, "BIG": 0.20, "JUNK": 0.20, "SMALL": 0.20}

    _, reasons = engine.apply(proposal, make_state(vols))
    joined = " | ".join(reasons)

    for marker in (
        "gedeckelt",
        "Symbol-Cap",
        "verworfen",
        "Vola unbekannt",
        "Positionszahl",
        "Brutto-Exposure",
    ):
        assert marker in joined, f"kein Hinweis auf {marker}: {joined}"


def test_apply_is_deterministic():
    """Zwei gleiche Engines, gleicher Input, bitgleicher Output."""
    cfg = RiskConfig(max_positions=2)
    proposal = {"BTC/USD": 0.4, "ETH/USD": -0.3, "SOL/USD": 0.35}
    vols = {"BTC/USD": 0.35, "ETH/USD": 0.10, "SOL/USD": math.nan}

    a = RiskEngine(cfg).apply(dict(proposal), make_state(dict(vols)))
    b = RiskEngine(cfg).apply(dict(proposal), make_state(dict(vols)))

    assert a == b


def test_defaults_are_conservative():
    """Die Defaults sind Teil des Vertrags, nicht Beiwerk."""
    cfg = RiskConfig()

    assert cfg.max_gross_exposure <= 1.0, "Default darf keinen Hebel erlauben"
    assert cfg.max_weight_per_symbol <= 1.0
    assert cfg.max_vol_scale <= 2.0, "Default-Hebel aus ruhigem Markt begrenzt"
    assert 0 < cfg.max_drawdown < 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_vol": 0},
        {"max_vol_scale": 0.5},
        {"max_weight_per_symbol": -0.1},
        {"max_gross_exposure": 0},
        {"max_drawdown": 1.0},
        {"max_drawdown": 0},
        {"max_positions": 0},
        {"vol_lookback": 1},
    ],
)
def test_invalid_config_is_rejected_at_load_time(kwargs: dict[str, float]):
    """Falsche Grenzen sollen beim Laden auffallen, nicht mitten im Lauf."""
    with pytest.raises(ValueError):
        RiskConfig(**kwargs)
