"""Der Generator: das Modell schreibt Strategie-Kandidaten.

Anders als der Allokator bekommt der Generator **keine einzige aus Marktdaten
abgeleitete Zahl** zu sehen -- auch keine anonymisierte.

Das ist die konsequente Fortsetzung von ADR-003/ADR-017, nicht eine Ausnahme
davon. Beim Allokator ist Anonymisierung noetig, weil seine Aufgabe darin
besteht, auf aktuelle Kennzahlen zu reagieren: man kann ihm die Daten nicht
vorenthalten, nur unkenntlich machen. Der Generator dagegen soll *generischen*
Code schreiben, der spaeter gegen ihm unbekannte Daten laeuft. Er hat keinen
legitimen Grund, irgendeine datenabgeleitete Zahl zu sehen -- und was er nicht
sieht, kann er auch nicht versehentlich hineinoptimieren.

Was er stattdessen bekommt: die API-Beschreibung (im eingefrorenen
Systemprompt) und einen Diversitaets-Hinweis, damit `--generate 20` nicht
zwanzigmal dieselbe Idee liefert.
"""

from __future__ import annotations

from qt.llm.schemas import StrategyCandidateProposal

# Mehr als das im Briefing aufzuzaehlen hilft nicht mehr gegen Wiederholung
# und verlaengert nur jedes Prompt in der Charge.
MAX_LISTED_PREVIOUS = 12


def build_generation_briefing(
    index: int, total: int, previous: list[str] | None = None
) -> str:
    """Das Briefing fuer einen einzelnen Kandidaten.

    Enthaelt bewusst nur drei Dinge: die Position in der Charge, die bereits
    verwendeten Ansaetze, und die Aufforderung. Keine Kurse, keine Kennzahlen,
    keine Zeitraeume, keine Asset-Namen.

    Die Liste der bisherigen Ansaetze ist der einzige Grund, warum die
    Kandidaten einer Charge nacheinander statt nebeneinander erzeugt werden.
    Ohne sie liefert dasselbe Modell auf dasselbe Prompt zwanzigmal die
    naheliegendste Idee -- und zwanzig Varianten derselben Idee sind fuer die
    Deflated Sharpe Ratio trotzdem zwanzig Versuche.
    """
    previous = previous or []
    lines = [
        f"Kandidat {index + 1} von {total}.",
        "",
        "Entwirf eine Handelsstrategie und schreibe sie als Klasse in der",
        "vorgegebenen Form.",
    ]

    if previous:
        shown = previous[-MAX_LISTED_PREVIOUS:]
        lines += [
            "",
            "In dieser Charge gibt es bereits:",
            *(f"  - {name}" for name in shown),
            "",
            "Waehle einen anderen Ansatz. Eine Variante mit anderem Lookback",
            "ist kein anderer Ansatz -- sie ist derselbe Versuch mit einer",
            "weiteren Stellschraube.",
        ]
    else:
        lines += [
            "",
            "Der erste Kandidat der Charge. Faengt gern schlicht an: eine",
            "einfache Idee, die man verstehen kann, ist als Ausgangspunkt",
            "mehr wert als eine komplizierte, die man nicht widerlegen kann.",
        ]

    return "\n".join(lines)


def generate_batch(
    client, n: int, on_candidate=None
) -> list[StrategyCandidateProposal]:
    """`n` Kandidaten nacheinander erzeugen.

    Sequentiell und nicht parallel, weil jedes Briefing die bereits erzeugten
    Namen kennt (siehe `build_generation_briefing`). Das kostet Laufzeit und
    spart Versuche -- der bessere Tausch, solange jeder Versuch die
    DSR-Schwelle fuer alle folgenden anhebt.

    Ein fehlgeschlagener Aufruf beendet die Charge **nicht**: `on_candidate`
    bekommt dann `None` und die Schleife laeuft weiter. Zwanzig Kandidaten
    wegzuwerfen, weil der neunzehnte Aufruf in ein Zeitlimit lief, waere die
    teuerste denkbare Reaktion auf einen Netzwerkfehler.
    """
    out: list[StrategyCandidateProposal] = []
    seen: list[str] = []

    for index in range(n):
        briefing = build_generation_briefing(index, n, seen)
        try:
            proposal = client.propose(briefing)
        except Exception as exc:  # noqa: BLE001 -- siehe Docstring
            if on_candidate is not None:
                on_candidate(index, None, exc)
            continue

        out.append(proposal)
        seen.append(f"{proposal.name}: {proposal.rationale[:80]}")
        if on_candidate is not None:
            on_candidate(index, proposal, None)

    return out
