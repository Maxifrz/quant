"""Rank IC: die Fill-Konvention und die Autokorrelaturkorrektur.

Beides sind die Stellen, an denen sich diese Umsetzung vom NVIDIA-Blueprint
unterscheidet, und beides sind Stellen, an denen ein Fehler keinen Test rot
macht, sondern nur eine Zahl schoener.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from qt.research.ic import (
    T_SCHWELLE,
    autokorrelation,
    effektive_perioden,
    forward_returns,
    rank_ic,
)


def _panel(werte: dict[str, list[float]]) -> pd.DataFrame:
    n = len(next(iter(werte.values())))
    idx = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    return pd.DataFrame(werte, index=idx)


# ---------------------------------------------------------------------------
# Fill-Konvention
# ---------------------------------------------------------------------------


def test_vorwaertsrendite_beginnt_am_open_des_naechsten_bars():
    """Der Unterschied zum Blueprint, an einem Fall von Hand nachgerechnet.

    Der Blueprint rechnet close[t+k]/close[t]: Einstieg zu einem Kurs, den man
    gerade erst benutzt hat, um sich zu entscheiden. Hier ist der Einstieg das
    Open von t+1 (ADR-001).
    """
    opens = _panel({"A": [10.0, 20.0, 40.0, 80.0, 160.0]})
    fwd = forward_returns(opens, horizont=1)
    # Zeile t=0: Einstieg Open[1]=20, Ausstieg Open[2]=40 -> +100 %
    assert fwd["A"].iloc[0] == pytest.approx(1.0)
    # Zeile t=2: Einstieg Open[3]=80, Ausstieg Open[4]=160 -> +100 %
    assert fwd["A"].iloc[2] == pytest.approx(1.0)
    # Die letzten beiden Zeilen haben keinen Ausstieg mehr.
    assert np.isnan(fwd["A"].iloc[-1]) and np.isnan(fwd["A"].iloc[-2])


def test_horizont_null_wird_abgelehnt():
    with pytest.raises(ValueError, match="horizont"):
        forward_returns(_panel({"A": [1.0, 2.0]}), horizont=0)


def test_ein_open_von_null_wird_nan_und_nicht_unendlich():
    opens = _panel({"A": [10.0, 0.0, 40.0, 80.0]})
    assert np.isnan(forward_returns(opens, horizont=1)["A"].iloc[0])


# ---------------------------------------------------------------------------
# Autokorrelaturkorrektur -- der eigentliche Fund
# ---------------------------------------------------------------------------


def test_effektive_perioden_bei_unabhaengigkeit_gleich_der_zahl():
    assert effektive_perioden(1000, 0.0) == pytest.approx(1000.0)


def test_effektive_perioden_fallen_mit_der_autokorrelation():
    """rho = 0,8 -> etwa ein Neuntel. Von Hand: (1-0,8)/(1+0,8) = 0,111."""
    assert effektive_perioden(900, 0.8) == pytest.approx(100.0, rel=1e-6)


def test_negative_autokorrelation_hebt_die_stichprobe_nicht_ueber_n():
    """Rechnerisch korrekt waere mehr; als Signifikanzgrundlage zu grosszuegig."""
    assert effektive_perioden(500, -0.5) == 500.0


def test_autokorrelation_einer_konstanten_reihe_ist_null_statt_nan():
    assert autokorrelation(np.ones(50)) == 0.0
    assert autokorrelation(np.array([1.0, 2.0])) == 0.0


def test_traeges_rauschsignal_wird_naiv_signifikant_und_korrigiert_nicht():
    """Die Gegenprobe, die den ganzen Unterschied traegt.

    Signal und Renditen sind unabhaengig -- es KANN keinen Zusammenhang
    geben. Weil das Signal traege ist, wird die IC-Reihe autokorreliert, und
    die naive Rechnung des Blueprints haelt das fuer Evidenz.
    """
    rng = np.random.default_rng(7)
    n_tage, n_namen, glaettung, horizont = 1500, 40, 60, 21
    idx = pd.date_range("2020-01-01", periods=n_tage, freq="1D", tz="UTC")
    spalten = [f"S{j}" for j in range(n_namen)]

    # Traeges Signal: gleitendes Mittel ueber unabhaengiges Rauschen.
    roh = rng.normal(0, 1, (n_tage, n_namen))
    kern = np.ones(glaettung) / glaettung
    traege = np.column_stack(
        [np.convolve(roh[:, j], kern, mode="same") for j in range(n_namen)]
    )
    signal = pd.DataFrame(traege, index=idx, columns=spalten)

    # Kurse aus unabhaengigem Rauschen -- und daraus **ueberlappende**
    # Vorwaertsrenditen ueber den echten Pfad. Beides zusammen erzeugt die
    # Autokorrelation: ein traeges Signal allein genuegt nicht, wenn die
    # Renditefenster sich nicht ueberschneiden.
    kurse = pd.DataFrame(
        100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n_tage, n_namen)), axis=0)),
        index=idx, columns=spalten,
    )
    renditen = forward_returns(kurse, horizont)

    r = rank_ic(signal, renditen, horizont=horizont)
    assert r.n_perioden > 500
    assert r.rho > 0.5, f"Ein traeges Signal muss autokorrelierte ICs geben, rho={r.rho}"
    assert r.n_eff < r.n_perioden / 3
    assert r.aufblaehung > 1.7, (
        f"Der naive t-Wert muss deutlich zu gross sein, Faktor {r.aufblaehung}"
    )


def test_bestanden_haengt_am_korrigierten_wert_nicht_am_naiven():
    """Es gibt keinen Schalter, der die Korrektur abstellt."""
    from qt.research.ic import ICResult

    r = ICResult(
        mittel=0.03, streuung=0.4, n_perioden=2500, rho=0.8,
        n_eff=effektive_perioden(2500, 0.8), anteil_positiv=0.54, horizont=21,
    )
    assert abs(r.t_naiv) > T_SCHWELLE
    assert abs(r.t_korrigiert) < T_SCHWELLE
    assert not r.bestanden


# ---------------------------------------------------------------------------
# Rank IC selbst
# ---------------------------------------------------------------------------


def test_ein_perfekt_ordnendes_signal_gibt_ic_eins():
    idx = pd.date_range("2024-01-01", periods=30, freq="1D", tz="UTC")
    spalten = [f"S{j}" for j in range(10)]
    rng = np.random.default_rng(1)
    renditen = pd.DataFrame(rng.normal(0, 0.02, (30, 10)), index=idx, columns=spalten)
    r = rank_ic(renditen.copy(), renditen, horizont=1)
    assert r.mittel == pytest.approx(1.0)


def test_ein_umgekehrtes_signal_gibt_ic_minus_eins():
    """Das Vorzeichen wird durchgereicht -- der Blueprint nimmt hier abs()."""
    idx = pd.date_range("2024-01-01", periods=30, freq="1D", tz="UTC")
    spalten = [f"S{j}" for j in range(10)]
    rng = np.random.default_rng(2)
    renditen = pd.DataFrame(rng.normal(0, 0.02, (30, 10)), index=idx, columns=spalten)
    r = rank_ic(-renditen, renditen, horizont=1)
    assert r.mittel == pytest.approx(-1.0)


def test_zu_wenige_namen_ergeben_kein_ergebnis_statt_einer_zahl():
    """Eine Rangkorrelation ueber vier Werte ist Rauschen mit Dezimalstellen."""
    idx = pd.date_range("2024-01-01", periods=30, freq="1D", tz="UTC")
    klein = pd.DataFrame(np.random.default_rng(3).normal(0, 1, (30, 4)),
                         index=idx, columns=list("ABCD"))
    r = rank_ic(klein, klein, horizont=1)
    assert r.n_perioden == 0 and np.isnan(r.mittel)


def test_ein_konstantes_signal_hat_keine_meinung_und_keine_korrelation_null():
    idx = pd.date_range("2024-01-01", periods=40, freq="1D", tz="UTC")
    spalten = [f"S{j}" for j in range(10)]
    konstant = pd.DataFrame(1.0, index=idx, columns=spalten)
    rng = np.random.default_rng(4)
    renditen = pd.DataFrame(rng.normal(0, 0.02, (40, 10)), index=idx, columns=spalten)
    assert rank_ic(konstant, renditen, horizont=1).n_perioden == 0


def test_der_naive_t_wert_stimmt_mit_der_blueprint_formel_ueberein():
    """Damit der Vergleich in der Tabelle wirklich deren Rechnung ist."""
    rng = np.random.default_rng(5)
    idx = pd.date_range("2024-01-01", periods=300, freq="1D", tz="UTC")
    spalten = [f"S{j}" for j in range(12)]
    signal = pd.DataFrame(rng.normal(0, 1, (300, 12)), index=idx, columns=spalten)
    renditen = pd.DataFrame(rng.normal(0, 0.02, (300, 12)), index=idx, columns=spalten)
    r = rank_ic(signal, renditen, horizont=1)

    ic = r.ics
    erwartet = ic.mean() / (ic.std() / np.sqrt(len(ic)))
    assert r.t_naiv == pytest.approx(erwartet)
    # und der p-Wert des Blueprints waere der aus T-1 Freiheitsgraden
    assert stats.t.cdf(abs(erwartet), df=len(ic) - 1) > 0.0
