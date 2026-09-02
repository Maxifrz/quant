"""Handelskosten.

Ohne Kostenmodell ist jeder Backtest eine Luege. Besonders auf kurzen
Timeframes fressen Gebuehren und Slippage den gesamten Edge -- eine
Strategie mit Sharpe 2.0 vor Kosten kann nach Kosten glatt negativ sein.

Deshalb sind die Defaults pessimistisch: lieber eine gute Strategie
verwerfen als eine schlechte live schalten.

Modell: die effektive Ausfuehrung liegt um (halber Spread + Slippage)
schlechter als der Referenzpreis, dazu kommt die Taker-Gebuehr auf den
Gegenwert. Bewusst kein Market-Impact-Modell -- das braeuchte Orderbuchtiefe,
die wir nicht haben, und ein erfundenes Impact-Modell ist schlechter als
ein ehrlich konstanter Aufschlag.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from qt.core.config import CostConfig

BPS = 1e-4


@dataclass(frozen=True, slots=True)
class FillContext:
    """Was ueber den Markt zum Ausfuehrungszeitpunkt bekannt ist.

    Bar-Volumen und Symbol -- mehr geben OHLCV-Daten nicht her. Der Typ
    existiert trotzdem, damit ein spaeterer Wechsel auf Orderbuchdaten die
    Signatur der Fill-Modelle nicht bricht.

    `symbol` ist noetig, seit im Store mehr als eine Anlageklasse liegt. Ein
    US-ETF kostet je Ausfuehrung wenige Basispunkte, ein Krypto-Taker 45 --
    beides mit demselben Satz zu rechnen macht die eine Haelfte des Laufs
    absurd pessimistisch und die andere absurd optimistisch (ADR-055).
    """

    bar_volume: float | None = None
    symbol: str | None = None

    def participation(self, qty: float) -> float:
        """Anteil der Order am Volumen des Bars, 0.0 wenn unbekannt."""
        if self.bar_volume is None or self.bar_volume <= 0:
            return 0.0
        return abs(qty) / self.bar_volume


@dataclass(frozen=True, slots=True)
class TradeCost:
    fill_price: float
    fee: float
    slippage_cost: float

    @property
    def total(self) -> float:
        return self.fee + self.slippage_cost


def apply(reference_price: float, qty: float, cfg: CostConfig) -> TradeCost:
    """Kosten einer Market-Order.

    `qty` vorzeichenbehaftet: positiv kauft (teurer), negativ verkauft
    (billiger). Der Aufschlag wirkt immer gegen uns -- das ist der ganze
    Punkt.
    """
    if qty == 0:
        return TradeCost(reference_price, 0.0, 0.0)

    adverse_bps = (cfg.half_spread_bps + cfg.slippage_bps) * BPS
    direction = 1.0 if qty > 0 else -1.0
    fill_price = reference_price * (1 + direction * adverse_bps)

    notional = abs(qty) * fill_price
    fee = notional * cfg.taker_fee_bps * BPS
    slippage_cost = abs(qty) * abs(fill_price - reference_price)

    return TradeCost(fill_price=fill_price, fee=fee, slippage_cost=slippage_cost)


class FillModel(ABC):
    """Austauschbares Modell dafuer, zu welchem Preis eine Order ausgefuehrt wird.

    Als Abstraktion statt als feste Formel, weil die realistische Modellierung
    von Ausfuehrung stark davon abhaengt, welche Daten vorliegen: mit reinen
    OHLCV-Bars kann man nur ueber das Volumen argumentieren, mit Orderbuchtiefe
    ginge deutlich mehr. Ein Wechsel der Datenquelle soll dann das Modell
    tauschen, nicht die Engine.

    (Der Gedanke ist bei NautilusTrader abgeschaut, das denselben Schnitt
    macht -- dort mit acht Implementierungen bis hin zu gestuften
    Orderbuchtiefen. Uebernommen ist die Struktur, nicht der Code.)
    """

    name: str = "unnamed"

    @abstractmethod
    def fill(self, reference_price: float, qty: float, ctx: FillContext) -> TradeCost:
        """Ausfuehrungspreis und Kosten einer Market-Order."""

    def describe(self) -> str:
        return self.name


class FlatFillModel(FillModel):
    """Konstanter Aufschlag, unabhaengig von der Ordergroesse.

    Das Modell aus Phase 1 und weiterhin der Default. Fuer kleine Orders in
    liquiden Maerkten ist es angemessen -- es unterstellt lediglich, dass die
    eigene Order den Markt nicht bewegt.
    """

    name = "flat"

    def __init__(
        self,
        cfg: CostConfig | None = None,
        by_symbol: dict[str, CostConfig] | None = None,
    ) -> None:
        self.cfg = cfg or CostConfig()
        self.by_symbol = dict(by_symbol or {})

    def costs_for(self, symbol: str | None) -> CostConfig:
        """Kostensatz dieses Symbols, sonst der Default.

        Bewusst ein Nachschlagen und keine Heuristik auf dem Symbolnamen: eine
        Regel wie "enthaelt ein Slash, also Krypto" waere genau die Sorte
        stiller Annahme, die dieses Projekt sonst ueberall herausrechnet.
        """
        if symbol is None:
            return self.cfg
        return self.by_symbol.get(symbol, self.cfg)

    def fill(self, reference_price: float, qty: float, ctx: FillContext) -> TradeCost:
        return apply(reference_price, qty, self.costs_for(ctx.symbol))


class SizeAwareFillModel(FillModel):
    """Slippage waechst mit der Beteiligungsquote am Bar-Volumen.

    Das Flat-Modell unterstellt, man koenne jede Menge zum selben Aufschlag
    handeln. Das ist die optimistischste Annahme im ganzen Backtest: eine
    Strategie, die 10% des Tagesvolumens umschlaegt, zahlt in Wirklichkeit ein
    Vielfaches dessen, was sie mit 0,1% zahlen wuerde.

    Modelliert wird der uebliche Wurzel-Zusammenhang: der zusaetzliche
    Aufschlag waechst mit der Wurzel der Beteiligungsquote. Bei doppelter
    Ordergroesse steigt die Slippage also nicht doppelt, sondern um Faktor 1,41.
    Das ist die Standardnaeherung fuer Market Impact und deutlich naeher an der
    Realitaet als eine Konstante -- aber sie bleibt eine Naeherung. Wer es
    genauer braucht, braucht Orderbuchdaten.

    Ohne bekanntes Volumen faellt das Modell auf den konstanten Aufschlag
    zurueck: eine Impact-Schaetzung ohne Volumenbezug waere geraten, und
    geraten ist schlechter als bescheiden.
    """

    name = "size_aware"

    def __init__(
        self,
        cfg: CostConfig | None = None,
        impact_bps: float = 100.0,
        by_symbol: dict[str, CostConfig] | None = None,
    ) -> None:
        self.cfg = cfg or CostConfig()
        self.by_symbol = dict(by_symbol or {})
        if impact_bps < 0:
            raise ValueError("impact_bps darf nicht negativ sein.")
        self.impact_bps = impact_bps

    def costs_for(self, symbol: str | None) -> CostConfig:
        if symbol is None:
            return self.cfg
        return self.by_symbol.get(symbol, self.cfg)

    def fill(self, reference_price: float, qty: float, ctx: FillContext) -> TradeCost:
        basis = self.costs_for(ctx.symbol)
        participation = ctx.participation(qty)
        if participation <= 0:
            return apply(reference_price, qty, basis)

        extra_bps = self.impact_bps * math.sqrt(min(participation, 1.0))
        cfg = basis.model_copy(
            update={"slippage_bps": basis.slippage_bps + extra_bps}
        )
        return apply(reference_price, qty, cfg)


def one_way_bps(cfg: CostConfig) -> float:
    """Kosten einer einzelnen Ausfuehrung in Basispunkten.

    Gebuehr, halber Spread und Slippage zusammen -- also alles, was ein
    einzelner Kauf oder Verkauf kostet. Steht als eigene Funktion da, weil es
    Faelle gibt, in denen nur eine Seite anfaellt: wer kauft und liegen laesst,
    zahlt den Einstieg und (solange nicht verkauft wird) keinen Ausstieg.
    """
    return cfg.taker_fee_bps + cfg.half_spread_bps + cfg.slippage_bps


def round_trip_bps(cfg: CostConfig) -> float:
    """Kosten eines vollen Round-Trips in Basispunkten.

    Nuetzliche Faustzahl: eine Strategie muss pro Trade mehr als diesen Wert
    verdienen, um ueberhaupt bei null herauszukommen.
    """
    return 2 * one_way_bps(cfg)
