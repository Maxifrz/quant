# Roadmap

> ## ▶ HIER WEITER
>
> **Phase 0–4 sind gebaut.** Nächster Schritt: **Phase 5 — Research-Loop.**
>
> Erster Handgriff: `src/qt/research/sandbox.py` — die AST-Whitelist, die
> LLM-generierte Strategie-Kandidaten prüft, bevor irgendetwas davon läuft.
> Vor dem Generator, nicht danach: eine Sandbox, die erst nachgerüstet wird,
> ist keine.
>
> Danach `qt.research.screening` mit der **Deflated Sharpe Ratio** gegen die
> Anzahl *aller je getesteten* Kandidaten (ADR-005) — ohne die ist der
> Research-Loop eine Overfitting-Maschine.
>
> **Weiterhin offen aus Phase 3:** Der LLM-Allokator ist gebaut, aber seine
> Wirksamkeit ungeprüft (ADR-019, kein API-Schlüssel in der Bauumgebung).
> Erkundungslauf zuerst, er kostet wenige Dollar und beantwortet die Frage
> vielleicht schon:
> ```bash
> ANTHROPIC_API_KEY=... uv run qt alloc --compare-baselines \
>     --allocate-every 96 --effort low --model claude-sonnet-5
> ```
> Auf die letzte Zeile schauen: eine **Rückfallquote über 0%** heißt, der
> Allokator war insoweit heimlich die Equal-Weight-Baseline (ADR-018) — dann
> misst das Gate den Fallback und nicht das Modell.

---

## Was ein Gate-Lauf kostet

Gemessen am Stub-Lauf über BTC/ETH 4h (16.712 Bars, 29 Fenster), Aufrufe je
Fenster-Instanz zugeordnet:

| Einstellung | LLM-Aufrufe | vs. Default |
|---|---|---|
| Default vor ADR-027 | 1450 | — |
| Vorlauf-Verzicht (ADR-027, jetzt Standard) | **580** | −60% |
| + `--allocate-every 96` | **145** | −90% |

Das Briefing ist mit ~1200 Zeichen (≈340 Token) der billigste Teil und der
falsche Ort zum Sparen — die Rechnung hängt an der **Zahl der Aufrufe** und am
**Denk-Aufwand** (`--effort`, ADR-028).

Zwei Dinge, die keine reinen Sparmaßnahmen sind:
- `--allocate-every 96` testet einen *anderen* Allokator (wöchentlich statt
  täglich). Beim Stub-Lauf stand ein Umsatz von 20,6 Mio. auf 100k
  Startkapital — bei 90bps Round-Trip dreht die tägliche Taktung das Buch zu
  Tode. Gut möglich, dass wöchentlich nicht nur billiger, sondern besser ist.
- `--since` zu kürzen spart Fenster und damit statistische Evidenz. Falscher
  Tausch.

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

## 🟡 Phase 3 — LLM-Allokator (gebaut, Wirksamkeit ungeprüft)

- `qt.llm.schemas` — pydantic-Modelle; erfundene Strategie-Labels werden
  verworfen, nicht geraten
- `qt.llm.briefing` — das Blind Briefing (ADR-017): anonym, datumsfrei,
  gerundet, bitgleich für denselben Zustand
- `qt.llm.cache` — Key aus Briefing + Systemprompt + Modell + Effort +
  Schema-Version; macht Backtests reproduzierbar und beim zweiten Lauf gratis
- `qt.llm.client` — strukturierte Ausgaben über `output_format`,
  Prompt-Caching des eingefrorenen Systemprompts, `StubClient` für Läufe ohne
  API-Zugang
- `qt.portfolio.llm_allocator` — jeder Fehler wird zu Gleichgewichtung
  (ADR-018), `AllocatorTelemetry` macht stille Rückfälle sichtbar
- `qt.portfolio.gate` — der Out-of-Sample-Vergleich gegen die Baselines

**Vorführen:**
```bash
uv run qt alloc --compare-baselines            # das Gate aus ADR-004
uv run qt allocators
```

### Was geprüft ist und was nicht

**Geprüft** (31 Tests, keiner ruft ein Modell auf): dass ein *schlechtes* Modell
das System nicht beschädigen kann. Ausfallender Client, halluziniertes Label,
Gewicht 1e9, Antwort die sich zu null summiert, Cache mit falschem Modell — all
das ist abgedeckt.

**Nicht geprüft:** ob ein *gutes* Modell das System verbessert. In der
Bauumgebung gab es keinen API-Schlüssel (ADR-019). Der `StubClient`
gleichgewichtet und ist damit per Konstruktion identisch zur
Equal-Weight-Baseline — er beweist die Verdrahtung, nicht die Idee.

## ✅ Phase 4 — Pfad-Simulation

- `qt.sim.base` — `PathEnsemble`, `PathGenerator`, gewichtete Quantile. Das
  Interface lässt die Antwort "ein einzelner bester Pfad" gar nicht erst zu.
- `qt.sim.bootstrap` — stationärer Block-Bootstrap (Politis/Romano,
  geometrische Blocklängen, zirkulär) plus i.i.d.-Bootstrap als Kontrast
- `qt.sim.regimes` — GARCH(1,1) mit t-Innovationen für Vol-Pfade, HMM für
  Regimewechsel
- `qt.sim.scenarios` — Szenario-Priors: das LLM gewichtet Möglichkeiten,
  es prognostiziert keinen Preis (ADR-024)
- `qt.sim.objective` — Median-Rendite maximieren unter CVaR-Grenze auf dem
  **Drawdown** (ADR-025), Kosten eingerechnet, "kein Trade" ist ein
  mögliches Ergebnis

**Vorführen:**
```bash
uv run qt sim --symbol BTC/USD --tf 4h --paths 10000 --horizon 180
uv run qt sim --generator garch --until 2024-12-31
uv run qt sim --scenarios --stub          # LLM-Priors, ohne API-Zugang
```

### Was der erste echte Lauf zeigt

Das System reagiert auf die Daten, nicht auf eine Meinung:

| Fenster | BTC-Entwicklung | Ergebnis |
|---|---|---|
| letzte 2000 Bars | −44,8% | **kein Trade** |
| bis Ende 2024 | bullisch | Exposure 65–90% je nach Risikobasis |

Und die Modellwahl macht einen Unterschied. Gleiche Daten, gleicher Horizont,
gleiche CVaR-Grenze — nur der Generator variiert:

| Generator | Exposure | zulässige Gitterpunkte |
|---|---|---|
| `iid_bootstrap` | 80% | 16 von 20 |
| `hmm` | 80% | 16 von 20 |
| `stationary_bootstrap` | 75% | 15 von 20 |
| `garch` | **35%** | 7 von 20 |

GARCH mit t-Innovationen erzeugt die fettesten Tails und lässt deshalb am
wenigsten Exposure zu. Welches Modell recht hat, entscheidet diese Tabelle
nicht — sie zeigt nur, dass die Wahl folgenreich ist und deshalb begründet
werden muss.

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
