# Roadmap

> ## ▶ HIER WEITER
>
> **Phase 0, 1 und 2 sind fertig.** Nächster Schritt: **Phase 3 — LLM-Allokator.**
>
> Erster Handgriff: `src/qt/llm/briefing.py` — das Blind Briefing (ADR-003).
> Regime-Features, rollierende Strategie-Performance und Risikobudget-Auslastung,
> anonymisiert und datumsfrei.
>
> Danach `src/qt/llm/schemas.py` (pydantic-validierter Output) und
> `src/qt/llm/cache.py` (Hash(Prompt) + Modell-ID → reproduzierbar und beim
> zweiten Lauf kostenlos).
>
> **Nicht mehr nötig:** Die Risk-Engine zu kalibrieren stand hier als Vorarbeit.
> Die Messung hat gezeigt, dass sie nicht zu scharf eingestellt ist — die
> vermeintliche Drosselung war eine irreführende Kennzahl (ADR-016).

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

## ✅ Phase 2 — Portfolio + Walk-Forward

- `qt.portfolio.base` — Verträge: `Allocator`, `AllocationContext`, `RiskLimits`, `combine()`
- `qt.backtest.portfolio_engine` — mehrere Strategien, gemischte Timeframes, ein Konto
- `qt.backtest.walkforward` — rollierendes Fenster mit Purging und Embargo
- `qt.portfolio.baselines` — Equal-Weight, Vol-Parity, Best-Single, Fixed
- `qt.portfolio.risk` — Vol-Targeting, Symbol- und Brutto-Caps, Drawdown-Kill-Switch

**Vorführen:**
```bash
uv run qt wf --strategy trend --symbol BTC/USD --tf 4h --train 3000 --test 800 --embargo 50
uv run qt portfolio --strategies trend,meanrev --symbols BTC/USD,ETH/USD --tf 4h
uv run qt allocators
```

### Ergebnisse

**Walk-Forward `trend` BTC/USD 4h** (17 Fenster, Train 3000 / Test 800 / Embargo 50):
OOS-Gesamtrendite −71,94%, Sharpe −0,37, **4 von 17 Fenstern mit positivem Sharpe**.
Die In-Sample-Zahlen aus Phase 1 waren also nicht zu pessimistisch, sondern zu
optimistisch. Genau dafür gibt es Walk-Forward.

**Die Risk-Engine ist die wirksamste Komponente im System** (ADR-013):

| Lauf | Gesamtrendite | Vola p.a. | Max Drawdown |
|---|---|---|---|
| `vol_parity`, ohne Risk-Engine | −99,68% | 32,8% | −99,72% |
| `equal_weight`, mit Risk-Engine | −16,90% | 3,7% | −20,11% |

Identische Strategien und Daten — der Unterschied ist allein die Risikoschicht.
Sie macht schlechte Strategien nicht gut, aber sie sorgt dafür, dass man einen
Fehler überlebt. Das ist der empirische Beleg dafür, dass "Allokator schlägt
vor, Risk-Engine entscheidet" richtig herum gebaut ist.

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
