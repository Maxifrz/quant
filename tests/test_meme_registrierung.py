"""Die Registrierung des Memecoin-Papiertests steht fest (ADR-081).

Wer eine dieser Zahlen aendert, muss auch diesen Test aendern -- zwei Diffs,
wie bei den Gate-Schwellen (ADR-057). Nach dem ersten Testtag ist jede
Aenderung ein Bruch der Registrierung und gehoert als solcher ins ADR.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from qt.meme import registrierung as r


def test_zeitraum_steht_fest():
    assert r.KALIBRIERTAG == date(2026, 9, 26)
    assert r.ERSTER_TESTTAG == date(2026, 9, 27)
    assert r.LETZTER_TESTTAG == date(2026, 10, 24)
    assert r.AUSWERTUNG_AB == datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)
    assert len(r.testtage()) == 28
    assert r.alle_tage()[0] == r.KALIBRIERTAG


def test_stichprobe_und_zeitplan_stehen_fest():
    assert r.STICHPROBE_JE_TAG == 500
    assert r.MIN_VOLLSTAENDIG == 0.95
    assert r.MIN_TESTTAGE == 21
    assert (r.SIGNAL_NACH, r.LATENZ, r.HALTEDAUER) == (60, 2, 1800)


def test_kosten_und_szenarien_stehen_fest():
    assert r.EINSATZ_SOL == 0.25
    assert r.NETZWERK_SOL_JE_TX == 0.0002
    assert r.POOL_GEBUEHR == 0.01
    assert [(s.name, s.abschlag, s.graduierung) for s in r.SZENARIEN] == [
        ("guenstig", 0.0, 0.0),
        ("primaer", 0.01, 0.05),
        ("streng", 0.03, 0.15),
    ]
    assert r.PRIMAER == "primaer"


def test_regeln_und_entscheidung_stehen_fest():
    assert r.REGELN == ("R0", "R1")
    assert r.R1_QUANTIL == 0.95
    assert r.MIN_HANDEL_R1 == 100
    assert (r.BOOTSTRAP_ZIEHUNGEN, r.BOOTSTRAP_SAAT) == (10_000, 81)
    assert r.INTERVALL == (0.025, 0.975)


def test_die_saat_waehlt_niemand_und_jeder_kann_sie_nachrechnen():
    assert r.saat(date(2026, 9, 27)) == r.saat(date(2026, 9, 27))
    assert r.saat(date(2026, 9, 27)) != r.saat(date(2026, 9, 28))
    assert r.saat(r.KALIBRIERTAG) == 8356477896857729915


def test_ein_tag_ist_erst_nach_dem_letzten_ausstieg_faellig():
    tag = date(2026, 9, 27)
    assert not r.faellig(tag, datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc))
    assert r.faellig(tag, datetime(2026, 9, 28, 1, 32, tzinfo=timezone.utc))


def test_tagesgrenzen_sind_utc():
    beginn, ende = r.tagesgrenzen(date(2026, 9, 27))
    assert ende - beginn == 86_400
    assert datetime.fromtimestamp(beginn, tz=timezone.utc).hour == 0
