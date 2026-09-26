"""Vollstaendigkeit jederzeit, das Urteil erst nach dem letzten Testtag (ADR-081)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import typer.main
from typer.testing import CliRunner

from qt import cli
from qt.meme import registrierung as reg
from qt.meme.auswertung import Kennzahlen, ZuFrueh, auswerten, bootstrap, entscheiden, stand
from qt.meme.sammeln import pfade
from tests.meme_welt import satz

SOL = 10**9


def _schreiben(verzeichnis, tag, saetze, **meta):
    verzeichnis.mkdir(parents=True, exist_ok=True)
    daten, meta_pfad = pfade(tag, verzeichnis)
    daten.write_text("".join(json.dumps(s) + "\n" for s in saetze))
    werte = {
        "tag": tag.isoformat(), "starts_gesamt": 40_000, "stichprobe": len(saetze),
        "fehlerfrei": len(saetze), "versuche": 1, "fehlerarten": {},
        "vollstaendig": True, "fertig": True,
    }
    meta_pfad.write_text(json.dumps(werte | meta))


def _welt(verzeichnis, *, steigen: float, ohne=()):
    """Je Tag 100 Starts mit Signalen 0 bis 9,9 SOL. R1 handelt die fuenf
    staerksten; bei ihnen fliessen bis zum Ausstieg `steigen` SOL nach."""
    for tag in reg.alle_tage():
        if tag in ohne:
            continue
        saetze = []
        for i in range(100):
            b = i * 10**8
            e = b + (steigen if i >= 95 else 0)
            saetze.append(satz(tag, a=0, b=b, e=e, nummer=i))
        _schreiben(verzeichnis, tag, saetze)


# -- kein Blick vor dem Datum -------------------------------------------------------


def test_vor_dem_datum_gibt_es_kein_ergebnis(tmp_path):
    _welt(tmp_path, steigen=20 * SOL)

    with pytest.raises(ZuFrueh):
        auswerten(reg.AUSWERTUNG_AB - timedelta(seconds=1), tmp_path)


def test_die_kommandozeile_verweigert_vor_dem_datum_und_hat_keinen_schalter(monkeypatch):
    class Vorher(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 25, 5, 59, tzinfo=timezone.utc)

    monkeypatch.setattr(cli, "datetime", Vorher)
    ergebnis = CliRunner().invoke(cli.app, ["meme", "auswerten"])

    assert ergebnis.exit_code == 1
    assert "erst ab 2026-10-25 06:00 UTC" in ergebnis.output
    befehl = typer.main.get_command(cli.app).commands["meme"].commands["auswerten"]
    assert [p.name for p in befehl.params] == [], "keine Option, die den Blick vorzieht"


def test_stand_liest_nur_zaehlungen_nie_datensaetze(tmp_path):
    _schreiben(tmp_path, reg.KALIBRIERTAG, [])
    _schreiben(tmp_path, reg.ERSTER_TESTTAG, [], fehlerfrei=470, vollstaendig=False, fertig=False,
               fehlerarten={"ValueError: log_abgeschnitten": 30})
    for tag in (reg.KALIBRIERTAG, reg.ERSTER_TESTTAG):
        # Wuerde `stand` die Datensaetze lesen, scheiterte es hieran.
        pfade(tag, tmp_path)[0].write_text("kein json {")

    text = stand(tmp_path)

    assert "vollstaendig (Kalibrierung)" in text
    assert "in Arbeit" in text and "log_abgeschnitten 30" in text
    assert "Vollstaendige Testtage: 0 von 28" in text
    assert "%" not in text


# -- das Urteil -----------------------------------------------------------------------


def _k(mittel: float, n: int = 500) -> Kennzahlen:
    return Kennzahlen(n, mittel, mittel, 0.5, 0.01)


@pytest.mark.parametrize(
    ("r1", "r0", "intervall", "haelften", "tage", "urteil"),
    [
        (_k(0.10), _k(-0.05), (0.02, 0.2), (0.1, 0.1), 20, reg.UNENTSCHIEDEN),
        (_k(0.10, n=99), _k(-0.05), (0.02, 0.2), (0.1, 0.1), 28, reg.UNENTSCHIEDEN),
        (_k(0.0), _k(-0.05), (-0.1, 0.1), (0.1, -0.1), 28, reg.NEIN),
        (_k(-0.03), _k(-0.05), (-0.05, -0.01), (-0.02, -0.04), 21, reg.NEIN),
        (_k(0.10), _k(-0.05), (0.02, 0.2), (0.1, 0.1), 21, reg.VIELLEICHT),
        (_k(0.10), _k(-0.05), (-0.01, 0.2), (0.1, 0.1), 28, reg.UNENTSCHIEDEN),
        (_k(0.10), _k(0.12), (0.02, 0.2), (0.1, 0.1), 28, reg.UNENTSCHIEDEN),
        (_k(0.10), _k(-0.05), (0.02, 0.2), (0.25, -0.01), 28, reg.UNENTSCHIEDEN),
        (_k(0.10), _k(-0.05), (0.02, 0.2), (0.1, float("nan")), 28, reg.UNENTSCHIEDEN),
    ],
    ids=[
        "zu-wenige-tage", "zu-wenige-handel", "null-ist-nein", "negativ-ist-nein", "vielleicht",
        "intervall-ueber-null", "nicht-besser-als-r0", "eine-haelfte-negativ", "leere-haelfte",
    ],
)
def test_entscheidungstabelle(r1, r0, intervall, haelften, tage, urteil):
    assert entscheiden(r1, r0, intervall, haelften, tage)[0] == urteil


def test_vielleicht_sagt_dass_es_kein_ja_ist():
    _, gruende = entscheiden(_k(0.10), _k(-0.05), (0.02, 0.2), (0.1, 0.1), 28)
    assert any("kein Ja" in g for g in gruende)


def test_bootstrap_zieht_ganze_tage_und_ist_reproduzierbar():
    tage = reg.testtage()[:6]
    gleich = {t: [0.1, 0.1, 0.1] for t in tage}
    verschieden = {t: [0.1 * i] * (i + 1) for i, t in enumerate(tage)}

    assert bootstrap(gleich, tage) == pytest.approx((0.1, 0.1))
    assert bootstrap(verschieden, tage) == bootstrap(verschieden, tage)
    lo, hi = bootstrap(verschieden, tage)
    assert 0 < lo < hi < 0.5


# -- von Anfang bis Ende --------------------------------------------------------------


def test_ende_zu_ende_vielleicht(tmp_path):
    fehlen = reg.testtage()[3:9]
    _welt(tmp_path, steigen=20 * SOL, ohne=fehlen)

    bericht = auswerten(reg.AUSWERTUNG_AB, tmp_path)

    assert bericht.urteil == reg.VIELLEICHT, bericht.text()
    assert len(bericht.testtage) == 22
    assert bericht.fehlende_tage == fehlen
    r1 = bericht.kennzahlen[("R1", reg.PRIMAER)]
    r0 = bericht.kennzahlen[("R0", reg.PRIMAER)]
    assert r1.n == 22 * 5 and r0.n == 22 * 100
    assert r1.mittel > r0.mittel
    assert bericht.intervall[0] > 0
    assert "VIELLEICHT" in bericht.text()


def test_ende_zu_ende_nein(tmp_path):
    _welt(tmp_path, steigen=0)

    bericht = auswerten(reg.AUSWERTUNG_AB, tmp_path)

    assert bericht.urteil == reg.NEIN
    assert bericht.kennzahlen[("R1", reg.PRIMAER)].mittel < 0
    assert bericht.intervall[1] < 0


def test_ende_zu_ende_zu_wenige_tage(tmp_path):
    _welt(tmp_path, steigen=20 * SOL, ohne=reg.testtage()[:8])

    assert auswerten(reg.AUSWERTUNG_AB, tmp_path).urteil == reg.UNENTSCHIEDEN


def test_ohne_kalibriertag_zaehlt_der_erste_testtag_nicht(tmp_path):
    """Er haette keine R1-Schwelle; er liefert sie aber fuer den zweiten."""
    _welt(tmp_path, steigen=20 * SOL, ohne=[reg.KALIBRIERTAG])

    bericht = auswerten(reg.AUSWERTUNG_AB, tmp_path)

    assert bericht.fehlende_tage == [reg.ERSTER_TESTTAG]
    assert bericht.testtage == reg.testtage()[1:]
    assert bericht.kennzahlen[("R1", reg.PRIMAER)].n == 27 * 5
