"""Der Sammler gegen eine nachgebaute Chain (ADR-081)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from qt.meme import registrierung as reg
from qt.meme import sammeln
from qt.meme.sammeln import (
    KEIN_START,
    NICHT_SOL,
    datensatz,
    lies_tag,
    offene_tage,
    reihenfolge,
    starts_des_tages,
    tag_sammeln,
)
from tests.meme_welt import FakeRpc, Welt, pubkey

TAG = date(2026, 9, 27)
BEGINN, ENDE = reg.tagesgrenzen(TAG)


def test_starts_des_tages_nur_dieser_tag_nur_erfolgreiche(monkeypatch):
    welt = Welt()
    welt.start("gestern", BEGINN - 10)
    heute = [welt.start(f"t{i}", BEGINN + 100 * i)[2] for i in range(5)]
    welt.sonstiges(reg.MINT_AUTORITAET, BEGINN + 50, fehler={"InstructionError": [0, "x"]})
    welt.start("morgen", ENDE + 10)

    starts = starts_des_tages(FakeRpc(welt), TAG)

    assert sorted(s[0] for s in starts) == sorted(heute)


def test_seitenweise_bis_vor_den_tagesbeginn(monkeypatch):
    welt = Welt()
    welt.start("vorher", BEGINN - 1)
    for i in range(25):
        welt.start(f"t{i}", BEGINN + i)

    original = sammeln.MAX_SEITEN_STARTS
    rpc = FakeRpc(welt)
    # Seiten zu je 1000 sind im Test zu gross; die Logik muss auch mit
    # kleinen Seiten weiterblaettern.
    monkeypatch.setattr(sammeln, "MAX_SEITEN_STARTS", original)
    starts = starts_des_tages(rpc, TAG)
    assert len(starts) == 25


def test_die_reihenfolge_waehlt_niemand():
    starts = [(pubkey(f"s{i}"), BEGINN + i, 1000 + i) for i in range(50)]

    a = reihenfolge(starts, TAG)
    b = reihenfolge(list(reversed(starts)), TAG)
    c = reihenfolge(starts, date(2026, 9, 28))

    assert a == b, "gleiche Grundgesamtheit, gleiche Reihenfolge -- egal woher die Liste kam"
    assert sorted(a) == sorted(starts)
    assert a != c


def _token_mit_verlauf(welt: Welt, t0: int):
    mint, kurve, sig = welt.start("verlauf", t0)
    welt.handel(mint, kurve, t0 + 30, 900_000_000)
    welt.handel(mint, kurve, t0 + 61, 2_000_000_000)
    welt.sonstiges(kurve, t0 + 62)  # Kontenpflege ohne Ereignis
    welt.handel(mint, kurve, t0 + 100, 1_500_000_000, kauf=False)
    welt.handel(mint, kurve, t0 + 5000, 9_000_000_000)
    return mint, kurve, sig


def test_die_kurvenstaende_zu_den_registrierten_zeitpunkten():
    welt = Welt()
    t0 = BEGINN + 1000
    _, _, sig = _token_mit_verlauf(welt, t0)

    satz = datensatz(FakeRpc(welt), sig, 1, TAG)

    assert satz["fehler"] is None
    assert satz["a"]["rsol"] == 500_000_000, "nach dem Start: Kauf des Erstellers"
    assert satz["b"]["rsol"] == 900_000_000 and satz["b"]["zeit"] == t0 + 30
    assert satz["c"]["rsol"] == 2_000_000_000 and satz["c"]["zeit"] == t0 + 61
    assert satz["e"]["rsol"] == 1_500_000_000 and satz["e"]["zeit"] == t0 + 100
    assert satz["tx_bis_signal"] == 1
    assert satz["tx_bis_ende"] == 4


def test_ohne_handel_bleibt_der_stand_nach_dem_start():
    welt = Welt()
    _, _, sig = welt.start("tot", BEGINN + 5)

    satz = datensatz(FakeRpc(welt), sig, 1, TAG)

    assert satz["fehler"] is None
    assert satz["a"] == satz["b"] == satz["c"] == satz["e"]


def test_nicht_zur_grundgesamtheit():
    welt = Welt()
    _, _, usdc = welt.start("usdc", BEGINN + 5, quote_mint=pubkey("usdc"))
    fremd = welt.sonstiges(reg.MINT_AUTORITAET, BEGINN + 6)

    rpc = FakeRpc(welt)
    assert datensatz(rpc, usdc, 1, TAG) == NICHT_SOL
    assert datensatz(rpc, fremd, 1, TAG) == KEIN_START


def test_mayhem_agent_zahlt_keine_gebuehr_wir_schon():
    """Ist der Handel, der den Stand bestimmt, einer des gebuehrenfreien
    Agenten, gilt fuer uns die Gebuehr des letzten bezahlten Handels."""
    welt = Welt()
    t0 = BEGINN + 10
    mint, kurve, sig = welt.start("mayhem", t0, mayhem=True)
    welt.handel(mint, kurve, t0 + 20, 800_000_000, fee_bps=80, creator_fee_bps=20)
    welt.handel(mint, kurve, t0 + 40, 700_000_000, fee_bps=0, creator_fee_bps=0, basis=31_000_000_000)

    satz = datensatz(FakeRpc(welt), sig, 1, TAG)

    assert satz["fehler"] is None and satz["mayhem"] is True
    assert satz["b"]["zeit"] == t0 + 40, "der Stand ist der nach dem Agenten"
    assert (satz["b"]["fee_bps"], satz["b"]["creator_fee_bps"]) == (80, 20)


def test_ohne_jede_bezahlte_gebuehr_bleibt_sie_unbekannt():
    welt = Welt()
    _, _, sig = welt.start("ohne", BEGINN + 5, devkauf=None)

    satz = datensatz(FakeRpc(welt), sig, 1, TAG)

    assert satz["a"]["fee_bps"] == -1, "die Simulation nimmt dann die uebliche Gebuehr des Tages"


def test_bei_normalen_coins_wird_das_produkt_geprueft():
    welt = Welt()
    t0 = BEGINN + 10
    mint, kurve, sig = welt.start("kaputt", t0)
    welt.handel(mint, kurve, t0 + 20, 800_000_000, basis=40_000_000_000)

    satz = datensatz(FakeRpc(welt), sig, 1, TAG)

    assert "kurve_kein_konstantes_produkt" in satz["fehler"]


def test_ein_ganzer_tag_und_die_wiederholung(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "STICHPROBE_JE_TAG", 4)
    welt = Welt()
    welt.start("vorher", BEGINN - 1)
    for i in range(6):
        welt.start(f"t{i}", BEGINN + 60 * i)
    welt.start("usdc", BEGINN + 999, quote_mint=pubkey("usdc"))
    rpc = FakeRpc(welt)

    meta = tag_sammeln(rpc, TAG, tmp_path)
    _, saetze = lies_tag(TAG, tmp_path)

    assert meta["vollstaendig"] and meta["fertig"]
    assert meta["stichprobe"] == 4 == len(saetze)
    assert meta["starts_gesamt"] == 7
    aufrufe = rpc.aufrufe
    assert tag_sammeln(rpc, TAG, tmp_path) == meta
    assert rpc.aufrufe == aufrufe, "ein fertiger Tag wird nicht noch einmal abgefragt"


def test_fehlerhafte_saetze_werden_erneut_versucht_nicht_ersetzt(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "STICHPROBE_JE_TAG", 3)
    welt = Welt()
    welt.start("vorher", BEGINN - 1)
    sigs = [welt.start(f"t{i}", BEGINN + 60 * i)[2] for i in range(3)]
    fehlend = welt.transaktionen.pop(sigs[1])

    erste = tag_sammeln(FakeRpc(welt), TAG, tmp_path)
    assert not erste["vollstaendig"] and not erste["fertig"]
    assert erste["stichprobe"] == 3, "der Fehler wird verbucht, nicht durch einen anderen Start ersetzt"

    welt.transaktionen[sigs[1]] = fehlend
    zweite = tag_sammeln(FakeRpc(welt), TAG, tmp_path)
    assert zweite["vollstaendig"] and zweite["versuche"] == 2


def test_offene_tage(tmp_path):
    jetzt = datetime(2026, 9, 28, 3, 0, tzinfo=timezone.utc)

    assert offene_tage(jetzt, tmp_path) == [reg.KALIBRIERTAG, TAG]
    with pytest.raises(StopIteration):
        next(t for t in offene_tage(jetzt, tmp_path) if t > TAG)


def test_ein_abgebrochener_lauf_macht_beim_zwischenstand_weiter(tmp_path, monkeypatch):
    """Der Workflow hat ein Zeitlimit; ein Abbruch darf nicht alles kosten."""
    monkeypatch.setattr(reg, "STICHPROBE_JE_TAG", 5)
    monkeypatch.setattr(sammeln, "ZWISCHENSTAND_ALLE", 2)
    welt = Welt()
    welt.start("vorher", BEGINN - 1)
    for i in range(5):
        welt.start(f"t{i}", BEGINN + 60 * i)

    class Abbruch(Exception):
        pass

    echt = sammeln.datensatz
    aufrufe = []

    def bricht_ab(*args, **kw):
        if len(aufrufe) == 3:
            raise Abbruch
        aufrufe.append(args[1])
        return echt(*args, **kw)

    monkeypatch.setattr(sammeln, "datensatz", bricht_ab)
    with pytest.raises(Abbruch):
        tag_sammeln(FakeRpc(welt), TAG, tmp_path)

    zwischen, saetze = lies_tag(TAG, tmp_path)
    assert len(saetze) == 2 and not zwischen["fertig"] and not zwischen["vollstaendig"]

    aufrufe.clear()
    monkeypatch.setattr(sammeln, "datensatz", lambda *a, **k: (aufrufe.append(a[1]), echt(*a, **k))[1])
    meta = tag_sammeln(FakeRpc(welt), TAG, tmp_path)

    assert meta["vollstaendig"] and meta["versuche"] == 2
    assert len(aufrufe) == 3, "die zwei gesicherten Saetze werden nicht noch einmal abgefragt"
    assert not list(tmp_path.glob("*.tmp"))
