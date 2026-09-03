# Roadmap

> ## ▶ HIER WEITER
>
> ### Zustand nachsehen, nicht nachlesen
>
> Dieser Abschnitt **behauptet keinen Laufzeitzustand mehr**. Dreimal hat eine
> Prosa-Behauptung hier länger gestimmt als die Wirklichkeit — das Paper-Konto
> lief wochenlang nicht, während hier stand, es laufe (ADR-051); zwei Absätze
> desselben Blocks widersprachen sich (ADR-052); und zuletzt schrieb der
> tägliche Tick seinen Zustand auf einen Zweig, dessen Pull Request längst
> zusammengeführt war (ADR-059). Eine Behauptung veraltet still, ein Befehl
> nicht.
>
> **Der erste Befehl ist nicht optional: der Store ist beim Sitzungsstart
> leer.** `/data/*` ist nicht versioniert, der Container ist frisch, und jeder
> Befehl unten braucht Bars. Rund 20 Minuten, einmal je Sitzung:
>
> ```bash
> KRYPTO="BTC/USD,ETH/USD,LTC/USD,BCH/USD,ETC/USD,XLM/USD,LINK/USD"
> KRYPTO="$KRYPTO,ALGO/USD,ADA/USD,DOGE/USD,DOT/USD,SOL/USD,AVAX/USD,XRP/USD"
> uv run qt data pull --symbols "$KRYPTO" --tf 1d --since 2019-01-01
> uv run qt data pull --symbols "BTC/USD,ETH/USD" --tf 1h,4h --since 2019-01-01
> uv run qt data stocks --since 2019-01-01     # 13 ETFs, braucht TIINGO_API_KEY
> uv run qt data report                        # muss 27 Maerkte zeigen, alle "ok"
> ```
> **Zählen, nicht überfliegen.** Bis ADR-059 schrieb derselbe Befehl neun der
> vierzehn Krypto-Märkte und meldete keinen Fehler: Coinbase antwortet für ein
> Fenster vor der Notierung mit einer leeren Seite, und die galt als Ende der
> Historie. Behoben, aber die Gewohnheit bleibt richtig — `qt data pull` nennt
> jetzt außerdem, was nichts geliefert hat.
>
> ```bash
> uv run qt paper status --strategy macross --symbols BTC/USD --tf 1d
> uv run qt paper status --strategy macross --symbols ETH/USD --tf 1d
> ```
> Erwartet: „Status: laeuft", und **rund vier Fills im Jahr je Konto** —
> `macross` handelt selten, lange Strecken ohne jeden Fill sind der Normalfall
> und kein Hinweis auf einen Fehler. Steht dort „ANGEHALTEN", hat der
> Kill-Switch ausgelöst; das ist der einzige Fall, der eine Entscheidung
> braucht.
>
> Bis hierher stand „über Wochen hinweg **null Fills**". Das war die
> Beobachtung eines Kontos, das noch nie gehandelt hatte, formuliert als
> Erwartung — am 2026-09-03 sind beide Konten zum ersten Mal long gegangen,
> und die Zeile hätte das als Auffälligkeit gelesen.
>
> **Auf `Letzter verarbeiteter Bar` schauen, nicht nur auf „laeuft".** Am
> 2026-09-03 stand dort der 2026-09-02, und im ganzen Repo — auf `main` wie auf
> dem alten Sitzungszweig — lag kein Commit, der den Kontostand über den
> 2026-09-02T05:32Z hinausschreibt. Die Konten liefen also nicht seit über
> einem Tag, während Punkt 1 unten „läuft von selbst" versprach. Der eine Grund,
> der sich finden ließ, ist behoben (der tote Zweig, ADR-059); ob die Routine
> überhaupt feuert, sagt nur der nächste Tag.
>
> **Seit ADR-053 handeln die Konten Gewicht 1,0 statt 0,25.** Vorher formte die
> Risk-Engine mit den Portfolio-Defaults, und das Konto prüfte damit eine
> andere Strategie nach als die gemessene. Der Kill-Switch bleibt an;
> `--shape-risk` schaltet die Formung zurück, falls man den Portfolio-Pfad
> nachstellen will.
>
> ```bash
> bash scripts/paper_tick.sh    # sicher wiederholbar, sichert den Zustand ins Repo
> uv run qt trials              # Versuchszaehler der DSR -- steht bei 16
> uv run qt gate --strategy macross --tf 1d   # Gate 1, alle Kriterien auf einmal
> uv run qt ic --strategy crossmom --tf 1d    # Querschnitts-Rank-IC (ADR-058)
> uv run qt placebo shuffle --strategy <name> --tf 1d   # Negativkontrolle
> ```
> Gate 1 ist seit ADR-057 ein Programm, keine Prosa. Kein Bestandskandidat
> kommt derzeit bis zum Walk-Forward — `macross` scheitert bei 8,8× am
> Umschlagbudget von 7×.
>
> ### Wofür das alles
>
> Das Ziel und der Weg dahin stehen in **`docs/ZIEL.md`**: echtes Geld,
> 12 Monate, besserer Calmar als Buy-and-Hold. Der Termin, der wirklich zählt,
> ist **2027-03-01 (Gate 1)** — bis dahin muss ein Kandidat alle Kontrollen
> bestanden haben, sonst lautet die Antwort „kein Edge gefunden".
>
> Die Zahl, die den Plan diktiert: bei 7,7 Jahren Historie und 3,4 effektiv
> unabhängigen Märkten ist erst ein **Sharpe ab 0,41** beweisbar (ADR-055).
> `macross` hat 0,25. Deshalb steht das Verbreitern der Datenbasis vor jeder
> neuen Strategie-Idee.
>
> ### Der Stand in einem Satz
>
> **Neun Hypothesen geprüft, neun gescheitert — und acht von neun Strategien
> haben jetzt eine Negativkontrolle, die keine besteht.** LLM-Allokator
> **dreimal** (ADR-045/046/060), `hashribbon` (ADR-048), echtes ML (ADR-050),
> die Timeframe-Frage (ADR-047), zwei BTC-Mechanismen vor der ersten Codezeile
> (ADR-048), `macross` selbst (ADR-054) und `crossmom` (ADR-058).
>
> Der dritte Allokator-Lauf ist der, den dieser Block als Punkt 3 verlangt hat:
> `macross` mit im Korb, auf 1d, damit der Allokator etwas zu verteilen hat,
> das gewinnt. Ergebnis **Sharpe −1,50** gegen 0,00 (Gleichgewichtung) und
> +0,58 (Vol-Parität), bei 20 sauberen Aufrufen ohne einen einzigen Rückfall.
> Der Einwand „ein Allokator kann nicht verteilen, was nicht da ist" ist damit
> ausgeräumt und rettet ihn nicht (ADR-060).
>
> Die Kontrollen im Überblick, alle *Datenstand 2026-09-03*, 200 Ziehungen,
> gefordert waren 95 % (ADR-059):
>
> | | Perzentil | | Perzentil |
> |---|---|---|---|
> | `macross` | 75,5 % | `crossmom` | 45,0 % |
> | `trend` | 70,5 % | `crossrev` | 64,5 % |
> | `meanrev` | 38,5 % | `timesfm` | 66,5 % |
>
> `meanrev` liegt **unter** dem Median seiner eigenen Zufallsfassungen. Im
> Querschnitt über 26 Märkte kommen `trend` (−0,17) und `meanrev` (−0,20) auf
> negative Median-Sharpes. `orderflow` ist die einzige Strategie ohne Kontrolle
> — sie bräuchte Handelsdaten, die aus Platzgründen nicht im Repo liegen.
> Ungeprüft ist nicht bestanden.
>
> **Und die Zahl, auf der alles ruht, reproduziert nicht.** Aus einem frisch
> gezogenen Store liefert `macross` OOS-Sharpe **0,25** auf BTC und **0,28** auf
> ETH, nicht die 0,31/0,32, die hier bis ADR-059 standen. Die Fenstergeometrie
> erklärt nur ±0,02 davon; der Rest ist mit dem alten Store nicht mehr
> nachvollziehbar. Der Unterschied hat dieselbe Größenordnung wie der behauptete
> Effekt.
>
> Dazu ein vollständiger Code-Audit (ADR-053): neun Funde, davon fünf, die
> Zahlen verfälscht haben, ohne dass ein Test rot wurde.
>
> **Die Paper-Konten laufen trotzdem weiter, und zwar genau deswegen.** Die
> historischen Daten können die fehlenden unabhängigen Beobachtungen nicht
> liefern; Vorwärtszeit kann es. Was sich geändert hat, ist der Anspruch: die
> Konten prüfen nicht nach, ob eine belegte Strategie hält — sie sammeln die
> Evidenz, die noch fehlt.
>
> ### Was als Nächstes Sinn ergibt
>
> 1. **Nachsehen, ob der Tick wirklich feuert.** Die Routine
>    `Paper-Tick macross BTC+ETH (taeglich)` soll täglich 01:00 UTC in einer
>    frischen Sitzung laufen. Am 2026-09-03 war der Kontostand über einen Tag
>    alt. `scripts/paper_tick.sh` schrieb bis dahin auf einen zusammengeführten
>    Zweig und meldete „Kontostand gesichert" auch nach vier gescheiterten
>    Push-Versuchen; beides ist behoben (ADR-059). Ob damit alles behoben ist,
>    zeigt genau eine Zahl: `Letzter verarbeiteter Bar` muss von Tag zu Tag
>    weiterwandern. Tut er das nicht, ist das kein Wartefall, sondern ein Bug.
> 2. **Die Datenbasis um eine Anlageklasse erweitern, die mit keiner
>    vorhandenen läuft.** Das ist der einzige Hebel, der die beweisbare Schwelle
>    senkt: von 3,4 auf 5 effektive Märkte fällt sie von 0,41 auf 0,33
>    (ADR-055). Volatilität, Zinsdifferenzen, Einzelwerte außerhalb der Indizes
>    — alles, was nicht schon im Korb steckt. Jede Strategie-Idee vorher ist
>    eine Messung, die nicht entscheiden kann.
> 3. **`orderflow` eine Kontrolle geben oder die Strategie streichen.** Sie ist
>    die letzte ohne, und der Grund ist ein Speicherproblem, kein
>    methodisches: 27,8 MB für 59 nutzbare Tage, ein Jahr wären rund 170 MB,
>    und GitHub lehnt Dateien über 100 MB ab. Eine Strategie in der Bibliothek,
>    die sich nicht prüfen lässt, ist eine offene Rechnung.
> 4. **Research-Loop** — der Versuchszähler steht auf 16, und jeder Lauf
>    verschärft die DSR-Schwelle dauerhaft für alle künftigen Kandidaten
>    (ADR-032). In dieser Umgebung ist `NVIDIA_API_KEY` gesetzt und ein
>    Gate-Lauf über `--provider nim` kommt durch (ADR-060); der Blocker, den
>    `docs/ZIEL.md` in Phase C.2 nennt, gilt hier nicht mehr. Das Budget ist
>    trotzdem nicht gratis — und `qt alloc --stub` nennt vorher in der
>    Telemetriezeile, was ein echter Lauf kostet.
>
> **Der LLM-Allokator steht nicht mehr auf dieser Liste.** Er stand hier als
> Punkt 3 mit einem berechtigten Vorbehalt; der ist geprüft und erledigt
> (ADR-060). Ein vierter Lauf wäre eine weitere Konfiguration auf denselben
> Daten — was fehlt, ist ein zweites Testfenster, und das liefert nur eine
> breitere Datenbasis oder Vorwärtszeit.
>
> **Order-Flow bleibt herabgestuft, und zwar aus einem gemessenen Grund.** Der
> Punkt stand hier lange auf Platz 2 mit der Begründung, er brauche „dieselbe
> Sicherungslogik wie der Paper-Zustand". Das geht nicht auf: der Paper-Zustand
> sind 2 KB JSON, die Trades sind 27,8 MB für 59 nutzbare Tage. Dazu lebt Order
> Flow auf 4h, und ADR-047 hat für 4h gemessen, dass die Gebühren dort *jede*
> getestete Strategie von positiv auf −0,65 bis −1,60 Sharpe ziehen.
>
> **Was ausdrücklich nicht empfohlen wird:** noch eine Strategie-Idee. Nicht
> weil Ideen schlecht wären, sondern weil dieses Projekt gerade achtmal
> gezeigt hat, dass es sie zuverlässig widerlegt — und jede kostet einen
> Versuch im Nenner.

---

## Konvention für jede Zahl in diesem Dokument

**Jede Ergebnistabelle nennt ihren Datenstand.** Ohne ihn driftet sie mit
jedem `qt data pull` still weiter: der In-Sample-Faktor von `macross` stand
hier als 15,2 und war beim Nachrechnen 16,0, Buy & Hold als 16,8 und war
20,1 — nichts davon war je falsch, nur undatiert (ADR-053). Dieselbe Lehre
wie bei ADR-051/052, eine Ebene tiefer: eine Behauptung ohne Schnitt veraltet,
ohne dass es jemandem auffällt.

---

## Die erste Strategie, die Geld verdient (ADR-035)

```bash
uv run qt backtest --strategy macross --symbol BTC/USD --tf 1d
uv run qt wf --strategy macross --symbol BTC/USD --tf 1d --train 1000 --test 250 --embargo 20
```

Zwei Hebel, die das Projekt nie gezogen hatte: **Tagesbasis statt 4h** (66 statt
889 Trades — Frequenz ist Kosten) und **long/flach statt long/short** (die
Gegenrichtung kämpft gegen die stärkste Drift im Datensatz).

**Out-of-Sample, Fenster 2021-10 bis 2026-08** *(Datenstand 2026-08-24)*:

| | macross | Buy & Hold |
|---|---|---|
| Faktor | **1,25** | 1,03 |
| Sharpe | **0,31** | 0,27 |
| Max Drawdown | **−51,0%** | −76,7% |

Nachgerechnet am 2026-09-01 (7 OOS-Fenster, 1000/250/20): Sharpe **0,31** auf
BTC und **0,32** auf ETH.

> **Nachgerechnet am 2026-09-03 aus einem frisch gezogenen Store: 0,25 auf BTC
> und 0,28 auf ETH.** Dieselben Fenster, dieselben Parameter, dieselbe
> Symbolquelle — nur ein Store, der neu von Coinbase geholt wurde, weil
> `/data/*` nicht versioniert ist. Verschiebt man den Anker des Walk-Forward um
> 1 bis 10 Bars, wandert der BTC-Wert zwischen 0,229 und 0,266; die
> Fenstergeometrie erklärt die Differenz also nicht. Woran es sonst liegt, ist
> ohne den alten Store nicht mehr feststellbar (ADR-059). Die Zahl, auf der die
> Auswahl von `macross` fürs Paper-Trading ruht, ist damit **nicht
> reproduzierbar**, und der Unterschied ist so groß wie der behauptete Effekt.

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
einzige Kandidat mit positiver Out-of-Sample-Kennzahl, auf BTC und auf ETH
ohne Neuanpassung. *Nicht* „mit echter Evidenz" — so stand es hier, und
ADR-054 hat es widerlegt: die Permutationskontrolle ist nicht bestanden.
Es bleibt der beste verfügbare Kandidat, nicht ein belegter.

Seit ADR-059 gilt derselbe Satz für alle: acht der neun Bibliotheksstrategien
haben eine Negativkontrolle, keine besteht sie. `macross` liegt mit Perzentil
75,5 % im Mittelfeld dieses Feldes — schlechter als nichts ist es nicht, ein
Beleg aber auch nicht.

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

*Datenstand 2026-08-24. Datenlage: 7,6 Jahre, 99,97% Abdeckung, keine
kaputten Bars.* Alle Zahlen sind **In-Sample** und wandern mit dem Datenstand
— `macross` steht am 2026-09-01 bei Faktor 16,0 / Sharpe 1,04, Buy & Hold bei
20,1 / 0,94. Die belastbaren Zahlen stehen weiter oben, out-of-sample.

Nachgezogen am 2026-09-02: der Gebührensatz im Kostenmodell stand auf der
falschen Zeile der Coinbase-Staffel (40 statt 60 bps, ADR-056). Der Round-Trip
ist von 90 auf 130 bps gestiegen, **alle vier Zeilen wurden dadurch
schlechter**, und die alten Zahlen standen bis dahin hier, als seien sie
aktuell. Nachrechnen: `uv run qt costs --symbol BTC/USD --tf 4h`.

| Lauf | Ergebnis (130 bps) | vorher (90 bps) | Buy & Hold |
|---|---|---|---|
| `trend` BTC/USD 4h | Faktor 0,14 · Sharpe −0,44 | 0,46 · −0,06 | Faktor 17,4 · Sharpe 0,92 |
| `meanrev` ETH/USD 1h | Faktor 0,00 · Sharpe −2,37 | 0,00 · −2,08 | Faktor 14,6 · Sharpe 0,84 |
| `elliott` BTC/USD 4h | Faktor 0,01 · Sharpe −0,87 | 0,03 · −0,68 | Faktor 17,4 · Sharpe 0,92 |
| **`macross` BTC/USD 1d** | **Faktor 14,15 · Sharpe 1,00 · MaxDD −58%** | 15,2 · 1,03 | Faktor 16,8 · Sharpe 0,91 · MaxDD −77% |

Die letzte Zeile ist die interessante: `macross` verliert durch 40 bps mehr
Round-Trip-Kosten **0,03 Sharpe**, `trend` auf 4h verliert 0,38. Das ist der
Frequenzeffekt aus ADR-056 in zwei Zahlen.

Beide verlieren deutlich — wie erwartet. Die Diagnose ist aber unterschiedlich, und
genau das ist der Wert dieser Phase:

- **`trend` scheitert an den Kosten, nicht am Signal.** Ohne Gebühren macht dieselbe
  Strategie Faktor 6,44, bei Maker-Gebühren 4,04, bei Coinbase-Taker-Gebühren 0,46.
  Details in ADR-009 — die Zahlen reproduzieren bis heute, aber sie gelten für
  **4h**. Auf 1d kostet dieselbe Annahme nur 0,08 bis 0,23 Sharpe (ADR-056).
  Nachzusehen mit `uv run qt costs --symbol BTC/USD --tf 1d`.
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
