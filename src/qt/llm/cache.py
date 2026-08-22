"""Disk-Cache fuer LLM-Antworten.

Ein Backtest ueber den LLM-Allokator ruft das Modell hunderte bis tausende
Male auf. Ohne Cache waere so ein Lauf weder reproduzierbar noch bezahlbar:
dieselbe Konfiguration zweimal gestartet ergaebe zwei verschiedene Kurven,
und jeder Lauf kostet echtes Geld. Mit Cache kostet der erste Lauf, jeder
weitere ist gratis und liefert exakt dieselben Vorschlaege -- erst damit ist
der Allokator ueberhaupt gegen die Baselines aus `qt.portfolio.baselines`
vergleichbar (ADR-004).

Der Cache ist eine **Optimierung, keine Datenquelle**. Alles, was schiefgehen
kann -- halb geschriebene Datei, von Hand verfaelschter Inhalt, Eintrag aus
einer aelteren Schema-Fassung -- fuehrt zu einem Fehlschlag und einem frischen
Aufruf, niemals zu einem Abbruch und niemals zu einem falschen Treffer.

Layout: `.llm_cache/<zwei Hex-Zeichen>/<Rest des Hashes>.json`

Die Aufteilung in 256 Unterverzeichnisse ist kein Selbstzweck: ein einzelnes
Verzeichnis mit zehntausenden Dateien wird auf den gaengigen Dateisystemen
beim Auflisten und Anlegen spuerbar langsam, und ein `ls` darin ist
unbenutzbar. Zwei Hex-Zeichen streuen gleichmaessig (der Hash ist
gleichverteilt) und halten die Fanout-Kosten konstant. Dieselbe Bauform wie
die Objektablage von git, aus demselben Grund.

Die Dateien enthalten bewusst keinen Zeitstempel: ein zweiter Lauf mit
identischen Eingaben soll bitgleiche Cache-Dateien erzeugen, sonst ist
"reproduzierbar" nur behauptet und nicht pruefbar.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from qt.core.config import DEFAULT_LLM_MODEL, PROJECT_ROOT
from qt.llm.schemas import AllocationProposal

CACHE_DIR = PROJECT_ROOT / ".llm_cache"

# Default-Modell. Steht im Key, siehe `LLMCache.key`.
# Gemeinsamer Default mit `qt.llm.client` -- siehe dort, warum das
# an genau einer Stelle stehen muss.
DEFAULT_MODEL = DEFAULT_LLM_MODEL

# Fassung von `qt.llm.schemas`. **Muss hochgezaehlt werden, sobald sich
# `AllocationProposal` aendert.** Sonst liefert ein alter Cache stillschweigend
# Antworten in der alten Form -- mit Feldern, die es nicht mehr gibt, oder
# ohne Felder, die inzwischen erwartet werden.
#
# Bewusst eine Konstante und nicht aus `model_json_schema()` abgeleitet:
# abgeleitet haenge der Key an der Schema-Serialisierung von pydantic, und ein
# Bibliotheks-Update entwertete den gesamten Cache lautlos. Der Preis ist, dass
# das Hochzaehlen von Hand passiert -- deshalb steht der Hinweis hier und nicht
# in einer Doku, die niemand liest.
SCHEMA_VERSION = 1

# Zwei Zeichen -> 256 Unterverzeichnisse. Siehe Modul-Docstring.
_SHARD_CHARS = 2

# Endung der temporaeren Datei beim atomaren Schreiben. Wichtig ist nur, dass
# sie *nicht* auf .json endet: ein nach einem harten Abbruch liegengebliebener
# Rest darf beim naechsten Lauf unter keinen Umstaenden als Treffer gelesen
# werden.
_TMP_SUFFIX = ".partial"


@dataclass(frozen=True, slots=True)
class CacheStats:
    """Was der Cache waehrend eines Laufs getan hat.

    Die Trefferquote ist die eigentliche Diagnose: ein Cache mit 0% ueber
    einen ganzen Lauf bedeutet nicht "noch kalt", sondern fast immer einen
    instabilen Key -- ein Wert im Prompt, der sich bei jedem Aufruf aendert.
    Ohne diese Zahl bleibt der Fehler monatelang unsichtbar, weil alles
    funktioniert; es kostet nur jedes Mal Geld.
    """

    hits: int = 0
    misses: int = 0
    errors: int = 0
    entries: int = 0
    bytes: int = 0

    @property
    def lookups(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        """Trefferquote, 0.0 wenn noch nichts abgefragt wurde."""
        return self.hits / self.lookups if self.lookups else 0.0

    def describe(self) -> str:
        return (
            f"{self.hits} Treffer / {self.misses} Fehlschlaege "
            f"({self.hit_rate:.0%}), davon {self.errors} defekte Eintraege; "
            f"{self.entries} Dateien, {self.bytes / 1024:.1f} KiB"
        )


class LLMCache:
    """Antworten des Allokators auf der Platte, adressiert ueber ihren Inhalt.

    Der Key wird aus Prompt, Modell-ID und Schema-Version gebildet. Alle drei
    gehoeren hinein: dieselbe Frage an zwei Modelle sind zwei Antworten, und
    ein Eintrag aus einer aelteren Schema-Fassung ist keine Antwort mehr,
    sondern Muell in der Form einer Antwort.
    """

    def __init__(
        self,
        path: Path | None = None,
        model: str = DEFAULT_MODEL,
        schema_version: int = SCHEMA_VERSION,
    ) -> None:
        self.path = Path(path) if path is not None else CACHE_DIR
        self.model = model
        self.schema_version = schema_version
        self._hits = 0
        self._misses = 0
        self._errors = 0

    # -- Key ----------------------------------------------------------------

    def key(self, prompt: str, **extra: Any) -> str:
        """Stabiler Hash ueber Prompt, Modell, Schema-Version und `extra`.

        `extra` nimmt alles auf, was die Antwort mitbestimmt, aber nicht im
        Prompt-Text steht -- Temperatur, Seed, Timeframe. Was die Antwort
        beeinflusst und hier fehlt, erzeugt einen falschen Treffer; das ist
        die einzige Art von Fehler, die dieser Cache nicht selbst abfangen
        kann.

        Gehasht wird der **Inhalt**, nie die Objekt-Identitaet: `sort_keys`
        sorgt dafuer, dass zwei Dicts mit gleichen Paaren in anderer
        Einfuegereihenfolge denselben Key ergeben. Ohne das haette der Cache
        eine Trefferquote nahe null, ohne dass irgendwo etwas fehlschluege.
        """
        payload = {
            "model": self.model,
            "schema_version": self.schema_version,
            "prompt": prompt,
            "extra": extra,
        }
        blob = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=_content_of,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def path_for(self, key: str) -> Path:
        """Ablageort eines Keys. Oeffentlich, damit Tests nicht raten muessen."""
        return self.path / key[:_SHARD_CHARS] / f"{key[_SHARD_CHARS:]}.json"

    # -- Lesen und Schreiben ------------------------------------------------

    def get(self, key: str) -> AllocationProposal | None:
        """Eintrag lesen, oder `None` wenn es keinen brauchbaren gibt.

        Jeder denkbare Defekt -- fehlende Datei, Muell-Bytes, leere Datei,
        gueltiges JSON mit falschen Feldern, Eintrag eines anderen Modells --
        ist hier ein Fehlschlag und keine Ausnahme. Ein Cache darf einen Lauf
        nicht abbrechen; der Aufrufer fragt dann eben das Modell.
        """
        file = self.path_for(key)
        try:
            raw = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self._misses += 1
            return None

        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return self._broken()

        if not isinstance(entry, dict):
            return self._broken()

        # Der Dateiname allein ist keine Garantie: eine von Hand kopierte oder
        # umbenannte Datei liegt an der richtigen Stelle und enthaelt trotzdem
        # die Antwort auf eine andere Frage. Deshalb steht der Key im Eintrag
        # und wird gegengeprueft.
        if (
            entry.get("key") != key
            or entry.get("model") != self.model
            or entry.get("schema_version") != self.schema_version
        ):
            return self._broken()

        try:
            return self._hit(AllocationProposal.model_validate(entry["value"]))
        except (ValidationError, KeyError, TypeError):
            return self._broken()

    def put(self, key: str, value: AllocationProposal) -> None:
        """Eintrag atomar schreiben: erst temporaer, dann `os.replace`.

        Wuerde direkt in die Zieldatei geschrieben, hinterliesse ein zur
        Unzeit abgebrochener Lauf eine halbe JSON-Datei -- und die waere beim
        naechsten Mal ein Fehlschlag, im schlimmsten Fall aber gerade noch
        parsebar und damit ein falscher Treffer. `os.replace` ist auf einem
        Dateisystem atomar: entweder der alte Eintrag oder der neue, nie etwas
        dazwischen. Deshalb liegt die temporaere Datei im selben Verzeichnis
        wie das Ziel -- ueber eine Dateisystemgrenze hinweg gaebe es diese
        Garantie nicht.
        """
        entry = {
            "key": key,
            "model": self.model,
            "schema_version": self.schema_version,
            "value": value.model_dump(mode="json"),
        }
        file = self.path_for(key)
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_name(f"{file.name}.{os.getpid()}{_TMP_SUFFIX}")

        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(entry, handle, sort_keys=True, ensure_ascii=False, indent=1)
                handle.flush()
                # Ohne fsync kann die Umbenennung nach einem Stromausfall vor
                # den Daten auf der Platte stehen -- der Eintrag existiert dann
                # unter dem richtigen Namen und ist leer.
                os.fsync(handle.fileno())
            os.replace(tmp, file)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    # -- Statistik ----------------------------------------------------------

    def stats(self) -> CacheStats:
        """Zaehler des laufenden Prozesses plus Groesse der Ablage."""
        entries = 0
        total = 0
        for file in self.path.rglob("*.json"):
            if file.is_file():
                entries += 1
                total += file.stat().st_size
        return CacheStats(
            hits=self._hits,
            misses=self._misses,
            errors=self._errors,
            entries=entries,
            bytes=total,
        )

    # -- Intern -------------------------------------------------------------

    def _hit(self, value: AllocationProposal) -> AllocationProposal:
        self._hits += 1
        return value

    def _broken(self) -> None:
        """Defekter Eintrag: zaehlt als Fehlschlag *und* als Fehler.

        Die Datei bleibt liegen. Sie ueberschreibt sich beim naechsten `put`
        von selbst, und ein Cache, der beim Lesen Dateien loescht, nimmt einem
        die Moeglichkeit nachzusehen, was da eigentlich kaputt war.
        """
        self._misses += 1
        self._errors += 1
        return None


def _content_of(value: Any) -> Any:
    """Fallback fuer `json.dumps`: Werte, die keinen JSON-Typ haben.

    Erlaubt ist nur, was einen inhaltlich stabilen Ausdruck hat. Kein
    `repr()`-Fallback: das Standard-`repr` vieler Objekte enthaelt die
    Speicheradresse, der Key haenge damit an der Objekt-Identitaet und der
    Cache traefe nie. Lieber hier laut scheitern.

    Mengen sind bewusst nicht dabei -- eine Menge hat keine Reihenfolge, und
    eine hier erfundene waere eine Konvention, die der Aufrufer nicht kennt.
    Wer eine Menge im Key braucht, uebergibt eine sortierte Liste.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(
        f"{type(value).__name__} taugt nicht als Cache-Key-Bestandteil. "
        "Erlaubt sind JSON-Typen, pydantic-Modelle, Path und datetime."
    )
