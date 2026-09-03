"""Das Operator-Vokabular: kein Blick nach vorn, saubere Aritaeten.

Der erste Test ist der wichtigste. Ein Operator mit `shift(-1)` waere ein
Lookahead, den kein Backtest meldet -- er macht das Ergebnis nur besser.
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from qt.research import operators as ops
from qt.research.operators import OPERATORS, arities, describe


def _panel(n: int = 60, m: int = 5, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    return pd.DataFrame(
        100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n, m)), axis=0)),
        index=idx, columns=[f"S{j}" for j in range(m)],
    )


def test_kein_operator_schaut_nach_vorn():
    """Am Quelltext geprueft, nicht am Vertrauen.

    Eine negative Verschiebung nimmt einen Wert aus der Zukunft. In einem
    Signal ist das der Fehler, der jeden Backtest gewinnt und jedes echte
    Konto verliert.
    """
    for name, fn in OPERATORS.items():
        quelle = inspect.getsource(fn)
        assert "shift(-" not in quelle.replace(" ", ""), f"{name} verschiebt negativ"
        assert ".iloc[-1 -" not in quelle or name in (), f"{name} greift nach vorn"


def test_ts_operatoren_lassen_den_vorlauf_leer():
    """Ein Fenster von d Werten kann vor dem d-ten Bar nichts sagen."""
    p = _panel(n=60)
    for name in ("TS_Mean", "TS_Std", "TS_Rank", "TS_Zscore", "TS_Vol"):
        ergebnis = OPERATORS[name](p, 20)
        assert ergebnis.iloc[:18].isna().all().all(), f"{name} rechnet zu frueh"
        assert ergebnis.iloc[-1].notna().any(), f"{name} liefert am Ende nichts"


def test_ts_momentum_laesst_den_juengsten_monat_aus():
    """`skip` ist nicht Zierde, sondern die Fassung aus der Literatur."""
    p = _panel(n=300)
    mit_skip = ops.TS_Momentum(p, d=252, skip=21)
    ohne_skip = ops.TS_Momentum(p, d=252, skip=0)
    assert not np.allclose(
        mit_skip.iloc[-1].to_numpy(), ohne_skip.iloc[-1].to_numpy()
    )
    # Von Hand: mit skip vergleicht Kurs[t-21] gegen Kurs[t-252].
    erwartet = p.iloc[-22] / p.iloc[-253] - 1.0
    assert np.allclose(mit_skip.iloc[-1].to_numpy(), erwartet.to_numpy())


def test_momentum_mit_skip_groesser_als_lookback_wird_abgelehnt():
    with pytest.raises(ValueError, match="skip"):
        ops.TS_Momentum(_panel(), d=10, skip=10)


def test_cs_rank_ordnet_innerhalb_des_zeitpunkts():
    p = pd.DataFrame(
        [[1.0, 5.0, 3.0], [9.0, 2.0, 4.0]],
        index=pd.date_range("2024-01-01", periods=2, freq="1D", tz="UTC"),
        columns=list("ABC"),
    )
    r = ops.CS_Rank(p)
    assert r.iloc[0].idxmax() == "B" and r.iloc[0].idxmin() == "A"
    assert r.iloc[1].idxmax() == "A" and r.iloc[1].idxmin() == "B"


def test_cs_demean_nimmt_heraus_was_allen_gemeinsam_ist():
    """Der Grund, warum ein Querschnittssignal mit korrelierten Maerkten kann."""
    p = _panel(n=30, m=6)
    gemeinsam = p.add(pd.Series(np.arange(30) * 10.0, index=p.index), axis=0)
    assert np.allclose(
        ops.CS_Demean(p).to_numpy(), ops.CS_Demean(gemeinsam).to_numpy()
    )


def test_cs_scale_normiert_auf_bruttoexposure_eins():
    p = pd.DataFrame(
        [[1.0, -2.0, 1.0], [0.0, 0.0, 0.0]],
        index=pd.date_range("2024-01-01", periods=2, freq="1D", tz="UTC"),
        columns=list("ABC"),
    )
    r = ops.CS_Scale(p)
    assert r.iloc[0].abs().sum() == pytest.approx(1.0)
    assert r.iloc[1].abs().sum() == 0.0, "Eine Nullzeile darf nicht durch 0 teilen"


def test_division_durch_null_wird_nan_und_nicht_unendlich():
    a = pd.DataFrame({"A": [1.0, 2.0]})
    b = pd.DataFrame({"A": [0.0, 2.0]})
    r = ops.Div(a, b)
    assert np.isnan(r["A"].iloc[0]) and r["A"].iloc[1] == pytest.approx(1.0)


def test_log_von_nicht_positiven_werten_wird_nan():
    p = pd.DataFrame({"A": [-1.0, 0.0, np.e]})
    r = ops.Log(p)
    assert np.isnan(r["A"].iloc[0]) and np.isnan(r["A"].iloc[1])
    assert r["A"].iloc[2] == pytest.approx(1.0)


def test_winsorize_stutzt_statt_zu_verwerfen():
    p = pd.DataFrame(
        [[1.0, 2.0, 3.0, 4.0, 100.0]],
        columns=list("ABCDE"),
    )
    r = ops.Winsorize(p, quantil=0.2)
    assert r.notna().all().all(), "Stutzen heisst nicht loeschen"
    assert r.iloc[0].max() < 100.0


def test_winsorize_lehnt_unsinnige_quantile_ab():
    with pytest.raises(ValueError, match="quantil"):
        ops.Winsorize(_panel(), quantil=0.6)


# ---------------------------------------------------------------------------
# Aritaeten -- der billige Vorfilter
# ---------------------------------------------------------------------------


def test_aritaeten_sind_aus_den_signaturen_abgeleitet():
    a = arities()
    assert a["TS_Mean"] == (1, 2)      # x noetig, d optional
    assert a["Add"] == (2, 2)          # beide noetig
    assert a["CS_Rank"] == (1, 1)      # nur x
    assert a["TS_Momentum"] == (1, 3)  # x noetig, d und skip optional


def test_jeder_operator_hat_eine_aritaet():
    assert set(arities()) == set(OPERATORS)


def test_describe_nennt_jeden_operator_mit_erster_dokuzeile():
    text = describe()
    for name in OPERATORS:
        assert name in text
    assert "CS_Demean" in text and "Querschnittsmittel" in text


def test_alle_operatoren_liefern_dataframes_und_stuerzen_nicht_ab():
    """Rauchtest ueber das ganze Vokabular -- ein kaputter faellt hier auf."""
    p = _panel(n=300, m=6)
    zwei_argumente = {"Add", "Sub", "Mul", "Div", "TS_Corr"}
    for name, fn in OPERATORS.items():
        ergebnis = fn(p, p) if name in zwei_argumente else fn(p)
        assert isinstance(ergebnis, pd.DataFrame), f"{name} liefert kein DataFrame"
        assert ergebnis.shape[1] == p.shape[1], f"{name} aendert die Spaltenzahl"


# ---------------------------------------------------------------------------
# Aritaetspruefung ohne Ausfuehrung
# ---------------------------------------------------------------------------


def test_zu_wenige_argumente_werden_beanstandet():
    (befund,) = ops.pruefe_aufrufe("y = TS_Mean()")
    assert "TS_Mean" in befund and "bekommt 0" in befund


def test_zu_viele_argumente_werden_beanstandet():
    (befund,) = ops.pruefe_aufrufe("y = CS_Rank(a, b, c)")
    assert "CS_Rank" in befund and "bekommt 3" in befund


def test_richtige_aufrufe_und_keyword_argumente_gehen_durch():
    assert ops.pruefe_aufrufe("y = TS_Momentum(p, 252, 21)") == []
    assert ops.pruefe_aufrufe("y = TS_Mean(p, d=20)") == []


def test_fremde_namen_werden_nicht_beanstandet():
    """Ob ein Name erlaubt ist, entscheidet die Sandbox, nicht dieses Modul."""
    assert ops.pruefe_aufrufe("y = irgendwas(1, 2, 3)") == []


def test_die_pruefung_fuehrt_nichts_aus():
    """Der Punkt gegenueber dem Blueprint, der `exec` ruft.

    Waere hier `exec` im Spiel, liefe fremder Code, bevor irgendetwas ihn
    geprueft hat -- genau das tut `signal_evaluator.py` Zeile 375.
    """
    marker = tests_pfad = __file__ + ".darf-nicht-entstehen"
    ops.pruefe_aufrufe(f"open({marker!r}, 'w').write('x')")
    import os

    assert not os.path.exists(tests_pfad)


def test_syntaxfehler_wird_gemeldet_statt_zu_werfen():
    (befund,) = ops.pruefe_aufrufe("y = TS_Mean(")
    assert "Syntaxfehler" in befund
