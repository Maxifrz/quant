# Roadmap

> ## ▶ HIER WEITER
>
> **Phase 0 und 1 sind fertig.** Nächster Schritt: **Phase 2 — Portfolio + Walk-Forward.**
>
> Erster Handgriff:
> `src/qt/backtest/walkforward.py` anlegen — rollierendes Fenster mit Purging und
> Embargo, das `run_backtest()` aus `qt/backtest/engine.py` mehrfach über getrennte
> Zeitfenster laufen lässt und die OOS-Segmente zu einer Equity-Curve zusammensetzt.
>
> **Mit im Gepäck aus Phase 1 (siehe ADR-009):** Umschlagshäufigkeit gehört ab sofort
> in jede Bewertung. Bei 90 bps Round-Trip ist eine Strategie mit täglichem Umschlag
> chancenlos, egal wie gut ihr Sharpe vor Kosten aussieht.

---

## Prinzip der Phaseneinteilung

Jede Phase endet mit **etwas Sichtbarem** — einem Chart, einer Zahl, einem Report —
nicht mit "Infrastruktur fertig". Jede hat **einen Befehl**, der sie vorführt.
Man kann nach jeder Phase aufhören und hat etwas Funktionierendes.

---

## ✅ Phase 0 — Skelett + Daten

- `pyproject.toml` (uv), Paketstruktur, Docs
- `qt.data.ingest` — OHLCV via ccxt gegen Coinbase Exchange, paginiert, UTC
- `qt.data.store` — Parquet, partitioniert nach `symbol/timeframe`
- `qt.data.integrity` — Lücken, Duplikate, Monotonie, OHLC-Plausibilität

**Vorführen:** `uv run qt data pull` → `uv run qt data report`

## ✅ Phase 1 — Backtest-Engine + zwei Baseline-Strategien

- `qt.core` — Clock, Events, Typen
- `qt.features` — Point-in-Time-Feature-Store mit `LookaheadError`
- `qt.backtest.engine` — event-getriebener Bar-Loop
- `qt.backtest.costs` — Fees + Spread + Slippage, pessimistische Defaults
- `qt.backtest.broker_sim` — Fills auf dem *nächsten* Bar-Open
- `qt.strategy.library` — Donchian-Breakout, z-Score-Mean-Reversion
- `qt.report.tearsheet` — Kennzahlen + Equity-Curve-PNG inkl. Buy-&-Hold

**Vorführen:** `uv run qt backtest --strategy trend --symbol BTC/USD --tf 4h`

> Erwartungsmanagement: Diese beiden Strategien sollen *nicht* profitabel sein.
> Sie sind Testinstrumente für die Engine. Ob etwas Geld verdient, entscheidet
> frühestens Phase 2.

### Ergebnisse des ersten echten Laufs (BTC/USD + ETH/USD, 2019–2026)

Datenlage: 7,6 Jahre, 99,97% Abdeckung, keine kaputten Bars.

| Lauf | Ergebnis | Buy & Hold |
|---|---|---|
| `trend` BTC/USD 4h | Faktor 0,46 · Sharpe −0,06 | Faktor 17,4 · Sharpe 0,92 |
| `meanrev` ETH/USD 1h | Faktor 0,00 · Sharpe −2,08 | Faktor 14,6 · Sharpe 0,84 |

Beide verlieren deutlich — wie erwartet. Die Diagnose ist aber unterschiedlich, und
genau das ist der Wert dieser Phase:

- **`trend` scheitert an den Kosten, nicht am Signal.** Ohne Gebühren macht dieselbe
  Strategie Faktor 6,44, bei Maker-Gebühren 4,04, bei Coinbase-Taker-Gebühren 0,46.
  Details in ADR-009.
- **`meanrev` scheitert am Signal.** Auch ohne jede Gebühr bleibt Faktor 0,23
  (long-only) bzw. 0,01 (mit Short). Naive Mean-Reversion, die in einem Bullenmarkt
  Rallyes shortet, ruiniert das Konto — das ist kein Kostenproblem.

Dass die Engine dabei korrekt rechnet, ist separat abgesichert: eine Immer-Long-
Strategie ohne Kosten trifft Buy-&-Hold auf 0,54% genau, mit genau einem Trade
(`tests/test_engine.py`).

---

## ⬜ Phase 2 — Portfolio + Walk-Forward

- Mehrere Strategien parallel über mehrere Symbole und Timeframes
- `qt.portfolio.risk` — Vol-Targeting, Exposure-Caps, Drawdown-Kill-Switch
- `qt.backtest.walkforward` — rollierendes Fenster, Purging + Embargo gegen Leakage
- `qt.portfolio.baselines` — Equal-Weight, Vol-Parity, Best-Single

**Ergebnis:** ehrliche OOS-Zahlen statt In-Sample-Fantasie.
**Vorführen:** `uv run qt wf --strategies trend,meanrev --symbols BTC/USD,ETH/USD`

## ⬜ Phase 3 — LLM-Allokator

- `qt.llm.briefing` — Blind Briefing: Regime-Features, rollierende Strategie-Performance,
  Risikobudget-Auslastung. Anonymisiert, datumsfrei (siehe ADR-003).
- `qt.llm.schemas` — pydantic-validierter Output: `{strategy_id: weight}` + Begründung
  + Confidence
- `qt.llm.cache` — Cache über Hash(Prompt) + Modell-ID → Backtests reproduzierbar und
  beim zweiten Lauf kostenlos
- `qt.portfolio.llm_allocator` — Vorschlag → Risk-Clamps → Zielgewichte

**Das Gate:** geht nur weiter, wenn der Allokator out-of-sample die Baselines schlägt.
**Vorführen:** `uv run qt alloc --compare-baselines`

## ⬜ Phase 4 — Pfad-Simulation

- `qt.sim.bootstrap` — stationärer Block-Bootstrap (erhält Autokorrelation + Vol-Clustering)
- `qt.sim.regimes` — GARCH für Vol-Pfade, HMM für Regime-Wechsel
- `qt.sim.scenarios` — das LLM setzt Szenario-Priors, die das Pfad-Ensemble umgewichten
- Zielfunktion: Median-Rendite unter CVaR-Nebenbedingung

**Vorführen:** `uv run qt sim --paths 10000 --horizon 30d`
Neue Deps: `arch`, `hmmlearn`, `scipy`

## ⬜ Phase 5 — Research-Loop

- `qt.research.generator` — LLM schreibt Kandidaten gegen eine eng definierte API
- `qt.research.sandbox` — AST-Whitelist: keine Imports, keine I/O, kein Netzwerk
- `qt.research.screening` — In-Sample → Walk-Forward-OOS → **Deflated Sharpe Ratio**
  gegen die Anzahl *aller je getesteten* Kandidaten (ADR-005)
- `qt.research.registry` — Herkunft, Zeitpunkt, OOS-Fenster, Statistik
- Promotion nach `strategy/library/` nur mit manueller Freigabe

**Vorführen:** `uv run qt research --generate 20 --screen`

## ⬜ Phase 6 — Paper-Trading

- `qt.live.runner` mit `PaperClock` + `SimBroker`: echte Live-Daten, simulierte Fills
- `qt.live.reconcile` — Soll- vs. Ist-Positionen
- `qt.live.killswitch` — Drawdown-Stopp, manuelles Zurücksetzen
- `qt.report.daily` — Tagesreport
- Über Wochen laufen lassen, Live-vs-Backtest-Divergenz je Strategie messen.
  Wer divergiert, kommt in Quarantäne.

**Vorführen:** `uv run qt paper --daemon`

## ⬜ Phase 7 — Live (separate Entscheidung)

Erst wenn Phase 6 über Wochen sauber läuft. Exchange-Keys, `qt.live.broker_ccxt`,
Mini-Kapital, harte Positionslimits. Das ist eine eigene Entscheidung mit echtem
Geld — keine Fortsetzung der Bauarbeit.
