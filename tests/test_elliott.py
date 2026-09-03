"""Tests fuer die Elliott-Wellen-Strategie.

Der wichtigste Test ist `test_zukuenftige_bars_aendern_keine_vergangene_zaehlung`.
Pivot-Erkennung ist die klassische Stelle, an der ein Backtest unbemerkt in
die Zukunft schaut: ein Swing-Hoch sieht man am fertigen Chart sofort, aber
zum Entscheidungszeitpunkt ist es noch keins. Wer beim Zaehlen bis zum
letzten Bar laeuft, baut sich eine Strategie, die im Backtest glaenzt und
live nicht reproduzierbar ist.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from qt.core.clock import BacktestClock
from qt.features.registry import FeatureStore
from qt.strategy.library.elliott import (
    ElliottWave,
    Pivot,
    confirmed_pivots,
    impulse_complete,
    wave_two_setup,
)
from tests.conftest import make_bars


# --------------------------------------------------------------------------
# Pivot-Bestaetigung
# --------------------------------------------------------------------------


def test_ein_pivot_wird_erst_nach_confirm_bars_sichtbar():
    """Vor der Bestaetigung gibt es dort keinen Pivot -- das ist der Punkt.

    Der Preis dafuer ist Reaktionszeit, der Gegenwert ist eine Zaehlung, die
    zum Entscheidungszeitpunkt tatsaechlich moeglich war.
    """
    # Ein klares Hoch bei Index 5, danach faellt es.
    highs = np.array([10, 11, 12, 13, 14, 20, 14, 13, 12, 11, 10], dtype=float)
    lows = highs - 1.0

    # Nur bis Index 7 bekannt: das Hoch bei 5 hat erst zwei Bars Abstand.
    fruehe = confirmed_pivots(highs[:8], lows[:8], confirm_bars=3, min_move=0.5)
    assert all(p.index != 5 for p in fruehe), "Pivot vor der Bestaetigung sichtbar"

    # Mit drei Bars Abstand ist es bestaetigt.
    spaete = confirmed_pivots(highs[:9], lows[:9], confirm_bars=3, min_move=0.5)
    assert any(p.index == 5 and p.is_high for p in spaete)


def test_die_letzten_confirm_bars_sind_nie_pivots():
    """Die Schleife darf nicht bis zum letzten Bar laufen.

    Genau dort schummelt jede ZigZag-Implementierung: der juengste Pivot
    benutzt sonst Bars, die es zum Entscheidungszeitpunkt noch nicht gab.
    """
    rng = np.random.default_rng(7)
    closes = 100 + np.cumsum(rng.normal(0, 1, 200))
    highs, lows = closes + 0.5, closes - 0.5

    confirm = 6
    pivots = confirmed_pivots(highs, lows, confirm_bars=confirm, min_move=0.1)
    grenze = len(highs) - confirm
    assert pivots, "der Test braucht ueberhaupt Pivots, sonst prueft er nichts"
    assert max(p.index for p in pivots) < grenze


def test_zukuenftige_bars_aendern_keine_vergangene_zaehlung():
    """Der eigentliche Lookahead-Test: die Vergangenheit bleibt bitgleich.

    Wird die Historie verlaengert, duerfen die bereits bestaetigten Pivots
    sich nicht veraendern -- weder in der Zahl noch im Preis noch im Index.
    """
    rng = np.random.default_rng(11)
    closes = 100 + np.cumsum(rng.normal(0, 1.5, 400))
    highs, lows = closes + 0.8, closes - 0.8

    confirm = 5
    kurz = confirmed_pivots(highs[:250], lows[:250], confirm, min_move=1.0)
    lang = confirmed_pivots(highs, lows, confirm, min_move=1.0)

    # Alles, was im kurzen Fenster sicher bestaetigt war, muss im langen
    # identisch wieder auftauchen.
    sicher = [p for p in kurz if p.index < 250 - 2 * confirm]
    assert sicher, "der Test braucht bestaetigte Pivots"
    lang_nach_index = {p.index: p for p in lang}
    for pivot in sicher:
        assert pivot.index in lang_nach_index, f"Pivot {pivot.index} verschwunden"
        spaeter = lang_nach_index[pivot.index]
        assert spaeter.price == pivot.price
        assert spaeter.is_high == pivot.is_high


def test_rauschpivots_werden_gefiltert():
    """Ohne Filter zerfaellt jede Seitwaertsphase in Miniwellen.

    Das ist der klassische Vorwurf gegen die Methode -- sie findet ueberall
    Muster. Hier soll er wenigstens nicht noch verstaerkt werden.
    """
    rng = np.random.default_rng(3)
    closes = 100 + rng.normal(0, 0.2, 300)  # reines Rauschen, kein Trend
    highs, lows = closes + 0.1, closes - 0.1

    fein = confirmed_pivots(highs, lows, confirm_bars=3, min_move=0.0)
    grob = confirmed_pivots(highs, lows, confirm_bars=3, min_move=1.0)
    assert len(grob) < len(fein)


def test_pivots_wechseln_sich_immer_ab():
    """Eine Wellenzaehlung braucht abwechselnde Extrema.

    Zwei Hochs hintereinander waeren kein Zickzack -- sie werden zum
    hoeheren zusammengefasst.
    """
    rng = np.random.default_rng(5)
    closes = 100 + np.cumsum(rng.normal(0, 1, 500))
    pivots = confirmed_pivots(closes + 0.5, closes - 0.5, 4, min_move=0.8)
    assert len(pivots) >= 4
    for links, rechts in zip(pivots, pivots[1:]):
        assert links.is_high != rechts.is_high


# --------------------------------------------------------------------------
# Die drei harten Regeln
# --------------------------------------------------------------------------


def _impuls(p0, p1, p2, p3, p4, p5) -> list[Pivot]:
    """Sechs Pivots einer Aufwaertszaehlung: Tief, Hoch, Tief, Hoch, Tief, Hoch."""
    preise = (p0, p1, p2, p3, p4, p5)
    return [
        Pivot(i * 10, float(preis), is_high=bool(i % 2))
        for i, preis in enumerate(preise)
    ]


def test_gueltiger_impuls_wird_erkannt():
    # W1 100->120, W2 zurueck auf 110, W3 auf 160 (laengste), W4 auf 140
    # (ueber W1-Hoch), W5 auf 170.
    assert impulse_complete(_impuls(100, 120, 110, 160, 140, 170), direction=1)


def test_regel_1_welle_zwei_ueber_den_start_hinaus_wird_abgelehnt():
    """W2 faellt unter den Anfang von W1 -- die Zaehlung ist ungueltig."""
    assert not impulse_complete(_impuls(100, 120, 99, 160, 140, 170), direction=1)


def test_regel_2_kuerzeste_dritte_welle_wird_abgelehnt():
    """W3 (10) ist kuerzer als W1 (20) und W5 (30) -- verboten."""
    assert not impulse_complete(_impuls(100, 120, 110, 120, 115, 145), direction=1)


def test_regel_3_ueberlappung_von_welle_vier_wird_abgelehnt():
    """W4 faellt unter das Hoch von W1 -- Ueberlappung, also kein Impuls."""
    assert not impulse_complete(_impuls(100, 120, 110, 160, 119, 170), direction=1)


def test_falsche_richtung_wird_abgelehnt():
    aufwaerts = _impuls(100, 120, 110, 160, 140, 170)
    assert not impulse_complete(aufwaerts, direction=-1)


def test_zu_wenige_pivots_sind_kein_impuls():
    assert not impulse_complete(_impuls(100, 120, 110, 160, 140, 170)[:5], direction=1)


# --------------------------------------------------------------------------
# Das Einstiegssetup
# --------------------------------------------------------------------------


def _drei(p0, p1, p2, aufwaerts=True) -> list[Pivot]:
    art = [not aufwaerts, aufwaerts, not aufwaerts]
    return [Pivot(i * 10, float(p), art[i]) for i, p in enumerate((p0, p1, p2))]


def test_setup_liefert_die_ungueltigkeitsmarke_von_regel_1():
    """Der Ausstieg ist der Anfang von Welle 1 -- die Theorie liefert ihn mit.

    Das ist der eigentliche Grund, warum diese Strategie handelbar ist: die
    Widerlegung ist ein Preis, keine Meinung.
    """
    setup = wave_two_setup(_drei(100, 120, 110), fib_min=0.382, fib_max=0.786)
    assert setup is not None
    richtung, ungueltig_ab, welle_eins = setup
    assert richtung == 1
    assert ungueltig_ab == 100.0
    assert welle_eins == 20.0


def test_zu_flacher_rueckzug_ist_keine_zweite_welle():
    """20% Rueckzug: der Impuls laeuft vermutlich noch."""
    assert wave_two_setup(_drei(100, 120, 116), 0.382, 0.786) is None


def test_zu_tiefer_rueckzug_ist_keine_zweite_welle():
    """95% Rueckzug: Regel 1 steht kurz vor dem Bruch, kein Abstand mehr."""
    assert wave_two_setup(_drei(100, 120, 101), 0.382, 0.786) is None


def test_rueckzug_ueber_den_start_hinaus_bricht_regel_1():
    assert wave_two_setup(_drei(100, 120, 99), 0.382, 0.786) is None


def test_abwaertssetup_wird_erkannt():
    setup = wave_two_setup(_drei(120, 100, 110, aufwaerts=False), 0.382, 0.786)
    assert setup is not None
    assert setup[0] == -1
    assert setup[1] == 120.0


def test_nicht_abwechselnde_pivots_ergeben_kein_setup():
    kaputt = [Pivot(0, 100, False), Pivot(10, 120, False), Pivot(20, 110, False)]
    assert wave_two_setup(kaputt, 0.382, 0.786) is None


# --------------------------------------------------------------------------
# Die Strategie im Lauf
# --------------------------------------------------------------------------


def _lauf(closes: list[float], **params) -> list[float]:
    """Die Strategie ueber eine Kursreihe laufen lassen, Gewichte zurueck."""
    prices = np.asarray(closes, dtype=float)
    bars = make_bars(len(prices), "BTC/USD", "4h", prices=prices)
    clock = BacktestClock(bars[0].ts)
    store = FeatureStore(clock, maxlen=2000)
    strategy = ElliottWave(["BTC/USD"], "4h", **params)

    gewichte = []
    for bar in bars:
        clock.advance(bar.close_ts)
        store.on_bar(bar)
        gewichte.append(strategy.on_bar("BTC/USD", store))
    return gewichte


def test_vor_dem_warmup_gibt_es_keine_meinung():
    """`nan` heisst "keine Meinung" und ist etwas anderes als 0.0."""
    gewichte = _lauf([100.0 + i * 0.1 for i in range(30)])
    assert all(math.isnan(w) for w in gewichte)


def test_die_strategie_laeuft_ueber_echte_bars_ohne_zu_werfen():
    rng = np.random.default_rng(23)
    closes = list(100 + np.cumsum(rng.normal(0, 1.5, 600)))
    gewichte = _lauf(closes)

    gueltig = [w for w in gewichte if not math.isnan(w)]
    assert gueltig, "nach dem Warmup muss es Entscheidungen geben"
    assert all(-1.0 <= w <= 1.0 for w in gueltig)


def test_zwei_identische_laeufe_ergeben_identische_gewichte():
    rng = np.random.default_rng(31)
    closes = list(100 + np.cumsum(rng.normal(0, 1.2, 500)))
    erste = _lauf(closes)
    zweite = _lauf(closes)
    assert [repr(w) for w in erste] == [repr(w) for w in zweite]


def test_ohne_shorts_gibt_es_nie_ein_negatives_gewicht():
    rng = np.random.default_rng(41)
    closes = list(100 + np.cumsum(rng.normal(-0.1, 1.5, 700)))
    gewichte = _lauf(closes, allow_short=False)
    assert all(w >= 0 for w in gewichte if not math.isnan(w))


@pytest.mark.slow
def test_die_zaehlung_wird_nie_umgedeutet_sondern_geschlossen():
    """Bricht Regel 1, geht die Position auf null -- nicht in die Gegenrichtung.

    Eine Zaehlung, die nach ihrer Widerlegung zu einer anderen Zaehlung
    umgedeutet werden darf, kann nicht falsch sein. Genau das soll hier nicht
    passieren: der Bruch beendet die Position, ein neuer Einstieg braucht ein
    neues Setup.
    """
    rng = np.random.default_rng(53)
    closes = list(100 + np.cumsum(rng.normal(0, 2.0, 900)))
    gewichte = [w for w in _lauf(closes) if not math.isnan(w)]

    # Kein direkter Sprung von +1 auf -1 oder umgekehrt: dazwischen liegt
    # immer mindestens ein Bar mit Gewicht 0.
    for links, rechts in zip(gewichte, gewichte[1:]):
        assert not (links > 0 and rechts < 0), "Sprung von long direkt auf short"
        assert not (links < 0 and rechts > 0), "Sprung von short direkt auf long"
