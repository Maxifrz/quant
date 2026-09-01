"""Kandidaten-Registry des Research-Loops.

Die Registry traegt eine Zahl, an der die Deflated Sharpe Ratio haengt
(ADR-005). Zwei Eigenschaften entscheiden darueber, ob sie ihren Zweck
erfuellt, und beide sind so beschaffen, dass ein Fehler darin nichts
fehlschlagen laesst -- er macht nur alle kuenftigen DSR-Werte zu optimistisch:

1. Der Versuchszaehler liegt auf der Platte und nicht im Prozess.
2. Gezaehlt wird, wer gegen echte Out-of-Sample-Daten lief -- sonst niemand.

Deshalb stehen beide hier als Test und nicht als Absicht.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from qt.research.registry import (
    _COLUMNS,
    DEFAULT_PATH,
    SANDBOX_OK,
    SANDBOX_REJECTED,
    SCREENING_PASSED,
    SCREENING_REJECTED,
    TABLE,
    ResearchRegistry,
    _migrate,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)

CODE = "class Donchian(Strategy):\n    pass\n"


def _registry(tmp_path: Path) -> ResearchRegistry:
    """Immer unter `tmp_path`. Siehe `test_default_path_is_never_touched`."""
    return ResearchRegistry.open(tmp_path / "registry.duckdb")


def _screened(reg: ResearchRegistry, name: str, *, status: str, **kwargs) -> str:
    """Kandidaten anlegen und vollstaendig durch das Screening bringen."""
    cid = reg.record_generated(name, CODE, **kwargs)
    reg.record_sandbox_result(cid, True)
    reg.record_critique(cid, recommendation="accept")
    reg.record_screening(
        cid, status=status, n_windows=6, oos_bars=1440, sharpe=1.2, dsr=0.55
    )
    return cid


# -- Der Versuchszaehler ----------------------------------------------------


def test_trial_count_survives_close_and_reopen(tmp_path):
    """Der Zaehler liegt auf der Platte, nicht im Objekt.

    Der Research-Loop laeuft ueber Wochen in vielen einzelnen Aufrufen. Waere
    der Zaehler an die Lebensdauer eines Prozesses gebunden, faenge die
    Korrektur bei jedem Start wieder bei null an -- und zwar lautlos, weil
    nichts fehlschlaegt. Ohne diesen Test ist das ganze Modul seinen Zweck
    nicht wert.
    """
    path = tmp_path / "registry.duckdb"

    first = ResearchRegistry.open(path)
    for i in range(3):
        _screened(first, f"Cand{i}", status=SCREENING_PASSED)
    assert first.trial_count() == 3
    first.close()

    second = ResearchRegistry.open(path)
    assert second.trial_count() == 3, "Zaehler nach Neu-Oeffnen verloren"
    _screened(second, "Cand3", status=SCREENING_REJECTED)
    second.close()

    third = ResearchRegistry.open(path)
    assert third.trial_count() == 4
    third.close()


def test_sandbox_and_critic_rejections_do_not_count_as_trials(tmp_path):
    """Regression: nur abgeschlossenes Screening ist ein Versuch.

    ADR-005 spricht von "allen je getesteten" Kandidaten. Getestet heisst:
    durch Walk-Forward-OOS gelaufen. Wer in der Sandbox oder an der Kritik
    scheiterte, hat nie gegen echte Daten geblickt und darf die Korrektur
    nicht belasten -- sonst deflationiert man den Sharpe der guten Kandidaten
    mit Code, der nicht einmal importierbar war.

    Das ist die am leichtesten zu uebersehende Eigenschaft der Registry: sie
    faellt nirgends auf, sie verschiebt nur eine Schwelle.
    """
    with _registry(tmp_path) as reg:
        # In der Sandbox gescheitert.
        broken = reg.record_generated("Broken", "def (")
        reg.record_sandbox_result(broken, False, ["SyntaxError: invalid syntax"])
        assert reg.get(broken)["sandbox_status"] == SANDBOX_REJECTED
        assert reg.get(broken)["sandbox_reasons"] == ["SyntaxError: invalid syntax"]
        assert reg.trial_count() == 0

        # Sandbox ok, aber von der Kritik verworfen.
        overfit = reg.record_generated("Overfit", CODE)
        reg.record_sandbox_result(overfit, True, [], ["0.618", "1.618"])
        reg.record_critique(
            overfit,
            recommendation="reject",
            overfitting_risk=0.9,
            magic_constants=True,
        )
        assert reg.trial_count() == 0

        # Nur wer wirklich gescreent wurde, zaehlt.
        _screened(reg, "Real", status=SCREENING_PASSED)
        assert reg.trial_count() == 1

        # Und zwar auch dann, wenn er durchgefallen ist: der Blick auf die
        # Daten hat stattgefunden, unabhaengig vom Ausgang.
        _screened(reg, "Weak", status=SCREENING_REJECTED)
        assert reg.trial_count() == 2


def test_trial_count_at_screening_pins_the_number_the_dsr_was_deflated_against(
    tmp_path,
):
    """Eine DSR ohne ihre Versuchszahl ist im Nachhinein nicht lesbar."""
    with _registry(tmp_path) as reg:
        ids = [
            _screened(reg, f"Cand{i}", status=SCREENING_PASSED, created_at=START)
            for i in range(3)
        ]
        recorded = [reg.get(cid)["trial_count_at_screening"] for cid in ids]

    assert recorded == [1, 2, 3]


def test_trial_count_is_derived_and_cannot_drift(tmp_path):
    """Es gibt kein zweites, gepflegtes Zaehlerfeld.

    Ein nebenher hochgezaehltes Feld und der Bestand sind zwei Zahlen an zwei
    Orten, die zueinander passen muessen -- der Fehler aus ADR-020. Hier wird
    eine Zeile direkt in der Datenbank ergaenzt, also unter Umgehung jeder
    `record_*`-Methode: ein abgeleiteter Zaehler sieht sie trotzdem.
    """
    path = tmp_path / "registry.duckdb"
    with ResearchRegistry.open(path) as reg:
        _screened(reg, "Cand", status=SCREENING_PASSED)
        assert reg.trial_count() == 1

    conn = duckdb.connect(str(path))
    conn.execute(
        f"INSERT INTO {TABLE} (id, created_at, screening_status) VALUES (?, ?, ?)",
        ["von-hand", START.replace(tzinfo=None), SCREENING_PASSED],
    )
    conn.close()

    with ResearchRegistry.open(path) as reg:
        assert reg.trial_count() == 2


# -- Lebenszyklus -----------------------------------------------------------


def test_full_lifecycle_is_visible_at_every_stage(tmp_path):
    """Erzeugt -> Sandbox -> Kritik -> gescreent -> promoted."""
    with _registry(tmp_path) as reg:
        cid = reg.record_generated(
            "DonchianBreakout",
            CODE,
            rationale="Ausbruch aus der 20-Tage-Spanne.",
            generator_model="claude-opus-5",
            generator_effort="high",
            created_at=START,
        )

        row = reg.get(cid)
        assert row["class_name"] == "DonchianBreakout"
        assert row["code"] == CODE, "der volle Quelltext gehoert in die Zeile"
        assert row["generator_model"] == "claude-opus-5"
        assert row["generator_effort"] == "high"
        assert row["created_at"] == START
        assert row["sandbox_status"] is None
        assert row["promoted"] is False
        assert reg.trial_count() == 0

        reg.record_sandbox_result(cid, True, [], ["0.02", "14"])
        row = reg.get(cid)
        assert row["sandbox_status"] == SANDBOX_OK
        assert row["sandbox_reasons"] == []
        assert row["literal_flags"] == ["0.02", "14"], "Listen als Liste zurueck"

        reg.record_critique(
            cid,
            recommendation="accept",
            overfitting_risk=0.25,
            magic_constants=True,
            unrealistic_turnover=False,
            excess_dof=False,
            rationale_mismatch=False,
            reasoning="Zwei Konstanten, sonst unauffaellig.",
            model="claude-opus-5",
            effort="medium",
        )
        row = reg.get(cid)
        assert row["critic_recommendation"] == "accept"
        assert row["critic_overfitting_risk"] == pytest.approx(0.25)
        assert row["critic_magic_constants"] is True
        assert row["critic_unrealistic_turnover"] is False
        assert row["critic_model"] == "claude-opus-5"
        assert row["critic_effort"] == "medium"

        reg.record_screening(
            cid,
            status=SCREENING_PASSED,
            n_windows=8,
            oos_bars=2190,
            sharpe=1.35,
            dsr=0.62,
            dsr_threshold=0.5,
        )
        row = reg.get(cid)
        assert row["screening_status"] == SCREENING_PASSED
        assert row["n_windows"] == 8
        assert row["oos_bars"] == 2190
        assert row["sharpe"] == pytest.approx(1.35)
        assert row["dsr"] == pytest.approx(0.62)
        assert row["dsr_threshold"] == pytest.approx(0.5)
        assert row["trial_count_at_screening"] == 1
        assert reg.trial_count() == 1

        reg.mark_promoted(cid, "Erster Kandidat der Bibliothek.")
        row = reg.get(cid)
        assert row["promoted"] is True
        assert row["promoted_note"] == "Erster Kandidat der Bibliothek."
        assert row["promoted_at"] is not None
        assert row["promoted_at"].tzinfo is not None, "Zeitstempel ohne Zone"

        hist = reg.history()
        assert len(hist) == 1
        entry = hist.iloc[0]
        assert entry["class_name"] == "DonchianBreakout"
        assert entry["sandbox_status"] == SANDBOX_OK
        assert entry["critic_recommendation"] == "accept"
        assert entry["screening_status"] == SCREENING_PASSED
        assert entry["literal_flags"] == ["0.02", "14"]
        assert bool(entry["promoted"]) is True


def test_history_filters_by_screening_status(tmp_path):
    with _registry(tmp_path) as reg:
        for i in range(2):
            _screened(
                reg,
                f"Good{i}",
                status=SCREENING_PASSED,
                created_at=START + timedelta(hours=i),
            )
        _screened(
            reg,
            "Weak",
            status=SCREENING_REJECTED,
            created_at=START + timedelta(hours=2),
        )
        rejected = reg.record_generated("Broken", "def (", created_at=START)
        reg.record_sandbox_result(rejected, False, ["SyntaxError"])

        assert len(reg.history()) == 4
        assert len(reg.history(SCREENING_PASSED)) == 2
        assert len(reg.history(SCREENING_REJECTED)) == 1
        assert list(reg.history(SCREENING_PASSED)["class_name"]) == ["Good0", "Good1"]

        # Der Filter zerlegt genau die Versuche, nichts sonst.
        assert (
            len(reg.history(SCREENING_PASSED)) + len(reg.history(SCREENING_REJECTED))
            == reg.trial_count()
        )


def test_history_is_ordered_oldest_first(tmp_path):
    with _registry(tmp_path) as reg:
        for i in reversed(range(4)):
            reg.record_generated(f"Cand{i}", CODE, created_at=START + timedelta(days=i))
        assert list(reg.history()["class_name"]) == [f"Cand{i}" for i in range(4)]


# -- Unbekannte IDs ---------------------------------------------------------


def test_get_on_unknown_id_returns_none(tmp_path):
    """Beim Lesen ist "kenne ich nicht" eine Antwort, keine Ausnahme."""
    with _registry(tmp_path) as reg:
        assert reg.get("gibt-es-nicht") is None
        reg.record_generated("Cand", CODE)
        assert reg.get("gibt-es-nicht") is None


def test_writing_to_an_unknown_id_raises(tmp_path):
    """Beim Schreiben ist sie es sehr wohl.

    Ein UPDATE auf eine unbekannte ID trifft in SQL null Zeilen und ist kein
    Fehler. Bliebe das unbemerkt, verschwaende ein vertippter Aufrufer ein
    Screening-Ergebnis lautlos -- der Kandidat waere fuer immer ungezaehlt.
    """
    with _registry(tmp_path) as reg:
        with pytest.raises(KeyError):
            reg.record_sandbox_result("gibt-es-nicht", True)
        with pytest.raises(KeyError):
            reg.record_critique("gibt-es-nicht", recommendation="accept")
        with pytest.raises(KeyError):
            reg.record_screening(
                "gibt-es-nicht",
                status=SCREENING_PASSED,
                n_windows=1,
                oos_bars=1,
                sharpe=0.0,
            )
        with pytest.raises(KeyError):
            reg.mark_promoted("gibt-es-nicht")
        assert reg.trial_count() == 0


# -- Migration --------------------------------------------------------------


def test_migration_adds_missing_columns_and_keeps_existing_rows(tmp_path):
    """Additiv, nie neu angelegt.

    Anders als bei `LLMCache.SCHEMA_VERSION`, wo ein Versionswechsel alte
    Eintraege folgenlos entwertet, waere ein Neuanlegen hier ein stiller
    Datenverlust: der Versuchszaehler faellt auf null, und jede kuenftige DSR
    wird zu optimistisch. Deshalb prueft der Test nicht nur, dass die Spalte
    danach da ist, sondern dass die Zeilen es auch sind.
    """
    path = tmp_path / "registry.duckdb"
    missing = "dsr_threshold"

    # Eine Registry im "alten" Schema, von Hand gebaut: alles ausser einer
    # Spalte, die erst spaeter dazukam.
    conn = duckdb.connect(str(path))
    conn.execute(f"CREATE TABLE {TABLE} (id VARCHAR PRIMARY KEY)")
    for name, sql_type in _COLUMNS.items():
        if name != missing:
            conn.execute(f'ALTER TABLE {TABLE} ADD COLUMN "{name}" {sql_type}')
    for i in range(3):
        conn.execute(
            f"""INSERT INTO {TABLE} (id, created_at, class_name, sharpe,
                                     screening_status)
                VALUES (?, ?, ?, ?, ?)""",
            [f"alt-{i}", START.replace(tzinfo=None), f"Alt{i}", 1.0 + i, "passed"],
        )
    conn.close()

    with ResearchRegistry.open(path) as reg:
        row = reg.get("alt-1")
        assert row is not None, "bestehende Zeile nach Migration verschwunden"
        assert missing in row
        assert row[missing] is None, "neue Spalte ist leer, nicht erfunden"
        assert row["class_name"] == "Alt1"
        assert row["sharpe"] == pytest.approx(2.0)

        assert len(reg.history()) == 3, "Migration hat Zeilen verloren"
        assert reg.trial_count() == 3, "Versuchszaehler durch Migration verfaelscht"

        # Ab jetzt normal weiterbenutzbar.
        _screened(reg, "Neu", status=SCREENING_PASSED)
        assert reg.trial_count() == 4


def test_migration_is_idempotent(tmp_path):
    """Ein zweiter Lauf darf nichts mehr tun -- und nichts kaputtmachen."""
    path = tmp_path / "registry.duckdb"
    with ResearchRegistry.open(path) as reg:
        _screened(reg, "Cand", status=SCREENING_PASSED)
        assert _migrate(reg._conn) == []
        assert reg.trial_count() == 1

    with ResearchRegistry.open(path) as reg:
        assert reg.trial_count() == 1
        assert set(reg.history().columns) == {"id", *_COLUMNS}


def test_fresh_registry_gets_every_planned_column(tmp_path):
    """Die Tabelle wird nur mit `id` angelegt, alles andere kommt ueber die
    Migration -- der Pfad ist damit bei jedem Oeffnen in Benutzung und kann
    nicht zu einem selten benutzten Zweig verkommen."""
    with _registry(tmp_path) as reg:
        columns = set(reg.history().columns)
    assert columns == {"id", *_COLUMNS}
    assert "trial_count_at_screening" in columns


# -- Dieselbe Datei, nacheinander -------------------------------------------


def test_two_registries_on_the_same_file_see_the_same_data(tmp_path):
    """Nacheinander, nicht gleichzeitig -- DuckDB erlaubt nur einen Schreiber."""
    path = tmp_path / "registry.duckdb"

    writer = ResearchRegistry.open(path)
    cid = _screened(writer, "Donchian", status=SCREENING_PASSED, created_at=START)
    writer.mark_promoted(cid, "uebernommen")
    writer.close()

    reader = ResearchRegistry.open(path)
    row = reader.get(cid)
    assert row is not None
    assert row["class_name"] == "Donchian"
    assert row["screening_status"] == SCREENING_PASSED
    assert row["promoted"] is True
    assert row["promoted_note"] == "uebernommen"
    assert reader.trial_count() == 1
    assert len(reader.history()) == 1
    reader.close()


# -- Zweiter Prozess --------------------------------------------------------

_CHILD = """
import sys
from pathlib import Path

from qt.research.registry import RegistryLocked, ResearchRegistry

try:
    with ResearchRegistry.open(Path(sys.argv[1])) as reg:
        print("trials", reg.trial_count())
except RegistryLocked:
    print("locked")
"""


def _child(path: Path) -> str:
    src = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(src)}
    done = subprocess.run(
        [sys.executable, "-c", _CHILD, str(path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return done.stdout.strip()


def test_a_second_writing_process_is_refused_rather_than_losing_writes(tmp_path):
    """DuckDB laesst pro Datei genau einen Schreiber. Gemessen, nicht geraten.

    Fuer die Registry ist das der richtige Ausfallmodus: der zweite Prozess
    kommt gar nicht erst hoch (laut und sofort), statt einen Versuch unbemerkt
    zu verlieren (leise und fuer immer). Dass er `RegistryLocked` und nicht die
    rohe `duckdb.IOException` sieht, ist der Unterschied zwischen "sag dem
    Nutzer, was los ist" und "lass ihn eine Sperrmeldung googeln".

    Der zweite Teil des Tests ist zugleich der harte Beleg dafuer, dass der
    Versuchszaehler auf der Platte liegt: ihn liest ein wirklich getrennter
    Interpreter.
    """
    path = tmp_path / "registry.duckdb"

    holder = ResearchRegistry.open(path)
    _screened(holder, "Cand", status=SCREENING_PASSED)

    assert _child(path) == "locked", "zweiter Schreiber haette scheitern muessen"

    holder.close()
    assert _child(path) == "trials 1"


# -- Der echte Ablageort ----------------------------------------------------


def test_default_path_is_never_touched(tmp_path):
    """Kein Test darf in die echte Registry schreiben.

    Eine Testzeile dort waere ein Versuch, den niemand unternommen hat, und
    verfaelschte den Versuchszaehler des Nutzers dauerhaft -- in die
    pessimistische Richtung, also unauffaellig.
    """
    assert DEFAULT_PATH.parts[-3:] == ("data", "research", "registry.duckdb")
    with _registry(tmp_path) as reg:
        reg.record_generated("Cand", CODE)
        assert reg.path != DEFAULT_PATH
        assert tmp_path in reg.path.parents
