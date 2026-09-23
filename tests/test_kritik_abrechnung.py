"""Die Kritik-Stufe wird abgerechnet: jede Zahl gegen das, was eintrat (ADR-080).

Laya (NandhaKishorM/laya) definiert jede Antwort als Wahrscheinlichkeit eines
benannten Ereignisses und rechnet sie mit strikt properen Scoring Rules ab.
Die Kritik dieses Projekts gab bis ADR-080 eine Skala ohne Ereignis aus. Diese
Tests halten die Uebertragung fest:

* das Schema kennt nur noch Zahlen, zu denen es einen Ausgang gibt;
* der Brier-Score wird gegen die Basisrate gerechnet, nicht gegen den
  Muenzwurf -- bei null Treffern ist "faellt durch" der ehrliche Gegner;
* was nicht beobachtet ist, wird nicht abgerechnet, aber gezaehlt;
* der Altbestand wird gemessen, wo er Ereignisse beruehrt: das Umschlags-Flag
  gegen den nachgemessenen Umschlag.
"""

from __future__ import annotations

import math
import pathlib

import pandas as pd
import pydantic
import pytest
from typer.testing import CliRunner

from qt import cli
from qt.llm.client import StubCriticClient, StubGeneratorClient
from qt.llm.schemas import CandidateCritique
from qt.research.abrechnung import (
    EREIGNISSE,
    UMSCHLAG_QUELLE,
    abrechnen,
    altbestand,
    bericht,
    brier,
    umschlag_nachmessen,
)
from qt.research.loop import run_research_loop
from qt.research.registry import ResearchRegistry
from tests.conftest import make_bars

# --------------------------------------------------------------------------
# Das Schema
# --------------------------------------------------------------------------


def test_das_schema_kennt_nur_noch_abrechenbare_zahlen():
    felder = set(CandidateCritique.model_fields)

    assert {e.feld for e in EREIGNISSE} <= felder
    assert "overfitting_risk" not in felder, (
        "eine Skala ohne Ereignis laesst sich nicht abrechnen und gehoert "
        "nicht mehr ins Schema"
    )


@pytest.mark.parametrize("wert", [-0.1, 1.5, math.nan])
def test_eine_wahrscheinlichkeit_bleibt_eine_wahrscheinlichkeit(wert):
    with pytest.raises(pydantic.ValidationError):
        CandidateCritique(recommendation="proceed", p_dsr_bestanden=wert)


def test_eine_alte_antwort_mit_overfitting_risk_laesst_sich_noch_lesen():
    """Ein Cache-Eintrag aus der Zeit vor ADR-080 darf nicht abstuerzen."""
    urteil = CandidateCritique.model_validate(
        {"recommendation": "proceed", "overfitting_risk": 0.2, "reasoning": "alt"}
    )

    assert urteil.p_umschlag_ueber_budget is None
    assert urteil.p_dsr_bestanden is None


def test_der_stub_kritiker_erfindet_keine_wahrscheinlichkeiten():
    urteil = StubCriticClient().critique("brief")

    assert urteil.p_umschlag_ueber_budget is None
    assert urteil.p_oos_sharpe_positiv is None
    assert urteil.p_dsr_bestanden is None


# --------------------------------------------------------------------------
# Die Abrechnung
# --------------------------------------------------------------------------


def _zeile(**werte) -> dict:
    basis = {
        "critic_recommendation": "proceed",
        "critic_p_umschlag": None,
        "critic_p_oos_positiv": None,
        "critic_p_dsr": None,
        "umschlag_pro_jahr": None,
        "umschlag_quelle": None,
        "screening_status": None,
        "n_windows": None,
        "sharpe": None,
        "critic_overfitting_risk": None,
        "critic_magic_constants": False,
        "critic_unrealistic_turnover": False,
        "critic_excess_dof": False,
        "critic_rationale_mismatch": False,
    }
    return basis | werte


def _nach(abrechnung, spalte: str):
    return next(a for a in abrechnung if a.ereignis.spalte == spalte)


def test_brier_ist_der_mittlere_quadrierte_abstand():
    assert brier([0.9, 0.1], [1, 0]) == pytest.approx(0.01)
    assert brier([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    assert math.isnan(brier([], []))


def test_eine_trennscharfe_kritik_schlaegt_die_basisrate():
    frame = pd.DataFrame(
        [
            _zeile(critic_p_dsr=0.9, screening_status="passed", n_windows=5, sharpe=1.2),
            _zeile(critic_p_dsr=0.9, screening_status="passed", n_windows=5, sharpe=1.1),
            _zeile(critic_p_dsr=0.1, screening_status="rejected", n_windows=5, sharpe=-0.3),
            _zeile(critic_p_dsr=0.1, screening_status="rejected", n_windows=5, sharpe=-0.8),
        ]
    )

    dsr = _nach(abrechnen(frame), "critic_p_dsr")

    assert dsr.n == 4
    assert dsr.brier == pytest.approx(0.01)
    assert dsr.brier_basisrate == pytest.approx(0.25)
    assert dsr.schlaegt_basisrate is True


def test_bei_null_treffern_ist_jede_hoffnung_schlechter_als_die_basisrate():
    """Genau die Lage dieses Projekts: 0 von 24 bestanden."""
    frame = pd.DataFrame(
        [
            _zeile(critic_p_dsr=0.2, screening_status="rejected", n_windows=4, sharpe=-1.0)
            for _ in range(5)
        ]
    )

    dsr = _nach(abrechnen(frame), "critic_p_dsr")

    assert dsr.haeufigkeit == 0.0
    assert dsr.brier == pytest.approx(0.04)
    assert dsr.brier_basisrate == 0.0
    assert dsr.schlaegt_basisrate is False
    assert dsr.brier < dsr.brier_muenzwurf, "besser als ein Muenzwurf heisst hier nichts"


def test_was_nicht_beobachtet_ist_wird_nicht_abgerechnet_aber_gezaehlt():
    frame = pd.DataFrame(
        [
            # Prognose, aber noch kein Screening: kein Ausgang.
            _zeile(critic_p_dsr=0.3),
            # Ausgang, aber keine Prognose (Altbestand): gezaehlt, nicht gerechnet.
            _zeile(screening_status="rejected", n_windows=3, sharpe=-0.5),
            # Nicht von der Kritik beurteilt: gehoert gar nicht dazu.
            _zeile(critic_recommendation=None, critic_p_dsr=0.9, screening_status="rejected"),
        ]
    )

    dsr = _nach(abrechnen(frame), "critic_p_dsr")

    assert dsr.n == 0
    assert dsr.ohne_prognose == 1
    assert dsr.schlaegt_basisrate is None, "nichts abzurechnen ist kein Nein"


def test_ohne_walk_forward_gibt_es_keinen_oos_sharpe_aber_ein_nicht_bestanden():
    """Am Sanity-Check gescheitert: die DSR ist nicht bestanden, einen
    OOS-Sharpe gibt es dagegen nicht -- weder positiv noch negativ."""
    frame = pd.DataFrame(
        [
            _zeile(
                critic_p_oos_positiv=0.4,
                critic_p_dsr=0.1,
                screening_status="rejected",
                n_windows=0,
                sharpe=float("nan"),
            )
        ]
    )

    abrechnung = abrechnen(frame)

    assert _nach(abrechnung, "critic_p_oos_positiv").n == 0
    assert _nach(abrechnung, "critic_p_dsr").n == 1


def test_der_umschlag_wird_gegen_das_budget_des_gates_abgerechnet():
    frame = pd.DataFrame(
        [
            _zeile(critic_p_umschlag=0.9, umschlag_pro_jahr=307.7),
            _zeile(critic_p_umschlag=0.2, umschlag_pro_jahr=3.0),
        ]
    )

    umschlag = _nach(abrechnen(frame), "critic_p_umschlag")

    assert umschlag.n == 2
    assert umschlag.haeufigkeit == 0.5
    assert umschlag.brier == pytest.approx((0.1**2 + 0.2**2) / 2)


def test_der_altbestand_misst_das_umschlagsflag_am_gemessenen_umschlag():
    """Die Lage vom 2026-09-23: kein Flag gesetzt, der Umschlag weit drueber."""
    frame = pd.DataFrame(
        [
            _zeile(umschlag_pro_jahr=307.7, umschlag_quelle=UMSCHLAG_QUELLE),
            _zeile(umschlag_pro_jahr=12.4, umschlag_quelle=UMSCHLAG_QUELLE),
            _zeile(umschlag_pro_jahr=3.0, umschlag_quelle=UMSCHLAG_QUELLE),
            _zeile(
                umschlag_pro_jahr=2.0,
                umschlag_quelle=UMSCHLAG_QUELLE,
                critic_unrealistic_turnover=True,
            ),
        ]
    )

    alt = altbestand(frame)

    assert alt.umschlag_gemessen == 4
    assert alt.umschlag_ueber_budget == 2
    assert alt.flag_erkannt == 0
    assert alt.flag_uebersehen == 2
    assert alt.flag_fehlalarm == 1
    assert alt.umschlag_quellen == [UMSCHLAG_QUELLE]


def test_overfitting_risk_wird_beschrieben_nicht_abgerechnet():
    frame = pd.DataFrame(
        [
            _zeile(critic_overfitting_risk=r, screening_status="rejected", n_windows=3, sharpe=s)
            for r, s in [(0.1, -2.0), (0.2, -1.0), (0.25, 0.5), (0.15, -3.0)]
        ]
    )

    alt = altbestand(frame)
    text = bericht(frame)

    assert alt.risiko_n == 4
    assert (alt.risiko_min, alt.risiko_max) == (0.1, 0.25)
    assert math.isfinite(alt.risiko_spearman)
    assert "Nicht abrechenbar" in text
    assert "Noch nichts abzurechnen" in text


# --------------------------------------------------------------------------
# Registry, Loop und Nachmessen
# --------------------------------------------------------------------------


@pytest.fixture
def registry(tmp_path: pathlib.Path):
    with ResearchRegistry.open(tmp_path / "registry.duckdb") as reg:
        yield reg


def _stub_code() -> tuple[str, str]:
    vorschlag = StubGeneratorClient().propose("brief")
    return vorschlag.code, vorschlag.class_name


def test_die_registry_haelt_wahrscheinlichkeiten_und_umschlag_fest(registry):
    code, klasse = _stub_code()
    cid = registry.record_generated(klasse, code)
    registry.record_critique(
        cid, recommendation="proceed", p_umschlag=0.3, p_oos_positiv=0.4, p_dsr=0.05
    )
    registry.record_umschlag(cid, 4.2, UMSCHLAG_QUELLE)

    zeile = registry.get(cid)

    assert zeile["critic_p_umschlag"] == pytest.approx(0.3)
    assert zeile["critic_p_oos_positiv"] == pytest.approx(0.4)
    assert zeile["critic_p_dsr"] == pytest.approx(0.05)
    assert zeile["critic_overfitting_risk"] is None, "ADR-080 schreibt keine Skala mehr"
    assert zeile["umschlag_pro_jahr"] == pytest.approx(4.2)
    assert zeile["umschlag_quelle"] == UMSCHLAG_QUELLE


class _WahrscheinlichkeitsKritiker:
    model, effort = "wahrscheinlich", "low"

    def critique(self, briefing: str) -> CandidateCritique:
        return CandidateCritique(
            recommendation="proceed",
            p_umschlag_ueber_budget=0.25,
            p_oos_sharpe_positiv=0.35,
            p_dsr_bestanden=0.02,
            reasoning="Testurteil",
        )


def test_der_loop_schreibt_die_wahrscheinlichkeiten_der_kritik(registry):
    run_research_loop(
        1,
        {"BTC/USD": make_bars(1500, "BTC/USD", "4h")},
        ["BTC/USD"],
        "4h",
        StubGeneratorClient(),
        _WahrscheinlichkeitsKritiker(),
        registry=registry,
        train_bars=600,
        test_bars=200,
        embargo_bars=10,
    )

    (zeile,) = registry.history().itertuples()
    assert zeile.critic_p_umschlag == pytest.approx(0.25)
    assert zeile.critic_p_oos_positiv == pytest.approx(0.35)
    assert zeile.critic_p_dsr == pytest.approx(0.02)


def test_nachmessen_traegt_den_umschlag_ein_und_misst_nicht_doppelt(registry):
    code, klasse = _stub_code()
    beurteilt = registry.record_generated(klasse, code)
    registry.record_critique(beurteilt, recommendation="proceed")
    unbeurteilt = registry.record_generated(klasse, code)
    bars = {"BTC/USD": make_bars(800, "BTC/USD", "1d")}

    erste = umschlag_nachmessen(registry, bars)
    zweite = umschlag_nachmessen(registry, bars)

    assert set(erste) == {beurteilt}, "gemessen wird nur, was die Kritik beurteilt hat"
    assert math.isfinite(erste[beurteilt]) and erste[beurteilt] >= 0
    assert zweite == {}, "wer schon gemessen ist, wird nicht noch einmal gemessen"
    assert registry.get(unbeurteilt)["umschlag_pro_jahr"] is None
    assert registry.get(beurteilt)["umschlag_quelle"] == UMSCHLAG_QUELLE


def test_nachmessen_fuehrt_keinen_code_an_der_sandbox_vorbei_aus(registry):
    boese = (
        "class Boese(Strategy):\n"
        "    name = 'boese'\n"
        "    warmup_bars = 5\n\n"
        "    def on_bar(self, symbol, store):\n"
        "        return ().__class__.__base__.__subclasses__()\n"
    )
    cid = registry.record_generated("Boese", boese)
    registry.record_critique(cid, recommendation="proceed")

    ergebnis = umschlag_nachmessen(registry, {"BTC/USD": make_bars(300, "BTC/USD", "1d")})

    assert "Sandbox" in str(ergebnis[cid])
    assert registry.get(cid)["umschlag_pro_jahr"] is None


# --------------------------------------------------------------------------
# qt trials --kritik
# --------------------------------------------------------------------------

runner = CliRunner()


@pytest.fixture
def cli_registry(monkeypatch, tmp_path):
    from qt.data import store
    from qt.research import registry as registry_mod

    pfad = tmp_path / "registry.duckdb"
    monkeypatch.setattr(registry_mod, "DEFAULT_PATH", pfad)
    monkeypatch.setattr(store, "read_bars", lambda *a, **k: None)
    monkeypatch.setattr(store, "to_bars", lambda sym, tf, df: make_bars(800, sym, tf))
    with ResearchRegistry.open(pfad) as reg:
        code, klasse = _stub_code()
        cid = reg.record_generated(klasse, code)
        reg.record_critique(cid, recommendation="proceed", overfitting_risk=0.2)
    return pfad


def test_qt_trials_kritik_druckt_die_abrechnung(cli_registry):
    ergebnis = runner.invoke(cli.app, ["trials", "--kritik"])

    assert ergebnis.exit_code == 0, ergebnis.output
    assert "Kritik-Abrechnung (ADR-080)" in ergebnis.output
    assert "Umschlag noch nicht gemessen" in ergebnis.output


def test_qt_trials_umschlag_nachmessen_misst_und_rechnet_ab(cli_registry):
    ergebnis = runner.invoke(cli.app, ["trials", "--umschlag-nachmessen"])

    assert ergebnis.exit_code == 0, ergebnis.output
    assert "kein Versuch, keine Auswahl" in ergebnis.output
    assert "Umschlags-Flag gegen Messung (1 gemessen)" in ergebnis.output
    with ResearchRegistry.open(cli_registry) as reg:
        assert reg.trial_count() == 0, "Nachmessen ist kein Versuch"
