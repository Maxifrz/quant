"""Gate 1 als Programm: die Kriterien, ihre Reihenfolge, und was nicht zaehlt.

Der teuerste Fehler waere ein Gate, das durchwinkt. Der zweitteuerste eines,
das teure Pruefungen laufen laesst, obwohl schon feststeht, dass sie nichts
aendern -- jedes abgeschlossene Screening erhoeht den DSR-Nenner dauerhaft
(ADR-032).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from qt.core.types import Bar
from qt.research import gate as gate_mod
from qt.research.gate import (
    MAX_UMSCHLAG_PRO_JAHR,
    MIN_FILLS,
    MIN_SHARPE,
    umschlag_pro_jahr,
)

T0 = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _equity(werte: list[float], umschlaege: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": [T0 + timedelta(days=i) for i in range(len(werte))],
            "equity": werte,
            "turnover": umschlaege,
        }
    )


def test_umschlag_wird_gegen_das_jeweilige_eigenkapital_gerechnet():
    """Der springende Punkt aus ADR-056.

    Zwei Konten schlagen denselben Dollarbetrag um, aber eines ist zehnmal so
    gross. Der kumulierte Umschlag ist gleich, die Kostenlast nicht.
    """
    ein_jahr = 366  # T0 ist ein Schaltjahr-Beginn
    klein = _equity([100.0] * ein_jahr, [i * 100.0 for i in range(ein_jahr)])
    gross = _equity([1000.0] * ein_jahr, [i * 100.0 for i in range(ein_jahr)])

    assert umschlag_pro_jahr(klein) == pytest.approx(10 * umschlag_pro_jahr(gross), rel=0.01)


def test_umschlag_eines_konstanten_kontos_ist_die_erwartete_zahl():
    """Gegenprobe von Hand: 100 pro Tag auf 100 Kapital, ein Jahr."""
    tage = 366
    df = _equity([100.0] * tage, [i * 100.0 for i in range(tage)])
    # 365 Zuwaechse zu je 100, jeweils durch 100 Kapital -> 365, auf ~1 Jahr
    assert umschlag_pro_jahr(df) == pytest.approx(365.0, rel=0.02)


def test_ohne_zeitspanne_gibt_es_keine_jahresrate():
    df = _equity([100.0, 100.0], [0.0, 50.0])
    df["ts"] = [T0, T0]
    assert pd.isna(umschlag_pro_jahr(df))


# ---------------------------------------------------------------------------
# Die Schwellen sind Konstanten und keine Optionen
# ---------------------------------------------------------------------------


def test_das_gate_kennt_keine_option_die_eine_schwelle_setzt():
    """Der eigentliche Bauentscheid.

    Nach einem verfehlten Kriterium ist die Versuchung, den Massstab
    nachzubessern, am groessten (ADR-055). Eine Schwelle als
    Kommandozeilenoption waere die bequemste Stelle, genau das zu tun --
    ohne Diff, ohne Spur.
    """
    import inspect

    from qt.cli import gate_cmd

    parameter = set(inspect.signature(gate_cmd).parameters)
    verboten = {
        "min_sharpe", "dsr", "dsr_threshold", "min_dsr", "perzentil",
        "max_umschlag", "turnover", "min_fills", "anlageklassen",
    }
    assert not (parameter & verboten), (
        f"Das Gate darf keine Schwelle als Option annehmen: {parameter & verboten}"
    )


def test_die_schwellen_stehen_dort_wo_ADR_055_und_056_sie_hergeleitet_haben():
    assert MIN_SHARPE == 0.41
    assert MAX_UMSCHLAG_PRO_JAHR == 7.0
    assert MIN_FILLS == 20


# ---------------------------------------------------------------------------
# Reihenfolge und Abbruch
# ---------------------------------------------------------------------------


def _bars(n: int, symbol: str = "BTC/USD") -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            ts=T0 + timedelta(days=i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1000.0,
        )
        for i in range(n)
    ]


class _Nichtstuer:
    """Eine Strategie, die nie handelt."""

    name = "nichtstuer"
    warmup_bars = 2

    def __init__(self, symbols, timeframe):
        self.symbols = symbols
        self.timeframe = timeframe

    def on_bar(self, symbol, store):
        return 0.0

    def describe(self):
        return self.name


def test_wer_nicht_handelt_besteht_nichts(monkeypatch):
    """Der Fund aus dem ersten Lauf ueber die Bibliothek.

    `orderflow` und `timesfm` "bestanden" das Umschlagbudget mit 0,1x und
    0,0x -- weil beiden die Datenquelle fehlt. Ein Kriterium, das sich durch
    Nichtstun erfuellen laesst, ist keines.
    """
    maerkte = {"BTC/USD": _bars(300)}
    ergebnis = gate_mod.run_gate(_Nichtstuer, maerkte, "1d", trial_count=1)

    assert not ergebnis.bestanden
    assert ergebnis.abgebrochen_nach == "Aktivitaet"
    assert ergebnis.kriterien[0].name == "Ausfuehrungen"
    assert not ergebnis.screening_gezaehlt, (
        "Ohne Walk-Forward darf der Versuchszaehler nicht steigen (ADR-032)"
    )


def test_eine_zu_feine_zeitebene_wird_abgelehnt_bevor_gerechnet_wird():
    """Unter 1d frisst die Ausfuehrung den Edge (ADR-047, ADR-056)."""
    ergebnis = gate_mod.run_gate(_Nichtstuer, {"BTC/USD": _bars(300)}, "4h", trial_count=1)
    assert ergebnis.abgebrochen_nach == "Zeitebene"
    assert not ergebnis.screening_gezaehlt


def test_ein_abbruch_vor_dem_walk_forward_kostet_keinen_versuch():
    """Der Grund fuer die Kostenreihenfolge, nicht nur Sparsamkeit."""
    ergebnis = gate_mod.run_gate(_Nichtstuer, {"BTC/USD": _bars(300)}, "1d", trial_count=1)
    assert not ergebnis.screening_gezaehlt
    assert "NICHT als Versuch" in ergebnis.table()


def test_das_urteil_ist_nur_bestanden_wenn_jedes_kriterium_geprueft_wurde():
    """Ein leeres Ergebnis darf nicht als bestanden durchgehen."""
    from qt.research.gate import GateResult, Kriterium

    leer = GateResult(strategie="x", timeframe="1d")
    assert not leer.bestanden

    halb = GateResult(
        strategie="x",
        timeframe="1d",
        kriterien=[Kriterium("a", True, "1", "1")],
        abgebrochen_nach="Umschlag",
    )
    assert halb.bestanden is True and halb.offen is True, (
        "bestanden und offen sind verschiedene Fragen -- die Tabelle muss "
        "beides zeigen"
    )
    assert "ABGEBROCHEN" in halb.table()
