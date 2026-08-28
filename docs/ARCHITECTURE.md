# Architektur

## Ziel

Ein Krypto-Handelssystem, in dem ein LLM zwei Rollen hat:

1. **Allokator** — verteilt live Risikobudget über ein Portfolio deterministischer Strategien.
2. **Forscher** — generiert offline neue Strategie-Kandidaten, die eine harte statistische Prüfung bestehen müssen.

Das LLM handelt nicht selbst. Es entscheidet *worüber* gehandelt wird, nicht *wie* ausgeführt wird.

---

## Das tragende Prinzip: eine Engine, drei Uhren

Backtest, Paper-Trading und Live laufen durch **denselben Code**. Sie unterscheiden sich
ausschließlich in zwei austauschbaren Komponenten: der Clock und dem Broker.

```
                  ┌────────────────┐
BacktestClock  →  │                │  →  SimBroker    (Parquet-Replay, simulierte Fills)
PaperClock     →  │   qt.engine    │  →  SimBroker    (Live-Daten, simulierte Fills)
LiveClock      →  │                │  →  CcxtBroker   (echte Orders)
                  └────────────────┘
```

Backtest-vs-Live-Divergenz entsteht in der Praxis fast immer dadurch, dass es zwei
getrennte Codepfade gibt: einer, der über einen DataFrame vektorisiert, und einer, der
live auf Events reagiert. Sie driften auseinander, und der Backtest wird zur Fiktion.
Hier gibt es nur einen Pfad.

Konsequenz: die Engine ist event-getrieben, nicht vektorisiert. Das ist langsamer, aber
es ist die einzige Bauform, in der ein Backtest überhaupt etwas über das Live-Verhalten
aussagt.

---

## Signalfluss

```
Bars → Features → Strategien → Zielgewichte → Allokator → Risk-Engine → Orders → Fills
        (PIT)     (determin.)   (−1…+1)       (LLM,        (hart,
                                               schlägt vor) entscheidet)
```

### Strategien geben Gewichte, keine Orders

Eine Strategie gibt ein **Zielgewicht** pro Symbol in ihrem eigenen Buch aus, im Bereich
−1…+1. Sie weiß nichts über Kontogröße, Hebel oder andere Strategien.

Das entkoppelt drei Dinge, die sonst gern verschmelzen:

| Verantwortung | Zuständig |
|---|---|
| *Wohin zeigt der Markt?* | Strategie |
| *Wieviel Kapital darauf?* | Allokator |
| *Was ist maximal erlaubt?* | Risk-Engine |

Man kann so eine Strategie austauschen, ohne das Risikomodell anzufassen — und umgekehrt.

### Der Allokator schlägt vor, die Risk-Engine entscheidet

Der LLM-Output ist ein Vorschlag. Er läuft danach durch nicht verhandelbare Clamps:

- Max-Gewicht pro Strategie
- Max-Brutto-Exposure über das Portfolio
- Vol-Targeting (Positionsgröße skaliert invers zur realisierten Vola)
- Drawdown-Kill-Switch (hält an, erfordert manuelles Zurücksetzen)

Ein halluzinierter oder böswilliger Gewichtsvektor kann das Konto damit nicht sprengen.
Das ist bewusst so herum gebaut: das LLM ist die kreative, unzuverlässige Komponente;
die Risikoschicht ist dumm, deterministisch und überprüfbar.

---

## Point-in-Time: die Regel, die alles trägt

Ein Bar wird **erst nach seinem Close sichtbar**. Ein Signal, das auf dem Close von
Bar *t* entsteht, wird auf dem Open von Bar *t+1* ausgeführt — nie auf dem Close, auf
dem es entstanden ist.

Erzwungen wird das an zwei Stellen:

1. `qt.core.clock.Clock` kennt einen `now`. `qt.features.registry.FeatureStore` wirft
   eine `LookaheadError`, wenn Daten jenseits von `now` angefragt werden.
2. Ein Test (`tests/test_lookahead.py`) verändert *zukünftige* Bars und prüft, dass die
   Features bitidentisch bleiben.

Lookahead-Bias ist der häufigste Grund, warum ein Backtest großartig aussieht und live
nichts davon übrig bleibt. Deshalb ist es hier kein Review-Thema, sondern ein Test.

---

## Kostenmodell

`qt.backtest.costs` modelliert Taker-Fees, Spread und Slippage. Die Defaults sind
bewusst pessimistisch.

Ohne Kostenmodell ist jeder Backtest eine Lüge — besonders bei kurzen Timeframes, wo
Gebühren und Slippage den gesamten Edge auffressen können. Deshalb ist es Teil der
ersten Bauphase und nicht ein späterer "Realismus-Layer".

---

## Wo das LLM sitzt (Phase 3, gebaut)

Die Kette vom Modell zum Konto, mit den Prüfstufen dazwischen:

```
AllocationContext                      (nur Point-in-Time-Daten)
        ↓  qt.llm.briefing
Blind Briefing                         anonym, datumsfrei, gerundet
        ↓  qt.llm.cache                Treffer? → kein Modellaufruf
        ↓  qt.llm.client               output_format legt das Schema fest
AllocationProposal                     pydantic validiert ein zweites Mal
        ↓  qt.portfolio.llm_allocator  erfundene Labels → verworfen
        ↓                              jeder Fehler → Gleichgewichtung
Allocation je Strategie
        ↓  qt.portfolio.risk           beschneidet hart
Zielgewichte je Symbol
```

Fünf Stufen, jede nimmt an, dass die vorherige Unsinn geliefert haben könnte.

### Blind Briefings

Ein LLM kennt die Vergangenheit. Fragt man es "wie hättest du im März 2020
allokiert", weiß es die Antwort — und der Backtest des Allokators wird wertlos,
ohne dass es irgendwo einen Bug gibt.

Das Briefing enthält deshalb **keine Datumsangaben, keine Asset-Namen, keine
Strategienamen, keinen Kontostand** — nur normalisierte Kennzahlen unter
anonymen Labels (`STRAT_A`, `STRAT_B`). Fenster werden in Bars angegeben, nicht
in Tagen; "30 Tage" wäre bereits eine Zeitangabe. Zahlen sind auf drei Stellen
gerundet, weil exakte Fließkommawerte ein Fingerabdruck sind.

Das entfernt das Problem nicht vollständig — ein Einbruch von −50% bei
verdreifachter Volatilität ist auch anonymisiert wiedererkennbar. Deshalb gilt
zusätzlich: **der Allokator wird primär am Forward-Paper-Trading gemessen,
nicht am Backtest.**

Neun Tests halten das fest, darunter einer, der prüft, dass ein Kontostand von
1.000 und einer von 50.000.000 dasselbe Briefing erzeugen.

### Reproduzierbarkeit

Jeder Aufruf wird gecacht, Key = Hash(Briefing + Systemprompt + Modell-ID +
Effort + Schema-Version). Ein Backtest über den Allokator ist damit wiederholbar
und beim zweiten Lauf kostenlos. Ohne Cache ist ein LLM-Backtest weder
reproduzierbar noch bezahlbar.

Dass das Briefing **bitgleich** sein muss, ist kein Nebenaspekt, sondern die
Voraussetzung dafür: ein `datetime.now()` darin hätte einen Cache mit 0%
Trefferquote erzeugt, ohne dass irgendetwas fehlschlägt.

### Ausfall ist ein langweiliges Ereignis

`LLMAllocator` fängt jede Exception und gibt Gleichgewichtung zurück. Ein
Allokator, der bei einem Netzwerkfehler wirft, reißt im Livebetrieb das System
mit — genau dann, wenn die Verbindung ohnehin schlecht ist.

Die Kehrseite: ein Allokator, der dauerhaft zurückfällt, ist heimlich eine
Baseline und wird für gut gehalten, weil er nie auffällt. Deshalb zählt
`AllocatorTelemetry` die Rückfälle mit, und die Rückfallquote gehört in jeden
Report.

### Das Gate

Der LLM-Allokator geht nur weiter, wenn er **out-of-sample** die Baselines aus
`qt.portfolio.baselines` schlägt: Equal-Weight, Vol-Parity, Best-Single. Alle
drei, nicht die schwächste.

Tut er das nicht, ist das ein Ergebnis und kein Fehler. Ein LLM, das nicht
besser allokiert als Vol-Parity, gehört nicht in den Kreislauf.

## Der Research-Loop (Phase 5)

Ein LLM, das 500 Strategien generiert, findet garantiert welche mit gutem Sharpe —
auch wenn alle 500 reines Rauschen sind. Das ist keine Hypothese, sondern Statistik.

Deshalb ab Tag 1:

- **Sandbox**: Kandidaten werden per AST-Whitelist geprüft. Keine Imports, keine I/O,
  keine Netzwerkzugriffe.
- **Deflated Sharpe Ratio** gegen die Anzahl *aller je getesteten* Kandidaten, nicht
  nur der aktuellen Charge. Die Registry zählt mit.
- **Walk-Forward-OOS** mit Purging und Embargo.
- **Promotion nur mit manueller Freigabe.**

---

## Pfad-Simulation (Phase 4, gebaut)

Die ursprüngliche Projektidee war, "den wahrscheinlichsten Verlauf zu traden".
Genau so formuliert ist das der klassische Weg, Geld zu verlieren: ein einzelner
prognostizierter Pfad ist eine Wette, keine Kante. Wer ihn trifft, hatte Glück;
wer ihn verfehlt, hat nichts, worauf er zurückfällt.

Die tragfähige Version derselben Idee: ein **Ensemble** möglicher Pfade erzeugen
und die Allokation wählen, die über das ganze Ensemble hinweg gut abschneidet.

```
historische Renditen
        ↓  qt.sim.bootstrap / qt.sim.regimes
PathEnsemble                    viele mögliche Zukünfte, gleichgewichtet
        ↓  qt.sim.scenarios     LLM gewichtet Möglichkeiten (kein Preis!)
PathEnsemble                    umgewichtet, Verzerrung gedeckelt
        ↓  qt.sim.objective
Exposure                        Median maximiert unter CVaR-Grenze
```

`PathEnsemble` hat bewusst **keine** Methode, die einen einzelnen "besten" Pfad
zurückgibt. Das Interface lässt diese Antwort gar nicht erst zu.

### Die vier Generatoren

| Generator | Was er erhält | Grenze |
|---|---|---|
| `stationary_bootstrap` | Autokorrelation, Vol-Clustering | nur Regime, die vorkamen |
| `iid_bootstrap` | nur die Randverteilung | Kontrast, kein Produktivmodell |
| `garch` | Vola als Prozess mit Persistenz | Parametrisch, Verteilungsannahme |
| `hmm` | diskrete Regimewechsel | Zustandszahl ist eine Setzung |

Der i.i.d.-Bootstrap steht nicht da, weil er gut wäre, sondern damit "erhält
Vol-Clustering" eine gemessene Aussage ist und keine Behauptung.

### Die Grenze bezieht sich auf den Drawdown

Ein Konto wird nicht am Ende des Horizonts liquidiert, sondern unterwegs. Ein
Pfad, der zwischenzeitlich 60% verliert und bei −10% endet, ist real ein
Totalschaden — die Endrendite sieht ihn nicht.

Dazu kommt ein empirischer Grund (ADR-025): Block- und i.i.d.-Resampling
unterscheiden sich beim kurzfristigen Drawdown deutlich, beim 5%-Quantil der
Endrendite über lange Horizonte praktisch gar nicht. Eine Grenze auf der
Endrendite misst also ausgerechnet die Größe, bei der die Modellwahl folgenlos
bleibt.

### Szenario-Priors: gewichten statt prognostizieren

Ein Sprachmodell nach einem Kursziel zu fragen, wäre die schlechteste denkbare
Verwendung: eine Punktprognose ohne Fehlerbalken aus einem Modell, das keine
Preisreihen rechnet.

Was es kann: sagen, welche *Art* von Verlauf gerade wahrscheinlicher ist. Ein
Prior benennt eine messbare Pfad-Eigenschaft (`volatility`, `terminal_return`,
`max_drawdown`), eine Richtung und eine Stärke — das Schema ist so geschnitten,
dass eine Preisprognose gar nicht ausdrückbar ist.

Drei Eigenschaften, die das gegenüber einer direkten Prognose hat: der Prior
kann nicht ins Unendliche danebenliegen (er verschiebt nur Gewichte innerhalb
eines Ensembles aus echten historischen Eigenschaften), er ist beschränkbar
(`max_tilt`), und er ist im Nachhinein prüfbar.

---

## Modulübersicht

| Modul | Zweck |
|---|---|
| `qt.core` | Clock, Events, Typen, Config — die Begriffe, die alle teilen |
| `qt.data` | Ingest (ccxt), Parquet-Store, Integritätsprüfung |
| `qt.features` | Point-in-Time-Feature-Store, TA-Bausteine, Regime-Features |
| `qt.strategy` | Strategie-Interface, Registry, Bibliothek (Trend, Mean-Reversion, TimesFM-Forecast) |
| `qt.backtest` | Engine, SimBroker, Kosten, Metriken, Walk-Forward |
| `qt.portfolio` | Baselines, LLM-Allokator, Risk-Engine |
| `qt.sim` | Pfad-Ensembles: Bootstrap, GARCH/HMM, Szenario-Priors, CVaR-Zielfunktion |
| `qt.research` | Strategie-Generator, Sandbox, Screening, Registry |
| `qt.llm` | Client, Briefing-Bau, Output-Schemas, Cache |
| `qt.live` | Runner, CCXT-Broker, Reconciliation, Kill-Switch |
| `qt.report` | Tearsheets, Tagesreport |

Gebaut sind aktuell: `core`, `data`, `features`, `strategy`, `backtest`,
`portfolio`, `llm`, `sim`, `report`. Offen sind `research` und `live` — siehe
`ROADMAP.md`.

---

## Die Portfolio-Schicht (Phase 2)

`qt.portfolio.base` definiert die Verträge, gegen die alles Weitere gebaut wird:

| Baustein | Rolle |
|---|---|
| `Allocator` | verteilt Kapital auf Strategien — ab Phase 3 ein LLM |
| `AllocationContext` | was der Allokator sehen darf; strikt Point-in-Time |
| `combine()` | Strategie-Gewichte × Kapitalanteile → Portfolio-Gewichte |
| `RiskLimits` | beschneidet das Ergebnis; deterministisch und überprüfbar |

`qt.backtest.portfolio_engine` verdrahtet sie. Drei Punkte, die dort nicht
offensichtlich sind:

**Der Allokator läuft auf einem langsameren Takt als die Strategien.**
`allocate_every` zählt Bars des gröbsten Timeframes im Lauf. Ab Phase 3 ist das
zwingend — ein LLM-Aufruf pro Minutenbar ist weder bezahlbar noch sinnvoll.

**Die Papier-Renditen für den Allokator sind brutto.** Würde man ihnen Kosten
aufbürden, hinge der Vergleich zwischen Strategien an der Positionsgröße, die
der Allokator selbst vergeben hat — eine Rückkopplung, in der eine zufällig
klein gestartete Strategie klein bliebe. Die tatsächliche Kontoentwicklung
enthält selbstverständlich alle Kosten.

**Die Ausgabe des Allokators wird geprüft, nicht geglaubt.** `nan`, `inf`,
unbekannte Strategie-IDs und Summen über 100% werden abgefangen; im Zweifel
fällt das Portfolio auf Gleichgewichtung zurück statt in einen undefinierten
Zustand zu geraten. Das ist die Vorbereitung darauf, dass an dieser Stelle bald
ein Sprachmodell steht.

### Walk-Forward

`qt.backtest.walkforward` ist die einzige Quelle belastbarer Zahlen im System.
Alles, was `qt backtest` ausgibt, ist In-Sample.

Zwei Details entscheiden über die Gültigkeit: der Warmup jedes Fensters liegt
**vor** dem Testfenster (sonst verbrennt man dessen Anfang), und jedes Fenster
bekommt eine **frische Strategie-Instanz** (sonst wandert Zustand über
Fenstergrenzen). Beides ist getestet, nicht nur beabsichtigt.

Aussagekräftiger als der Gesamtsharpe ist die Streuung über die Fenster: eine
Strategie, die in einem von siebzehn Fenstern alles verdient, ist eine
Zufallsstichprobe und keine Kante.

## Paper-Trading (Phase 6, gebaut)

**Korrektur einer Doku-Luecke:** Die ROADMAP verwies hier lange auf eine
Empfehlung, die Ausfuehrung fuer Phase 6/7 auf Nautilus aufzusetzen statt den
`SimBroker` auszubauen -- dieser Abschnitt existierte nie. Die tatsaechliche
Entscheidung: **Paper-Trading bleibt in diesem Repo**, weil das Fundament
dafuer seit Phase 0 gelegt ist. `qt.core.clock.LiveClock` stand von Anfang an
neben `BacktestClock`, und `SimBroker`s eigener Docstring versprach schon
laenger: "Live wird er durch `qt.live.broker_ccxt` ersetzt, das dieselbe
Schnittstelle erfuellt." Nautilus fuer die Ausfuehrung zu verwenden waere eine
neue Systemgrenze mit eigenem Datenmodell -- gerechtfertigt, falls die
Anforderungen an Order-Typen oder Boersenanbindung ueber das hier gebaute
Mass hinauswachsen, aber keine Voraussetzung, um ehrlich zu beobachten, ob
eine Strategie ausserhalb des Backtests haelt.

### Der Tick statt des Daemons

`qt.live.runner.run_paper_tick` ist bewusst kein Prozess, der Wochen
durchlaeuft, sondern eine reine Funktion, die einmal aufgerufen wird und
zurueckkehrt. Der Grund ist keine Vorsicht auf Vorrat: in der Sitzung, in der
dieser Code entstand, sind Hintergrundprozesse mehrfach mit einem
Container-Neustart gestorben, einmal mitten in einem mehrstuendigen
Trade-Abzug. Ein Paper-Konto, das Wochen laufen soll, hat genau dasselbe
Risiko, nur hoeher: ein gestorbener Daemon faellt erst auf, wenn niemand mehr
neue Fills sieht.

**Was deshalb zwischen zwei Ticks ueberleben muss, ist absichtlich klein.**
Nicht der `FeatureStore`, nicht die Uhr -- beide werden bei jedem Tick aus den
durabel gespeicherten Bars in `qt.data.store` neu aufgebaut, ein Nachbau, der
so guenstig ist, dass er keine eigene Zwischenspeicherung rechtfertigt. Was
ueberleben muss, ist ausschliesslich der Broker-Zustand (Cash, Positionen,
vorgemerkte Orders) plus ein Zeitstempel-Cursor, der trennt "bereits
entschieden" von "nur Kontext fuer den Feature-Store". Gespeichert wird nach
jedem einzelnen neu verarbeiteten Bar, nicht erst am Ende eines Ticks, und
atomar (Schreiben in eine temporaere Datei, dann `replace`) -- ein Absturz
darf hoechstens die Zeit bis zum naechsten Aufruf kosten, nie einen bereits
gebuchten Fill.

### Dieselbe Risk-Engine, keine zweite

Der Kill-Switch ist keine neue Komponente. `qt.portfolio.risk.RiskEngine`
existiert seit Phase 2 fuer den Portfolio-Pfad; der Paper-Tick ruft
`risk.apply()` mit genau demselben `RiskState`-Aufbau wie
`qt.backtest.portfolio_engine` auf. Der einzige neue Baustein ist
`RiskEngine.restore_halted()`: da jeder Tick die Engine neu aufbaut, muss der
Kill-Switch-Zustand explizit aus dem persistierten Konto uebernommen werden --
sonst vergaesse ein frisch aufgebauter `RiskEngine` bei jedem Tick, dass er
schon einmal angehalten hat, und der Kill-Switch waere keiner.

### Die reale Uhr ist eine zusaetzliche Grenze, keine automatische

`BacktestClock`, wie im Backtest, prueft nur, dass Bars in aufsteigender
Reihenfolge ankommen -- nicht, ob ihre Close-Zeit tatsaechlich schon vergangen
ist. Das reicht fuer Replay historischer Daten, aber nicht fuer live gezogene:
gemessen an echten Coinbase-Daten gab `fetch_ohlcv` einen Bar zurueck, dessen
`close_ts` einen Tag in der Zukunft lag -- die gerade erst offene, sich noch
aendernde Kerze des laufenden Tages. `run_paper_tick` verwirft deshalb jeden
Bar mit `close_ts > now` vollstaendig, bevor er ueberhaupt den Feature-Store
erreicht. Der Store heilt sich von selbst (`write_bars` dedupliziert mit
`keep="last"`, ein spaeterer Pull ueberschreibt den vorlaeufigen Wert), aber
bis dahin darf keine Entscheidung auf einem Wert stehen, der sich noch aendern
kann.
