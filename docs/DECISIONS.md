# Entscheidungs-Log (ADR)

Kurze Begründungen, damit nichts im Kopf gehalten werden muss.
Neueste zuerst. Format: Entscheidung — Warum — Konsequenz.

---

## ADR-056 — Das Kostenregime entscheidet nur unter 1d, und der Default war zu billig
**Datum:** 2026-09-02

**Die Entscheidung:** Ab Phase C wird unter **`coinbase_taker`** gesucht — 60 bps
Gebühr, 2 bps halber Spread, 3 bps Slippage, zusammen **130 bps Round-Trip**.
Kein Maker-Szenario, keine günstigere Börse, keine Hoffnung auf eine
Volumenstufe. Dazu ein vorab festgelegtes **Umschlagbudget**, siehe unten.

Phase B sollte laut ZIEL.md klären, ob billigere Ausführung den Suchraum
öffnet. Sie hat drei Dinge gefunden, und nur eines davon war die erwartete
Frage.

---

### 1. Der Gebührensatz im Code war die falsche Zeile der Staffel

`CostConfig.taker_fee_bps` stand seit Phase 1 auf **40.0**, mit dem Kommentar
„Coinbase ~40bps". Nachgelesen am 2026-09-02:

| Börse | Stufe | Maker | Taker |
|---|---|---|---|
| Coinbase Advanced | < 10k USD / 30 T | 0,40 % | **0,60 %** |
| Coinbase Advanced | 10k–50k USD | 0,25 % | 0,40 % |
| Kraken Pro | Eingangsstufe | 0,40 % | **0,80 %** |

40 bps ist der Taker-Satz der **zweiten** Stufe. Ein Konto am ersten Tag steht
in der ersten und zahlt 60. Der Kommentar war nicht falsch abgeschrieben — er
zeigte auf die falsche Zeile, und das ist die Sorte Fehler, die kein Test
findet, weil beide Zahlen im Gebührenplan stehen.

Der Default steht jetzt auf 60 bps. **Das macht jede bisher gemessene Zahl
schlechter, nicht besser** — der Round-Trip steigt von 90 auf 130 bps.

**Zur Belastbarkeit der Quellen, weil sie ungleich ist.** Kraken und Alpaca
sind Primärquellen (`kraken.com/features/fee-schedule`,
`files.alpaca.markets/disclosures/BrokFeeSched.pdf`, beide abgerufen
2026-09-02). Coinbase antwortet aus dieser Umgebung mit HTTP 403; die 0,40/0,60
stammen aus **drei unabhängigen Sekundärquellen**, die übereinstimmen. Das ist
schwächer, und es steht so im Code.

Ein Nebenbefund, der die Regel begründet: für Kraken nennen mehrere aktuelle
Zusammenstellungen 0,16 %/0,26 %. Das ist die Staffel **vor** Krakens Umstellung
im Juli 2026. Eine Sekundärquelle ohne Datum ist wertlos, auch wenn sie von
2026 ist.

---

### 2. ADR-009s Kernaussage gilt nur unter 1d — und wurde überall zitiert, als gälte sie allgemein

ADR-009 maß `trend` auf BTC/USD **4h** und schloss: die Kostenannahme leistet
mehr als jede Strategieentscheidung. Der Satz ist seither in `ZIEL.md`, in
`config.py` und in mehreren ADRs als allgemeine Wahrheit weitergereicht worden.

`qt costs` zieht die Tabelle jetzt für jede registrierte Strategie nach. Zuerst
die Gegenprobe, dass der alte Befund reproduziert — er tut es **exakt**:

**BTC/USD, 4h** (Endkapital als Faktor, dahinter Sharpe):

| Strategie | ohne Kosten | adr009_maker (16 bps) | coinbase_taker (130 bps) |
|---|---|---|---|
| trend | 6,44× / 0,81 | 4,04× / 0,66 | 0,14× / −0,44 |
| macross | 9,44× / 0,92 | 6,53× / 0,80 | 0,47× / −0,03 |

6,44 und 4,04 sind die Zahlen aus ADR-009, auf die Stelle.

**Dieselben Strategien auf 1d:**

| Strategie | ohne Kosten | coinbase_taker (130 bps) | ΔSharpe |
|---|---|---|---|
| macross | 21,69× / 1,13 | 14,15× / **1,00** | −0,13 |
| trend | 3,96× / 0,63 | 1,85× / 0,40 | −0,23 |
| hashribbon | 10,15× / 0,83 | 7,18× / 0,75 | −0,08 |
| elliott | 1,04× / 0,28 | 0,53× / 0,12 | −0,16 |
| meanrev | 0,06× / −0,54 | 0,03× / −0,72 | −0,18 |

**Auf 4h kostet die Ausführung 0,94 bis 1,26 Sharpe. Auf 1d kostet sie 0,08 bis
0,23.** Das ist kein gradueller Unterschied, das ist ein anderer Sachverhalt.

Das deckt sich mit ADR-047 („unter 1d entscheidet die Frequenz, über 1d
entscheidet nichts mehr") — dies ist dessen Kostenseite.

---

### 3. Warum, in einer Formel — und damit ein Budget statt einer Meinung

Der Mechanismus ist nicht „weniger Trades". `macross` schlägt auf 1d mehr
Gegenwert um als auf 4h. Was zählt, ist der Umschlag **relativ zum jeweiligen
Eigenkapital**:

> **Kostendrag p. a. ≈ (Umschlag / Eigenkapital / Jahr) × (einfache Kosten in bps)**
>
> **ΔSharpe ≈ − Kostendrag / annualisierte Volatilität**

Gegengerechnet auf BTC/USD bei 65 bps je Ausführung:

| Strategie | TF | Umschlag/EK/Jahr | vorhergesagt | gemessen |
|---|---|---|---|---|
| trend | 4h | 77,4 | 50,3 % | 50,3 % |
| macross | 4h | 61,0 | 39,7 % | 43,7 % |
| trend | 1d | 15,4 | 10,0 % | 11,3 % |
| macross | 1d | 8,8 | 5,7 % | 8,1 % |

**Grenze der Formel:** sie überschätzt, wenn das Konto zusammenbricht — bei
`meanrev` auf 4h sagt sie 53,6 % und misst 26,8 %, weil ein schrumpfendes Konto
immer kleinere Positionen handelt. Für einen Kandidaten, der sein Kapital
hält, stimmt sie auf wenige Prozentpunkte.

**Daraus das Budget, vorab und in Zahlen.** Gate 1 verlangt Sharpe ≥ 0,41
(ADR-055). Die Ausführung darf davon höchstens **0,10 Sharpe** fressen. Bei
44 % Jahresvolatilität sind das 4,4 % Drag, bei 65 bps je Ausführung:

> **Ein Kandidat in Phase C darf höchstens rund 7× sein Eigenkapital pro Jahr
> umschlagen.** Darüber wird er nicht gescreent — nicht weil er schlecht wäre,
> sondern weil er die Kosten nicht tragen kann, die er nachweislich zahlt.

`macross` auf 1d liegt bei 8,8 und damit knapp darüber. Auf 4h bei 61.

---

### 4. Die Füllquote widerlegt Maker nicht — und die Messung ist trotzdem wenig wert

Das Maker-Regime setzt voraus, dass eine passive Limit-Order gefüllt wird.
`qt maker` (`qt.backtest.maker`) rechnet das je vorgemerkter Order nach: Limit
beim Signalpreis, dann drei Fälle im Ausführungsbar.

`macross`, BTC/USD:

| TF | Orders | marktnah | passiv gefüllt | nie gefüllt |
|---|---|---|---|---|
| 1h | 1.746 | 62 % | 37 % | **1 %** |
| 4h | 461 | 63 % | 36 % | **1 %** |
| 1d | 67 | 67 % | 33 % | **0 %** |

Über drei Zeitebenen stabil: der Kurs kommt fast immer zurück. Der Einwand
„Trendfolge kauft in die Stärke, das Limit wird nie erreicht" ist damit **nicht
bestätigt**.

**Trotzdem trägt die Zahl die Entscheidung nicht.** Das Modell zählt jede
Berührung des Limits als vollen Fill. Genau das ist die Annahme, die bei einer
ruhenden Order an einem kurz angetippten Kurs bricht — vor uns steht die
Warteschlange. Die 0 bis 1 % Ausfall sind ein direktes Produkt dieser Annahme,
nicht ein Ergebnis daneben. Was hier entscheiden würde, ist die
Warteschlangenposition, und die steht in Tages-OHLC nicht drin.

Also: **Maker bleibt eine Aufwärtsmöglichkeit, kein Suchszenario.** Geprüft
wird sie in Phase D/E an echten Fills, wo die Antwort direkt abzulesen ist.

---

### 5. Der Spread ließ sich nicht messen, und der Versuch steht als Warnung im Repo

Der halbe Spread stand als blanke Zahl im Code. Der Versuch, ihn aus Tages-OHLC
zu schätzen — Corwin/Schultz (2012) und Abdi/Ranaldo (2017) —, ist
**gescheitert**. `qt.backtest.spread` bleibt als offen markierter Fehlschlag
liegen, wie `timesfm` und `elliott` (ADR-022, ADR-033).

Zwei Gründe, und der erste ist der wichtigere:

**Die erste Fassung meldete 47 bps für eine simulierte Reihe mit einem Spread
von exakt null.** Beide Formeln stehen in ihren Papieren unter einem
Erwartungswert; ich hatte sie je Tagespaar ausgewertet und die Ergebnisse
hinterher gemittelt. 42 % der Paarschätzungen waren negativ, wurden auf 0
geklemmt, und der Median der übrigen lag hoch. Erst β und γ zu mitteln ergibt
−16 bps, also null im Rauschen. **An echten Kursen hätte die Zahl plausibel
ausgesehen und wäre in dieses ADR gewandert.** Aufgefallen ist es nur an einer
Simulation, in der die Antwort bekannt war; sie steht deshalb als Test im Repo
(`tests/test_spread.py`).

**Auch repariert taugt die Methode hier nicht.** Gegen bekannte Wahrheit liegt
Corwin/Schultz systematisch zu tief und klemmt bei 0, Abdi/Ranaldo zu hoch, und
zwar um rund 0,14 × Tagesvolatilität — bei 4 % Tagesvol und Spread 0 meldet er
55 bps. In der Simulation klammern die beiden den wahren Wert ein. Auf echten
Kursen nicht: über die 28 Reihen im Store liegt CS in **10 Fällen über AR**, was
in der Simulation nie vorkommt. Und der Fall mit bekannter Antwort geht daneben
— BTC/USD auf Coinbase handelt mit einer Spanne im Bereich eines Basispunkts,
geschätzt werden 23,3 und 45,9.

Ursache: beide Verfahren setzen konstante Volatilität im Zweitagesfenster
voraus. Volatilitätsclusterung und Sprünge schreiben sie dem Spread zu.

**Konsequenz:** `half_spread_bps` und `slippage_bps` sind **Annahmen** und im
Feld-Text jetzt als solche gekennzeichnet, statt als Zahlen dazustehen, die
nach Messung aussehen. Aus Tages-OHLC sind sie nicht zu holen.

---

### Konsequenzen

- **Gesucht wird unter `coinbase_taker`**, auf **1d oder gröber**, mit einem
  Umschlagbudget von **≈ 7× Eigenkapital pro Jahr**. Alle drei sind vorab
  festgelegt und wandern in Gate 1.
- **Die Hoffnung aus ZIEL.md §2 wird nicht gebraucht.** „Billigere Ausführung
  öffnet den Suchraum" gilt unter 1d; dort wird nicht gesucht. Auf 1d kostet
  die pessimistischste Annahme rund 0,13 Sharpe, und das ist bezahlbar.
- **Der Versuchszähler bleibt bei 8.** Hier wurde kein Kandidat gescreent
  (ADR-032).
- **Neue Befehle:** `qt costs` (Sensitivität je Strategie und Regime),
  `qt maker` (Füllquote passiver Orders), `qt backtest --costs <regime>`.
- **Was offen bleibt und offen heißt:** der reale Spread, die
  Warteschlangenposition, und ob Coinbases 0,60 % stimmen. Alle drei sind erst
  mit echten Fills oder Orderbuchdaten zu klären — also in Phase E, nicht durch
  längeres Nachdenken.

---

### Nachtrag am selben Tag: `costs_by_symbol` gab es, aber niemand füllte es

Beim Nachziehen der ROADMAP-Tabelle fiel ein zweiter Verdrahtungsfehler auf.
`BacktestConfig.costs_by_symbol` entstand in Phase A (ADR-055) genau dafür,
dass ein US-ETF nicht den Krypto-Taker zahlt — **aber keine einzige Aufrufstelle
hat es je gefüllt.** `qt placebo cross` baute seine Konfiguration ohne, und
damit standen die ETF-Zellen in ADR-055 unter **130 statt 5,2 bps**
Round-Trip, dem 25-fachen.

Ein Feld, das existiert und leer bleibt, ist schlimmer als keines: es sieht im
Code aus wie eine gelöste Frage.

Verdrahtet über `qt.core.config.costs_for_symbols`. Der Querschnitt danach:

| | vorher (ETFs auf Krypto-Tarif) | nachher |
|---|---|---|
| Median-Sharpe über 26 Märkte | +0,03 | **+0,15** |
| davon positiv | — | 69 % |
| mittlere Paarkorrelation | 0,26 | **0,26** |
| effektive Märkte | 3,4 | **3,4** |

**Das Kriterium bleibt verfehlt.** Und das ist der Punkt, an dem die Korrektur
lehrreich ist: sie hat den Median vervierfacht und an der Zahl, auf die es
ankommt, **nichts** geändert. `n_eff` misst die Korrelationsstruktur, nicht das
Niveau — ein Kostenfehler verschiebt alle Reihen ähnlich und lässt ihre
Korrelation in Ruhe. Gate 1 bleibt bei Mindest-Sharpe **0,41**.

Die vier besten Zellen sind jetzt GLD 1,45, QQQ 1,25, SLV 1,23, SPY 1,20. Das
bleibt, was es in ADR-055 schon war: **kein Ergebnis.** Vier von 26 Zellen,
drei OOS-Fenster statt sieben, ohne Permutationskontrolle, ohne DSR. Ein
Kandidat für Phase C, nicht mehr.

---

## ADR-055 — Vier Anlageklassen: Kriterium verfehlt, und die Ausrede war falsch
**Datum:** 2026-09-02

Phase A aus `docs/ZIEL.md`: die Datenbasis von 13 Krypto-Paaren auf vier
Anlageklassen erweitern, damit schwächere Effekte überhaupt beweisbar werden.
Abnahme war **vorab** festgelegt: `qt placebo cross` meldet n_eff ≥ 4.

### Das Ergebnis: 3,4. Kriterium verfehlt.

13 US-ETFs von Tiingo, je 1.927 Bars über dieselben 7,7 Jahre — SPY QQQ EFA
EEM, TLT IEF, GLD SLV DBC USO, UUP FXE FXY. Die mittlere paarweise Korrelation
über alle 26 Märkte fällt von 0,67 auf **0,26**, n_eff steigt von 1,4 auf
**3,4**. Die Schwelle war 4.

### Was ich dann getan habe, und warum es der eigentliche Eintrag ist

Nach dem Fehlschlag habe ich die Struktur aufgeschlüsselt und einen Verdacht
gefasst:

| | mittlere Korrelation |
|---|---|
| innerhalb Krypto (13) | 0,700 |
| innerhalb ETFs (13) | 0,160 |
| Krypto gegen ETFs | 0,143 |

Zwei Blöcke mit 0,70 und 0,16 sind offensichtlich keine Gleichkorrelation. Die
Formel `n_eff = n / (1 + (n−1)·ρ̄)` sieht nach einer Näherung *für* gleich
korrelierte Reihen aus — also müsste sie hier falsch liegen. Der
Eigenwert-Schätzer sagte 4,8, und 4,8 ≥ 4.

**Die Vermutung war falsch, und ich hatte sie nur, weil die Zahl das Kriterium
verfehlte.** Nachgerechnet:

```
n/(1+(n-1)·rho)   3.2412
n² / 1'C1         3.2412      Differenz 4.4e-16
```

Die Formel ist keine Näherung. Sie **ist** `n²/(1ᵀC1)` — die Varianz eines
gleichgewichteten Mittels ist `(1/n²)·1ᵀC1`, und mit `1ᵀC1 = n + n(n−1)·ρ̄`
fällt sie unmittelbar heraus. Sie gilt exakt, für jede Korrelationsstruktur,
auch für Blöcke.

Die Teilnahmequote der Eigenwerte misst etwas anderes: wieviele unabhängige
**Richtungen** die Märkte aufspannen, nicht wieviele unabhängige
**Beobachtungen** ein Mittel über sie wert ist. Für die Frage „wieviel Evidenz
habe ich" ist die zweite Größe die richtige.

Damit war der Schritt, den ich beinahe gegangen wäre — Kriterium verfehlt,
Schätzer getauscht, Kriterium erfüllt — keine Methodenkorrektur, sondern eine
Zielverschiebung mit besserer Begründung. Genau die Bewegung, gegen die
ADR-005, ADR-032 und ADR-048 gebaut sind, und sie kam von innen.

**Phase A ist verfehlt. Punkt.** Für Gate 1 gilt:

> Mindest-Sharpe: **0,41** (aus n_eff 3,4, 7,7 Jahre, t ≥ 2).

Der Eigenwert-Schätzer bleibt im Code, aber unter dem Namen
`unabhaengige_richtungen`, ausdrücklich als Beschreibung der Struktur und
ausdrücklich **nicht** als Messlatte. Wer ihn dort künftig sucht, sucht
vermutlich aus demselben Grund wie ich.

### Was der Befund trotzdem wert ist

Die Richtung ist eindeutig und groß: von 1,4 auf 3,4 unabhängige Märkte senkt
die beweisbare Schwelle von **0,67 auf 0,41**. Der Abstand zu `macross` (0,31)
schrumpft von Faktor 2,2 auf 1,3. Das Ziel aus `ZIEL.md` ist damit erstmals in
Reichweite — erreicht ist es nicht.

Um von 3,4 auf 4 zu kommen, fehlt eine Anlageklasse, die mit keiner der
vorhandenen läuft: Volatilität, Zinsdifferenzen, Einzelaktien außerhalb der
Indizes. Oder mehr Kalenderzeit, die niemand beschleunigen kann.

Nebenbefund, ungeprüft und deshalb nur notiert: `macross` liefert auf SLV
(1,11), QQQ (1,04) und SPY (0,93) OOS-Sharpes weit über allem, was Krypto je
gezeigt hat. Das ist **kein Ergebnis** — drei von 26 Zellen, ohne
Negativkontrolle, ohne DSR, mit drei OOS-Fenstern statt sieben. Es ist der
erste Kandidat für Phase C, nicht mehr. Wer daraus jetzt eine Strategie macht,
hat die Lehre dieses Eintrags nicht gelesen.

### Drei Fallen, vor dem ersten Backtest beseitigt

Alle drei von derselben Art wie ADR-053: falsch, ohne dass etwas fehlschlägt.

**1 — Die Annualisierung war eine Konstante.** `bars_per_year(tf)` unterstellt
24/7. Ein Aktien-ETF handelt an rund 252 statt 365 Tagen; jeder annualisierte
Sharpe wäre um Faktor **1,20** zu hoch gewesen. Behoben, indem `metrics` die
Bar-Dichte aus der **Zeitachse der Reihe misst** statt sie aus dem Timeframe zu
schließen. Für Krypto ändert das nichts (BTC 1d: 365,2 gegen angenommene
365,25), für gelückte Reihen ist es sogar richtiger.

**2 — Wochenenden waren Lücken.** `find_gaps` hätte für jeden ETF rund 400
Lücken im Jahr gemeldet; eine Warnung, die man überliest, ist keine. Neu ist
ein `calendar`-Modus je Reihe. Die **Abdeckung** wird im Börsenmodus gar nicht
mehr ausgewiesen: ohne echten Handelskalender wäre jede Prozentzahl geraten,
und eine geratene Zahl in einer Integritätsprüfung ist schlimmer als keine.

**3 — Das Kostenmodell kannte keine Symbole.** Ein `CostConfig` je Lauf, kein
Symbolargument in `FillModel.fill`. Neu: `BacktestConfig.costs_by_symbol`,
Default unverändert. Gemessen: BTC/USD 90 bps, SPY 5 bps im selben Lauf.

### Adjustierung ist retroaktiv, und das bricht eine Zusage

Übernommen werden `adjOpen/adjHigh/adjLow/adjClose` — nicht nur `adjClose`:
`trend` rechnet Donchian auf Hochs und Tiefs, `elliott` setzt Pivots darauf.
Eine Reihe mit adjustiertem Schluss und rohen Extremen wäre für die halbe
Bibliothek unbrauchbar, und der Fehler verschiebt nur Signale statt
aufzufallen.

Jede Dividende schreibt die Vergangenheit der Reihe neu. Eine heute gezogene
Reihe ist damit eine *andere* als dieselbe vor einem Jahr — ein Backtest darauf
ist nicht reproduzierbar, ohne dass etwas fehlschlägt. Deshalb liegt neben
jeder Reihe eine Meta-Datei mit Quelle, Abrufdatum und Adjustierungsflag, und
deshalb schreibt der Abzug mit `replace=True`: eine adjustierte Reihe ist eine
Funktion, kein Zuwachs.

### Quelle und ihre Bedingungen

Tiingo, freier Tarif: 1.000 Aufrufe am Tag, 500 Symbole im Monat, adjustierte
OHLC über 30+ Jahre. **Ausdrücklich auf private Nutzung beschränkt.** Für ein
Repo mit gitignorierten Daten passt das; wer davon etwas veröffentlicht,
braucht den kommerziellen Tarif. `yfinance` wurde als Primärquelle verworfen:
ein gescrapter, inoffizieller Endpunkt, der ohne Vorwarnung bricht, ist für
ein Projekt, dessen Wert Reproduzierbarkeit ist, das falsche Fundament.

---

## ADR-054 — `macross` ist von seiner eigenen Wuerfelfassung nicht zu unterscheiden
**Datum:** 2026-09-02

Sechs Hypothesen geprüft, sechs verworfen — und in drei Fällen entschied
**diese** Art Test: `hashribbon` fiel, weil eine von fünf permutierten
Hashraten die echte Reihe schlug (ADR-048), das ML-Modell an vertauschten
Labels (ADR-050), `elliott` bekam den Placebo nachträglich (ADR-033).

`macross` — die einzige überlebende Strategie, seit ADR-037 auf zwei
Paper-Konten — hatte nie eine. Sie hatte ein Parameterfeld und eine
Replikation auf ETH. Beides beantwortet die eigentliche Frage nicht:

> Die Strategie ist 54 % der Zeit long in einem Markt, der Faktor 16 gemacht
> hat. Trägt das **Timing** etwas bei, oder misst der Sharpe „viel long im
> Bullenmarkt"?

Buy-and-Hold beantwortet das nicht — das ist 100 % Zeit im Markt.

### Die Kontrolle

Der echte Gewichtsverlauf wird in Läufe zerlegt; innerhalb jeder
Gewichtsklasse werden die **Längen** getauscht. Erhalten bleiben Episodenzahl,
Zeit im Markt, Abwechslungsmuster — und damit Trade-Zahl und Gebühren.
Zufällig wird ausschließlich, **wann** die Episoden liegen.

Gemessen, nicht unterstellt:

| | echt | 200 Ziehungen |
|---|---|---|
| Trades | 47 | Median 45 (35–54) |
| Umsatz | 4.807.710 | Median 4.816.397 |

Die gewürfelten Fassungen zahlen dieselbe Reibung. Kein Vorteil durch weniger
Handeln.

**Die Kalibrierprobe ist Teil der Kontrolle.** Der Abspieler mit den *echten*
Gewichten muss dieselbe Kennzahl liefern wie die Strategie. Abweichung
gemessen: **0,0000** — bitidentisch, in beiden Märkten. Ohne diese Probe
verglichen die Ziehungen zwei verschiedene Dinge, und niemand hätte es
gesehen.

### Das Ergebnis, Kriterium vorab festgelegt

Gefordert war: die echte Strategie im obersten **5 %** ihrer Permutationen.

| | echter Sharpe | Median der Ziehungen | p95 | Perzentil |
|---|---|---|---|---|
| BTC/USD | +0,308 | +0,098 | +0,635 | **74,3 %** |
| ETH/USD | +0,321 | +0,024 | +0,536 | **82,1 %** |

Je 1000 Ziehungen. **257 bzw. 179 von 1000 Zufallsplatzierungen waren
mindestens so gut wie die echte Strategie.** Das Kriterium ist nicht knapp
verfehlt, sondern deutlich.

### Der Querschnitt, dieselbe Strategie auf 13 Märkten

Unverändert 10/50, kein Parameter je Markt neu gewählt:

| | |
|---|---|
| Median-Sharpe | **+0,14** |
| davon positiv | 69 % |
| Spanne | −1,17 (DOT) bis +0,48 (XLM) |
| mittlere paarweise Korrelation | 0,67 |
| **effektive Marktzahl** | **1,4**, nicht 13 |

Besteht dem Buchstaben nach (Median > 0). Aber zwei Dinge relativieren das
sofort: bei einer Korrelation von 0,67 sind dreizehn Krypto-Märkte nach
ADR-052 gut *ein* unabhängiger Test — und **BTC und ETH liegen auf Platz 3
und 4 von 13**, mit gut dem doppelten Median-Sharpe. Genau das erwartet man,
wenn zwei Märkte ausgewählt wurden.

### Was daraus folgt — und was nicht

**Nicht:** „`macross` funktioniert nicht." Die Strategie liegt in beiden
Märkten **über** dem Median ihrer Ziehungen (0,31 gegen 0,10; 0,32 gegen
0,02) und über dem Querschnitts-Median. Es ist ein Hinweis, nur keiner, der
die Schwelle dieses Projekts hält.

**Sondern:** die Aussage von ADR-035 war zu stark. Dort steht „die erste
Strategie, die Geld verdient" und im ROADMAP „der einzige Kandidat mit echter
Evidenz". Richtig ist: **der einzige Kandidat, dessen Kennzahl positiv ist
und der keine Kontrolle bestanden hat.** ADR-035 sagte das im DSR-Absatz
bereits selbst („man kann einen Sharpe von 0,6 mit 4,8 Jahren Tagesdaten
nicht beweisen"); diese Kontrolle sagt dasselbe aus einer zweiten Richtung
und macht es konkret.

**Grenze des Tests, ausdrücklich:** bei 47 Round-Trips hat er wenig
Trennschärfe. Ein Perzentil von 74 % heißt „nicht gezeigt", nicht „gezeigt,
dass nichts da ist". Eine echte, aber schwache Kante würde hier genauso
durchfallen.

### Konsequenz für die Paper-Konten: sie laufen weiter

Das ist kein Widerspruch, sondern der Grund, warum es sie gibt. Die
historischen Daten können die fehlenden unabhängigen Beobachtungen nicht
liefern — Vorwärtszeit kann es, und nur sie. Was sich ändert, ist der
Anspruch: die Konten prüfen nicht nach, ob eine belegte Strategie hält. Sie
sammeln die Evidenz, die noch fehlt, bei einem Risikoprofil (long/flach, vier
Trades im Jahr, kein Hebel), das den Irrtum billig macht.

Was ausdrücklich **nicht** passiert: Parameter nachjustieren, weil das
Ergebnis dünn ist. Jede Nachjustierung wäre ein weiterer Versuch auf
denselben Daten und träfe denselben Nenner (ADR-005).

---

## ADR-053 — Ein vollständiger Audit: was zwischen den Tests durchfiel
**Datum:** 2026-09-02

806 Tests grün, ruff sauber — und trotzdem handelte das Paper-Konto eine
andere Strategie als die, gegen die es verglichen wurde. Dieser ADR hält
fest, was ein Zeile-für-Zeile-Durchgang durch `src/` (16.315 Zeilen, 39
Module), die Tests und die drei Doku-Dateien gefunden hat, und was daraus
folgt.

**Die Klammer um alles:** kein einziger der schwerwiegenden Funde war ein
Absturz. Alle waren Zahlen, die falsch waren, ohne dass irgendetwas
fehlschlug — genau die Klasse, gegen die dieses Projekt seine ganze
Teststrategie richtet, und sie kam trotzdem durch.

---

### 1. Das Paper-Konto handelte ein Viertel der gemessenen Größe

`qt backtest` und `qt wf` — die Quelle **jeder** gemessenen Zahl des Projekts
— rufen überhaupt keine Risk-Engine auf. `run_paper_tick` rief sie mit den
Portfolio-Defaults auf. Gemessen bei BTCs Tagesvola von 0,44:

```
Strategie sagt 1.0  ->  Vol-Targeting 0,455  ->  Symbol-Cap  0,25
```

Das Vorwärtskonto prüfte also nicht die Strategie nach, deren OOS-Sharpe von
0,31 den ganzen Aufbau begründet. Nebenwirkung: der Kill-Switch bei 20%
Kontodrawdown hätte rund 80% Marktdrawdown gebraucht und konnte praktisch
nicht auslösen.

**Entscheidung: die Risk-Engine *sichert* immer, sie *formt* nur auf Ansage.**
`RiskConfig.vol_targeting` ist neu und im Paper-Tick per Default aus, ebenso
der Symbol-Cap; der Drawdown-Kill-Switch bleibt immer an. `qt paper run
--shape-risk` schaltet die Formung dazu, wenn man den Portfolio-Pfad
nachstellen will.

Der Zeitpunkt war Glück: beide Konten standen flach und ohne einen einzigen
Fill. Eine Woche später hätte die Korrektur eine Historie entwertet.

### 2. Die Risk-Engine sah im Tick immer nur ein Symbol

`risk.apply({bar.symbol: target}, ...)` — ein Symbol je Bar.
`max_gross_exposure` ist aber eine Grenze für das Konto als Ganzes; so
geprüft hätten *n* Symbole in Summe das *n*-fache durchgelassen. Heute
harmlos (ein Symbol je Konto), aber `--symbols BTC/USD,ETH/USD` ist erlaubt.
Der Tick übergibt jetzt alle Symbole auf einmal.

### 3. `qt data resample` zerstörte, statt zu reparieren

Die sechs abgeleiteten Dateien im Store (`2d`/`3d`/`1w` für BTC und ETH) lagen
auf dem `start_day`-Raster (2019-01-01, 01-03, …); der Code schreibt
`origin="epoch"` vor und erzeugt 2019-01-02, 01-04, … — **kein einziger
gemeinsamer Zeitstempel.** Der Befehl, der das hätte richten sollen, machte es
schlimmer, weil `write_bars` additiv vereinigt:

```
vorher  1.396 Bars (2d)
nachher 2.796 Bars, Abstände: 2.791× ein Tag, 4× zwei Tage
qt data report:  ok  BTC/USD 2d  Abdeckung 100.00%  Luecken 0
```

**Zwei Ursachen, und die zweite ist die interessantere.** `resample_store`
vereinigte, wo es hätte ersetzen müssen — eine abgeleitete Reihe ist eine
Funktion ihrer Quelle, kein Zuwachs. Und `qt.data.integrity` konnte es nicht
sehen: `find_gaps` sucht ausschließlich nach Abständen, die zu **groß** sind.
Ein Prüfer, der nur in eine Richtung schaut, übersieht die andere zuverlässig.

Behoben: `write_bars(..., replace=True)` für abgeleitete Reihen, plus eine
fünfte Fehlerklasse `too_fine`. Die sechs Dateien sind neu erzeugt; ADR-047 ist
auf dem korrigierten Raster nachgerechnet und hält (Abweichung ≤ 0,03 Sharpe).

### 4. Der Tagesreport zeigte die Kostenbasis und nannte sie „Preise"

`equity = cash + sum(qty * avg_price)` unter der Überschrift „Eigenkapital
(letzte bekannte Preise)". Eine verdoppelte Position bewegte die Zahl nicht —
und neben dem korrekt geführten Höchststand sah das aus wie ein Drawdown, den
es nicht gab. Das ist die eine Zahl, die ein Mensch täglich liest.

`qt paper status` liest jetzt die letzten Schlusskurse aus dem Store. Fehlen
sie, weist der Report **kein** Eigenkapital aus, sondern sagt warum. Lieber
keine Zahl als eine, die etwas anderes misst als ihr Etikett.

### 5. Der Broker vergab beim Short aus flach keinen Einstandspreis

Mit `position.qty == 0` und `qty < 0` waren beide Vorzeichenvergleiche falsch,
der Zweig griff nicht, `avg_price` blieb auf 0,0. Long war korrekt, Short
nicht — und das Projekt handelt long-only, der Fehler konnte also beliebig
lange leben. Sichtbar geworden wäre er im Tagesreport, in der Zustandsdatei
und im Rückfallpfad von `equity()`.

### 6. Der wichtigste Test deckte die wichtigsten Strategien nicht ab

`test_future_data_cannot_change_the_past` lief über `["trend", "meanrev"]` —
ausgerechnet die beiden Strategien, die das Projekt als unbrauchbar verworfen
hat. `macross` (läuft live) und `elliott` (Grundlage von ADR-047) waren nicht
dabei. Beide sind jetzt drin und beide sauber; der Wert war die Abdeckung,
nicht ein gefangener Bug.

### 7. Zwei stille Verschlechterungen

**`hashribbon` hat in den letzten 400 Bars zu 19,5% keine Meinung.** Elf
fehlende Tage in der Hashrate-Reihe, und jeder blendet ein ganzes
`slow + LAG_BARS`-Fenster aus: 78 von 400 Bars. Die Reihe endet 2026-08-31,
die Bars laufen bis 09-01 — auf dem neuesten Bar liefert die Strategie `nan`.
Live würde sie ohne frisches `qt data onchain` nie ein Signal geben. Das
Verhalten ist richtig (`nan` heißt „keine Meinung"), die Häufigkeit war
ungemessen.

**Der Order-Flow-Lückenwächter maß nur *innerhalb* eines Buckets.**
`groupby("bucket")["ts"].diff()` gab dem ersten Trade eines Buckets `NaN`, das
zu 0,0 wurde. Ein Bar mit einem einzigen Trade nach 58 Minuten Stille meldete
`max_gap_s = 0.0` — perfekte Abdeckung, ausgerechnet für den Fall, den die
Kennzahl fangen soll.

### 8. Die Sandbox: ein Timeout, der nichts beendet

`probe` startet einen Thread und wartet 5 Sekunden. Python-Threads lassen sich
nicht töten — ein hängender Kandidat rechnet bis zum Prozessende weiter.
Schleifen sind verboten, aber `sum(range(10**12))` lief trotzdem: unbegrenzt
und ohne Speicherbedarf. `range` und `enumerate` sind ohne Schleifen ohnehin
nutzlos und stehen jetzt nicht mehr in `SAFE_BUILTINS`; wer einen Indexvektor
braucht, nimmt `np.arange` (neu in der Fassade), und eine `np.arange`-Bombe
scheitert sofort an MemoryError statt zu spinnen.

### 9. Was die Doku behauptete und der Code nicht tat

| Behauptung | Wirklichkeit |
|---|---|
| Diagramm „eine Engine, drei Uhren" mit `PaperClock` | Existiert nicht. Es sind zwei Uhren, und der Paper-Tick benutzt `BacktestClock`. |
| „`qt.features` — … Regime-Features" | Gibt es nicht. |
| „Von `qt.live` fehlt nur der `CcxtBroker`" | `reconcile` fehlt auch — die ROADMAP sagte es richtig, ARCHITECTURE falsch. |
| NIM-Latenz „90–155 s" / „40 bis 110 Sekunden" | Zwei Zahlen für dieselbe Messung. ADR-040 ist die Quelle: 90–155 s. |
| „Der Tick ruft `risk.apply()` mit genau demselben Aufbau wie `portfolio_engine`" | Fund 1 und 2 in Prosa. |
| Ergebnistabellen | Nicht falsch, **undatiert** — `macross` stand als Faktor 15,2, war beim Nachrechnen 16,0. |

Der letzte Punkt ist der einzige strukturelle: jede Tabelle nennt jetzt ihren
Datenstand. Dieselbe Lehre wie ADR-051 und ADR-052, eine Ebene tiefer.

---

### Was dieser Audit über die Teststrategie sagt

**28 neue Tests, 14 davon fallen gegen den alten Code durch** — geprüft, indem
`src/` zurückgesetzt und die Testsuite dagegen laufen gelassen wurde. Die
übrigen 14 sind Abdeckung und gemessene Größen, kein gefangener Fehler; das ist
hier ausdrücklich unterschieden, weil ein Test, der nie rot war, leicht für
mehr gehalten wird, als er ist.

Zwei Muster, die sich wiederholen und die man vorher benennen kann:

1. **Ein Prüfer, der nur in eine Richtung schaut.** `find_gaps` suchte zu große
   Abstände und übersah zu kleine. `_update_position` prüfte zwei Vorzeichen
   und übersah die Kombination, in der beide falsch sind. Beides derselbe
   Fehler in verschiedenen Kleidern.
2. **Zwei Orte, die dasselbe festlegen.** Das Modell in `llm_allocator` neben
   dem in `config`, die NIM-Latenz im Kommentar neben der im ADR, die
   Risk-Konfiguration im Paper-Tick neben der im Backtest-Pfad. `qt.core.config`
   warnt in einem Kommentar genau davor — und war selbst betroffen.

**Was ausdrücklich offen bleibt:** der Probe-Timeout kann einen Thread weiterhin
nur melden, nicht beenden (echte Isolation bräuchte einen Subprozess). Die
Namensauflösung der Sandbox ist scope-frei — der Probelauf fängt das, ein
zweiter Namensauflöser wäre mehr Angriffsfläche als Nutzen. Und `fetch_ohlcv`
kann weiterhin die noch offene Kerze schreiben; der Paper-Pfad verwirft sie
(ADR-038), der Backtest-Pfad sieht ohnehin nur längst geschlossene Bars.

---

## ADR-052 — Zwei Paper-Konten sind 1,2 Konten, nicht 2 (und zwei eigene Fehler)
**Datum:** 2026-09-02

Auf die Frage nach dem nächsten Schritt habe ich den Zustand geprüft statt aus
dem Gedächtnis zu antworten. Dabei kamen drei Dinge heraus, zwei davon eigene
Fehler vom Vortag.

### Der Datenbestand war beschädigt, und zwar durch mich

Beim Kaltstart-Test (ADR-051) wurde `data/ohlcv/` beiseitegelegt und danach mit
`cp -rn` zurückgeholt. `-n` heisst **no-clobber**: der Kaltstart hatte
`BTC-USD/1d.parquet` bereits mit 112 frisch gezogenen Bars neu angelegt, und
die Wiederherstellung prallte wortlos daran ab.

```
BTC-USD/1d   129.119 B -> 8.920 B   (2.793 -> 112 Bars)
ETH-USD/1d   122.230 B -> 8.680 B   (2.788 -> 112 Bars)
```

Genau zwei Dateien, alles andere identisch. **Aufgefallen ist es nur, weil
eine Round-Trip-Auswertung 1 Trade statt 33 meldete.** Wäre die Zahl weniger
absurd gewesen, wäre auf 4% der Historie gerechnet und das Ergebnis geglaubt
worden. Kein Codefehler, sondern ein Shell-Flag — ein Test dafür wäre Theater.
Was bleibt: beim Wiederherstellen ist `cp -n` die falsche Wahl.

### Die ROADMAP widersprach sich selbst

Oben stand „Das Paper-Konto läuft", Punkt 1 der Liste darunter „`data/paper/`
ist leer … der einzige offene Punkt". Der neue Block war vorangestellt, ohne
den alten aufzulösen — **exakt die Fehlerform, die das Konto schon einmal
wochenlang unbemerkt stillstehen liess.**

**Konsequenz, strukturell statt kosmetisch:** die ROADMAP behauptet keinen
Laufzeitzustand mehr, sie nennt den Befehl, mit dem man ihn nachsieht. Eine
Behauptung veraltet still, ein Befehl nicht.

### Die geprüfte Behauptung: „repliziert auf ETH" — teilweise

ADR-035 führt BTC (OOS 0,31) und ETH (0,32) als Replikation ohne Neuanpassung,
und darauf wurden gestern zwei Paper-Konten als **zwei unabhängige
Vorwärtstests** gestartet. Vorab festgelegt war: liegen die Spitzengewinne im
selben Regime, gilt die Aussage als relativiert.

| | grösster Gewinn | zweitgrösster |
|---|---|---|
| BTC | **2020-10 bis 2021-04** (63%) | 2024-09 bis 2024-12 (42%) |
| ETH | 2025-07 bis 2025-09 (41%) | **2020-10 bis 2021-03** (38%) |

Bei beiden liegt ein Grossgewinner im **selben Fenster**, dem Bullenlauf
2020/21; der jeweils zweite liegt in unterschiedlichen. Die Equity-Kurven
korrelieren mit **0,65** auf Tagesbasis, stabil über alle Jahre (0,57–0,75).

**Damit sind zwei Konten rechnerisch 1,21 Konten**
(n_eff = 2/(1+ρ) = 2/1,65). Meine Formulierung „zwei unabhängige
Vorwärtstests kosten nichts und sind mehr wert als eine bessere Einzelzahl"
war zu grosszügig: sie sind **20% mehr wert**, nicht doppelt.

Ein Nebenbefund, der die Gewichte verschiebt: **ETH ist robuster als BTC.**
Ohne die zwei besten Trades bleibt ETH bei +436.109 (21% des Originals,
positiv), BTC fällt auf −63.557. Die Kopfzahl des Projekts steht auf dem
schwächeren der beiden Märkte.

**Konsequenz:** ADR-035s „repliziert auf ETH ohne Neuanpassung" bleibt
richtig, aber die Unabhängigkeit war überzeichnet. Beide Konten laufen weiter
— sie kosten nichts, und ab jetzt erzeugen sie *neue* Fenster statt geteilter
Vergangenheit. Genau das ist der Zweck des Vorwärtstests. Die Erwartung an die
Aussagekraft wird auf 1,2 Konten korrigiert, nicht auf 2.

---

## ADR-051 — Das Paper-Konto lief nicht, weil ein Tick 5 Bars zieht und 52 braucht
**Datum:** 2026-09-01

Fünf geprüfte Hypothesen, fünf gescheitert (ADR-045 bis ADR-050). Jede weitere
Suche auf denselben Daten hebt die DSR-Schwelle und hat eine Trefferquote von
0/5. **Die einzige Informationsquelle, die ein Backtest nicht liefern kann,
ist Vorwärtszeit** — und die ist uhrgebunden: Rechenzeit lässt sich aufholen,
Kalenderzeit nicht.

Dazu ein Befund aus der Round-Trip-Sicht (ADR-049): **ohne die zwei besten
Trades ist `macross` negativ** (−63.557 gegen +1.424.232). Die einzige positive
Zahl des Projekts steht auf zwei Beobachtungen.

### Der eigentliche Fehler war nicht die `.gitignore`

`qt.live.state` ist bereits containerbewusst entworfen — jeder Tick ist ein
vollständiger deterministischer Replay, überleben muss nur der Broker-Zustand
plus ein Cursor, als kleine JSON-Datei. Insofern war die naheliegende Diagnose
(„`/data/` ist gitignored") nur die halbe Wahrheit.

**Die andere Hälfte:** `REFRESH_BARS = 5` — ein Tick zieht fünf Bars nach.
`macross` braucht `warmup_bars = 52`. In einem frischen Container mit leerem
`data/ohlcv/` endete der Tick deshalb mit „erst `qt data pull` laufen lassen",
also mit einem Handgriff, den ein geplanter Job nicht tun kann. **Genau daran
ist das Konto beim letzten Containerwechsel stehengeblieben, wochenlang, ohne
dass es jemandem auffiel** — die ROADMAP behauptete die ganze Zeit, es laufe.

Eine Zeile in der `.gitignore` hätte den Zustand gerettet und das Konto
trotzdem nicht zum Laufen gebracht.

**Konsequenz:** `_refresh_recent_bars` ist warmup-bewusst. Reicht der Bestand
nicht für `warmup_bars + 60`, wird einmalig ein langes Fenster gezogen; danach
greift wieder das kleine. Die Prüfung läuft **je Symbol** — ein neu
dazugenommenes Symbol soll nicht die Historie der anderen mitziehen.
Zusätzlich die `.gitignore`-Ausnahme für `/data/paper/`: Bars sind
nachziehbar, ein Konto ist es nicht.

Der Kaltstart ist gegengeprüft, nicht behauptet: `data/ohlcv/` geleert, Tick
gestartet, 112 Bars wurden nachgezogen und das Konto lief an. Der Test dazu
schlägt mit der alten Logik fehl.

### Was läuft

Zwei getrennte Konten, `macross` auf BTC/USD und ETH/USD, je 1d. BTC lieferte
OOS 0,31, ETH 0,32 (ADR-035) — **zwei unabhängige Vorwärtstests sind mehr wert
als eine bessere Einzelzahl.** `scripts/paper_tick.sh` macht ziehen → ticken →
committen → pushen; Git steht bewusst im Skript und nicht in der CLI.

### Vorab registriert, bevor Daten anfallen

**Gemessen wird Divergenz, nicht Gewinn.** Bei ~4 Trades im Jahr dauert eine
Aussage über Rentabilität Jahre. Die beantwortbare Frage ist, ob die
Papier-Kurve zu den Backtest-Annahmen passt:

1. **Fill-Preise** gegen das Kostenmodell — liegt der realisierte Slippage
   systematisch über den angenommenen 90 bps?
2. **Signalzeitpunkte** — fallen Ein- und Ausstiege dort, wo der Backtest sie
   erwartet, oder verschiebt die Refresh-Logik sie?
3. **Trades je Zeit** gegen die Backtest-Rate von ~4,3 im Jahr.

Eine systematische Abweichung in 1 oder 2 wäre ein Fehler in den
Backtest-Annahmen — **wertvoller als jede Renditezahl**, weil er alle
bisherigen Ergebnisse relativiert.

**Erwartungsmanagement:** die ersten Wochen liefern mit hoher
Wahrscheinlichkeit **null Trades**. Das ist kein Fehlschlag, sondern die
Frequenz, die `macross` überhaupt erst profitabel macht (ADR-035). Wer nach
zwei Wochen ein Ergebnis erwartet, misst das Falsche.

---

## ADR-050 — ML auf Marktdaten: gebaut, geprüft, an der eigenen Kontrolle gescheitert
**Datum:** 2026-09-01

Der Plan aus `docs/ML-PLAN.md` ist umgesetzt: Labeling, Merkmale, gepurgte
Validierung, Modell, Kontrollen. Auf die Frage „warum baust du nicht einfach
das Modell?" gibt es eine ehrliche Antwort — meine Begründung war halb
Zeremonie. Die Reihenfolge schützt nicht vor Selbsttäuschung; **vorab
festgelegte Bewertungskriterien** tun es. Die standen im Plan, also ist es
gleich, ob das Modell vor oder nach der Zählung entsteht.

### M0: das Datenbudget, und warum es der richtige Anfang war

14 Märkte gezogen (BTC, ETH, LTC, BCH, ETC, XLM, LINK, ALGO ab 2019; ADA,
DOGE, DOT, SOL, AVAX ab 2021; XRP bis zur Coinbase-Delistung nach der
SEC-Klage). **XRP bleibt bewusst im Panel** — es rauszunehmen wäre Selektion
auf Überleben, und genau die macht Krypto-Backtests systematisch zu optimistisch.

```
32.122 Bars -> 5.304 Labels -> effektiv 2.506
mittlere Einzigartigkeit 0,472
```

Die Bar-Zahl ist die falsche Größe: bei 20 Bars Horizont überlappen sich
benachbarte Labels zu über der Hälfte. **2.506 statt 32.122 ist der
Unterschied zwischen „viel Daten" und „genug Daten"** — und der Grund, warum
so viele Modelle im Backtest funktionieren und sonst nirgends: 5.304 Zeilen
sehen in jeder Bibliothek wie 5.304 unabhängige Beobachtungen aus.

Das Abbruchkriterium (~500) ist mit Faktor fünf bestanden.

### Die Basisrate, die das Modell schlagen musste

`macross`-Einstiege an CUSUM-Ereignissen, Ziel 2×ATR, Stop 1×ATR, 20 Bars:

* Trefferquote **35,8%**, Gewinner i.M. +12,67%, Verlierer −8,09%
* Gewinnschwelle daraus: **38,9%** → die Basis verliert 0,66% je Ereignis

**Die Aufgabe war damit exakt beziffert: 3,1 Prozentpunkte Trefferquote**,
zu holen durch Aussortieren. Bescheiden genug, um plausibel zu sein.

### Das Ergebnis: durchgefallen

Logistische Regression, fünf Merkmale, `C=0,1`, Uniqueness als
Stichprobengewicht, gepurgte Vorwärts-Folds mit 20 Tagen Embargo:

| | Folds über der Basis | Summe |
|---|---|---|
| **Modell** | **2 von 5** | −1078% |
| vertauschte Labels, Seed 0 | 2/5 | −2448% |
| **vertauschte Labels, Seed 1** | **4/5** | **−321%** |
| vertauschte Labels, Seed 2 | 3/5 | −974% |

**Zufällige Labels schlagen die echten.** Seed 1 liegt in 4 von 5 Folds über
der Basis, das echte Modell nur in 2 — und erzielt dabei die bessere Summe.
Dieselbe Kontrolle, die `hashribbon` gekippt hat (ADR-048), kippt auch das
Modell. Ohne sie wäre „Summe −1078% gegen Basis −2310%" als Halbierung des
Verlusts durchgegangen.

Zwei Anmerkungen zur Ehrlichkeit der Zahlen: die Summen zählen überlappende
Ereignisse mehrfach und sind **keine Portfoliorendite** — vergleichbar sind
sie nur untereinander. Und ich könnte jetzt Schwelle, `C` und Barrieren
variieren, bis etwas hält; genau das verbietet die Vorab-Registrierung. Jede
weitere Konfiguration wäre ein Versuch im DSR-Nenner.

### Was bleibt

Die Infrastruktur, und die ist der eigentliche Ertrag: Triple-Barrier-Labeling
mit Kostenschwelle, CUSUM-Ereignisse, Uniqueness-Gewichte, gepurgte
Vorwärts-Folds über eine gemeinsame Zeitachse mehrerer Märkte. Sie ist
wiederverwendbar, sobald es bessere Merkmale oder mehr Daten gibt.

**Der teuerste Kompromiss ist benannt:** `qt.ml.dataset` rechnet vektorisiert
statt über den `FeatureStore`. Damit kommt die Point-in-Time-Zusage **nur noch
aus einem Test** — künftige Bars verändern, Matrix muss bitidentisch bleiben.
Dazu eine Gegenprobe, die beweist, dass der Test einen echten Lookahead
(zentriertes Mittel) auch fängt. Wer den Test löscht, verliert die Zusage,
ohne dass etwas rot wird.

Fünf geprüfte Hypothesen in diesem Projekt, fünf gescheitert. Das ist kein
gutes Ergebnis, aber ein ehrliches — und jedes Mal hat eine Kontrolle
gesprochen, nicht ein Bauchgefühl.

---

## ADR-049 — Das Trade-Journal dokumentiert, es schlussfolgert nicht
**Datum:** 2026-09-01

Gewünscht war ein LLM-„Skill", der Trades dokumentiert, durchspielt warum ein
Setup schieflief, und daraus Lehren für künftige Trades zieht. Der zweite und
dritte Teil sind **überwachtes Lernen auf Marktdaten** mit dem LLM als
Funktionsapproximator: gelabelte Beispiele (Gewinner/Verlierer) rein, Muster
raus, Muster werden zu Regeln.

**Und zwar eine Variante mit schlechteren Eigenschaften als klassisches ML:**

1. **Kein Holdout, per Konstruktion.** Das Modell sieht alle Trades eines
   abgeschlossenen Laufs; jede Lehre ist auf denselben Daten gefittet, gegen
   die sie danach bewertet würde.
2. **Unzählbare Kapazität.** Ein Baum hat zählbare Parameter, die man
   regularisieren kann. Freitext-Lehren können beliebig spezifisch werden.
   Was man nicht zählen kann, kann man nicht korrigieren.
3. **Der Versuchszähler wird blind.** Das ist die schlimmste Eigenschaft. Eine
   Lehre, die in einen Prompt wandert, ist eine auf denselben Daten selektierte
   Hypothese, die der DSR-Nenner (ADR-032) nie sieht. Der Schutz wäre dann
   nicht abgeschaltet, sondern **still falsch** — schlimmer, weil er weiter
   grüne Zahlen liefert.
4. **n ist zu klein.** `macross` auf BTC/1d: **33 Round-Trips in 7,6 Jahren**.
5. **Narrativ-Fehlschluss obendrauf.** Ein Modell, das „warum ist das
   gescheitert?" gefragt wird, antwortet immer. Es hat keinen Prior für „war
   Rauschen"; eine Regression meldet wenigstens ein niedriges R².

**Die Trennlinie: ein Journal, das dokumentiert, ist kein ML. Eines, das
schlussfolgert, ist es.**

**Konsequenz:** Gebaut wird nur der deterministische Teil.
`qt.backtest.roundtrips` verdichtet Fills zu Round-Trips (Position von null
nach null) und `qt trades` zeigt sie. Kein Modell, keine Deutung.

Der interpretierende Teil ist nicht verworfen, sondern verlegt: er darf keine
Freitext-Lehren erzeugen, die in Prompts wandern, sondern **Kandidaten, die
durch das bestehende Phase-5-Tor gehen** — Sandbox, Walk-Forward, DSR gegen
den Versuchszähler. Dann liegt das Lernen *innerhalb* der statistischen
Kontrolle statt an ihr vorbei, und jede Lehre kostet einen Versuch. Technisch
wäre das kein neuer Mechanismus, sondern ein zusätzlicher Briefing-Typ für den
vorhandenen Generator.

### Was die Messung sofort zeigte

`macross` BTC/1d, 33 Round-Trips:

| | Gewinner | Verlierer |
|---|---|---|
| Anzahl | 15 (45,5%) | 18 |
| Rendite i.M. | +48,9% | −9,3% |
| **Kostenanteil i.M.** | **9,1%** | **52,8%** |
| Haltedauer i.M. | 78 Bars | 18 Bars |

Die Verlierer zahlen die **Hälfte ihres Bruttoergebnisses an Gebühren**. Die
Median-Rendite über alle Trades ist **−1,08%** bei einem Mittelwert von
+17,1%, und **der größte Einzelgewinn trägt 63% des Gesamtergebnisses**.

Das ist die erwartete Signatur einer Trendfolge — wenige große Gewinner,
viele kleine Verlierer — aber die Konzentration relativiert den OOS-Sharpe von
0,31 aus ADR-035 erheblich: ohne den besten Trade bleibt wenig übrig. Keine
dieser Zahlen war aus der Equity-Kurve ablesbar, und `Metrics.hit_rate` half
nicht: sie zählt Bars mit positiver Rendite, nicht Trades mit positivem
Ergebnis (48,6% gegen 45,5% sind hier zufällig ähnlich, messen aber
Verschiedenes).

**Bemerkenswert ist, was hier nicht steht:** keine Erklärung, warum die
Verlierer verloren. Die Zahlen laden dazu ein ("kürzere Haltedauer, also
Fehlausbrüche") — genau diese Einladung auszuschlagen ist der Zweck des ADR.

---

## ADR-048 — Miner-Kapitulation: BTC-spezifisch, aber nicht von Zufall zu unterscheiden
**Datum:** 2026-09-01

Auftrag war eine Strategie, die auf BTCs Unterschiede zu anderen Kryptos
zugeschnitten ist. **Zwei Begründungen wurden vorher gemessen und beide
widerlegt — vor der ersten Zeile Code:**

1. **„BTC ist berechenbarer."** 23 gepaarte Walk-Forwards (gleiche Strategie,
   gleicher Timeframe, nur Markt getauscht): BTC gewinnt 11 von 23, mittlere
   Differenz **−0,04** Sharpe. Kein Vorteil.
2. **„BTC ist der Zufluchtsort bei Krypto-Risk-off."** Der BTC/ETH-Dominanz-
   Filter trennt BTCs 20-Tage-Vorwärtsrendite um **+4,29** Prozentpunkte —
   ETHs aber um **+5,52**. Wäre der Mechanismus BTC-spezifisch, dürfte er ETH
   nicht helfen. Er hilft mehr.
3. **„Die Beta-Asymmetrie ist handelbar."** Long BTC / short ETH: Sharpe
   −0,47 gesamt, +0,16 im Hoch-Vola-Drittel (unter dem Standardfehler von
   ±0,47), Vorzeichenwechsel in vier von sieben Jahren.

Die Halving-These wurde gar nicht erst getestet: mit **zwei** Halvings im
Datensatz (2020-05, 2024-04) ist sie unfalsifizierbar, und keine der
Strategien hat ohnehin einen Eingang, der einen Zyklus sehen könnte.

**Was übrig blieb, war kein besseres Argument, sondern ein struktureller
Unterschied.** Ethereum ist seit 2022 Proof-of-Stake — es gibt keine
ETH-Miner, keine Hashrate, keine Kapitulation. Ein Signal aus der Hashrate ist
BTC-spezifisch per Konstruktion statt per Erzählung.

### Die Strategie

`hashribbon`: flach, solange das 30-Tage-Mittel der Hashrate unter dem
60-Tage-Mittel liegt, sonst long. **Zwei Parameter**, wie `macross`; `elliott`
hat sechs und streut über BTCs höhere Timeframes um 0,99 Sharpe (ADR-047).
Long/flach ohne Short, wie ADR-035. Die handelbare Behauptung ist eine
Unterlassung, keine Prognose: BTC nicht halten, solange die Miner kapitulieren.

Neu: `qt data onchain` zieht blockchain.info **jahrweise** — `timespan=8years`
liefert gemessen nur ein 2-Tages-Raster, erst `timespan=1year&start=…` gibt
Tagesauflösung. 2790 Punkte ab 2019-01, 99,6% Abdeckung, zwei Lücken (4 und 8
Tage), die die Prüfung benennt statt glättet.

### Das Ergebnis: durchgefallen

| | Sharpe | Rendite | MaxDD | Fenster+ |
|---|---|---|---|---|
| hashribbon BTC | **+0,16** | −13,8% | −68,7% | 3/7 |
| macross BTC (Messlatte) | +0,31 | +25,2% | −51,0% | 5/7 |
| hashribbon ETH (Placebo) | −0,16 | −73,4% | −79,5% | 3/7 |

Vorab festgelegt war: positiver Sharpe **und** besser als `macross`. Das
erste hält knapp, das zweite nicht. Damit ist die Spur beendet — dokumentiert,
nicht nachjustiert.

### Die zwei Kontrollen sagen mehr als die Tabelle

**Der Placebo hält.** BTC +0,16 gegen ETH −0,16: das Signal wirkt auf BTC und
nicht auf ETH, Abstand 0,32 Sharpe. Die BTC-Spezifität ist damit **belegt** —
anders als bei den zwei widerlegten Erzählungen oben. Es gibt nur keine Kante,
auf die sie sich beziehen könnte.

**Die Negativkontrolle ist vernichtend.** Dieselbe Regel auf zeitlich
permutierter Hashrate, fünf Ziehungen: −0,24, −0,78, −0,71, **+0,32**, −0,55.
Im Mittel −0,39, aber **eine von fünf zufälligen Permutationen schlägt die
echte Reihe** (+0,32 gegen +0,16). Wenn gewürfelte Daten in 20% der Fälle
besser abschneiden als die richtigen, ist das Ergebnis von Zufall nicht zu
unterscheiden. Ohne diese Kontrolle wäre +0,16 als „schwach, aber positiv"
durchgegangen.

### Was das über die Methode sagt

Drei Hypothesen, drei Widerlegungen, und die teuerste kostete eine Strategie
statt 56 Walk-Forwards. Bei `elliott` kam derselbe Placebo-Test **nach** der
Messung und musste eine bereits gebaute Zahl entwerten; hier stand er vorher
fest und hat zweimal Arbeit erspart, die nichts gebracht hätte.

Der Point-in-Time-Test dieser Strategie hat dieselbe Lektion noch einmal
geliefert: die erste Fassung war grün und **wertlos** — sie prüfte gegen eine
steigende Hashrate, bei der die Strategie ohnehin long ist, sodass ein
manipulierter Wert nichts änderte. Erst gegen eine *fallende* Reihe schlägt er
fehl, wenn man den Ein-Tages-Versatz entfernt. Gegengeprüft mit `LAG_BARS = 0`.

**Konsequenz:** `hashribbon` bleibt im Repo als geprüfter Negativbefund, nicht
als Kandidat. Der Ingest bleibt, weil die Reihe für künftige Fragen taugt. Der
Versuchszähler ist um einen Eintrag gewachsen — bewusst genau eine
Parametrisierung, kein Gitter.

---

## ADR-047 — Unter 1d entscheidet die Frequenz, über 1d entscheidet nichts mehr
**Datum:** 2026-09-01

56 Walk-Forwards über vier Strategien, zwei Symbole und sechs Timeframes von
`1h` bis `1w`, alle über dieselben 7,6 Jahre mit **kalender-gleichen** Fenstern
(1d: 1000/250/20 Bars, entsprechend skaliert). Bar-gleiche Fenster hätten `1h`
über zehn Monate und `1d` über sieben Jahre getestet — das wäre ein Vergleich
zwischen Zeiträumen gewesen, nicht zwischen Timeframes.

**Unter 1d: 8 von 8 Zellen monoton fallend, ohne Ausnahme.**

| Sharpe | 1d | 4h | 1h |
|---|---|---|---|
| trend BTC / ETH | +0,25 / +0,41 | −0,64 / −0,34 | −3,77 / −2,58 |
| meanrev BTC / ETH | −0,34 / −0,85 | −1,60 / −1,28 | −4,25 / −3,58 |
| macross BTC / ETH | +0,31 / +0,32 | −0,65 / −0,44 | −2,97 / −2,42 |
| elliott BTC / ETH | +0,20 / +0,26 | −1,26 / −0,40 | −2,04 / −0,91 |

Der Mechanismus steht in der Gebührenspalte: ~25k bei `1d`, ~135k bei `4h`,
~390k bei `1h` — gegen 100.000 Startkapital. Bei `1h` zahlt jede Strategie ein
Vielfaches ihres Kapitals an Gebühren; daher die Renditen um −99%.

**Über 1d: kein Muster mehr.** `macross` BTC steigt monoton bis 0,49 bei `1w`,
`trend` ETH fällt von 0,41 auf −0,58, `elliott` BTC springt zwischen
benachbarten Timeframes um 1,0 Sharpe (0,57 → −0,42 → 0,46). Plausibel: bei
`1d` sind die Gebühren bereits auf ~25k gefallen, von `1d` auf `1w` spart man
nur noch 20k — verliert aber sieben Achtel der Stichprobe. Der Gewinn ist
ausgereizt, das Rauschen übernimmt.

**Die verlockendste Zahl ist die gefährlichste.** `elliott` BTC auf `2d` liefert
+105% Rendite — der beste Wert von 56 getesteten Konfigurationen, und genau so
entsteht Overfitting (ADR-005). Das Kriterium ist nicht die höchste Zahl,
sondern die, die **repliziert**: bei `1d` liefert `macross` 0,31 auf BTC und
0,32 auf ETH ohne Neuanpassung; bei `1w` sind es 0,49 und −0,22.

**Zur Aussagekraft, vorab und nicht nachträglich:** der Standardfehler eines
annualisierten Sharpe hängt an der **Kalenderspanne**, nicht an der
Bar-Frequenz. Bei 4,8 Jahren OOS liegt er bei ±0,47 — für `1h` genauso wie für
`1w`. Feiner abzutasten schärft die Schätzung nicht. Belastbar ist deshalb nur
das Muster über acht Zellen, nie eine einzelne Zeile.

**Konsequenz:** `1d` bleibt der Arbeits-Timeframe. `2d`/`3d`/`1w` existieren
jetzt im Store (`qt data resample`) und dürfen geprüft werden, aber ohne
Erwartung. `meanrev` ist bei `1w` nicht testbar — 194 Bars Vorlauf passen nicht
in ein 143-Bar-Trainfenster; die Prüfung hat das gefangen statt still Unsinn zu
rechnen.

### Nachtrag 2026-09-01: auf anderem Bucket-Raster nachgerechnet

Der Audit in ADR-053 hat gefunden, dass die damals im Store liegenden
`2d`/`3d`/`1w`-Dateien auf einem **anderen Raster** lagen, als der Code heute
erzeugt (`start_day` statt `origin="epoch"` — kein einziger gemeinsamer
Zeitstempel). Die Über-1d-Zeilen oben stammen also aus Dateien, die sich mit
diesem Repo nicht reproduzieren lassen.

Nach der Reparatur nachgerechnet, gleiche Fenstergeometrie, Datenstand
2026-09-01:

| Sharpe | 1d | 2d | 3d | 1w |
|---|---|---|---|---|
| macross BTC | 0,31 | 0,27 | 0,33 | **0,52** (dok. 0,49) |
| macross ETH | 0,32 | 0,25 | 0,37 | **−0,25** (dok. −0,22) |
| elliott BTC | 0,20 | **0,59** (dok. 0,57) | −0,44 (dok. −0,42) | 0,46 |
| elliott ETH | 0,26 | −0,94 | −0,56 | −0,72 |

**Die Aussage hält, und zwar deutlicher als vorher.** Über 1d kein Muster:
`elliott` BTC springt weiterhin um rund 1,0 Sharpe zwischen benachbarten
Timeframes (0,59 → −0,44 → 0,46), `macross` repliziert bei `1w` weiterhin
nicht (0,52 gegen −0,25). Die verlockendste Zahl ist immer noch `elliott` BTC
auf `2d`, jetzt mit +111% statt +105%.

Dass ein um einen Tag verschobenes Raster die Zahlen um höchstens 0,03 Sharpe
bewegt, ist selbst ein Befund: **die Schlussfolgerung hing nicht am Raster.**
Sie hätte es können, und niemand hätte es gemerkt.

---

## ADR-046 — Der Allokator schaltet nicht zu schnell: er wird langsamer schlechter
**Datum:** 2026-09-01

ADR-045 schloss aus den 145 zwischengespeicherten Vorschlägen, der Allokator
schalte **zu schnell für die Persistenz des Signals** — 40% des Buches je
Schritt, 28 Vollumkehrungen, und gewonnen hatte `best_single` mit dem längsten
Lookback. Der Test dieser Hypothese kostete 29 Aufrufe und 45 Minuten. **Sie
ist falsch.**

| Sharpe | Takt 96 | Takt 384 |
|---|---|---|
| **llm** | −1,18 | **−2,19** |
| equal_weight | −2,54 | −2,54 |
| vol_parity(168) | −2,12 | −1,80 |
| best_single(720) | −0,98 | **−0,35** |
| llm gewinnt gg. equal_weight | 23/29 | **14/29** |

Langsamer schalten hilft **jedem regelbasierten** Allokator und **schadet dem
Modell**. `best_single` verdreifacht seinen Sharpe fast, der Allokator halbiert
seinen — und verliert seinen einzigen belastbaren Vorsprung: von 23 gewonnenen
Fenstern gegen `equal_weight` bleiben 14, also keine Mehrheit mehr.

**Damit ist auch die Kostenthese endgültig erledigt.** Der Umsatz fiel von 23,9
auf 21,2 Mio: weniger gehandelt, weniger Gebühren, schlechteres Ergebnis. Zwei
unabhängige Messungen zeigen jetzt in dieselbe Richtung — die Umsatzdifferenz
war nie die Ursache.

**Die plausibelste Deutung:** der Vorsprung des Modells kam aus
*Reaktionsfähigkeit*, nicht aus Urteilskraft. Bei Takt 384 trifft es 29
Entscheidungen statt 145, jede wird viermal so lange gehalten, und eine falsche
kostet entsprechend mehr. Seine Trefferquote je Entscheidung reicht nicht, um
Festlegung zu überleben. Ein Allokator, der nur solange gut aussieht, wie er
oft nachjustieren darf, hat keine Kante, sondern eine kurze Halbwertszeit.

Das Peeking-Risiko war vorher benannt: der Takt 384 wurde ausgewählt, nachdem
drei Werte auf denselben OOS-Daten verglichen worden waren. Es hat sich hier
nicht ausgewirkt, weil die Wahl **gegen** den Kandidaten arbeitete — der
stärkste Gegner profitierte am meisten.

5 der 29 Aufrufe kamen aus dem Cache. Das bestätigt die Pfadabhängigkeit des
Briefings: nur dort, wo `current_allocation` zufällig übereinstimmte, war die
Anfrage identisch.

**Konsequenz:** Die Takt-Spur ist zu Ende. Der nächste sinnvolle Test ist nicht
ein weiterer Parameter am Allokator, sondern ein Korb, in dem überhaupt etwas
Verdienendes liegt (ADR-047: `macross` auf `1d`). Ein Allokator kann nicht
verteilen, was nicht da ist.

---

## ADR-045 — Das Gate ist gelaufen: der LLM-Allokator ist durchgefallen
**Datum:** 2026-09-01

Der erste echte Gate-Lauf (ADR-004) gegen NVIDIA NIM: 145 Aufrufe, 3:47 Stunden,
29 Out-of-Sample-Fenster über `trend` und `meanrev` auf BTC/USD und ETH/USD, 4h.

| Allokator | Sharpe | Rendite | MaxDD | Umsatz | Trades |
|---|---|---|---|---|---|
| **llm** | −1,18 | −74,3% | −80,2% | 23,86 Mio | 1.271 |
| equal_weight | −2,54 | −70,5% | −71,0% | 20,64 Mio | 1.261 |
| vol_parity(168) | −2,12 | −68,7% | −70,2% | 21,98 Mio | 1.400 |
| best_single(720) | −0,98 | −62,2% | −69,2% | 14,43 Mio | 635 |

**Durchgefallen**, an zwei Punkten: der Sharpe ist nicht positiv, und
`best_single` wird weder im Gesamtwert (−1,18 gegen −0,98) noch in der Mehrheit
der Fenster (13 von 29) geschlagen.

**Was das Modell kann:** es schlägt `equal_weight` in 23 von 29 Fenstern und
`vol_parity` in 22 von 29. Das ist kein Rauschen, das ist ein Muster — und der
Sharpe-Abstand −1,18 gegen −2,54 ist mehr als eine Halbierung des Schadens.
Trotzdem geht es nicht weiter. Ein Allokator, der zwei von drei Baselines
schlägt, ist ein Teilerfolg, und das Gate ist genau dafür gebaut, sich von
Teilerfolgen nicht kaufen zu lassen.

**Die Telemetriezeile ist wichtiger als die Tabelle:** *Aufrufe 145, aus Cache
0, Rückfälle auf Gleichgewichtung 0 (0,0%), bewusste Ausstiege 3, halluzinierte
Labels 0.* Das Modell hat 145-mal wirklich geantwortet, jede Antwort war
brauchbar, keine wurde still zu Equal-Weight. Ohne ADR-043 hätte diese Zeile
5 von 145 Aufrufen beschrieben; ohne ADR-044 wäre jede kaputte JSON-Antwort als
Rückfall durchgeschlagen. Beide Korrekturen entstanden am selben Tag, Stunden
vor dem Lauf — ohne sie wäre das Ergebnis nicht interpretierbar gewesen.

Drei **bewusste Ausstiege**: das Modell ist dreimal absichtlich flach gegangen,
statt zu allokieren. Die Option, nichts zu tun, wird genutzt.

### Der Umsatz-Befund, und eine Korrektur

Naheliegend war die These, der Allokator handle zu teuer: 23,9 Mio gegen 14,4
Mio bei `best_single` sind +65%. **Das ist ein Vergleich mit dem falschen
Gegner.** `best_single` hält per Konstruktion nur eine Strategie; seine Hälfte
an Trades (635 gegen 1.271) kommt aus Konzentration, nicht aus Sparsamkeit.

Gegen den strukturgleichen Gegner `equal_weight` — ebenfalls voll investiert,
ebenfalls beide Strategien — sind es **+15,6% Umsatz bei 10 Trades
Unterschied**, und dafür Sharpe −1,18 statt −2,54. Das ist ein guter Tausch.
Ein Trägheitsterm gegen Umschichtungskosten löst also ein Problem, das die
Zahlen nicht hergeben.

### Was die 145 zwischengespeicherten Vorschläge zeigen

Ohne einen einzigen neuen Aufruf, direkt aus `.llm_cache`:

* Bruttoexposure **immer exakt 1,00** — nie Hebel, nie teilinvestiert.
* Änderung je Allokation: **Median 0,80** bei 1,00 Brutto. Im Median werden
  40% des Buches umgeschichtet.
* **28 von 144 Schritten sind Vollumkehrungen** (|Δw| = 2,0), 37 ändern nichts.
* Bei zwei Labels ist der Allokator faktisch ein **Schalter, kein Mischer**.

**Daraus die eigentliche Hypothese:** nicht "zu teuer", sondern **zu schnell
geschaltet für die Persistenz des Signals**. Der Gewinner demonstriert es:
`best_single` mit Lookback **720** hat gewonnen, während der Allokator alle 96
Bars auf Basis eines 384-Bar-Sharpe neu entscheidet.

### Einschränkung, die das Ergebnis begrenzt

Alle vier Allokatoren verlieren dreistellig Prozent. Gemessen wurde `trend` und
`meanrev` auf 4h — die Baseline-Strategien, die laut Plan ausdrücklich **nicht**
profitabel sein sollen. Das Gate vergleicht also, wer eine schlechte
Strategiemenge am wenigsten schlecht verteilt. Über einen LLM-Allokator auf
einer tragfähigen Menge (etwa `macross`, ADR-035) sagt dieser Lauf nichts.

**Konsequenz:** Der LLM-Allokator geht nicht in den Kreislauf. Der Cache des
Laufs ist versioniert (163 Einträge), damit die Zahlen in jedem künftigen
Container in Minuten statt vier Stunden reproduzierbar sind — er ist
zeitstempelfrei und liefert bitgleiche Dateien.

---

## ADR-044 — Ungeführtes JSON braucht eine korrigierende Nachfrage
**Datum:** 2026-09-01

Der Pflicht-Vorlauf vor dem Gate-Lauf (`tests/test_nim_live.py`) schlug fehl:
einer von drei Kritiker-Aufrufen lieferte JSON mit einem **nicht maskierten
Anführungszeichen** mitten in der Begründung — das Modell zitierte die
beanstandete Codezeile wörtlich. Ein zweiter, identischer Aufruf lief danach
sauber durch: der Fehler ist sporadisch, nicht systematisch.

Das ist die direkte Kehrseite von ADR-041. Geführte Dekodierung garantierte
gültiges JSON, fraß dafür Zeilenumbrüche; ungeführt kommen die Umbrüche
zurück, dafür schreibt das Modell die Syntax selbst — und gelegentlich falsch.
Es gibt hier keine Option ohne Preis, nur die Wahl, welchen man zahlt.

**Warum sporadisch schlimmer ist als systematisch:** ein Fehler, der immer
auftritt, fällt beim ersten Test auf. Einer, der in 1 von 3 Fällen auftritt,
kommt über die 145 Aufrufe eines Gate-Laufs mit Sicherheit vor — und wird dort
zu einer `LLMUnavailable`, die der Allokator als Rückfall auf Gleichgewichtung
behandelt (ADR-018). Der Lauf hätte also streckenweise eine Baseline gegen
sich selbst gemessen. Zusammen mit ADR-043, wo dieselbe Quote 29-fach zu
niedrig gemeldet wurde, wäre das unbemerkt geblieben.

**Konsequenz:** `LLMMalformed` trennt "Antwort kam an, war unbrauchbar" von
"kein Zugang" — abgeleitet von `LLMUnavailable`, damit kein Aufrufer sich
ändern muss. `NimProvider.parse` fragt bei einer unbrauchbaren Antwort bis zu
zweimal korrigierend nach und nennt dabei den Parser-Fehler und die
wahrscheinliche Ursache. Eine Nachfrage ohne Diagnose wäre derselbe Aufruf
noch einmal. Zwei Versuche, nicht mehr: wer zweimal kaputtes JSON liefert,
liefert es auch beim dritten Mal, und jeder Versuch kostet 40–155 Sekunden.
`malformed_retries` zählt mit — eine still weggeputzte Fehlerquote ist nach
ADR-043 genau die Art Zahl, die niemandem auffällt.

Ein bestehender Test brach dadurch und wurde angepasst statt umgangen: er
lieferte eine einzige falsche Antwort und erwartete sofortiges Aufgeben.

**Der eigentliche Ertrag ist der Vorlauf selbst.** Fünf Minuten und zwei
Aufrufe haben einen Fehler gefunden, der sechs Stunden später als
unerklärliche Rückfallquote aufgetaucht wäre — wenn überhaupt.

---

## ADR-043 — Die Rückfallquote beschrieb 5 von 145 Aufrufen
**Datum:** 2026-09-01

Bei der Vorbereitung des Gate-Laufs (ADR-004) fiel auf, dass `qt alloc` nach
einem kompletten Lauf über 29 Walk-Forward-Fenster `Aufrufe 5` meldete. Fünf
ist die Zahl der Aufrufe in *einem* Fenster: 500 Testbars durch einen Takt von
96. Der Gate-Lauf baut pro Fenster einen frischen Allokator — richtig so, sonst
trüge Zustand aus Fenster *n* nach *n+1* und unterliefe genau das Purging, für
das Embargo und Walk-Forward gebaut sind. Die CLI hielt aber nur einen Slot
(`allocator_telemetry["last"]`) und überschrieb ihn pro Fenster.

**Warum das mehr ist als eine falsche Zahl im Bericht:** die betroffene Größe
ist die Rückfallquote, und die ist nach ADR-018 die einzige Zahl, an der man
erkennt, ob der Allokator heimlich Gleichgewichtung war. Ein Lauf, der in 28
von 29 Fenstern auf Equal-Weight zurückfällt und im letzten nicht, meldete
`Rueckfaelle 0 (0.0%)`. Das Gate hätte dann sauber ausgesehen, während es in
Wahrheit eine Baseline gegen sich selbst gemessen hätte — der Fehler, gegen den
ADR-018 überhaupt geschrieben wurde, unsichtbar gemacht durch die Anzeige.

Besonders relevant für NIM: an diesem Endpunkt ist HTTP 503 gemessen (ADR-040),
und ein durchgereichter 503 wird zum Rückfall. Über sechs Stunden Laufzeit ist
vorübergehende Überlast wahrscheinlich, nicht möglich. Eine um Faktor 29 zu
niedrig gemeldete Quote hätte genau die Härtung verdeckt, die dafür eingebaut
wurde (`max_retries=4`).

**Konsequenz:** `AllocatorTelemetry.merge()` summiert die Zähler; `qt alloc`
sammelt ein Objekt je Fenster und faltet sie vor der Ausgabe zusammen. Der
Stub-Lauf meldet jetzt `Aufrufe 145` — die Zahl, die in der ROADMAP schon
richtig stand, nur nirgends gemessen wurde.

Der Test dazu konstruiert den bösartigen Fall ausdrücklich: 28 Fenster mit
100% Rückfall, ein sauberes am Ende. Die Einzelansicht sagt 0%, die Summe 97%.

Lektion, zum dritten Mal in dieser Reihe (ADR-040, ADR-042): **eine Kennzahl,
die nie gegen einen bekannten Wert geprüft wurde, ist eine Behauptung.** Hier
stand die richtige Zahl seit Wochen in der ROADMAP und wurde vom Werkzeug nie
bestätigt — niemandem fiel der Widerspruch auf, weil nie jemand beides
nebeneinander gelegt hat.

---

## ADR-042 — `bars_per_year` gehört in die Sandbox: ein dokumentierter Indikator war nicht aufrufbar
**Datum:** 2026-08-31

Der erste Research-Lauf mit funktionierendem Generator (ADR-041) lieferte zehn
Kandidaten. Acht liefen durch das Screening, zwei starben im Probelauf — und beide
an Stellen, an denen der Prompt die Wahrheit über die eigene API nicht sagte:

* `vol_regime_trend`: `realised_vol() missing 1 required positional argument`.
  Der Prompt bewarb `ta.realised_vol(closes, n, bars_per_year)`, aber
  `bars_per_year` war in der Sandbox überhaupt nicht gebunden — verfügbar waren
  nur `np`, `math`, `ta`, `Strategy`, `clip_weight`. Der Indikator war aus einem
  Kandidaten heraus **nicht korrekt aufrufbar**. Der einzige verbleibende Weg
  wäre eine hartkodierte Zahl gewesen, also genau die an einen Timeframe
  gebundene Magic Constant, die der Kritiker ablehnen soll. Der Aufruf mit zwei
  Argumenten war die logische Folge, nicht der Fehler des Modells.
* `rsi_reversion`: `'float' object is not subscriptable`. Das Modell hat RSI
  korrekt aus zwei Skalaren gerechnet und dann aus Gewohnheit `rsi[-1]`
  geschrieben. Der Prompt notierte `-> float`, sagte aber nirgends den Satz
  "da ist nichts zu indizieren". In pandas und TA-Lib sind Indikatoren Reihen;
  hier sind sie fertige Werte für den aktuellen Bar.

**Warum das keine Modellschwäche ist:** derselbe Kandidat benutzte `np.diff` und
`np.where` auf Arrays völlig richtig. Das Modell versteht die Unterscheidung — es
hatte nur keine Ansage, auf welcher Seite `ta.*` steht. Zwei von zehn Kandidaten
an unklarer Dokumentation zu verlieren, ist eine Prompt-Quote, keine Modellquote.

**Konsequenz:**

1. `bars_per_year` ist der sechste gebundene Name in der Sandbox. Die Funktion ist
   rein (Timeframe-String rein, Zahl raus) und erweitert die Angriffsfläche nicht.
2. `INJECTED_NAMES` steht jetzt *vor* `_WHY_FORBIDDEN` und speist die
   Ablehnungsmeldung. Vorher waren es zwei Listen, die auseinanderlaufen konnten;
   ein `assert` hält Liste und Bindung deckungsgleich.
3. Der Prompt sagt den Skalar-Satz ausdrücklich, mit dem falschen und dem
   richtigen Beispiel nebeneinander, und zeigt `bars_per_year(self.timeframe)`
   als Argument statt eines nackten Namens.

**Der Wächter, und warum die naheliegende Variante nichts getaugt hätte:**
`tests/test_generator_prompt.py` vergleicht die Indikatorliste im Prompt mit
`inspect.signature`. Das allein hätte den Fehler **nicht** gefunden: der Prompt
nannte drei Argumente, die Funktion hat drei — die Stelligkeit stimmte. Falsch war,
dass der Kandidat an das dritte nicht herankam. Der Test, der greift, baut deshalb
zu *jedem* dokumentierten Indikator einen Minimalkandidaten, der ihn genau wie
dokumentiert aufruft, und schickt ihn durch `check` und `probe`. Gegengeprüft: mit
zurückgenommener Bindung schlägt er mit derselben Meldung fehl wie der echte Lauf.

Das ist dieselbe Lektion wie in ADR-040, eine Ebene tiefer: **Dokumentation, die
nicht ausgeführt wird, driftet.** Der Prompt ist ausführbare Schnittstelle, kein
Fließtext, und gehört wie Code getestet.

---

## ADR-041 — Erzwungene JSON-Form ist auf NIM aus: sie frisst Zeilenumbrüche
**Datum:** 2026-08-31

ADR-040 hat `response_format` zum Primärweg gemacht, weil der Endpunkt es
annimmt und ein verschachteltes Schema korrekt beantwortet. Der erste echte
Research-Lauf hat gezeigt, dass "nimmt es an" und "beantwortet es richtig"
zwei verschiedene Dinge sind.

**Der Befund:** 10 von 10 Kandidaten an der Sandbox gescheitert, alle mit
einem Syntaxfehler in **Zeile 1**. Der gespeicherte Code erklärt es:

```
"class SMAMomentum(Strategy):n    name = 'sma_momentum'n    LOOKBACK = 50n..."
"class VolBreakout(Strategy):    name = 'vol_breakout'    LOOKBACK = 20..."
```

Beim einen wurde aus dem Umbruch der **Buchstabe `n`** (der Backslash fehlt),
beim anderen ist er ersatzlos weg. Jede Klasse ist eine einzige Zeile und
damit syntaktisch tot.

**Die Ursache ist nicht das Modell, sondern die erzwungene Form.** Derselbe
Prompt, zweimal, einziger Unterschied ist `response_format`:

| | Zeilen im Code | echte Umbrüche |
|---|---|---|
| geführt (`response_format` + `json_schema`) | **1** | nein |
| ungeführt (nur Prompt-Anweisung) | **17** | ja |

Die grammatikgesteuerte Dekodierung dieses Endpunkts kann kein `\n` in einem
String erzeugen.

**Warum das schlimmer ist als "der Generator ist kaputt":** betroffen ist
jedes Freitextfeld. `CandidateCritique.reasoning` und
`AllocationProposal.reasoning` würden genauso verstümmelt — nur fällt es dort
**nicht auf**, weil ein einzeiliger Fließtext kein Syntaxfehler ist. Ein
Mechanismus, der Inhalte lautlos beschädigt, ist schlechter als keiner.
Deshalb: `guided=False` als Default für NIM. Der Schalter bleibt für eine
Bereitstellung, die es besser kann, aber er ist eine bewusste Entscheidung
und kein Zustand, in den man hineinrutscht.

**Der dritte Fehler desselben Abends, und der heimtückischste:** nach der
Korrektur lief der Research-Lauf erneut — und lieferte Zeichen für Zeichen
dasselbe kaputte Ergebnis. Zehn identische Kandidatennamen, zehn identische
Syntaxfehler. Der Cache hatte die alten Antworten zurückgegeben, weil die
**Aufrufform nicht im Key stand**. Die Korrektur sah wirkungslos aus, obwohl
sie wirkte.

Dieselbe Lehre wie bei Modell (ADR-028), Effort (ADR-028) und Anbieter
(ADR-039), einmal mehr: *alles, was die Antwort mitbestimmt, gehört in den
Key.* Der Anbieter liefert dafür jetzt einen `cache_tag` statt nur seines
Namens — `nim` gegen `nim+gefuehrt`. Ein Provider-Name allein reicht nicht,
sobald derselbe Anbieter auf zwei Arten aufgerufen werden kann.

**Was das über die Testbarkeit sagt.** Alle drei Fehler dieses Abends —
`guided_json` (ADR-040), die verschluckten Umbrüche und der zu grobe
Cache-Key — waren gegen Attrappen unsichtbar, und zwar aus demselben Grund:
eine Attrappe liefert genau den Text, den man ihr vorlegt. Sie kann nicht
zeigen, dass der echte Endpunkt bei denselben Parametern etwas anderes tut.
36 grüne Tests haben keinen davon gefunden; ein echter Aufruf hat alle drei
gefunden. `tests/test_nim_live.py` prüft deshalb jetzt ausdrücklich, dass
erzeugter Code mehr als drei Zeilen hat und eine eingerückte Zeile enthält —
grob mit Absicht: nicht "der Code ist gut", sondern "der Code ist überhaupt
Python".

**Nicht behoben, nur benannt:** die zehn verworfenen Kandidaten des ersten
Laufs bleiben in der Registry stehen. Sie haben den Versuchszähler korrekt
**nicht** erhöht (ADR-032 — nur ein abgeschlossenes Screening zählt), und ein
Audit-Pfad, aus dem man Fehlschläge entfernt, ist keiner.

---

## ADR-040 — Der erste echte NIM-Aufruf hat zwei Fehler gefunden, die 34 grüne Tests nicht sahen
**Datum:** 2026-08-31

ADR-039 hat die Anbieter-Naht gegen Attrappen gebaut und dort ausdrücklich
festgehalten, was nicht bewiesen ist: "ob der gehostete Endpunkt sich so
verhält, wie seine Dokumentation sagt". Der erste echte Aufruf hat die Frage
beantwortet — mit Nein, und zwar zweimal.

**Fehler 1: `nvext.guided_json` gibt es am gehosteten Endpunkt nicht.** Die
NIM-Dokumentation empfiehlt es ausdrücklich gegenüber `response_format`, und
genau danach war der Provider gebaut. Die Antwort von
`integrate.api.nvidia.com` ist HTTP 400: `unknown field 'guided_json',
expected one of ... max_thinking_tokens, cache_salt, ...`. Der Abstiegspfad
aus ADR-039 hat das aufgefangen — jeder Aufruf lief ungeführt durch, lieferte
gültiges JSON und *sah deshalb erfolgreich aus*. Ohne die Diagnosezeile
"guided_json akzeptiert: nein" wäre das nie aufgefallen: ein dauerhaft
degradierter Pfad, der funktioniert.

**Die Korrektur:** `response_format` mit `json_schema` ist jetzt der
Primärweg. Gemessen angenommen, auch mit dem **verschachtelten** Schema von
`AllocationProposal` samt `$defs`/`$ref`, das pydantic erzeugt — die erste
Probe lief gegen ein flaches Schema und bewies für den Allokator nichts, die
zweite gegen das echte. `guided_json` ist ersatzlos raus; der eine gemerkte
Abstieg bleibt für eine selbst betriebene Instanz, die es umgekehrt hält.

**Fehler 2: kein Denkbudget.** `nvext.max_thinking_tokens` steht in der
Feldliste, die der 400er zurückgibt — es sah nach dem fehlenden echten Regler
für die Effort-Abbildung aus. Der Runner lehnt es trotzdem ab:
`thinking_token_budget is not yet supported by the V2 model runner`. Die
Abbildung bleibt damit bei den drei Zuständen aus `chat_template_kwargs`. Das
ist jetzt eine gemessene Grenze und keine Auslassung aus Unsicherheit — und
steht so im Code, damit es niemand "korrigiert".

**Fehler 3, kein Codefehler, aber ein Ergebnis: HTTP 503.** Der
Kontrollaufruf kam als "Service temporarily overloaded" zurück. Ein
durchgereichter 503 wird im Allokator zu einem Rückfall auf Gleichgewichtung —
der Lauf läuft weiter, sieht gesund aus und misst heimlich eine Baseline
(ADR-018). Deshalb sind `max_retries=4` und `timeout=300s` jetzt **ausdrücklich
gesetzt** statt vom SDK geerbt (dessen Defaults 2 und 600 Sekunden sind). Ein
Gate-Lauf mit 145 Aufrufen darf seine Aussage nicht an einer Überlastung
verlieren, die niemand sieht.

**Die Zahl, die die Planung ändert: 90 bis 155 Sekunden pro Aufruf.** Vier
Messungen: 89 s und 106 s vor der Korrektur, 133 s und 155 s danach — die
Schwankung ist Auslastung des geteilten Endpunkts, nicht Effort (auch
`low` mit Denken *aus* und 7 Token Antwort brauchte 36 s). Hochgerechnet:

| Lauf | Aufrufe | NIM | Anthropic |
|---|---|---|---|
| `qt alloc --compare-baselines --allocate-every 96` | 145 | **4–6 Stunden** | Minuten |
| `qt research --generate 10 --screen` | ≤ 20 | **30–50 Minuten** | Minuten |

Das ist kein Argument gegen NIM, aber es macht den Gate-Lauf zu etwas, das man
startet und liegen lässt — nicht zu etwas, das man nebenbei ausprobiert. Der
Antwort-Cache federt jeden Wiederholungslauf ab; der erste kostet die Zeit.

**Inhaltlich hat Nemotron bestanden.** Der Testkandidat enthält `close >
42000`, eine magische Preiskonstante. Beide Effort-Stufen haben sie benannt,
mit Zeilenbezug, und `reject` empfohlen (Overfitting-Risiko 0.85 bis 1.0). Als
Vorfilter taugt das Modell also. Über den *Generator* sagt das nichts — ob
Nemotron sandbox-legalen Code schreibt, entscheidet erst die Quote "Sandbox
verworfen" im ersten echten Research-Lauf.

**Die Lehre, die über NIM hinausgeht:** 34 grüne Tests gegen Attrappen haben
einen falschen Primärparameter nicht gesehen, weil Attrappen alles annehmen,
was man ihnen gibt. Deshalb liegt der Aufruf jetzt als `tests/test_nim_live.py`
im Repo — übersprungen ohne Schlüssel, mit `slow` markiert, und er prüft
ausdrücklich `not provider._schema_refused`. Ein stiller Abstieg ist ab jetzt
ein roter Test und keine Diagnosezeile, die jemand lesen muss.

---

## ADR-039 — Ein zweiter Anbieter als Naht, nicht als zweite Client-Familie
**Datum:** 2026-08-31

**Der Anlass:** ein NVIDIA-NIM-Schlüssel und der Wunsch, Nemotron 3 Ultra
statt Claude zu fahren. Bis dahin war "das Modell" gleichbedeutend mit
"Anthropic": vier Clients (Allokator, Szenario, Generator, Kritiker) trugen
jeder eine eigene Kopie von `_ensure_client` und jeder denselben
`messages.parse`-Aufruf. Ein zweiter Anbieter hätte diese Kopien verdoppelt —
acht Stellen, an denen dieselbe Entscheidung getroffen wird.

**Die Entscheidung:** `qt/llm/providers.py` ist eine Naht. Ein Provider
übersetzt (Systemprompt, Prompt, Schema, Modell, Token-Budget, Effort) in
einen Aufruf und die Antwort zurück in ein validiertes pydantic-Modell. Was
ein Client fachlich tut — welchen Prompt er stellt, was er cacht, wie er den
Key bildet — bleibt im Client. Die vier `_call`-Methoden schrumpfen auf je
einen Aufruf, die vier `_ensure_client`-Kopien entfallen ersatzlos.

**Anthropic bleibt Default.** Jede bisher gemessene Zahl, jeder ADR und jeder
Cache-Eintrag hängt daran. `--provider nim` ist eine Option, kein Umzug.

**Der Anbieter gehört in den Cache-Key.** Dieselbe Frage an Claude und an
Nemotron sind zwei Antworten. Stünde der Anbieter nicht im Key, lieferte ein
NIM-Lauf stillschweigend die gecachte Claude-Antwort — kein Fehlschlag, nur
ein falsches Ergebnis, und zwar in einem Backtest, wo es niemand mehr findet.
Dieselbe Überlegung wie bei Modell und Effort (ADR-028). Ein Modellname
allein reicht als Trennung nicht: nichts hindert zwei Endpunkte daran,
denselben Namen zu führen. Der Cache war zum Zeitpunkt der Umstellung leer,
die Entwertung kostete also nichts.

**Die Effort-Stufen sind eine gemeinsame Sprache, aber keine Äquivalenz.** Die
Kommandozeile kennt weiter `low` bis `max`. NIM hat aber nicht fünf
Denkstufen, sondern drei Zustände (`enable_thinking` aus, `medium_effort`,
volles Denken). `xhigh` und `max` denken auf NIM deshalb **nicht** tiefer als
`high` — sie heben nur die Token-Decke. Das steht als Test fest
(`test_xhigh_und_max_denken_nicht_tiefer_als_high_sondern_laenger`) und nicht
nur als Kommentar: wer es nicht weiß, glaubt, er habe etwas eingestellt, das
es nicht gibt.

**Denk-Token zählen gegen `max_tokens`** — anders als bei Anthropic, wo das
Denkbudget getrennt geführt wird. Ein Aufruf mit eingeschaltetem Denken kann
sein gesamtes Budget im Gedankengang verbrauchen und eine abgeschnittene oder
leere Antwort liefern. Das Budget wird deshalb je nach Stufe angehoben, und
beide Fehlerbilder bekommen einen eigenen Satz: "abgeschnitten bei N Token"
und "leerer Inhalt trotz Antwort" haben verschiedene Ursachen und werden sonst
an der falschen Stelle gesucht.

**Strukturierte Ausgabe über `nvext.guided_json`, mit genau einem Abstieg.**
NVIDIA empfiehlt `guided_json` ausdrücklich gegenüber
`response_format={"type": "json_object"}`, weil letzteres jedes gültige JSON
erlaubt — auch ein leeres Objekt. Nicht jede NIM-Bereitstellung kennt `nvext`;
wird es abgelehnt, steigt der Provider einmal auf den ungeführten Weg ab und
**merkt sich das**. Ohne das Merken zahlte jeder der hunderte Aufrufe eines
Laufs den abgelehnten Versuch erneut. Der Abstieg ist eng gefasst: nur bei
einer Meldung über ein unbekanntes Feld, nicht bei jedem Fehler — sonst
verwandelt ein falscher Schlüssel sich in einen zweiten, genauso aussichtslosen
Aufruf, und die Folgemeldung verdeckt die Ursache.

**Die eingefrorenen Systemprompts bleiben eingefroren.** Ein
OpenAI-kompatibler Endpunkt muss dem Modell im Text sagen, was es produzieren
soll — `guided_json` erzwingt nur die Form. Diese Schema-Anweisung hängt der
*Provider* an, nicht der Client. Der Prompt in `qt/llm/client.py` bleibt
unverändert und damit vergleichbar mit früheren Läufen; dass die beiden
Anbieter trotzdem getrennte Cache-Einträge bekommen, leistet der Anbieter im
Key. Ein Test hält das fest.

**Temperatur 0 wäre hier ein Fehler.** NVIDIA empfiehlt für die
Reasoning-Modi ausdrücklich `temperature=1.0, top_p=0.95`; ein auf 0 gedrehtes
Reasoning-Modell wird nicht deterministisch, sondern schlechter.
Reproduzierbarkeit kommt in diesem Projekt ohnehin nicht vom Sampler, sondern
vom Antwort-Cache — ein fester `seed` ist nur die zweite Verteidigungslinie.

**Was hier ausdrücklich nicht bewiesen ist.** Getestet ist die Naht gegen
Attrappen: Effort-Abbildung, Cache-Trennung, Entfernen des Gedankengangs,
Abstieg, Fehlermeldungen — 33 Tests, keiner davon mit Schlüssel. Nicht geprüft
sind (a) ob der gehostete Endpunkt sich so verhält, wie seine Dokumentation
sagt, und (b) ob Nemotron im Research-Loop sandbox-legalen Code schreibt.
ADR-030 hat den Generator-Prompt gegen die echte `check()`-Funktion geprüft —
aber für Claude. Ein hoher Anteil verworfener Kandidaten im ersten
NIM-Research-Lauf wäre deshalb ein Befund über die Prompt-Modell-Passung, kein
Fehler der Sandbox. Die Trichter-Zahlen von `qt research` zeigen es direkt.

**`openai` ist ein optionales Extra** (`uv sync --extra nim`), aus demselben
Grund wie `timesfm` in ADR-022: wer beim Default bleibt, soll dafür kein
zweites SDK installieren müssen. Der Preis ist, dass der NIM-Pfad selbst
erklären muss, was fehlt — er tut es, mit dem Installationsbefehl in der
Meldung.

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

> **Nachtrag 2026-09-02 (ADR-054):** „nicht bewiesen" war noch zu freundlich.
> Die Permutationskontrolle, die `hashribbon` gekippt hat, wurde nachgeholt:
> `macross` liegt auf **Perzentil 74 % (BTC) und 82 % (ETH)** seiner eigenen
> gewürfelten Fassungen — 257 bzw. 179 von 1000 Zufallsplatzierungen derselben
> Episoden waren mindestens so gut. Die unten stehenden Zahlen bleiben richtig;
> was nicht gilt, ist die Lesart „die erste Strategie, die etwas kann".

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
