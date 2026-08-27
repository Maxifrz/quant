"""Trend aus dem Order Flow: Volumen mit Richtung statt Volumen allein.

**Warum das eine neue Informationsachse ist.** `trend`, `meanrev` und
`elliott` kauen alle auf denselben OHLCV-Daten herum, und alle drei sind
gescheitert (ADR-009, ADR-033). Eine vierte Strategie auf derselben Datenbasis
haette schlechte Aussichten. Der Aggressor-getriebene Fluss steht in OHLCV
schlicht nicht drin: ein Bar mit hohem Volumen und unveraendertem Schluss kann
von einem Kaufueberhang stammen, der auf Widerstand lief, oder von einem
ausgeglichenen Umsatz -- fuer die Kursreihe sieht beides identisch aus.

**Wie der Fluss zur Strategie kommt, und warum das kein Lookahead ist.** Die
Flow-Tabelle ist ein Beiwagen: ein Woerterbuch von Bar-Open-Zeit auf vier
Kennzahlen, beim Bau geladen. Nachgeschlagen wird ausschliesslich mit
Zeitstempeln, die der `FeatureStore` bereits herausgegeben hat -- die Uhr
bleibt also allein beim Store, der Beiwagen hat keine eigene.

Das ist eine bewusste Entscheidung gegen die Alternative, `Bar` um Flow-Felder
zu erweitern. Diese waere sauberer im Typ, wuerde aber `qt.core.types`
anfassen und jeden Bar im System um Felder erweitern, die fast nirgends
existieren. Der Preis der gewaehlten Loesung: die PIT-Garantie ruht hier auf
einer Regel ("nur nachschlagen, was der Store schon zeigte") statt auf dem
Typsystem. Deshalb ist genau das der wichtigste Test dieser Datei.

**Das Signal.** Kein Divergenz-Handel -- die uebliche Erzaehlung, dass
Divergenz eine Umkehr ankuendigt, ist eine Erzaehlung. Gehandelt wird die
schlichtere Aussage: laeuft der aggressive Fluss ueber mehrere Bars in
dieselbe Richtung und ist er im Vergleich zur juengeren Vergangenheit
auffaellig gross, wird mitgelaufen. Faellt er zurueck ins Rauschen, wird flach
gestellt.

**Erwartungsmanagement.** Bei 90 Basispunkten Round-Trip muss ein Signal ueber
0,9% Bewegung vorhersagen, nur um bei null herauszukommen. Order-Flow-Signale
sind kurzfristig; das ist die eigentliche Huerde, nicht die Datenbeschaffung.
Ob es reicht, entscheidet `qt wf`, nicht dieser Docstring.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from qt.features import orderflow as of
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy
from qt.strategy.registry import register


def load_flow_table(
    symbol: str, timeframe: str, data_dir=None
) -> dict[datetime, dict]:
    """Flow-Kennzahlen je Bar-Open-Zeit laden.

    Getrennte Funktion, damit Tests eine Tabelle einspeisen koennen, ohne
    Dateien anzulegen -- und damit der Ladefehler ("keine Trades abgezogen")
    an einer Stelle steht und sprechend ist.
    """
    from qt.data.trades import read_trades

    trades = read_trades(symbol, data_dir=data_dir)
    flow = of.aggregate(trades, timeframe)
    return {
        row.ts.to_pydatetime(): {
            "delta": float(row.delta),
            "buy_share": float(row.buy_share),
            "n_trades": int(row.n_trades),
            "avg_size": float(row.avg_size),
        }
        for row in flow.itertuples()
    }


@register
class OrderFlowTrend(Strategy):
    """Mitlaufen, solange der aggressive Fluss auffaellig einseitig ist."""

    name = "orderflow"

    def __init__(
        self,
        symbols: list[str],
        timeframe: str,
        flow: dict[str, dict[datetime, dict]] | None = None,
        lookback: int = 30,
        entry_z: float = 1.0,
        exit_z: float = 0.25,
        persistence: int = 2,
        min_trades: int = 20,
        allow_short: bool = True,
        data_dir=None,
    ) -> None:
        super().__init__(
            symbols,
            timeframe,
            lookback=lookback,
            entry_z=entry_z,
            exit_z=exit_z,
            persistence=persistence,
            min_trades=min_trades,
            allow_short=allow_short,
        )
        # Beim Bau geladen, nicht bei jedem Bar: ein Dateizugriff im Bar-Loop
        # waere nicht nur langsam, er waere auch die Stelle, an der sich
        # unbemerkt ein Blick auf spaetere Daten einschleicht.
        if flow is not None:
            self._flow = flow
        else:
            self._flow = {
                sym: load_flow_table(sym, timeframe, data_dir) for sym in symbols
            }

    @property
    def warmup_bars(self) -> int:
        return self.params["lookback"] + self.params["persistence"] + 2

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        window = store.window(symbol, self.timeframe)
        if len(window) < self.warmup_bars:
            return math.nan

        table = self._flow.get(symbol)
        if not table:
            return math.nan

        # **Hier sitzt die Point-in-Time-Regel dieser Datei.** Nachgeschlagen
        # wird ausschliesslich ueber `window.timestamps` -- also ueber die
        # Bars, die der Store bereits herausgegeben hat. Wer stattdessen ueber
        # die Flow-Tabelle iterierte und nach Zeitstempeln bis "jetzt" filterte,
        # haette eine zweite Uhr im System, und zwei Uhren gehen irgendwann
        # auseinander.
        stamps = window.timestamps[-(self.params["lookback"] + 2) :]
        deltas = np.array(
            [table.get(ts, {}).get("delta", math.nan) for ts in stamps], dtype=float
        )
        trades = np.array(
            [table.get(ts, {}).get("n_trades", 0) for ts in stamps], dtype=float
        )

        if trades[-1] < self.params["min_trades"]:
            # Zu duenn, um von Fluss zu sprechen. Keine Meinung ist etwas
            # anderes als "geh flat" -- eine Datenluecke ist kein Signal.
            return math.nan

        z = of.flow_zscore(deltas, self.params["lookback"])
        if not math.isfinite(z):
            return math.nan

        state = self._state.setdefault(symbol, {"weight": 0.0, "streak": 0, "sign": 0})
        weight = state["weight"]

        # Beharrlichkeit zaehlen: ein einzelner auffaelliger Bar ist eine
        # Stichprobe, mehrere in dieselbe Richtung sind ein Fluss.
        sign = 0
        if z >= self.params["entry_z"]:
            sign = 1
        elif z <= -self.params["entry_z"]:
            sign = -1

        if sign != 0 and sign == state["sign"]:
            state["streak"] += 1
        elif sign != 0:
            state["sign"], state["streak"] = sign, 1
        else:
            state["sign"], state["streak"] = 0, 0

        # Ausstieg zuerst: der Fluss ist zurueck im Rauschen.
        if weight != 0.0 and abs(z) < self.params["exit_z"]:
            state["weight"] = 0.0
            return 0.0

        # Gegenlaeufiger Fluss beendet die Position, dreht sie aber nicht in
        # einem Schritt um -- das waere doppelter Umsatz auf ein Signal, das
        # sich gerade erst gedreht hat.
        if weight > 0 and z <= -self.params["entry_z"]:
            state["weight"] = 0.0
            return 0.0
        if weight < 0 and z >= self.params["entry_z"]:
            state["weight"] = 0.0
            return 0.0

        if weight != 0.0:
            return weight

        if state["streak"] >= self.params["persistence"] and state["sign"] != 0:
            direction = float(state["sign"])
            if direction < 0 and not self.params["allow_short"]:
                return 0.0
            state["weight"] = direction
            return direction

        return 0.0
