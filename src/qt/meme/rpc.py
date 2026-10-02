"""Oeffentliche Solana-RPC, gedrosselt und mit Rueckfall.

Gemessen am 2026-09-26, nicht angenommen:

* **Publicnode** liefert rund 5 Abfragen pro Sekunde ohne Drosselung, haelt
  aber nur kurz Historie vor: am 2026-09-26 rund 40 Stunden, am 2026-10-02
  nur noch 20. Wo sie endet, antwortet ein Knoten mit einer kurzen Seite, ein
  anderer mit Fehler -32020 ("Transaction ... not found").
* **api.mainnet-beta.solana.com** hat die ganze Historie, drosselt aber schon
  nach wenigen Abfragen (HTTP 429) und schafft dann 0,7 pro Sekunde.
* Publicnode sperrt den Standard-User-Agent von Python (Cloudflare 1010).
  Ein eigener geht.

Also: Publicnode zuerst, und wenn die Daten dort schon geloescht sind, die
langsame Adresse. Ein Tag, der einmal verpasst wurde, ist damit nicht
verloren, sondern nur langsam.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

USER_AGENT = "qt-meme-papiertest/0.1 (+https://github.com/Maxifrz/quant)"

@dataclass(slots=True)
class Endpunkt:
    url: str
    min_abstand: float
    letzter: float = 0.0
    aufrufe: int = 0
    gedrosselt: int = 0


def standard_endpunkte() -> list[Endpunkt]:
    return [
        Endpunkt("https://solana-rpc.publicnode.com", 0.25),
        Endpunkt("https://api.mainnet-beta.solana.com", 1.25),
    ]


class RpcFehler(RuntimeError):
    """Kein Endpunkt konnte die Anfrage beantworten."""


class _NichtDa(Exception):
    pass


@dataclass
class SolanaRpc:
    endpunkte: list[Endpunkt] = field(default_factory=standard_endpunkte)
    versuche: int = 6
    schlafen: Callable[[float], None] = time.sleep
    uhr: Callable[[], float] = time.monotonic
    senden: Callable[[str, bytes], dict] | None = None

    def aufruf(
        self,
        methode: str,
        params: list,
        *,
        null_ist_fehlend: bool = False,
        unvollstaendig: Callable[[Any], bool] | None = None,
    ) -> Any:
        """Erst der schnelle Knoten, dann der vollstaendige.

        `null_ist_fehlend`: bei `getTransaction` heisst `null` auf einem
        beschnittenen Knoten "nicht mehr da", nicht "gibt es nicht".

        `unvollstaendig`: prueft eine Antwort, die formal gueltig ist, aber
        erkennbar zu frueh aufhoert -- eine Signaturseite, die endet, bevor der
        gesuchte Zeitpunkt erreicht ist. Ein beschnittener Knoten meldet dafuer
        keinen Fehler, er liefert einfach weniger. Ohne diese Pruefung saehe
        ein halber Tag aus wie ein ganzer.
        """
        letzter_fehler: Exception | None = None
        for endpunkt in self.endpunkte:
            try:
                ergebnis = self._an(endpunkt, methode, params)
            except _NichtDa as exc:
                letzter_fehler = exc
                continue
            if ergebnis is None and null_ist_fehlend:
                letzter_fehler = _NichtDa(f"{endpunkt.url}: null")
                continue
            if unvollstaendig is not None and unvollstaendig(ergebnis):
                letzter_fehler = _NichtDa(f"{endpunkt.url}: Antwort endet zu frueh")
                continue
            return ergebnis
        raise RpcFehler(f"{methode}: kein Endpunkt hatte die Daten ({letzter_fehler})")

    def _an(self, endpunkt: Endpunkt, methode: str, params: list) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": methode, "params": params}).encode()
        for versuch in range(self.versuche):
            warten = endpunkt.min_abstand - (self.uhr() - endpunkt.letzter)
            if warten > 0:
                self.schlafen(warten)
            endpunkt.letzter = self.uhr()
            endpunkt.aufrufe += 1
            try:
                antwort = (self.senden or _senden)(endpunkt.url, body)
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504):
                    endpunkt.gedrosselt += 1
                    self.schlafen(min(2.0**versuch, 30.0))
                    continue
                raise _NichtDa(f"{endpunkt.url}: HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                self.schlafen(min(2.0**versuch, 30.0))
                if versuch == self.versuche - 1:
                    raise _NichtDa(f"{endpunkt.url}: {exc}") from exc
                continue
            if "error" in antwort:
                # Jeder JSON-RPC-Fehler heisst erst einmal "dieser Knoten kann
                # das nicht", und der naechste wird gefragt. Bis 2026-10-01
                # stand hier eine Liste bekannter Codes; dann antwortete
                # Publicnode auf einen Cursor jenseits seiner Historie mit
                # -32020, und die Sammlung brach drei Laeufe lang ab, obwohl
                # die offizielle Adresse die Daten hatte (ADR-081). Scheitern
                # alle Knoten, meldet `aufruf` den letzten Fehler.
                raise _NichtDa(f"{endpunkt.url}: {antwort['error']}")
            return antwort.get("result")
        raise _NichtDa(f"{endpunkt.url}: {self.versuche} Versuche gedrosselt")

    def statistik(self) -> dict[str, dict[str, int]]:
        return {e.url: {"aufrufe": e.aufrufe, "gedrosselt": e.gedrosselt} for e in self.endpunkte}


def _senden(url: str, body: bytes) -> dict:
    anfrage = urllib.request.Request(
        url, body, {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(anfrage, timeout=60) as antwort:
        return json.load(antwort)
