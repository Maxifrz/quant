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
    index: int,
    total: int,
    previous: list[str] | None = None,
    bereits_geprueft: list[str] | None = None,
) -> str:
    """Das Briefing fuer einen einzelnen Kandidaten.

    Enthaelt bewusst nur vier Dinge: die Position in der Charge, die bereits
    verwendeten Ansaetze **dieser Charge**, die bereits geprueften Ansaetze
    **aus der Vergangenheit**, und die Aufforderung. Keine Kurse, keine
    Kennzahlen, keine Zeitraeume, keine Asset-Namen.

    Die Liste der bisherigen Ansaetze ist der einzige Grund, warum die
    Kandidaten einer Charge nacheinander statt nebeneinander erzeugt werden.
    Ohne sie liefert dasselbe Modell auf dasselbe Prompt zwanzigmal die
    naheliegendste Idee -- und zwanzig Varianten derselben Idee sind fuer die
    Deflated Sharpe Ratio trotzdem zwanzig Versuche.

    **`bereits_geprueft` ist dieselbe Ueberlegung mit laengerem Gedaechtnis,
    und sie ist gemessen entstanden (ADR-065).** Die Charge kannte sich
    selbst, aber nicht die Bibliothek: der Lauf vom 2026-09-03 lieferte
    `SMACross50_200`, `DonchianBreakout` und `ZScoreMeanReversion` -- also
    `macross`, `trend` und `meanrev`, die seit Phase 1 im Repo stehen und
    laengst gescheitert sind. **Drei von fuenf Versuchen** gingen dafuer
    drauf, und der Zaehler vergisst sie nie (ADR-032).

    Es weicht das blinde Briefing nicht auf (ADR-003): eine Liste von
    Ansatznamen enthaelt keine Kurse, keine Kennzahlen und keinen Markt. Sie
    sagt, was schon versucht wurde, nicht was funktioniert hat -- die
    Ergebnisse bleiben draussen, sonst suchte das Modell in der Naehe der
    besten bisherigen Zahl.

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
    bereits_geprueft = bereits_geprueft or []
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

    if bereits_geprueft:
        lines += [
            "",
            "Diese Ansaetze sind bereits geprueft und gescheitert:",
            *(f"  - {name}" for name in sorted(set(bereits_geprueft))),
            "",
            "Sie noch einmal vorzuschlagen kostet einen Versuch und liefert",
            "kein neues Wissen. Das gilt auch fuer eine Umbenennung oder eine",
            "andere Parameterwahl derselben Mechanik.",
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
