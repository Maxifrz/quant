# Roadmap

> ## ▶ HIER WEITER
>
> **Der LLM-Allokator ist zweimal durchgefallen, und die naheliegende
> Reparatur ist widerlegt.** Takt 96: Sharpe −1,18. Takt 384: −2,19, während
> jede regelbasierte Baseline besser wurde (ADR-046). Weniger Umsatz,
> schlechteres Ergebnis — die Kostenthese ist zweimal unabhängig tot.
>
> Parallel dazu: `1d` schlägt `4h` und `1h` in **8 von 8** Strategie/Symbol-
> Kombinationen, und oberhalb von `1d` hört jedes Muster auf (ADR-047).
>
> **Der nächste Schritt ist deshalb nicht ein weiterer Parameter am
> Allokator, sondern ein Korb, in dem etwas Verdienendes liegt.** Alle
> bisherigen Gate-Läufe verteilten `trend` und `meanrev` auf `4h` — beide
> verlieren dort dreistellig. Ein Allokator kann nicht verteilen, was nicht
> da ist.
>
> ```bash
> NVIDIA_API_KEY=nvapi-... uv run qt alloc --compare-baselines \
>     --strategies macross,trend,meanrev --tf 1d \
>     --allocate-every 24 --effort low --provider nim
> ```
> Vorher prüfen, wie viele Aufrufe das ergibt (`--stub` und die
> Telemetriezeile lesen) — bei `1d` sind die Testfenster in Bars kürzer,
> der Lauf also billiger als die 145 des 4h-Laufs.
>
> **Danach offen, nach Wert sortiert:**
>
> 1. **Paper-Trading auf einer Maschine, die überlebt.** `data/paper/` ist
>    leer; `/data/` ist gitignored und der Container wird neu gebaut. Ein
>    Paper-Konto braucht Persistenz und eine Uhr — beides hat eine flüchtige
>    Session nicht. Das ist der einzige offene Punkt, der mit Wartezeit statt
>    mit Rechenzeit bezahlt wird, also der, der am längsten braucht.
> 2. **Ein Jahr Order-Flow für `orderflow`.** Der Bestand täuscht: 2,4 Mio
>    Trades über 361 Tage Spanne, aber mit einem **301-Tage-Loch**. Real sind
>    zwei Blöcke, 33 und 26 Tage. Fortsetzbar über `qt data trades --days 365`.
> 3. **Research-Loop erneut laufen lassen** — aber der Versuchszähler steht
>    auf 8, und jeder Lauf verschärft die DSR-Schwelle dauerhaft für alle
>    künftigen Kandidaten (ADR-032). Das Budget ist nicht gratis.
>
> **Was gemessen und erledigt ist:** Phase 3 (Gate, zweimal, ADR-045/046),
> Phase 5 (Research-Loop, ADR-042), die Timeframe-Frage (ADR-047). Der
> LLM-Cache des 4h-Laufs ist versioniert — diese Zahlen sind in jedem
> Container in Minuten reproduzierbar statt in vier Stunden.

---

## Die erste Strategie, die Geld verdient (ADR-035)

```bash
uv run qt backtest --strategy macross --symbol BTC/USD --tf 1d
uv run qt wf --strategy macross --symbol BTC/USD --tf 1d --train 1000 --test 250 --embargo 20
```

Zwei Hebel, die das Projekt nie gezogen hatte: **Tagesbasis statt 4h** (66 statt
889 Trades — Frequenz ist Kosten) und **long/flach statt long/short** (die
Gegenrichtung kämpft gegen die stärkste Drift im Datensatz).

**Out-of-Sample, Fenster 2021-10 bis 2026-08:**

| | macross | Buy & Hold |
|---|---|---|
| Faktor | **1,25** | 1,03 |
| Sharpe | **0,31** | 0,27 |
| Max Drawdown | **−51,0%** | −76,7% |

20 von 25 Gitterpunkten verdienen Geld, und das Muster **repliziert auf ETH
ohne Neuanpassung** (dort Faktor 1,00 gegen 0,49 bei Buy & Hold).

**Trotzdem: DSR über 0,95 bei null von 25 Punkten.** Bei Sharpe 0,64 über 1.751
Tagesbars liegt der t-Wert bei 1,4 — man kann einen Sharpe von 0,6 mit 4,8
Jahren Tagesdaten nicht beweisen. Das ist die Datenmenge, nicht die Strategie.

Damit ist `macross` der erste Kandidat für **Phase 6 (Paper-Trading)** — nicht
weil sie bewiesen wäre, sondern weil Paper-Trading genau die fehlenden
Beobachtungen sammelt und ihr Risikoprofil den Irrtum billig macht.

---

## Phase 6 — Paper-Trading (ADR-037, ADR-038)

```bash
uv run qt paper run --strategy macross --symbols BTC/USD --tf 1d
uv run qt paper status --strategy macross --symbols BTC/USD --tf 1d
uv run qt paper reset-killswitch --strategy macross --symbols BTC/USD --tf 1d --note "..."
```

**Kein Daemon.** `run_paper_tick` ist ein einzelner, sicher wiederholbarer
Aufruf — diese Sitzung hat mehrfach gezeigt, dass Hintergrundprozesse in
dieser Umgebung Container-Neustarts nicht überstehen. Bewiesen, nicht nur
behauptet: derselbe Datensatz, einmal in einem Tick und einmal mit einem
erzwungenen Neustart nach *jedem einzelnen* Bar, ergibt bitgleiche Konten.

**Wiederverwendet statt neu gebaut:** `SimBroker` (Docstring versprach das
seit Phase 1), `qt.portfolio.risk.RiskEngine` als Kill-Switch (seit Phase 2),
`LiveClock` (seit Phase 0). Neu ist nur der Zustands-Cursor
(`PaperState.last_processed_ts`) und die Erkenntnis, die ihn nötig machte.

**Gefunden im ersten echten Lauf gegen Coinbase:** `fetch_ohlcv` gab einen
Bar zurück, dessen Close-Zeit einen Tag in der Zukunft lag — die gerade erst
offene Tageskerze. `run_paper_tick` verwirft jeden Bar mit `close_ts > now`
jetzt vollständig, bevor er den Feature-Store erreicht (ADR-038). Der
Backtest-Pfad war davon nie betroffen — dort sind Bars immer längst
geschlossen.

**Warum `macross` und nicht `orderflow` oder `elliott`:** `macross` ist der
einzige Kandidat mit echter Evidenz (ADR-035) — Out-of-Sample positiv,
repliziert auf ETH ohne Neuanpassung. Paper-Trading ist der teuerste Weg,
eine ungeprüfte Strategie zu testen; hier ist wenigstens die Vorprüfung
gemacht.

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
| `elliott` BTC/USD 4h | Faktor 0,03 · Sharpe −0,68 | Faktor 17,4 · Sharpe 0,92 |
| **`macross` BTC/USD 1d** | **Faktor 15,2 · Sharpe 1,03 · MaxDD −57%** | Faktor 16,8 · Sharpe 0,91 · MaxDD −77% |

Beide verlieren deutlich — wie erwartet. Die Diagnose ist aber unterschiedlich, und
genau das ist der Wert dieser Phase:

- **`trend` scheitert an den Kosten, nicht am Signal.** Ohne Gebühren macht dieselbe
  Strategie Faktor 6,44, bei Maker-Gebühren 4,04, bei Coinbase-Taker-Gebühren 0,46.
  Details in ADR-009.
- **`elliott` scheitert ebenfalls am Signal** (ADR-033). Ohne jede Gebühr bleibt
  Faktor 0,14 bei Sharpe −0,25; mit Kosten 0,03. Die Wellenzählung findet Muster,
  aber die Muster sagen nichts über den nächsten Bar. Walk-Forward: 7 von 17
  Fenstern positiv.
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

## ✅ Phase 5 — Research-Loop

- `qt.research.sandbox` — AST-Whitelist statt Blockliste, plus Fassaden für
  `np` und `ta`, damit eine erlaubte *Syntax* nicht doch eine verbotene
  *Methode* erreicht (ADR-029)
- `qt.research.dsr` — Deflated Sharpe Ratio gegen alle je getesteten
  Kandidaten (ADR-005)
- `qt.research.registry` — DuckDB; der Versuchszähler ist eine abgeleitete
  Abfrage, kein gepflegtes Feld (ADR-032)
- `qt.research.generator` — Briefing **ohne jede** datenabgeleitete Zahl
- `qt.research.critic` — adversariale Kritik als billiger Vorfilter vor dem
  teuren Backtest (ADR-031)
- `qt.research.screening` — Sanity-Check, Walk-Forward, DSR
- `qt.research.loop` — die Orchestrierung samt Trichter-Telemetrie

**Vorführen:**
```bash
uv run qt research --generate 3 --screen --stub
uv run qt research --show <id>          # voller Audit-Pfad, nur lesen
```

### Die Reihenfolge ist nach Kosten sortiert

```
Generator (LLM)  ->  Sandbox (gratis)  ->  Kritik (billig)  ->  Walk-Forward + DSR (teuer)
```

Jede Stufe, die früher ablehnt, spart alle folgenden. Der Sanity-Check
zwischen Kritik und Walk-Forward verwirft nur, was technisch nichts liefert —
eine In-Sample-Schwelle auf die Rendite wäre eine Vorauswahl auf denselben
Daten, gegen die anschließend out-of-sample geprüft wird.

### Was der erste Stub-Lauf zeigt

Zwei Läufe hintereinander, je über BTC/USD 4h ab 2021:

| | Lauf 1 | Lauf 2 |
|---|---|---|
| Versuchszähler vorher | 0 | 3 |
| erzeugt / gescreent | 3 / 3 | 2 / 2 |
| DSR bestanden | 0 | 0 |
| Versuchszähler nachher | 3 | **5** |

Der Zähler überlebt den Prozesswechsel — die DSR des vierten Kandidaten
rechnet gegen 4 Versuche, nicht wieder gegen 1. Genau das ist der Unterschied
zwischen einem Overfitting-Schutz und einem, der bei jedem Neustart vergisst.

Dass alle durchfallen, ist der Normalfall: der Stub-Generator liefert
absichtlich schwache z-Score-Reversionen mit Sharpe zwischen −4 und −6.

### Promotion bleibt Handarbeit

Es gibt bewusst **keinen** Befehl, der einen bestandenen Kandidaten nach
`strategy/library/` schreibt. `--show <id>` druckt ihn, kopieren muss ein
Mensch, `--mark-promoted <id>` hält es nur fest. Eine automatische Übernahme —
auch hinter einer Bestätigung — wäre der schleichende Weg, die Freigabe
abzuschaffen.

## ✅ Phase 6 — Paper-Trading

- `qt.live.runner` — `run_paper_tick`, ein sicher wiederholbarer Aufruf statt
  eines Daemons (ADR-037). `SimBroker` und `qt.portfolio.risk.RiskEngine`
  wiederverwendet, nicht neu gebaut.
- `qt.live.state` — der einzige persistierte Zustand: Broker-Konto plus ein
  Zeitstempel-Cursor. Feature-Store und Uhr werden bei jedem Tick aus der
  durablen Bar-Historie neu aufgebaut.
- `qt.live.killswitch` — manuelles Zurücksetzen des Kill-Switches, mit
  Begründung in der Historie.
- `qt.report.daily` — Tagesreport als Text, Kill-Switch-Status zuerst.
- **`qt.live.reconcile` bewusst nicht gebaut:** Soll- vs. Ist-Vergleich setzt
  zwei unabhängige Quellen voraus. Mit nur einem `SimBroker` gibt es noch
  nichts, wogegen abgeglichen werden könnte — das wird erst mit einem
  zweiten, echten Broker in Phase 7 zu einer sinnvollen Prüfung.
- Gefunden im ersten echten Lauf: eine noch offene Tageskerze wurde von der
  Exchange als geschlossen ausgegeben (ADR-038). Behoben, bevor produktiv
  gelaufen wurde.

**Noch zu tun, keine Code-Aufgabe mehr:** über Wochen laufen lassen und
Live-vs-Backtest-Divergenz messen (siehe ROADMAP-Marker oben).

**Vorführen:**
```bash
uv run qt paper run --strategy macross --symbols BTC/USD --tf 1d
uv run qt paper status --strategy macross --symbols BTC/USD --tf 1d
```

## ⬜ Phase 7 — Live (separate Entscheidung)

Erst wenn Phase 6 über Wochen sauber läuft. Exchange-Keys, `qt.live.broker_ccxt`,
Mini-Kapital, harte Positionslimits. Das ist eine eigene Entscheidung mit echtem
Geld — keine Fortsetzung der Bauarbeit.
