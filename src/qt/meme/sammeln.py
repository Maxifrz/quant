"""Je UTC-Tag eine Zufallsstichprobe aller pump.fun-Starts, mit den Kurvenstaenden
zu den registrierten Zeitpunkten (ADR-081).

Ablauf je Tag:

1. **Alle Starts aufzaehlen.** Die Signaturen der Mint-Autoritaet, seitenweise
   von jetzt zurueck bis zum Tagesbeginn. Die toten Tokens sind dabei; wer von
   einer Kursseite ausgeht, sieht nur die Ueberlebenden.
2. **Mischen mit der Saat des Tages** und der Reihe nach abarbeiten, bis 500
   Starts beisammen sind. Transaktionen ohne `CreateEvent` zaehlen nicht mit.
3. **Je Start die Kurvenstaende** nach dem Start, bei t0+60 s, t0+62 s und
   t0+1.864 s: der Stand nach dem letzten erfolgreichen Handel bis dahin.

**Hier wird nichts bewertet.** Der Sammler kennt keine Regel und keine
Rendite; er schreibt Staende. Was sie wert gewesen waeren, rechnet erst
`papier`, und das erst nach dem letzten Testtag (`auswertung`).

Fehler beim Abruf werden verbucht und **nicht durch andere Starts ersetzt**.
Scheitern bevorzugt die aktiven Tokens (mehr Signaturen, laengere Wege), und
ein Ersatz zoege systematisch ruhige nach.
"""

from __future__ import annotations

import hashlib
import json
import random
import time as _time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timezone
from pathlib import Path

from qt.core.config import PROJECT_ROOT
from qt.meme import registrierung as reg
from qt.meme.pumpfun import CreateEreignis, TradeEreignis, Zustand, ereignisse
from qt.meme.rpc import RpcFehler

VERZEICHNIS = PROJECT_ROOT / "data" / "meme" / "tage"

TX_OPTIONEN = {"encoding": "json", "maxSupportedTransactionVersion": 1, "commitment": "finalized"}

#: Obergrenzen gegen Endlosschleifen, weit ueber dem Erwarteten: ein Tag hatte
#: am 2026-09-26 rund 50 Seiten Starts, eine Kurve selten mehr als eine Seite.
#: Die Starts werden von jetzt aus zurueckgeblaettert; 2.000 Seiten reichen fuer
#: einen Tag, der rund 45 Tage zurueckliegt (vorher 600, rund 13 Tage). So
#: bleibt jeder Testtag bis nach der Auswertung nachholbar, auch wenn die
#: Sammlung tagelang steht.
MAX_SEITEN_STARTS = 2000
MAX_SEITEN_KURVE = 200
MAX_RUECKSCHRITTE = 60
#: Wie weit hinter den Stand zurueck nach einer bezahlten Gebuehr gesucht
#: wird. Der Mayhem-Agent handelt oft Dutzende Male am Stueck gebuehrenfrei;
#: danach gilt die Gebuehr des Erstellerkaufs -- die Einstiegsstufe der
#: dynamischen Gebuehren, eher zu hoch als zu niedrig.
MAX_GEBUEHR_SCHRITTE = 20

#: Weicht das Produkt der Reserven um mehr als das vom Startwert ab, rechnet
#: die Kurve nicht mehr als konstantes Produkt, und jede Simulation darauf
#: waere erfunden.
MAX_PRODUKTABWEICHUNG = 0.01

VERSUCHE_JE_TAG = 3

ZEITPUNKTE = {
    "b": reg.SIGNAL_NACH,
    "c": reg.SIGNAL_NACH + reg.LATENZ,
    "e": reg.SIGNAL_NACH + reg.LATENZ + reg.HALTEDAUER + reg.LATENZ,
}


# -- 1. Starts aufzaehlen -------------------------------------------------------


def starts_des_tages(rpc, tag: date) -> list[tuple[str, int, int]]:
    """Alle erfolgreichen Signaturen der Mint-Autoritaet dieses Tages.

    Rueckgabe: (Signatur, Blockzeit, Slot).
    """
    beginn, ende = reg.tagesgrenzen(tag)

    def endet_zu_frueh(seite) -> bool:
        # Eine kurze Seite heisst "hier beginnt die Historie dieses Knotens".
        # Die Mint-Autoritaet ist viel aelter als jeder Testtag; endet ihre
        # Historie vor dem Tagesbeginn nicht, fehlt ein Stueck.
        if not seite:
            return True
        return len(seite) < 1000 and (seite[-1].get("blockTime") or 0) >= beginn

    gefunden: list[tuple[str, int, int]] = []
    vor: str | None = None
    for _ in range(MAX_SEITEN_STARTS):
        params: dict = {"limit": 1000, "commitment": "finalized"}
        if vor:
            params["before"] = vor
        seite = rpc.aufruf(
            "getSignaturesForAddress",
            [reg.MINT_AUTORITAET, params],
            unvollstaendig=endet_zu_frueh,
        )
        for eintrag in seite:
            zeit = eintrag.get("blockTime")
            if zeit is not None and beginn <= zeit < ende and eintrag.get("err") is None:
                gefunden.append((eintrag["signature"], zeit, eintrag["slot"]))
        vor = seite[-1]["signature"]
        if (seite[-1].get("blockTime") or 0) < beginn:
            return gefunden
    raise RpcFehler(f"{tag}: mehr als {MAX_SEITEN_STARTS} Seiten, Aufzaehlung abgebrochen")


def reihenfolge(starts: Iterable[tuple[str, int, int]], tag: date) -> list[tuple[str, int, int]]:
    """Die Starts in der Reihenfolge, in der sie gezogen werden.

    Erst nach Slot, Zeit und Signatur geordnet, dann mit der Saat des Tages
    gemischt: dieselbe Liste ergibt immer dieselbe Reihenfolge, gleich von
    welchem Knoten sie kam.
    """
    geordnet = sorted(set(starts), key=lambda s: (s[2], s[1], s[0]))
    random.Random(reg.saat(tag)).shuffle(geordnet)
    return geordnet


def fingerabdruck(starts: Iterable[tuple[str, int, int]]) -> str:
    """SHA-256 der sortierten Signaturen -- damit ein zweiter Lauf zeigen kann,
    dass er dieselbe Grundgesamtheit gesehen hat."""
    h = hashlib.sha256()
    for sig in sorted(s[0] for s in starts):
        h.update(sig.encode())
    return h.hexdigest()[:16]


# -- 2. Je Start die Kurvenstaende ----------------------------------------------


class _Kurve:
    """Die erfolgreichen Transaktionen einer Kurve und ihre Ereignisse, mit Cache."""

    def __init__(self, rpc, mint: str, kurve: str, create_sig: str) -> None:
        self.rpc = rpc
        self.mint = mint
        self.kurve = kurve
        self.create_sig = create_sig
        self._tx: dict[str, dict] = {}
        self.signaturen = self._signaturen()

    def _signaturen(self) -> list[tuple[str, int]]:
        """Chronologisch, ohne den Start selbst und ohne gescheiterte."""

        def endet_zu_frueh(seite) -> bool:
            if not seite:
                return True
            return len(seite) < 1000 and all(e["signature"] != self.create_sig for e in seite)

        gesammelt: list[dict] = []
        vor: str | None = None
        for _ in range(MAX_SEITEN_KURVE):
            params: dict = {"limit": 1000, "commitment": "finalized"}
            if vor:
                params["before"] = vor
            seite = self.rpc.aufruf(
                "getSignaturesForAddress", [self.kurve, params], unvollstaendig=endet_zu_frueh
            )
            gesammelt.extend(seite)
            if any(e["signature"] == self.create_sig for e in seite):
                break
            vor = seite[-1]["signature"]
        else:
            raise ValueError("zu_viele_signaturen")
        chronologisch = list(reversed(gesammelt))
        start = next(i for i, e in enumerate(chronologisch) if e["signature"] == self.create_sig)
        return [
            (e["signature"], e["blockTime"])
            for e in chronologisch[start + 1 :]
            if e.get("err") is None and e.get("blockTime") is not None
        ]

    def tx(self, sig: str) -> dict:
        if sig not in self._tx:
            self._tx[sig] = self.rpc.aufruf(
                "getTransaction", [sig, TX_OPTIONEN], null_ist_fehlend=True
            )
        return self._tx[sig]

    def zustand_bis(self, bis: int, nach_start: Zustand) -> Zustand:
        """Der Stand nach dem letzten erfolgreichen Handel mit Blockzeit <= `bis`.

        Rueckwaerts von der letzten Transaktion bis dahin, bis eine ein
        Handelsereignis dieses Tokens enthaelt. Nicht jede Transaktion an der
        Kurve ist ein Handel (Migration, Kontenpflege); die werden uebersprungen.

        **Die Gebuehr ist die eines normalen Haendlers.** Der Mayhem-Agent
        handelt gebuehrenfrei (pump.fun/docs/mayhem-mode); ist der Handel, der
        den Stand bestimmt, einer von ihm, gilt fuer uns die Gebuehr des
        letzten Handels davor, der eine zahlte.
        """
        kandidaten = [s for s in self.signaturen if s[1] <= bis]
        stand: Zustand | None = None
        stand_schritt = 0
        for schritt, (sig, zeit) in enumerate(reversed(kandidaten)):
            if stand is None and schritt >= MAX_RUECKSCHRITTE:
                raise ValueError("zustand_nicht_gefunden")
            if stand is not None and schritt - stand_schritt > MAX_GEBUEHR_SCHRITTE:
                break
            tx = self.tx(sig)
            trades = [e for e in ereignisse(tx) if isinstance(e, TradeEreignis) and e.mint == self.mint]
            if not trades:
                logs = (tx.get("meta") or {}).get("logMessages") or []
                if any("Log truncated" in zeile for zeile in logs):
                    # Ein abgeschnittenes Log kann genau den Handel
                    # verschlucken, der den Stand bestimmt. Dann lieber ein
                    # Fehler als ein falscher Stand.
                    raise ValueError("log_abgeschnitten")
                continue
            if stand is None:
                stand = Zustand.aus_trade(trades[-1], zeit, sig)
                stand_schritt = schritt
            bezahlt = [t for t in trades if t.fee_bps > 0]
            if bezahlt:
                t = bezahlt[-1]
                return stand.mit_gebuehr(t.fee_bps, t.creator_fee_bps, t.zusatz_bps)
        if stand is None:
            return nach_start
        return stand.mit_gebuehr(nach_start.fee_bps, nach_start.creator_fee_bps, nach_start.zusatz_bps)


def _pruefe_kurve(z: Zustand, k0: dict, mayhem: bool) -> None:
    """Passt der Stand zur Kurve, auf der gerechnet wird?

    Die Token-Seite muss **exakt** stimmen: verkaufte Tokens aus den
    virtuellen und aus den echten Reserven sind dieselbe Zahl. Das Produkt
    muss zum Startwert passen -- ausser bei Mayhem-Coins: dort verschiebt der
    Agent die virtuelle SOL-Basis (gemessen am 2026-09-26, Anweisung
    `set_mayhem_virtual_params` im IDL). Zwischen seinen Handeln bleibt die
    Kurve ein konstantes Produkt, und gerechnet wird ohnehin gegen den
    jeweiligen Stand, nie gegen den Startwert.
    """
    if k0["vtok"] - z.vtok != k0["rtok"] - z.rtok:
        raise ValueError("token_reserven_inkonsistent")
    soll = k0["vsol"] * k0["vtok"]
    if not mayhem and abs(z.vsol * z.vtok - soll) > MAX_PRODUKTABWEICHUNG * soll:
        raise ValueError("kurve_kein_konstantes_produkt")


KEIN_START = "kein_start"
NICHT_SOL = "nicht_sol"


def datensatz(rpc, sig: str, slot: int, tag: date) -> dict | str:
    """Ein Start der Stichprobe als Datensatz -- oder der Grund, warum er
    nicht zur Grundgesamtheit gehoert (`KEIN_START`, `NICHT_SOL`)."""
    try:
        tx = rpc.aufruf("getTransaction", [sig, TX_OPTIONEN], null_ist_fehlend=True)
    except RpcFehler as exc:
        return {"tag": tag.isoformat(), "sig": sig, "slot": slot, "fehler": f"rpc: {exc}"[:300]}
    alle = ereignisse(tx)
    creates = [e for e in alle if isinstance(e, CreateEreignis)]
    if not creates:
        return KEIN_START
    c = creates[0]
    if not c.in_sol:
        # Eine Kurve in USDC oder einer anderen Waehrung laesst sich mit einem
        # Einsatz in SOL nicht rechnen. Sie gehoert nicht zur Frage (ADR-081,
        # Nachtrag vor dem ersten Testtag).
        return NICHT_SOL
    t0 = tx["blockTime"]
    k0 = {"vsol": c.vsol, "vtok": c.vtok, "rtok": c.rtok, "supply": c.supply}
    dev = [e for e in alle if isinstance(e, TradeEreignis) and e.mint == c.mint]
    if dev:
        nach_start = Zustand.aus_trade(dev[-1], t0, sig)
    else:
        # Ohne Kauf des Erstellers steht keine Gebuehr in der Transaktion. Die
        # Simulation nimmt dann die haeufigste Anfangsgebuehr des Tages.
        nach_start = Zustand(t0, sig, c.vsol, c.vtok, 0, c.rtok, -1, -1)
    datensatz_ = {
        "tag": tag.isoformat(),
        "sig": sig,
        "slot": slot,
        "t0": t0,
        "mint": c.mint,
        "kurve": c.kurve,
        "ersteller": c.ersteller,
        "k0": k0,
        "mayhem": c.mayhem,
        "a": nach_start.als_dict(),
        "fehler": None,
    }
    try:
        kurve = _Kurve(rpc, c.mint, c.kurve, sig)
        for name, versatz in ZEITPUNKTE.items():
            z = kurve.zustand_bis(t0 + versatz, nach_start)
            _pruefe_kurve(z, k0, c.mayhem)
            datensatz_[name] = z.als_dict()
        datensatz_["tx_bis_signal"] = sum(1 for _, z in kurve.signaturen if z <= t0 + reg.SIGNAL_NACH)
        datensatz_["tx_bis_ende"] = sum(1 for _, z in kurve.signaturen if z <= t0 + ZEITPUNKTE["e"])
    except (RpcFehler, ValueError, KeyError, StopIteration) as exc:
        datensatz_["fehler"] = f"{type(exc).__name__}: {exc}"[:300]
    return datensatz_


# -- 3. Ein ganzer Tag -----------------------------------------------------------


def pfade(tag: date, verzeichnis: Path) -> tuple[Path, Path]:
    return verzeichnis / f"{tag.isoformat()}.jsonl", verzeichnis / f"{tag.isoformat()}.meta.json"


def lies_tag(tag: date, verzeichnis: Path = VERZEICHNIS) -> tuple[dict | None, list[dict]]:
    daten, meta = pfade(tag, verzeichnis)
    if not meta.exists():
        return None, []
    saetze = [json.loads(z) for z in daten.read_text().splitlines() if z.strip()] if daten.exists() else []
    return json.loads(meta.read_text()), saetze


def lies_meta(tag: date, verzeichnis: Path = VERZEICHNIS) -> dict | None:
    """Nur die Metadaten eines Tages: Zaehlungen, keine Kurvenstaende."""
    _, meta = pfade(tag, verzeichnis)
    return json.loads(meta.read_text()) if meta.exists() else None


def vollstaendig(meta: dict | None) -> bool:
    return bool(meta and meta.get("fertig") and meta.get("vollstaendig"))


#: Alle so viele Datensaetze wird der Tag zwischengespeichert. Bricht ein Lauf
#: ab (Zeitlimit des Workflows, Netz), gehen hoechstens so viele verloren.
ZWISCHENSTAND_ALLE = 50


def tag_sammeln(
    rpc,
    tag: date,
    verzeichnis: Path = VERZEICHNIS,
    echo: Callable[[str], None] | None = None,
    uhr: Callable[[], float] = _time.time,
) -> dict:
    """Einen Tag sammeln. Wiederholbar: Fehlerhafte Datensaetze eines frueheren
    Laufs werden erneut versucht, fehlerfreie bleiben stehen."""
    verzeichnis.mkdir(parents=True, exist_ok=True)
    daten_pfad, meta_pfad = pfade(tag, verzeichnis)
    alte_meta, alte_saetze = lies_tag(tag, verzeichnis)
    if vollstaendig(alte_meta):
        return alte_meta
    versuch = (alte_meta or {}).get("versuche", 0) + 1
    beginn = uhr()

    starts = starts_des_tages(rpc, tag)
    folge = reihenfolge(starts, tag)
    bisher = {s["sig"]: s for s in alte_saetze}
    saetze: list[dict] = []
    ausgeschlossen = {KEIN_START: 0, NICHT_SOL: 0}

    def meta_fuer(abschluss: bool) -> dict:
        fehlerfrei = sum(1 for s in saetze if s.get("fehler") is None)
        ist_vollstaendig = (
            abschluss
            and len(saetze) == reg.STICHPROBE_JE_TAG
            and fehlerfrei >= reg.MIN_VOLLSTAENDIG * reg.STICHPROBE_JE_TAG
        )
        return {
            "tag": tag.isoformat(),
            "starts_gesamt": len(starts),
            "starts_fingerabdruck": fingerabdruck(starts),
            "stichprobe": len(saetze),
            "fehlerfrei": fehlerfrei,
            "kein_start": ausgeschlossen[KEIN_START],
            "nicht_sol": ausgeschlossen[NICHT_SOL],
            "mayhem": sum(1 for s in saetze if s.get("mayhem")),
            "fehlerarten": _fehlerarten(saetze),
            "vollstaendig": ist_vollstaendig,
            # Unvollstaendige Tage werden erneut versucht -- nur ihre
            # fehlerhaften Saetze --, bis VERSUCHE_JE_TAG erreicht ist. Danach
            # bleibt der Tag, wie er ist, und zaehlt nicht (ADR-081). Ein
            # Zwischenstand ist nie fertig.
            "fertig": abschluss and (ist_vollstaendig or versuch >= VERSUCHE_JE_TAG),
            "versuche": versuch,
            "rpc": rpc.statistik() if hasattr(rpc, "statistik") else {},
            "dauer_s": round(uhr() - beginn, 1),
            "gesammelt_am": datetime.fromtimestamp(uhr(), tz=timezone.utc).isoformat(timespec="seconds"),
        }

    for sig, _zeit, slot in folge:
        if len(saetze) >= reg.STICHPROBE_JE_TAG:
            break
        alt = bisher.get(sig)
        if alt is not None and alt.get("fehler") is None:
            saetze.append(alt)
            continue
        satz = datensatz(rpc, sig, slot, tag)
        if isinstance(satz, str):
            ausgeschlossen[satz] += 1
            continue
        saetze.append(satz)
        if len(saetze) % ZWISCHENSTAND_ALLE == 0 and len(saetze) < reg.STICHPROBE_JE_TAG:
            _schreiben(daten_pfad, meta_pfad, saetze, meta_fuer(abschluss=False))
            if echo is not None:
                echo(f"  {tag}: {len(saetze)}/{reg.STICHPROBE_JE_TAG}")

    meta = meta_fuer(abschluss=True)
    _schreiben(daten_pfad, meta_pfad, saetze, meta)
    return meta


def _schreiben(daten_pfad: Path, meta_pfad: Path, saetze: list[dict], meta: dict) -> None:
    """Erst die Datensaetze, dann die Metadaten, beide atomar."""
    for pfad, text in (
        (daten_pfad, "".join(json.dumps(s, sort_keys=True) + "\n" for s in saetze)),
        (meta_pfad, json.dumps(meta, indent=2, sort_keys=True) + "\n"),
    ):
        tmp = pfad.with_name(pfad.name + ".tmp")
        tmp.write_text(text)
        tmp.replace(pfad)


def _fehlerarten(saetze: list[dict]) -> dict[str, int]:
    arten: dict[str, int] = {}
    for s in saetze:
        if s.get("fehler"):
            art = s["fehler"].split(":", 1)[0] if s["fehler"].startswith("rpc") else s["fehler"]
            art = art[:60]
            arten[art] = arten.get(art, 0) + 1
    return arten


def offene_tage(jetzt: datetime, verzeichnis: Path = VERZEICHNIS) -> list[date]:
    """Faellige Tage, die noch nicht fertig gesammelt sind, aeltester zuerst."""
    offen = []
    for tag in reg.alle_tage():
        if not reg.faellig(tag, jetzt):
            continue
        meta = lies_meta(tag, verzeichnis)
        if meta is None or not meta.get("fertig"):
            offen.append(tag)
    return offen
