"""Kennzahlen einer Equity-Curve.

Zwei Konventionen, die hier bewusst gesetzt sind:

- Annualisiert wird mit 365.25 Tagen, nicht 252. Krypto handelt 24/7; mit
  Aktienkonventionen zu rechnen verzerrt jeden Sharpe um rund 20%.
- Sharpe ohne risikofreien Zins (rf = 0). Bei Krypto-Zeitreihen mit
  wechselnden Zinsumfeldern ist ein pauschaler rf mehr Schein- als
  Genauigkeitsgewinn; wer ihn braucht, uebergibt ihn explizit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from qt.core.types import bars_per_year


@dataclass(slots=True)
class Metrics:
    n_bars: int
    total_return: float
    cagr: float
    ann_vol: float
    sharpe: float
    sortino: float
    max_drawdown: float
    calmar: float
    hit_rate: float
    time_in_market: float
    ann_vol_active: float
    n_trades: int
    turnover: float
    fees_paid: float

    def as_dict(self) -> dict[str, float]:
        return {
            "Bars": self.n_bars,
            "Gesamtrendite": self.total_return,
            "CAGR": self.cagr,
            "Vola p.a.": self.ann_vol,
            "Sharpe": self.sharpe,
            "Sortino": self.sortino,
            "Max Drawdown": self.max_drawdown,
            "Calmar": self.calmar,
            "Trefferquote": self.hit_rate,
            "Zeit im Markt": self.time_in_market,
            "Vola p.a. aktiv": self.ann_vol_active,
            "Trades": self.n_trades,
            "Umsatz": self.turnover,
            "Gebuehren": self.fees_paid,
        }

    def table(self) -> str:
        pct = {
            "Gesamtrendite",
            "CAGR",
            "Vola p.a.",
            "Vola p.a. aktiv",
            "Max Drawdown",
            "Trefferquote",
            "Zeit im Markt",
        }
        money = {"Umsatz", "Gebuehren"}
        lines = []
        for key, value in self.as_dict().items():
            if key in pct:
                cell = f"{value:>12.2%}"
            elif key in money:
                cell = f"{value:>12,.0f}"
            elif key in {"Bars", "Trades"}:
                cell = f"{value:>12,.0f}"
            else:
                cell = f"{value:>12.2f}"
            lines.append(f"  {key:<16}{cell}")
        return "\n".join(lines)


def compute(
    equity: pd.Series,
    timeframe: str,
    n_trades: int = 0,
    turnover: float = 0.0,
    fees_paid: float = 0.0,
) -> Metrics:
    """Kennzahlen aus einer Equity-Zeitreihe berechnen."""
    equity = equity.dropna()
    if len(equity) < 2:
        return Metrics(
            len(equity), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, n_trades, turnover, fees_paid
        )

    returns = equity.pct_change().dropna()
    py = bars_per_year(timeframe)

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    years = len(returns) / py
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0

    ann_vol = float(returns.std(ddof=1) * np.sqrt(py))
    sharpe_value = sharpe(returns.to_numpy(), timeframe)
    sharpe_value = sharpe_value if np.isfinite(sharpe_value) else 0.0

    downside = returns[returns < 0]
    dd_std = downside.std(ddof=1) if len(downside) > 1 else 0.0
    sortino = float(returns.mean() / dd_std * np.sqrt(py)) if dd_std > 0 else 0.0

    max_dd = float(drawdown(equity).min())
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else 0.0

    # Trefferquote nur ueber Bars, an denen die Strategie ueberhaupt positioniert
    # war. Ueber alle Bars gerechnet misst sie vor allem, wie oft eine Strategie
    # flat ist -- eine Strategie, die zu 90% aussetzt, saehe dann aus wie eine mit
    # 10% Trefferquote, obwohl sie in ihren Positionen gut sein kann.
    active = returns[returns != 0]
    hit_rate = float((active > 0).mean()) if len(active) else 0.0

    # Zeit im Markt und die Vola *waehrend* dieser Zeit.
    #
    # Ohne diese beiden liest man `ann_vol` falsch. Eine Strategie, die zu 93%
    # flat ist, zeigt eine annualisierte Vola von 3,7% -- was wie starke
    # Drosselung aussieht, tatsaechlich aber Untaetigkeit misst. Waehrend sie
    # tatsaechlich positioniert ist, liegt dieselbe Strategie bei 13,9%.
    #
    # Dieser Fehlschluss ist hier real passiert: die Risk-Engine wurde
    # faelschlich fuer zu scharf gehalten (ADR-016).
    time_in_market = float(len(active) / len(returns)) if len(returns) else 0.0
    ann_vol_active = (
        float(active.std(ddof=1) * np.sqrt(py)) if len(active) > 1 else 0.0
    )

    return Metrics(
        n_bars=len(equity),
        total_return=total_return,
        cagr=cagr,
        ann_vol=ann_vol,
        sharpe=sharpe_value,
        sortino=sortino,
        max_drawdown=max_dd,
        calmar=calmar,
        hit_rate=hit_rate,
        time_in_market=time_in_market,
        ann_vol_active=ann_vol_active,
        n_trades=n_trades,
        turnover=turnover,
        fees_paid=fees_paid,
    )


def sharpe(returns: np.ndarray, timeframe: str) -> float:
    """Annualisierter Sharpe einer Renditereihe, `nan` wenn nicht bestimmbar.

    Steht bewusst getrennt von `compute()` und arbeitet auf einem Array: die
    Allokatoren in `qt.portfolio.baselines` brauchen den Sharpe einmal pro Bar
    und Strategie und koennen dafuer nicht durch pandas gehen. Zwei getrennte
    Sharpe-Definitionen im System waeren schlimmer -- sie laufen mit der Zeit
    auseinander, und dann misst der Allokator etwas anderes als der Report.
    """
    values = np.asarray(returns, dtype=float)
    if len(values) < 2 or not np.all(np.isfinite(values)):
        return float("nan")
    sd = values.std(ddof=1)
    if sd <= 0:
        return float("nan")
    return float(values.mean() / sd * np.sqrt(bars_per_year(timeframe)))


def drawdown(equity: pd.Series) -> pd.Series:
    """Relativer Abstand zum bisherigen Hoechststand, als negative Reihe."""
    return equity / equity.cummax() - 1.0


def buy_and_hold(
    prices: pd.Series, initial_cash: float, entry_cost_bps: float = 0.0
) -> pd.Series:
    """Vergleichskurve: einmal kaufen, liegen lassen.

    Der wichtigste Massstab ueberhaupt. Eine Krypto-Strategie, die Buy-&-Hold
    nicht schlaegt, ist die Komplexitaet nicht wert -- und in einem Bullenmarkt
    sieht fast jede Long-Strategie gut aus, bis man sie danebenlegt.

    **Nur der Einstieg wird berechnet, nicht der Ausstieg.** Das ist keine
    Nachlaessigkeit, sondern die Konvention, mit der die Engine auch die
    Strategie bewertet: `run_backtest` liquidiert am Ende nicht, die
    Schlusszahl ist eine Mark-to-Market-Bewertung offener Positionen. Wuerde
    man Buy-&-Hold einen Ausstieg berechnen und der Strategie nicht, waere der
    Vergleich zugunsten der Strategie verzerrt -- und zwar unsichtbar.

    Die Kosten kommen **auf** den Gegenwert obendrauf, genau wie im
    `SimBroker` (`cash -= qty * fill_price + fee`): mit Kapital C und
    Kostensatz f werden N Einheiten gekauft, fuer die N * p0 * (1 + f) = C
    gilt. Ein Kostensatz von 0 ergibt exakt die alte Formel zurueck.

    Warum das ueberhaupt zaehlt: ohne Einstiegskosten ist Buy-&-Hold die
    einzige Zeile in der Tabelle, die gar keine Reibung kennt -- und damit ein
    Massstab, den in der Wirklichkeit auch Buy-&-Hold nicht erreicht.
    """
    prices = prices.dropna()
    if prices.empty:
        return prices
    if entry_cost_bps < 0:
        raise ValueError(f"entry_cost_bps={entry_cost_bps} ist negativ.")

    units = initial_cash / (prices.iloc[0] * (1.0 + entry_cost_bps / 10_000.0))
    curve = units * prices
    if entry_cost_bps == 0 or len(curve) < 2:
        return curve

    # **Der erste Punkt bleibt das Startkapital.** Ohne das waere die ganze
    # Korrektur wirkungslos: `compute` misst `total_return` als
    # equity[-1] / equity[0], und ein konstanter Faktor auf die gesamte Kurve
    # kuerzt sich darin restlos heraus. Die Gebuehr stuende dann zwar im Code,
    # aber in keiner einzigen angezeigten Kennzahl.
    #
    # Der Sprung vom ersten auf den zweiten Punkt traegt damit die Gebuehr --
    # genau wie bei der Strategie, deren Kurve ebenfalls beim Startkapital
    # beginnt und die Gebuehr erst mit dem ersten Fill zeigt.
    curve = curve.copy()
    curve.iloc[0] = initial_cash
    return curve
