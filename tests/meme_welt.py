"""Eine nachgebaute pump.fun-Welt fuer die Tests des Papiertests (ADR-081).

Ereignisse werden byte-genau so kodiert, wie pump.fun sie am 2026-09-26
ausgab (Layout gegen echte Transaktionen geprueft, siehe
`tests/fixtures/meme/`), und als `emit_cpi`-Inner-Instruction in
Transaktionen verpackt. Eine Fake-RPC liefert Signaturlisten seitenweise, wie
die echte. Kein Netz.
"""

from __future__ import annotations

import hashlib
import struct

from qt.meme.pumpfun import CREATE_EVENT, EVENT_CPI, TRADE_EVENT, b58decode, b58encode
from qt.meme.registrierung import MINT_AUTORITAET, PUMP_PROGRAMM
from qt.meme.rpc import RpcFehler

VSOL0 = 30_000_000_000
VTOK0 = 1_073_000_000_000_000
RTOK0 = 793_100_000_000_000
SUPPLY = 1_000_000_000_000_000
K0 = VSOL0 * VTOK0


def pubkey(name: str) -> str:
    return b58encode(hashlib.sha256(name.encode()).digest())


def _text(t: str) -> bytes:
    b = t.encode()
    return struct.pack("<I", len(b)) + b


def create_event(mint: str, kurve: str, zeit: int, *, mayhem: bool = False, quote_mint: str | None = None) -> bytes:
    ersteller = b58decode(pubkey("ersteller"))
    rumpf = _text("Name") + _text("SYM") + _text("https://x") + b58decode(mint) + b58decode(kurve)
    rumpf += ersteller + ersteller + struct.pack("<qQQQQ", zeit, VTOK0, VSOL0, RTOK0, SUPPLY)
    rumpf += b"\0" * 32 + bytes([mayhem]) + b"\0"
    rumpf += (b58decode(quote_mint) if quote_mint else b"\0" * 32)
    rumpf += struct.pack("<QQ", VSOL0, 30) + b"\0"
    return CREATE_EVENT + rumpf


def kurve_nach(netto: int, basis: int = VSOL0) -> tuple[int, int, int, int]:
    """(vsol, vtok, rsol, rtok) nach `netto` Lamports Zufluss auf einer Standardkurve.

    `basis` verschiebt die virtuelle SOL-Seite, wie es der Mayhem-Agent tut.
    """
    vsol = basis + netto
    vtok = K0 * basis // (VSOL0 * vsol) if basis != VSOL0 else K0 // vsol
    rtok = RTOK0 - (VTOK0 - vtok)
    return vsol, vtok, netto, max(rtok, 0)


def trade_event(
    mint: str,
    zeit: int,
    netto: int,
    *,
    kauf: bool = True,
    fee_bps: int = 95,
    creator_fee_bps: int = 30,
    basis: int = VSOL0,
    holder_bps: int = 0,
    ix: str = "buy",
    voll: bool = False,
) -> bytes:
    vsol, vtok, rsol, rtok = kurve_nach(netto, basis)
    if voll:
        vtok, rtok = VTOK0 - RTOK0, 0
        vsol = K0 // vtok
        rsol = vsol - basis
    rumpf = b58decode(mint) + struct.pack("<QQ", 1_000_000, 1_000) + bytes([kauf])
    rumpf += b58decode(pubkey("haendler")) + struct.pack("<qQQQQ", zeit, vsol, vtok, rsol, rtok)
    rumpf += b58decode(pubkey("gebuehren")) + struct.pack("<QQ", fee_bps, 0)
    rumpf += b58decode(pubkey("ersteller")) + struct.pack("<QQ", creator_fee_bps, 0)
    rumpf += b"\0" * 33 + _text(ix) + b"\0" + struct.pack("<QQQQ", 0, 0, 5000, 0)
    rumpf += struct.pack("<I", 0) + b"\0" * 32 + struct.pack("<QQQ", 0, 0, 0)
    rumpf += struct.pack("<QQ", holder_bps, 0)
    return TRADE_EVENT + rumpf


def transaktion(sig: str, zeit: int, slot: int, ereignisse: list[bytes], *, fremd: list[bytes] = ()) -> dict:
    """Eine erfolgreiche Transaktion; `fremd` sind Ereignisse eines anderen Programms."""
    konten = [pubkey("zahler"), PUMP_PROGRAMM, pubkey("fremdes-programm")]
    innen = [{"programIdIndex": 1, "accounts": [], "data": b58encode(EVENT_CPI + e)} for e in ereignisse]
    innen += [{"programIdIndex": 2, "accounts": [], "data": b58encode(EVENT_CPI + e)} for e in fremd]
    return {
        "slot": slot,
        "blockTime": zeit,
        "transaction": {"signatures": [sig], "message": {"accountKeys": konten}},
        "meta": {
            "err": None,
            "logMessages": [f"Program {PUMP_PROGRAMM} invoke [1]", f"Program {PUMP_PROGRAMM} success"],
            "innerInstructions": [{"index": 0, "instructions": innen}],
            "loadedAddresses": {"writable": [], "readonly": []},
        },
    }


class Welt:
    """Signaturlisten und Transaktionen, wie die RPC sie liefert (neueste zuerst)."""

    def __init__(self) -> None:
        self.signaturen: dict[str, list[dict]] = {}
        self.transaktionen: dict[str, dict] = {}
        self._slot = 1_000
        self._n = 0

    def _sig(self) -> str:
        self._n += 1
        return pubkey(f"sig-{self._n}") + "x"

    def _eintragen(self, konten: list[str], sig: str, zeit: int, tx: dict | None, fehler=None) -> None:
        self._slot += 1
        eintrag = {"signature": sig, "blockTime": zeit, "slot": self._slot, "err": fehler}
        for konto in konten:
            liste = self.signaturen.setdefault(konto, [])
            liste.append(eintrag)
            liste.sort(key=lambda e: (e["blockTime"], e["slot"]), reverse=True)
        if tx is not None:
            tx["slot"] = self._slot
            self.transaktionen[sig] = tx

    def start(self, name: str, zeit: int, *, devkauf: int | None = 500_000_000, mayhem=False, quote_mint=None) -> tuple[str, str, str]:
        mint, kurve = pubkey(f"mint-{name}"), pubkey(f"kurve-{name}")
        ereignisse = [create_event(mint, kurve, zeit, mayhem=mayhem, quote_mint=quote_mint)]
        if devkauf is not None:
            ereignisse.append(trade_event(mint, zeit, devkauf))
        sig = self._sig()
        self._eintragen([MINT_AUTORITAET, kurve], sig, zeit, transaktion(sig, zeit, 0, ereignisse))
        return mint, kurve, sig

    def handel(self, mint: str, kurve: str, zeit: int, netto: int, **kw) -> str:
        sig = self._sig()
        fremd = kw.pop("fremd", ())
        tx = transaktion(sig, zeit, 0, [trade_event(mint, zeit, netto, **kw)], fremd=fremd)
        self._eintragen([kurve], sig, zeit, tx)
        return sig

    def sonstiges(self, konto: str, zeit: int, *, fehler=None) -> str:
        """Eine Transaktion ohne Handelsereignis (Kontenpflege) oder eine gescheiterte."""
        sig = self._sig()
        self._eintragen([konto], sig, zeit, transaktion(sig, zeit, 0, []), fehler=fehler)
        return sig


class FakeRpc:
    def __init__(self, welt: Welt) -> None:
        self.welt = welt
        self.aufrufe = 0

    def aufruf(self, methode, params, *, null_ist_fehlend=False, unvollstaendig=None):
        self.aufrufe += 1
        if methode == "getSignaturesForAddress":
            konto, optionen = params
            liste = self.welt.signaturen.get(konto, [])
            if "before" in optionen:
                i = next(i for i, e in enumerate(liste) if e["signature"] == optionen["before"])
                liste = liste[i + 1 :]
            seite = [dict(e) for e in liste[: optionen.get("limit", 1000)]]
            if unvollstaendig is not None and unvollstaendig(seite):
                raise RpcFehler("Fake: Antwort endet zu frueh")
            return seite
        if methode == "getTransaction":
            tx = self.welt.transaktionen.get(params[0])
            if tx is None and null_ist_fehlend:
                raise RpcFehler("Fake: Transaktion fehlt")
            return tx
        raise AssertionError(methode)

    def statistik(self):
        return {"fake": {"aufrufe": self.aufrufe, "gedrosselt": 0}}


# -- Datensaetze, wie der Sammler sie schreibt ---------------------------------------


def kurvenstand(netto: int = 0, *, basis: int = VSOL0, gebuehr=(95, 30, 0), voll: bool = False) -> dict:
    """Ein Kurvenstand als dict (`Zustand.als_dict`); `voll`: alle Tokens verkauft."""
    if voll:
        vtok = VTOK0 - RTOK0
        vsol = K0 * basis // (VSOL0 * vtok)
        rsol, rtok = vsol - basis, 0
    else:
        vsol, vtok, rsol, rtok = kurve_nach(int(netto), basis)
    return {
        "zeit": 0, "sig": "s", "vsol": vsol, "vtok": vtok, "rsol": rsol, "rtok": rtok,
        "fee_bps": gebuehr[0], "creator_fee_bps": gebuehr[1], "zusatz_bps": gebuehr[2],
    }


def satz(
    tag,
    *,
    a: float = 500_000_000,
    b: float | None = None,
    c: float | None = None,
    e: float | None = None,
    c_voll: bool = False,
    e_voll: bool = False,
    gebuehr=(95, 30, 0),
    basis: int = VSOL0,
    fehler: str | None = None,
    nummer: int = 0,
) -> dict:
    """Ein Datensatz; `a`..`e` sind die echten SOL-Reserven der Staende.

    Ohne Angabe bleibt die Kurve, wo sie war: b = a, c = b, e = c.
    """
    b = a if b is None else b
    c = b if c is None else c
    e = c if e is None else e
    kw = {"basis": basis, "gebuehr": gebuehr}
    return {
        "tag": tag.isoformat(), "sig": f"{tag.isoformat()}-{nummer}", "slot": nummer, "t0": 0,
        "mint": pubkey(f"mint-{tag}-{nummer}"), "kurve": pubkey(f"kurve-{tag}-{nummer}"),
        "ersteller": pubkey("ersteller"),
        "k0": {"vsol": VSOL0, "vtok": VTOK0, "rtok": RTOK0, "supply": SUPPLY},
        "mayhem": basis != VSOL0,
        "a": kurvenstand(a, **kw),
        "b": kurvenstand(b, **kw),
        "c": kurvenstand(c, voll=c_voll, **kw),
        "e": kurvenstand(e, voll=e_voll or c_voll, **kw),
        "tx_bis_signal": 0, "tx_bis_ende": 0, "fehler": fehler,
    }
