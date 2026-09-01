"""Taeglicher Report ueber ein Paper-Konto: reine Textausgabe.

Kein Chart, keine PNG -- ein Paper-Konto wird ueber Wochen taeglich
angeschaut, und ein Report, den man nicht in einem Terminal oder einer
Chat-Nachricht lesen kann, wird nicht taeglich angeschaut. Wer die
Equity-Kurve sehen will, hat `qt.report.tearsheet` fuer den Backtest, gegen
den das Paper-Konto irgendwann verglichen wird.
"""

from __future__ import annotations

from qt.live.state import PaperState


def _eigenkapital_zeile(
    state: PaperState, prices: dict[str, float] | None
) -> str:
    """Eigenkapital -- oder eine ehrliche Auskunft, warum es fehlt."""
    if not state.positions:
        # Flach: das Konto *ist* sein Cash, dafuer braucht es keine Preise.
        return (
            f"  Eigenkapital: {state.cash:,.2f}  "
            f"(Hoechststand {state.peak_equity:,.2f})"
        )

    fehlend = sorted(s for s in state.positions if s not in (prices or {}))
    if fehlend:
        return (
            f"  Eigenkapital: nicht bewertbar -- kein Kurs fuer "
            f"{', '.join(fehlend)}. Erst `qt data pull` laufen lassen."
        )

    equity = state.cash + sum(
        p["qty"] * prices[symbol] for symbol, p in state.positions.items()
    )
    return (
        f"  Eigenkapital (letzte Schlusskurse): {equity:,.2f}  "
        f"(Hoechststand {state.peak_equity:,.2f})"
    )


def render(
    state: PaperState,
    strategy_name: str,
    symbols: list[str],
    prices: dict[str, float] | None = None,
) -> str:
    """Kontostand als kurzer Text -- Positionen, Kill-Switch, Herkunft.

    Der Kill-Switch-Status steht bewusst **oben**, nicht unten: es ist die
    eine Zeile, die ein Mensch, der den Report ueberfliegt, nicht uebersehen
    darf.

    `prices` sind die letzten bekannten Schlusskurse je Symbol. Ohne sie wird
    das Eigenkapital bei offenen Positionen **nicht** ausgewiesen. Frueher
    stand hier `cash + qty * avg_price` unter der Ueberschrift "letzte
    bekannte Preise" -- also die Kostenbasis mit einem falschen Etikett. Eine
    verdoppelte Position bewegte die Zahl nicht, und neben dem korrekt
    gefuehrten Hoechststand sah das aus wie ein Drawdown, den es nicht gab
    (ADR-053). Lieber keine Zahl als eine, die etwas anderes misst, als ihre
    Beschriftung behauptet.
    """
    zeilen = [f"Paper-Konto: {strategy_name} auf {', '.join(symbols)}"]

    if state.halted:
        zeilen.append("  !! KILL-SWITCH AUSGELOEST -- keine neuen Positionen !!")
        for grund in state.halt_reasons[-3:]:
            zeilen.append(f"     {grund}")
    else:
        zeilen.append("  Status: laeuft")

    zeilen.append(f"  Cash: {state.cash:,.2f}")
    zeilen.append(_eigenkapital_zeile(state, prices))

    if state.positions:
        zeilen.append("  Positionen:")
        for symbol, p in sorted(state.positions.items()):
            kurs = (prices or {}).get(symbol)
            wert = f"  Wert {p['qty'] * kurs:,.2f}" if kurs is not None else ""
            zeilen.append(
                f"    {symbol:>10}  Menge {p['qty']:+.6f}  "
                f"Einstand {p['avg_price']:.2f}{wert}"
            )
    else:
        zeilen.append("  Positionen: keine (flach)")

    if state.risk_notes:
        zeilen.append("  Letzte Eingriffe der Risk-Engine:")
        for notiz in state.risk_notes[-3:]:
            zeilen.append(f"    {notiz}")

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
