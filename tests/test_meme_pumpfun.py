"""Ereignisse lesen und die Kurve nachrechnen (ADR-081).

Die Fixtures in `tests/fixtures/meme/` sind echte Transaktionen vom
Kalibriertag 2026-09-26, gekuerzt auf Log, Inner Instructions und Konten. An
ihnen wurde nur die Technik geprueft, nie ein Kurs.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from qt.meme.pumpfun import (
    CREATE_EVENT,
    STANDARD_PUBKEY,
    TRADE_EVENT,
    CreateEreignis,
    TradeEreignis,
    Zustand,
    b58decode,
    b58encode,
    ereignisse,
    kaufen,
    pool_verkauf,
    verkaufen_mit_uns,
)
from tests.meme_welt import K0, RTOK0, VSOL0, VTOK0, pubkey, trade_event, transaktion

FIXTURES = Path(__file__).parent / "fixtures" / "meme"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_diskriminatoren_sind_sha256_der_ereignisnamen():
    assert CREATE_EVENT.hex() == "1b72a94ddeeb6376"
    assert TRADE_EVENT.hex() == "bddb7fd34ee661ee"


def test_base58_hin_und_zurueck():
    for roh in (b"\0" * 32, b"\0\0\x01\x02", bytes(range(32))):
        assert b58decode(b58encode(roh)) == roh
    assert b58encode(b"\0" * 32) == STANDARD_PUBKEY


def test_echter_start_mit_erstellerkauf():
    liste = ereignisse(_fixture("create_mit_devkauf"))
    create = [e for e in liste if isinstance(e, CreateEreignis)]
    trades = [e for e in liste if isinstance(e, TradeEreignis)]

    assert len(create) == 1 and len(trades) == 1
    c, t = create[0], trades[0]
    assert (c.vsol, c.vtok, c.rtok, c.supply) == (VSOL0, VTOK0, RTOK0, 10**15)
    assert c.in_sol and not c.mayhem
    assert t.mint == c.mint and t.kauf
    assert (t.fee_bps, t.creator_fee_bps, t.zusatz_bps) == (95, 30, 0)
    # Die Kurve ist ein konstantes Produkt, die Token-Seite stimmt exakt.
    assert abs(t.vsol * t.vtok - K0) / K0 < 1e-9
    assert VTOK0 - t.vtok == RTOK0 - t.rtok
    assert t.vsol - VSOL0 == t.rsol


def test_verkauf_ueber_einen_router_liefert_nur_das_ereignis_von_pumpfun():
    """Der Router gibt ein eigenes Ereignis aus; es darf nicht als Handel gelten."""
    liste = ereignisse(_fixture("verkauf"))

    assert len(liste) == 1
    assert isinstance(liste[0], TradeEreignis) and not liste[0].kauf


def test_ein_abgeschnittenes_log_verliert_kein_ereignis():
    """Am 2026-09-26 fehlte an zwei von 80 Starts genau der Handel, der den
    Stand bestimmte, weil Solana das Log gekuerzt hatte."""
    tx = copy.deepcopy(_fixture("kauf"))
    tx["meta"]["logMessages"] = [z for z in tx["meta"]["logMessages"] if not z.startswith("Program data: ")]
    tx["meta"]["logMessages"].append("Log truncated")

    assert [type(e) for e in ereignisse(tx)] == [TradeEreignis]


def test_ohne_inner_instructions_greift_das_log():
    tx = copy.deepcopy(_fixture("kauf"))
    del tx["meta"]["innerInstructions"]

    assert [type(e) for e in ereignisse(tx)] == [TradeEreignis]


def test_ein_fremdes_programm_mit_gleichnamigem_ereignis_zaehlt_nicht():
    """Jedes Anchor-Programm mit einem `TradeEvent` hat denselben Diskriminator."""
    mint = pubkey("mint")
    tx = transaktion("s", 100, 1, [], fremd=[trade_event(mint, 100, 5_000_000_000)])

    assert ereignisse(tx) == []


def test_holder_rewards_zaehlen_einmal_als_erstellergebuehr():
    mint = pubkey("mint")
    tx = transaktion("s", 100, 1, [trade_event(mint, 100, 10**9, creator_fee_bps=0, holder_bps=30)])

    (t,) = ereignisse(tx)
    assert (t.fee_bps, t.creator_fee_bps, t.zusatz_bps) == (95, 0, 30)


# -- Kurvenrechnung ---------------------------------------------------------------


def _stand(netto: int = 0, fee=(95, 30, 0)) -> Zustand:
    vsol = VSOL0 + netto
    vtok = K0 // vsol
    return Zustand(0, "s", vsol, vtok, netto, RTOK0 - (VTOK0 - vtok), *fee)


def test_kauf_und_sofortiger_verkauf_kosten_genau_die_gebuehren():
    z = _stand(2 * 10**9)
    kauf = kaufen(z, 250_000_000)
    zurueck = verkaufen_mit_uns(z, kauf.netto, kauf.tokens)

    assert zurueck == pytest.approx(kauf.netto, rel=1e-9)
    assert kauf.netto == pytest.approx(250_000_000 / 1.0125)


def test_ohne_unseren_kauf_in_der_kurve_zahlte_man_die_preiswirkung_zweimal():
    """Der Grund fuer `verkaufen_mit_uns`: die beobachtete Kurve enthaelt uns nicht."""
    z = _stand()
    kauf = kaufen(z, 5 * 10**9)
    naiv = z.vsol - z.vsol * z.vtok / (z.vtok + kauf.tokens)

    assert naiv < 0.9 * verkaufen_mit_uns(z, kauf.netto, kauf.tokens)


def test_ein_kauf_nimmt_hoechstens_die_restlichen_tokens():
    fast_voll = _stand(84 * 10**9)
    kauf = kaufen(fast_voll, 10 * 10**9)

    assert kauf.macht_voll
    assert kauf.tokens == fast_voll.rtok
    assert kauf.rest > 0
    assert kauf.netto + kauf.rest / (1 + fast_voll.gebuehr) == pytest.approx(10 * 10**9 / (1 + fast_voll.gebuehr))


def test_poolverkauf_kleiner_mengen_zum_preis():
    assert pool_verkauf(85e9, 206.9e12, 1e6) == pytest.approx(85e9 / 206.9e12 * 1e6, rel=1e-6)


def test_gebuehr_summiert_alle_teile():
    assert _stand(fee=(95, 30, 5)).gebuehr == pytest.approx(0.0130)


def test_ein_neues_feld_vor_dem_anweisungsnamen_verschiebt_nur():
    """pump.fun erweitert das Ereignis laufend; der Rest muss lesbar bleiben."""
    ereignis = trade_event(pubkey("mint"), 100, 10**9, creator_fee_bps=0, holder_bps=30)
    stelle = 8 + 217
    erweitert = ereignis[:stelle] + b"\x07" * 8 + ereignis[stelle:]

    (t,) = ereignisse(transaktion("s", 100, 1, [erweitert]))
    assert t.zusatz_bps == 30
