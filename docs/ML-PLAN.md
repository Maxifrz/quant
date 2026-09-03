# Machine Learning auf Marktdaten — von Grund auf

Ein Entwurf, kein Bauauftrag. Er endet nach Phase M0 möglicherweise mit
„nicht machbar", und das ist ein zulässiges Ergebnis.

---

## Die Zahl, die über alles entscheidet: gemessen, nicht geschätzt

Fast jedes ML-auf-Märkte-Projekt startet mit dem Modell. Der richtige Start
ist das **Datenbudget** — wieviele *unabhängige* Beobachtungen gibt es
wirklich?

```
BTC/USD 1d:   2.792 Bars,  AR(1) −0,073,  effektiv ~2.737
BTC/USD 1h:  66.879 Bars,  AR(1) −0,020,  effektiv ~66.879
```

Die Bar-Zahl ist die falsche Größe. Ein Label mit Horizont *h* überlappt sich
mit den *h* folgenden Labels; die Zahl der wirklich unabhängigen Episoden ist:

| Horizont | Episoden je Markt | bei zwei Märkten |
|---|---|---|
| 5 Tage | 558 | 1.116 |
| 20 Tage | 139 | 278 |
| **45 Tage** (`macross` i.M.) | **62** | **124** |
| 60 Tage | 46 | 92 |

**Bei dem Horizont, auf dem die einzige funktionierende Strategie des Projekts
arbeitet, gibt es 124 unabhängige Beobachtungen.** Damit ist ein Modell mit 50
Merkmalen nicht unterparametrisiert, sondern absurd. Diese Tabelle ist die
Begründung für jede Entscheidung weiter unten.

**Konsequenz, vorab:** entweder kurzer Horizont (mehr Episoden, aber die
Kostenschwelle von 90 Basispunkten wird härter, ADR-047) oder Querschnitt
über viele Märkte. Beides zugleich ist der einzig gangbare Weg.

---

## Was hier „richtig" heißt

Fünf Fehler machen die meisten Anläufe, und alle fünf hat dieses Projekt an
anderer Stelle schon einmal gemacht oder abgewehrt:

1. **Naives Label.** „Vorzeichen der nächsten Bar-Rendite" ist fast reines
   Rauschen und ignoriert, dass eine Position einen Stop und ein Ziel hat.
2. **Jeder Bar ist ein Sample.** Aufeinanderfolgende Bars sind fast identisch;
   man bläst die Stichprobe auf und hält die Aufblähung für Evidenz.
3. **Überlappende Labels als unabhängig behandeln** — derselbe Fehler noch
   einmal, eine Ebene tiefer.
4. **Standard-k-Fold-CV.** Bei Zeitreihen leckt sie katastrophal. Das Projekt
   hat Purging und Embargo bereits (`walkforward.py`) — eine zweite,
   schlechtere Variante daneben wäre grob fahrlässig.
5. **Hyperparameter-Suche, die im Versuchszähler nicht auftaucht.** Ein Grid
   über 100 Konfigurationen sind **100 Versuche**. Die DSR-Schwelle danach ist
   brutal — und genau deshalb wird sie in der Praxis verschwiegen.

---

## Der Ansatz: Meta-Labeling statt Richtungsprognose

Nicht „wohin geht der Markt" — das ist die schwerste denkbare Frage bei
124 Beobachtungen. Stattdessen: **`macross` liefert die Richtung, das Modell
schätzt, ob dieser Einstieg trägt.**

Vorteile, die alle aus vorhandenen Messungen kommen:

* Das Label ist natürlich und schon gemessen: hat der Round-Trip gewonnen?
  (`qt.backtest.roundtrips`, ADR-049)
* Die Messlatte steht fest: `macross` ungefiltert, Sharpe 0,31 auf BTC/1d.
* Die Verlierer haben eine sichtbare Signatur — 18 Bars Haltedauer gegen 78,
  Kostenanteil 52,8% gegen 9,1%. Es gibt also überhaupt etwas zu trennen.
* Das Modell gibt eine **Wahrscheinlichkeit** aus, keine Position. Damit passt
  es ohne neue Verdrahtung in die bestehende Architektur: es wird zu einer
  `Strategy`, die Zielgewichte ausgibt (ADR-002).

Der Preis: **33 Round-Trips auf BTC/1d.** Das reicht nicht. Deshalb muss die
Stichprobe über Märkte und über ein ereignisbasiertes Sampling wachsen, bevor
irgendein Modell gefittet wird — siehe M0.

---

## Phasen

Jede endet mit etwas Sichtbarem und darf die Reihe beenden.

### M0 — Datenbudget und Machbarkeit *(zuerst, und mit Abbruchrecht)*

Kein Modell. Nur: wieviele unabhängige, gelabelte Episoden bekommen wir?

* Ereignisbasiertes Sampling statt jeder Bar: **CUSUM-Filter** auf
  normalisierte Renditen — ein Sample nur, wenn sich etwas bewegt hat.
* Querschnitt: BTC, ETH und weitere liquide Coinbase-Paare ziehen. Mehr
  Märkte sind nicht nur mehr Daten, sie sind ein **eingebauter Placebo**: ein
  Merkmal, das nur auf einem Markt wirkt, ist verdächtig (ADR-048).
* Durchschnittliche Einzigartigkeit der Labels berechnen (Überlappungsgrad).

**Abbruchkriterium, vorab:** unter ~500 effektiven Beobachtungen wird nicht
weitergebaut. Dann ist die ehrliche Antwort „die Datenmenge trägt kein
Modell", und die steht als ADR im Log statt als Modell im Repo.

### M1 — Labeling

* **Triple-Barrier**: je Ereignis ein oberes (Ziel), unteres (Stop) und
  zeitliches Barrier; das Label ist, welches zuerst fällt. Die Barrieren
  werden in **ATR-Vielfachen** gesetzt, nie in Preiskonstanten (die
  Sandbox-Regel aus ADR-030 gilt hier genauso).
* Barrieren gegen das **bestehende Kostenmodell** kalibrieren: ein Ziel unter
  90 Basispunkten ist vor Kosten tot und darf gar nicht erst gelabelt werden.
* **Uniqueness-Gewichte** für überlappende Labels.

*Sichtbar:* eine Label-Verteilung und die effektive Stichprobengröße.

### M2 — Merkmale, durch den bestehenden Point-in-Time-Pfad

**Das größte Risiko des ganzen Vorhabens.** Eine pandas-Pipeline über die
ganze Reihe ist eine Lookahead-Maschine, und sie sieht harmlos aus. Deshalb:

* Merkmale werden über `FeatureStore` und `window.*` gebaut, wie jede
  Strategie — nicht über einen zweiten Pfad.
* Der bestehende Lookahead-Test wird auf die Merkmalsmatrix erweitert:
  zukünftige Bars verändern → die Matrix muss bitidentisch bleiben.
* **Wenige Merkmale, und jedes begründet.** Startbudget: **fünf**. `elliott`
  mit sechs Parametern streut um 0,99 Sharpe, `macross` mit zweien um 0,12
  (ADR-047). Diese Erfahrung gilt für Merkmale genauso.

### M3 — Validierung: das Vorhandene benutzen

`walkforward.py` kann Purging und Embargo bereits. Es wird **wiederverwendet**,
nicht nachgebaut. Ergänzt um **Combinatorial Purged CV**, wenn M0 genug Daten
zeigt; bei 124 Episoden lohnt sich das nicht.

### M4 — Modell: das kleinste, das die Frage beantworten kann

* **Logistische Regression, regularisiert, fünf Merkmale.** Kein Gradient
  Boosting. Bei dieser Stichprobengröße ist ein Verfahren mit hoher Kapazität
  kein besseres Werkzeug, sondern ein schnellerer Weg zum Overfitting.
* **Genau drei vorab festgelegte Konfigurationen**, kein Grid. Jede
  zusätzliche ist ein Versuch mehr im DSR-Nenner, und das wird ausgewiesen.

### M5 — Auswertung, mit denselben Kontrollen wie heute

Vorab registriert, nicht nachträglich gewählt:

1. **Messlatte:** schlägt es `macross` ungefiltert out-of-sample?
2. **Vertauschte Labels:** dasselbe Modell auf permutierten Labels muss
   nichts liefern. (Genau diese Kontrolle hat `hashribbon` gekippt: eine von
   fünf Zufallsziehungen war besser als die echte Reihe, ADR-048.)
3. **Markt-Placebo:** auf einem Markt trainiert, auf einem anderen getestet.
4. **DSR gegen den Versuchszähler**, inklusive aller Konfigurationen.

### M6 — Verdrahtung, nur wenn M5 hält

Als gewöhnliche `Strategy`, die Zielgewichte ausgibt. Keine Sonderrolle, kein
eigener Pfad, dieselbe Risk-Engine. Ein ML-Modell ist im Sinne von ADR-002
nichts Besonderes: es ist eine Meinung, die durch dieselben Klammern läuft.

---

## Was ausdrücklich nicht gebaut wird

* **Kein Deep Learning.** Bei 124 bis vielleicht 1.000 Beobachtungen ist die
  Modellklasse nicht das Problem.
* **Keine automatische Merkmalsgenerierung.** Sie erzeugt Freiheitsgrade, die
  niemand zählt — derselbe Fehler wie beim verworfenen Journal-Skill (ADR-049).
* **Kein Online-Lernen im Livebetrieb**, bevor Phase 6 über Wochen sauber lief.

## Der ehrliche Erwartungswert

Nach ADR-045 bis ADR-048 sind in diesem Projekt vier Hypothesen geprüft und
vier gescheitert. Es gibt keinen Grund anzunehmen, dass ein Modell das ändert
— besonders nicht bei dieser Stichprobengröße. Der Wert des Vorhabens liegt
darin, die Frage **beantwortbar** zu machen: mit Labeling, das die
Handelsentscheidung abbildet, Validierung ohne Leck, und Kontrollen, die ein
Nein erzwingen können.

Ein Modell, das an M0 scheitert, hat mehr geliefert als eines, das an M5
vorbeikommt, weil niemand die Kontrollen gerechnet hat.
