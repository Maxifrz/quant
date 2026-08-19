# Entscheidungs-Log (ADR)

Kurze Begründungen, damit nichts im Kopf gehalten werden muss.
Neueste zuerst. Format: Entscheidung — Warum — Konsequenz.

---

## ADR-008 — Rebalancing-Band statt exaktem Zielgewicht
**Datum:** 2026-08-19

**Beobachtung:** Der erste echte Backtest (trend, BTC/USD 4h, 16.712 Bars) erzeugte
2.872 Fills bei nur 559 Signalwechseln — 5,1 Fills pro Entscheidung.

**Ursache:** Das Zielgewicht wird auf dem Close bewertet, ausgefuehrt wird auf dem
Open des naechsten Bars. Nach jeder Ausfuehrung weicht die Ist-Position minimal vom
Ziel ab, und die naechste Bewertung erzeugt sofort wieder eine Mikro-Order. Die
Positionsgroesse wird dadurch perfekt gehalten — und zahlt dafuer bei jedem Bar.

**Konsequenz:** `BacktestConfig.rebalance_band` (Default 5% des Eigenkapitals). Es
wird erst gehandelt, wenn die Abweichung das Band verlaesst. Fills fielen damit von
2.872 auf 819.

Nebenbefund: die Gebuehren aenderten sich kaum (267k -> 269k). Die Mikro-Orders waren
zahlreich, aber winzig — der Kostenblock stammt aus echten Signalwechseln. Das Band
ist trotzdem richtig, es beseitigt nur nicht das eigentliche Problem (ADR-009).

---

## ADR-009 — Coinbase-Retail-Gebuehren machen 4h-Trendfolge unrentabel
**Datum:** 2026-08-19

**Messung** (trend, BTC/USD 4h, 2019–2026, Startkapital 100k):

| Kostenniveau | Endkapital | Faktor |
|---|---|---|
| ohne Kosten | 644.194 | 6,44x |
| Maker-Niveau (~16 bps Round-Trip) | 403.760 | 4,04x |
| Coinbase Taker (90 bps Round-Trip) | 45.725 | 0,46x |

**Was das heisst:** Das Signal hat eine Kante. Die Gebuehrenstruktur frisst sie
vollstaendig auf — aus 6,4x wird 0,46x. Die Strategie ist nicht kaputt, das
Ausfuehrungsmodell ist es.

**Warum das hier steht:** Genau dafuer gehoert das Kostenmodell in Phase 1 und nicht
in einen spaeteren "Realismus-Layer". Ohne Kosten haette der erste Backtest 6,4x
angezeigt und mehrere Wochen Arbeit in die falsche Richtung gelenkt.

**Konsequenzen fuer die weitere Arbeit:**
- Buy-&-Hold liegt im selben Zeitraum bei 17,4x. Beide Baselines liegen deutlich
  darunter — das ist der ehrliche Ausgangspunkt, kein Grund zur Beunruhigung.
- Ab Phase 2 gehoert die Umschlagshaeufigkeit in jede Bewertung. Eine Strategie mit
  Sharpe 1,0 und taeglichem Umschlag ist bei diesen Gebuehren wertlos.
- Phase 7 (live) braucht entweder Maker-Orders oder eine guenstigere Boerse. Die
  Annahme "Taker auf Coinbase" ist als Default konservativ richtig, aber sie ist kein
  Naturgesetz.

---

## ADR-006 — Coinbase Exchange als primäre Datenquelle, Symbole in USD statt USDT
**Datum:** 2026-08-19

Erreichbarkeitstest der Exchange-APIs aus dieser Umgebung:

| Quelle | Ergebnis |
|---|---|
| Binance | HTTP 451, geblockt |
| Kraken | erreichbar, aber nur ~721 Bars (~30 Tage) Historie |
| Crypto.com | erreichbar, liefert für 2019 keine Daten |
| **Coinbase Exchange** | erreichbar, Historie bis mindestens 2016, Paginierung über `start`/`end` |

**Warum:** Nur Coinbase liefert die tiefe Historie, die Walk-Forward-Tests brauchen.
Kraken mit 30 Tagen Historie ist für Backtests wertlos.

**Konsequenz:** Handelspaare sind `BTC/USD` und `ETH/USD`, nicht `BTC/USDT`.
Coinbase gibt max. 300 Bars pro Request — der Ingest paginiert rückwärts.
Die Store-Schicht ist quellenagnostisch; ein Wechsel der Exchange berührt nur
`qt/data/ingest.py`.

---

## ADR-005 — Deflated Sharpe Ratio ab Tag 1 im Research-Loop
**Datum:** 2026-08-19

**Warum:** Ein LLM, das 500 Strategien generiert, findet garantiert welche mit
Sharpe > 2 — auch wenn alle 500 Rauschen sind. Das ist Statistik, keine Hypothese.
Wer erst später korrigiert, hat bis dahin eine Overfitting-Maschine gebaut und ihr
vertraut.

**Konsequenz:** Die Kandidaten-Registry zählt *alle je getesteten* Versuche mit, nicht
nur die der aktuellen Charge. Der DSR wird gegen diese Gesamtzahl berechnet.

---

## ADR-004 — LLM-Allokator muss Baselines out-of-sample schlagen
**Datum:** 2026-08-19

**Warum:** Ohne Vergleichsmaßstab wird jedes LLM-Ergebnis als Erfolg gelesen.
Equal-Weight und Vol-Parity sind erstaunlich schwer zu schlagen.

**Konsequenz:** `qt.portfolio.baselines` wird vor dem LLM-Allokator gebaut, nicht
danach. Verliert das LLM gegen Vol-Parity, ist das ein Ergebnis — kein Bug.

---

## ADR-003 — Blind Briefings für den LLM-Allokator
**Datum:** 2026-08-19

**Warum:** Das LLM kennt die Vergangenheit. Ein Briefing mit Datum und Asset-Namen
lässt es im Backtest mit Hindsight allokieren, ohne dass irgendwo ein Bug ist — die
subtilste Fehlerquelle im ganzen Entwurf.

**Konsequenz:** Briefings enthalten nur normalisierte Features unter anonymen Labels
(`ASSET_1`, `STRAT_C`), keine Daten, keine Namen, keine News. Zusätzlich wird der
Allokator primär am Forward-Paper-Trading beurteilt, nicht am Backtest.

---

## ADR-002 — Strategien geben Zielgewichte, keine Orders
**Datum:** 2026-08-19

**Warum:** Trennt "wohin zeigt der Markt" (Strategie) von "wieviel Kapital" (Allokator)
und "was ist maximal erlaubt" (Risk-Engine). Ohne diese Trennung ist jede Strategie
implizit auch ein Risikomodell, und man kann weder das eine noch das andere
unabhängig testen oder austauschen.

**Konsequenz:** `Strategy.on_bar()` gibt `dict[symbol, weight]` mit weight ∈ [−1, +1]
zurück. Die Engine übersetzt Gewichte in Orders.

---

## ADR-001 — Eine Engine, drei Uhren (event-getrieben statt vektorisiert)
**Datum:** 2026-08-19

**Warum:** Der übliche Aufbau hat zwei Codepfade — einen vektorisierten für Backtests,
einen event-getriebenen für live. Die beiden driften auseinander, und der Backtest wird
zur Fiktion. Ein einziger Pfad, in dem nur Clock und Broker ausgetauscht werden,
schließt diese Klasse von Fehlern aus.

**Konsequenz:** Langsamer als eine vektorisierte Engine. Akzeptiert — Korrektheit vor
Geschwindigkeit. Falls Performance später zum Problem wird, ist der Ausweg
Parallelisierung über Parameter-Sets, nicht Vektorisierung des Kernpfads.
