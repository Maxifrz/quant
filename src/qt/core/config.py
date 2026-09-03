"""Konfiguration. Pydantic, damit falsche Werte beim Laden auffallen und
nicht erst mitten im Backtest."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

# Projektwurzel: .../src/qt/core/config.py -> vier Ebenen hoch
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
REPORT_DIR = PROJECT_ROOT / "reports"


# Das Modell, das der Allokator ab Phase 3 befragt. Steht hier und nicht in
# `qt.llm`, weil sowohl der Client als auch der Antwort-Cache es brauchen --
# und zwei Stellen mit demselben Default laufen frueher oder spaeter
# auseinander. Genau das waere hier teuer: der Cache wuerde dann die Antwort
# eines anderen Modells liefern, ohne dass etwas fehlschlaegt.
DEFAULT_LLM_MODEL = "claude-opus-5"

# Das Modell des zweiten Anbieters (NVIDIA NIM, siehe `qt.llm.providers`).
# Anthropic bleibt Default -- jede bislang gemessene Zahl und jeder
# Cache-Eintrag haengt daran. Dieser Name gilt nur, wenn `--provider nim`
# ausdruecklich gesetzt ist.
DEFAULT_NIM_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"


class CostConfig(BaseModel):
    """Handelskosten. Defaults bewusst pessimistisch (siehe ARCHITECTURE.md).

    Lieber ein Backtest, der zu schlecht aussieht, als einer, der zu gut
    aussieht: der erste kostet eine verworfene Strategie, der zweite Geld.
    """

    taker_fee_bps: float = Field(
        default=60.0,
        ge=0,
        description="Gebuehr je Ausfuehrung in bps. Default: Coinbase "
        "Advanced Trade Taker, Eingangsstufe (ADR-056, geprueft 2026-09-02)",
    )
    half_spread_bps: float = Field(
        default=2.0,
        ge=0,
        description="Halber Spread in bps. ANNAHME, nicht gemessen -- der "
        "Versuch, sie aus Tages-OHLC zu schaetzen, ist gescheitert "
        "(qt.backtest.spread, ADR-056)",
    )
    slippage_bps: float = Field(
        default=3.0,
        ge=0,
        description="Zusaetzliche Slippage in bps je Ausfuehrung. ANNAHME",
    )


# --------------------------------------------------------------------------
# Kostenregime
#
# ADR-009 hat gemessen, dass dieselbe Strategie im selben Zeitraum je nach
# Gebuehrenannahme bei Faktor 0,46 oder bei 4,04 landet. Damit leistet die
# Kostenannahme mehr als jede Strategieentscheidung dieses Projekts -- und
# stand trotzdem bis 2026-09-02 als blanke Zahl im Code, ohne Quelle.
#
# Ab jetzt haben die Annahmen Namen, Quellen und ein Abrufdatum (ADR-056).
# Die Gebuehren sind nachgelesene Eingangsstufen, die Spread- und
# Slippage-Anteile bleiben Annahmen; welcher Teil was ist, steht je Eintrag.
#
# Bewusst ein Nachschlagen und keine Heuristik auf dem Symbolnamen: eine Regel
# wie "enthaelt einen Slash, also Krypto" waere genau die stille Annahme, die
# dieses Projekt sonst herausrechnet (ADR-055).
# --------------------------------------------------------------------------

# Coinbase Advanced Trade / Exchange, Eingangsstufe (30-Tage-Volumen < 10k USD).
# Gebuehren abgerufen 2026-09-02: Maker 0,40 %, Taker 0,60 %. Die Seite von
# Coinbase selbst antwortet aus dieser Umgebung mit HTTP 403; die Zahlen
# stammen aus drei unabhaengigen Sekundaerquellen, die uebereinstimmen. Das
# ist schwaecher als die Primaerquelle und steht so im ADR.
COINBASE_TAKER = CostConfig(taker_fee_bps=60.0, half_spread_bps=2.0, slippage_bps=3.0)

# Maker: keine Spanne, keine Slippage. Wer passiv im Buch liegt, zahlt den
# Spread nicht, er verdient ihn -- deshalb 0,0 und nicht der Taker-Wert.
#
# Diese beiden Nullen sind die optimistischste Annahme im ganzen Projekt, und
# sie haben einen Preis, den das Kostenmodell nicht abbilden kann: eine
# Limit-Order, die nicht gefuellt wird, ist keine Ersparnis, sondern ein
# verpasster Trade. Was das kostet, misst `qt maker` (ADR-056) -- die Zahl
# gehoert neben jede Rechnung, die dieses Regime benutzt.
COINBASE_MAKER = CostConfig(taker_fee_bps=40.0, half_spread_bps=0.0, slippage_bps=0.0)

# Kraken Pro, Eingangsstufe. Primaerquelle kraken.com/features/fee-schedule,
# abgerufen 2026-09-02: Maker 0,40 %, Taker 0,80 %. Kraken hat seine Staffel
# im Juli 2026 umgestellt; aeltere Zusammenstellungen im Netz nennen noch
# 0,16/0,26 und sind veraltet. Ein Beleg fuer die Regel, dass eine
# Sekundaerquelle ohne Datum wertlos ist.
KRAKEN_TAKER = CostConfig(taker_fee_bps=80.0, half_spread_bps=2.0, slippage_bps=3.0)
KRAKEN_MAKER = CostConfig(taker_fee_bps=40.0, half_spread_bps=0.0, slippage_bps=0.0)

# US-ETFs ueber einen provisionsfreien API-Broker (Alpaca). Primaerquelle
# files.alpaca.markets/disclosures/BrokFeeSched.pdf, abgerufen 2026-09-02:
# keine Provision auf US-Werte, dazu durchgereichte Aufsichtsgebuehren --
# SEC 0,00206 bps vom Gegenwert (nur Verkauf), FINRA TAF 0,000195 USD je
# Stueck (nur Verkauf), CAT 0,000003 USD je Stueck. Auf einen Round-Trip
# gerechnet bleibt davon deutlich unter 0,1 bps; als 0,1 gerundet, damit im
# Modell nicht "kostenlos" steht, was nicht kostenlos ist.
US_ETF_COSTS = CostConfig(taker_fee_bps=0.1, half_spread_bps=1.0, slippage_bps=1.5)

# Ohne Kosten. Nicht als Szenario gedacht, sondern als Messgeraet: die
# Differenz zu jedem anderen Regime ist das, was die Ausfuehrung kostet.
NO_COSTS = CostConfig(taker_fee_bps=0.0, half_spread_bps=0.0, slippage_bps=0.0)

# Was ADR-009 "Maker-Niveau (~16 bps Round-Trip)" nannte. Bei Coinbase
# entspraeche das rund 5,5 bps Maker-Gebuehr und damit einer Stufe ab etwa
# 1 Mio. USD Monatsvolumen. Der Eintrag bleibt, damit die alte Tabelle
# reproduzierbar ist -- er ist ausdruecklich **kein** erreichbares Szenario
# fuer ein Konto dieser Groesse.
ADR009_MAKER = CostConfig(taker_fee_bps=5.5, half_spread_bps=1.25, slippage_bps=1.25)

COST_REGIMES: dict[str, CostConfig] = {
    "none": NO_COSTS,
    "coinbase_taker": COINBASE_TAKER,
    "coinbase_maker": COINBASE_MAKER,
    "kraken_taker": KRAKEN_TAKER,
    "kraken_maker": KRAKEN_MAKER,
    "us_etf": US_ETF_COSTS,
    "adr009_maker": ADR009_MAKER,
}


def costs_for_symbols(symbols: list[str]) -> dict[str, CostConfig]:
    """Kostensatz je Symbol aus dem Anlageklassen-Zuschnitt des Stores.

    Ohne diese Zuordnung rechnet jeder Lauf ueber gemischte Maerkte die
    US-ETFs mit dem Krypto-Taker ab -- 130 statt 5,2 Basispunkte Round-Trip.
    Genau das ist bis 2026-09-02 passiert: `costs_by_symbol` gab es seit
    ADR-055, aber keine Aufrufstelle fuellte es, und dadurch stehen die
    ETF-Zellen in ADR-055 unter einem 25-fach zu hohen Kostensatz.

    Der Import liegt in der Funktion, weil `qt.data.tiingo` `requests` zieht
    und die Konfiguration sonst eine Netzbibliothek zum Importzeitpunkt
    braeuchte.
    """
    from qt.data.tiingo import BASKET

    return {sym: US_ETF_COSTS for sym in symbols if sym in BASKET}


def regime(name: str) -> CostConfig:
    """Kostenregime nach Namen, mit einer Fehlermeldung, die weiterhilft."""
    try:
        return COST_REGIMES[name]
    except KeyError:
        raise KeyError(
            f"Unbekanntes Kostenregime {name!r}. "
            f"Verfuegbar: {', '.join(sorted(COST_REGIMES))}"
        ) from None


class BacktestConfig(BaseModel):
    initial_cash: float = Field(default=100_000.0, gt=0)
    costs: CostConfig = Field(default_factory=CostConfig)
    costs_by_symbol: dict[str, CostConfig] = Field(
        default_factory=dict,
        description="Kostensatz je Symbol; alles Uebrige faellt auf `costs` "
        "zurueck. Noetig, seit im Store mehr als eine Anlageklasse liegt: ein "
        "US-ETF kostet wenige Basispunkte je Ausfuehrung, ein Krypto-Taker 45.",
    )
    max_gross_exposure: float = Field(
        default=1.0,
        gt=0,
        description="Max. Brutto-Exposure als Vielfaches des Eigenkapitals. "
        "1.0 = kein Hebel.",
    )
    min_trade_notional: float = Field(
        default=10.0,
        ge=0,
        description="Orders unterhalb dieses Gegenwerts werden verworfen. "
        "Verhindert, dass Rundungsrauschen Gebuehren erzeugt.",
    )
    rebalance_band: float = Field(
        default=0.05,
        ge=0,
        lt=1,
        description="Toleranz um das Zielgewicht, als Bruchteil des Eigenkapitals. "
        "Es wird erst gehandelt, wenn die Ist-Position weiter abweicht. "
        "Ohne Band erzeugt jede Preisbewegung eine Mikro-Order, weil das Ziel "
        "auf dem Close berechnet und auf dem naechsten Open ausgefuehrt wird "
        "-- siehe ADR-008.",
    )


class DataConfig(BaseModel):
    exchange: str = Field(
        default="coinbaseexchange",
        description="ccxt-Exchange-ID. Siehe ADR-006 zur Wahl.",
    )
    data_dir: Path = Field(default=DATA_DIR)
    rate_limit_ms: int = Field(default=350, ge=0)
