"""Der Kontostand des Paper-Kontos -- das einzige, was ueberleben muss.

Ein Paper-Lauf zieht sich ueber Wochen, und diese Umgebung hat in dieser
Sitzung mehrfach demonstriert, dass ein Container mitten in einem Lauf
verschwinden kann. Die Konsequenz ist eine Entwurfsentscheidung, keine
Randnotiz: **jeder Tick ist ein vollstaendiger, deterministischer Replay der
bekannten Historie**, und was zwischen zwei Ticks ueberleben muss, ist nicht
der `FeatureStore` oder die Uhr -- die werden aus den durabel gespeicherten
Bars in `qt.data.store` billig neu aufgebaut -- sondern ausschliesslich der
Broker-Zustand (Cash, Positionen, vorgemerkte Orders) plus ein Zeitstempel-
Cursor, der markiert, bis wohin bereits **entschieden** wurde.

Dieselbe Idee wie `qt.data.trades.resume_point`, nur fuer Konten statt fuer
Trade-Abzuege: nicht "was ist der neueste bekannte Punkt", sondern "was ist
zusammenhaengend bereits verarbeitet".

Als JSON gespeichert, nicht als Pickle -- ein Paper-Konto, das man nicht mit
einem Texteditor oeffnen und verstehen kann, wenn etwas schiefgeht, verfehlt
den Zweck von Paper-Trading, naemlich vertrauenswuerdig beobachtbar zu sein.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

from qt.core.config import DataConfig
from qt.core.types import Order, Position
from qt.data.store import symbol_to_path


def state_path(
    strategy_name: str, symbols: list[str], timeframe: str, data_dir: Path | None = None
) -> Path:
    """Pfad des Kontostands. Eindeutig je Strategie/Symbole/Timeframe.

    Zwei Laeufe derselben Strategie auf verschiedenen Symbolen sind zwei
    verschiedene Konten -- sie teilen sich sonst denselben Cash-Topf und
    ergaeben ein Portfolio, das niemand konfiguriert hat.
    """
    root = (data_dir or DataConfig().data_dir) / "paper"
    symbolteil = "+".join(symbol_to_path(s) for s in sorted(symbols))
    return root / f"{strategy_name}_{symbolteil}_{timeframe}.json"


@dataclass(slots=True)
class PaperState:
    """Alles, was ein Tick zum Fortsetzen braucht.

    `last_processed_ts` ist der wichtigste Wert in dieser Klasse: er trennt
    "Bars, die die Strategie schon gesehen und beurteilt hat" von "Bars, die
    nur historischen Kontext fuer den Feature-Store liefern". Ohne ihn
    wuerde jeder Tick die gesamte Historie neu entscheiden und bei jedem
    Neustart Hunderte Fills auf einmal ausloesen.
    """

    cash: float
    positions: dict[str, dict] = field(default_factory=dict)
    pending: list[dict] = field(default_factory=list)
    fees_paid: float = 0.0
    turnover: float = 0.0
    n_fills: int = 0
    # Tiefe des Feature-Stores je Symbol **im letzten Tick**, nicht seit
    # Kontoeroeffnung: der Store wird jeden Tick neu gefuellt, und die
    # Warmup-Pruefung fragt nach ihm. Kein Fortschrittszaehler -- wer ihn
    # ueber Ticks aufsummiert, meldet Konten zu frueh als warm (ADR-075).
    bars_seen: dict[str, int] = field(default_factory=dict)
    target: dict[str, float] = field(default_factory=dict)
    warmup_end: str | None = None
    peak_equity: float = 0.0
    halted: bool = False
    # Nur Gruende eines **Halts**. Routine-Eingriffe der Risk-Engine (ein
    # greifender Cap meldet sich in jedem Bar mit Position) gehoeren nach
    # `risk_notes` -- sonst steht beim naechsten echten Halt die letzte
    # Cap-Meldung hier und der Tagesreport zeigt sie als Halt-Grund an
    # (ADR-053).
    halt_reasons: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    last_processed_ts: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def fresh(cls, initial_cash: float) -> "PaperState":
        """Ein leeres Konto. `last_processed_ts` bleibt `None` -- der
        Aufrufer setzt ihn beim ersten Tick auf den juengsten bekannten Bar,
        damit das Konto **flach startet** statt die gesamte Historie
        rueckwirkend zu handeln (siehe `runner.py`)."""
        return cls(cash=initial_cash, peak_equity=initial_cash)

    def positions_as_objects(self) -> dict[str, Position]:
        return {
            symbol: Position(symbol=symbol, qty=p["qty"], avg_price=p["avg_price"])
            for symbol, p in self.positions.items()
        }

    def set_positions(self, positions: dict[str, Position]) -> None:
        self.positions = {
            symbol: {"qty": p.qty, "avg_price": p.avg_price}
            for symbol, p in positions.items()
            if p.qty != 0.0
        }

    def pending_as_orders(self) -> list[tuple[Order, str]]:
        return [
            (Order(symbol=o["symbol"], qty=o["qty"], reason=o["reason"]), o["symbol"])
            for o in self.pending
        ]

    def set_pending(self, pending: list[tuple[Order, str]]) -> None:
        self.pending = [
            {"symbol": o.symbol, "qty": o.qty, "reason": o.reason} for o, _ in pending
        ]

    def last_processed(self) -> datetime | None:
        if self.last_processed_ts is None:
            return None
        return datetime.fromisoformat(self.last_processed_ts)

    def set_last_processed(self, ts: datetime) -> None:
        self.last_processed_ts = ts.isoformat()

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def save(self, path: Path) -> None:
        self.touch()
        path.parent.mkdir(parents=True, exist_ok=True)
        # In eine temporaere Datei schreiben und dann umbenennen: `os.replace`
        # ist auf demselben Dateisystem atomar. Ohne das koennte ein Absturz
        # mitten im Schreiben eine halbe, kaputte JSON-Datei hinterlassen --
        # und genau in dem Moment, in dem man den Kontostand am dringendsten
        # braucht, waere er unlesbar.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_asdict(self), indent=2, sort_keys=True))
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "PaperState | None":
        """Kontostand lesen, unbekannte Felder ueberspringen.

        Ein Konto laeuft ueber Wochen, der Code aendert sich dabei. Ein
        `cls(**raw)` ueber die rohen Schluessel wirft, sobald eine Fassung
        ein Feld schreibt, das eine andere nicht kennt -- und das ausgerechnet
        beim Laden des einzigen Zustands, der ueberleben muss. Unbekanntes
        wird deshalb verworfen, Fehlendes bekommt seinen Default.
        """
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
        bekannt = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in bekannt})


def _asdict(state: PaperState) -> dict:
    return {
        "cash": state.cash,
        "positions": state.positions,
        "pending": state.pending,
        "fees_paid": state.fees_paid,
        "turnover": state.turnover,
        "n_fills": state.n_fills,
        "bars_seen": state.bars_seen,
        "target": state.target,
        "warmup_end": state.warmup_end,
        "peak_equity": state.peak_equity,
        "halted": state.halted,
        "halt_reasons": state.halt_reasons,
        "risk_notes": state.risk_notes,
        "last_processed_ts": state.last_processed_ts,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }
