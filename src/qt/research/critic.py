"""Die Kritik-Stufe: ein zweites Modell sucht Gruende, nicht zu testen.

Der Gedanke stammt aus Multi-Agenten-Handelsframeworks, die Bull und Bear
gegeneinander debattieren lassen -- aber er sitzt hier an einer anderen
Stelle der Kette, und das ist der ganze Unterschied.

Dort urteilt das Debattenteam ueber eine **live auszufuehrende** Entscheidung;
ein Fehlurteil kostet echtes Geld. Hier sitzt die Kritik **im Forschungslauf,
vor dem teuren Backtest**, und selbst ein voll akzeptierter, DSR-bestandener
Kandidat erreicht die Bibliothek nur ueber manuelle Freigabe. Der Schaden
eines Fehlurteils ist damit im schlimmsten Fall verschwendete oder gesparte
Rechenzeit, nie eine falsche Order.

Zwei Eigenschaften, die die Stufe von einem Veto unterscheiden:

1. **Eine Ablehnung verwirft nichts.** Sie ueberspringt den Walk-Forward-Lauf;
   der Kandidat liegt samt Code und Begruendung in der Registry. Wer die
   Begruendung fuer falsch haelt, laesst den Lauf mit `--no-critic` erneut
   laufen. Dieselbe Haltung wie ADR-018/ADR-021: eine LLM-Entscheidung faellt
   auf einen sichtbaren Zustand zurueck, nie auf ein stilles Verschwinden.
2. **Ein Ausfall des Kritikers laesst durch, er blockiert nicht.** Ein
   Vorfilter, der bei einem Netzwerkfehler alles anhaelt, hat aus einer
   Sparmassnahme einen Single Point of Failure gemacht.
"""

from __future__ import annotations

from qt.llm.schemas import CandidateCritique
from qt.research.sandbox import LiteralFlag, ProbeReport

# Der einzige datenabgeleitete Wert im Briefing, und er ist in Bars gezaehlt.
#
# Um "zu viele Freiheitsgrade fuer die Datenmenge" beurteilen zu koennen, muss
# der Kritiker die Groessenordnung der Testdaten kennen. In Bars statt in
# Kalenderzeit ist das dieselbe Konvention wie im Allokations-Briefing
# (ADR-017): fuer die Einschaetzung reicht es, fuer das Wiedererkennen eines
# Zeitraums nicht.
_ROUND_OOS_BARS_TO = 500


def build_critique_briefing(
    proposal,
    probe: ProbeReport | None = None,
    literals: list[LiteralFlag] | None = None,
    oos_bars: int | None = None,
    round_trip_bps: float | None = None,
) -> str:
    """Das Briefing fuer die Kritik: Code, Begruendung, harte Vorbefunde.

    Die Vorbefunde (`literals`, Parameterzahl) sind deterministisch ermittelt
    und werden dem Modell **mitgeteilt**, statt es sie suchen zu lassen. Das
    ist dieselbe Kostenrechnung wie ueberall sonst: was ein AST-Durchlauf
    umsonst findet, soll kein Modell fuer Geld nacherzaehlen. Das Modell soll
    die Frage beantworten, die der AST-Durchlauf nicht beantworten kann --
    ob eine auffaellige Zahl ein legitimer Lookback oder ein Kursniveau ist.

    `round_trip_bps=None` zieht den Wert aus dem geltenden Kostenmodell,
    statt ihn zu kopieren. Vorher stand hier fest 90.0 -- der Default hat sich
    am 2026-09-02 auf 130 geaendert (ADR-056), und eine kopierte Zahl haette
    dem Kritiker weiter die alte genannt, ohne dass etwas fehlschlaegt.
    """
    if round_trip_bps is None:
        from qt.backtest.costs import round_trip_bps as _rt
        from qt.core.config import CostConfig

        kosten_bps = _rt(CostConfig())
    else:
        kosten_bps = round_trip_bps

    lines = [
        "Pruefe diesen Strategie-Kandidaten, bevor er einen Walk-Forward-Lauf",
        "bekommt.",
        "",
        f"Begruendung des Entwurfs: {proposal.rationale or '(keine)'}",
        "",
        "Code:",
        "```python",
        proposal.code.rstrip(),
        "```",
        "",
        f"Handelskosten im Test: {kosten_bps:.0f} Basispunkte Round-Trip.",
    ]

    if oos_bars is not None:
        rounded = max(
            _ROUND_OOS_BARS_TO,
            round(oos_bars / _ROUND_OOS_BARS_TO) * _ROUND_OOS_BARS_TO,
        )
        lines.append(
            f"Out-of-Sample stehen groessenordnungsmaessig {rounded} Bars zur "
            "Verfuegung."
        )

    n_constants = _count_constants(proposal.code)
    lines.append(f"Der Code setzt {n_constants} Klassenkonstanten als Parameter.")

    if probe is not None and probe.ok:
        lines.append(
            f"Ein technischer Probelauf ist durchgelaufen (warmup_bars="
            f"{probe.warmup_bars})."
        )

    if literals:
        lines += [
            "",
            "Ein AST-Durchlauf hat folgende Zahlen im Vergleich mit einer",
            "preisverdaechtigen Variablen gefunden:",
            *(f"  - Zeile {f.line}: {f.value:g} ({f.context})" for f in literals),
            "",
            "Entscheide, ob das legitime Parameter sind oder an ein Kursniveau",
            "gebundene Konstanten.",
        ]

    return "\n".join(lines)


def critique(
    client,
    proposal,
    probe: ProbeReport | None = None,
    literals: list[LiteralFlag] | None = None,
    oos_bars: int | None = None,
) -> tuple[CandidateCritique, str | None]:
    """Kritik einholen. Gibt (Urteil, Fehlermeldung-oder-None) zurueck.

    Bei einem Ausfall wird ein "proceed"-Urteil erzeugt und der Grund
    mitgeliefert -- der Aufrufer soll den Ausfall protokollieren koennen, ohne
    dass die Pipeline stehenbleibt. Ein durchgelassener Kandidat kostet einen
    Backtest; ein blockierter Lauf kostet die ganze Charge.
    """
    briefing = build_critique_briefing(proposal, probe, literals, oos_bars)
    try:
        return client.critique(briefing), None
    except Exception as exc:  # noqa: BLE001 -- siehe Docstring
        fallback = CandidateCritique(
            recommendation="proceed",
            reasoning=f"Kritik nicht verfuegbar ({type(exc).__name__}): {exc}",
        )
        return fallback, f"{type(exc).__name__}: {exc}"


def _count_constants(code: str) -> int:
    """Klassenkonstanten in GROSSBUCHSTABEN zaehlen.

    Deterministisch ermittelt statt vom Modell geschaetzt: die Zahl der
    Freiheitsgrade ist genau die Groesse, ueber die der Kritiker urteilen
    soll -- sie ihn selbst zaehlen zu lassen, hiesse den Massstab von dem
    ableiten, was gemessen wird.
    """
    n = 0
    for raw in code.splitlines():
        line = raw.strip()
        if "=" not in line or line.startswith("#"):
            continue
        head = line.split("=", 1)[0].strip()
        if head.isupper() and head.isidentifier():
            n += 1
    return n
