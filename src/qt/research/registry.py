"""Kandidaten-Registry des Research-Loops.

Jeder je erzeugte Strategie-Kandidat bekommt hier genau eine Zeile: woher er
kam (Modell, Effort), wann, was die Sandbox und die Kritik-Stufe von ihm
hielten und -- falls er so weit kam -- ueber wieviele Walk-Forward-Fenster und
wieviele Out-of-Sample-Bars er lief, mit welchem Sharpe und welcher Deflated
Sharpe Ratio.

Die Registry ist die **einzige Quelle des Versuchszaehlers** fuer die DSR
(ADR-005). Sie ist damit keine Bequemlichkeit neben dem Research-Loop, sondern
Bestandteil der statistischen Korrektur selbst.

Warum DuckDB und nicht ein JSON-Log wie in `qt.llm.cache`
---------------------------------------------------------
Der Unterschied ist nicht technischer Geschmack, sondern die Frage, was ein
Datenverlust kostet.

Der Antwort-Cache ist eine reine **Optimierung**. Ein halb geschriebener, von
Hand verfaelschter oder schlicht fehlender Eintrag wird dort folgenlos
verworfen: das Modell wird eben noch einmal gefragt. Das kostet Geld und sonst
nichts, und deshalb darf der Cache aus einzelnen Dateien bestehen, die jede
fuer sich kaputtgehen duerfen.

Bei der Registry ist genau das nicht so. Geht hier etwas verloren, faellt der
Versuchszaehler zurueck -- und **jede zukuenftige DSR-Berechnung wird zu
optimistisch**, weil sie gegen weniger Versuche deflationiert als tatsaechlich
unternommen wurden. Rauschen geht dann als Fund durch. Ein zu optimistischer
Overfitting-Schutz ist schlimmer als gar keiner, weil er Sicherheit
vortaeuscht, wo keine ist: ohne Schutz ist man misstrauisch, mit einem
kaputten nicht.

Darum eine Datenbank mit Transaktionen, festem Schema und einer einzigen Datei
statt eines Verzeichnisses, aus dem man beim Zusammenlesen stillschweigend
etwas auslassen kann. `duckdb` steht seit Beginn in `pyproject.toml` und wird
im ganzen Projekt ausschliesslich hier benutzt.

Nebenlaeufigkeit
----------------
Gemessen mit duckdb 1.5.5, nicht geraten: DuckDB laesst pro Datei genau
**einen schreibenden Prozess** zu.

* Ein zweiter schreibender Prozess scheitert sofort mit `duckdb.IOException`
  ("Could not set lock on file ... Conflicting lock is held"). Er wartet
  nicht, und er schreibt nichts -- es gibt also keinen halben Zustand und
  keine verlorene Zeile.
* Solange ein Schreiber die Datei haelt, scheitert auch eine *lesende*
  Verbindung aus einem anderen Prozess an derselben Sperre.
* Mehrere rein lesende Prozesse duerfen dagegen gleichzeitig offen sein.

Fuer den Research-Loop ist das kein Problem, sondern die gewuenschte Bauform.
Der Loop ist eine Sequenz -- erzeugen, Sandbox, Kritik, Screening -- und
schreibt aus einem Prozess. Wird er spaeter parallelisiert, darf die Arbeit
faechern, aber der Schreibzugriff auf die Registry muss durch genau einen
Prozess laufen. Der Ausfallmodus ist dabei der richtige herum: ein zweiter
Prozess kann gar nicht erst starten (laut und sofort), statt einen Versuch
unbemerkt zu verlieren (leise und fuer immer). `ResearchRegistry.open` uebersetzt
die Sperrmeldung deshalb in `RegistryLocked` mit dem wahrscheinlichen Grund.

Listen als JSON-Strings
-----------------------
`sandbox_reasons` und `literal_flags` sind Listen und werden als JSON-String in
einer VARCHAR-Spalte abgelegt, nicht in einer zweiten Tabelle. Eine
Nebentabelle waere die sauberere Normalform, aber sie kostet einen Join in
jeder Abfrage und eine zweite Stelle, an der beim Loeschen etwas
zurueckbleiben kann. Diese Listen werden nie einzeln abgefragt, nur im ganzen
gelesen -- fuer den Zweck ist der JSON-String die kleinere Konstruktion.
`get()` und `history()` geben sie wieder als Liste zurueck.

Ablageort: `data/research/registry.duckdb` (`/data/` ist gitignored).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

import duckdb
import pandas as pd

from qt.core.config import PROJECT_ROOT

DEFAULT_PATH = PROJECT_ROOT / "data" / "research" / "registry.duckdb"

TABLE = "candidates"

# Werte fuer `sandbox_status` und `screening_status`. Als Konstanten, damit ein
# Tippfehler im Aufrufer nicht stillschweigend einen neuen Status erfindet --
# bei `screening_status` waere das direkt ein falscher Versuchszaehler, denn
# gezaehlt wird ueber "IS NOT NULL" und nicht ueber eine Werteliste.
SANDBOX_OK = "ok"
SANDBOX_REJECTED = "rejected"
SCREENING_PASSED = "passed"
SCREENING_REJECTED = "rejected"

# Spalten, die eine Liste tragen (JSON-String, siehe Modul-Docstring).
_JSON_COLUMNS = ("sandbox_reasons", "literal_flags")

# Spalten mit Zeitstempel. Abgelegt als naives TIMESTAMP in UTC, beim Lesen
# wieder mit UTC versehen. Grund: DuckDBs TIMESTAMPTZ braucht in der
# Python-Anbindung `pytz`, und eine neue Abhaengigkeit ist das nicht wert.
# Ausserdem gaebe TIMESTAMPTZ die Werte in der Zeitzone der Maschine zurueck --
# dieselbe Registry saehe auf zwei Rechnern anders aus.
_TIMESTAMP_COLUMNS = ("created_at", "promoted_at")

# Der vollstaendige Spaltenplan, ohne `id`. Er ist die einzige Quelle sowohl
# fuer das Anlegen als auch fuer die Migration -- siehe `_migrate`.
#
# `id` fehlt hier mit Absicht: ein Primaerschluessel laesst sich nachtraeglich
# nicht per ALTER TABLE ergaenzen, die Spalte muss also im CREATE stehen und
# kann nicht ueber denselben Weg laufen wie alle anderen.
_COLUMNS: dict[str, str] = {
    "created_at": "TIMESTAMP",
    "generator_model": "VARCHAR",
    "generator_effort": "VARCHAR",
    "class_name": "VARCHAR",
    "code": "VARCHAR",
    "rationale": "VARCHAR",
    "sandbox_status": "VARCHAR",
    "sandbox_reasons": "VARCHAR",
    "literal_flags": "VARCHAR",
    "critic_model": "VARCHAR",
    "critic_effort": "VARCHAR",
    "critic_recommendation": "VARCHAR",
    "critic_overfitting_risk": "DOUBLE",
    "critic_magic_constants": "BOOLEAN",
    "critic_unrealistic_turnover": "BOOLEAN",
    "critic_excess_dof": "BOOLEAN",
    "critic_rationale_mismatch": "BOOLEAN",
    "critic_reasoning": "VARCHAR",
    "screening_status": "VARCHAR",
    "n_windows": "INTEGER",
    "oos_bars": "INTEGER",
    "sharpe": "DOUBLE",
    "dsr": "DOUBLE",
    "dsr_threshold": "DOUBLE",
    "trial_count_at_screening": "INTEGER",
    "promoted": "BOOLEAN DEFAULT FALSE",
    "promoted_at": "TIMESTAMP",
    "promoted_note": "VARCHAR",
}


class RegistryLocked(RuntimeError):
    """Die Registry-Datei wird bereits von einem anderen Prozess gehalten."""


class ResearchRegistry:
    """Herkunft, Zeitpunkt, OOS-Fenster und Statistik jedes Kandidaten.

    Benutzung als Kontextmanager, damit die Datei-Sperre nicht laenger gehalten
    wird als noetig (siehe Modul-Docstring zur Nebenlaeufigkeit)::

        with ResearchRegistry.open() as reg:
            cid = reg.record_generated("DonchianBreakout", code)
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection, path: Path) -> None:
        self._conn = conn
        self.path = path

    # -- Oeffnen und Schliessen ---------------------------------------------

    @classmethod
    def open(cls, path: Path | None = None) -> ResearchRegistry:
        """Registry oeffnen, Verzeichnis und Schema bei Bedarf anlegen.

        Das Schema wird bei *jedem* Oeffnen ueber `_migrate` nachgezogen. Eine
        Datei aus einer aelteren Fassung des Programms ist damit ohne
        Zwischenschritt weiterbenutzbar -- was die Voraussetzung dafuer ist,
        dass der Versuchszaehler ueber Schemawechsel hinweg ueberlebt.
        """
        target = Path(path) if path is not None else DEFAULT_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = duckdb.connect(str(target))
        except duckdb.IOException as exc:
            raise RegistryLocked(
                f"Registry {target} ist gesperrt. DuckDB laesst pro Datei genau "
                "einen schreibenden Prozess zu -- vermutlich laeuft bereits ein "
                "Research-Loop. Original: "
                f"{exc}"
            ) from exc
        conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (id VARCHAR PRIMARY KEY)")
        _migrate(conn)
        return cls(conn, target)

    def close(self) -> None:
        """Verbindung schliessen. Mehrfach aufrufbar."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> ResearchRegistry:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- Schreiben ----------------------------------------------------------

    def record_generated(
        self,
        class_name: str,
        code: str,
        *,
        rationale: str = "",
        generator_model: str = "",
        generator_effort: str = "",
        created_at: datetime | None = None,
    ) -> str:
        """Einen frisch erzeugten Kandidaten anlegen, gibt seine ID zurueck.

        Der volle Quelltext wird mitgeschrieben und nicht nur ein Verweis auf
        eine Datei. Ein Kandidat, dessen Code spaeter nicht mehr auffindbar
        ist, ist als Zeile wertlos: man weiss dann, dass ein Versuch
        stattfand, kann ihn aber weder nachvollziehen noch wiederholen.

        `created_at` ist ueberschreibbar, damit Tests und ein spaeteres
        Nachtragen alter Laeufe eine Reihenfolge herstellen koennen, ohne auf
        die Uhr angewiesen zu sein.
        """
        candidate_id = str(uuid.uuid4())
        self._conn.execute(
            f"""
            INSERT INTO {TABLE}
                (id, created_at, generator_model, generator_effort,
                 class_name, code, rationale, promoted)
            VALUES (?, ?, ?, ?, ?, ?, ?, FALSE)
            """,
            [
                candidate_id,
                _utc_naive(created_at),
                str(generator_model),
                str(generator_effort),
                str(class_name),
                str(code),
                str(rationale),
            ],
        )
        return candidate_id

    def record_sandbox_result(
        self,
        candidate_id: str,
        ok: bool,
        reasons: Iterable[str] = (),
        literal_flags: Iterable[str] = (),
    ) -> None:
        """Ergebnis der Sandbox festhalten.

        Auch eine Ablehnung wird geschrieben, nicht nur ein Erfolg. Sie zaehlt
        zwar nicht als Versuch (siehe `trial_count`), aber ohne sie liesse sich
        nicht erkennen, dass ein Generator reihenweise nicht lauffaehigen Code
        produziert -- und genau das ist die Diagnose, die man braucht, bevor
        man dem Modell laenger zusieht.
        """
        self._update(
            candidate_id,
            {
                "sandbox_status": SANDBOX_OK if ok else SANDBOX_REJECTED,
                "sandbox_reasons": _encode_list(reasons),
                "literal_flags": _encode_list(literal_flags),
            },
        )

    def record_critique(
        self,
        candidate_id: str,
        *,
        recommendation: str,
        overfitting_risk: float = 0.0,
        magic_constants: bool = False,
        unrealistic_turnover: bool = False,
        excess_dof: bool = False,
        rationale_mismatch: bool = False,
        reasoning: str = "",
        model: str = "",
        effort: str = "",
    ) -> None:
        """Urteil der Kritik-Stufe festhalten.

        Abgelegt werden nur primitive Werte, kein pydantic-Objekt: die Registry
        soll auch dann noch lesbar sein, wenn sich das Schema der Kritik
        laengst geaendert hat. Ein serialisiertes Modell waere ab dem naechsten
        Feldwechsel nur noch mit dem Code von damals zu entschluesseln.
        """
        self._update(
            candidate_id,
            {
                "critic_model": str(model),
                "critic_effort": str(effort),
                "critic_recommendation": str(recommendation),
                "critic_overfitting_risk": float(overfitting_risk),
                "critic_magic_constants": bool(magic_constants),
                "critic_unrealistic_turnover": bool(unrealistic_turnover),
                "critic_excess_dof": bool(excess_dof),
                "critic_rationale_mismatch": bool(rationale_mismatch),
                "critic_reasoning": str(reasoning),
            },
        )

    def record_screening(
        self,
        candidate_id: str,
        *,
        status: str,
        n_windows: int,
        oos_bars: int,
        sharpe: float,
        dsr: float | None = None,
        dsr_threshold: float | None = None,
        trial_count: int | None = None,
    ) -> None:
        """Ergebnis des Walk-Forward-Screenings festhalten.

        **Dieser Aufruf ist es, der einen Kandidaten zum Versuch macht** -- er
        setzt `screening_status`, und ueber genau dieses Feld zaehlt
        `trial_count()`. Deshalb wird er erst gerufen, wenn tatsaechlich gegen
        Out-of-Sample-Daten gerechnet wurde, nie vorher.

        `trial_count_at_screening` wird mitgeschrieben, weil eine DSR ohne die
        Versuchszahl, gegen die sie deflationiert wurde, nicht interpretierbar
        ist: dieselbe Zahl bedeutet beim zehnten und beim tausendsten Versuch
        voellig Verschiedenes. Ohne diese Spalte waere ein alter Eintrag im
        Nachhinein nicht mehr nachvollziehbar, weil der Zaehler inzwischen
        weitergelaufen ist.

        Gezaehlt wird **einschliesslich** dieses Kandidaten: er selbst ist
        einer der Blicke auf die Daten, gegen die korrigiert werden muss.
        `trial_count` ueberschreibt das, falls der Aufrufer nachweislich gegen
        eine andere Zahl gerechnet hat -- gespeichert gehoert die Zahl, die in
        der Formel stand, nicht die, die schoener aussieht.
        """
        # Beide Schreibvorgaenge in einer Transaktion: eine Zeile mit
        # `screening_status`, aber ohne `trial_count_at_screening` waere ein
        # Versuch, dessen DSR sich nicht mehr einordnen laesst.
        self._conn.execute("BEGIN TRANSACTION")
        try:
            self._update(
                candidate_id,
                {
                    "screening_status": str(status),
                    "n_windows": int(n_windows),
                    "oos_bars": int(oos_bars),
                    "sharpe": _opt_float(sharpe),
                    "dsr": _opt_float(dsr),
                    "dsr_threshold": _opt_float(dsr_threshold),
                },
            )
            count = self.trial_count() if trial_count is None else int(trial_count)
            self._update(candidate_id, {"trial_count_at_screening": count})
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def mark_promoted(self, candidate_id: str, note: str = "") -> None:
        """Kandidaten als in die Bibliothek uebernommen markieren.

        Bewusst ein eigenes Feld und kein weiterer `screening_status`-Wert:
        Promotion ist eine Entscheidung ueber einen Kandidaten, das Screening
        ist eine Messung an ihm. Waeren beide dasselbe Feld, wuerde eine
        Promotion die Messung ueberschreiben -- und der Versuchszaehler haenge
        an einem Feld, das nach der Promotion etwas anderes bedeutet.
        """
        self._update(
            candidate_id,
            {
                "promoted": True,
                "promoted_at": _utc_naive(None),
                "promoted_note": str(note),
            },
        )

    # -- Lesen --------------------------------------------------------------

    def trial_count(self) -> int:
        """Zahl der Versuche fuer die Deflated Sharpe Ratio (ADR-005).

        **Abgeleitet, nicht gepflegt.** Es gibt kein mutables Zaehlerfeld, das
        nebenher hochgezaehlt wird, sondern nur diese Abfrage. Zwei Zahlen, die
        zueinander passen muessen und an verschiedenen Orten stehen, passen
        irgendwann nicht mehr zueinander -- das ist die Lehre aus ADR-020, wo
        eine fest verdrahtete Historienlaenge und der Bedarf einer Baseline
        auseinanderliefen und eine Baseline monatelang still auf `nan` rechnete.
        Ein abgeleiteter Zaehler kann nicht driften: er ist der Bestand.

        **Nur abgeschlossenes Screening zaehlt.** Ein Kandidat, den die Sandbox
        oder die Kritik-Stufe verworfen hat, zaehlt *nicht* mit. ADR-005 spricht
        von "allen je getesteten" Kandidaten, und getestet heisst hier: durch
        Walk-Forward-OOS gelaufen, ein Sharpe wurde auf echten
        Out-of-Sample-Daten berechnet. Wer nie gegen echte Daten lief, hat
        keinen zusaetzlichen Blick auf die Daten gekauft und darf die Korrektur
        nicht mit einem Versuch belasten -- sonst bestraft man sich fuer
        Kandidaten, die nie eine Chance hatten, gut auszusehen.

        Gescreent und *durchgefallen* zaehlt dagegen sehr wohl: der Blick auf
        die Daten hat stattgefunden, unabhaengig davon, wie er ausging.
        """
        row = self._conn.execute(
            f"SELECT count(*) FROM {TABLE} WHERE screening_status IS NOT NULL"
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def get(self, candidate_id: str) -> dict[str, Any] | None:
        """Eine Zeile als Dict, oder `None` wenn es die ID nicht gibt.

        `None` statt einer Ausnahme, weil "kenne ich nicht" beim Lesen eine
        gueltige Antwort ist. Beim *Schreiben* ist es das nicht -- dort
        bedeutet eine unbekannte ID ein verlorenes Ergebnis, und die
        `record_*`-Methoden werfen deshalb `KeyError`.
        """
        cur = self._conn.execute(f"SELECT * FROM {TABLE} WHERE id = ?", [candidate_id])
        names = [d[0] for d in cur.description]
        row = cur.fetchone()
        if row is None:
            return None
        return _decode_row(dict(zip(names, row, strict=True)))

    def history(self, status: str | None = None) -> pd.DataFrame:
        """Alle Kandidaten als DataFrame, aelteste zuerst.

        `status` filtert auf `screening_status` -- das ist der Status, der
        ueber die Zugehoerigkeit zum Versuchszaehler entscheidet, und damit der
        einzige, nach dem man beim Nachrechnen einer DSR filtern will.
        `history(SCREENING_PASSED)` und `history(SCREENING_REJECTED)` ergeben
        zusammen genau `trial_count()` Zeilen.
        """
        sql = f"SELECT * FROM {TABLE}"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE screening_status = ?"
            params.append(status)
        # `id` als zweites Sortierkriterium: zwei Kandidaten koennen denselben
        # Zeitstempel tragen, und eine Reihenfolge, die sich von Lauf zu Lauf
        # aendert, macht jeden Vergleich zweier Ausgaben wertlos.
        sql += " ORDER BY created_at, id"
        df = self._conn.execute(sql, params).df()
        return _decode_frame(df)

    # -- Intern -------------------------------------------------------------

    def _update(self, candidate_id: str, values: Mapping[str, Any]) -> None:
        """Felder einer bestehenden Zeile setzen, sonst `KeyError`.

        Die Pruefung auf die betroffene Zeilenzahl ist der Kern: ein UPDATE auf
        eine unbekannte ID ist in SQL kein Fehler, sondern trifft null Zeilen.
        Ohne diese Pruefung verschwaende ein vertippter Aufrufer ein
        Screening-Ergebnis lautlos -- und der Kandidat bliebe fuer immer
        ungescreent, also ungezaehlt.
        """
        assignments = ", ".join(f'"{name}" = ?' for name in values)
        cur = self._conn.execute(
            f"UPDATE {TABLE} SET {assignments} WHERE id = ?",
            [*values.values(), candidate_id],
        )
        changed = cur.fetchone()
        if changed is None or int(changed[0]) == 0:
            raise KeyError(f"Kein Kandidat mit ID {candidate_id!r} in {self.path}")


def _migrate(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Fehlende Spalten ergaenzen. Gibt die Namen der ergaenzten zurueck.

    **Ausschliesslich additiv.** Es gibt hier kein DROP und kein CREATE unter
    demselben Namen, und es gibt bewusst keine `SCHEMA_VERSION` wie in
    `qt.llm.cache`. Dort entwertet ein Versionswechsel alle alten Eintraege,
    und das ist richtig so: ein Cache-Eintrag im alten Format ist Muell in der
    Form einer Antwort. Hier waere dieselbe Bauform ein Fehler -- ein
    entwerteter Bestand setzte den Versuchszaehler auf null zurueck und machte
    jede kuenftige DSR zu optimistisch (siehe Modul-Docstring).

    Die Umsetzung fragt die tatsaechlich vorhandenen Spalten ab und ergaenzt
    die Differenz zu `_COLUMNS`. Damit ist der Mechanismus nicht auf eine
    gepflegte Versionsnummer angewiesen, die jemand hochzuzaehlen vergessen
    kann: er vergleicht den Ist-Zustand mit dem Soll und braucht keine
    Buchfuehrung darueber, was frueher einmal war.

    Dieser Weg ist auch der einzige, auf dem Spalten ueberhaupt entstehen -- die
    Tabelle wird nur mit `id` angelegt. Die Migration laeuft dadurch bei jedem
    frischen Oeffnen mit und kann nicht zu dem selten benutzten Zweig
    verkommen, der beim ersten echten Schemawechsel dann doch nicht
    funktioniert.
    """
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM duckdb_columns() WHERE table_name = ?", [TABLE]
        ).fetchall()
    }
    added = []
    for name, sql_type in _COLUMNS.items():
        if name in existing:
            continue
        conn.execute(f'ALTER TABLE {TABLE} ADD COLUMN "{name}" {sql_type}')
        added.append(name)
    return added


def _utc_naive(value: datetime | None) -> datetime:
    """Zeitstempel auf naives UTC bringen (siehe `_TIMESTAMP_COLUMNS`).

    Ein naiver Wert wird als UTC gelesen und nicht als Ortszeit: alles in
    diesem Projekt rechnet in UTC, und eine stillschweigende Umrechnung nach
    Ortszeit waere eine Verschiebung, die erst im Sommer auffiele.
    """
    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _encode_list(values: Iterable[str]) -> str:
    """Liste als JSON-String. `sort_keys` ist hier bewusst nicht noetig --
    die Reihenfolge der Gruende ist Information, keine Menge."""
    return json.dumps([str(v) for v in values], ensure_ascii=False)


def _decode_list(raw: Any) -> list[str]:
    """JSON-String zurueck in eine Liste. Unlesbares gibt eine leere Liste.

    Anders als beim Cache waere ein Absturz hier teuer: eine Registry, die sich
    wegen eines verunglueckten Grundtextes nicht mehr oeffnen laesst, nimmt den
    Versuchszaehler mit. Die Liste ist Beiwerk, der Zaehler ist es nicht.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


def _opt_float(value: float | None) -> float | None:
    return None if value is None else float(value)


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    """Rohzeile in die Form bringen, in der Aufrufer sie erwarten."""
    for name in _JSON_COLUMNS:
        if name in row:
            row[name] = _decode_list(row[name])
    for name in _TIMESTAMP_COLUMNS:
        value = row.get(name)
        if isinstance(value, datetime) and value.tzinfo is None:
            row[name] = value.replace(tzinfo=timezone.utc)
    return row


def _decode_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Wie `_decode_row`, aber spaltenweise -- damit `get()` und `history()`
    dieselben Werte liefern und nicht zwei Konventionen nebeneinander stehen."""
    for name in _JSON_COLUMNS:
        if name in df.columns:
            df[name] = [_decode_list(v) for v in df[name]]
    for name in _TIMESTAMP_COLUMNS:
        if name in df.columns and df[name].dt.tz is None:
            df[name] = df[name].dt.tz_localize("UTC")
    return df
