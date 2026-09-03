"""Tests des Live-Pfads (ADR-062).

Zwei Tests tragen hier alles andere:

`test_entschaerft_wird_nichts_gesendet` ist die Sicherung. Faellt er, kann ein
Lauf ohne beide Schalter Geld ausgeben -- und zwar genau dann, wenn niemand
damit rechnet.

`test_bei_minimalkapital_faellt_der_trade_aus_statt_kleiner_zu_werden` ist der
Verhaltensunterschied, den der Backtest nicht kennt: unterhalb der
Mindestordergroesse faellt eine Anpassung **ganz** aus, statt kleiner zu
werden. Der Test daneben haelt fest, wie stark das an Coinbases gemessenen
Grenzen wirklich ist -- naemlich erst unter rund 100 USD Kontogroesse. Beide
gehoeren zusammen: der erste zeigt den Mechanismus, der zweite verhindert,
dass daraus eine zu allgemeine Behauptung wird.
"""

from __future__ import annotations

import pytest

from qt.core.types import Order
from qt.live.broker_ccxt import (
    CcxtBroker,
    Limits,
    LimitVerletzt,
    NichtScharf,
    OrderAbgelehnt,
    Zugang,
)
from qt.live.reconcile import reconcile
from qt.live.sizing import Marktgrenzen, menge_fuer_zielgewicht

SCHARF = {"QT_LIVE_SCHARF": "ja"}


class FakeExchange:
    """Boerse, die nichts tut und mitschreibt, was von ihr verlangt wurde.

    Der Punkt ist das Mitschreiben: bei einer Sicherung reicht es nicht zu
    pruefen, dass ein Fehler geworfen wurde -- es muss auch nichts gesendet
    worden sein.
    """

    def __init__(self, antwort: dict | None = None, wirft: Exception | None = None):
        self.aufrufe: list[tuple] = []
        self.antwort = antwort or {
            "average": 100.0,
            "filled": 1.0,
            "fee": {"cost": 0.6},
            "timestamp": 1_700_000_000_000,
        }
        self.wirft = wirft
        self.markets = {"BTC/USD": {}, "ETH/USD": {}}
        self.balances = {"total": {"BTC": 0.0, "ETH": 0.0, "USD": 500.0}}

    def create_order(self, symbol, typ, seite, menge):
        self.aufrufe.append((symbol, typ, seite, menge))
        if self.wirft:
            raise self.wirft
        return self.antwort

    def fetch_balance(self):
        return self.balances


# ---------------------------------------------------------------------------
# Die Sicherung
# ---------------------------------------------------------------------------


def test_entschaerft_wird_nichts_gesendet():
    """Der wichtigste Test der Datei."""
    exchange = FakeExchange()
    broker = CcxtBroker(exchange, umgebung=SCHARF)  # scharf=False im Aufruf
    broker.submit(Order("BTC/USD", 0.5, reason="target_weight=1.0"))

    with pytest.raises(NichtScharf) as exc:
        broker.execute_pending("BTC/USD", preis=100.0)

    assert exchange.aufrufe == [], "entschaerft darf nichts an der Boerse ankommen"
    assert "scharf=True im Aufruf" in str(exc.value)
    assert "0.5" in str(exc.value) and "BTC/USD" in str(exc.value), (
        "ein Trockenlauf soll zeigen, was passiert waere"
    )


@pytest.mark.parametrize(
    "im_aufruf,umgebung",
    [(True, {}), (False, SCHARF), (True, {"QT_LIVE_SCHARF": "1"})],
)
def test_ein_schalter_allein_reicht_nicht(im_aufruf, umgebung):
    """Beide, mit `and`. Ein vergessener Default darf nicht genuegen.

    Der dritte Fall ist der wichtigste: `QT_LIVE_SCHARF=1` sieht aus wie ein
    gesetzter Schalter und ist keiner. "1" und "true" stehen zu leicht
    versehentlich irgendwo -- verlangt ist genau "ja".
    """
    exchange = FakeExchange()
    broker = CcxtBroker(exchange, scharf=im_aufruf, umgebung=umgebung)
    broker.submit(Order("BTC/USD", 0.5))

    with pytest.raises(NichtScharf):
        broker.execute_pending("BTC/USD", preis=100.0)
    assert exchange.aufrufe == []


def test_mit_beiden_schaltern_wird_gesendet():
    exchange = FakeExchange()
    broker = CcxtBroker(exchange, scharf=True, umgebung=SCHARF)
    broker.submit(Order("BTC/USD", 0.5))

    fills = broker.execute_pending("BTC/USD", preis=100.0)

    assert exchange.aufrufe == [("BTC/USD", "market", "buy", 0.5)]
    assert len(fills) == 1 and fills[0].price == 100.0


# ---------------------------------------------------------------------------
# Die harten Grenzen
# ---------------------------------------------------------------------------


def test_eine_zu_grosse_order_wird_geworfen_und_nicht_gekappt():
    """Still zurechtgestutzt waere eine Order, die niemand so gewollt hat."""
    exchange = FakeExchange()
    broker = CcxtBroker(
        exchange,
        limits=Limits(max_order_gegenwert=100.0),
        scharf=True,
        umgebung=SCHARF,
    )
    broker.submit(Order("BTC/USD", 5.0))  # 5 * 100 = 500 > 100

    with pytest.raises(LimitVerletzt, match="Ordermaximum"):
        broker.execute_pending("BTC/USD", preis=100.0)
    assert exchange.aufrufe == []


def test_die_grenze_gilt_fuer_die_position_nach_der_order():
    exchange = FakeExchange()
    exchange.balances = {"total": {"BTC": 4.0, "USD": 500.0}}
    broker = CcxtBroker(
        exchange,
        limits=Limits(
            max_order_gegenwert=1000.0,
            max_position_gegenwert=450.0,
            erlaubte_symbole=frozenset({"BTC/USD"}),
        ),
        scharf=True,
        umgebung=SCHARF,
    )
    broker.submit(Order("BTC/USD", 1.0))  # danach 5 * 100 = 500 > 450

    with pytest.raises(LimitVerletzt, match="laege danach"):
        broker.execute_pending("BTC/USD", preis=100.0)
    assert exchange.aufrufe == []


def test_ein_fremdes_symbol_kommt_nicht_einmal_in_die_vormerkung():
    broker = CcxtBroker(
        FakeExchange(), limits=Limits(erlaubte_symbole=frozenset({"BTC/USD"}))
    )
    with pytest.raises(LimitVerletzt, match="erlaubter Symbole"):
        broker.submit(Order("DOGE/USD", 1.0))


def test_die_zahl_der_orders_je_tick_ist_begrenzt():
    """Eine Strategie, die haeufiger handeln will, hat einen Fehler."""
    exchange = FakeExchange()
    broker = CcxtBroker(
        exchange,
        limits=Limits(max_orders_je_tick=2, max_position_gegenwert=1e9),
        scharf=True,
        umgebung=SCHARF,
    )
    for i in range(2):
        broker.submit(Order("BTC/USD", 0.1))
        broker.execute_pending("BTC/USD", preis=100.0)

    broker.submit(Order("BTC/USD", 0.1))
    with pytest.raises(LimitVerletzt, match="Orders in diesem Tick"):
        broker.execute_pending("BTC/USD", preis=100.0)
    assert len(exchange.aufrufe) == 2


# ---------------------------------------------------------------------------
# Was die Boerse antwortet
# ---------------------------------------------------------------------------


def test_eine_abgelehnte_order_verweist_auf_den_abgleich():
    """Nach einer abgelehnten Order folgt der Ist-Stand nicht mehr aus dem Soll."""
    exchange = FakeExchange(wirft=ValueError("insufficient funds"))
    broker = CcxtBroker(exchange, scharf=True, umgebung=SCHARF)
    broker.submit(Order("BTC/USD", 0.5))

    with pytest.raises(OrderAbgelehnt, match="reconcile"):
        broker.execute_pending("BTC/USD", preis=100.0)


def test_eine_antwort_ohne_preis_wird_nicht_geraten():
    """Der letzte bekannte Kurs waere eine Zahl, die nicht von der Boerse kommt."""
    exchange = FakeExchange(antwort={"filled": 1.0})
    broker = CcxtBroker(exchange, scharf=True, umgebung=SCHARF)
    broker.submit(Order("BTC/USD", 1.0))

    with pytest.raises(OrderAbgelehnt, match="Durchschnittspreis"):
        broker.execute_pending("BTC/USD", preis=100.0)


def test_der_fill_kommt_aus_der_antwort_und_nicht_aus_dem_kostenmodell():
    """Das Kostenmodell war die Schaetzung, die hier geprueft werden soll."""
    exchange = FakeExchange(
        antwort={"average": 77_437.4, "filled": 0.006, "fee": {"cost": 0.6}}
    )
    broker = CcxtBroker(exchange, scharf=True, umgebung=SCHARF)
    broker.submit(Order("BTC/USD", 0.006))

    fill = broker.execute_pending("BTC/USD", preis=1.0)[0]

    assert fill.price == 77_437.4
    assert fill.fee == 0.6
    assert fill.slippage_cost == 0.0, (
        "Schlupf ist eine Groesse des Abgleichs, nicht des Fills"
    )


def test_eine_vormerkung_ersetzt_die_vorige(monkeypatch):
    """ADR-010: zwei Vormerkungen sind zwei Schaetzungen derselben Absicht."""
    exchange = FakeExchange()
    broker = CcxtBroker(exchange, scharf=True, umgebung=SCHARF)
    broker.submit(Order("BTC/USD", 0.1))
    broker.submit(Order("BTC/USD", 0.2))
    broker.execute_pending("BTC/USD", preis=100.0)

    assert exchange.aufrufe == [("BTC/USD", "market", "buy", 0.2)]


def test_der_zugang_zeigt_den_schluessel_nicht_im_repr():
    """Traceback, Log und print sind die drei Wege ins Repository."""
    zugang = Zugang(key="abcdef123456", secret="streng-geheim")
    text = repr(zugang)
    assert "streng-geheim" not in text
    assert "abcdef123456" not in text


def test_fehlender_zugang_nennt_die_variable():
    with pytest.raises(RuntimeError, match="QT_EXCHANGE_API_KEY"):
        Zugang.aus_umgebung(key_var="QT_EXCHANGE_API_KEY_GIBT_ES_NICHT")


# ---------------------------------------------------------------------------
# Ordergroessen bei echtem Geld
# ---------------------------------------------------------------------------


def test_bei_minimalkapital_faellt_der_trade_aus_statt_kleiner_zu_werden():
    """Der Verhaltensunterschied, den der Backtest nicht kennt.

    Dieselbe Strategie, dasselbe Signal, derselbe Preis -- nur das Kapital
    unterscheidet sich. Ueber der Grenze wird gehandelt, darunter **gar
    nicht**, nicht etwa ungenauer. Die Mindestordergroesse wirkt damit wie ein
    zweites Rebalancing-Band, das kein Backtest modelliert.

    Die Grenzen hier sind zur Veranschaulichung gewaehlt, nicht gemessen --
    wie stark der Effekt an einer echten Boerse ist, haelt der Test darunter
    fest.
    """
    grenzen = Marktgrenzen(min_menge=0.0001, min_gegenwert=1.0, schritt=1e-8)
    preis = 77_437.40

    gross = menge_fuer_zielgewicht(0.02, 0.0, preis, 1_000_000.0, grenzen)
    klein = menge_fuer_zielgewicht(0.02, 0.0, preis, 100.0, grenzen)

    assert gross.handelt
    assert not klein.handelt
    assert "Mindestmenge" in klein.grund, (
        "der ausgelassene Trade braucht einen Grund im Report, keine stille Null"
    )


def test_an_coinbases_gemessenen_grenzen_bindet_das_erst_unter_hundert_dollar():
    """Der Effekt ist echt und **klein** -- gemessen statt behauptet.

    Abgefragt am 2026-09-03 ueber `qt live groesse` gegen
    api.exchange.coinbase.com, BTC/USD: **keine** Mindestmenge, ein
    Mindestgegenwert von 1 USD, Mengenraster 1e-8.

    Damit faellt eine Anpassung erst aus, wenn ihr Gegenwert unter einem
    Dollar liegt -- bei einem Prozent Rebalancing-Band also unterhalb von rund
    100 USD Kontogroesse. Die allgemeine Behauptung "Minimalkapital handelt
    eine andere Strategie" waere zu stark; richtig ist sie erst unterhalb
    dieser Schwelle. Derselbe Fehler wie in ADR-056, wo ein Satz ueber 4h
    ueberall zitiert wurde, als gaelte er allgemein.
    """
    coinbase_btc = Marktgrenzen(min_menge=None, min_gegenwert=1.0, schritt=1e-8)
    preis = 77_910.54

    # Ein Prozent nachziehen, bei drei Kontogroessen.
    assert menge_fuer_zielgewicht(0.01, 0.0, preis, 50.0, coinbase_btc).grund != ""
    assert menge_fuer_zielgewicht(0.01, 0.0, preis, 500.0, coinbase_btc).handelt
    assert menge_fuer_zielgewicht(0.01, 0.0, preis, 100_000.0, coinbase_btc).handelt


def test_die_menge_wird_abgerundet_und_nie_auf():
    """Aufgerundet ueberschreitet eine Order die Grenze, gegen die sie prueft."""
    grenzen = Marktgrenzen(schritt=0.01)
    ergebnis = menge_fuer_zielgewicht(1.0, 0.0, 100.0, 1_239.0, grenzen)
    assert ergebnis.menge == pytest.approx(12.39)

    ergebnis = menge_fuer_zielgewicht(1.0, 0.0, 100.0, 1_239.9, grenzen)
    assert ergebnis.menge == pytest.approx(12.39), "12.399 darf nicht 12.40 werden"


def test_eine_verkaufsorder_wird_durch_rundung_nicht_groesser():
    """Sonst schliesst sie mehr, als da ist, und eroeffnet eine Gegenposition."""
    grenzen = Marktgrenzen(schritt=0.01)
    ergebnis = menge_fuer_zielgewicht(0.0, 12.399, 100.0, 1_239.9, grenzen)
    assert ergebnis.menge == pytest.approx(-12.39)
    assert abs(ergebnis.menge) <= 12.399


def test_der_mindestgegenwert_greift_auch_wenn_die_menge_reicht():
    grenzen = Marktgrenzen(min_menge=0.000001, min_gegenwert=10.0, schritt=1e-8)
    ergebnis = menge_fuer_zielgewicht(1.0, 0.0, 1.0, 5.0, grenzen)
    assert not ergebnis.handelt
    assert "Mindestgegenwert" in ergebnis.grund


def test_das_band_steht_vor_den_boersengrenzen():
    """Eine Entscheidung der Strategie, keine der Boerse -- und der Grund sagt es."""
    grenzen = Marktgrenzen(min_menge=1e-8, schritt=1e-8)
    ergebnis = menge_fuer_zielgewicht(0.005, 0.0, 100.0, 10_000.0, grenzen, band=0.01)
    assert not ergebnis.handelt
    assert "Rebalancing-Band" in ergebnis.grund


@pytest.mark.parametrize("praezision,erwartet", [(4, 1e-4), (0.0001, 1e-4), (8, 1e-8)])
def test_ccxt_meldet_praezision_auf_zwei_arten(praezision, erwartet):
    """Nachkommastellen **oder** Schrittweite -- beides kommt vor."""
    grenzen = Marktgrenzen.aus_ccxt({"precision": {"amount": praezision}})
    assert grenzen.schritt == pytest.approx(erwartet)


def test_eine_fehlende_grenze_ist_keine_grenze_null():
    """`None` heisst "nicht gemeldet", nicht "alles verboten"."""
    grenzen = Marktgrenzen.aus_ccxt({})
    ergebnis = menge_fuer_zielgewicht(1.0, 0.0, 100.0, 1_000.0, grenzen)
    assert ergebnis.menge == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Der Abgleich
# ---------------------------------------------------------------------------


def test_der_abgleich_findet_eine_position_die_es_lokal_nicht_gibt():
    """Der gefaehrlichste Fall: eine Order kam durch, galt aber als abgelehnt."""
    bericht = reconcile(soll_positionen={}, ist_positionen={"BTC/USD": 0.3})
    assert not bericht.ok
    assert [a.symbol for a in bericht.auffaellig] == ["BTC/USD"]


def test_soll_null_gegen_ist_ungleich_null_teilt_nicht_durch_null():
    bericht = reconcile({"BTC/USD": 0.0}, {"BTC/USD": 0.3})
    assert bericht.positionen[0].relativ == pytest.approx(1.0)
    assert not bericht.ok


def test_kleine_rundungsdifferenzen_bleiben_in_ordnung():
    """Sonst waere der Abgleich nach einer Woche abgeschaltet."""
    bericht = reconcile({"BTC/USD": 1.0}, {"BTC/USD": 1.0000005})
    assert bericht.ok
    assert "In Ordnung" in bericht.table()


def test_der_bericht_nennt_die_zahlen_auch_wenn_alles_stimmt():
    """Eine Toleranz ist der Ort, an dem ein echter Fehler verschwindet."""
    bericht = reconcile({"BTC/USD": 1.0}, {"BTC/USD": 1.0000005})
    text = bericht.table()
    assert "1.00000000" in text and "1.00000050" in text


def test_es_gibt_keine_funktion_die_den_zustand_nachzieht():
    """Der fehlende Befehl ist die Sicherung -- wie bei der Promotion (ADR-032).

    Eine Abweichung heisst, dass eine Annahme falsch war. Wer den lokalen
    Zustand auf den Boersenstand zieht, loescht die Spur und faehrt mit
    demselben Fehler weiter, nur unsichtbar.
    """
    import qt.live.reconcile as modul

    verboten = {"angleichen", "sync", "uebernehmen", "fix", "repair", "apply"}
    vorhanden = {name for name in dir(modul) if not name.startswith("_")}
    assert not (vorhanden & verboten), (
        f"Diese Namen duerfen hier nicht entstehen: {vorhanden & verboten}"
    )
