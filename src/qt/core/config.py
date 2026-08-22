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


class CostConfig(BaseModel):
    """Handelskosten. Defaults bewusst pessimistisch (siehe ARCHITECTURE.md).

    Lieber ein Backtest, der zu schlecht aussieht, als einer, der zu gut
    aussieht: der erste kostet eine verworfene Strategie, der zweite Geld.
    """

    taker_fee_bps: float = Field(
        default=40.0, ge=0, description="Taker-Gebuehr in Basispunkten (Coinbase ~40bps)"
    )
    half_spread_bps: float = Field(
        default=2.0, ge=0, description="Halber Spread in bps, als Kauf-/Verkaufsaufschlag"
    )
    slippage_bps: float = Field(
        default=3.0, ge=0, description="Zusaetzliche Slippage in bps pro Trade"
    )


class BacktestConfig(BaseModel):
    initial_cash: float = Field(default=100_000.0, gt=0)
    costs: CostConfig = Field(default_factory=CostConfig)
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
