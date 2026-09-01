"""Der Kill-Switch fuer ein Paper-Konto -- duenn, mit Absicht.

Die eigentliche Logik (Drawdown-Grenze, Ausloesung) sitzt in
`qt.portfolio.risk.RiskEngine` und wird dort in jedem Tick neu ausgewertet
(siehe `qt.live.runner`). Diese Datei tut nur eine Sache: den **persistierten**
Zustand eines Kontos manuell zuruecksetzen, nachdem ein Mensch nachgesehen hat,
warum das Konto angehalten wurde.

Absichtlich keine automatische Wiederaufnahme bei erholter Equity -- siehe
`RiskEngine.reset()`. Ein Kill-Switch, der sich selbst zurueckstellt, ist
keiner.
"""

from __future__ import annotations

from pathlib import Path

from qt.live.state import PaperState, state_path


class NoPaperAccount(Exception):
    """Es gibt noch kein Konto, das man zuruecksetzen koennte."""


def reset(
    strategy_name: str,
    symbols: list[str],
    timeframe: str,
    note: str = "",
    data_dir: Path | None = None,
    state_dir: Path | None = None,
) -> PaperState:
    """Kill-Switch eines Kontos manuell loesen.

    `note` landet in `halt_reasons` und bleibt dort stehen, statt geloescht zu
    werden -- ein zurueckgesetztes Konto, dessen Halt-Historie verschwunden
    ist, sieht beim naechsten Blick aus, als sei nie etwas passiert.
    """
    path = state_path(strategy_name, symbols, timeframe, state_dir)
    state = PaperState.load(path)
    if state is None:
        raise NoPaperAccount(
            f"Kein Paper-Konto fuer {strategy_name} {symbols} {timeframe}. "
            "Erst `qt paper run` laufen lassen."
        )
    state.halted = False
    if note:
        state.halt_reasons = [*state.halt_reasons, f"manuell zurueckgesetzt: {note}"]
    state.save(path)
    return state
