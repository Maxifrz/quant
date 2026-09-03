"""Der Paper-Tick: ein Aufruf, der sicher wiederholt werden darf.

Bewusst **kein** Daemon, der endlos laeuft. Diese Entscheidung ist keine
Vereinfachung, sondern eine Reaktion auf eine gemessene Eigenschaft dieser
Umgebung: Hintergrundprozesse in dieser Sitzung sind wiederholt an
Container-Neustarts gestorben, mitten in einem mehrstuendigen Trade-Abzug.
Ein Daemon, der Wochen durchlaufen muss, hat genau dieses Risiko, nur mit
hoeherem Einsatz -- ein gestorbenes Paper-Konto faellt erst auf, wenn man
nach Tagen nachsieht, warum keine neuen Fills entstanden sind.

Die Alternative: `run_paper_tick` ist eine reine Funktion, die einmal
aufgerufen wird, das Konto um alle inzwischen geschlossenen Bars fortschreibt
und zurueckkehrt. Sicher wiederholbar (nichts wird doppelt ausgefuehrt, siehe
`PaperState.last_processed_ts`), sicher unterbrechbar (Zustand wird nach
jedem Bar geschrieben, nicht erst am Ende), und sicher extern taktbar --
durch einen Cron, eine `Routine` dieser Plattform, oder von Hand.

Der Ablauf pro Bar ist **identisch mit `qt.backtest.engine.run_backtest`**
(ADR-001, eine Engine, drei Uhren): Uhr stellen, vorgemerkte Orders auf dem
Open ausfuehren, Bar in den Feature-Store aufnehmen, Strategie entscheiden
lassen, Zieldifferenz vormerken. Der einzige Unterschied ist, dass ein Teil
der Bars **nur den Feature-Store fuettert** und keine neue Entscheidung
ausloest -- naemlich die, die ein frueherer Tick bereits entschieden hat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qt.backtest.broker_sim import SimBroker
from qt.backtest.engine import rebalance_order
from qt.core.clock import BacktestClock
from qt.core.config import BacktestConfig, DataConfig
from qt.core.events import merge_bar_streams
from qt.core.types import Bar, Fill, timeframe_seconds
from qt.features.registry import FeatureStore
from qt.live.state import PaperState, state_path
from qt.portfolio.base import RiskState
from qt.portfolio.risk import RiskConfig, RiskEngine, realised_vol_map
from qt.strategy.base import Strategy, clip_weight

# Wie weit vor dem juengsten bekannten Bar nachgezogen wird, um den
# neuesten Abschluss sicher zu erwischen. Klein gehalten: das ist ein
# Auffrischen, kein vollstaendiger Pull -- die grosse Historie liegt schon
# im Store und wird hier nicht jedes Mal neu gezogen.
REFRESH_BARS = 5

# Puffer ueber den Warmup hinaus, wenn der Store kalt ist. Bars fallen aus
# (Wartungsfenster der Exchange, Netzfehler beim Abzug), und ein Konto, das
# genau am Warmup entlangschrammt, laeuft dann nicht an.
COLD_START_BUFFER = 60

# Wieviele Routine-Meldungen der Risk-Engine im Zustand aufgehoben werden.
# Sie entstehen bei jedem Bar mit Position; ungebremst waere die
# Zustandsdatei nach einem Jahr groesser als der Rest des Kontos.
_MAX_RISK_NOTES = 5


@dataclass(slots=True)
class TickReport:
    """Was in diesem Tick passiert ist -- fuer Log, Report und Tests."""

    new_bars: int
    new_fills: list[Fill]
    equity: float
    halted: bool
    halt_reasons: list[str]
    warm: bool
    last_bar_ts: datetime | None

    def summary(self) -> str:
        if self.new_bars == 0:
            return "kein neuer Bar seit dem letzten Tick."
        status = "ANGEHALTEN" if self.halted else "laeuft"
        return (
            f"{self.new_bars} neue(r) Bar(s), {len(self.new_fills)} Fill(s), "
            f"Eigenkapital {self.equity:,.2f}, Status {status}"
        )


def run_paper_tick(
    strategy_factory: "callable[[], Strategy]",
    symbols: list[str],
    timeframe: str,
    cfg: BacktestConfig | None = None,
    risk_cfg: RiskConfig | None = None,
    data_dir: Path | None = None,
    state_dir: Path | None = None,
    refresh: bool = True,
    now: datetime | None = None,
) -> TickReport:
    """Konto um alle seit dem letzten Tick geschlossenen Bars fortschreiben.

    `refresh=False` ist fuer Tests: dort liegen die Bars schon vollstaendig
    im Store, ein Netzwerkzugriff waere ein ungewollter Seiteneffekt und
    macht den Test langsam und flackrig.

    `now` ist injizierbar, damit die Grenze testbar ist, statt von der
    Systemuhr abzuhaengen -- der Grund fuer den Parameter ist zugleich der
    Grund, warum es ihn ueberhaupt braucht: **eine Exchange kann den
    gerade noch offenen Bar als letzte Zeile von `fetch_ohlcv`
    zurueckgeben.** Gemessen an echten Coinbase-Daten: ein Bar mit
    `close_ts` einen Tag in der Zukunft lag tatsaechlich im Store. Sein
    `close_ts` ist ein Versprechen, keine Tatsache, solange die reale Uhr
    es nicht eingeholt hat. Der Store heilt sich selbst (`write_bars`
    dedupliziert mit `keep='last'`, ein spaeterer Pull ueberschreibt den
    vorlaeufigen Wert), aber genau in der Luecke davor wuerde diese Engine
    sonst auf einer Kerze entscheiden, die sich noch aendern kann.
    """
    strategy = strategy_factory()
    cfg = cfg or BacktestConfig()
    strategy_name = strategy.name
    now = now or datetime.now(timezone.utc)

    if refresh:
        _refresh_recent_bars(symbols, timeframe, data_dir, strategy.warmup_bars)

    bars_by_symbol = _load_bars(symbols, timeframe, data_dir)
    if not bars_by_symbol or all(len(v) == 0 for v in bars_by_symbol.values()):
        raise ValueError(
            f"Keine Bars fuer {symbols} @ {timeframe} im Store. "
            f"Erst `qt data pull` laufen lassen."
        )
    events = merge_bar_streams(list(bars_by_symbol.values()))

    # **Die harte Grenze.** Ein Bar, dessen Close-Zeit noch in der Zukunft
    # liegt, ist nicht bekannt -- unabhaengig davon, ob die Exchange ihn
    # schon herausgegeben hat. Er wird komplett ignoriert, nicht nur von
    # der Entscheidung ausgenommen: auch als Kontext im Feature-Store waere
    # er ein Wert, der sich noch aendern kann.
    events = [e for e in events if e.ts <= now]
    if not events:
        raise ValueError(
            f"Kein abgeschlossener Bar fuer {symbols} @ {timeframe} vor {now.isoformat()}. "
            f"Erst `qt data pull` laufen lassen."
        )

    path = state_path(strategy_name, symbols, timeframe, state_dir)
    state = PaperState.load(path)
    if state is None:
        state = PaperState.fresh(cfg.initial_cash)
        # **Das Konto startet flach, nicht rueckwirkend.** Ohne diese Zeile
        # waere jeder Bar der gesamten Historie "neu", und ein frisches
        # Paper-Konto wuerde beim ersten Tick Hunderte Fills auf einmal
        # ausloesen -- das genaue Gegenteil von "beobachten, was ab jetzt
        # passiert".
        state.set_last_processed(events[-1].ts)
        state.save(path)
        return TickReport(0, [], cfg.initial_cash, False, [], False, events[-1].ts)

    clock = BacktestClock(events[0].ts)
    store = FeatureStore(clock, maxlen=max(1000, strategy.warmup_bars * 3))
    broker = _restore_broker(cfg, state)
    risk = RiskEngine(risk_cfg)
    risk.restore_halted(state.halted)

    last_price: dict[str, float] = {}
    bars_seen = dict(state.bars_seen)
    target = dict(state.target)
    warmup_end = (
        datetime.fromisoformat(state.warmup_end) if state.warmup_end else None
    )
    peak_equity = state.peak_equity
    last_processed = state.last_processed()

    new_bars = 0
    new_fills: list[Fill] = []
    last_bar_ts: datetime | None = None

    for event in events:
        bar = event.bar
        is_new = last_processed is None or event.ts > last_processed
        if not is_new:
            # Nur Kontext fuer den Feature-Store -- Uhr trotzdem vorstellen,
            # sonst wirft `store.on_bar` einen Lookahead-Fehler auf den
            # naechsten wirklich neuen Bar.
            clock.advance(event.ts)
            store.on_bar(bar)
            last_price[bar.symbol] = bar.close
            bars_seen[bar.symbol] = bars_seen.get(bar.symbol, 0) + 1
            continue

        new_bars += 1
        last_bar_ts = event.ts
        clock.advance(event.ts)

        fills = broker.execute_pending(bar.symbol, bar.open, bar.ts, bar.volume)
        new_fills.extend(fills)

        store.on_bar(bar)
        last_price[bar.symbol] = bar.close
        bars_seen[bar.symbol] = bars_seen.get(bar.symbol, 0) + 1

        warm = all(
            bars_seen.get(sym, 0) >= strategy.warmup_bars for sym in bars_by_symbol
        )
        if warm:
            if warmup_end is None:
                warmup_end = event.ts

            weight = clip_weight(strategy.on_bar(bar.symbol, store))
            if not math.isnan(weight):
                target[bar.symbol] = weight

            equity_now = broker.equity(last_price)
            peak_equity = max(peak_equity, equity_now)

            # **Ueber alle Symbole des Kontos, nicht nur ueber das gerade
            # geschlossene.** `max_gross_exposure` ist eine Grenze fuer das
            # Konto als Ganzes; wird die Engine je Bar mit einem
            # Ein-Symbol-Dict gefuettert, prueft sie diese Grenze n-mal
            # gegen je ein Symbol und laesst in Summe das n-fache durch
            # (ADR-053). Dass die Konten heute ein Symbol halten, macht das
            # zu einem stillen Fehler statt zu keinem.
            vol_map = realised_vol_map(
                {
                    sym: store.window(sym, timeframe).closes()
                    for sym in bars_by_symbol
                },
                timeframe,
                risk.cfg,
            )
            risk_state = RiskState(
                ts=event.ts,
                equity=equity_now,
                peak_equity=peak_equity,
                realised_vol=vol_map,
                halted=risk.halted,
            )
            adjusted, reasons = risk.apply(
                {sym: target.get(sym, 0.0) for sym in bars_by_symbol}, risk_state
            )
            state.halted = risk.halted or risk_state.halted
            # **Halt-Gruende und Routine-Meldungen getrennt halten.** Ein
            # greifender Cap meldet sich in *jedem* Bar mit Position. Landete
            # er in `halt_reasons`, stuende beim naechsten echten Halt dort
            # die letzte Cap-Meldung statt des Halt-Grundes -- und der
            # Tagesreport zeigt genau diese drei Zeilen (ADR-053).
            if reasons:
                state.risk_notes = reasons[-_MAX_RISK_NOTES:]
            if state.halted and reasons:
                state.halt_reasons = reasons

            order = rebalance_order(
                broker, bar.symbol, adjusted[bar.symbol], bar.close, last_price, cfg
            )
            if order is not None:
                broker.submit(order)

        # **Nach jedem einzelnen neuen Bar sichern**, nicht erst am Ende der
        # Schleife. Ein Tick kann mehrere neue Bars auf einmal verarbeiten
        # (wenn zwischen zwei Ticks laenger Zeit verging als ein Bar dauert);
        # ein Absturz nach dem dritten von fuenf Bars soll die ersten drei
        # nicht verlieren.
        state.set_last_processed(event.ts)
        _persist_broker(broker, state)
        state.bars_seen = bars_seen
        state.target = target
        state.warmup_end = warmup_end.isoformat() if warmup_end else None
        state.peak_equity = peak_equity
        state.n_fills += len(fills)
        state.save(path)

    final_equity = broker.equity(last_price) if last_price else state.cash
    return TickReport(
        new_bars=new_bars,
        new_fills=new_fills,
        equity=final_equity,
        halted=state.halted,
        halt_reasons=state.halt_reasons,
        warm=warmup_end is not None,
        last_bar_ts=last_bar_ts,
    )


def _restore_broker(cfg: BacktestConfig, state: PaperState) -> SimBroker:
    """`SimBroker` aus dem persistierten Kontostand wiederherstellen."""
    broker = SimBroker(cfg)
    broker.cash = state.cash
    broker.positions = state.positions_as_objects()
    broker.fees_paid = state.fees_paid
    broker.turnover = state.turnover
    broker._pending = state.pending_as_orders()
    return broker


def _persist_broker(broker: SimBroker, state: PaperState) -> None:
    state.cash = broker.cash
    state.set_positions(broker.positions)
    state.fees_paid = broker.fees_paid
    state.turnover = broker.turnover
    state.set_pending(broker._pending)


def _refresh_recent_bars(
    symbols: list[str], timeframe: str, data_dir: Path | None, warmup_bars: int = 0
) -> None:
    """Die juengsten Bars nachziehen -- und bei kaltem Store genug davon.

    Im Normalfall ein kleines Fenster: `qt.data.ingest.pull` und `write_bars`
    sind additiv und dedupliziert (siehe `qt.data.store.write_bars`), ein
    ueberlappender Pull ist also unschaedlich -- aber ein taeglicher Tick, der
    jedes Mal Jahre an Historie neu abfragt, waere langsam und unhoeflich
    gegenueber der Exchange, ohne dass es etwas braechte.

    **Der Kaltstart ist der Grund, warum diese Funktion mehr tut als ihr Name
    sagt.** Ein Konto laeuft ueber Wochen, eine Session nicht: der Container
    wird neu gebaut, `data/ohlcv/` ist gitignored und damit leer, und ein
    Auffrischen um fuenf Bars reicht dann fuer keinen Warmup -- `macross`
    braucht 52. Frueher endete der Tick an dieser Stelle mit "erst `qt data
    pull` laufen lassen", also mit einem Handgriff, den ein geplanter Job
    nicht tun kann. Genau daran ist das Konto beim letzten Containerwechsel
    stehengeblieben, ohne dass es jemandem auffiel.

    Deshalb: reicht der Bestand nicht fuer den Warmup, wird einmalig ein
    langes Fenster gezogen. Danach greift wieder das kleine, weil der Store
    dann warm ist. Die Pruefung laeuft je Symbol -- ein neu dazugenommenes
    Symbol soll nicht die Historie der anderen mitziehen.
    """
    from qt.data.ingest import pull
    from qt.data.store import read_bars

    tf_sekunden = timeframe_seconds(timeframe)
    jetzt = datetime.now(timezone.utc)
    noetig = warmup_bars + COLD_START_BUFFER

    kalt: list[str] = []
    warm: list[str] = []
    for symbol in symbols:
        try:
            vorhanden = len(read_bars(symbol, timeframe, data_dir=data_dir))
        except FileNotFoundError:
            vorhanden = 0
        (kalt if vorhanden < noetig else warm).append(symbol)

    cfg = DataConfig(data_dir=data_dir) if data_dir else None
    if warm:
        pull(
            warm,
            [timeframe],
            jetzt - timedelta(seconds=REFRESH_BARS * tf_sekunden),
            cfg=cfg,
        )
    if kalt:
        pull(kalt, [timeframe], jetzt - timedelta(seconds=noetig * tf_sekunden), cfg=cfg)


def _load_bars(
    symbols: list[str], timeframe: str, data_dir: Path | None
) -> dict[str, list[Bar]]:
    from qt.data.store import read_bars, to_bars

    out: dict[str, list[Bar]] = {}
    for symbol in symbols:
        try:
            df = read_bars(symbol, timeframe, data_dir=data_dir)
        except FileNotFoundError:
            continue
        out[symbol] = to_bars(symbol, timeframe, df)
    return out
