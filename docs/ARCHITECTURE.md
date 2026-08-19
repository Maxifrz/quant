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

## Wo das LLM sitzt (Phase 3+)

### Blind Briefings

Ein LLM kennt die Vergangenheit. Fragt man es "wie hättest du im März 2020 allokiert",
weiß es die Antwort — und der Backtest des Allokators wird wertlos, ohne dass es
irgendwo einen Bug gibt.

Gegenmittel: das Briefing enthält **keine Datumsangaben, keine Asset-Namen, keine
News** — nur normalisierte Features unter anonymen Labels (`ASSET_1`, `STRAT_C`).
Das LLM sieht ein Regime, keinen Zeitpunkt.

Das entfernt das Problem nicht vollständig (markante Regime bleiben erkennbar).
Deshalb gilt zusätzlich: **der Allokator wird primär am Forward-Paper-Trading
gemessen, nicht am Backtest.**

### Reproduzierbarkeit

Jeder LLM-Call wird gecached, Key = Hash(Prompt) + Modell-ID. Ein Backtest über den
Allokator ist damit wiederholbar und beim zweiten Lauf kostenlos. Ohne Cache ist ein
LLM-Backtest weder reproduzierbar noch bezahlbar.

### Das Gate

Der LLM-Allokator geht nur in Produktion, wenn er **out-of-sample** die Baselines aus
`qt.portfolio.baselines` schlägt: Equal-Weight, Vol-Parity, Best-Single-Strategy.

Tut er das nicht, ist das ein Ergebnis und kein Fehler. Ein LLM, das nicht besser
allokiert als Vol-Parity, gehört nicht in den Kreislauf.

---

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

## Pfad-Simulation (Phase 4)

Die ursprüngliche Idee war, "den wahrscheinlichsten Verlauf zu traden". Genau so
formuliert ist das der klassische Weg, Geld zu verlieren: ein einzelner prognostizierter
Pfad ist eine Wette, keine Kante.

Die tragfähige Version derselben Idee: ein **Ensemble** von Pfaden simulieren und eine
Allokation wählen, die über das ganze Ensemble hinweg gut abschneidet.

- Stationärer Block-Bootstrap (erhält Autokorrelation und Vol-Clustering)
- GARCH für Volatilitätspfade
- HMM für Regime-Wechsel
- Das LLM darf **Szenario-Priors** setzen ("Vol-Spike-Regime auf 30% gewichten"),
  die das Ensemble umgewichten — es prognostiziert keinen Preis, es gewichtet
  Möglichkeiten.
- Zielfunktion: Median-Rendite unter CVaR-Nebenbedingung.

Damit wird aus "wahrscheinlichster Verlauf" etwas Rechenbares.

---

## Modulübersicht

| Modul | Zweck |
|---|---|
| `qt.core` | Clock, Events, Typen, Config — die Begriffe, die alle teilen |
| `qt.data` | Ingest (ccxt), Parquet-Store, Integritätsprüfung |
| `qt.features` | Point-in-Time-Feature-Store, TA-Bausteine, Regime-Features |
| `qt.strategy` | Strategie-Interface, Registry, Bibliothek |
| `qt.backtest` | Engine, SimBroker, Kosten, Metriken, Walk-Forward |
| `qt.portfolio` | Baselines, LLM-Allokator, Risk-Engine |
| `qt.sim` | Bootstrap, Regime-Modelle, Szenarien |
| `qt.research` | Strategie-Generator, Sandbox, Screening, Registry |
| `qt.llm` | Client, Briefing-Bau, Output-Schemas, Cache |
| `qt.live` | Runner, CCXT-Broker, Reconciliation, Kill-Switch |
| `qt.report` | Tearsheets, Tagesreport |

Gebaut sind aktuell: `core`, `data`, `features`, `strategy`, `backtest`, `report`.
Der Rest ist in `ROADMAP.md` beschrieben.
