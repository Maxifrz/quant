"""Echte Orders gegen eine Boerse -- und die Sicherungen, ohne die das fahrlaessig waere.

Dies ist das erste Modul im Projekt, das Geld ausgeben kann. Alles andere
rechnet; hier passiert etwas. Der Aufbau richtet sich danach.

## Was hier anders ist als im `SimBroker`

Die Schnittstelle sieht aehnlich aus, und das ist eine Falle. Drei
Unterschiede, die kein Aufrufer uebersehen darf:

1. **Der Fill kommt von der Boerse, nicht aus einem Modell.** Preis, Gebuehr
   und Menge stehen in ihrer Antwort. Das Kostenmodell aus ADR-056 wird hier
   **nicht** angewandt -- es war die Schaetzung, die diese Zahlen vorhersagen
   sollte, und sie jetzt daruebernzulegen hiesse, die Pruefung zu verhindern,
   fuer die der Live-Pfad da ist.
2. **Es gibt kein "naechster Bar-Open".** `SimBroker` merkt Orders vor, weil
   die Verzoegerung sein Realismusmodell ist. Eine Market-Order an einer
   echten Boerse fuellt jetzt. Die Vormerkung bleibt trotzdem erhalten,
   damit derselbe Aufrufcode funktioniert -- aber `execute_pending` schickt
   sofort.
3. **Ein Fehlschlag ist ein Zustand, kein Rueckgabewert.** Eine abgelehnte
   Order hinterlaesst ein Konto, dessen Ist-Stand nicht mehr aus dem
   Soll-Stand folgt. Deshalb wirft dieses Modul, statt still weiterzumachen,
   und deshalb gibt es `qt.live.reconcile`.

## Die zwei Schalter

Scharf ist dieser Broker nur, wenn **beides** gilt:

    CcxtBroker(..., scharf=True)          # im Aufruf
    QT_LIVE_SCHARF=ja                     # in der Umgebung

Zwei unabhaengige Schalter, weil ein einzelner zu leicht aus Versehen steht:
ein vergessener Default im Code, eine geerbte Umgebungsvariable in einer
Routine. Beides gleichzeitig passiert nicht aus Versehen. Ein unscharfer
Broker **liest** normal (Kontostand, Positionen, Marktgrenzen) und wirft bei
jedem Sendeversuch `NichtScharf` -- mit der Order im Text, damit ein
Trockenlauf zeigt, was passiert waere.

## Die Grenzen

`Limits` wird **in diesem Modul** geprueft, nicht nur oben in der Risk-Engine.
Eine Grenze, die nur an einer Stelle im Aufrufpfad steht, schuetzt genau so
lange, wie dieser Pfad der einzige ist. Verletzungen werden **geworfen, nicht
gekappt**: eine still zurechtgestutzte Order ist eine Order, die niemand so
gewollt hat, und der Kontostand danach passt zu keiner Absicht.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from qt.core.types import Fill, Order, Position

__all__ = [
    "CcxtBroker",
    "LimitVerletzt",
    "Limits",
    "NichtScharf",
    "OrderAbgelehnt",
    "Zugang",
]

#: Der zweite Schalter. Der Wert muss genau so lauten -- "1" oder "true"
#: waeren zu leicht versehentlich gesetzt.
SCHARF_UMGEBUNG = "QT_LIVE_SCHARF"
SCHARF_WERT = "ja"


class NichtScharf(RuntimeError):
    """Es sollte gesendet werden, aber der Broker ist entschaerft."""


class LimitVerletzt(RuntimeError):
    """Eine Order haette eine harte Grenze ueberschritten."""


class OrderAbgelehnt(RuntimeError):
    """Die Boerse hat die Order nicht angenommen."""


@dataclass(frozen=True)
class Zugang:
    """Boersenschluessel aus der Umgebung.

    `repr` ist ueberschrieben und die Felder stehen nicht darin. Ein
    Traceback, eine Log-Zeile oder ein `print` des Brokers soll den Schluessel
    nicht mitnehmen -- das sind die drei Wege, auf denen so etwas in ein
    Repository gelangt.
    """

    key: str = field(repr=False)
    secret: str = field(repr=False)
    password: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:  # pragma: no cover -- triviale Maskierung
        return f"Zugang(key=***{self.key[-4:] if self.key else ''}, secret=***)"

    @classmethod
    def aus_umgebung(
        cls,
        key_var: str = "QT_EXCHANGE_API_KEY",
        secret_var: str = "QT_EXCHANGE_SECRET",
        password_var: str = "QT_EXCHANGE_PASSWORD",
    ) -> Zugang:
        key, secret = os.environ.get(key_var), os.environ.get(secret_var)
        if not key or not secret:
            fehlt = [v for v, w in ((key_var, key), (secret_var, secret)) if not w]
            raise RuntimeError(
                f"Kein Boersenzugang: {', '.join(fehlt)} ist nicht gesetzt. "
                "Die Schluessel gehoeren in die Umgebung, nicht in eine Datei "
                "im Repository."
            )
        return cls(key=key, secret=secret, password=os.environ.get(password_var))


@dataclass(frozen=True, slots=True)
class Limits:
    """Harte Obergrenzen, unabhaengig von allem, was die Strategie will.

    Die Defaults sind absichtlich klein. Wer mit echtem Geld anfaengt, soll
    die Zahl bewusst hochsetzen muessen -- ein vergessener Default darf nicht
    teuer sein (ZIEL.md Phase E: "Startkapital so klein, dass ein
    Totalverlust folgenlos ist").
    """

    max_order_gegenwert: float = 100.0
    max_position_gegenwert: float = 500.0
    max_brutto_gegenwert: float = 1000.0
    erlaubte_symbole: frozenset[str] = frozenset()
    max_orders_je_tick: int = 4

    def pruefe_symbol(self, symbol: str) -> None:
        if self.erlaubte_symbole and symbol not in self.erlaubte_symbole:
            raise LimitVerletzt(
                f"{symbol} steht nicht auf der Liste erlaubter Symbole "
                f"({', '.join(sorted(self.erlaubte_symbole))})."
            )


class CcxtBroker:
    """Kontofuehrung gegen eine echte Boerse.

    `exchange` ist ein ccxt-Exchange oder irgendein Objekt mit derselben
    Teilmenge (`create_order`, `fetch_balance`, `fetch_ticker`, `markets`).
    Injiziert statt hier gebaut, damit die Tests ohne Netz auskommen -- und
    damit niemand versehentlich einen echten Zugang erwischt, weil ein
    Default ihn sich selbst besorgt hat.
    """

    def __init__(
        self,
        exchange: Any,
        limits: Limits | None = None,
        scharf: bool = False,
        umgebung: dict[str, str] | None = None,
    ) -> None:
        self.exchange = exchange
        self.limits = limits or Limits()
        self._scharf_im_aufruf = bool(scharf)
        self._umgebung = os.environ if umgebung is None else umgebung
        self._pending: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self.orders_dieser_tick = 0

    # ------------------------------------------------------------------
    # Scharf oder nicht
    # ------------------------------------------------------------------

    @property
    def scharf(self) -> bool:
        """Beide Schalter, mit `and` verknuepft."""
        return self._scharf_im_aufruf and (
            self._umgebung.get(SCHARF_UMGEBUNG) == SCHARF_WERT
        )

    def warum_nicht_scharf(self) -> str:
        """Welcher der beiden Schalter fehlt -- als Text fuer den Bericht."""
        fehlt = []
        if not self._scharf_im_aufruf:
            fehlt.append("scharf=True im Aufruf")
        if self._umgebung.get(SCHARF_UMGEBUNG) != SCHARF_WERT:
            fehlt.append(f"{SCHARF_UMGEBUNG}={SCHARF_WERT} in der Umgebung")
        return " und ".join(fehlt)

    # ------------------------------------------------------------------
    # Orderfluss
    # ------------------------------------------------------------------

    def submit(self, order: Order) -> None:
        """Order vormerken. Ersetzt eine bestehende desselben Symbols (ADR-010)."""
        if order.qty == 0:
            return
        self.limits.pruefe_symbol(order.symbol)
        self._pending[order.symbol] = order

    def has_pending(self) -> bool:
        return bool(self._pending)

    def execute_pending(self, symbol: str, preis: float) -> list[Fill]:
        """Die vorgemerkte Order **jetzt** an die Boerse geben.

        `preis` dient nur der Grenzpruefung vorab; ausgefuehrt wird zum Kurs
        der Boerse, und der steht erst in ihrer Antwort.
        """
        order = self._pending.pop(symbol, None)
        if order is None:
            return []

        self._pruefe_grenzen(order, preis)

        if not self.scharf:
            raise NichtScharf(
                f"Entschaerft, es fehlt: {self.warum_nicht_scharf()}.\n"
                f"Gesendet worden waere: {order.side.name} {abs(order.qty):.8f} "
                f"{order.symbol} (Gegenwert rund {abs(order.qty) * preis:,.2f}), "
                f"Grund: {order.reason or 'kein Grund vermerkt'}"
            )

        antwort = self._sende(order)
        fill = self._fill_aus_antwort(order, antwort)
        self.fills.append(fill)
        self.orders_dieser_tick += 1
        return [fill]

    def _pruefe_grenzen(self, order: Order, preis: float) -> None:
        self.limits.pruefe_symbol(order.symbol)

        if self.orders_dieser_tick >= self.limits.max_orders_je_tick:
            raise LimitVerletzt(
                f"Schon {self.orders_dieser_tick} Orders in diesem Tick, "
                f"erlaubt sind {self.limits.max_orders_je_tick}. Eine Strategie, "
                "die haeufiger handeln will, hat einen Fehler oder ein "
                "Kostenproblem -- in beiden Faellen ist Anhalten richtig."
            )

        gegenwert = abs(order.qty) * preis
        if gegenwert > self.limits.max_order_gegenwert:
            raise LimitVerletzt(
                f"Order ueber {gegenwert:,.2f} ueberschreitet das Ordermaximum "
                f"von {self.limits.max_order_gegenwert:,.2f}."
            )

        nachher = abs(self.qty(order.symbol) + order.qty) * preis
        if nachher > self.limits.max_position_gegenwert:
            raise LimitVerletzt(
                f"Position in {order.symbol} laege danach bei {nachher:,.2f}, "
                f"erlaubt sind {self.limits.max_position_gegenwert:,.2f}."
            )

    def _sende(self, order: Order) -> dict:
        seite = "buy" if order.qty > 0 else "sell"
        try:
            return self.exchange.create_order(
                order.symbol, "market", seite, abs(order.qty)
            )
        except Exception as exc:  # noqa: BLE001 -- jede Boersenantwort ist moeglich
            raise OrderAbgelehnt(
                f"Die Boerse hat {seite} {abs(order.qty):.8f} {order.symbol} "
                f"nicht angenommen: {type(exc).__name__}: {exc}\n"
                "Der Kontostand kann jetzt von der Erwartung abweichen -- "
                "vor dem naechsten Tick `qt live reconcile` laufen lassen."
            ) from exc

    @staticmethod
    def _fill_aus_antwort(order: Order, antwort: dict) -> Fill:
        """Fill aus der Boersenantwort, ohne Schaetzung.

        Fehlt der Durchschnittspreis, ist das **kein** Anlass, den letzten
        bekannten Kurs einzusetzen: dann steht im Konto eine Zahl, die nicht
        von der Boerse kommt und trotzdem so aussieht.
        """
        preis = antwort.get("average") or antwort.get("price")
        menge = antwort.get("filled")
        if preis is None or menge is None:
            raise OrderAbgelehnt(
                f"Die Antwort der Boerse zu {order.symbol} nennt weder "
                f"Fuellmenge noch Durchschnittspreis: {antwort!r}. "
                "Ohne beides laesst sich der Kontostand nicht fortschreiben."
            )

        gebuehr = 0.0
        fee = antwort.get("fee") or {}
        if isinstance(fee, dict) and fee.get("cost") is not None:
            gebuehr = float(fee["cost"])

        ts = antwort.get("timestamp")
        zeit = (
            datetime.fromtimestamp(ts / 1000, timezone.utc)
            if ts
            else datetime.now(timezone.utc)
        )
        return Fill(
            symbol=order.symbol,
            ts=zeit,
            qty=float(menge) * (1 if order.qty > 0 else -1),
            price=float(preis),
            fee=gebuehr,
            # Der Schlupf gegen den erwarteten Kurs ist eine Groesse des
            # Abgleichs, nicht des Fills -- hier stuende sonst eine Zahl aus
            # zwei Quellen (ADR-059).
            slippage_cost=0.0,
        )

    # ------------------------------------------------------------------
    # Lesen -- geht auch entschaerft
    # ------------------------------------------------------------------

    def positionen(self) -> dict[str, Position]:
        """Positionen aus dem Guthaben der Boerse.

        Am Spotmarkt gibt es keine Positionen, sondern Guthaben je Waehrung.
        Der Einstandspreis ist von hier aus **nicht** feststellbar; er bleibt
        deshalb 0.0 statt geraten zu werden. Wer ihn braucht, nimmt den
        lokalen Zustand -- und genau die Differenz zwischen beiden Quellen
        ist der Gegenstand von `qt.live.reconcile`.
        """
        balance = self.exchange.fetch_balance()
        gesamt = balance.get("total") or {}
        out: dict[str, Position] = {}
        for symbol in self._symbole():
            basis = symbol.split("/")[0]
            menge = float(gesamt.get(basis) or 0.0)
            if menge != 0.0:
                out[symbol] = Position(symbol=symbol, qty=menge, avg_price=0.0)
        return out

    def guthaben(self, waehrung: str = "USD") -> float:
        balance = self.exchange.fetch_balance()
        return float((balance.get("total") or {}).get(waehrung) or 0.0)

    def qty(self, symbol: str) -> float:
        return self.positionen().get(symbol, Position(symbol)).qty

    def _symbole(self) -> list[str]:
        if self.limits.erlaubte_symbole:
            return sorted(self.limits.erlaubte_symbole)
        maerkte = getattr(self.exchange, "markets", None) or {}
        return sorted(maerkte)
