# Entscheidungs-Log (ADR)

Kurze Begründungen, damit nichts im Kopf gehalten werden muss.
Neueste zuerst. Format: Entscheidung — Warum — Konsequenz.

---

## ADR-037 — Paper-Trading als wiederholbarer Tick, nicht als Daemon
**Datum:** 2026-08-28

**Der Anlass ist eine gemessene Eigenschaft dieser Umgebung, nicht eine
Vorsichtsmaßnahme auf Vorrat.** In derselben Sitzung sind Hintergrundprozesse
mehrfach an Container-Neustarts gestorben — einmal mitten in einem
mehrstündigen Trade-Abzug (ADR-034), zweimal beim Fortsetzen desselben
Abzugs. Ein Paper-Konto soll Wochen laufen. Ein Daemon mit demselben
Sterberisiko, nur mit höherem Einsatz: ein gestorbener Daemon fällt erst auf,
wenn tagelang keine neuen Fills mehr erscheinen.

**Die Entscheidung:** `qt.live.runner.run_paper_tick` ist eine reine
Funktion. Sie wird einmal aufgerufen, verarbeitet alle seit dem letzten Tick
geschlossenen Bars, speichert und kehrt zurück. Extern taktbar — von Hand,
per Cron, über eine Routine dieser Plattform.

**Was zwischen zwei Ticks überleben muss, ist absichtlich klein.** Nicht der
`FeatureStore`, nicht die Uhr — beide werden aus den durabel gespeicherten
Bars in `qt.data.store` bei jedem Tick neu aufgebaut, günstig genug, um keine
eigene Zwischenspeicherung zu rechtfertigen. Persistiert wird ausschließlich
der Broker-Zustand (Cash, Positionen, vorgemerkte Orders) plus ein
Zeitstempel-Cursor (`last_processed_ts`), der trennt: "bereits entschieden"
von "nur Kontext für den Feature-Store". Dieselbe Idee wie
`qt.data.trades.resume_point`, nur für Konten statt für Trade-Abzüge.

**Ein frisches Konto startet flach, nicht rückwirkend.** `last_processed_ts`
wird beim ersten Tick auf den jüngsten zu diesem Zeitpunkt bekannten Bar
gesetzt. Ohne das würde ein neues Paper-Konto beim ersten Aufruf die gesamte
verfügbare Historie rückwirkend handeln und Hunderte Fills auf einmal
auslösen — das genaue Gegenteil von "beobachten, was ab jetzt passiert".

**Persistiert wird nach jedem einzelnen neuen Bar**, nicht erst am Ende eines
Ticks — ein Tick kann mehrere neue Bars auf einmal verarbeiten, wenn zwischen
zwei Aufrufen mehr Zeit verging als ein Bar dauert. Ein Absturz nach dem
dritten von fünf Bars darf die ersten drei nicht verlieren. Geschrieben wird
zusätzlich atomar (temporäre Datei, dann `replace`) — ein Absturz mitten im
Schreiben darf keine halbe, kaputte JSON-Datei hinterlassen, gerade in dem
Moment, in dem der Kontostand am dringendsten gebraucht wird.

**Der Beweis, nicht nur die Behauptung:** Derselbe Datensatz wurde einmal in
einem einzigen Tick verarbeitet und einmal mit einem erzwungenen Neustart
nach *jedem einzelnen* Bar (`test_ueberlebt_neustart_zwischen_jedem_bar`).
Cash, Positionen, Fill-Zahl und Gebühren stimmen exakt überein.

**Wiederverwendet, nicht neu erfunden:** Der Kill-Switch ist
`qt.portfolio.risk.RiskEngine`, dieselbe Komponente wie im Portfolio-Pfad seit
Phase 2 — keine zweite Risikologik. Der einzige neue Baustein ist
`RiskEngine.restore_halted()`: weil jeder Tick die Engine neu aufbaut, muss
der Halt-Zustand explizit aus dem persistierten Konto übernommen werden,
sonst vergäße ein frisch aufgebauter `RiskEngine` bei jedem Tick, dass er
schon einmal ausgelöst hat.

**Korrektur einer Doku-Lücke:** Die ROADMAP verwies auf eine
Nautilus-Empfehlung in `docs/ARCHITECTURE.md`, die dort nie existierte.
Nachgetragen: Paper-Trading bleibt in diesem Repo, weil `LiveClock` und das
Docstring-Versprechen von `SimBroker` ("Live wird er durch
`qt.live.broker_ccxt` ersetzt") seit Phase 0 genau darauf angelegt waren.

---

## ADR-038 — Eine Exchange kann eine noch offene Kerze als geschlossen ausgeben
**Datum:** 2026-08-28

**Der Fehler, gefunden im ersten echten Lauf gegen Coinbase:** Der erste
Paper-Tick gegen reale Daten zeigte `Letzter verarbeiteter Bar:
2026-08-29T00:00:00+00:00` — einen Tag **in der Zukunft** gegenüber der
tatsächlichen Uhrzeit (2026-08-28, 11:39 UTC). `fetch_ohlcv` hatte die gerade
erst begonnene, sich noch ändernde Tageskerze als letzte Zeile zurückgegeben,
mit vollständig aussehenden OHLCV-Werten.

**Warum das durchrutschte:** `BacktestClock.advance()` prüft ausschließlich,
dass Bars in aufsteigender Reihenfolge ankommen — nicht, ob ihre Close-Zeit
tatsächlich schon vergangen ist. Für den Replay historischer Daten ist das
richtig: dort ist per Konstruktion jeder Bar längst geschlossen. Der
Paper-Tick nutzte exakt denselben Mechanismus (bewusst, siehe ADR-001) und
erbte damit eine Prüfung, die für Live-Daten nicht ausreicht. Die Uhr wird
vor jedem `store.on_bar()` auf genau `event.ts` gestellt — der interne
Lookahead-Test in `FeatureStore` (`bar.close_ts > clock.now`) kann also gar
nicht auslösen, weil Uhr und Bar-Zeit per Konstruktion synchron sind.

**Die Konsequenz, wäre es unentdeckt geblieben:** Der Kill-Switch, die
Positionsgröße, jede Entscheidung hätte auf einem Wert gestanden, der sich
noch ändern kann. Der Store selbst heilt sich (`write_bars` dedupliziert mit
`keep="last"`, ein späterer Pull überschreibt den vorläufigen Wert) — aber in
der Lücke davor hätte eine Strategie auf Basis von Daten gehandelt, die es in
dieser Form nie gab.

**Die Behebung:** `run_paper_tick` bekommt einen `now`-Parameter (Default:
die reale Systemzeit) und verwirft jeden Bar mit `close_ts > now`
**vollständig** — nicht nur für die Entscheidung, auch als Kontext für den
Feature-Store. Injizierbar statt an `datetime.now()` fest verdrahtet, damit
die Grenze selbst testbar ist: ein Test verschiebt einen unfertigen Bar in
die Zukunft und prüft, dass er ignoriert wird, und ein zweiter, dass er
verarbeitet wird, sobald `now` ihn einholt.

**Was das nicht betrifft:** `qt data pull` und jeder Backtest sind unberührt.
Wer historische Daten liest, liest immer längst geschlossene Bars — das
Problem existiert ausschließlich an der Spitze eines live gezogenen Streams,
also ausschließlich im neuen `qt.live`-Pfad.

---

## ADR-036 — Buy-&-Hold zahlt den Einstieg, und der erste Punkt bleibt das Startkapital
**Datum:** 2026-08-28

**Der Anlass war eine Frage, keine Fehlermeldung:** warum Buy-&-Hold in der
Tabelle Gewinn macht, obwohl dort „0 Trades" steht. Die Antwort war harmlos —
`buy_and_hold()` ist eine reine Rechnung (`Kapital × Preis / Startpreis`) und
läuft nie durch `SimBroker`, taucht also im Trade-Zähler nicht auf. Die
Nachfrage legte aber eine echte Schieflage frei.

**Die Schieflage:** Die Strategie-Spalte rechnet mit 90bps Round-Trip, die
Buy-&-Hold-Spalte mit **null Reibung** — nicht einmal für den einen Kauf, den
Buy-&-Hold zwingend braucht. Der Maßstab, an dem sich jede Strategie beweisen
muss, war damit einer, den in der Wirklichkeit auch Buy-&-Hold nicht erreicht.

**Nur der Einstieg, kein Ausstieg.** `run_backtest` liquidiert am Ende nicht;
die Schlusszahl ist eine Mark-to-Market-Bewertung offener Positionen. Würde man
Buy-&-Hold einen Ausstieg berechnen und der Strategie nicht, wäre der Vergleich
zugunsten der Strategie verzerrt — und zwar unsichtbar. Deshalb `one_way_bps`
als eigene Funktion neben `round_trip_bps`: es gibt Fälle, in denen genau eine
Seite anfällt.

Die Kosten kommen **auf** den Gegenwert obendrauf, wie im Broker
(`cash -= qty * fill_price + fee`): N · p0 · (1 + f) = C.

### Der Fehler in meiner ersten Fassung — gefunden vom eigenen Test

Die naheliegende Implementierung skaliert die gesamte Kurve mit 1/(1+f). Sie
ist **wirkungslos**: `compute()` misst `total_return` als
`equity[-1] / equity[0]`, und ein konstanter Faktor kürzt sich darin restlos
heraus. Die Gebühr hätte im Code gestanden und in **keiner einzigen
angezeigten Kennzahl**.

Aufgefallen ist es nur, weil ein Test behauptete „Gesamtrendite sinkt, Sharpe
bleibt" — und an der ersten Hälfte scheiterte. Das ist dieselbe Klasse wie
ADR-023 (eine Tilt-Begrenzung, die Erfolg meldete und nichts tat): Code, der
aussieht, als täte er etwas.

**Die Behebung:** Der erste Punkt der Kurve bleibt das **Startkapital**. Der
Sprung vom ersten auf den zweiten Punkt trägt damit die Gebühr — genau wie bei
der Strategie, deren Kurve ebenfalls beim Startkapital beginnt und die Gebühr
erst mit dem ersten Fill zeigt.

### Wirkung

XAUT/USDT 1d, 351 Bars, Default-Kosten (45bps einfach):

| | vorher | nachher |
|---|---|---|
| Gesamtrendite B&H | 26,04% | **25,47%** |
| Sharpe B&H | 0,97 | **0,95** |
| CAGR B&H | 27,31% | **26,72%** |

Klein, wie erwartet — ein einmaliger Abzug verliert über lange Zeiträume an
Gewicht. Bei kurzen Vergleichsfenstern ist er sichtbar, und dort war der
Maßstab bisher falsch.

`entry_cost_bps` hat den Default 0.0; Aufrufer ohne das Argument bekommen
exakt die alte Formel. Der Tearsheet reicht `one_way_bps(config.costs)` durch.

---

## ADR-035 — Die erste Strategie, die Geld verdient — und warum sie trotzdem nicht bewiesen ist
**Datum:** 2026-08-27

**Ausgangslage:** Vier Strategien gebaut, vier verloren. Alle vier liefen auf
1h- oder 4h-Bars, alle vier durften short gehen. Beide Entscheidungen waren
teuer, und keine von beiden war je hinterfragt worden.

**Was die Recherche sagt.** Han/Kang/Ryu (2023) finden über den Kryptomarkt
hinweg **starke** Belege für Zeitreihen-Momentum und schwache für
Querschnitts-Momentum — aber: viele Momentum-Portfolios verlieren ihre
Signifikanz, sobald Transaktionskosten realistisch angesetzt werden. Grayscale
berichtet für einen 20/100-Tage-Crossover auf BTC einen Sharpe von 1,7 gegen
1,3 bei Buy-and-Hold (2012–2023). Beide Quellen zeigen in dieselbe Richtung:
**Tagesbasis, wenige Trades, long/flach**.

**Zwei Hebel, die dieses Projekt nie gezogen hatte:**

1. **Frequenz ist Kosten.** Bei 90bps Round-Trip muss ein Signal über 0,9%
   vorhersagen, nur um bei null zu landen. `elliott` zahlte das 889-mal,
   `macross` zahlt es 66-mal in siebeneinhalb Jahren.
2. **Short kämpft gegen die Drift.** BTC machte im Datenfenster Faktor 16,8.
   Die Gegenrichtung zu handeln heißt, gegen den stärksten Effekt im Datensatz
   zu stehen.

### In-Sample, BTC/USD 1d, mit vollen Kosten

| | macross 10/50 | Buy & Hold |
|---|---|---|
| Gesamtrendite | 1424% | **1583%** |
| Sharpe | **1,03** | 0,91 |
| Max Drawdown | **−56,97%** | −76,67% |
| Calmar | **0,75** | 0,58 |
| Zeit im Markt | 54% | 100% |

Sie schlägt Buy-and-Hold **nicht** in der Rendite, aber in jeder Risikokennzahl.
Genau das beschreibt die Literatur: Trendfolge liefert nicht mehr Ertrag,
sondern denselben Ertrag mit weniger Absturz.

### Out-of-Sample (Walk-Forward, Train 1000 / Test 250 / Embargo 20)

Fenster 2021-10 bis 2026-08 — ein Zeitraum, in dem Buy-and-Hold über 4,8 Jahre
auf Faktor 1,03 kam:

| | macross OOS | Buy & Hold |
|---|---|---|
| Faktor | **1,25** | 1,03 |
| CAGR | **4,8%** | 0,7% |
| Sharpe | **0,31** | 0,27 |
| Max Drawdown | **−51,0%** | −76,7% |

**Die erste positive Out-of-Sample-Zahl des Projekts.**

### Das Parameterfeld — der eigentliche Test

25 Gitterpunkte, jeder als eigener Walk-Forward:

- **20 von 25 verdienen Geld**, 17 schlagen den Sharpe von Buy-and-Hold
- Median-Faktor 1,36, Median-Sharpe 0,36
- Klare Struktur: **je länger die langsame Linie, desto besser**
  (5/150: Faktor 2,14, Sharpe 0,64, MaxDD −29,1%)

Ein einzelner guter Punkt wäre Rauschen. Ein *Gefälle* über das Feld ist ein
Hinweis auf einen Effekt.

### Replikation auf ETH, ohne jede Neuanpassung

Buy-and-Hold ETH im selben Fenster: Faktor **0,49**, Sharpe 0,13.

| | macross (Median über 25 Punkte) | Buy & Hold |
|---|---|---|
| Faktor | 1,00 | 0,49 |
| Sharpe | 0,23 | 0,13 |

Und dieselbe Struktur: Median-Sharpe bei `slow ≥ 150` ist **0,31** gegen
**0,20** bei `slow ≤ 80`. Das Muster hält auf einem Asset, auf das es nie
angepasst wurde — die stärkste Einzelbeobachtung dieses ADRs.

### Und trotzdem: **DSR über 0,95 — null von 25**

Die Deflated Sharpe Ratio lehnt jeden Gitterpunkt ab. Bester Wert: 0,276 für
5/150. Das ist kein Pech, sondern Arithmetik: bei einem annualisierten Sharpe
von 0,64 über 1.751 Tagesbars liegt der t-Wert bei rund **1,4** — schon vor
jeder Mehrfachtest-Korrektur nicht signifikant. **Man kann einen Sharpe von 0,6
mit 4,8 Jahren Tagesdaten nicht beweisen.** Das ist eine Eigenschaft der
Datenmenge, nicht der Strategie.

Zwei Dinge relativieren die Strenge, und beide gehören genannt statt
weggelassen:

- Die 25 Gitterpunkte sind **stark korreliert** (überlappende Fenster auf
  denselben Daten). Die DSR behandelt sie als unabhängig und bestraft deshalb
  zu hart.
- Die ETH-Replikation geht in die DSR gar nicht ein, ist aber das
  aussagekräftigste Einzelergebnis.

Beides ändert nichts am t-Wert von 1,4. Es bleibt: **bester Kandidat des
Projekts, nicht bewiesen.**

**Konsequenz:** `macross` ist die erste Strategie, die für Phase 6
(Paper-Trading) in Frage kommt. Nicht weil sie bewiesen wäre, sondern weil
Paper-Trading genau das Instrument ist, das fehlende Beobachtungen sammelt —
und weil ihr Risikoprofil (halbe Zeit flach, Drawdown 25 Punkte unter
Buy-and-Hold) den Fehler billig macht, falls sie doch keine Kante hat.

**Was ausdrücklich nicht behauptet wird:** dass 10/50 oder 5/150 die "richtigen"
Parameter sind. Sie stammen aus einem Vergleich auf denselben Daten. Für den
Live-Betrieb ist die robuste Region (`slow` groß) wichtiger als der Bestwert.

---

## ADR-034 — Order Flow als neue Informationsachse, mit fremder Quelle
**Datum:** 2026-08-27

**Warum überhaupt.** `trend`, `meanrev` und `elliott` kauen alle auf denselben
OHLCV-Daten und sind alle gescheitert (ADR-009, ADR-033). Eine vierte Strategie
auf derselben Datenbasis hätte schlechte Aussichten. Aggressor-getriebener
Fluss steht in OHLCV **nicht drin**: ein Bar mit hohem Volumen und
unverändertem Schluss kann von einem Kaufüberhang stammen, der auf Widerstand
lief, oder von ausgeglichenem Umsatz — für die Kursreihe sieht beides identisch
aus.

**Die Quelle ist eine andere Börse, und das ist eine Annahme.** Gemessen, nicht
geraten:

| Börse | Historische Trades mit Seite | Befund |
|---|---|---|
| Coinbase (Bar-Quelle) | ❌ | ignoriert `since` — bei −30 Tagen kommen Trades von *heute* |
| Binance | ❌ | weiterhin 451-gesperrt (ADR-006) |
| **Kraken** | ✅ | `since` wird respektiert, Aggressor-Seite dabei |

Der Fluss stammt damit von Kraken, die Kursreihe von Coinbase, die späteren
Fills von Coinbase. Die stille Annahme dahinter — der Fluss der einen Börse
erklärt den Preis der anderen — ist plausibel und **unbewiesen**. Sie gehört
gemessen, bevor auf dieser Strategie etwas aufgebaut wird.

**Verdichtet statt erweitert.** Trades sind sub-bar, der `FeatureStore` ist
bar-basiert, und die ganze PIT-Garantie hängt daran (ADR-001). Rohe Ticks in
die Engine zu geben hieße, genau diese Zusage aufzugeben. Stattdessen werden
vier Zahlen je Bar gebildet — `delta`, `buy_share`, `n_trades`, `avg_size` —
mit demselben `close_ts` wie der Bar. Engine, Kostenmodell und Handelsfrequenz
bleiben unverändert.

**Fehlende Bars werden `nan`, nicht 0.** 0 hieße „ausgeglichener Fluss", `nan`
heißt „keine Information". Eine Strategie, die beides verwechselt, handelt
Datenlücken als Signal.

**Die PIT-Regel ruht hier auf einer Regel, nicht auf dem Typsystem.** Die
Flow-Tabelle ist ein Beiwagen neben dem Store; nachgeschlagen wird
ausschließlich über `window.timestamps`, also über Bars, die der Store bereits
herausgegeben hat. Die Alternative — `Bar` um Flow-Felder erweitern — wäre im
Typ sauberer, würde aber jeden Bar im System um Felder erweitern, die fast
nirgends existieren. Der Preis der gewählten Lösung ist, dass ein Test die
Zusage tragen muss: `test_die_strategie_sieht_nur_was_der_store_zeigt` hängt
Flussdaten für die **Zukunft** in die Tabelle und prüft, dass sich das Ergebnis
nicht ändert.

**Ein Abzug muss unterwegs schreiben.** Der erste 30-Tage-Lauf sammelte 1,14
Mio. Trades im Speicher und schrieb erst am Ende — ein Zeitlimit auf der
vorletzten Seite hätte alles verloren. `fetch_trades` hat jetzt einen `sink`,
der alle `flush_every` Seiten ablegt. Der Lauf endete tatsächlich an einem
`NetworkError` und lieferte trotzdem 1.376.988 Trades.

### Das Ergebnis — und warum es noch keins ist

BTC/USD, 28.07. bis 24.08.2026 (27 Tage, 640 Bars zu 1h / 160 zu 4h):

| TF | Kosten | Faktor | Sharpe | Trades |
|---|---|---|---|---|
| 1h | ohne | 0,981 | −2,99 | 34 |
| 1h | Coinbase | 0,841 | −18,52 | 34 |
| 4h | ohne | 1,007 | 2,29 | 10 |
| 4h | Coinbase | 0,963 | −8,16 | 10 |

Buy & Hold im selben Fenster: 1,015.

**Diese Zahlen belegen die Verdrahtung, nicht die Idee.** 27 Tage sind 160
Bars zu 4h und zehn Trades. Ein Sharpe von 2,29 auf zehn Trades ist eine
Stichprobe, keine Kante — und ein Walk-Forward ist auf dieser Datenmenge gar
nicht möglich. Wer aus dieser Tabelle eine Aussage über die Strategie ableitet,
liest Rauschen.

**Was sie trotzdem zeigt:** die Kosten schlagen auch hier durch. Bei 4h kostet
der Weg von 1,007 auf 0,963 zehn Trades — rund 4,4 Prozentpunkte auf ein
Signal, das brutto 0,7% verdient hat. Das ist dieselbe Diagnose wie überall
sonst in diesem Projekt und war die Hauptsorge schon vor dem Bauen.

**Korrektur meiner Aufwandsschätzung.** Die erste Rechnung ("7.100 Anfragen,
zwei Stunden") stammte aus einer einzelnen Stichprobenseite, die 74 Minuten
abdeckte. Der echte 30-Tage-Lauf brauchte **52 Seiten je Tag**, nicht 19 — bei
höherem Handelsaufkommen deckt eine Seite weniger Zeit ab. Ein Jahr sind damit
rund **19.100 Anfragen und 7,6 Stunden** (ccxt pausiert selbst 1s je Anfrage,
gemessen 1,43s je Seite). Eine Hochrechnung aus einer Stichprobe ist eine
Vermutung, auch wenn sie aus einer Messung stammt.

**Bei der Laufzeit ist Fortsetzbarkeit Pflicht.** `resume_point` gibt das Ende
des *zusammenhängenden* Blocks ab `since` zurück, nicht den jüngsten
gespeicherten Trade. Der Unterschied ist der ganze Punkt: nach einem
30-Tage-Abzug liegen die letzten 30 Tage im Store; wer danach ein Jahr holen
will und beim jüngsten Trade ansetzt, überspringt die elf Monate davor — und
merkt es nicht, weil nichts fehlschlägt. Die Lücke fällt erst auf, wenn eine
Strategie über ihr eine Kennzahl bildet.

---

## ADR-033 — Elliott-Wellen: mechanisierbar gemacht, und dann widerlegt
**Datum:** 2026-08-27

**Das Problem mit der Theorie:** Elliott-Wellen sind in üblicher Form nicht
mechanisch. Ein Zähler vergibt Nummern, und wenn der Markt die Zählung
widerlegt, wird umnummeriert. Genau das macht sie im Rückblick überzeugend und
im Voraus wertlos: eine Zählung, die nach jedem neuen Hoch neu vergeben werden
darf, kann nicht falsch sein.

**Drei Einschränkungen machen sie prüfbar**, alle drei enger als das, was ein
menschlicher Zähler täte:

1. **Pivots werden bestätigt, nicht erkannt.** Ein Swing-Hoch bei Bar `i` steht
   erst fest, wenn `confirm_bars` weitere Bars vergangen sind. Die Schleife
   läuft bis `len - confirm_bars`, nicht bis `len - 1`. **Das ist die Stelle,
   an der jede ZigZag-Implementierung schummelt** — wer bis zum letzten Bar
   zählt, benutzt für den jüngsten Pivot Bars, die es zum
   Entscheidungszeitpunkt noch nicht gab.
2. **Es wird nie umnummeriert.** Bricht Regel 1, wird die Position geschlossen
   — nicht zu einer anderen Zählung umgedeutet, die den Verlust wegerklärt.
   Ein Test verankert das: kein direkter Sprung von +1 auf −1.
3. **Nur die drei harten Regeln zählen**, nicht die Leitlinien. Sie sind die
   einzigen Aussagen der Theorie, die eine Zählung eindeutig ausschließen.

**Warum die dritte Welle und nicht die fünfte:** Die dritte ist die einzige,
über die die Theorie eine nachprüfbare Aussage macht (sie ist nie die
kürzeste). Ein Einstieg in der fünften wäre eine reine Vorhersage. Und Regel 1
liefert die Ausstiegsmarke als **Preis, nicht als Meinung** — das ist der
eigentliche Grund, warum diese Strategie überhaupt handelbar ist.

**Das Ergebnis, BTC/USD 4h über 16.712 Bars:**

| Variante | Faktor | Sharpe | Trades | Gebühren |
|---|---|---|---|---|
| ohne Kosten | **0,140** | −0,25 | 893 | 0 |
| Maker 10bps | 0,090 | −0,37 | 894 | 24.491 |
| Coinbase Taker (Default) | 0,027 | −0,68 | 889 | 69.241 |

Walk-Forward (Train 3000 / Test 800 / Embargo 50): **7 von 17 Fenstern mit
positivem Sharpe**, Gesamtergebnis negativ.

**Die Diagnose ist eindeutig und sie ist die interessante Zahl:** Auch **ohne
jede Gebühr** bleibt Faktor 0,14. Das ist kein Kostenproblem wie bei `trend`
(ADR-009: 6,44 ohne Kosten, 0,46 mit), sondern ein Signalproblem wie bei
`meanrev`. Die Wellenzählung findet Muster, aber die Muster sagen nichts über
den nächsten Bar.

**Was das über die Theorie sagt — und was nicht:** Widerlegt ist *diese*
Mechanisierung auf *diesem* Markt in *diesem* Timeframe. Ein Verfechter würde
einwenden, echte Wellenzählung brauche Urteilsvermögen. Das mag sein — aber
dann ist sie keine Strategie, sondern eine Fähigkeit, und eine Fähigkeit lässt
sich nicht backtesten. Der Wert dieses Moduls liegt darin, dass die Frage
überhaupt beantwortbar wurde.

Die Strategie bleibt in der Bibliothek, wie `timesfm` (ADR-022): offen als
unrentabel markiert, prüfbar, und ein Testinstrument für die Engine.

---

## ADR-029 — Die Sandbox ist eine Whitelist, und sie hat zwei Schlösser
**Datum:** 2026-08-27

**Die Entscheidung:** `qt.research.sandbox` erlaubt eine feste Menge von
AST-Knotentypen und lehnt alles andere ab — mit Zeilennummer.

**Warum keine Blockliste:** Eine Liste verbotener Namen (`eval`, `exec`,
`open`, …) ist ein Spiel, das man verliert, weil man etwas vergisst. Eine
Whitelist ist geschlossen: was nicht ausdrücklich erlaubt ist, kommt nicht
durch — auch das, woran beim Schreiben niemand gedacht hat.

**Warum die Whitelist allein trotzdem nicht reicht:** Sie sieht nur Syntax,
nicht, *welche* Methode auf einem erlaubten Namen aufgerufen wird.
`np.save(...)` ist syntaktisch ein völlig normales `Attribute` + `Call`.
Deshalb ist `np` nicht das echte numpy-Modul, sondern eine Fassade mit 23
reinen Rechenfunktionen; `ta` ebenso mit 7. Die gefährliche Methode existiert
auf dem Objekt schlicht nicht — egal, unter welchem lokalen Namen der
generierte Code es weiterreicht. Dazu ein kleines, explizites `SAFE_BUILTINS`
und ein hartes Verbot jedes Attributs mit führendem Doppel-Unterstrich, was
die Restricted-Exec-Familie an der Wurzel blockiert.

`getattr` und `setattr` sind mitgesperrt. Ohne sie ließe sich die
dunder-Sperre per String-Konkatenation umgehen:
`getattr(np, '__' + 'class__')`. Nachgemessen: wird abgelehnt.

**Strukturell wichtiger als jede einzelne Regel:** `load_strategy_class` ruft
intern `check()` und wirft, **bevor** irgendein `exec` passiert. Es gibt in
der öffentlichen API keinen Pfad, der die Prüfung umgehen kann — "vergessen zu
prüfen" ist damit unmöglich statt nur unwahrscheinlich. Belegt mit einem
Payload, der bei Ausführung eine Datei anlegen würde; geprüft wird danach,
dass sie *nicht existiert*. Ein Test, der nur die Exception prüft, belegt das
nicht.

**Der Preis, den das hat, und er ist hoch:** Kandidaten können **keinen
Konstruktor** haben. `__init__` ist ein dunder, `super()` ist gesperrt,
Dekoratoren sind verboten. Parameter sind deshalb Klassenkonstanten,
`warmup_bars` ist ein schlichtes Klassenattribut statt `@property`. Das ist
eine ungewohnte Form — aber sie hat einen Nebennutzen: weniger Freiheitsgrade,
kein Konstruktor-Beiwerk, und die Parameter stehen sichtbar oben in der Klasse.

---

## ADR-030 — Der Generator-Prompt wird gegen die Sandbox getestet, nicht gegen die Absicht
**Datum:** 2026-08-27

**Der Fehler, in meinem eigenen ersten Entwurf:** Der Systemprompt für den
Generator zeigte ein Beispiel mit `__init__`, `super().__init__(...)` und
`@property warmup_bars`. Alle drei sind von der Sandbox verboten (ADR-029).
Der Prompt hätte damit **hundert Prozent abgelehnte Kandidaten** erzeugt — und
zwar bezahlte: jeder Generator-Aufruf kostet, jede Ablehnung kommt danach.

Aufgefallen ist das nicht beim Lesen, sondern beim Durchschicken des Beispiels
durch die echte `check()`.

**Die Konsequenz als Test, nicht als Vorsatz:**
`test_das_beispiel_im_generator_prompt_besteht_die_sandbox` schneidet den
Beispielblock aus dem Systemprompt heraus und schickt ihn durch die echte
Prüfung. Läuft die Sandbox dem Prompt künftig davon, fällt es dort auf — und
nicht an einer Ablehnungsquote von hundert Prozent im ersten bezahlten Lauf.

Dasselbe gilt für den Stub: `test_stub_generator_erzeugt_code_der_die_echte_sandbox_besteht`.
Ein Stub, der die eigene Sandbox nicht besteht, blockiert jeden Offline-Lauf —
und der Fehler sähe im Trichter wie ein Befund über den Generator aus statt
wie ein kaputter Stub.

**Verallgemeinert:** Jede Beschreibung einer Schnittstelle, die an einer
anderen Stelle erzwungen wird, gehört gegen diese Stelle getestet. Ein Prompt
ist Code, nur in einer Sprache ohne Compiler.

---

## ADR-031 — Die Kritik-Stufe ist ein protokollierter Übersprung, kein Veto
**Datum:** 2026-08-27

**Woher der Gedanke stammt:** Multi-Agenten-Handelsframeworks wie
TradingAgents lassen Bull- und Bear-Rollen debattieren, bevor entschieden
wird. Übernommen ist die Idee, **nicht ihr Sitz in der Kette**.

Dort urteilt das Debattenteam über eine *live auszuführende* Entscheidung; ein
Fehlurteil kostet echtes Geld. Hier sitzt die Kritik **im Forschungslauf, vor
dem teuren Backtest** — und selbst ein voll akzeptierter, DSR-bestandener
Kandidat erreicht die Bibliothek nur über manuelle Freigabe. Der Schaden eines
Fehlurteils ist damit im schlimmsten Fall verschwendete oder gesparte
Rechenzeit, nie eine falsche Order.

**Zwei Eigenschaften, die die Stufe von einem Veto unterscheiden:**

1. **Eine Ablehnung verwirft nichts.** Sie überspringt den Walk-Forward-Lauf;
   der Kandidat liegt samt Code und Begründung in der Registry. Wer die
   Begründung für falsch hält, lässt den Lauf mit `--no-critic` erneut laufen.
   Dieselbe Haltung wie ADR-018/ADR-021: eine LLM-Entscheidung fällt auf einen
   sichtbaren, überprüfbaren Zustand zurück, nie auf ein stilles Verschwinden.
2. **Ein Ausfall des Kritikers lässt durch, er blockiert nicht.** Ein
   Vorfilter, der bei einem Netzwerkfehler die ganze Charge anhält, hat aus
   einer Sparmaßnahme einen Single Point of Failure gemacht. `critic.critique`
   gibt bei jedem Fehler ein "proceed" zurück **plus** die Fehlermeldung, damit
   der Ausfall in der Telemetrie steht statt unsichtbar zu bleiben.

**Befunde und Entscheidung sind getrennte Felder.** Ein Modell, das vier Mängel
auflistet und trotzdem durchwinkt, hat sich widersprochen. Das Schema
definiert diesen Widerspruch **nicht** weg — `CandidateCritique.contradictory`
meldet ihn in die Telemetrie. Wer sich auf die Empfehlung verlässt, soll die
Befunde daneben sehen können.

**Der Kritik-Prompt hält ausdrücklich vom Dauerablehnen ab:** "proceed ist die
richtige Antwort, wenn nichts Konkretes dagegen spricht." Ein Kritiker, der
jeden Kandidaten ablehnt, filtert nichts — er blockiert nur, und dann wird er
abgeschaltet. `--critic-effort` steht per Default auf `low`, der Generator auf
`medium`: der Unterschied gehört in den Code, nicht nur in die Doku, sonst
wird aus dem billigen Vorfilter beim nächsten Lauf unbemerkt ein teurer.

---

## ADR-032 — Nur abgeschlossenes Screening zählt als Versuch
**Datum:** 2026-08-27

**Die Frage:** Zählt ein Kandidat, den die Sandbox oder die Kritik verworfen
hat, in den Versuchszähler der Deflated Sharpe Ratio?

**Antwort: nein.** ADR-005 spricht von *"allen je getesteten"* Kandidaten, und
"getestet" heißt hier: durch Walk-Forward-OOS gelaufen, ein Sharpe wurde auf
echten Out-of-Sample-Daten berechnet. Wer nie gegen echte Daten lief, hat
keinen zusätzlichen Blick auf die Daten gekauft und darf die Korrektur nicht
mit einem Versuch belasten.

**Die angenehme Folge:** Die Kritik-Stufe senkt damit nicht nur die Kosten,
sondern die *tatsächliche Zahl der Datenblicke* — und wird dafür statistisch
nicht bestraft. Ein Vorfilter, der jeden abgelehnten Kandidaten trotzdem als
Versuch zählte, hätte die DSR-Schwelle für alle folgenden angehoben, ohne dass
je jemand hingeschaut hätte.

**Die Umsetzung ist eine abgeleitete Abfrage, kein gepflegtes Feld:**

```sql
SELECT count(*) FROM candidates WHERE screening_status IS NOT NULL
```

Zwei Zahlen, die zueinander passen müssen und an verschiedenen Orten stehen,
passen irgendwann nicht mehr zueinander — genau der Fehler aus ADR-020. Ein
abgeleiteter Zähler kann nicht driften.

**Der Zähler schließt den laufenden Kandidaten ein.** Er ist einer der Blicke
auf die Daten. `trial_count_at_screening` wird mitgeschrieben, weil eine DSR
nur zusammen mit der Versuchszahl interpretierbar ist, gegen die sie gerechnet
wurde.

**Gemessen, nicht behauptet:** Zwei getrennte `qt research`-Läufe hintereinander
zeigen 0 → 3 → 5, und die DSR des vierten Kandidaten rechnet gegen 4 Versuche,
nicht wieder gegen 1.

---

## ADR-027 — Der Vorlauf wird nicht bezahlt
**Datum:** 2026-08-25

**Der Befund:** Ein Gate-Lauf mit den Defaults macht 1450 LLM-Aufrufe. Ordnet
man sie je Fenster-Instanz zu, ergibt sich:

```
Warmup-Slots 914, Testfenster 500
Aufrufe gesamt 1450
  im Vorlauf (wird nicht bewertet):  870  (60%)
  im Testfenster (bewertet):         580  (40%)
```

**60% der Aufrufe wurden bezahlt und nie ausgewertet.** `_score_window`
bewertet ausschließlich ab `test_start`; alles davor formt nur den
Portfoliozustand, mit dem das Testfenster beginnt.

**Warum der Vorlauf überhaupt so lang ist:** Er richtet sich nach der
langsamsten Komponente im Feld — dasselbe Prinzip wie ADR-012, nur eine Ebene
höher. `best_single(lookback=720)` braucht 720 Bars, bis es eine Meinung hat.
Der LLM-Allokator braucht 96. Es wurde also ein Sprachmodell dafür bezahlt,
dass eine *Baseline* warmläuft.

**Die Mechanik:** `AllocationContext.is_warmup` sagt dem Allokator, ob seine
Entscheidung bewertet wird. Gesetzt wird es von
`run_portfolio_backtest(evaluate_from=...)`, das Gate reicht `cut.test_start`
durch. Der `LLMAllocator` gleichgewichtet dann, ohne den Client zu fragen.

Das ist **kein Lookahead**. Der Allokator erfährt nichts über Daten nach `ts`,
sondern nur, an welcher Stelle des Laufs er steht — eine Information, die vor
dem ersten Bar feststeht und die ein live laufendes System genauso hat.

**Der Preis, offen benannt:** Der Kandidat startet jedes Testfenster
gleichgewichtet statt LLM-geformt und zahlt beim ersten bewerteten Aufruf eine
Umschichtung, die die Baselines nicht zahlen. Das verzerrt **gegen** den
Kandidaten. Für ein Gate ist das die richtige Richtung — aber wer die Tabelle
liest, muss es wissen: *ein knapp gescheiterter Kandidat ist knapper
gescheitert, als die Zahlen zeigen.*

**`telemetry.calls` zählt die übersprungenen Aufrufe bewusst nicht mit.** Sonst
verwässerten sie die `fallback_rate` — und genau an der erkennt man, ob der
Allokator heimlich eine Baseline ist (ADR-018). Stattdessen zählt
`warmup_skips` sie separat und `summary()` nennt sie. Eine Einsparung, die
niemand sieht, wird beim nächsten Refactor versehentlich rückgängig gemacht
und fällt dann nur auf der Rechnung auf, nicht im Ergebnis.

**Der Test, auf den es ankommt:** Die Baselines ignorieren `is_warmup`, ihre
Gate-Ergebnisse müssen also vorher und nachher **bitidentisch** sein. Bewegt
sich dort etwas, ist versehentlich die Zeitachse verschoben worden. Geprüft
auf zwei Ebenen — Engine (`assert_frame_equal(check_exact=True)` auf Equity,
Allokationen, Fills) und Gate (verkettete Kurve, Metriken, Fenstergrenzen,
jede einzelne `oos_equity`). Beide halten.

**Konsequenz:** 1450 → 580 Aufrufe, `warmup_skips` summiert sich auf exakt 870.
Kandidaten-Kurven aus Läufen *vor* dieser Änderung sind nicht mehr direkt
vergleichbar; Baseline-Kurven schon.

---

## ADR-028 — Effort gehört an die Kommandozeile, und der Cache-Key hing an einem Zufall
**Datum:** 2026-08-25

**Warum:** Der Denk-Aufwand stand fest auf `medium` im Client. Er ist der
größte einzelne Hebel auf Laufzeit und Ausgabe-Token — und die Ausgabeseite
dominiert die Rechnung, weil Ausgabe-Token ein Vielfaches der Eingabe kosten.
Ein Parameter, der die Kosten eines Laufs um ein Vielfaches ändert, gehört
nicht in eine Konstante.

**Die Stufen sind gelesen, nicht erinnert:** `low, medium, high, xhigh, max`
stammen aus der installierten Bibliothek (`anthropic.types.output_config_param`
deklariert das Feld als Literal). Ein Test hält die Liste per `get_type_hints`
dagegen, damit ein SDK-Update nicht stillschweigend vorbeigeht. Geprüft wird
im Typer-Callback, also **bevor** der Lauf Daten lädt: ein Wert, den erst die
Gegenseite ablehnt, ist an der Kommandozeile kein Wert.

**Der Nebenbefund — latent, nicht live.** `qt sim` konstruierte
`ScenarioClient(cache=LLMCache())`, den Cache also ohne Modell, während der
`alloc`-Pfad `LLMCache(model=model)` übergab. Das ging aus zwei Gründen gut,
von denen an der Aufrufstelle keiner sichtbar war:

1. Beide Defaults stammen aus derselben Konstante `qt.core.config.DEFAULT_LLM_MODEL`.
2. Selbst bei Divergenz schreibt der Client sein Modell *zusätzlich* ins
   `extra` des Keys. Die Folge wäre ein Cache-Miss gewesen — doppelte Kosten
   und eine Trefferquote nahe null, aber nie die Antwort eines fremden Modells.

Das ist der harmlose Zwilling des Fehlers, der schon einmal echt war: ein
Cache-Key, der das Modell des *Caches* trug statt das des *Clients*. Repariert,
indem Modell und Effort explizit an beide Enden gehen. Ein Aufruf, dessen
Korrektheit von zwei zufällig gleichen Defaults abhängt, ist auch dann
reparaturbedürftig, wenn er heute richtig rechnet.

**Offen geblieben:** `LLMAllocator.__init__` trägt ein viertes hartes
Modell-Literal und kennt kein `effort`. Aktuell folgenlos, weil die CLI immer
einen fertigen Client injiziert — aber es ist dieselbe Driftklasse. Notiert,
nicht behoben.

---

## ADR-024 — Szenario-Priors gewichten Möglichkeiten, sie prognostizieren nichts
**Datum:** 2026-08-24

**Die Frage, die dahinter steht:** Was kann ein Sprachmodell bei einer
Marktsimulation überhaupt beitragen?

**Was es nicht kann:** einen Preis nennen. Das wäre eine Punktprognose ohne
Fehlerbalken, aus einem Modell, das keine Preisreihen rechnet. Selbst wenn die
Zahl gut klänge, wäre sie unprüfbar.

**Was es kann:** eine Lageeinschätzung, die sich in Gewichte übersetzen lässt.
Nicht „BTC steht in 30 Tagen bei X", sondern „das Marktbild spricht eher für
erhöhte Volatilität als für Beruhigung". Das ist eine Aussage über die
*Verteilung* möglicher Zukünfte — und genau die ist als Umgewichtung eines
Pfad-Ensembles darstellbar.

**Formal:** Ein Prior benennt eine messbare Pfad-Eigenschaft (`volatility`,
`terminal_return`, `max_drawdown`), eine Richtung und eine Stärke. Das
`ScenarioPriorProposal`-Schema ist so eng geschnitten, dass eine Preisprognose
**gar nicht ausdrückbar ist** — das ist der Zweck, nicht eine Bequemlichkeit.

Drei Eigenschaften, die diese Bauform gegenüber einer direkten Prognose hat:
1. **Sie kann nicht ins Unendliche danebenliegen.** Ein Prior verschiebt
   Gewichte innerhalb eines Ensembles aus echten historischen Eigenschaften.
   Einen Pfad, den die Simulation nicht erzeugt hat, kann kein Prior herbeireden.
2. **Sie ist beschränkbar.** `max_tilt` (Default 5,0) begrenzt die Verzerrung.
   Eine Punktprognose hat keine solche Bremse.
3. **Sie ist prüfbar.** Ein Prior sagt vorher, welche Region des Ensembles
   wahrscheinlicher wird. Ob das eintrat, lässt sich hinterher messen.

**Der Stub schlägt bewusst keine Priors vor.** Der neutrale Zustand ist das
gleichgewichtete Ensemble. Ein Stub, der Priors erfände, würde in Läufen ohne
API-Zugang eine Verzerrung einbauen, die im Ergebnis wie eine
Modellentscheidung aussähe.

**Halluzinierte Eigenschaften werden verworfen, nicht geraten** — dieselbe
Haltung wie bei erfundenen Strategie-Labels (ADR-018). `TranslationReport`
zählt die Ablehnungen mit: ohne diese Zahlen sieht ein Modell, dessen
Vorschläge alle im Filter hängenbleiben, aus wie ein zurückhaltendes Modell.

---

## ADR-026 — Ruin ist absorbierend
**Datum:** 2026-08-24

**Der Fehler:** `PathEnsemble.terminal_returns()` und `equity_curves()`
verketteten mit `prod(1 + r)` ohne Untergrenze. Zwei Bars mit −150% ergaben
damit (−0,5)·(−0,5) = +0,25 — gemeldet als **−75% statt −100%**, mit einer
Kapitalkurve `[1, −0,5, +0,25]`: negatives Kapital, das sich rechnerisch
erholt.

**Warum das ernst ist:** Es trifft ausgerechnet die schlimmsten Pfade — also
genau die, auf die es beim CVaR ankommt — und es beschönigt sie. Das ist die
eine Richtung, in die eine Risikorechnung nicht danebenliegen darf. Bei
gehebeltem Exposure entstehen solche Pfade regelmäßig.

**Die Korrektur:** `growth_factors()` schneidet den Wachstumsfaktor bei null
ab; `terminal_returns` und `equity_curves` bauen darauf auf. Eine Kurve, die
null erreicht, bleibt dort.

Gefunden hat das ein Agent, der beim Bau der Zielfunktion gegen dieselbe
Schnittstelle arbeitete und das Verhalten nachrechnete, statt es anzunehmen.

---

## ADR-025 — Die CVaR-Grenze bezieht sich auf den Drawdown, nicht auf die Endrendite
**Datum:** 2026-08-24

**Anlass:** Ein Messergebnis beim Bau des Block-Bootstraps, das gegen die
Erwartung ausfiel.

Die übliche Begründung für einen Block-Bootstrap lautet: ein i.i.d.-Resampling
zerstört das Volatilitäts-Clustering und **unterschätzt damit die Tails**. Die
Messung zeigt, dass das so pauschal nicht stimmt:

| Kennzahl (Historie mit Regime-Clustering, 4.000 Pfade × 250 Bars) | stationär | i.i.d. |
|---|---|---|
| Median schlimmster 20-Bar-Verlust | −25,0% | −22,0% |
| Median Max-Drawdown | −32,7% | −34,6% |
| **p05 Endrendite** | **−50,5%** | **−51,1%** |

Über kurze Fenster schlägt das Clustering klar durch. Beim 5%-Quantil der
**End**rendite über 250 Bars ist der Effekt verschwunden, sogar minimal
umgekehrt — die Vol-Mischung eines Pfades mittelt sich über viele Bars wieder
aus, und ein Pfad mit vielen ruhigen Blöcken verliert zugleich weniger
Volatilitäts-Drag.

**Die Konsequenz:** Eine CVaR-Grenze auf der Endrendite misst ausgerechnet die
Größe, bei der das Vol-Clustering — der ganze Grund für den Block-Bootstrap —
keinen Unterschied macht. Sie wäre nicht falsch, aber sie ließe die Modellwahl
folgenlos.

**Dazu der praktische Grund:** Ein Konto wird nicht am Ende des Horizonts
liquidiert, sondern unterwegs. Ein Pfad, der zwischenzeitlich 60% verliert und
bei −10% endet, ist real ein Totalschaden (Margin Call, Kill-Switch,
aufgegebener Anleger). Die Endrendite sieht ihn nicht.

**Default ist deshalb `risk_basis="drawdown"`.** Messbarer Unterschied auf
echten Daten (BTC/USD 4h, 8.000 Pfade, Horizont 180, Grenze −20%):

| Basis | Exposure | Endrendite-CVaR | Drawdown-CVaR |
|---|---|---|---|
| `terminal` | 90% | −19,03% | **−25,22%** |
| `drawdown` | 65% | −13,97% | −18,81% |

Bei 90% Exposure verlieren die schlimmsten 5% der Pfade zwischenzeitlich über
25% — ein Risiko, das die Endrendite-Grenze nicht sah. Beide Zahlen stehen
immer im Ergebnis, damit sich hinterher beantworten lässt, wie es unter der
anderen Annahme ausgefallen wäre.

---

## ADR-023 — Die Tilt-Begrenzung skaliert die Stärke, sie kappt keine Gewichte
**Datum:** 2026-08-24

**Der Fehler, den ich beim Bauen gemacht habe:** `max_tilt` war zunächst als
Deckel auf die Gewichte umgesetzt — der Boden wurde auf `max/max_tilt`
angehoben. Das klang vernünftig (erhält die Rangfolge, drängt nur die Extreme
zusammen) und war falsch.

**Warum:** Bei einem Prior der Stärke 0,8 und z-Scores über ±4 spannen die
Rohgewichte einen Faktor von rund 600. Ein Deckel bei 5 trifft damit *fast
alle* Pfade und setzt sie auf exakt denselben Wert. Die Verteilung war
praktisch wieder gleichgewichtet — während der Report korrekt „Gewichtsspanne
5.00x" meldete.

**Wie es auffiel:** Ein Vol-Prior mit Stärke 0,8 bewegte die Quantile des
Ensembles um **null** Prozentpunkte (p05 blieb bei −19,3%). Das sah aus wie
„der Prior war eben schwach", nicht wie ein Fehler.

**Die Korrektur:** Der gesamte Tilt wird im Logarithmus linear
heruntergerechnet, bis die Spanne passt. Das erhält die *Form* der Gewichtung
vollständig und schwächt nur ihre Ausprägung ab. Danach verschieben Priors die
Verteilung sichtbar und monoton in der Stärke.

**Zu lernen:** Eine Begrenzung, die ihre eigene Kennzahl erfüllt und trotzdem
wirkungslos ist, ist die unangenehmste Sorte Fehler — sie meldet Erfolg. Der
Test dazu (`test_tilt_limit_scales_rather_than_ties_paths_together`) prüft
deshalb nicht die gemeldete Spanne, sondern dass die Gewichte noch
unterscheidbar sind.

---

## ADR-022 — TimesFM als optionale, offen als unzuverlaessig markierte Strategie
**Datum:** 2026-08-24

**Anlass:** Frage, ob ein Handelsbot auf Googles TimesFM
(github.com/google-research/timesfm, ein vortrainiertes Zeitreihen-
Foundation-Model) basieren koennte.

**Antwort:** Als eine Strategie unter mehreren, nicht als Ersatz fuer das
System. TimesFM liefert einen Renditen-Forecast, keine Positionsgroesse,
keine Kostenabwaegung, kein Risikomanagement — das bleibt bei
`qt.portfolio.risk` (ADR-002 gilt unveraendert).

**Das ungeloeste Problem, offen dokumentiert statt verschwiegen:** Bei der
LLM-Allokation laesst sich Lookahead durch Anonymisierung entschaerfen
(ADR-003, ADR-017) — das Modell sieht Kennzahlen statt Rohdaten. Das
funktioniert hier nicht: die Eingabe *ist* die Rohreihe (Log-Renditen), und
TimesFM wurde auf einem grossen, nicht vollstaendig dokumentierten Korpus
vortrainiert. Ob historische Krypto-Kursreihen darin enthalten waren, ist von
aussen nicht feststellbar. Ein Backtest auf einem Zeitraum, der im
Pretraining gewesen sein koennte, ist damit **strukturell nicht
vertrauenswuerdig** — nicht wegen eines Bugs, sondern wegen der Natur eines
Foundation Models. Es gibt dagegen keine Abhilfe im Code. Konsequenz: wie
beim LLM-Allokator gilt **belastbar ist nur Forward-Paper-Trading, nicht der
Backtest** — hier noch strikter, weil selbst die Milderung durch
Anonymisierung fehlt.

**Bauweise, dem Muster von Phase 3 folgend:**
- `Forecaster`-Abstraktion (`qt.strategy.library.timesfm_strategy`), analog zu
  `Allocator`/`AllocatorClient`: `TimesFMForecaster` (echt, lazy importiert) und
  `NaiveForecaster` (Random-Walk-Vorhersage, fuer Tests und als ehrlicher
  Platzhalter ohne Modellzugang).
- Optionale Abhaengigkeit (`pyproject.toml` `[timesfm]`-Extra) — zieht `torch`
  und ein mehrere-hundert-MB-Checkpoint nach, deshalb kein Standard-Paket.
  Ohne installiertes `timesfm` ist die Strategie trotzdem voll test- und
  lauffaehig (Default-Forecaster faellt beim ersten Aufruf sauber zurueck).
- Kadenz statt Aufruf pro Bar (`forecast_every`): ein 200M-Parameter-Modell
  braucht Sekunden pro Aufruf, bei zehntausenden Bars unbezahlbar. Dieselbe
  Idee wie `allocate_every` in der Portfolio-Engine.
- Ausfall ist ein langweiliges Ereignis (ADR-018-Muster): jeder Fehler des
  Forecasters haelt das letzte Gewicht, mit **genau einer** Warnung statt
  einer pro Bar oder einem Absturz. `ForecastTelemetry` zaehlt Aufrufe,
  Cache-Treffer und Fehlschlaege — sonst faellt ein durchgehend ausfallender
  Forecaster nicht auf.
- Positionsgroesse skaliert mit der Konfidenz der Vorhersage (Quantil-
  Spannweite), nicht binaer; Schwelle `min_edge_bps` default auf das Doppelte
  der in ADR-009 gemessenen Round-Trip-Kosten gesetzt.

**Ein Detail, das eine falsche Annahme verhindert hat:** Die Quantil-Spalten,
die TimesFM zurueckgibt, sind in keiner mir vorliegenden Dokumentation
erklaert. Nachgesehen im installierten Paketquellcode
(`timesfm_2p5_torch.py`, `_compiled_decode`): Spalte 0 ist kein 10.-Perzentil
wie naheliegend vermutet, sondern ein interner Rest ohne definierte
Bedeutung fuer Aufrufer — die neun Quantile 0,1 bis 0,9 liegen in Spalte 1
bis 9, Spalte 5 ist der Median. Ohne diesen Blick in den Code waere `q10`
still falsch belegt gewesen.

---

## ADR-021 — Der Allokator darf aussteigen
**Datum:** 2026-08-22

**Der Fehler:** `LLMAllocator` behandelte jeden Vorschlag mit Summe null als
unbrauchbar und ersetzte ihn durch Gleichgewichtung. „Ich sehe gerade keine
Kante" und „meine Ausgabe ist Müll" waren derselbe Fall — ein Modell, das
aussteigen wollte, bekam ausgerechnet **volle** Gleichgewichtung.

**Die Schwierigkeit:** Beides ist tatsächlich schwer zu unterscheiden. Eine
abgeschnittene Antwort sieht aus wie ein Ausstieg.

**Die Lösung:** Ein Ausstieg zählt nur, wenn das Modell **jede** bekannte
Strategie ausdrücklich mit Gewicht 0 nennt. Eine halbe oder leere Antwort
bleibt mehrdeutig und fällt weiterhin auf Gleichgewichtung zurück — im Zweifel
gehört das Portfolio nicht flat.

`AllocatorTelemetry.deliberate_flats` zählt die Ausstiege getrennt von den
Rückfällen, damit im Report unterscheidbar bleibt, ob der Allokator entschieden
hat oder ausgefallen ist.

---

## ADR-020 — Die Engine richtet die Renditehistorie nach dem Allokator
**Datum:** 2026-08-22

**Der Fehler:** `RETURN_HISTORY` war fest auf 512 Bars gesetzt. `BestSingle`
verlangt 720. Die Baseline sah damit **nie** genug Historie, ihr Sharpe blieb in
jedem Lauf `nan`, und sie fiel unbemerkt auf Gleichgewichtung zurück.

**Warum das ernst ist:** `BestSingle` ist eine der drei Baselines, die der
LLM-Allokator laut ADR-004 schlagen muss. Sie war stillschweigend eine zweite
Equal-Weight-Zeile — das Gate prüfte also gegen zwei unterschiedliche Baselines,
nicht gegen drei, und behauptete das Gegenteil.

**Messung:** In einem vollen Lauf über BTC/USD + ETH/USD, 4h, 2019–2026 wich
`BestSingle` in **0 von 688** Allokationen von 50/50 ab. Nach der Korrektur:
681 von 688.

**Konsequenz:** Die Historienlänge folgt `Allocator.warmup_bars` plus Zuschlag.
Ein Allokator, der mehr verlangt als die Engine vorhält, rechnet dauerhaft auf
`nan` und fällt still auf sein Standardverhalten zurück — kein Fehlschlag, keine
Warnung, nur ein falsches Ergebnis. Festgenagelt durch
`test_allocator_gets_the_history_it_declares`.

**Zu lernen:** Zwei Zahlen, die zueinander passen müssen und an verschiedenen
Orten stehen, passen irgendwann nicht mehr zueinander. Der Bedarf gehört dorthin,
wo er entsteht — und die Gegenseite muss ihn abfragen statt zu raten.

---

## ADR-019 — Ohne API-Zugang gebaut, und das ist kein Provisorium
**Datum:** 2026-08-20

**Lage:** In der Bauumgebung gibt es keinen Anthropic-API-Schlüssel. Phase 3
konnte deshalb vollständig gebaut und getestet, aber **nicht mit echten
LLM-Antworten belegt** werden.

**Konsequenz, die sich als Vorteil erwiesen hat:** Der gesamte Stapel läuft
offline — `StubClient` für Tests, Antwort-Cache für wiederholte Läufe, und ein
`LLMUnavailable`, das den Backtest nicht abbricht. Das ist genau die Bauform,
die ADR-003 ohnehin verlangt (Reproduzierbarkeit) und die ein Live-System
braucht (ein Modellausfall darf kein Systemausfall sein).

Eine Testsuite, die einen Netzwerkzugang und einen Schlüssel braucht, wird
irgendwann übersprungen. Diese hier nicht: 203 Tests laufen ohne beides.

**Was offen bleibt:** Ob der LLM-Allokator die Baselines schlägt, ist
ungeprüft. Der Stub gleichgewichtet, ist also per Konstruktion identisch zur
Equal-Weight-Baseline. Sobald ein Schlüssel vorliegt:
`ANTHROPIC_API_KEY=... uv run qt alloc --compare-baselines`.

**Was das ausdrücklich nicht heißt:** Dass die Verdrahtung ungeprüft wäre. Ein
ausfallender Client, ein halluziniertes Strategie-Label, ein Vorschlag mit
Gewicht 1e9, eine Antwort die sich zu null summiert — all das ist getestet.
Geprüft ist, dass ein schlechtes Modell das System nicht beschädigen kann.
Ungeprüft ist nur, ob ein gutes Modell es verbessert.

---

## ADR-018 — Jeder Fehler des Allokators wird zu Gleichgewichtung
**Datum:** 2026-08-20

**Warum:** Ein Allokator, der bei einem Netzwerkfehler eine Exception wirft,
reißt im Livebetrieb das ganze System mit — und zwar genau dann, wenn die
Verbindung ohnehin schlecht ist. Ein Ausfall des Modells muss ein langweiliges
Ereignis sein.

**Konsequenz:** `LLMAllocator.allocate` fängt **jede** Exception, nicht nur
`LLMUnavailable`, und gibt Gleichgewichtung zurück. Dasselbe gilt für einen
Vorschlag, der sich zu null summiert: als Meinung wäre „gar nichts allokieren"
legitim, aber von einer verstümmelten Antwort ist es nicht unterscheidbar.

**Die Gefahr dabei, und ihr Gegenmittel:** Ein Allokator, der dauerhaft
zurückfällt, ist heimlich eine Baseline — und wird für gut gehalten, weil er
nie auffällt. Deshalb zählt `AllocatorTelemetry` die Rückfälle mit, und die
Rückfallquote gehört in jeden Report. Eine Quote über null ist erklärungspflichtig.

---

## ADR-017 — Anonymisierung im Briefing ist eine Testsache, keine Vorsatzsache
**Datum:** 2026-08-20

**Warum:** Fragt man ein Sprachmodell „wie hättest du im März 2020 allokiert",
weiß es die Antwort. Ein Briefing mit Datum, Asset- oder Strategienamen macht
den Backtest des Allokators wertlos, **ohne dass irgendwo ein Bug ist**. Das ist
die subtilste Fehlerquelle im ganzen Entwurf.

**Konsequenz:** `qt.llm.briefing` erzeugt ausschließlich normalisierte
Kennzahlen unter anonymen Labels. Nicht enthalten: Datumsangaben, Asset-Namen,
Strategienamen, absolute Preise, der Kontostand. Fenster werden in **Bars**
angegeben, nicht in Tagen — „30 Tage" wäre bereits eine Zeitangabe.

Festgenagelt durch neun Tests, darunter einer, der prüft, dass ein Kontostand
von 1.000 und einer von 50.000.000 dasselbe Briefing erzeugen.

**Zwei Nebeneffekte derselben Bauweise:**
- Zahlen werden auf drei Stellen gerundet. Exakte Fließkommawerte sind ein
  Fingerabdruck, über den sich eine historische Periode identifizieren ließe.
- Das Briefing ist für denselben Zustand bitgleich — Voraussetzung dafür, dass
  der Antwort-Cache überhaupt trifft. Ein `datetime.now()` darin hätte einen
  Cache mit 0% Trefferquote erzeugt, ohne dass etwas fehlschlägt.

**Was das nicht löst:** Ein Einbruch von −50% bei verdreifachter Volatilität ist
auch anonymisiert wiedererkennbar. Deshalb gilt unverändert: der Allokator wird
primär am Forward-Paper-Trading gemessen, nicht am Backtest.

---

## ADR-016 — Korrektur: die Risk-Engine drosselt nicht, die Kennzahl war irreführend
**Datum:** 2026-08-20

**Korrigiert:** die offene Frage in ADR-013 und den „HIER WEITER"-Schritt in
ROADMAP.md. Beide behaupteten, der Symbol-Cap von 25% drossele zu hart, weil die
realisierte Vola bei 3,7% statt der angepeilten 20% lag. **Das war falsch.**

**Messung** (Portfolio `trend` + `meanrev`, BTC/USD + ETH/USD, 4h, 2019–2026):

| Variante | Vola gesamt | Vola über aktive Bars | Bars mit Position |
|---|---|---|---|
| Symbol-Cap 25% (Default) | 3,7% | 13,9% | 7,3% |
| Symbol-Cap 50% | 3,8% | — | — |
| Symbol-Cap 100% (praktisch aus) | 3,8% | — | — |
| Drawdown-Stop praktisch aus | 7,4% | 12,9% | 33,0% |

Der Symbol-Cap macht 0,1 Prozentpunkte Unterschied. Er war nie die Ursache.

**Die tatsächliche Erklärung:** Das Portfolio ist zu **92,7% flat** — teils weil
die Strategien selbst meist kein Signal geben (`trend` ist zu 65% flat), teils weil
der Kill-Switch früh auslöst. Annualisierte Volatilität über eine überwiegend
flache Reihe misst vor allem Untätigkeit, nicht Drosselung. *Während* das
Portfolio positioniert ist, liegt die Vola bei 13,6–13,9% — plausibel nahe am Ziel
von 20%, der Rest ist Vol-Targeting, das seine Arbeit tut.

**Konsequenz:** Zwei neue Kennzahlen in `qt.backtest.metrics`, **Zeit im Markt**
und **Vola p.a. aktiv**. Ohne sie liest man `ann_vol` bei jeder überwiegend
flachen Strategie falsch — was hier tatsächlich passiert ist und beinahe eine
Kalibrierung ausgelöst hätte, die nichts repariert hätte.

**Zu lernen:** Eine Kennzahl, die zwei Effekte vermischt (wie stark bin ich
positioniert × wie oft bin ich positioniert), taugt nicht als Diagnose. Die
Hypothese stand in der ROADMAP, klang plausibel und war ungeprüft — die Messung
hat zehn Minuten gedauert und die geplante Arbeit überflüssig gemacht.

---

## ADR-015 — Fill-Modell als Abstraktion, mit größenabhängiger Variante
**Datum:** 2026-08-20

**Anlass:** Vergleich mit NautilusTrader. Dort ist das Fill-Modell ein Trait mit
acht Implementierungen (gestufte Orderbuchtiefen, größenabhängig, probabilistisch
mit gesetztem RNG-Seed). Bei uns war es eine feste Formel, die das **Volumen
komplett ignorierte**. Übernommen ist die Struktur, nicht der Code — Nautilus
steht unter LGPL-3.0.

**Das Problem, das dabei sichtbar wurde:** Unter dem alten Modell liefert dieselbe
Strategie bei 100.000 und bei 100.000.000 Startkapital **exakt dasselbe Ergebnis**
(Faktor 0,457). Sie skaliert unendlich. Kapazität war im gesamten System nicht
darstellbar.

**Messung** (trend, BTC/USD 4h, 2019–2026):

| Startkapital | flat | größenabhängig | Differenz |
|---|---|---|---|
| 100.000 | 0,457x | 0,364x | −20,3% |
| 10.000.000 | 0,457x | 0,096x | −78,9% |
| 100.000.000 | 0,457x | 0,028x | −93,9% |

Die Zahlen sind nicht von Ausreißern getrieben: kein Bar hat Volumen 0, die
Median-Beteiligung liegt bei 0,05% (p99: 1,6%), der Median-Aufschlag bei 2,31 bps.
Über 820 Trades summiert sich das.

**Modell:** Der zusätzliche Aufschlag wächst mit der **Wurzel** der
Beteiligungsquote am Bar-Volumen — die Standardnäherung für Market Impact. Bei
vierfacher Ordergröße verdoppelt sich der Aufschlag, er vervierfacht sich nicht.
Ohne bekanntes Volumen fällt das Modell auf den konstanten Aufschlag zurück:
eine Impact-Schätzung ohne Volumenbezug wäre geraten, und geraten ist schlechter
als bescheiden.

**Default bleibt `flat`.** Das größenabhängige Modell ist eine Näherung mit einem
frei gewählten Parameter (`impact_bps=100`), der nicht kalibriert ist. Es als
Default zu setzen hieße, einen geschätzten Wert wie eine Messung zu behandeln.
Wählbar über `--fills size_aware`.

**Konsequenz für Phase 5:** Der Research-Loop muss Kapazität mitbewerten. Eine
Strategie, die bei 100k funktioniert und bei 10 Mio nicht, ist keine Kante,
sondern eine Nische — und das gehört in die Registry.

---

## ADR-014 — Positionsgrößen werden gegen Marktwerte gerechnet, nicht Einstände
**Datum:** 2026-08-20

**Der Fehler:** `_rebalance_order` bewertete das Eigenkapital mit
`broker.equity({symbol: reference_price})` — also nur mit dem Preis des gerade
schliessenden Symbols. `SimBroker.equity` fällt für alle übrigen Positionen auf
deren **Einstandspreis** zurück. Die Positionsgröße hing damit an einem
Eigenkapital, in dem der Rest des Portfolios zu historischen Kursen stand.

**Warum er so lange unsichtbar war:** Bei einem einzelnen Symbol ist der Ausdruck
korrekt — es gibt keine anderen Positionen. Phase 1 hatte nur Einzelsymbol-Läufe.
Der Fehler entstand nicht in Phase 2, er wurde dort erst sichtbar.

**Auswirkung:** In einem Testfall mit einem verdreifachten und einem flachen
Symbol lag die Zielposition bei 32,9% statt 50% — das Portfolio sizete dauerhaft
zu klein, ohne dass irgendetwas fehlschlug.

**Konsequenz:** Beide Engines bewerten jetzt mit den letzten bekannten Preisen
**aller** Symbole. Festgenagelt durch
`test_position_sizing_values_the_whole_portfolio_at_market`, per Mutation
gegengeprüft.

**Zu lernen:** Ein Ausdruck, der für den Einzelfall korrekt ist, ist deshalb
nicht allgemein korrekt. Multi-Symbol-Tests gehören auch dann geschrieben, wenn
das System noch einsymbolig ist.

---

## ADR-013 — Die Risk-Engine ist die wirksamste Komponente im System
**Datum:** 2026-08-20

**Messung** (Portfolio aus `trend` + `meanrev`, BTC/USD + ETH/USD, 4h, 2019–2026):

| Lauf | Gesamtrendite | Vola p.a. | Max Drawdown |
|---|---|---|---|
| `vol_parity`, ohne Risk-Engine | −99,68% | 32,8% | **−99,72%** |
| `equal_weight`, mit Risk-Engine | −16,90% | 3,7% | **−20,11%** |

Identische Strategien, identische Daten. Der Unterschied ist ausschliesslich
die Risikoschicht.

**Was das heisst:** Die Risk-Engine hat aus einem Totalverlust einen
überschaubaren verwandelt. Sie macht schlechte Strategien nicht gut — der
Sharpe bleibt negativ — aber sie sorgt dafür, dass man einen Fehler überlebt
und korrigieren kann.

**Warum das für Phase 3 zentral ist:** Genau diese Schicht steht zwischen dem
LLM-Allokator und dem Konto. Der Befund ist der empirische Beleg dafür, dass
die Reihenfolge "Allokator schlägt vor, Risk-Engine entscheidet" richtig
herum ist.

**~~Offener Punkt~~ — erledigt, siehe ADR-016:** Die hier notierte Vermutung, die
Engine drossele mit ihrem Symbol-Cap zu hart, hat sich als falsch erwiesen. Die
3,7% waren ein Messartefakt einer zu 92,7% flachen Reihe; während das Portfolio
positioniert ist, liegt die Vola bei 13,9%. Keine Kalibrierung nötig.

---

## ADR-012 — Warmup wartet auf die langsamste Strategie
**Datum:** 2026-08-20

**Warum:** Würde das Portfolio starten, sobald *eine* Strategie warm ist, bekäme
eine noch blinde Strategie bereits Kapital. Ihr Nullgewicht sähe aus wie eine
bewusste Flat-Entscheidung und verwässerte das Portfolio, ohne dass es in den
Kennzahlen auffiele.

**Konsequenz:** Die langsamste Strategie bestimmt den Start (`meanrev` mit 194
Bars Warmup). Der Preis ist verlorene Historie am Anfang — akzeptiert.

---

## ADR-011 — Papier-Renditen für den Allokator sind brutto
**Datum:** 2026-08-20

**Warum:** Der Allokator vergleicht Strategien anhand ihrer Renditereihen. Würde
man diesen Reihen Kosten aufbürden, hinge der Vergleich an der Positionsgröße,
die der Allokator gerade selbst vergeben hat — eine Rückkopplung, in der eine
zufällig klein gestartete Strategie dauerhaft klein bliebe.

**Konsequenz:** `_accrue_paper_returns` in `portfolio_engine.py` rechnet ohne
Kosten. Die tatsächliche Kontoentwicklung enthält selbstverständlich alle
Kosten; nur dieser Vergleichsmaßstab ist brutto. Im Docstring vermerkt, damit
es niemand später "korrigiert".

---

## ADR-010 — Vorgemerkte Orders werden ersetzt, nicht addiert
**Datum:** 2026-08-20

**Warum:** Alle Orders im System sind Differenzen zu einem Zielgewicht. Zwei
aufeinanderfolgende Vormerkungen für dasselbe Symbol sind zwei Schätzungen
derselben Absicht, nicht zwei Absichten. Beim Portfolio-Lauf mit gemischten
Timeframes schliessen 1h- und 4h-Bar desselben Symbols gleichzeitig — bei
additiver Semantik verdoppelte das Portfolio dann seine Position.

**Konsequenz:** `SimBroker.submit()` verwirft eine bestehende Vormerkung
desselben Symbols. Für den Einzelstrategie-Lauf ändert sich nichts, dort gab
es nie zwei Vormerkungen zwischen zwei Ausführungen.

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
