"""Der Versuchszaehler zaehlt die handgeschriebenen Hypothesen mit.

Der Zaehler ist der Nenner jeder Deflated Sharpe Ratio. Faellt er zu niedrig
aus, wird jede kuenftige Korrektur zu optimistisch -- und ein Schutz, der
Sicherheit vortaeuscht, ist schlimmer als keiner.
"""

from __future__ import annotations

from pathlib import Path

from qt.research.backfill import HYPOTHESEN, QUELLE, bereits_eingetragen, nachtragen
from qt.research.registry import ResearchRegistry


def _leere_registry(tmp_path: Path) -> ResearchRegistry:
    return ResearchRegistry.open(tmp_path / "registry.duckdb")


def test_nachtragen_erhoeht_den_zaehler_um_die_sieben(tmp_path):
    with _leere_registry(tmp_path) as r:
        assert r.trial_count() == 0
        neu = nachtragen(r)
        assert len(neu) == len(HYPOTHESEN) == 7
        assert r.trial_count() == 7


def test_nachtragen_ist_idempotent(tmp_path):
    """Zweimal laufen darf den Zaehler nicht verdoppeln.

    Ein Zaehler, der bei jedem Aufruf waechst, waere schlimmer als einer, der
    zu niedrig steht: er sieht nach Sorgfalt aus und ist Zufall.
    """
    with _leere_registry(tmp_path) as r:
        nachtragen(r)
        assert nachtragen(r) == []
        assert r.trial_count() == 7


def test_dry_run_schreibt_nichts(tmp_path):
    with _leere_registry(tmp_path) as r:
        assert len(nachtragen(r, dry_run=True)) == 7
        assert r.trial_count() == 0


def test_die_eintraege_sind_als_handschrift_erkennbar(tmp_path):
    """Sonst liessen sie sich spaeter nicht von Loop-Kandidaten trennen."""
    with _leere_registry(tmp_path) as r:
        nachtragen(r)
        assert bereits_eingetragen(r) == {h.klasse for h in HYPOTHESEN}
        df = r.history()
        assert set(df["generator_model"]) == {QUELLE}


def test_jede_hypothese_nennt_ihr_adr(tmp_path):
    """Ein Versuch ohne Fundstelle ist eine Zahl ohne Geschichte."""
    with _leere_registry(tmp_path) as r:
        nachtragen(r)
        for zeile in r.history()["rationale"]:
            assert zeile.startswith("ADR-"), zeile


def test_kein_sharpe_wird_erfunden(tmp_path):
    """Die sieben stammen aus verschiedenen Zeitebenen und Kostenannahmen.

    Eine davon als vergleichbare Kennzahl abzulegen waere eine Praezision,
    die es nie gab. Zaehlbar ist der Versuch, nicht sein Ergebnis.
    """
    import pandas as pd

    with _leere_registry(tmp_path) as r:
        nachtragen(r)
        assert pd.isna(r.history()["sharpe"]).all()


def test_alle_sieben_bibliotheksstrategien_sind_abgedeckt():
    """Faellt eine achte in die Bibliothek, soll dieser Test daran erinnern."""
    from qt.strategy.registry import load_library, names

    load_library()
    assert len(names()) == len(HYPOTHESEN), (
        f"Bibliothek hat {len(names())} Strategien, HYPOTHESEN kennt "
        f"{len(HYPOTHESEN)}. Neue Strategie = neuer Versuch (ADR-005)."
    )


def test_stub_und_echte_registry_sind_verschiedene_dateien():
    """Ein Rauchtest darf den Versuchszaehler nicht heben.

    Gemessen, nicht vermutet: `qt research --generate 3 --stub` hob den
    echten Zaehler am 2026-09-02 von 15 auf 18, bevor die Trennung existierte.
    Die Stub-Kandidaten stehen fest, egal was die Kurse sagen -- sie sind kein
    Selektionsereignis und duerfen den DSR-Nenner nicht belasten (ADR-057).
    """
    from qt.research.registry import DEFAULT_PATH, STUB_PATH

    assert DEFAULT_PATH != STUB_PATH


def test_der_stub_pfad_wird_bei_stub_laeufen_auch_benutzt():
    """Zwei Konstanten helfen nichts, wenn die CLI die falsche oeffnet."""
    import inspect

    from qt import cli

    quelle = inspect.getsource(cli.research)
    assert "STUB_PATH if stub else None" in quelle, (
        "Der Stub-Lauf muss die Stub-Registry oeffnen"
    )
