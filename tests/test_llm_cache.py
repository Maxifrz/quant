"""Disk-Cache fuer LLM-Antworten.

Zwei Eigenschaften entscheiden darueber, ob der Cache seinen Zweck erfuellt:
der Key muss ueber Prozessgrenzen hinweg derselbe sein (sonst ist ein
LLM-Backtest nicht reproduzierbar und zahlt jedes Mal), und ein defekter
Eintrag darf einen Lauf nicht abbrechen. Beides steht hier als Test und nicht
als Absicht.
"""

from __future__ import annotations

import json

import pytest

from qt.llm.cache import CACHE_DIR, DEFAULT_MODEL, CacheStats, LLMCache
from qt.llm.schemas import AllocationProposal, StrategyAllocation

PROMPT = "STRAT_A: vol=0.31 mom=-0.12"


def _proposal() -> AllocationProposal:
    return AllocationProposal(
        allocations=[
            StrategyAllocation(strategy_id="STRAT_A", weight=0.4, reason="Trend intakt"),
            StrategyAllocation(strategy_id="STRAT_B", weight=-0.25),
        ],
        regime="Seitwaerts mit erhoehter Vola",
        confidence=0.7,
        reasoning="Kurz begruendet.",
    )


def _cache(tmp_path, **kwargs) -> LLMCache:
    return LLMCache(path=tmp_path, **kwargs)


# -- Runde durch ------------------------------------------------------------


def test_round_trip_returns_an_equivalent_proposal(tmp_path):
    cache = _cache(tmp_path)
    key = cache.key(PROMPT)
    original = _proposal()

    assert cache.get(key) is None, "leerer Cache darf nichts liefern"
    cache.put(key, original)

    restored = cache.get(key)
    assert restored is not None
    assert restored == original
    assert restored.as_allocation(["STRAT_A", "STRAT_B"]) == original.as_allocation(
        ["STRAT_A", "STRAT_B"]
    )


def test_same_value_written_twice_is_byte_identical(tmp_path):
    """Reproduzierbar heisst auch: die Ablage selbst enthaelt keinen Zufall.

    Ein Zeitstempel im Eintrag wuerde genuegen, damit zwei identische Laeufe
    unterschiedliche Dateien hinterlassen -- und damit waere nicht mehr
    pruefbar, ob sie wirklich dasselbe getan haben.
    """
    cache = _cache(tmp_path)
    key = cache.key(PROMPT)

    cache.put(key, _proposal())
    first = cache.path_for(key).read_bytes()
    cache.put(key, _proposal())

    assert cache.path_for(key).read_bytes() == first


# -- Key --------------------------------------------------------------------


def test_key_is_stable_across_processes(tmp_path):
    """Hart kodiert, weil genau das die Eigenschaft ist, die zaehlt.

    Faellt dieser Test, hat sich die Key-Bildung geaendert und jeder
    bestehende Cache ist entwertet. Das darf auffallen, nicht durchrutschen.
    """
    cache = _cache(tmp_path, model="test-model-v1", schema_version=1)

    assert (
        cache.key(PROMPT, horizon_bars=42, temperature=0.0)
        == "4efe5d1a6f974cee70c00c9cea7380037dc3fde32529ca5f54422bfef99fc714"
    )


def test_key_changes_with_model(tmp_path):
    """Dieselbe Frage an zwei Modelle sind zwei Antworten."""
    a = _cache(tmp_path, model="model-a")
    b = _cache(tmp_path, model="model-b")

    assert a.key(PROMPT) != b.key(PROMPT)


def test_key_changes_with_schema_version(tmp_path):
    """Ein Eintrag aus einer aelteren Schema-Fassung darf nicht weiterleben."""
    old = _cache(tmp_path, schema_version=1)
    new = _cache(tmp_path, schema_version=2)

    assert old.key(PROMPT) != new.key(PROMPT)

    old.put(old.key(PROMPT), _proposal())
    assert new.get(new.key(PROMPT)) is None


def test_key_changes_with_prompt_and_extras(tmp_path):
    cache = _cache(tmp_path)

    assert cache.key(PROMPT) != cache.key(PROMPT + " ")
    assert cache.key(PROMPT) != cache.key(PROMPT, temperature=0.2)
    assert cache.key(PROMPT, temperature=0.0) != cache.key(PROMPT, temperature=0.2)


def test_key_ignores_insertion_order(tmp_path):
    """Gehasht wird der Inhalt, nicht die Reihenfolge des Aufbaus.

    Ohne diese Eigenschaft haette der Cache eine Trefferquote nahe null,
    ohne dass irgendwo etwas fehlschlaegt -- der teuerste denkbare Fehler.
    """
    cache = _cache(tmp_path)

    first = {"vol": 0.3, "mom": -0.1, "carry": 0.02}
    second = {"carry": 0.02, "mom": -0.1, "vol": 0.3}
    assert first == second and list(first) != list(second)

    assert cache.key(PROMPT, features=first) == cache.key(PROMPT, features=second)
    assert cache.key(PROMPT, features=first, seed=7) == cache.key(
        PROMPT, seed=7, features=second
    )


def test_key_ignores_nested_insertion_order(tmp_path):
    cache = _cache(tmp_path)

    deep_a = {"outer": {"b": {"y": 1, "x": 2}, "a": [1, {"q": 0, "p": 1}]}}
    deep_b = {"outer": {"a": [1, {"p": 1, "q": 0}], "b": {"x": 2, "y": 1}}}

    assert cache.key(PROMPT, ctx=deep_a) == cache.key(PROMPT, ctx=deep_b)


def test_key_refuses_values_without_stable_content(tmp_path):
    """Kein repr()-Fallback: das haenge den Key an die Speicheradresse."""
    cache = _cache(tmp_path)

    with pytest.raises(TypeError, match="Cache-Key"):
        cache.key(PROMPT, thing=object())


# -- Layout -----------------------------------------------------------------


def test_entries_are_sharded_by_hash_prefix(tmp_path):
    """Ein Verzeichnis mit tausenden Dateien wird langsam; deshalb Fanout."""
    cache = _cache(tmp_path)
    key = cache.key(PROMPT)
    cache.put(key, _proposal())

    path = cache.path_for(key)
    assert path.parent.name == key[:2]
    assert path.name == f"{key[2:]}.json"
    assert path.parent.parent == tmp_path


def test_default_path_is_the_ignored_project_directory():
    """Default gehoert nach .llm_cache/ -- das steht in .gitignore."""
    assert LLMCache().path == CACHE_DIR
    assert CACHE_DIR.name == ".llm_cache"
    assert LLMCache().model == DEFAULT_MODEL


# -- Defekte Eintraege ------------------------------------------------------


def _write_raw(cache: LLMCache, key: str, payload: bytes) -> None:
    path = cache.path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _wrong_fields(key: str) -> bytes:
    entry = {
        "key": key,
        "model": DEFAULT_MODEL,
        "schema_version": 1,
        # weight ausserhalb [-1, 1] -- genau das, was die zweite Pruefung in
        # schemas.py abfangen soll.
        "value": {"allocations": [{"strategy_id": "STRAT_A", "weight": 42.0}]},
    }
    return json.dumps(entry).encode("utf-8")


@pytest.mark.parametrize(
    "name,payload",
    [
        ("muell", b"\x00\x01\x02nicht mal text"),
        ("leer", b""),
        ("halbes json", b'{"key": "abc", "value": {"alloc'),
        ("json ohne struktur", b'["voellig", "anderes"]'),
        ("felder fehlen", b'{"model": "x"}'),
    ],
)
def test_broken_entry_is_a_miss_not_a_crash(tmp_path, name, payload):
    """Ein Cache ist eine Optimierung, keine Datenquelle.

    Was auch immer in der Datei steht: der Lauf geht weiter und fragt das
    Modell. Ein Abbruch waere die schlechtere Antwort auf einen Defekt in
    einem Verzeichnis, das man jederzeit loeschen koennte.
    """
    cache = _cache(tmp_path)
    key = cache.key(PROMPT)
    _write_raw(cache, key, payload)

    assert cache.get(key) is None, name
    assert cache.stats().errors == 1


def test_valid_json_with_impossible_values_is_a_miss(tmp_path):
    cache = _cache(tmp_path)
    key = cache.key(PROMPT)
    _write_raw(cache, key, _wrong_fields(key))

    assert cache.get(key) is None
    assert cache.stats().errors == 1


def test_broken_entry_is_repaired_by_the_next_write(tmp_path):
    cache = _cache(tmp_path)
    key = cache.key(PROMPT)
    _write_raw(cache, key, b"kaputt")

    assert cache.get(key) is None
    cache.put(key, _proposal())
    assert cache.get(key) == _proposal()


def test_entry_of_another_model_is_not_a_hit(tmp_path):
    """Der Dateiname allein beweist nichts -- der Eintrag wird gegengeprueft.

    Eine von Hand kopierte Datei liegt an der richtigen Stelle und enthaelt
    trotzdem die Antwort auf eine andere Frage.
    """
    mine = _cache(tmp_path, model="model-a")
    key = mine.key(PROMPT)

    foreign = _cache(tmp_path, model="model-b")
    entry = {
        "key": key,
        "model": "model-b",
        "schema_version": mine.schema_version,
        "value": _proposal().model_dump(mode="json"),
    }
    _write_raw(foreign, key, json.dumps(entry).encode("utf-8"))

    assert mine.get(key) is None
    assert mine.stats().errors == 1


# -- Atomares Schreiben -----------------------------------------------------


def test_write_leaves_nothing_behind_when_it_fails(tmp_path, monkeypatch):
    """Abbruch mitten im Schreiben darf keine lesbare Ruine hinterlassen."""
    import qt.llm.cache as cache_module

    cache = _cache(tmp_path)
    key = cache.key(PROMPT)

    def boom(src, dst):
        raise OSError("Abbruch genau zwischen Schreiben und Umbenennen")

    monkeypatch.setattr(cache_module.os, "replace", boom)

    with pytest.raises(OSError):
        cache.put(key, _proposal())

    assert cache.get(key) is None
    assert list(tmp_path.rglob("*.json")) == []
    assert list(tmp_path.rglob("*.partial")) == []


def test_failed_write_keeps_the_previous_entry(tmp_path, monkeypatch):
    """Entweder der alte Eintrag oder der neue -- nie etwas dazwischen."""
    import qt.llm.cache as cache_module

    cache = _cache(tmp_path)
    key = cache.key(PROMPT)
    cache.put(key, _proposal())

    monkeypatch.setattr(
        cache_module.os,
        "replace",
        lambda src, dst: (_ for _ in ()).throw(OSError("Abbruch")),
    )
    other = _proposal()
    other.regime = "voellig andere Einschaetzung"
    with pytest.raises(OSError):
        cache.put(key, other)

    assert cache.get(key) == _proposal()


def test_leftover_temp_file_is_never_read_as_a_hit(tmp_path):
    """Ein harter Abbruch kann eine temporaere Datei zuruecklassen.

    Sie darf nicht auf .json enden -- sonst waere die halb geschriebene
    Fassung beim naechsten Lauf ein Kandidat fuer einen Treffer.
    """
    cache = _cache(tmp_path)
    key = cache.key(PROMPT)
    target = cache.path_for(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    (target.parent / f"{target.name}.999.partial").write_text("halb geschrieben")

    assert cache.get(key) is None
    assert cache.stats().entries == 0


# -- Statistik --------------------------------------------------------------


def test_stats_counts_hits_and_misses(tmp_path):
    """0% Trefferquote ueber einen ganzen Lauf heisst: der Key ist instabil."""
    cache = _cache(tmp_path)
    key = cache.key(PROMPT)

    assert cache.stats() == CacheStats()

    cache.get(key)  # Fehlschlag: nichts da
    cache.put(key, _proposal())
    cache.get(key)  # Treffer
    cache.get(key)  # Treffer
    cache.get(cache.key("anderes Briefing"))  # Fehlschlag

    stats = cache.stats()
    assert (stats.hits, stats.misses, stats.errors) == (2, 2, 0)
    assert stats.lookups == 4
    assert stats.hit_rate == 0.5
    assert "Treffer" in stats.describe()


def test_stats_reports_size_of_the_store(tmp_path):
    cache = _cache(tmp_path)
    for i in range(3):
        cache.put(cache.key(f"{PROMPT} #{i}"), _proposal())

    stats = cache.stats()
    assert stats.entries == 3
    assert stats.bytes > 0
    assert stats.hit_rate == 0.0, "ohne Abfragen ist die Quote 0, nicht undefiniert"


def test_stats_on_an_untouched_cache_is_empty(tmp_path):
    stats = _cache(tmp_path / "gibt-es-noch-nicht").stats()

    assert (stats.entries, stats.bytes, stats.lookups) == (0, 0, 0)
