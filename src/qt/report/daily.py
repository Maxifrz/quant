"""Taeglicher Report ueber ein Paper-Konto: reine Textausgabe.

Kein Chart, keine PNG -- ein Paper-Konto wird ueber Wochen taeglich
angeschaut, und ein Report, den man nicht in einem Terminal oder einer
Chat-Nachricht lesen kann, wird nicht taeglich angeschaut. Wer die
Equity-Kurve sehen will, hat `qt.report.tearsheet` fuer den Backtest, gegen
den das Paper-Konto irgendwann verglichen wird.
"""

from __future__ import annotations

from qt.live.state import PaperState


def render(state: PaperState, strategy_name: str, symbols: list[str]) -> str:
    """Kontostand als kurzer Text -- Positionen, Kill-Switch, Herkunft.

    Der Kill-Switch-Status steht bewusst **oben**, nicht unten: es ist die
    eine Zeile, die ein Mensch, der den Report ueberfliegt, nicht uebersehen
    darf.
    """
    zeilen = [f"Paper-Konto: {strategy_name} auf {', '.join(symbols)}"]

    if state.halted:
        zeilen.append("  !! KILL-SWITCH AUSGELOEST -- keine neuen Positionen !!")
        for grund in state.halt_reasons[-3:]:
            zeilen.append(f"     {grund}")
    else:
        zeilen.append("  Status: laeuft")

    zeilen.append(f"  Cash: {state.cash:,.2f}")
    equity = state.cash + sum(
        p["qty"] * p["avg_price"] for p in state.positions.values()
    )
    zeilen.append(
        f"  Eigenkapital (letzte bekannte Preise): {equity:,.2f}  "
        f"(Hoechststand {state.peak_equity:,.2f})"
    )

    if state.positions:
        zeilen.append("  Positionen:")
        for symbol, p in sorted(state.positions.items()):
            zeilen.append(f"    {symbol:>10}  Menge {p['qty']:+.6f}  Einstand {p['avg_price']:.2f}")
    else:
        zeilen.append("  Positionen: keine (flach)")

    zeilen.append(
        f"  Fills gesamt {state.n_fills}, Gebuehren {state.fees_paid:,.2f}, "
        f"Umsatz {state.turnover:,.0f}"
    )
    if state.warmup_end:
        zeilen.append(f"  Warm seit: {state.warmup_end}")
    else:
        zeilen.append("  Noch im Warmup -- die Strategie hat noch keine Meinung.")
    zeilen.append(f"  Letzter verarbeiteter Bar: {state.last_processed_ts}")
    zeilen.append(f"  Konto erstellt: {state.created_at}")

    return "\n".join(zeilen)
