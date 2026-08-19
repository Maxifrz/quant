"""Tearsheet: Equity-Curve, Drawdown, Exposure und Kennzahlen als PNG.

Drei Panels statt einem: eine Equity-Curve allein verbirgt, *wie* die
Rendite entstanden ist. Der Drawdown zeigt, ob sie durchhaltbar war, und
das Exposure zeigt, ob die Strategie ueberhaupt gehandelt hat oder nur
zufaellig long war.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # kein Display in dieser Umgebung
import matplotlib.pyplot as plt
import pandas as pd

from qt.backtest.engine import BacktestResult
from qt.backtest.metrics import Metrics, buy_and_hold, compute, drawdown
from qt.core.config import REPORT_DIR


def summarise(result: BacktestResult) -> tuple[Metrics, Metrics | None]:
    """Kennzahlen der Strategie und -- wenn ein Preis vorliegt -- von Buy-&-Hold."""
    equity = result.equity.set_index("ts")["equity"]
    strat = compute(
        equity,
        result.timeframe,
        n_trades=result.n_trades,
        turnover=result.equity["turnover"].iloc[-1],
        fees_paid=result.fees_paid,
    )

    bh_metrics = None
    price = _price_series(result)
    if price is not None:
        bh = buy_and_hold(price, result.config.initial_cash)
        bh_metrics = compute(bh, result.timeframe)

    return strat, bh_metrics


def _price_series(result: BacktestResult) -> pd.Series | None:
    """Preisreihe fuer den Buy-&-Hold-Vergleich.

    Nur sinnvoll bei einem einzelnen Symbol -- bei mehreren waere ungeklaert,
    wie der Vergleichskorb gewichtet ist. Lieber keinen Vergleich zeigen als
    einen willkuerlichen.
    """
    price_cols = [c for c in result.equity.columns if c.startswith("price_")]
    if len(price_cols) != 1:
        return None
    return result.equity.set_index("ts")[price_cols[0]].dropna()


def render(result: BacktestResult, out_path: Path | None = None) -> Path:
    """Tearsheet erzeugen und als PNG speichern."""
    strat, bh_metrics = summarise(result)
    df = result.equity.set_index("ts")
    equity = df["equity"]
    price = _price_series(result)

    fig, axes = plt.subplots(
        3, 1, figsize=(12, 10), sharex=True, height_ratios=[3, 1.5, 1.2]
    )
    fig.suptitle(
        f"{result.strategy}  |  {', '.join(result.symbols)}  |  {result.timeframe}",
        fontsize=13,
        fontweight="bold",
    )

    # Panel 1 -- Equity gegen Buy-&-Hold
    ax = axes[0]
    ax.plot(equity.index, equity.values, label="Strategie", linewidth=1.4)
    if price is not None:
        bh = buy_and_hold(price, result.config.initial_cash)
        ax.plot(bh.index, bh.values, label="Buy & Hold", linewidth=1.1, alpha=0.65)
    if result.warmup_end is not None:
        ax.axvline(result.warmup_end, linestyle=":", alpha=0.5, label="Warmup-Ende")
    ax.set_ylabel("Eigenkapital")
    ax.set_yscale("log")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.25)

    # Panel 2 -- Drawdown
    ax = axes[1]
    dd = drawdown(equity)
    ax.fill_between(dd.index, dd.values * 100, 0, alpha=0.4, color="crimson")
    ax.set_ylabel("Drawdown %")
    ax.grid(alpha=0.25)

    # Panel 3 -- Exposure
    ax = axes[2]
    weight_cols = [c for c in df.columns if c.startswith("weight_")]
    for col in weight_cols:
        ax.plot(df.index, df[col].values, linewidth=0.9, label=col.removeprefix("weight_"))
    ax.axhline(0, color="black", linewidth=0.6, alpha=0.5)
    ax.set_ylabel("Zielgewicht")
    ax.set_ylim(-1.15, 1.15)
    if len(weight_cols) > 1:
        ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.25)

    fig.tight_layout(rect=(0, 0.06, 1, 0.98))

    footer = (
        f"Sharpe {strat.sharpe:.2f}   MaxDD {strat.max_drawdown:.1%}   "
        f"CAGR {strat.cagr:.1%}   Trades {strat.n_trades}   "
        f"Gebuehren {strat.fees_paid:,.0f}"
    )
    if bh_metrics is not None:
        footer += (
            f"      |      B&H: Sharpe {bh_metrics.sharpe:.2f}   "
            f"MaxDD {bh_metrics.max_drawdown:.1%}   CAGR {bh_metrics.cagr:.1%}"
        )
    fig.text(0.5, 0.015, footer, ha="center", fontsize=9)

    out_path = out_path or _default_path(result)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def _default_path(result: BacktestResult) -> Path:
    name = result.strategy.split("(")[0]
    symbols = "_".join(s.replace("/", "-") for s in result.symbols)
    return REPORT_DIR / f"{name}_{symbols}_{result.timeframe}.png"


def print_summary(result: BacktestResult) -> None:
    """Kennzahlen auf die Konsole, Strategie neben Buy-&-Hold."""
    strat, bh = summarise(result)
    print(f"\n{result.strategy}  |  {', '.join(result.symbols)}  |  {result.timeframe}")
    print("=" * 62)
    if bh is None:
        print(strat.table())
        return

    pct = {"Gesamtrendite", "CAGR", "Vola p.a.", "Max Drawdown", "Trefferquote"}
    print(f"  {'':<16}{'Strategie':>14}{'Buy & Hold':>14}")
    for key, value in strat.as_dict().items():
        other = bh.as_dict()[key]
        if key in pct:
            print(f"  {key:<16}{value:>13.2%}{other:>14.2%}")
        elif key in {"Bars", "Trades", "Umsatz", "Gebuehren"}:
            print(f"  {key:<16}{value:>13,.0f}{other:>14,.0f}")
        else:
            print(f"  {key:<16}{value:>13.2f}{other:>14.2f}")
