"""Der RPC-Client faellt auf den naechsten Knoten zurueck, statt aufzugeben (ADR-081)."""

from __future__ import annotations

import io
import urllib.error

import pytest

from qt.meme.rpc import Endpunkt, RpcFehler, SolanaRpc

SEITE = [{"signature": "s1", "blockTime": 100, "slot": 1, "err": None}]


def _rpc(antworten: dict[str, list]):
    """Ein Client mit zwei Knoten; `antworten` je URL in Aufrufreihenfolge."""
    aufrufe: list[str] = []

    def senden(url, body):
        aufrufe.append(url)
        antwort = antworten[url].pop(0)
        if isinstance(antwort, Exception):
            raise antwort
        return antwort

    rpc = SolanaRpc(
        endpunkte=[Endpunkt("https://schnell", 0.0), Endpunkt("https://vollstaendig", 0.0)],
        versuche=3,
        schlafen=lambda s: None,
        uhr=lambda: 0.0,
        senden=senden,
    )
    return rpc, aufrufe


def test_ein_cursor_jenseits_der_historie_geht_an_den_naechsten_knoten():
    """So brach die Sammlung ab dem 2026-10-01 ab: Publicnode kannte den
    Cursor nicht mehr und antwortete mit -32020 statt einer kurzen Seite."""
    rpc, aufrufe = _rpc({
        "https://schnell": [{"error": {"code": -32020, "message": "Transaction 5oyt... not found"}}],
        "https://vollstaendig": [{"result": SEITE}],
    })

    assert rpc.aufruf("getSignaturesForAddress", ["konto", {"before": "alt"}]) == SEITE
    assert aufrufe == ["https://schnell", "https://vollstaendig"]


def test_scheitern_alle_knoten_nennt_der_fehler_den_letzten():
    rpc, _ = _rpc({
        "https://schnell": [{"error": {"code": -32020, "message": "not found"}}],
        "https://vollstaendig": [{"error": {"code": -32602, "message": "Invalid params"}}],
    })

    with pytest.raises(RpcFehler, match="Invalid params"):
        rpc.aufruf("getSignaturesForAddress", ["konto", {}])


def test_eine_kurze_seite_geht_an_den_naechsten_knoten():
    rpc, aufrufe = _rpc({
        "https://schnell": [{"result": []}],
        "https://vollstaendig": [{"result": SEITE}],
    })

    ergebnis = rpc.aufruf("getSignaturesForAddress", ["konto", {}], unvollstaendig=lambda s: not s)

    assert ergebnis == SEITE and len(aufrufe) == 2


def test_null_heisst_auf_einem_beschnittenen_knoten_nicht_mehr_da():
    rpc, aufrufe = _rpc({
        "https://schnell": [{"result": None}],
        "https://vollstaendig": [{"result": {"slot": 7}}],
    })

    assert rpc.aufruf("getTransaction", ["sig", {}], null_ist_fehlend=True) == {"slot": 7}
    assert len(aufrufe) == 2


def test_gedrosselt_wird_am_selben_knoten_wiederholt():
    drossel = urllib.error.HTTPError("https://schnell", 429, "Too Many Requests", {}, io.BytesIO())
    rpc, aufrufe = _rpc({
        "https://schnell": [drossel, {"result": SEITE}],
        "https://vollstaendig": [],
    })

    assert rpc.aufruf("getSignaturesForAddress", ["konto", {}]) == SEITE
    assert aufrufe == ["https://schnell", "https://schnell"]
