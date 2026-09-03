"""Die sieben handgeschriebenen Hypothesen als das eintragen, was sie sind: Versuche.

Der Versuchszaehler stand am 2026-09-02 bei **8**. Alle acht stammen aus dem
LLM-Research-Loop vom 31.08. Die sieben Strategien in
`qt/strategy/library/` -- `trend`, `meanrev`, `elliott`, `macross`,
`hashribbon`, `orderflow`, `timesfm` -- tauchten dort **nicht** auf.

Das ist ein Buchhaltungsfehler mit statistischer Wirkung. Jede dieser sieben
ist eine Hypothese, die an *diesen* Daten geprueft wurde, jede hat ein ADR mit
einem Walk-Forward-Ergebnis, und `ZIEL.md` sagt selbst: "Sieben Hypothesen
geprueft, sieben gescheitert." Das Projekt weiss also, dass es sieben Blicke
getan hat -- nur die Zahl, die in die Deflated Sharpe Ratio eingeht, wusste es
nicht.

ADR-032 sagt "nur abgeschlossenes Screening zaehlt als Versuch". Das war eine
Entscheidung ueber die Buchfuehrung des Research-Loops, nicht ueber die Frage,
was ein Blick auf die Daten ist. ADR-005 ist da eindeutiger und aelter: die
Registry zaehlt **alle je getesteten** Kandidaten. Die sieben ueber eine
Formalie auszunehmen -- sie kamen nicht durch den Loop -- waere genau die
Sorte Technikalitaet, die einen Schutz zur Zierde macht.

**Die Richtung stimmt, und das ist der Punkt.** Der Zaehler steigt von 8 auf
15, die DSR-Huerde wird fuer jeden kuenftigen Kandidaten **haerter**. Eine
Korrektur der Buchfuehrung, die das eigene Ergebnis verbessert, waere
verdaechtig; diese verschlechtert es.

Der Eintrag ist idempotent: er erkennt seine eigenen Zeilen am Generatorfeld
und legt sie kein zweites Mal an.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Kennzeichnung im Feld `generator_model`. Die Zeilen stammen nicht von einem
# Modell, und sie sollen sich von denen des Loops unterscheiden lassen.
QUELLE = "handschrift"


@dataclass(frozen=True, slots=True)
class Hypothese:
    """Eine handgeschriebene Strategie mit dem ADR, das ihr Ergebnis haelt."""

    klasse: str
    modul: str
    adr: str
    befund: str
    datum: str


# Reihenfolge nach Entstehung, damit der nachgetragene Zaehler die echte
# Abfolge der Blicke abbildet und nicht die alphabetische.
HYPOTHESEN: tuple[Hypothese, ...] = (
    Hypothese(
        "TrendStrategy", "trend", "ADR-009",
        "Donchian-Ausbruch. Signal hat eine Kante, die Gebuehren fressen sie "
        "auf 4h vollstaendig.",
        "2026-08-19",
    ),
    Hypothese(
        "MeanReversionStrategy", "meanrev", "ADR-009",
        "Rueckkehr zum Mittel. Scheitert am Signal, nicht an den Kosten: "
        "auch ohne jede Gebuehr negativ.",
        "2026-08-19",
    ),
    Hypothese(
        "ElliottStrategy", "elliott", "ADR-033",
        "Mechanisierte Wellenzaehlung. Findet Muster, die Muster sagen nichts "
        "ueber den naechsten Bar.",
        "2026-08-27",
    ),
    Hypothese(
        "MovingAverageCrossStrategy", "macross", "ADR-035",
        "Gleitende Durchschnitte auf 1d. Die einzige mit positivem OOS -- und "
        "an der Permutationskontrolle gescheitert (ADR-054).",
        "2026-08-29",
    ),
    Hypothese(
        "TimesFMStrategy", "timesfm", "ADR-022",
        "Fremdes Zeitreihenmodell als Signalgeber. Offen als unzuverlaessig "
        "markiert.",
        "2026-08-25",
    ),
    Hypothese(
        "OrderFlowStrategy", "orderflow", "ADR-034",
        "Aggressor-Fluss von Kraken auf Coinbase-Kurse. Neue Informationsachse, "
        "fremde Quelle, unbewiesene Uebertragbarkeit.",
        "2026-08-28",
    ),
    Hypothese(
        "CrossMomentum", "cross_sectional", "ADR-058",
        "12-1-Momentum im Querschnitt der 27 Maerkte, monatlich umgeschichtet. "
        "Erste Strategie des Projekts, die Aktivitaet und Umschlagbudget "
        "besteht -- und im Walk-Forward bei OOS-Sharpe -0,24 landet.",
        "2026-09-03",
    ),
    Hypothese(
        "HashRibbonStrategy", "hashribbon", "ADR-048",
        "Miner-Kapitulation ueber die Hashrate. BTC-spezifisch und nicht von "
        "Zufall zu unterscheiden.",
        "2026-08-30",
    ),
)


def bereits_eingetragen(registry) -> set[str]:
    """Klassennamen, die aus dieser Quelle schon in der Registry stehen."""
    df = registry.history()
    if df.empty or "generator_model" not in df.columns:
        return set()
    eigene = df[df["generator_model"] == QUELLE]
    return set(eigene["class_name"].dropna().astype(str))


def nachtragen(registry, *, dry_run: bool = False) -> list[str]:
    """Fehlende Hypothesen eintragen, gibt die neu angelegten Klassennamen zurueck.

    Jede bekommt `screening_status='rejected'` -- keine der sieben hat je ein
    Gate bestanden, und genau dieses Feld macht sie zum Versuch.

    Der Sharpe wird **nicht** mitgeschrieben. Die Zahlen der sieben stammen aus
    verschiedenen Zeitebenen, Zeitraeumen und Kostenannahmen; eine davon hier
    als vergleichbare Kennzahl abzulegen waere eine Praezision, die es nie
    gab. Was zaehlbar ist, ist der Versuch, und der steht in `screening_status`.
    """
    vorhanden = bereits_eingetragen(registry)
    neu: list[str] = []

    for h in HYPOTHESEN:
        if h.klasse in vorhanden:
            continue
        neu.append(h.klasse)
        if dry_run:
            continue

        stempel = datetime.fromisoformat(h.datum).replace(tzinfo=timezone.utc)
        cid = registry.record_generated(
            h.klasse,
            f"# Der Code steht in src/qt/strategy/library/{h.modul}.py.\n"
            f"# Diese Zeile traegt den Versuch nach, nicht den Quelltext:\n"
            f"# eine Bibliotheksstrategie wird gepflegt, ein Kandidat ist ein\n"
            f"# Schnappschuss, und beides in einem Feld zu fuehren waere eine\n"
            f"# Kopie, die still veraltet.\n",
            rationale=f"{h.adr}: {h.befund}",
            generator_model=QUELLE,
            generator_effort="",
            created_at=stempel,
        )
        registry.record_screening(
            cid,
            status="rejected",
            n_windows=0,
            oos_bars=0,
            sharpe=float("nan"),
            dsr=None,
            dsr_threshold=None,
        )
    return neu
