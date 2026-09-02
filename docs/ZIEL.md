# Das Ziel, und was bis dahin fehlt

Stand 2026-09-02. Ergänzt ROADMAP.md (was gebaut wird) um die Frage, *wofür*.

---

## Das Ziel

> **Bis 2028-09-01 handelt ein System dieses Projekts echtes Geld und hat über
> 12 zusammenhängende Live-Monate nach allen Kosten Gewinn gemacht — bei einem
> besseren Verhältnis von Rendite zu Drawdown als Buy-and-Hold BTC im selben
> Zeitraum, und ohne den 25-%-Kill-Switch auszulösen.**

Vier Bedingungen, alle gleichzeitig:

| | Bedingung |
|---|---|
| Echtheit | echtes Geld, echte Fills, mindestens 12 zusammenhängende Monate |
| Ertrag | nach allen Kosten positiv |
| Qualität | Calmar (Rendite / MaxDD) über dem von Buy-and-Hold BTC |
| Überleben | kein Kill-Switch-Auslöser |

**Warum Calmar und nicht Rendite.** In Krypto ist Rendite fast vollständig
Beta. Wer 2020–2021 long war, sah großartig aus. Der einzige Beitrag, den
dieses Projekt je gemessen hat, ist *derselbe Ertrag mit weniger Absturz*
(ADR-035) — und genau das misst Calmar. Rohe Rendite als Ziel wäre in einem
Bullenmarkt durch Nichtstun erreichbar und in einem Bärenmarkt durch nichts.

### Der Termin, der wirklich zählt, ist viel früher

> **2027-03-01 — Gate 1: hat irgendein Kandidat alle Kontrollen bestanden?**

Alles danach ist Ausführung. Wenn bis dahin nichts durch die Kontrollen kommt,
ist die Antwort des Projekts „kein Edge gefunden", und das ist ein Ergebnis,
kein Scheitern. Siehe *Abbruchbedingung* unten.

---

## Warum genau dieses Ziel — die Arithmetik, die alles diktiert

Sieben Hypothesen geprüft, sieben gescheitert. Der Grund ist in keinem einzigen
Fall gewesen, dass die Idee dumm war. Er ist immer derselbe: **es gibt zu wenig
unabhängige Evidenz, um irgendetwas zu zeigen.**

Der Standardfehler eines annualisierten Sharpe hängt an der **Kalenderspanne**
und der **Zahl unabhängiger Reihen** — nicht an der Bar-Frequenz (ADR-047).
Bei 7,7 Jahren Historie:

| unabhängige Märkte | beweisbarer Sharpe (t ≥ 2) | Lage |
|---|---|---|
| 1,0 | 0,84 | ein einzelner Markt |
| **1,4** | **0,67** | 13 Krypto-Märkte, ρ = 0,67 |
| **3,4** | **0,41** | **heute: + 13 ETFs, ρ = 0,26 (ADR-055)** |
| 3,0 | 0,44 | + Aktien |
| 5,0 | 0,33 | + Aktien, FX, Rohstoffe |
| 8,0 | 0,26 | vier echte Anlageklassen |

**`macross` hat 0,31.** Um das auf einer einzelnen Reihe zu zeigen, bräuchte es
**46 Jahre**. Deshalb ist es an der Permutationskontrolle gescheitert (ADR-054),
und deshalb wäre es an jeder anderen auch gescheitert. Nicht weil es schlecht
ist, sondern weil die Datenlage die Frage nicht beantworten kann.

Zwei Wege führen aus dieser Zeile heraus, und nur zwei:

1. **Einen deutlich stärkeren Edge finden** (Sharpe ≥ 0,67 statt 0,31).
2. **Die Zahl unabhängiger Märkte erhöhen**, damit schwächere Edges beweisbar
   werden.

Weg 2 ist rechenbar, planbar und in Wochen erledigt. Weg 1 ist Hoffnung. Der
Plan unten geht deshalb zuerst Weg 2 — und macht Weg 1 danach überhaupt erst
entscheidbar.

---

## Was fehlt — nach Hebelwirkung sortiert, nicht nach Aufwand

### 1. Unkorrelierte Märkte *(der einzige Hebel, der Evidenz vervielfacht)*

Der Store hat 14 Krypto-Paare und einen Gold-Token. Krypto ist **eine** Wette:
mittlere paarweise Korrelation **0,67**, gemessen (ADR-054). Dreizehn Märkte
sind 1,4 unabhängige.

Mehr Krypto hinzuzufügen ist fast wertlos — der 14. Markt bringt bei ρ = 0,67
rund 2 % zusätzliche effektive Beobachtung. **Aktien, FX, Rohstoffe und Anleihen
brächten je eine fast ganze.** Von 1,4 auf 5 effektive Märkte senkt die
beweisbare Schwelle von 0,67 auf 0,33 — dorthin, wo die bisher gemessenen
Effekte tatsächlich liegen.

*Was dafür fehlt:* eine zweite Datenquelle. `ccxt` kann nur Krypto.

### 2. Das Kostenregime — nie hinterfragt, entscheidet über alles

ADR-009, dieselbe Strategie, derselbe Zeitraum, nur die Gebührenannahme
verschieden:

| Kostenniveau | Endkapital | Faktor |
|---|---|---|
| ohne Kosten | 644.194 | 6,44× |
| Maker (~16 bps Round-Trip) | 403.760 | **4,04×** |
| Coinbase Taker (90 bps) | 45.725 | **0,46×** |

**Die Kostenannahme leistet mehr als jede Strategieentscheidung des Projekts.**
90 bps sind der konservative Default und als Default richtig — aber sie sind
eine Annahme über die Ausführung, kein Naturgesetz, und sie wurde nie an einem
echten Gebührenplan geprüft.

Wichtig, damit hier keine falsche Hoffnung entsteht: **niedrigere Kosten geben
keine zusätzliche Evidenz.** Der Standardfehler bleibt, wo er ist. Was sie
ändern, ist der *Suchraum*: bei 16 bps sind Strategien möglich, die bei 90 bps
tot sind — und darunter vielleicht eine mit Sharpe über 0,67.

> **Erledigt am 2026-09-02 (ADR-056), und der Absatz oben stimmt nur halb.**
> Die Tabelle darüber ist `trend` auf **4h**. Auf **1d** kostet dieselbe
> pessimistische Annahme 0,08 bis 0,23 Sharpe statt 0,94 bis 1,26 — das
> Kostenniveau leistet dort **weniger** als jede Strategieentscheidung, nicht
> mehr. Der Satz war nie falsch, er war nie allgemein, und er ist überall
> zitiert worden, als wäre er es.
>
> Zwei Korrekturen fielen dabei an. Der Gebührensatz im Code (40 bps) war die
> zweite Zeile der Coinbase-Staffel; die Eingangsstufe zahlt **60**. Der
> Default steht jetzt dort, der Round-Trip damit auf **130 bps** — alle
> bisherigen Zahlen werden dadurch schlechter, nicht besser. Und der halbe
> Spread ließ sich **nicht** messen: der Versuch über Corwin/Schultz und
> Abdi/Ranaldo ist gescheitert und liegt als markierter Fehlschlag im Repo.
>
> Da unter 1d ohnehin nicht gesucht wird (ADR-047), wird die Hoffnung aus
> diesem Abschnitt **nicht gebraucht**.

### 3. Eine Zielhöhe, die zur Datenlage passt

Das Projekt sucht implizit nach „positiv out-of-sample". Das ist die falsche
Latte: 0,31 ist positiv und trotzdem nicht zeigbar. **Die Suche braucht ein
Mindest-Sharpe als Vorabkriterium**, abgeleitet aus der dann vorhandenen Zahl
unabhängiger Märkte — und Kandidaten darunter werden gar nicht erst gescreent,
weil jedes Screening den DSR-Nenner dauerhaft erhöht (ADR-032).

### 4. Vorwärts-Evidenz, und die unbequeme Rate

Gemessen: `macross` handelt **4,3 Round-Trips pro Jahr und Markt**. Zwei Konten
mit n_eff 1,21 ergeben rund **5 effektive Round-Trips pro Jahr**. Für 30
Round-Trips: **sechs Jahre**.

**Die Paper-Konten können die Frage in ihrer jetzigen Form nicht beantworten.**
Das ist kein Argument, sie abzuschalten — sie kosten nichts und fangen
Katastrophen. Es ist ein Argument dagegen, sie für den Beweis zu halten. Sie
sind eine Plausibilitätsprüfung, kein Gate.

### 5. Der Live-Pfad *(das kleinste Stück, deshalb zuletzt)*

Nicht gebaut: `qt.live.broker_ccxt` (echte Orders), `qt.live.reconcile`
(Soll-gegen-Ist, braucht die zweite Quelle), Positionsgrößen-Logik für echtes
Kapital, Börsen-Keys und ihre Verwahrung.

Das ist Ingenieursarbeit ohne offene Fragen — ein bis zwei Arbeitsblöcke. Es
steht zuletzt, weil es das Ziel nicht näher bringt: ein Live-Pfad ohne
validierten Edge ist ein Weg, schneller Geld zu verlieren.

---

## Die Durchführung

### Phase A — Datenbasis verbreitern *(2–3 Arbeitsblöcke)*

Ziel: **n_eff von 1,4 auf mindestens 4.**

1. Zweite Datenquelle für Nicht-Krypto anbinden. Der Schnitt existiert schon:
   `qt.data.ingest` ist quellenagnostisch, `qt.data.store` kennt nur Symbole.
   Zu klären ist die Quelle, nicht die Architektur.
2. Je Anlageklasse mehrere liquide Instrumente ziehen, mindestens über dieselben
   7,7 Jahre. Aktienindex, FX-Paar, Rohstoff, Anleihe.
3. **Abnahme:** `qt placebo cross` über den erweiterten Store meldet
   n_eff ≥ 4 statt 1,4. Das ist eine Zahl, kein Eindruck.

> **Durchgeführt am 2026-09-02 — verfehlt (ADR-055).** 13 US-ETFs gezogen, die
> mittlere Korrelation fällt von 0,67 auf 0,26, n_eff steigt von 1,4 auf
> **3,4**. Die Schwelle war 4. Die drei Fallen unten sind beseitigt.
>
> Der Versuch, das über einen anderen Schätzer doch noch zu bestehen, ist im
> ADR dokumentiert und **fehlgeschlagen**: `n/(1+(n−1)·ρ̄)` ist nicht die
> Näherung, für die ich sie hielt, sondern exakt `n²/(1ᵀC1)` — Differenz 4e-16.
>
> Konsequenz für Gate 1: **Mindest-Sharpe 0,41** statt der erhofften 0,33.
> Um auf 4 zu kommen, fehlt eine Anlageklasse, die mit keiner vorhandenen
> läuft — Volatilität, Zinsdifferenzen, Einzelwerte außerhalb der Indizes.

*Fallstrick, vorab benannt und inzwischen beseitigt:* Nicht-Krypto handelt
nicht 24/7. `bars_per_year` und `find_gaps` unterstellten durchgehend Krypto —
eine Aktienreihe wäre um Faktor 1,20 zu hoch annualisiert und als lückenhaft
gemeldet worden. Dazu kam eine dritte, die hier noch nicht stand: das
Kostenmodell kannte keine Symbole, ein gemischter Lauf war damit gar nicht
ehrlich möglich. Alle drei sind erledigt (ADR-055).

### Phase B — Kostenregime klären ✅ *(erledigt 2026-09-02, ADR-056)*

1. ~~Realen Gebührenplan der Zielbörse dokumentieren, mit Quelle und Datum.~~
   Coinbase 0,40/0,60 %, Kraken 0,40/0,80 %, Alpaca provisionsfrei plus
   Aufsichtsgebühren. Die Regime heißen jetzt `coinbase_taker`,
   `kraken_maker` und so weiter, jedes mit Quelle und Abrufdatum im Code.
2. ~~Sensitivitätstabelle für alle Strategien nachziehen.~~ `qt costs` zieht
   sie auf Zuruf. ADR-009 reproduziert auf 4h exakt (6,44× / 4,04×) — und
   gilt dort auch nur.
3. ~~Entscheidung als ADR.~~ Siehe unten.

**Das Ergebnis in drei Zeilen:**

| | |
|---|---|
| Gesucht wird unter | `coinbase_taker` — 130 bps Round-Trip, ohne Maker-Hoffnung |
| Zeitebene | **1d oder gröber**; darunter frisst die Ausführung den Edge |
| Umschlagbudget | **≤ 7× Eigenkapital pro Jahr** (aus 0,10 Sharpe Kostenspielraum) |

Das Budget folgt aus einer Identität, die auf wenige Prozentpunkte stimmt:
**Drag p. a. ≈ (Umschlag/EK/Jahr) × einfache Kosten**, und
**ΔSharpe ≈ −Drag/Vola**. Damit ist die Kostenwirkung eines Kandidaten
vorhersagbar, ohne ihn zu rechnen.

Was **nicht** geklärt ist und offen heißt: der reale Spread (nicht messbar aus
Tages-OHLC), die Warteschlangenposition bei Limit-Orders, und ob Coinbases
0,60 % stimmen — deren Seite antwortet hier mit HTTP 403, die Zahl steht auf
drei übereinstimmenden Sekundärquellen. Alle drei brauchen echte Fills.

### Phase C — Suchen, mit scharfer Latte *(offen, das ist die eigentliche Arbeit)*

Erst jetzt, weil erst jetzt feststeht, wonach gesucht wird.

1. Mindest-Sharpe aus Phase A ableiten und **vorab** als ADR festschreiben:
   **0,41** (ADR-055). Dazu das Umschlagbudget aus Phase B: **≤ 7× EK/Jahr**,
   auf 1d oder gröber, unter `coinbase_taker` (ADR-056).
2. Research-Loop und eigene Ideen gegen die erweiterte Marktbasis.
3. **Jeder Kandidat durchläuft die volle Kette, in dieser Reihenfolge:**
   Sandbox → Kritik → Walk-Forward → DSR → `qt placebo shuffle` →
   `qt placebo cross`. Die Kontrollen stehen jetzt als Befehl bereit
   (ADR-054), sie kosten 0,09 s je Durchlauf — es gibt keinen Grund mehr,
   sie ans Ende zu schieben.

### Gate 1 — 2027-03-01

**Bestanden**, wenn ein Kandidat *alle* erfüllt:

| | Kriterium |
|---|---|
| OOS-Sharpe | ≥ der in Phase A abgeleiteten Schwelle |
| DSR | ≥ 0,95 gegen den dann gültigen Versuchszähler |
| Permutation | Perzentil ≥ 95 % (`qt placebo shuffle`) |
| Querschnitt | Median-Sharpe > 0 über die erweiterten Märkte |
| Anlageklassen | wirkt in mindestens zwei, nicht nur in Krypto |
| Umschlag | ≤ 7× Eigenkapital pro Jahr, unter `coinbase_taker` (ADR-056) |

Die letzte Zeile ist neu und die schärfste: ein Effekt, der nur in einer
Anlageklasse auftritt, ist wahrscheinlich deren Beta und nicht dein Edge.

### Phase D — Paper-Forward *(6 Monate, läuft nebenher)*

Der Kandidat aus Gate 1 auf Paper, über die bestehende Routine. **Kein Gate,
sondern eine Sicherung:** bei realistischer Trade-Frequenz reichen sechs Monate
für keinen Beweis, aber vollkommen für den Nachweis, dass Verdrahtung, Kosten
und Ausführung sich verhalten wie im Backtest. Divergenz hier ist ein Stopp.

### Phase E — Live mit Minimalkapital

`broker_ccxt`, `reconcile`, Positionsgrößen, harte Limits. Startkapital so
klein, dass ein Totalverlust folgenlos ist. Ab dem ersten Fill läuft die Uhr
für das Ziel oben.

---

## Die Abbruchbedingung

**Kommt bis 2027-03-01 kein Kandidat durch Gate 1, geht nichts live.**

Dann lautet die Antwort des Projekts: *mit dieser Datenmenge, diesen
Kostenannahmen und diesen Methoden ist in liquiden Märkten kein Edge
nachweisbar, der die Selektion überlebt.* Das ist ein belastbares, negatives
Ergebnis — sauber hergeleitet, mit vollständigem Audit-Pfad, und ehrlicher als
die meisten positiven Ergebnisse in diesem Feld.

Was in dem Fall ausdrücklich **nicht** passiert: die Schwelle senken, die
Kontrollen lockern, oder ein achtes Mal dieselbe Klasse Idee versuchen. Der
Versuchszähler steht bei 8 und vergisst nichts (ADR-032).

---

## Der ehrliche Erwartungswert

Sieben Hypothesen, sieben gescheitert. Nichts im Repo hat je eine
Negativkontrolle bestanden. Es gibt keinen Grund anzunehmen, dass eine
verbreiterte Datenbasis daran etwas ändert — sie macht nur die *Frage*
entscheidbar, die bisher offenbleiben musste.

Der Wert dieses Plans liegt nicht darin, dass er zum Ziel führt. Er liegt
darin, dass er in sechs Monaten eine **belastbare Antwort** liefert statt einer
achten unentscheidbaren Messung.
