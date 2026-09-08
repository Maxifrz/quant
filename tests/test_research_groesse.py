"""Die Positionsgroessen-Schicht fuer generierte Kandidaten (ADR-069).

ADR-065 hat den Zustand so beschrieben: ueber 38 Maerkte summiert sich das
Bruttoexposure auf 38x und ruiniert das Konto, bei Normierung auf 1 handelt
keiner mehr -- und dazwischen liege keine Einstellung, die das Ergebnis der
*Idee* zeigen wuerde. Diese Tests halten fest, dass es sie doch gibt und
woraus sie besteht: Skalierung **und** ein Band, das die Positionsgroesse
kennt.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from qt.backtest.engine import run_backtest
from qt.backtest.metrics import compute
from qt.core.config import BacktestConfig
from qt.core.types import Bar
from qt.features.registry import FeatureStore
from qt.research.groesse import BruttoNormiert, mit_groessenschicht, normiere
from qt.strategy.base import Strategy

T0 = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _bars(symbol: str, n: int, start: float = 100.0, drift: float = 1.001):
    """Sanft steigende Reihe. Kein Rauschen -- gemessen wird die Groesse,
    nicht die Prognose."""
    preise = [start * drift**i for i in range(n)]
    return [
        Bar(symbol, "1d", T0 + timedelta(days=i), p, p, p, p, 1e9)
        for i, p in enumerate(preise)
    ]


class ImmerLong(Strategy):
    """Der typische Loop-Kandidat: je Markt unabhaengig, ohne Blick aufs Konto."""

    name = "immer_long"

    @property
    def warmup_bars(self) -> int:
        return 2

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        return 1.0


# ---------------------------------------------------------------------------
# Die Regel selbst
# ---------------------------------------------------------------------------


def test_unter_der_grenze_wird_nichts_angefasst():
    """Ein Kandidat im Budget darf von der Schicht nichts merken.

    Sonst waere sie eine Groessensteuerung und keine Grenze -- und jede
    dokumentierte Zahl haenge daran, ob sie an war.
    """
    roh = {"A": 0.3, "B": -0.4, "C": 0.0}
    assert normiere(roh, 1.0) == roh


def test_ueber_der_grenze_bleiben_die_verhaeltnisse_stehen():
    """Skaliert wird die Summe, nicht die Meinung.

    Das unterscheidet die proportionale Skalierung von Vol-Targeting: die
    Reihenfolge und das Verhaeltnis der Gewichte kommen aus dem Signal und
    duerfen es nicht verlassen.
    """
    skaliert = normiere({"A": 3.0, "B": -1.0}, 1.0)

    assert sum(abs(w) for w in skaliert.values()) == pytest.approx(1.0)
    assert skaliert["A"] / skaliert["B"] == pytest.approx(3.0 / -1.0)


def test_ein_einziger_markt_bekommt_das_volle_gewicht():
    """Selektivitaet wird nicht bestraft -- der Grund gegen 1/n.

    Bei Gleichgewichtung ueber alle Maerkte bekaeme ein Kandidat mit genau
    einer Meinung 1/38 Exposure, und sein Ergebnis waere von Rauschen nicht
    zu unterscheiden, egal wie gut das Signal ist.
    """
    roh = {f"M{i}": 0.0 for i in range(38)}
    roh["M7"] = 1.0

    assert normiere(roh, 1.0)["M7"] == pytest.approx(1.0)


def test_keine_meinung_bleibt_keine_meinung():
    """`nan` heisst "die Strategie sagt nichts". Die Schicht darf daraus
    keine Aussage machen und den Wert auch nicht mitzaehlen."""
    skaliert = normiere({"A": 2.0, "B": float("nan")}, 1.0)

    assert math.isnan(skaliert["B"])
    assert skaliert["A"] == pytest.approx(1.0)


def test_die_huelle_behaelt_den_namen_des_kandidaten():
    """Ein zweiter Name waere eine zweite Hypothese im Versuchszaehler.

    Die Schicht ist eine Ausfuehrungsregel, kein neuer Ansatz -- sie darf die
    DSR-Huerde nicht durch die Hintertuer erhoehen (ADR-032).
    """
    huelle = BruttoNormiert(ImmerLong(["A", "B"], "1d"), grenze=1.0)

    assert huelle.name == "immer_long"
    assert huelle.warmup_bars == 2


# ---------------------------------------------------------------------------
# Der Fall aus ADR-065, an einem Konto
# ---------------------------------------------------------------------------


def test_ohne_schicht_ruiniert_ein_kandidat_ueber_viele_maerkte_das_konto():
    """Der Ausgangszustand, damit die Korrektur einen Bezugspunkt hat.

    Faellt diese Zusicherung eines Tages, ist entweder die Engine repariert
    worden oder dieser Test misst nicht mehr, was er soll.
    """
    symbole = [f"M{i}/USD" for i in range(20)]
    bars = {s: _bars(s, 120) for s in symbole}

    r = run_backtest(ImmerLong(symbole, "1d"), bars, BacktestConfig())
    brutto = ((r.equity["equity"] - r.equity["cash"]) / r.equity["equity"]).abs()

    assert brutto.max() > 5.0, (
        f"Bruttoexposure blieb bei {brutto.max():.2f} -- entweder ist die "
        "Engine repariert oder dieser Test trifft den Mechanismus nicht mehr"
    )


def test_mit_schicht_bleibt_das_konto_im_budget():
    symbole = [f"M{i}/USD" for i in range(20)]
    bars = {s: _bars(s, 120) for s in symbole}

    r = run_backtest(
        mit_groessenschicht(ImmerLong, 1.0)(symbole, "1d"), bars, BacktestConfig()
    )
    eq = r.equity
    brutto = ((eq["equity"] - eq["cash"]) / eq["equity"]).abs()
    # Der Anlauf ist ausgenommen: Symbole werden nacheinander warm, und
    # solange erst k von n gesprochen haben, ist 1/k das richtige Gewicht.
    eingeschwungen = brutto.iloc[len(brutto) // 4 :]

    assert eingeschwungen.max() <= 1.05, (
        f"Bruttoexposure {eingeschwungen.max():.3f} -- die Schicht greift nicht"
    )
    assert compute(eq.set_index("ts")["equity"], "1d").ruined_at is None


def test_mit_schicht_handelt_der_kandidat_trotzdem():
    """Die zweite Haelfte von ADR-065: "auf 1 normiert handelt keiner mehr".

    Genau das passierte, solange das Rebalancing-Band ein Anteil des
    **Eigenkapitals** war: bei 20 Maerkten ist die natuerliche Position 5 %
    und liegt damit auf der Bandgrenze; bei 39 Maerkten mit 2,6 % darunter,
    und es entsteht nie eine Order. Gemessen an 39 echten Maerkten: Brutto im
    Median 0,502 statt 0,995 -- ein Portfolio, das die Haelfte dessen haelt,
    was es will, eingefroren aus der Anlaufphase.
    """
    symbole = [f"M{i}/USD" for i in range(20)]
    bars = {s: _bars(s, 200) for s in symbole}

    r = run_backtest(
        mit_groessenschicht(ImmerLong, 1.0)(symbole, "1d"), bars, BacktestConfig()
    )
    eq = r.equity
    brutto = ((eq["equity"] - eq["cash"]) / eq["equity"]).abs()

    assert len(r.fills) >= len(symbole), (
        f"nur {len(r.fills)} Ausfuehrungen fuer {len(symbole)} Maerkte -- "
        "der Kandidat handelt nicht, das Ergebnis sagt nichts ueber die Idee"
    )
    assert brutto.iloc[-1] > 0.9, (
        f"Brutto am Ende {brutto.iloc[-1]:.3f} -- das Portfolio haelt weniger, "
        "als es will"
    )


def test_das_band_misst_die_position_und_nicht_das_konto():
    """Bei Vollgewicht auf einem Symbol darf sich nichts geaendert haben.

    Das ist die Bedingung, unter der die Umstellung ueberhaupt vertretbar war:
    `macross` und `hashribbon` handeln nur 1,0 oder 0 und sind gemessen
    bitgleich geblieben (Sharpe +1,0263 / +0,7467, gleiche Fill-Zahl).
    """
    from qt.backtest.broker_sim import SimBroker
    from qt.backtest.engine import rebalance_order
    from qt.core.types import Order

    cfg = BacktestConfig()
    broker = SimBroker(cfg)
    preise = {"X/USD": 100.0}

    # Vollgewicht aus der Flat-Position: Bezug ist das ganze Konto.
    order = rebalance_order(broker, "X/USD", 1.0, 100.0, preise, cfg)
    assert order is not None

    # Position aufbauen, dann ein Ziel knapp daneben: das liegt im Band.
    broker.submit(Order("X/USD", order.qty))
    broker.execute_pending("X/USD", 100.0, T0)
    assert rebalance_order(broker, "X/USD", 0.98, 100.0, preise, cfg) is None

    # Und ein Ziel weit daneben nicht.
    assert rebalance_order(broker, "X/USD", 0.5, 100.0, preise, cfg) is not None


def test_ein_kleines_ziel_wird_nicht_mehr_verschluckt():
    """Die eine Verhaltensaenderung der Umstellung, absichtlich festgehalten.

    Solange das Band ein Anteil des **Eigenkapitals** war, verschluckte es
    jedes Ziel unter 5 % -- eine Strategie, die bewusst klein positionieren
    wollte, bekam davon nichts, und zwar ohne Meldung. Das war keine
    Rauschunterdrueckung (ADR-008), sondern eine Meinung der Engine ueber die
    Positionsgroesse.

    Jetzt gilt nur noch der absolute Boden `min_trade_notional`. Die Richtung
    ist die unbequeme: mehr Ausfuehrungen, mehr Gebuehren, schlechtere Zahlen
    -- gemessen an `trend`, `meanrev` und `elliott` zwischen 0,0007 und
    0,0034 Sharpe, alle drei nach unten.
    """
    from qt.backtest.broker_sim import SimBroker
    from qt.backtest.engine import rebalance_order

    cfg = BacktestConfig()
    broker = SimBroker(cfg)

    order = rebalance_order(broker, "X/USD", 0.001, 100.0, {"X/USD": 100.0}, cfg)

    assert order is not None, "ein gewolltes 0,1-%-Ziel darf nicht stumm verfallen"
    # 0,1 % von 100.000 sind 100 USD Gegenwert -- ueber dem Boden von 10.
    assert abs(order.qty) * 100.0 == pytest.approx(100.0, rel=0.01)


def test_unter_dem_absoluten_boden_wird_weiterhin_verworfen():
    """`min_trade_notional` bleibt die Sicherung gegen Rundungsrauschen."""
    from qt.backtest.broker_sim import SimBroker
    from qt.backtest.engine import rebalance_order

    cfg = BacktestConfig(min_trade_notional=10.0)
    broker = SimBroker(cfg)

    winzig = rebalance_order(broker, "X/USD", 0.00001, 100.0, {"X/USD": 100.0}, cfg)

    assert winzig is None
