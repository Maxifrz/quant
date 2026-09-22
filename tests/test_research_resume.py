"""`qt research --resume`: ein abgebrochener Lauf laeuft weiter, statt neu zu beginnen.

Ein `--generate N`-Lauf ist mehrstufig und kostet Geld: je Kandidat ein
Generator-Aufruf, eine Kritik, ein Walk-Forward. Ein Container-Neustart
mittendrin kostete bisher alles. TradingAgents hat dafuer einen Checkpointer,
dieses Projekt hatte nichts (ADR-077, umgesetzt in ADR-079).

Drei Zusagen, jede mit eigenem Test:

1. Die Fortsetzung sieht **dieselben Briefings** wie ein Lauf ohne
   Unterbrechung. Sonst waere sie ein anderer Lauf mit demselben Namen.
2. Ein halb gepruefter Kandidat laeuft aus seinem gespeicherten Code zu Ende,
   **ohne neuen Generator- oder Kritik-Aufruf**.
3. Kein Kandidat wird zweimal gescreent, der Versuchszaehler zaehlt also
   nichts doppelt (ADR-005).

Der Absturz ist eine `BaseException`: der Loop faengt `Exception` je
Kandidat ab, ein echter Container-Tod laesst sich davon nicht aufhalten.
"""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from qt import cli
from qt.llm.client import StubGeneratorClient
from qt.llm.schemas import CandidateCritique
from qt.research import loop as loop_mod
from qt.research.loop import auf_datenstand, datenstand, run_research_loop
from qt.research.registry import ResearchRegistry
from tests.conftest import make_bars

FENSTER = dict(train_bars=600, test_bars=200, embargo_bars=10)


class Absturz(BaseException):
    """Steht fuer den Container, der mitten im Lauf verschwindet."""


class ProtokollGenerator:
    """Stub-Kandidaten je Platz in der Charge, mit Mitschrift der Briefings.

    Der Kandidat haengt am Platz ("Kandidat 3 von 4"), nicht an der Zahl der
    Aufrufe. Nur so liefert ein frischer Generator nach dem Neustart fuer
    denselben Platz denselben Kandidaten wie der erste.
    """

    def __init__(self, absturz_bei: int | None = None, fehler_bei: int | None = None):
        self.model = "protokoll"
        self.effort = "stub"
        self.briefings: list[str] = []
        self._absturz_bei = absturz_bei
        self._fehler_bei = fehler_bei

    def propose(self, briefing: str):
        index = int(re.match(r"Kandidat (\d+) von", briefing).group(1)) - 1
        self.briefings.append(briefing)
        if index == self._absturz_bei:
            raise Absturz
        if index == self._fehler_bei:
            raise ConnectionError("Endpunkt nicht erreichbar")
        stub = StubGeneratorClient()
        stub.calls = index
        return stub.propose(briefing)


class ZaehlenderKritiker:
    def __init__(self) -> None:
        self.model = "zaehler"
        self.effort = "low"
        self.calls = 0

    def critique(self, briefing: str) -> CandidateCritique:
        self.calls += 1
        return CandidateCritique(recommendation="proceed", reasoning="Testurteil")


@pytest.fixture
def bars():
    return {"BTC/USD": make_bars(1500, "BTC/USD", "4h")}


def _registry(tmp_path, name: str) -> ResearchRegistry:
    return ResearchRegistry.open(tmp_path / f"{name}.duckdb")


def _lauf(n, bars, registry, generator, kritiker=None, **kwargs):
    return run_research_loop(
        n,
        bars,
        ["BTC/USD"],
        "4h",
        generator,
        kritiker or ZaehlenderKritiker(),
        registry=registry,
        **FENSTER,
        **kwargs,
    )


def _versuche(registry) -> list[int]:
    frame = registry.history()
    return sorted(int(v) for v in frame["trial_count_at_screening"].dropna())


def test_fortsetzung_sieht_dieselben_briefings_wie_ein_lauf_ohne_unterbrechung(
    tmp_path, bars
):
    with _registry(tmp_path, "durchgehend") as reg:
        durchgehend = ProtokollGenerator()
        _lauf(4, bars, reg, durchgehend)
        versuche_durchgehend = _versuche(reg)
        klassen_durchgehend = list(reg.history()["class_name"])

    with _registry(tmp_path, "abgebrochen") as reg:
        with pytest.raises(Absturz):
            _lauf(4, bars, reg, ProtokollGenerator(absturz_bei=2))
        offen = reg.open_run()
        assert offen is not None, "ein abgebrochener Lauf muss auffindbar bleiben"

        weiter = ProtokollGenerator()
        telemetry = _lauf(4, bars, reg, weiter, resume_run=offen["id"])

        assert weiter.briefings == durchgehend.briefings[2:], (
            "Die Fortsetzung hat andere Briefings gesehen als ein Lauf ohne "
            "Unterbrechung -- vermutlich stehen die eigenen Kandidaten des "
            "Laufs jetzt unter 'bereits geprueft'."
        )
        assert telemetry.uebernommen == 2
        assert telemetry.generated == 2
        assert list(reg.history()["class_name"]) == klassen_durchgehend
        assert _versuche(reg) == versuche_durchgehend
        assert reg.open_run() is None, "nach der Fortsetzung ist der Lauf fertig"


def test_halb_gepruefter_kandidat_laeuft_ohne_neuen_modellaufruf_zu_ende(
    tmp_path, bars, monkeypatch
):
    echtes_screening = loop_mod.screen_candidate
    aufrufe = {"n": 0}

    def stirbt_beim_ersten_screening(*args, **kwargs):
        aufrufe["n"] += 1
        if aufrufe["n"] == 1:
            raise Absturz
        return echtes_screening(*args, **kwargs)

    monkeypatch.setattr(loop_mod, "screen_candidate", stirbt_beim_ersten_screening)

    with _registry(tmp_path, "reg") as reg:
        with pytest.raises(Absturz):
            _lauf(2, bars, reg, ProtokollGenerator())
        (liegen_geblieben,) = reg.history().itertuples()
        assert liegen_geblieben.critic_recommendation == "proceed"
        assert liegen_geblieben.screening_status is None, (
            "Vorbedingung: der Kandidat muss vor dem Screening stehen geblieben sein"
        )

        generator, kritiker = ProtokollGenerator(), ZaehlenderKritiker()
        telemetry = _lauf(
            2, bars, reg, generator, kritiker, resume_run=reg.open_run()["id"]
        )

        assert len(generator.briefings) == 1, "nur der leere Platz wird neu erzeugt"
        assert kritiker.calls == 1, "die vorhandene Kritik wird nicht noch einmal bezahlt"
        assert telemetry.nachgeholt == 1
        frame = reg.history()
        assert len(frame) == 2, "kein zweiter Kandidat fuer denselben Platz"
        assert frame.iloc[0]["id"] == liegen_geblieben.id
        assert frame.iloc[0]["screening_status"] is not None
        assert _versuche(reg) == [1, 2]


def test_fertige_kandidaten_werden_nicht_noch_einmal_gescreent(tmp_path, bars):
    with _registry(tmp_path, "reg") as reg:
        with pytest.raises(Absturz):
            _lauf(3, bars, reg, ProtokollGenerator(absturz_bei=2))
        vorher = reg.trial_count()
        assert vorher == 2

        _lauf(3, bars, reg, ProtokollGenerator(), resume_run=reg.open_run()["id"])

        assert reg.trial_count() == 3
        assert _versuche(reg) == [1, 2, 3], "ein Versuch wurde doppelt gezaehlt"


def test_lauf_mit_generatorfehler_bleibt_offen_und_holt_den_platz_nach(tmp_path, bars):
    with _registry(tmp_path, "reg") as reg:
        erster = _lauf(3, bars, reg, ProtokollGenerator(fehler_bei=1))
        assert erster.generator_errors == 1
        offen = reg.open_run()
        assert offen is not None, (
            "ein Lauf mit leerem Platz ist nicht fertig -- sonst holt ihn "
            "niemand nach, wenn der Zugang wieder steht"
        )

        generator = ProtokollGenerator()
        zweiter = _lauf(3, bars, reg, generator, resume_run=offen["id"])

        assert len(generator.briefings) == 1
        assert zweiter.uebernommen == 2 and zweiter.generated == 1
        assert sorted(reg.run_candidates(offen["id"])) == [0, 1, 2]
        assert reg.open_run() is None


def test_ein_abgeschlossener_lauf_laesst_sich_nicht_fortsetzen(tmp_path, bars):
    with _registry(tmp_path, "reg") as reg:
        telemetry = _lauf(1, bars, reg, ProtokollGenerator())
        with pytest.raises(ValueError, match="abgeschlossen"):
            _lauf(1, bars, reg, ProtokollGenerator(), resume_run=telemetry.run_id)


def test_die_groesse_eines_laufs_aendert_sich_beim_fortsetzen_nicht(tmp_path, bars):
    with _registry(tmp_path, "reg") as reg:
        with pytest.raises(Absturz):
            _lauf(3, bars, reg, ProtokollGenerator(absturz_bei=1))
        with pytest.raises(ValueError, match="Plaetze"):
            _lauf(5, bars, reg, ProtokollGenerator(), resume_run=reg.open_run()["id"])


# -- Datenstand --------------------------------------------------------------


def test_neue_bars_seit_dem_start_werden_abgeschnitten_nicht_beanstandet():
    alt = {"BTC/USD": make_bars(300, "BTC/USD", "4h")}
    stand = datenstand(alt)
    neu = {"BTC/USD": make_bars(320, "BTC/USD", "4h")}

    gekuerzt, abweichungen = auf_datenstand(neu, stand)

    assert abweichungen == []
    assert len(gekuerzt["BTC/USD"]) == 300
    assert datenstand(gekuerzt) == stand


def test_rueckwirkend_geaenderte_kurse_werden_erkannt():
    alt = {"SPY": make_bars(300, "SPY", "1d", seed=1)}
    stand = datenstand(alt)
    adjustiert = {"SPY": make_bars(300, "SPY", "1d", seed=2)}

    _, abweichungen = auf_datenstand(adjustiert, stand)

    assert len(abweichungen) == 1
    assert "andere Kurse" in abweichungen[0]


def test_fehlende_bars_werden_erkannt():
    stand = datenstand({"BTC/USD": make_bars(300, "BTC/USD", "4h")})

    _, abweichungen = auf_datenstand({"BTC/USD": make_bars(250, "BTC/USD", "4h")}, stand)

    assert abweichungen and "300" in abweichungen[0] and "250" in abweichungen[0]


# -- CLI ---------------------------------------------------------------------

runner = CliRunner()

PARAMETER = {
    "symbols": "BTC/USD",
    "tf": "4h",
    "since": None,
    "until": None,
    "train": 600,
    "test": 200,
    "embargo": 10,
    "dsr_threshold": 0.9,
    "use_critic": False,
    "provider": "anthropic",
    "model": "claude-opus-5",
    "generator_effort": "high",
    "critic_effort": "low",
    "stub": True,
}


@pytest.fixture
def stub_registry(monkeypatch, tmp_path):
    from qt.data import store
    from qt.research import registry

    pfad = tmp_path / "registry_stub.duckdb"
    monkeypatch.setattr(registry, "STUB_PATH", pfad)
    monkeypatch.setattr(store, "read_bars", lambda *a, **k: None)
    monkeypatch.setattr(store, "to_bars", lambda sym, tf, df: make_bars(1500, sym, tf))
    return pfad


def test_resume_ohne_offenen_lauf_ist_ein_fehler(stub_registry):
    ergebnis = runner.invoke(cli.app, ["research", "--stub", "--resume"])

    assert ergebnis.exit_code == 1
    assert "Kein abgebrochener Lauf" in ergebnis.output


def test_resume_nimmt_die_parameter_aus_dem_lauf_nicht_von_der_befehlszeile(
    stub_registry, monkeypatch
):
    with ResearchRegistry.open(stub_registry) as reg:
        stand = datenstand({"BTC/USD": make_bars(1500, "BTC/USD", "4h")})
        run_id = reg.start_run(3, PARAMETER | {"datenstand": stand})

    gesehen = {}

    def aufzeichnen(n, bars, symbols, timeframe, *args, **kwargs):
        gesehen.update(kwargs, n=n, symbols=symbols, timeframe=timeframe)
        return loop_mod.ResearchTelemetry(uebernommen=3, run_id=run_id)

    monkeypatch.setattr(loop_mod, "run_research_loop", aufzeichnen)

    ergebnis = runner.invoke(
        cli.app,
        ["research", "--stub", "--resume", "--generate", "9", "--train", "99",
         "--tf", "1h", "--dsr-threshold", "0.5"],
    )

    assert ergebnis.exit_code == 0, ergebnis.output
    assert gesehen["resume_run"] == run_id
    assert gesehen["run_config"] is None, "ein fortgesetzter Lauf legt keinen neuen an"
    assert gesehen["n"] == 3
    assert gesehen["timeframe"] == "4h"
    assert gesehen["train_bars"] == 600
    assert gesehen["dsr_threshold"] == 0.9
    assert gesehen["use_critic"] is False


def test_resume_verweigert_einen_geaenderten_datenstand(stub_registry, monkeypatch):
    with ResearchRegistry.open(stub_registry) as reg:
        stand = datenstand({"BTC/USD": make_bars(1500, "BTC/USD", "4h", seed=7)})
        reg.start_run(2, PARAMETER | {"datenstand": stand})

    def darf_nicht_laufen(*args, **kwargs):
        raise AssertionError("gegen einen anderen Datenstand weitergerechnet")

    monkeypatch.setattr(loop_mod, "run_research_loop", darf_nicht_laufen)

    ergebnis = runner.invoke(cli.app, ["research", "--stub", "--resume"])

    assert ergebnis.exit_code == 1
    assert "Datenstand" in ergebnis.output


def test_ein_neuer_lauf_erwaehnt_den_offenen(stub_registry):
    with ResearchRegistry.open(stub_registry) as reg:
        reg.start_run(2, PARAMETER | {"datenstand": {}})

    ergebnis = runner.invoke(
        cli.app,
        ["research", "--stub", "--generate", "1", "--symbols", "BTC/USD",
         "--tf", "4h", "--train", "600", "--test", "200", "--embargo", "10"],
    )

    assert ergebnis.exit_code == 0, ergebnis.output
    assert "nicht abgeschlossen" in ergebnis.output
    assert "--resume" in ergebnis.output
