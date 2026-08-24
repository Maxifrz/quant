# quant

Krypto-Handelssystem, in dem ein LLM zwei Rollen bekommt:

- **Allokator** — verteilt Risikobudget über ein Portfolio deterministischer Strategien
- **Forscher** — generiert neue Strategie-Kandidaten, die eine harte statistische Prüfung bestehen müssen

Das LLM handelt nicht selbst. Es entscheidet *worüber* gehandelt wird, nicht *wie*
ausgeführt wird. Vorschläge des LLM laufen immer durch eine deterministische
Risk-Engine, die sie beschneiden kann.

**Status:** Phase 0–4 gebaut — Datenpipeline, Backtest-Engine, Portfolio-Schicht
mit Walk-Forward und Risk-Engine, der LLM-Allokator samt Blind Briefing,
Antwort-Cache und Gate, sowie die Pfad-Simulation mit CVaR-Zielfunktion.

Der LLM-Teil ist **gebaut, aber in seiner Wirksamkeit ungeprüft**: in der
Bauumgebung gab es keinen API-Schlüssel. Geprüft ist, dass ein schlechtes Modell
nichts kaputtmachen kann — ungeprüft, ob ein gutes Modell etwas verbessert.
Siehe [docs/ROADMAP.md](docs/ROADMAP.md).

---

## Schnellstart

```bash
uv sync --extra dev

# Marktdaten holen (Coinbase, ab 2019)
uv run qt data pull --symbols "BTC/USD,ETH/USD" --tf "1h,4h,1d" --since 2019-01-01
uv run qt data report

# Einzelne Strategie (In-Sample -- nur zur Anschauung)
uv run qt strategies
uv run qt backtest --strategy trend --symbol BTC/USD --tf 4h

# Walk-Forward: die einzigen belastbaren Zahlen im System
uv run qt wf --strategy trend --symbol BTC/USD --tf 4h --train 3000 --test 800 --embargo 50

# Portfolio aus mehreren Strategien unter einem Allokator
uv run qt allocators
uv run qt portfolio --strategies trend,meanrev --symbols BTC/USD,ETH/USD --tf 4h

# LLM-Allokator gegen die Baselines antreten lassen (braucht API-Schluessel)
ANTHROPIC_API_KEY=... uv run qt alloc --compare-baselines

# Pfad-Ensemble simulieren und das zulaessige Exposure bestimmen
uv run qt sim --symbol BTC/USD --tf 4h --paths 10000 --horizon 180
uv run qt sim --generator garch --until 2024-12-31

uv run pytest -q
```

Tearsheets landen in `reports/`, Marktdaten in `data/` — beides ist nicht versioniert.

---

## Wie es gebaut ist

**Eine Engine, drei Uhren.** Backtest, Paper-Trading und Live laufen durch denselben
Code; ausgetauscht werden nur Clock und Broker. Der übliche Aufbau mit einem
vektorisierten Backtest-Pfad und einem event-getriebenen Live-Pfad lässt die beiden
auseinanderdriften, bis der Backtest Fiktion ist. Hier gibt es einen Pfad.

```
Bars → Features → Strategien → Zielgewichte → Allokator → Risk-Engine → Orders
        (PIT)     (determin.)   (−1…+1)       (LLM,        (hart,
                                               schlägt vor) entscheidet)
```

Details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), Begründungen einzelner
Entscheidungen in [docs/DECISIONS.md](docs/DECISIONS.md).

---

## Drei Dinge, die baulich verankert sind

**Point-in-Time ist ein Test, kein Vorsatz.** Ein Bar wird erst nach seinem Close
sichtbar; ein Signal vom Close des Bars *t* wird auf dem Open von *t+1* ausgeführt.
`tests/test_lookahead.py` lässt dieselbe Historie einmal mit einem Crash und einmal
mit einer Rally *danach* laufen — der gemeinsame Zeitraum muss bitidentisch sein.

**Kosten von Anfang an.** Fees, Spread und Slippage sind Teil der ersten Bauphase,
nicht ein späterer Realismus-Layer. Die Defaults sind bewusst pessimistisch
(~90 bps Round-Trip): lieber eine gute Strategie verwerfen als eine schlechte live
schalten.

**Das LLM kennt die Vergangenheit.** Fragt man es "wie hättest du im März 2020
allokiert", weiß es die Antwort — der Backtest des Allokators wird wertlos, ohne dass
irgendwo ein Bug ist. Gegenmittel: das Briefing enthält keine Datumsangaben, keine
Asset- oder Strategienamen, keinen Kontostand — nur normalisierte Kennzahlen unter
anonymen Labels. Ein Test prüft, dass ein Kontostand von 1.000 und einer von
50.000.000 dasselbe Briefing erzeugen. Und: der Allokator wird primär am
Forward-Paper-Trading gemessen, nicht am Backtest.

**Das Modell schlägt vor, es entscheidet nicht.** Fünf Stufen zwischen Antwort und
Konto: Schema-Zwang, pydantic-Validierung, Verwerfen erfundener Labels, Rückfall auf
Gleichgewichtung bei *jedem* Fehler, dann die Risk-Engine. Ein Ausfall des Modells
ist ein langweiliges Ereignis, kein Systemausfall.

---

## Der wichtigste Befund bisher

Dieselben Strategien, dieselben Daten — der einzige Unterschied ist die Risikoschicht:

| Lauf | Gesamtrendite | Max Drawdown |
|---|---|---|
| ohne Risk-Engine | −99,68% | −99,72% |
| mit Risk-Engine | −16,90% | −20,11% |

Sie macht schlechte Strategien nicht gut. Sie sorgt dafür, dass man einen Fehler
überlebt und korrigieren kann. Genau diese Schicht steht ab Phase 3 zwischen dem
LLM-Allokator und dem Konto.

---

## Aus "wahrscheinlichster Verlauf" wird eine Verteilung

Die ursprüngliche Idee war, den wahrscheinlichsten Kursverlauf zu prognostizieren
und darauf zu setzen. Das ist der klassische Weg, Geld zu verlieren — ein
einzelner Pfad ist eine Wette, keine Kante.

`qt sim` macht daraus etwas Rechenbares: es erzeugt tausende mögliche Zukünfte
(Block-Bootstrap, GARCH oder HMM) und bestimmt das Exposure, das über das ganze
Ensemble hinweg die Verlustgrenze hält und dabei typischerweise am meisten
liefert. **"Kein Trade" ist ein mögliches Ergebnis** — und auf den letzten 2000
BTC-Bars (−44,8%) ist es das tatsächliche.

Die Grenze bezieht sich auf den zwischenzeitlichen Drawdown, nicht auf die
Endrendite: ein Konto wird unterwegs liquidiert, nicht am Ende des Horizonts
(ADR-025).

Ein LLM darf hier Szenario-Priors setzen — es **gewichtet Möglichkeiten, es
prognostiziert keine Preise**. Das Schema ist so geschnitten, dass eine
Preisprognose gar nicht ausdrückbar ist (ADR-024).

---

## Eine dritte, ausdruecklich unzuverlaessige Strategie

`timesfm` bindet Googles TimesFM-Foundation-Model als Forecast-Strategie ein
(`uv sync --extra timesfm`, sonst laeuft sie gegen einen Random-Walk-Platzhalter
ohne Kante). Ihr Backtest ist **nicht vertrauenswuerdig**: das Modell wurde auf
einem nicht dokumentierten Korpus vortrainiert, und ob historische Kursreihen
darin enthalten waren, laesst sich von aussen nicht feststellen. Anders als beim
LLM-Allokator gibt es hier keine Anonymisierung, die das mildern koennte — die
Eingabe ist die Rohreihe. Siehe ADR-022.

## Zu den Baseline-Strategien

`trend` (Donchian-Breakout) und `meanrev` (z-Score-Reversion) sind **Testinstrumente
für die Engine, keine Handelsempfehlung.** Sie stehen hier, weil ihr Verhalten gut
verstanden ist und weil sie gegenläufige Regime-Profile haben — das ist der
einfachste sinnvolle Testfall für einen Allokator.

Der Walk-Forward-Test aus Phase 2 hat das bestätigt: `trend` erreicht über 17
Out-of-Sample-Fenster −71,94% bei Sharpe −0,37, mit positivem Sharpe in nur 4 von
17 Fenstern. Die In-Sample-Zahlen aus Phase 1 waren also nicht zu pessimistisch,
sondern zu optimistisch.

Alles, was `qt backtest` ausgibt, ist In-Sample. Belastbar ist nur `qt wf`.
