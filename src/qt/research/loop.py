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

from dataclasses import dataclass, field

from qt.core.config import BacktestConfig
from qt.core.types import Bar
from qt.research import critic as critic_mod
from qt.research import sandbox
from qt.research.dsr import DEFAULT_THRESHOLD
from qt.research.generator import build_generation_briefing
from qt.research.registry import (
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
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"erzeugt {self.generated}, Sandbox verworfen {self.sandbox_rejected}, "
            f"Probelauf verworfen {self.probe_rejected}, Kritik abgelehnt "
            f"{self.critic_rejected}, Sanity verworfen {self.sanity_rejected}, "
            f"gescreent {self.screened}, davon bestanden {self.passed}"
        )

    def table(self) -> str:
        rows = (
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
    """
    telemetry = ResearchTelemetry()
    if registry is None:
        raise ValueError(
            "Ohne Registry gibt es keinen persistenten Versuchszaehler und "
            "damit keine belastbare Deflated Sharpe Ratio (ADR-005)."
        )

    seen: list[str] = []
    # Was das Projekt schon geprueft hat -- Bibliothek plus Registry. Ohne
    # diese Liste schlaegt der Generator zuverlaessig wieder SMA-Kreuzung,
    # Donchian-Ausbruch und z-Score-Reversion vor: drei von fuenf Versuchen
    # im Lauf vom 2026-09-03 gingen genau dafuer drauf (ADR-065).
    bereits = bereits_geprueft(registry)

    for index in range(n):
        briefing = build_generation_briefing(index, n, seen, bereits_geprueft=bereits)
        try:
            proposal = generator_client.propose(briefing)
        except Exception as exc:  # noqa: BLE001 -- eine Charge stirbt nicht am Netz
            telemetry.generator_errors += 1
            telemetry.notes.append(f"Generator {index + 1}: {type(exc).__name__}: {exc}")
            continue

        telemetry.generated += 1
        seen.append(f"{proposal.name}: {proposal.rationale[:80]}")

        candidate_id = registry.record_generated(
            class_name=proposal.class_name,
            code=proposal.code,
            rationale=proposal.rationale,
            generator_model=getattr(generator_client, "model", ""),
            generator_effort=getattr(generator_client, "effort", ""),
        )
        _say(echo, f"[{index + 1}/{n}] {proposal.name}")

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

        # --- Stufe 3: die Kritik, billiger Vorfilter vor dem Backtest -----
        if use_critic and critic_client is not None:
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
                overfitting_risk=verdict.overfitting_risk,
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

    return telemetry


def _say(echo, message: str) -> None:
    if echo is not None:
        echo(message)


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
