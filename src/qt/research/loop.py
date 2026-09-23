"""Die Orchestrierung des Research-Loops.

    Generator (LLM 1)
       |
       v
    Sandbox: AST-Whitelist ---- Ablehnung ---> Registry "rejected", ENDE
       |
       v
    Sandbox: technischer Probelauf ---- Fehlschlag ---> Registry, ENDE
       |
       v
    Kritik (LLM 2) ---- "reject" ---> Registry "rejected_by_critic", ENDE
       |
       v
    Sanity-Check (ein Backtest) ---- kein Trade ---> Registry, ENDE
       |
       v
    Walk-Forward-OOS -> Deflated Sharpe Ratio -> Registry
       |
       v
    manuelle Freigabe (nicht hier, nicht automatisierbar)

Die Reihenfolge ist nach Kosten sortiert, nicht nach Bequemlichkeit: erst
alles Deterministische und Kostenlose, dann der billige Modellaufruf, dann der
teure Backtest. Jede Stufe, die frueher ablehnt, spart alle folgenden.

**Die Sandbox steht vor allem anderen und das ist nicht verhandelbar.** Kein
generierter Code wird ausgefuehrt -- auch nicht probeweise, auch nicht in
einem try -- bevor die Whitelist ihn geprueft hat. `sandbox.load_strategy_class`
erzwingt das selbst; diese Datei verlaesst sich nicht darauf, sondern ruft die
Pruefung ohnehin in der richtigen Reihenfolge auf. Zwei Schloesser an
derselben Tuer sind hier angemessen.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from qt.core.config import BacktestConfig
from qt.core.types import Bar
from qt.llm.schemas import StrategyCandidateProposal
from qt.research import critic as critic_mod
from qt.research import sandbox
from qt.research.dsr import DEFAULT_THRESHOLD
from qt.research.generator import build_generation_briefing
from qt.research.groesse import mit_groessenschicht
from qt.research.registry import (
    SANDBOX_REJECTED,
    SCREENING_PASSED,
    SCREENING_REJECTED,
    ResearchRegistry,
)
from qt.research.screening import quick_sanity_check, screen_candidate


@dataclass(slots=True)
class ResearchTelemetry:
    """Der Trichter, Stufe fuer Stufe.

    Analog zu `AllocatorTelemetry` und aus demselben Grund: ein Loop, dessen
    Ausfaelle niemand sieht, wird fuer gut gehalten, weil er nie auffaellt.
    Zwanzig Kandidaten, von denen die Sandbox neunzehn verwirft, sind ein
    Befund ueber den Generator -- aber nur, wenn die Zahl irgendwo steht.
    """

    generated: int = 0
    generator_errors: int = 0
    sandbox_rejected: int = 0
    probe_rejected: int = 0
    critic_rejected: int = 0
    critic_errors: int = 0
    sanity_rejected: int = 0
    screened: int = 0
    passed: int = 0
    failed: int = 0
    # Nur beim Fortsetzen (ADR-079): Kandidaten, die der abgebrochene Lauf
    # schon fertig hinterlassen hat, und solche, die er halb geprueft liegen
    # liess und die jetzt aus dem gespeicherten Code zu Ende laufen.
    uebernommen: int = 0
    nachgeholt: int = 0
    run_id: str = ""
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"erzeugt {self.generated}, Sandbox verworfen {self.sandbox_rejected}, "
            f"Probelauf verworfen {self.probe_rejected}, Kritik abgelehnt "
            f"{self.critic_rejected}, Sanity verworfen {self.sanity_rejected}, "
            f"gescreent {self.screened}, davon bestanden {self.passed}"
        )

    def table(self) -> str:
        vorlauf = (
            (("aus dem Vorlauf fertig", self.uebernommen),
             ("halb fertig, nachgeholt", self.nachgeholt))
            if self.uebernommen or self.nachgeholt
            else ()
        )
        rows = (
            *vorlauf,
            ("erzeugt", self.generated),
            ("Generator-Fehler", self.generator_errors),
            ("Sandbox verworfen", self.sandbox_rejected),
            ("Probelauf verworfen", self.probe_rejected),
            ("Kritik abgelehnt", self.critic_rejected),
            ("Kritik nicht erreichbar", self.critic_errors),
            ("Sanity verworfen", self.sanity_rejected),
            ("gescreent", self.screened),
            ("DSR bestanden", self.passed),
            ("DSR durchgefallen", self.failed),
        )
        width = max(len(label) for label, _ in rows)
        return "\n".join(f"  {label:<{width}}  {value:>5}" for label, value in rows)


#: **Keine LLM-Kandidaten auf Preisdaten mehr (ADR-080).** 16 Kandidaten kamen
#: bis zum Screening, keiner hat bestanden, 15 davon lagen ueber dem
#: Umschlagbudget -- und jeder hat die DSR-Latte fuer alle kuenftigen
#: Hypothesen dauerhaft angehoben. Eine Konstante und keine Option, aus
#: demselben Grund wie bei den Gate-Schwellen (ADR-057): wer die Sperre
#: aufhebt, hinterlaesst einen Diff. Stub-Laeufe bleiben erlaubt, sie kosten
#: keinen Versuch (ADR-057).
LLM_KANDIDATEN_FREIGEGEBEN = False

#: Bruttobudget der Positionsgroessen-Schicht (ADR-069). Dieselbe Zahl wie
#: `BacktestConfig.max_gross_exposure` -- und bewusst **keine** Option des
#: Loops: wer sie aendert, hinterlaesst einen Diff, so wie bei den
#: Gate-Schwellen (ADR-057).
BRUTTOGRENZE = 1.0


def run_research_loop(
    n: int,
    bars: dict[str, list[Bar]],
    symbols: list[str],
    timeframe: str,
    generator_client,
    critic_client=None,
    registry: ResearchRegistry | None = None,
    train_bars: int = 3000,
    test_bars: int = 800,
    embargo_bars: int = 50,
    dsr_threshold: float = DEFAULT_THRESHOLD,
    cfg: BacktestConfig | None = None,
    use_critic: bool = True,
    echo=None,
    run_config: Mapping[str, Any] | None = None,
    resume_run: str | None = None,
) -> ResearchTelemetry:
    """`n` Kandidaten erzeugen und durch die Pipeline schicken.

    `registry` ist verpflichtend fuer einen echten Lauf: ohne sie gaebe es
    keinen ueber Laeufe hinweg persistenten Versuchszaehler, und die DSR
    korrigierte dann nur gegen die Kandidaten dieser Sitzung -- also gegen
    eine Zahl, die mit jedem Neustart auf null faellt. Genau das waere ein
    Overfitting-Schutz, der Sicherheit vortaeuscht.

    Die Kandidaten werden nacheinander vollstaendig durch die Pipeline
    geschickt, nicht stufenweise als Charge. Damit steht der Versuchszaehler
    beim Screening des zweiten Kandidaten bereits um den ersten hoeher -- was
    richtig ist: der erste Blick auf die Daten war da schon getan.

    **Fortsetzen** (`resume_run`, ADR-079): ein Lauf, den ein Container-
    Neustart mittendrin abgebrochen hat, laeuft an derselben Stelle weiter.
    Fertige Kandidaten bleiben, wie sie sind. Ein halb gepruefter laeuft aus
    seinem gespeicherten Code zu Ende, ohne neuen Generator-Aufruf und mit der
    schon vorliegenden Kritik. Nur Plaetze ohne Kandidaten werden neu
    erzeugt, und zwar mit denselben Briefings wie ohne Unterbrechung. Ein
    Kandidat wird dabei nie zweimal gescreent, also auch nie doppelt
    gezaehlt.
    """
    telemetry = ResearchTelemetry()
    if registry is None:
        raise ValueError(
            "Ohne Registry gibt es keinen persistenten Versuchszaehler und "
            "damit keine belastbare Deflated Sharpe Ratio (ADR-005)."
        )

    if resume_run is None:
        # Was das Projekt schon geprueft hat -- Bibliothek plus Registry. Ohne
        # diese Liste schlaegt der Generator zuverlaessig wieder SMA-Kreuzung,
        # Donchian-Ausbruch und z-Score-Reversion vor: drei von fuenf
        # Versuchen im Lauf vom 2026-09-03 gingen genau dafuer drauf (ADR-065).
        bereits = bereits_geprueft(registry)
        run_id = registry.start_run(n, run_config or {}, bereits)
        vorhanden: dict[int, dict[str, Any]] = {}
    else:
        lauf = registry.get_run(resume_run)
        if lauf is None:
            raise KeyError(f"Kein Lauf mit ID {resume_run!r} in {registry.path}")
        if lauf["finished_at"] is not None:
            raise ValueError(f"Lauf {resume_run} ist abgeschlossen, nichts fortzusetzen.")
        if int(lauf["n"]) != n:
            raise ValueError(
                f"Lauf {resume_run} hat {lauf['n']} Plaetze, nicht {n}. Ein "
                "fortgesetzter Lauf aendert seine Groesse nicht."
            )
        run_id = resume_run
        # Die Liste vom Start, nicht neu berechnet: inzwischen stehen die
        # Kandidaten dieses Laufs selbst in der Registry, und die Briefings
        # der restlichen Plaetze saehen anders aus als ohne Unterbrechung.
        bereits = lauf["bereits"]
        vorhanden = registry.run_candidates(run_id)
    telemetry.run_id = run_id

    seen: list[str] = []

    for index in range(n):
        stand = vorhanden.get(index)
        if stand is not None and _abgeschlossen(stand):
            telemetry.uebernommen += 1
            seen.append(_ansatz(stand["proposal_name"], stand["rationale"]))
            continue

        if stand is not None:
            # Halb geprueft liegen geblieben. Der Code steht in der Registry;
            # ein neuer Generator-Aufruf kostete Geld und lieferte einen
            # anderen Kandidaten fuer denselben Platz.
            proposal = StrategyCandidateProposal.model_construct(
                name=stand["proposal_name"] or stand["class_name"],
                class_name=stand["class_name"],
                code=stand["code"],
                rationale=stand["rationale"] or "",
            )
            candidate_id = stand["id"]
            telemetry.nachgeholt += 1
            _say(echo, f"[{index + 1}/{n}] {proposal.name} (aus der Registry fortgesetzt)")
        else:
            briefing = build_generation_briefing(
                index, n, seen, bereits_geprueft=bereits
            )
            try:
                proposal = generator_client.propose(briefing)
            except Exception as exc:  # noqa: BLE001 -- eine Charge stirbt nicht am Netz
                telemetry.generator_errors += 1
                telemetry.notes.append(
                    f"Generator {index + 1}: {type(exc).__name__}: {exc}"
                )
                continue

            telemetry.generated += 1
            candidate_id = registry.record_generated(
                class_name=proposal.class_name,
                code=proposal.code,
                rationale=proposal.rationale,
                generator_model=getattr(generator_client, "model", ""),
                generator_effort=getattr(generator_client, "effort", ""),
                run_id=run_id,
                run_index=index,
                proposal_name=proposal.name,
            )
            _say(echo, f"[{index + 1}/{n}] {proposal.name}")
        seen.append(_ansatz(proposal.name, proposal.rationale))

        # --- Stufe 1: die Whitelist. Vor jeder Ausfuehrung. ---------------
        report = sandbox.check(proposal.code)
        flags = sandbox.scan_literals(proposal.code) if report.ok else []
        registry.record_sandbox_result(
            candidate_id,
            report.ok,
            reasons=report.reasons,
            literal_flags=[f"Zeile {f.line}: {f.value:g}" for f in flags],
        )
        if not report.ok:
            telemetry.sandbox_rejected += 1
            _say(echo, f"      Sandbox: {report.reasons[0] if report.reasons else 'abgelehnt'}")
            continue

        # --- Stufe 2: technischer Probelauf, erst jetzt wird ausgefuehrt --
        try:
            strategy_cls = sandbox.load_strategy_class(
                proposal.code, proposal.class_name
            )
            probe = sandbox.probe(strategy_cls, symbols, timeframe)
        except Exception as exc:  # noqa: BLE001
            telemetry.probe_rejected += 1
            registry.record_sandbox_result(
                candidate_id, False, reasons=[f"{type(exc).__name__}: {exc}"]
            )
            _say(echo, f"      Probelauf: {type(exc).__name__}")
            continue

        if not probe.ok:
            telemetry.probe_rejected += 1
            registry.record_sandbox_result(
                candidate_id, False, reasons=[probe.reason]
            )
            _say(echo, f"      Probelauf: {probe.reason}")
            continue

        # **Ab hier mit Positionsgroessen-Schicht** (ADR-069). Der Probelauf
        # oben hat den Kandidaten nackt geprueft -- dort geht es um seine
        # Verdrahtung. Alles, was danach eine *Zahl* erzeugt, laeuft mit der
        # Bruttogrenze, weil ein Ergebnis ohne sie ueber 38 Maerkte ein Konto
        # mit 38x Hebel misst und damit keine Aussage ueber die Idee macht
        # (ADR-065).
        strategy_cls = mit_groessenschicht(strategy_cls, BRUTTOGRENZE)

        # --- Stufe 3: die Kritik, billiger Vorfilter vor dem Backtest -----
        # Ein fortgesetzter Kandidat mit gespeicherter Kritik wird nicht noch
        # einmal gefragt. Hiess sie "reject", waere er schon fertig.
        kritik_liegt_vor = stand is not None and bool(stand.get("critic_recommendation"))
        if kritik_liegt_vor:
            _say(echo, "      Kritik: liegt aus dem Vorlauf vor, kein neuer Aufruf")
        elif use_critic and critic_client is not None:
            verdict, error = critic_mod.critique(
                critic_client, proposal, probe, flags, oos_bars=test_bars
            )
            if error is not None:
                telemetry.critic_errors += 1
                telemetry.notes.append(f"Kritik {proposal.name}: {error}")
                # **Sichtbar machen, dass hier nicht gefiltert wurde.**
                # `critic_mod.critique` faellt bei jedem Fehler auf "proceed"
                # zurueck -- ein nicht erreichbarer Kritiker winkt also jeden
                # Kandidaten durch, und die DSR traegt dann allein. Das steht
                # zwar in der Telemetrie-Tabelle am Ende, aber wer den Lauf
                # mitliest, soll es an der Stelle sehen (ADR-053).
                _say(echo, "      Kritik: nicht erreichbar -- ungeprueft weiter")
            registry.record_critique(
                candidate_id,
                recommendation=verdict.recommendation,
                p_umschlag=verdict.p_umschlag_ueber_budget,
                p_oos_positiv=verdict.p_oos_sharpe_positiv,
                p_dsr=verdict.p_dsr_bestanden,
                magic_constants=verdict.magic_price_constants,
                unrealistic_turnover=verdict.unrealistic_turnover,
                excess_dof=verdict.excess_degrees_of_freedom,
                rationale_mismatch=verdict.rationale_code_mismatch,
                reasoning=verdict.reasoning,
                model=getattr(critic_client, "model", ""),
                effort=getattr(critic_client, "effort", ""),
            )
            if verdict.recommendation == "reject":
                telemetry.critic_rejected += 1
                _say(echo, f"      Kritik: abgelehnt -- {verdict.reasoning[:70]}")
                continue
            if verdict.contradictory:
                telemetry.notes.append(
                    f"{proposal.name}: Kritik winkt durch, nennt aber "
                    f"{len(verdict.flags)} Maengel"
                )

        # --- Stufe 4: billiger Sanity-Check vor dem Walk-Forward ----------
        sanity = quick_sanity_check(strategy_cls, bars, symbols, timeframe, cfg)
        if not sanity.ok:
            telemetry.sanity_rejected += 1
            registry.record_screening(
                candidate_id,
                status=SCREENING_REJECTED,
                n_windows=0,
                oos_bars=0,
                sharpe=float("nan"),
            )
            telemetry.screened += 1
            telemetry.failed += 1
            _say(echo, f"      Sanity: {sanity.reason}")
            continue

        # --- Stufe 5: Walk-Forward und DSR -------------------------------
        # Der Zaehler schliesst diesen Kandidaten ein: er ist einer der
        # Blicke auf die Daten, und die Korrektur soll ihn mitzaehlen.
        trials = registry.trial_count() + 1
        result = screen_candidate(
            strategy_cls,
            bars,
            symbols,
            timeframe,
            trial_count=trials,
            train_bars=train_bars,
            test_bars=test_bars,
            embargo_bars=embargo_bars,
            dsr_threshold=dsr_threshold,
            cfg=cfg,
        )
        registry.record_screening(
            candidate_id,
            status=SCREENING_PASSED if result.passed else SCREENING_REJECTED,
            n_windows=result.n_windows,
            oos_bars=result.oos_bars,
            sharpe=result.sharpe,
            dsr=result.dsr,
            dsr_threshold=result.dsr_threshold,
            trial_count=result.trial_count,
        )
        telemetry.screened += 1
        if result.passed:
            telemetry.passed += 1
        else:
            telemetry.failed += 1
        _say(echo, f"      {result.summary()}")

    # Ein Lauf mit Generator-Fehlern bleibt offen: `--resume` holt die
    # leeren Plaetze nach, sobald der Zugang wieder steht (ADR-078, ADR-079).
    if telemetry.generator_errors == 0:
        registry.finish_run(run_id)

    return telemetry


def _say(echo, message: str) -> None:
    if echo is not None:
        echo(message)


def _ansatz(name: str | None, rationale: str | None) -> str:
    """Eine Zeile "bisherige Ansaetze dieser Charge" fuer das Briefing."""
    return f"{name or ''}: {(rationale or '')[:80]}"


def _abgeschlossen(stand: Mapping[str, Any]) -> bool:
    """Hat dieser Kandidat seine letzte Stufe schon hinter sich?

    Gescreent (bestanden, durchgefallen oder am Sanity-Check gescheitert),
    von Sandbox oder Probelauf verworfen, oder von der Kritik abgelehnt.
    Alles andere ist mittendrin stehen geblieben.
    """
    return (
        stand.get("screening_status") is not None
        or stand.get("sandbox_status") == SANDBOX_REJECTED
        or stand.get("critic_recommendation") == "reject"
    )


def datenstand(bars: Mapping[str, list[Bar]]) -> dict[str, dict[str, Any]]:
    """Fingerabdruck der Bars, gegen die ein Lauf rechnet (ADR-079).

    Ein fortgesetzter Lauf muss dieselben Daten sehen wie sein Anfang. Sonst
    screent er die ersten Kandidaten gegen einen Datenstand und den Rest
    gegen einen anderen, und die Charge ist keine Charge mehr. Die Anzahl
    allein reicht nicht: Tiingo adjustiert rueckwirkend (ADR-055), dann
    bleibt die Zahl gleich und die Kurse aendern sich.
    """
    stand: dict[str, dict[str, Any]] = {}
    for symbol in sorted(bars):
        reihe = bars[symbol]
        h = hashlib.sha256()
        for b in reihe:
            h.update(
                f"{b.ts.isoformat()}|{b.open!r}|{b.high!r}|{b.low!r}|"
                f"{b.close!r}|{b.volume!r}\n".encode()
            )
        stand[symbol] = {
            "n": len(reihe),
            "bis": reihe[-1].ts.isoformat() if reihe else None,
            "hash": h.hexdigest()[:16],
        }
    return stand


def auf_datenstand(
    bars: Mapping[str, list[Bar]], stand: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, list[Bar]], list[str]]:
    """Bars auf den Stand beim Start eines Laufs zurueckschneiden.

    Neue Bars seit dem Start fallen weg, das ist erwartbar und kein Fehler.
    Weicht der Rest ab, gibt es eine Liste der Abweichungen: dann haben sich
    Kurse nachtraeglich geaendert, und der Lauf laesst sich nicht ehrlich
    fortsetzen.
    """
    gekuerzt: dict[str, list[Bar]] = {}
    abweichungen: list[str] = []
    for symbol, soll in stand.items():
        reihe = list(bars.get(symbol, []))
        if soll.get("bis") is not None:
            grenze = datetime.fromisoformat(soll["bis"])
            reihe = [b for b in reihe if b.ts <= grenze]
        gekuerzt[symbol] = reihe
        ist = datenstand({symbol: reihe})[symbol]
        if ist["n"] != soll.get("n"):
            abweichungen.append(
                f"{symbol}: beim Start {soll.get('n')} Bars bis {soll.get('bis')}, "
                f"jetzt {ist['n']}"
            )
        elif ist["hash"] != soll.get("hash"):
            abweichungen.append(
                f"{symbol}: gleiche Zahl Bars, aber andere Kurse "
                "(rueckwirkend adjustiert?)"
            )
    return gekuerzt, abweichungen


def bereits_geprueft(registry) -> list[str]:
    """Ansaetze, die dieses Projekt schon durchgerechnet hat.

    Zwei Quellen, weil es zwei Arten von Vorwissen gibt: die Bibliothek (was
    von Hand gebaut und verworfen wurde) und die Registry (was der Loop schon
    erzeugt hat). Beide zaehlen im DSR-Nenner, also gehoeren beide ins
    Briefing.

    **Nur Namen, keine Ergebnisse.** Wer dem Generator sagt, welcher Ansatz
    wie gut war, laesst ihn in der Naehe der besten bisherigen Zahl suchen --
    und das ist Overfitting mit einem Umweg ueber ein Sprachmodell.
    """
    namen: list[str] = []

    try:
        from qt.strategy.registry import load_library, names

        load_library()
        namen += list(names())
    except Exception:  # noqa: BLE001 -- ohne Bibliothek laeuft der Loop trotzdem
        pass

    try:
        frame = registry.history()
        if "class_name" in frame:
            namen += [str(x) for x in frame["class_name"].dropna().unique()]
    except Exception:  # noqa: BLE001 -- eine leere Registry ist kein Fehler
        pass

    return sorted({n for n in namen if n})
