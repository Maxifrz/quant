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

    **Die Grenzen aus Gate 1 stehen mit drin, und das ist keine Aufweichung
    des blinden Briefings** (ADR-003). Blind heisst: keine Kurse, keine
    Kennzahlen, keine Zeitraeume, keine Marktnamen -- nichts, woran sich eine
    Idee an *diese* Daten anpassen liesse. Die Handelsfrequenz ist nichts
    davon; sie folgt aus dem Gebuehrenplan der Boerse (ADR-056) und stuende
    genauso fest, wenn die Daten andere waeren.

    Der Anlass ist gemessen: der erste Lauf ueber die Bibliothek zeigte, dass
    fuenf von sieben Strategien am Umschlagbudget scheitern, und die acht
    Kandidaten des Loops vom 31.08. stammen aus derselben Familie. Ein
    Generator, der das nicht weiss, laeuft gegen eine Wand, die er nicht
    sieht.
    """
    from qt.research.gate import MAX_UMSCHLAG_PRO_JAHR, MIN_FILLS

    previous = previous or []
    lines = [
        f"Kandidat {index + 1} von {total}.",
        "",
        "Entwirf eine Handelsstrategie und schreibe sie als Klasse in der",
        "vorgegebenen Form.",
        "",
        "Zwei harte Grenzen, an denen die meisten Entwuerfe scheitern:",
        "",
        f"  1. Die Strategie darf hoechstens {MAX_UMSCHLAG_PRO_JAHR:.0f} mal "
        "ihr Eigenkapital pro Jahr",
        "     umschlagen. Das ist die Grenze, ab der die Ausfuehrungskosten",
        "     mehr fressen, als das Signal einbringen kann. Ein taeglich",
        "     wechselndes Gewicht ist damit ausgeschlossen; ein Signal, das",
        "     einige Wochen steht, ist es nicht.",
        f"  2. Mindestens {MIN_FILLS} Ausfuehrungen ueber die Historie. Eine",
        "     Strategie, die fast nie handelt, ist nicht pruefbar -- sie",
        "     erfuellt jedes Kostenkriterium durch Nichtstun.",
        "",
        "Beides betrifft die Handelsfrequenz, nicht die Idee. Gesucht ist ein",
        "Signal, das traege ist, weil es etwas Traeges misst -- nicht eines,",
        "das durch einen Glaettungsparameter kuenstlich langsam gemacht wurde.",
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
