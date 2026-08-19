# quant

Krypto-Handelssystem, in dem ein LLM zwei Rollen bekommt:

- **Allokator** — verteilt Risikobudget über ein Portfolio deterministischer Strategien
- **Forscher** — generiert neue Strategie-Kandidaten, die eine harte statistische Prüfung bestehen müssen

Das LLM handelt nicht selbst. Es entscheidet *worüber* gehandelt wird, nicht *wie*
ausgeführt wird. Vorschläge des LLM laufen immer durch eine deterministische
Risk-Engine, die sie beschneiden kann.

**Status:** Phase 0 und 1 fertig — Datenpipeline und Backtest-Engine laufen.
Der LLM-Teil ist Phase 3 und 5. Siehe [docs/ROADMAP.md](docs/ROADMAP.md).

---

## Schnellstart

```bash
uv sync --extra dev

# Marktdaten holen (Coinbase, ab 2019)
uv run qt data pull --symbols "BTC/USD,ETH/USD" --tf "1h,4h,1d" --since 2019-01-01
uv run qt data report

# Backtest laufen lassen
uv run qt strategies
uv run qt backtest --strategy trend   --symbol BTC/USD --tf 4h
uv run qt backtest --strategy meanrev --symbol ETH/USD --tf 1h

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
irgendwo ein Bug ist. Gegenmittel ab Phase 3: Briefings ohne Datumsangaben, ohne
Asset-Namen, nur normalisierte Features. Und: der Allokator wird primär am
Forward-Paper-Trading gemessen, nicht am Backtest.

---

## Zu den Baseline-Strategien

`trend` (Donchian-Breakout) und `meanrev` (z-Score-Reversion) sind **Testinstrumente
für die Engine, keine Handelsempfehlung.** Sie stehen hier, weil ihr Verhalten gut
verstanden ist und weil sie gegenläufige Regime-Profile haben — das ist der
einfachste sinnvolle Testfall für einen Allokator.

Ob eine Strategie tatsächlich Geld verdient, entscheidet frühestens Phase 2 mit
Walk-Forward-Tests. In-Sample-Zahlen sagen darüber nichts aus.
