# Das Ziel, und was bis dahin fehlt

Stand 2026-09-03. Ergänzt ROADMAP.md (was gebaut wird) um die Frage, *wofür*.

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

Neun Hypothesen geprüft, neun gescheitert. Der Grund ist in keinem einzigen
Fall gewesen, dass die Idee dumm war. Er ist immer derselbe: **es gibt zu wenig
unabhängige Evidenz, um irgendetwas zu zeigen.**

Der Standardfehler eines annualisierten Sharpe hängt an der **Kalenderspanne**
und der **Zahl unabhängiger Reihen** — nicht an der Bar-Frequenz (ADR-047).
Bei 7,7 Jahren Historie:

| unabhängige Märkte | beweisbarer Sharpe (t ≥ 2) | Lage |
|---|---|---|
| 1,0 | 0,84 | ein einzelner Markt |
| **1,4** | **0,67** | 13 Krypto-Märkte, ρ = 0,67 |
| 3,4 | 0,41 | + 13 ETFs, ρ = 0,26 (ADR-055) |
| 3,0 | 0,44 | + Aktien |
| **5,1** | **0,33** | **heute: + 12 Reihen, ρ = 0,18 (ADR-061)** |
| 8,0 | 0,26 | vier echte Anlageklassen |

**`macross` hat 0,31** — und auf einem am 2026-09-03 frisch gezogenen Store nur
noch **0,25** (ADR-059). Um 0,31 auf einer einzelnen Reihe zu zeigen, bräuchte
es **46 Jahre**; für 0,25 nach derselben Rechnung rund **71**. Deshalb ist es an
der Permutationskontrolle gescheitert (ADR-054), und deshalb wäre es an jeder
anderen auch gescheitert. Nicht weil es schlecht ist, sondern weil die
Datenlage die Frage nicht beantworten kann.

Zwei Wege führen aus dieser Zeile heraus, und nur zwei:

1. **Einen deutlich stärkeren Edge finden** (Sharpe ≥ 0,33 statt 0,25).
2. **Die Zahl unabhängiger Märkte erhöhen**, damit schwächere Edges beweisbar
   werden.

Weg 2 ist rechenbar, planbar und in Wochen erledigt. Weg 1 ist Hoffnung. Der
Plan unten geht deshalb zuerst Weg 2 — und macht Weg 1 danach überhaupt erst
entscheidbar.

> **Weg 2 ist am 2026-09-03 zu Ende gegangen (ADR-061).** n_eff steht bei
> 5,1, die Nachweisgrenze bei 0,33. Damit ist der billige Hebel gezogen: von
> 5 auf 8 effektive Märkte brächte nur noch 0,33 → 0,26, und dafür fehlen die
> Anlageklassen. **Ab hier bleibt Weg 1** — und `macross` liegt mit 0,25
> weiterhin darunter.

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

### 5. Der Live-Pfad ✅ *(gebaut 2026-09-03, ADR-062 — und unverdrahtet)*

~~Nicht gebaut: `qt.live.broker_ccxt` (echte Orders), `qt.live.reconcile`
(Soll-gegen-Ist, braucht die zweite Quelle), Positionsgrößen-Logik für echtes
Kapital, Börsen-Keys und ihre Verwahrung.~~ Alles vier existiert, mit 30
Tests, von denen keiner Netz braucht.

**`qt live tick` ist trotzdem nicht verdrahtet, und das ist der Punkt.** Der
Satz unten gilt unverändert: ein Live-Pfad ohne validierten Edge ist ein Weg,
schneller Geld zu verlieren. Neun Strategien geprüft, keine hat eine
Negativkontrolle bestanden. Was zum Handeln fehlt, ist kein Code mehr,
sondern Gate 1.

Gebaut wurde er trotzdem, weil er Fragen stellt, die kein Kursverlauf
enthält — Mindestordergrößen, Rundungsraster, was eine Börse als Fill
zurückmeldet. Eine davon ist beantwortet: bei Coinbase greift die
Mindestordergröße erst unterhalb von rund 100 USD Kontogröße, der Effekt ist
also echt und klein (`qt live groesse`).

---

## Die Durchführung

### Phase A — Datenbasis verbreitern ✅ *(erledigt 2026-09-03, ADR-061)*

Ziel: **n_eff von 1,4 auf mindestens 4.** Erreicht: **5,1.**

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

> **Zweiter Anlauf am 2026-09-03 — bestanden (ADR-061).** Zwölf Reihen, nach
> genau diesem Kriterium ausgesucht statt nach Anlageklasse allein: ρ̄ fällt
> von 0,26 auf **0,18**, n_eff steigt auf **5,1** über 38 prüfbare Märkte.
>
> Der Unterschied zum ersten Anlauf ist die Frage, die gestellt wurde. Dort:
> „welche Anlageklassen fehlen?" Hier: „welche Reihen laufen mit dem Bestand
> **nicht** mit?" Die Rechnung sagt, warum das der richtige Hebel ist —
> `n/(1+(n−1)ρ̄)` hängt fast nur an ρ̄; von 30 auf 60 Märkte zu verdoppeln
> lockert die Anforderung an ρ̄ um 0,012.
>
> Der Korb stand **vor** der Messung fest, mit vier Ausschlussregeln gegen den
> bequemen Weg — keine inversen Produkte (`SH` wäre −SPY: Korrelation −1, null
> Information), keine Geldmarktnähe, Historie bis 2021-09-30, nur Long. Zwei
> der zwölf (`VNQ`, `EWJ`) **verschlechtern** n_eff und sind trotzdem drin.
> Klassenweise weggelassen bleibt das Ergebnis über 4,48; es hängt an keiner
> einzelnen Reihe.
>
> Konsequenz für Gate 1: **Mindest-Sharpe 0,33.** Kein Kandidat gewinnt
> dadurch — `macross` bricht weiter beim Umschlag ab, `crossmom` steht bei
> −0,08.

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

1. ~~Mindest-Sharpe aus Phase A ableiten und **vorab** als ADR
   festschreiben.~~ **Erledigt und ausführbar gemacht (ADR-057):** alle
   Kriterien stecken in `qt gate`, mit fest verdrahteten Schwellen und
   **ohne eine einzige Option, die eine davon setzt**. Wer die Latte senkt,
   hinterlässt einen Diff.

   > **Der Bestand ist durch — und niemand kommt bis zum Walk-Forward.**
   > Fünf der sieben Bibliotheksstrategien scheitern am Umschlagbudget
   > (`trend` 15,4×, `meanrev` 15,7×, `elliott` 13,3×, `macross` 8,8×,
   > `hashribbon` 7,1×), zwei daran, dass sie mangels Datenquelle gar nicht
   > handeln. Kein Lauf hat einen Versuch gekostet.
   >
   > `macross`, seit ADR-035 die einzige Hoffnung des Projekts, scheitert
   > damit **nicht am Signal**, sondern daran, dass es sich seine eigene
   > Handelsfrequenz nicht leisten kann.
   >
   > Nebenbefund, der die Latte für alle hebt: der Versuchszähler kannte die
   > sieben handgeschriebenen Hypothesen nicht und stand bei 8. Er steht
   > jetzt bei **15**.
2. Research-Loop und eigene Ideen gegen die erweiterte Marktbasis.

   > **Eine neue Strategiefamilie steht seit 2026-09-03 bereit (ADR-058):
   > Querschnitt statt Timing.** Sie stellt die Märkte gegeneinander, statt
   > jeden für sich zu betrachten — damit wird die Korrelation von 0,26, die
   > 26 Einzeltests auf 3,4 effektive zusammenschrumpfen lässt, zu dem, was
   > herausgerechnet wird, statt zum Verlust.
   >
   > `crossmom` (12-1-Momentum, monatlich umgeschichtet) ist die **erste
   > Strategie des Projekts, die Aktivität und Umschlagbudget besteht** und
   > bis in einen Walk-Forward kommt: 494 Ausführungen, 4,6× Umschlag — und
   > dann OOS-Sharpe **−0,24**. Sie scheitert an der Zahl, auf die es ankommt.
   > Versuchszähler damit **16**.
   >
   > Der Vorbehalt gehört dazu: 27 Märkte sind ein dünner Querschnitt, und die
   > IC-Streuung von 0,40 heißt, dass auch ein echter Effekt hier schwer zu
   > zeigen wäre. Dieselbe Datenknappheit wie überall, nur an einer anderen
   > Achse.
   **Nicht mehr blockiert, und das ist neu.** Dieser Absatz stand hier als
   „in dieser Umgebung ist kein API-Schlüssel gesetzt". Am 2026-09-03 ist
   `NVIDIA_API_KEY` gesetzt, und ein vollständiger Gate-Lauf über
   `--provider nim` ist durchgelaufen — 20 Aufrufe, ohne einen einzigen
   Rückfall, und mit Sharpe −1,50 durchgefallen (ADR-060). Die Kette war
   ohnehin gegen die Stubs end-to-end geprüft; es fehlte der Zugang, nicht die
   Verdrahtung. Das Generator-Briefing kennt
   die beiden harten Grenzen inzwischen — vorher lief es gegen eine Wand, die
   es nicht sah.

   Nachsehen statt annehmen: `env | grep NVIDIA_API_KEY`, und
   `uv run qt alloc ... --stub` nennt in der Telemetriezeile die Zahl der
   Aufrufe, die ein echter Lauf kosten würde (derzeit 20).
3. **Jeder Kandidat durchläuft die volle Kette, in dieser Reihenfolge:**
   Sandbox → Kritik → Walk-Forward → DSR → `qt placebo shuffle` →
   `qt placebo cross`. Die Kontrollen stehen als Befehl bereit (ADR-054) und
   decken seit ADR-059 auch pfadabhängige und Querschnittsstrategien ab —
   `shuffle` wählt die passende Fassung selbst, `cross` verweigert die
   unpassende. Es gibt keinen Grund mehr, sie ans Ende zu schieben.

### Gate 1 — 2027-03-01

**Bestanden**, wenn ein Kandidat *alle* erfüllt:

| | Kriterium |
|---|---|
| OOS-Sharpe | ≥ **0,33** — die aus n_eff 5,1 abgeleitete Schwelle (ADR-061) |
| DSR | ≥ 0,95 gegen den dann gültigen Versuchszähler |
| Permutation | Perzentil ≥ 95 % (`qt placebo shuffle`) |
| Querschnitt | Median-Sharpe > 0 über die erweiterten Märkte |
| Anlageklassen | wirkt in mindestens zwei, nicht nur in Krypto |
| Umschlag | ≤ 7× Eigenkapital pro Jahr, unter `coinbase_taker` (ADR-056) |
| Aktivität | ≥ 20 Ausführungen — was nicht handelt, ist nicht prüfbar (ADR-057) |

Prüfbar in einem Befehl: `uv run qt gate --strategy <name> --tf 1d`. Exit 0
nur, wenn jede Zeile steht.

Die letzte Zeile ist neu und die schärfste: ein Effekt, der nur in einer
Anlageklasse auftritt, ist wahrscheinlich deren Beta und nicht dein Edge.

### Phase D — Paper-Forward *(6 Monate, läuft nebenher)*

Der Kandidat aus Gate 1 auf Paper, über die bestehende Routine. **Kein Gate,
sondern eine Sicherung:** bei realistischer Trade-Frequenz reichen sechs Monate
für keinen Beweis, aber vollkommen für den Nachweis, dass Verdrahtung, Kosten
und Ausführung sich verhalten wie im Backtest. Divergenz hier ist ein Stopp.

### Phase E — Live mit Minimalkapital

~~`broker_ccxt`, `reconcile`, Positionsgrößen, harte Limits.~~ **Gebaut am
2026-09-03 (ADR-062)** — mit zwei unabhängigen Schaltern, ohne die nichts
gesendet wird, und ohne einen Befehl, der den lokalen Zustand an den
Börsenstand angleicht.

Startkapital so klein, dass ein Totalverlust folgenlos ist. Ab dem ersten
Fill läuft die Uhr für das Ziel oben.

**Der erste Fill setzt Gate 1 voraus.** `qt live tick` beendet sich bis dahin
mit Exit 1 und nennt den Grund. Wer scharf schalten will, braucht
`scharf=True` im Aufruf **und** `QT_LIVE_SCHARF=ja` in der Umgebung — beides
gleichzeitig passiert nicht aus Versehen.

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
Versuchszähler steht bei **21** und vergisst nichts (ADR-032, ADR-057).
Nachsehen statt erinnern: `uv run qt trials`.

---

## Der ehrliche Erwartungswert

Neun Hypothesen, neun gescheitert, dazu fünf Loop-Kandidaten, die den
Zähler kosteten und nur die Verdrahtung geprüft haben. Nichts im Repo hat je eine
Negativkontrolle bestanden — und seit ADR-059/064 ist das keine Beobachtung
über drei Strategien mehr, sondern über **alle neun**: `macross`, `trend`,
`meanrev`, `timesfm`, `crossmom`, `crossrev` und `orderflow` liegen zwischen
Perzentil 38 % und 76 % ihrer eigenen gewürfelten Fassungen, gefordert waren
95 %; `elliott` und `hashribbon` sind schon vorher gefallen. Es gibt keine
ungeprüfte Strategie mehr. Es gibt keinen Grund anzunehmen, dass eine
verbreiterte Datenbasis daran etwas ändert — sie macht nur die *Frage*
entscheidbar, die bisher offenbleiben musste.

Der Wert dieses Plans liegt nicht darin, dass er zum Ziel führt. Er liegt
darin, dass er in sechs Monaten eine **belastbare Antwort** liefert statt einer
achten unentscheidbaren Messung.
