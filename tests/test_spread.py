"""Gegenprobe fuer die Spread-Schaetzer.

Diese Datei ist der Grund, warum `qt.backtest.spread` als untauglich
gekennzeichnet ist statt als Messinstrument benutzt zu werden. Die erste
Fassung des Moduls wertete die Formeln je Tagespaar aus und mittelte
hinterher; gegen eine simulierte Reihe mit einem Spread von **exakt null**
meldete sie 47 Basispunkte. An echten Kursen haette diese Zahl plausibel
ausgesehen.

Deshalb steht die Simulation hier als Test und nicht als einmaliges Skript:
sie ist die einzige Stelle im Projekt, an der die Wahrheit bekannt ist.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qt.backtest.spread import MONAT, abdi_ranaldo, corwin_schultz, schaetze


def simuliere(
    wahr_bps: float,
    tage: int = 1500,
    vol_tag: float = 0.02,
    ticks: int = 390,
    seed: int = 0,
) -> pd.DataFrame:
    """Tages-OHLC mit bekanntem, konstantem Spread.

    Innerhalb des Tages laeuft eine geometrische Brownsche Bewegung; jede
    Ausfuehrung schlaegt zufaellig auf Geld- oder Briefseite zu. Hoch und Tief
    sind damit um den halben Spread nach aussen verschoben -- genau die
    Verschiebung, die beide Schaetzer herausrechnen wollen.
    """
    rng = np.random.default_rng(seed)
    halb = wahr_bps / 2 / 10_000
    hoch, tief, schluss = [], [], []
    p = 100.0
    for _ in range(tage):
        pfad = p * np.exp(np.cumsum(rng.normal(0, vol_tag / np.sqrt(ticks), ticks)))
        gehandelt = pfad * (1 + rng.choice([-1.0, 1.0], ticks) * halb)
        hoch.append(gehandelt.max())
        tief.append(gehandelt.min())
        schluss.append(gehandelt[-1])
        p = pfad[-1]
    return pd.DataFrame({"high": hoch, "low": tief, "close": schluss})


def test_erst_mitteln_dann_aufloesen():
    """Die Reihenfolge ist der Unterschied zwischen Messung und Rauschen.

    Der eigentliche Fund. `alpha` je Tagespaar auszuwerten und die Ergebnisse
    zu mitteln ergibt bei wahrem Spread 0 einen deutlich positiven Wert; erst
    `beta` und `gamma` zu mitteln ergibt 0.
    """
    df = simuliere(0.0, tage=1500, vol_tag=0.02, seed=3)
    h, lo = np.log(df["high"].to_numpy()), np.log(df["low"].to_numpy())

    tages = (h - lo) ** 2
    beta_paare = tages[:-1] + tages[1:]
    gamma_paare = (np.maximum(h[:-1], h[1:]) - np.minimum(lo[:-1], lo[1:])) ** 2
    nenner = 3.0 - 2.0 * np.sqrt(2.0)

    def spread(beta, gamma):
        alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / nenner - np.sqrt(gamma / nenner)
        return 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))

    je_paar = spread(beta_paare, gamma_paare)
    naiv = float(np.median(np.where(je_paar < 0, 0.0, je_paar))) * 1e4
    richtig = float(spread(beta_paare.mean(), gamma_paare.mean())) * 1e4

    assert naiv > 20.0, f"Der falsche Weg sollte deutlich danebenliegen, war {naiv}"
    assert abs(richtig) < 10.0, f"Der richtige Weg sollte nahe 0 sein, war {richtig}"


def test_corwin_schultz_liegt_zu_tief_und_klemmt_bei_null():
    """CS unterschaetzt systematisch -- deshalb ist es keine Schaetzung."""
    for wahr in (0.0, 15.0):
        s = schaetze("sim", simuliere(wahr, vol_tag=0.02, seed=11), fenster=63)
        assert s.corwin_schultz <= wahr + 5.0, (
            f"CS meldete {s.corwin_schultz} bei wahrem Spread {wahr}"
        )


def _median_ueber_seeds(vol_tag: float, wahr_bps: float, seeds: int = 8) -> float:
    """AR-Schaetzung als Median ueber mehrere Ziehungen.

    Einzelne Ziehungen streuen so stark, dass sie nichts zeigen -- bei
    Seed 5 meldet AR 0,0, bei Seed 7 fuer dieselben Parameter 55. Genau das
    ist der Grund, warum die Methode unbrauchbar ist, und deshalb wird die
    Verzerrung hier ueber Ziehungen gemessen statt an einer.
    """
    werte = [
        schaetze("sim", simuliere(wahr_bps, tage=1200, vol_tag=vol_tag, seed=i),
                 fenster=63).abdi_ranaldo
        for i in range(seeds)
    ]
    return float(np.median(werte))


def test_abdi_ranaldo_liegt_zu_hoch_und_die_verzerrung_waechst_mit_der_vola():
    """AR ueberschaetzt, und zwar umso mehr, je unruhiger der Markt ist.

    Das ist der Grund, warum die Methode fuer Krypto (3-5 % Tagesvol) nichts
    entscheiden kann: bei wahrem Spread 0 meldet sie dort mehr, als ein
    realistischer Spread jemals waere.
    """
    ruhig = _median_ueber_seeds(0.01, 0.0)
    unruhig = _median_ueber_seeds(0.04, 0.0)

    assert unruhig > ruhig + 10.0, f"ruhig={ruhig}, unruhig={unruhig}"
    assert unruhig > 20.0, (
        f"Bei 4 % Tagesvol und Spread 0 sollte AR zweistellig danebenliegen, "
        f"war {unruhig}"
    )


def test_beide_schaetzer_bei_ruhigem_markt_und_grossem_spread_nah_beieinander():
    """Der einzige Fall, in dem die Methode etwas taugt: viel Spread, wenig Vola."""
    s = schaetze("sim", simuliere(60.0, vol_tag=0.005, seed=2), fenster=63)
    assert s.corwin_schultz == pytest.approx(60.0, abs=20.0)
    assert s.abdi_ranaldo == pytest.approx(60.0, abs=20.0)


def test_zu_kurze_reihen_geben_leer_statt_zu_raten():
    leer = pd.DataFrame({"high": [1.0], "low": [1.0], "close": [1.0]})
    assert corwin_schultz(leer["high"], leer["low"]).size == 0
    assert abdi_ranaldo(leer["high"], leer["low"], leer["close"]).size == 0


def test_angebrochener_block_faellt_weg():
    """Ein halber Block rauscht staerker, zaehlt im Median aber voll mit."""
    df = simuliere(0.0, tage=MONAT * 3 + 7, seed=1)
    # 3*21+7 = 70 Zeilen -> 69 Paare -> 3 volle Bloecke zu 21, 6 Paare fallen weg
    assert corwin_schultz(df["high"], df["low"], fenster=MONAT).size == 3


def test_fehlende_spalten_werfen_statt_still_zu_rechnen():
    with pytest.raises(ValueError, match="high/low/close"):
        schaetze("kaputt", pd.DataFrame({"close": [1.0, 2.0, 3.0]}))


def test_fenster_unter_zwei_ist_ein_fehler():
    with pytest.raises(ValueError, match="zu klein"):
        schaetze("sim", simuliere(0.0, tage=100), fenster=1)


# ---------------------------------------------------------------------------
# Der Weg, der funktioniert: das Orderbuch (ADR-070)
# ---------------------------------------------------------------------------


def test_der_effektive_spread_waechst_mit_der_ordergroesse():
    """Der ganze Grund, warum nicht an der Spitze des Buchs gemessen wird.

    Dort steht eine Spanne fuer eine unendlich kleine Order. Wer 25.000 USD
    handelt, isst sich durch mehrere Ebenen und zahlt mehr -- gemessen an
    ALGO-USD am 2026-09-04: 6,9 bps bei 2.560 USD, 21,7 bps bei 25.000.
    """
    from qt.backtest.spread import _vwap

    # Drei Ebenen, jede 1.000 USD tief, je 10 bps schlechter.
    asks = [(100.0, 10.0), (100.1, 10.0), (100.2, 10.0)]

    klein = _vwap(asks, 1_000.0)
    gross = _vwap(asks, 3_000.0)

    assert klein == pytest.approx(100.0)
    assert gross > klein


def test_ein_zu_duennes_buch_liefert_keine_zahl():
    """Ein Buch, das die Ordergroesse nicht hergibt, ist ein Befund.

    Die Alternative waere, den letzten bekannten Preis fortzuschreiben -- das
    ergaebe eine Zahl, die aussieht wie eine Messung und keine ist.
    """
    from qt.backtest.spread import _vwap

    assert _vwap([(100.0, 1.0)], 1_000_000.0) is None
    assert _vwap([], 100.0) is None


def test_die_messung_mittelt_beide_seiten_und_rechnet_gegen_die_mitte():
    """`halb_bps` ist das Gegenstueck zu `CostConfig.half_spread_bps`.

    Kauf und Verkauf getrennt zu berichten ist kein Detail: an einem
    einseitigen Buch weichen sie stark voneinander ab, und ein Mittelwert
    allein verdeckt das.
    """
    from qt.backtest.spread import BuchMessung

    m = BuchMessung(
        symbol="X-USD", notional=1000.0, mitte=100.0, kauf_bps=4.0, verkauf_bps=2.0
    )

    assert m.halb_bps == pytest.approx(3.0)
