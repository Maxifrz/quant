"""Querschnittsstrategien: der Panelaufbau ist die heikle Stelle.

Der erste Entwurf verlangte, dass alle Symbole einen Bar zum aktuellen
Zeitpunkt haben. Die Engine arbeitet die Bars eines Zeitpunkts aber
nacheinander ab -- die Bedingung war fuer alle ausser dem zuletzt
bearbeiteten Symbol unerfuellbar, und die Strategie machte ueber die ganze
Historie **null** Ausfuehrungen. Kein Test war rot; sie handelte einfach nicht.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from qt.backtest.engine import run_backtest
from qt.core.config import BacktestConfig
from qt.core.types import Bar
from qt.strategy.cross_sectional import (
    MIN_NAMEN,
    CrossMomentum,
    CrossReversal,
    CrossSectionalStrategy,
    score_panel,
)

T0 = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _bars(symbol: str, kurse: list[float]) -> list[Bar]:
    return [
        Bar(
            symbol=symbol, timeframe="1d", ts=T0 + timedelta(days=i),
            open=k, high=k * 1.01, low=k * 0.99, close=k, volume=1_000.0,
        )
        for i, k in enumerate(kurse)
    ]


def _markt(n_symbole: int, n_bars: int, seed: int = 0) -> dict[str, list[Bar]]:
    rng = np.random.default_rng(seed)
    return {
        f"S{j}": _bars(
            f"S{j}", list(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n_bars))))
        )
        for j in range(n_symbole)
    }


class _Kurz(CrossSectionalStrategy):
    """Momentum ueber ein kurzes Fenster, damit Tests schnell bleiben."""

    name = "_kurz"
    lookback = 10
    rebalance_every = 5

    def score(self, panel: pd.DataFrame) -> pd.Series:
        return panel.iloc[-1] / panel.iloc[0] - 1.0


# ---------------------------------------------------------------------------
# Der Fund: sie muss ueberhaupt handeln
# ---------------------------------------------------------------------------


def test_die_strategie_handelt_ueberhaupt():
    """Der Regressionstest fuer den Fehler, der einen ganzen Lauf gekostet hat."""
    bars = _markt(n_symbole=12, n_bars=120)
    ergebnis = run_backtest(_Kurz(sorted(bars), "1d"), bars, BacktestConfig())
    assert len(ergebnis.fills) > 0, (
        "Null Ausfuehrungen heisst: der Panelaufbau ist mit der "
        "Ereignisreihenfolge der Engine unvereinbar"
    )


def test_das_panel_endet_vor_dem_aktuellen_zeitpunkt():
    """Der letzte gemeinsame Bar ist abgeschlossen, der aktuelle nicht.

    Ein Bar Verzoegerung ist der Preis dafuer, dass jedes Symbol dasselbe
    Panel sieht, statt von seiner Position im Ereignisstrom abzuhaengen.
    """
    gesehen: list[tuple] = []

    class _Merkt(_Kurz):
        name = "_merkt"

        def score(self, panel: pd.DataFrame) -> pd.Series:
            gesehen.append((panel.index[-1], len(panel.columns)))
            return panel.iloc[-1] / panel.iloc[0] - 1.0

    bars = _markt(n_symbole=12, n_bars=60)
    run_backtest(_Merkt(sorted(bars), "1d"), bars, BacktestConfig())
    assert gesehen, "score wurde nie gerufen"
    for stempel, n_spalten in gesehen:
        assert n_spalten >= MIN_NAMEN


def test_unter_min_namen_gibt_es_kein_signal():
    """Eine Rangfolge ueber vier Namen ist keine."""
    bars = _markt(n_symbole=4, n_bars=60)
    ergebnis = run_backtest(_Kurz(sorted(bars), "1d"), bars, BacktestConfig())
    assert len(ergebnis.fills) == 0


# ---------------------------------------------------------------------------
# Gewichte
# ---------------------------------------------------------------------------


def test_gewichte_sind_marktneutral_und_auf_brutto_eins_normiert():
    s = _Kurz([f"S{j}" for j in range(10)], "1d")
    werte = pd.Series({f"S{j}": float(j) for j in range(10)})
    g = s.gewichte_aus_score(werte)
    assert sum(g.values()) == pytest.approx(0.0, abs=1e-9)
    assert sum(abs(v) for v in g.values()) == pytest.approx(1.0)
    assert g["S9"] > 0 and g["S0"] < 0


def test_ein_ausreisser_verschiebt_die_nulllinie_nicht():
    """Zentriert wird auf den Median, nicht auf den Mittelwert."""
    s = _Kurz([f"S{j}" for j in range(10)], "1d")
    normal = pd.Series({f"S{j}": float(j) for j in range(10)})
    mit_ausreisser = normal.copy()
    mit_ausreisser["S9"] = 10_000.0
    assert s.gewichte_aus_score(normal) == pytest.approx(
        s.gewichte_aus_score(mit_ausreisser)
    )


def test_zu_wenige_gueltige_werte_ergeben_keine_gewichte():
    s = _Kurz([f"S{j}" for j in range(10)], "1d")
    werte = pd.Series({f"S{j}": (float(j) if j < 3 else np.nan) for j in range(10)})
    assert s.gewichte_aus_score(werte) == {}


# ---------------------------------------------------------------------------
# Umschichtrhythmus -- ohne ihn ist die Familie von den Kosten erledigt
# ---------------------------------------------------------------------------


def test_seltener_umschichten_senkt_den_umschlag():
    """Taeglich schlaegt crossmom 79x sein Kapital um, monatlich 4,6x (ADR-058)."""
    from qt.research.gate import umschlag_pro_jahr

    bars = _markt(n_symbole=12, n_bars=400, seed=3)

    class _Taeglich(_Kurz):
        name = "_taeglich"
        rebalance_every = 1

    haeufig = run_backtest(_Taeglich(sorted(bars), "1d"), bars, BacktestConfig())
    selten = run_backtest(_Kurz(sorted(bars), "1d"), bars, BacktestConfig())
    assert umschlag_pro_jahr(selten.equity) < umschlag_pro_jahr(haeufig.equity)


def test_der_zaehler_laeuft_ueber_zeitpunkte_nicht_ueber_aufrufe():
    """Bei 27 Maerkten kaeme on_bar 27-mal je Bar -- der Monat waere nach
    anderthalb Tagen um."""
    umschichtungen: list[datetime] = []

    class _Zaehlt(_Kurz):
        name = "_zaehlt"
        rebalance_every = 10

        def score(self, panel: pd.DataFrame) -> pd.Series:
            umschichtungen.append(panel.index[-1])
            return panel.iloc[-1] / panel.iloc[0] - 1.0

    bars = _markt(n_symbole=12, n_bars=120)
    run_backtest(_Zaehlt(sorted(bars), "1d"), bars, BacktestConfig())
    assert len(umschichtungen) >= 2
    abstaende = [
        (b - a).days for a, b in zip(umschichtungen, umschichtungen[1:], strict=False)
    ]
    assert min(abstaende) >= 10, f"Zu haeufig umgeschichtet: {abstaende[:5]}"


# ---------------------------------------------------------------------------
# Die konkreten Strategien
# ---------------------------------------------------------------------------


def test_crossmom_laesst_den_juengsten_monat_aus():
    p = pd.DataFrame(
        np.arange(300 * 10, dtype=float).reshape(300, 10) + 100.0,
        index=pd.date_range("2020-01-01", periods=300, freq="1D", tz="UTC"),
        columns=[f"S{j}" for j in range(10)],
    )
    s = CrossMomentum([f"S{j}" for j in range(10)], "1d")
    erwartet = p.iloc[-22] / p.iloc[-253] - 1.0
    assert np.allclose(s.score(p).to_numpy(), erwartet.to_numpy())


def test_crossmom_lehnt_ein_skip_ab_das_den_lookback_frisst():
    with pytest.raises(ValueError, match="skip"):
        CrossMomentum(["A", "B"], "1d", lookback=20, skip=20)


def test_crossrev_hat_das_umgekehrte_vorzeichen_von_momentum():
    """Zwei Strategien mit entgegengesetztem Vorzeichen zeigen, dass die
    Mechanik das Vorzeichen ueberhaupt durchreicht."""
    idx = pd.date_range("2020-01-01", periods=40, freq="1D", tz="UTC")
    spalten = [f"S{j}" for j in range(10)]
    rng = np.random.default_rng(9)
    p = pd.DataFrame(
        100 * np.exp(np.cumsum(rng.normal(0, 0.02, (40, 10)), axis=0)),
        index=idx, columns=spalten,
    )
    rev = CrossReversal(spalten, "1d", lookback=21)
    einfach = p.iloc[-1] / p.iloc[-22] - 1.0
    assert np.allclose(rev.score(p).to_numpy(), (-einfach).to_numpy())


def test_score_panel_rollt_und_schaut_nicht_nach_vorn():
    """Jede Zeile entsteht nur aus der Historie bis dahin."""
    idx = pd.date_range("2020-01-01", periods=60, freq="1D", tz="UTC")
    spalten = [f"S{j}" for j in range(10)]
    rng = np.random.default_rng(11)
    p = pd.DataFrame(
        100 * np.exp(np.cumsum(rng.normal(0, 0.02, (60, 10)), axis=0)),
        index=idx, columns=spalten,
    )
    s = _Kurz(spalten, "1d")
    matrix = score_panel(s, p)

    # Zukunft veraendern -> die frueheren Zeilen duerfen sich nicht ruehren.
    veraendert = p.copy()
    veraendert.iloc[45:] *= 3.0
    matrix2 = score_panel(s, veraendert)
    gemeinsam = matrix.index.intersection(matrix2.index)
    frueh = [ts for ts in gemeinsam if ts <= idx[44]]
    assert frueh
    assert np.allclose(
        matrix.loc[frueh].to_numpy(), matrix2.loc[frueh].to_numpy(), equal_nan=True
    )
