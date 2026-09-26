"""Die Handelsrechnung des Papiertests, nur auf synthetischen Daten (ADR-081).

Auf echten Testtagen laeuft sie erst nach dem 2026-10-25.
"""

from __future__ import annotations

import numpy as np
import pytest

from qt.meme import registrierung as reg
from qt.meme.papier import (
    handeln,
    rsol_bei_abschluss,
    schwelle,
    signal,
    simulieren,
    uebliche_gebuehr,
)
from qt.meme.pumpfun import Zustand, kaufen, pool_verkauf
from tests.meme_welt import RTOK0, SUPPLY, VTOK0, kurvenstand, satz

TAG = reg.ERSTER_TESTTAG
GUENSTIG, PRIMAER, STRENG = reg.SZENARIEN
GEBUEHR = (95, 30, 0)
EINSATZ = reg.EINSATZ_SOL * 1e9
NETZ = 2 * reg.NETZWERK_SOL_JE_TX * 1e9
SOL = 10**9


def test_szenarien_in_der_registrierten_reihenfolge():
    assert [s.name for s in reg.SZENARIEN] == ["guenstig", "primaer", "streng"]


def test_ein_toter_token_kostet_genau_gebuehren_und_netz():
    """Nichts passiert zwischen Ein- und Ausstieg: verloren sind die Gebuehr
    beider Wege und die zwei Transaktionen, sonst nichts."""
    rendite, art = handeln(satz(TAG), GUENSTIG, GEBUEHR)

    g = 0.0125
    assert art == "normal"
    assert rendite == pytest.approx((1 - g) / (1 + g) - 1 - NETZ / EINSATZ, rel=1e-9)


def test_steigt_die_kurve_bis_zum_ausstieg_verdient_man():
    rendite, art = handeln(satz(TAG, e=10 * SOL), PRIMAER, GEBUEHR)

    assert art == "normal"
    assert rendite > 0


def test_die_abschlaege_machen_es_nur_schlechter():
    for s in (satz(TAG), satz(TAG, e=10 * SOL), satz(TAG, e_voll=True)):
        guenstig, primaer, streng = (handeln(s, sz, GEBUEHR)[0] for sz in reg.SZENARIEN)
        assert guenstig > primaer > streng


def test_graduiert_der_token_bis_zum_ausstieg_wird_im_pool_verkauft():
    s = satz(TAG, e_voll=True)
    rendite, art = handeln(s, PRIMAER, GEBUEHR)

    einstieg = Zustand.aus_dict(s["c"])
    tokens = kaufen(einstieg, EINSATZ).tokens * (1 - PRIMAER.abschlag)
    pool_sol = rsol_bei_abschluss(Zustand.aus_dict(s["e"]), s["k0"])
    brutto = pool_verkauf(pool_sol, SUPPLY - RTOK0, tokens)
    erloes = brutto * (1 - reg.POOL_GEBUEHR) * (1 - PRIMAER.graduierung)

    assert art == "graduiert"
    assert pool_sol == pytest.approx(85.005 * SOL, rel=1e-4), "rund 85 SOL bei jeder normalen Kurve"
    assert rendite == pytest.approx((erloes - NETZ - EINSATZ) / EINSATZ, rel=1e-12)


def test_mit_unserem_kauf_waere_die_kurve_vor_dem_ausstieg_voll_gewesen():
    """Beim Ausstieg sind weniger Tokens uebrig, als wir gekauft haetten."""
    s = satz(TAG, e=84.9 * SOL)
    e = Zustand.aus_dict(s["e"])
    assert not e.voll
    assert e.rtok < kaufen(Zustand.aus_dict(s["c"]), EINSATZ).tokens

    _, art = handeln(s, PRIMAER, GEBUEHR)
    assert art == "graduiert"


def test_macht_unser_kauf_die_kurve_voll_kommt_der_rest_zurueck():
    s = satz(TAG, b=84.9 * SOL)
    kauf = kaufen(Zustand.aus_dict(s["c"]), EINSATZ)

    assert kauf.macht_voll and kauf.rest > 0
    rendite, art = handeln(s, PRIMAER, GEBUEHR)
    assert art == "graduiert"
    assert rendite > -1


def test_ist_die_kurve_beim_einstieg_schon_voll_gibt_es_keinen_handel():
    assert handeln(satz(TAG, c_voll=True), PRIMAER, GEBUEHR) is None


def test_mayhem_der_abschluss_liegt_auf_der_kurve_des_stands():
    """Der Agent verschiebt die virtuelle SOL-Basis; mit ihr das SOL, das bei
    der Graduierung in den Pool geht."""
    k0 = satz(TAG)["k0"]
    normal = Zustand.aus_dict(kurvenstand(5 * SOL))
    verschoben = Zustand.aus_dict(kurvenstand(5 * SOL, basis=31 * SOL))

    assert rsol_bei_abschluss(normal, k0) == pytest.approx(85.005 * SOL, rel=1e-4)
    faktor = VTOK0 / (VTOK0 - RTOK0) - 1
    assert rsol_bei_abschluss(verschoben, k0) == pytest.approx(31 * SOL * faktor, rel=1e-6)


def test_unbekannte_gebuehr_wird_die_uebliche_des_tages_nie_null():
    """Ohne Erstellerkauf und nur mit dem Mayhem-Agenten steht -1 im Satz.
    Unersetzt ergaebe das eine negative Gebuehr."""
    tag = [satz(TAG, nummer=i) for i in range(3)] + [satz(TAG, gebuehr=(80, 20, 0), nummer=3)]
    unbekannt = satz(TAG, gebuehr=(-1, -1, 0), nummer=4)

    assert uebliche_gebuehr(tag + [unbekannt]) == GEBUEHR
    assert handeln(unbekannt, GUENSTIG, GEBUEHR) == handeln(tag[0], GUENSTIG, GEBUEHR)


def test_signal_ist_der_zufluss_der_ersten_minute_ohne_den_start():
    assert signal(satz(TAG, a=0.5 * SOL, b=3 * SOL)) == 2.5 * SOL


def test_schwelle_ist_das_95_prozent_quantil_des_vortags_ohne_fehler():
    vortag = [satz(TAG, a=0, b=i * 10**8, nummer=i) for i in range(100)]
    mit_fehler = vortag + [satz(TAG, a=0, b=80 * SOL, fehler="rpc: x", nummer=100)]

    assert schwelle(vortag) == pytest.approx(np.quantile(np.arange(100), 0.95) * 10**8)
    assert schwelle(mit_fehler) == schwelle(vortag)
    with pytest.raises(ValueError):
        schwelle([satz(TAG, fehler="x")])


def _tag(tag, signale, **kw):
    return [satz(tag, a=0, b=s * 10**8, nummer=i, **kw) for i, s in enumerate(signale)]


def test_simulation_r1_nur_ueber_der_schwelle_des_juengsten_vortags():
    kal = reg.KALIBRIERTAG
    t1, t2, t3 = reg.testtage()[:3]
    tage = {
        kal: _tag(kal, range(100)),  # Schwelle fuer t1: 94,05
        t1: _tag(t1, range(90, 100)),  # Schwelle fuer t3 (t2 fehlt): 98,55
        t3: _tag(t3, range(90, 100)),
    }

    handel, nicht_handelbar = simulieren(tage, [t1, t2, t3])

    def zahl(tag, regel, sz=reg.PRIMAER):
        return sum(1 for h in handel if h.tag == tag and h.regel == regel and h.szenario == sz)

    assert (zahl(t1, "R0"), zahl(t1, "R1")) == (10, 5)
    assert (zahl(t3, "R0"), zahl(t3, "R1")) == (10, 1)
    assert zahl(t2, "R0") == 0
    assert all(h.tag != kal for h in handel), "der Kalibriertag wird nicht gehandelt"
    assert {h.szenario for h in handel} == {"guenstig", "primaer", "streng"}
    assert nicht_handelbar == {}


def test_simulation_ohne_fehlerhafte_und_mit_voller_kurve_als_nicht_handelbar():
    kal, t1 = reg.KALIBRIERTAG, reg.ERSTER_TESTTAG
    tage = {
        kal: _tag(kal, range(100)),
        t1: _tag(t1, range(10)) + [
            satz(t1, a=0, b=90 * SOL, fehler="ValueError: log_abgeschnitten", nummer=10),
            satz(t1, a=0, c_voll=True, nummer=11),
        ],
    }
    tage[t1][-1]["b"] = kurvenstand(voll=True)  # starkes Signal, dann voll

    handel, nicht_handelbar = simulieren(tage, [t1])

    primaer = [h for h in handel if h.szenario == reg.PRIMAER]
    assert sum(1 for h in primaer if h.regel == "R0") == 10
    assert sum(1 for h in primaer if h.regel == "R1") == 0
    assert nicht_handelbar == {"R0": 1, "R1": 1}
