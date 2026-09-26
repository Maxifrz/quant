"""pump.fun lesen und nachrechnen: Ereignisse aus Transaktionen, die Bonding-Curve.

**Die Kurve ist ein konstantes Produkt auf virtuellen Reserven.** Jeder Token
startet mit 30 SOL und 1,073 Mrd. Tokens virtuell (gemessen am 2026-09-26);
ein Kauf von `dx` Lamports netto liefert `vtok - vsol*vtok/(vsol+dx)` Tokens,
ein Verkauf umgekehrt. Die Gebuehren (Protokoll plus Ersteller) werden auf den
SOL-Betrag aufgeschlagen bzw. von ihm abgezogen und fliessen **nicht** in die
Reserven. Damit ist jeder simulierte Handel exakt, nicht geschaetzt -- anders
als jeder Fill auf einem Orderbuch.

Die Ereignisse stehen als Anchor-Events in den Logs (`Program data: ...`).
Ihre ersten acht Bytes sind SHA-256 von `event:<Name>`; hier nachgerechnet,
nicht abgeschrieben.

Gelesen wird nur der Anfang eines Ereignisses, der seit 2024 stabil ist.
pump.fun haengt regelmaessig Felder hinten an -- das `TradeEvent` hatte am
2026-09-26 rund 150 Bytes mehr als hier gebraucht. Ein Parser, der die volle
Laenge erwartete, braeche bei der naechsten Erweiterung still.
"""

from __future__ import annotations

import base64
import hashlib
import re
import struct
from dataclasses import asdict, dataclass

from qt.meme.registrierung import PUMP_PROGRAMM

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

#: `Pubkey::default()` -- steht als `quote_mint` bei jeder Kurve, die in SOL
#: gehandelt wird. Alles andere ist eine Kurve in einer anderen Waehrung.
STANDARD_PUBKEY = "1" * 32


def _diskriminator(name: str) -> bytes:
    return hashlib.sha256(f"event:{name}".encode()).digest()[:8]


#: Anchor schickt Ereignisse zusaetzlich als Instruktion an das eigene
#: Programm (`emit_cpi!`); deren Daten beginnen mit diesem Kennzeichen.
EVENT_CPI = bytes.fromhex("e445a52e51cb9a1d")
CREATE_EVENT = _diskriminator("CreateEvent")
TRADE_EVENT = _diskriminator("TradeEvent")
COMPLETE_EVENT = _diskriminator("CompleteEvent")


def b58decode(text: str) -> bytes:
    zahl = 0
    for zeichen in text:
        zahl = zahl * 58 + _ALPHABET.index(zeichen)
    roh = zahl.to_bytes((zahl.bit_length() + 7) // 8, "big") if zahl else b""
    return b"\0" * (len(text) - len(text.lstrip("1"))) + roh


def b58encode(daten: bytes) -> str:
    """Base58 wie bei Bitcoin und Solana -- klein genug, um keine Abhaengigkeit zu rechtfertigen."""
    zahl = int.from_bytes(daten, "big")
    text = ""
    while zahl:
        zahl, rest = divmod(zahl, 58)
        text = _ALPHABET[rest] + text
    fuehrende_nullen = len(daten) - len(daten.lstrip(b"\0"))
    return "1" * fuehrende_nullen + text


class _Leser:
    def __init__(self, daten: bytes) -> None:
        self._d = daten
        self._o = 0

    def bytes_(self, n: int) -> bytes:
        if self._o + n > len(self._d):
            raise ValueError("Ereignis zu kurz")
        wert = self._d[self._o : self._o + n]
        self._o += n
        return wert

    def u64(self) -> int:
        return struct.unpack("<Q", self.bytes_(8))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.bytes_(8))[0]

    def pubkey(self) -> str:
        return b58encode(self.bytes_(32))

    def boolean(self) -> bool:
        return self.bytes_(1) != b"\0"

    def text(self) -> str:
        n = struct.unpack("<I", self.bytes_(4))[0]
        return self.bytes_(n).decode("utf-8", "replace")


@dataclass(frozen=True, slots=True)
class CreateEreignis:
    mint: str
    kurve: str
    ersteller: str
    zeit: int
    vtok: int
    vsol: int
    rtok: int
    supply: int
    mayhem: bool = False
    quote_mint: str = STANDARD_PUBKEY

    @property
    def in_sol(self) -> bool:
        return self.quote_mint == STANDARD_PUBKEY


@dataclass(frozen=True, slots=True)
class TradeEreignis:
    mint: str
    sol: int
    tokens: int
    kauf: bool
    nutzer: str
    zeit: int
    vsol: int
    vtok: int
    rsol: int
    rtok: int
    fee_bps: int
    creator_fee_bps: int
    #: Aus dem Schwanz des Ereignisses; 0, wenn er sich nicht lesen laesst.
    zusatz_bps: int = 0


def _create(daten: bytes) -> CreateEreignis:
    """Layout laut IDL (`pump-fun/pump-public-docs`, idl/pump.json, 2026-09-26).

    Der Schwanz -- token_program, is_mayhem_mode, is_cashback_enabled,
    quote_mint, virtual_quote_reserves, creator_fee_bps, is_holder_reward --
    ist 83 Bytes lang, genau wie am echten Ereignis gemessen.
    """
    r = _Leser(daten)
    r.text(), r.text(), r.text()  # Name, Symbol, URI
    mint, kurve, _nutzer, ersteller = r.pubkey(), r.pubkey(), r.pubkey(), r.pubkey()
    zeit = r.i64()
    vtok, vsol, rtok, supply = r.u64(), r.u64(), r.u64(), r.u64()
    mayhem, quote_mint = False, STANDARD_PUBKEY
    try:
        r.pubkey()  # token_program
        mayhem = r.boolean()
        r.boolean()  # is_cashback_enabled
        quote_mint = r.pubkey()
    except ValueError:
        pass  # aeltere Ereignisse ohne Schwanz: SOL-Kurve, kein Mayhem
    return CreateEreignis(mint, kurve, ersteller, zeit, vtok, vsol, rtok, supply, mayhem, quote_mint)


#: Das tatsaechliche Layout nach `creator_fee` (Offset 217) weicht vom IDL ab
#: (gemessen an drei Ereignissen am 2026-09-26): 33 Bytes, in allen drei null,
#: dann bei Offset 250 `ix_name` als String, dann mayhem_mode, cashback_bps,
#: cashback, buyback_bps, buyback, die Liste der Anteilseigner, quote_mint,
#: drei Betraege und holder_rewards_bps. Gesucht wird der Name der Anweisung,
#: nicht ein fester Offset: kommt vor ihm ein Feld hinzu, bleibt der Rest lesbar.
_IX_NAME = re.compile(rb"(buy|sell)[a-z0-9_]{0,40}")
_NACH_CREATOR_FEE = 217


def _ix_name_ende(daten: bytes) -> int | None:
    """Offset direkt hinter `ix_name`, oder `None`, wenn keiner zu finden ist."""
    for kopf in range(_NACH_CREATOR_FEE, len(daten) - 4):
        n = struct.unpack_from("<I", daten, kopf)[0]
        if 0 < n <= 40 and _IX_NAME.fullmatch(daten[kopf + 4 : kopf + 4 + n]):
            return kopf + 4 + n
    return None


def _zusatz_bps(daten: bytes, creator_fee_bps: int) -> int:
    """Was der Haendler ueber Protokoll- und Erstellergebuehr hinaus zahlt.

    **Buyback ist kein Aufschlag**, sondern ein Anteil der Protokollgebuehr:
    `buyback_bps` stand am 2026-09-26 bei 5000, und `buyback / fee` lag in
    jedem gemessenen Handel bei hoechstens 0,500. Bei Holder-Reward-Coins geht
    die Erstellergebuehr an die Halter statt an den Ersteller; gezaehlt wird
    der hoehere der beiden Saetze, einmal. Cashback wird als Kosten gezaehlt
    und nie gutgeschrieben -- er war in jeder Messung null.
    """
    o = _ix_name_ende(daten)
    if o is None:
        return 0
    try:
        cashback_bps = struct.unpack_from("<Q", daten, o + 1)[0]
        anteile = struct.unpack_from("<I", daten, o + 33)[0]
        o = o + 37 + anteile * 34 + 32 + 24
        holder_bps = struct.unpack_from("<Q", daten, o)[0]
    except struct.error:
        return 0
    if cashback_bps > 10_000 or holder_bps > 10_000:
        return 0
    return cashback_bps + max(0, holder_bps - creator_fee_bps)


def _trade(daten: bytes) -> TradeEreignis:
    r = _Leser(daten)
    mint, sol, tokens, kauf, nutzer = r.pubkey(), r.u64(), r.u64(), r.boolean(), r.pubkey()
    zeit = r.i64()
    vsol, vtok, rsol, rtok = r.u64(), r.u64(), r.u64(), r.u64()
    r.pubkey()  # Gebuehrenempfaenger
    fee_bps, _fee = r.u64(), r.u64()
    r.pubkey()  # Ersteller
    creator_fee_bps, _creator_fee = r.u64(), r.u64()
    return TradeEreignis(
        mint, sol, tokens, kauf, nutzer, zeit, vsol, vtok, rsol, rtok, fee_bps, creator_fee_bps,
        _zusatz_bps(daten, creator_fee_bps),
    )


def _parse(roh: bytes) -> CreateEreignis | TradeEreignis | None:
    kopf, rumpf = roh[:8], roh[8:]
    if kopf == CREATE_EVENT:
        return _create(rumpf)
    if kopf == TRADE_EVENT:
        return _trade(rumpf)
    return None


def ereignisse(tx: dict, programm: str = PUMP_PROGRAMM) -> list[CreateEreignis | TradeEreignis]:
    """Alle Create- und Trade-Ereignisse, die `programm` ausgegeben hat.

    **Zuerst aus den Inner Instructions.** pump.fun gibt jedes Ereignis
    doppelt aus: im Log und als Instruktion an sich selbst (`emit_cpi!`).
    Das Log kuerzt Solana bei grossen Transaktionen ("Log truncated") -- an
    zwei von 80 Starts am 2026-09-26 fehlte dadurch genau der Handel, der den
    Stand bestimmte. Die Inner Instructions werden nie gekuerzt, und wem sie
    gehoeren, steht explizit darin. Das Log bleibt der Rueckfall fuer
    Transaktionen ohne sie.
    """
    aus_cpi = _aus_inner_instructions(tx, programm)
    if aus_cpi is not None:
        return aus_cpi
    return _aus_log(tx, programm)


def _aus_inner_instructions(tx: dict, programm: str) -> list[CreateEreignis | TradeEreignis] | None:
    meta = tx.get("meta") or {}
    gruppen = meta.get("innerInstructions")
    nachricht = (tx.get("transaction") or {}).get("message") or {}
    if gruppen is None or "accountKeys" not in nachricht:
        return None
    geladen = meta.get("loadedAddresses") or {}
    konten = list(nachricht["accountKeys"]) + list(geladen.get("writable", [])) + list(geladen.get("readonly", []))
    gefunden: list[CreateEreignis | TradeEreignis] = []
    kennzeichen = False
    for gruppe in gruppen:
        for ix in gruppe.get("instructions", []):
            index = ix.get("programIdIndex")
            if index is None or index >= len(konten) or konten[index] != programm:
                continue
            daten = b58decode(ix.get("data", ""))
            if daten[:8] != EVENT_CPI:
                continue
            kennzeichen = True
            ereignis = _parse(daten[8:])
            if ereignis is not None:
                gefunden.append(ereignis)
    return gefunden if kennzeichen else None


def _aus_log(tx: dict, programm: str) -> list[CreateEreignis | TradeEreignis]:
    """Rueckfall: die Ereignisse aus den `Program data:`-Zeilen des Logs.

    **Nur die von pump.fun selbst.** Der Diskriminator ist SHA-256 von
    `event:TradeEvent` -- jedes Anchor-Programm mit einem Ereignis dieses
    Namens hat denselben. Am 2026-09-26 gaben Bot-Programme in zwei von zehn
    Stichproben-Tokens eigene "TradeEvents" aus, die ebenfalls mit der Mint
    beginnen und deren Reserven nicht zur Kurve passten (Produkt bis 90 %
    daneben). Wem eine Zeile gehoert, steht im Log nur implizit: im Stapel der
    `invoke`- und `success`-Zeilen davor.

    Unbekannte Ereignisse werden uebergangen. Ein abgeschnittenes Log
    ("Log truncated") liefert, was bis dahin stand -- der Aufrufer merkt am
    fehlenden Ereignis, dass etwas fehlt.
    """
    meta = tx.get("meta") or {}
    gefunden: list[CreateEreignis | TradeEreignis] = []
    stapel: list[str] = []
    for zeile in meta.get("logMessages") or []:
        teile = zeile.split(" ")
        if len(teile) >= 3 and teile[0] == "Program" and teile[2] == "invoke":
            stapel.append(teile[1])
            continue
        if len(teile) >= 3 and teile[0] == "Program" and (teile[2] == "success" or teile[2].startswith("failed")):
            if stapel:
                stapel.pop()
            continue
        if not zeile.startswith("Program data: ") or not stapel or stapel[-1] != programm:
            continue
        try:
            roh = base64.b64decode(zeile.split("Program data: ", 1)[1])
        except ValueError:
            continue
        ereignis = _parse(roh)
        if ereignis is not None:
            gefunden.append(ereignis)
    return gefunden


# -- Kurvenstand ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Zustand:
    """Die Kurve nach einem bestimmten Handel.

    `zeit` und `sig` bezeichnen den Handel, der diesen Stand hergestellt hat;
    `rtok == 0` heisst: die Kurve ist voll, gehandelt wird nur noch im Pool.
    """

    zeit: int
    sig: str
    vsol: int
    vtok: int
    rsol: int
    rtok: int
    fee_bps: int
    creator_fee_bps: int
    zusatz_bps: int = 0

    @property
    def voll(self) -> bool:
        return self.rtok == 0

    @property
    def gebuehr(self) -> float:
        return (self.fee_bps + self.creator_fee_bps + self.zusatz_bps) / 10_000

    def als_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def aus_dict(cls, d: dict) -> Zustand:
        return cls(**{k: d[k] for k in cls.__slots__ if k in d})

    @classmethod
    def aus_trade(cls, t: TradeEreignis, zeit: int, sig: str) -> Zustand:
        return cls(
            zeit, sig, t.vsol, t.vtok, t.rsol, t.rtok, t.fee_bps, t.creator_fee_bps, t.zusatz_bps
        )

    def mit_gebuehr(self, fee_bps: int, creator_fee_bps: int, zusatz_bps: int) -> Zustand:
        return Zustand(
            self.zeit, self.sig, self.vsol, self.vtok, self.rsol, self.rtok,
            fee_bps, creator_fee_bps, zusatz_bps,
        )


@dataclass(frozen=True, slots=True)
class Kauf:
    tokens: float
    netto: float
    rest: float
    macht_voll: bool


def kaufen(z: Zustand, einsatz: float) -> Kauf:
    """Tokens fuer `einsatz` Lamports inklusive Gebuehr.

    Wuerde der Kauf mehr Tokens verlangen, als die Kurve noch hat, bekommt man
    den Rest und das uebrige SOL zurueck -- und die Kurve ist danach voll.
    """
    netto = einsatz / (1.0 + z.gebuehr)
    tokens = z.vtok - z.vsol * z.vtok / (z.vsol + netto)
    if tokens < z.rtok:
        return Kauf(tokens, netto, 0.0, False)
    tokens = float(z.rtok)
    netto = z.vsol * z.vtok / (z.vtok - z.rtok) - z.vsol
    return Kauf(tokens, netto, einsatz - netto * (1.0 + z.gebuehr), True)


def verkaufen_mit_uns(z: Zustand, unser_netto: float, tokens: float) -> float:
    """Brutto-Lamports fuer `tokens`, wenn unser Kauf in der Kurve steckt.

    Beobachtet wurde die Kurve **ohne** uns. Mit uns laege sie um unseren
    Netto-Zufluss hoeher; wer das weglaesst, bezahlt die Preiswirkung des
    eigenen Kaufs beim Verkauf ein zweites Mal. Angenommen wird, dass alle
    anderen dieselben SOL-Betraege bewegt haetten.
    """
    k = z.vsol * z.vtok
    x = z.vsol + unser_netto
    y = k / x
    return x - k / (y + tokens)


def pool_verkauf(pool_sol: float, pool_tok: float, tokens: float) -> float:
    """Brutto-Lamports fuer `tokens` im Pool nach der Graduierung (konstantes Produkt)."""
    return pool_sol * tokens / (pool_tok + tokens)
