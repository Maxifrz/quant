# Roadmap

> ## ▶ HIER WEITER
>
> ### Zustand nachsehen, nicht nachlesen
>
> Dieser Abschnitt **behauptet keinen Laufzeitzustand mehr**. Dreimal hat eine
> Prosa-Behauptung hier länger gestimmt als die Wirklichkeit — das Paper-Konto
> lief wochenlang nicht, während hier stand, es laufe (ADR-051); zwei Absätze
> desselben Blocks widersprachen sich (ADR-052); und zuletzt schrieb der
> tägliche Tick seinen Zustand auf einen Zweig, dessen Pull Request längst
> zusammengeführt war (ADR-059). Eine Behauptung veraltet still, ein Befehl
> nicht.
>
> **Der erste Befehl ist nicht optional: der Store ist beim Sitzungsstart
> leer.** `/data/*` ist nicht versioniert, der Container ist frisch, und jeder
> Befehl unten braucht Bars. Rund 20 Minuten, einmal je Sitzung:
>
> ```bash
> KRYPTO="BTC/USD,ETH/USD,LTC/USD,BCH/USD,ETC/USD,XLM/USD,LINK/USD"
> KRYPTO="$KRYPTO,ALGO/USD,ADA/USD,DOGE/USD,DOT/USD,SOL/USD,AVAX/USD,XRP/USD"
> uv run qt data pull --symbols "$KRYPTO" --tf 1d --since 2019-01-01
> uv run qt data pull --symbols "BTC/USD,ETH/USD" --tf 1h,4h --since 2019-01-01
> uv run qt data stocks --since 2019-01-01     # 13 ETFs, braucht TIINGO_API_KEY
> uv run qt data report                        # muss 39 Maerkte zeigen, alle "ok"
> ```
> **Zählen, nicht überfliegen.** Bis ADR-059 schrieb derselbe Befehl neun der
> vierzehn Krypto-Märkte und meldete keinen Fehler: Coinbase antwortet für ein
> Fenster vor der Notierung mit einer leeren Seite, und die galt als Ende der
> Historie. Behoben, aber die Gewohnheit bleibt richtig — `qt data pull` nennt
> jetzt außerdem, was nichts geliefert hat.
>
> ```bash
> uv run qt paper status --strategy macross --symbols BTC/USD --tf 1d
> uv run qt paper status --strategy macross --symbols ETH/USD --tf 1d
> ```
> Erwartet: „Status: laeuft", und **rund vier Fills im Jahr je Konto** —
> `macross` handelt selten, lange Strecken ohne jeden Fill sind der Normalfall
> und kein Hinweis auf einen Fehler. Steht dort „ANGEHALTEN", hat der
> Kill-Switch ausgelöst; das ist der einzige Fall, der eine Entscheidung
> braucht.
>
> Bis hierher stand „über Wochen hinweg **null Fills**". Das war die
> Beobachtung eines Kontos, das noch nie gehandelt hatte, formuliert als
> Erwartung — am 2026-09-03 sind beide Konten zum ersten Mal long gegangen,
> und die Zeile hätte das als Auffälligkeit gelesen.
>
> **Auf `Letzter verarbeiteter Bar` schauen, nicht nur auf „laeuft".** Am
> 2026-09-03 stand dort der 2026-09-02, und im ganzen Repo — auf `main` wie auf
> dem alten Sitzungszweig — lag kein Commit, der den Kontostand über den
> 2026-09-02T05:32Z hinausschreibt. Die Konten liefen also nicht seit über
> einem Tag, während Punkt 1 unten „läuft von selbst" versprach. Der eine Grund,
> der sich finden ließ, ist behoben (der tote Zweig, ADR-059); ob die Routine
> überhaupt feuert, sagt nur der nächste Tag.
>
> **Seit ADR-053 handeln die Konten Gewicht 1,0 statt 0,25.** Vorher formte die
> Risk-Engine mit den Portfolio-Defaults, und das Konto prüfte damit eine
> andere Strategie nach als die gemessene. Der Kill-Switch bleibt an;
> `--shape-risk` schaltet die Formung zurück, falls man den Portfolio-Pfad
> nachstellen will.
>
> ```bash
> bash scripts/paper_tick.sh    # sicher wiederholbar, sichert den Zustand ins Repo
> uv run qt trials              # Versuchszaehler der DSR -- steht bei 24
> uv run qt gate --strategy macross --tf 1d   # Gate 1, alle Kriterien auf einmal
> uv run qt ic --strategy crossmom --tf 1d    # Querschnitts-Rank-IC (ADR-058)
> uv run qt placebo shuffle --strategy <name> --tf 1d   # Negativkontrolle
> ```
> Gate 1 ist seit ADR-057 ein Programm, keine Prosa. **Seit ADR-069 fallen
> alle neun Strategien am Umschlagbudget** — auch `crossmom`, das vorher als
> einzige durchkam und dessen 2,9× eine stille Subvention des Rebalancing-
> Bandes waren (10,8× nach der Korrektur).
>
> **Das Budget ist trotzdem nicht die Hürde.** Rückwärts durch die
> Kostenidentität gerechnet, was sich jede Strategie *selbst* leisten kann
> (ADR-071, Datenstand 2026-09-04):
>
> | | Umschlag ist | OOS netto | OOS brutto | erlaubt |
> |---|---|---|---|---|
> | `trend` BTC | 15,2× | +0,160 | **+0,459** | 6,6× |
> | `macross` ETH | 8,7× | +0,276 | +0,403 | 5,0× |
> | `macross` BTC | 8,7× | +0,250 | +0,418 | 4,6× |
> | `elliott` BTC | 13,2× | +0,145 | +0,334 | 0,3× |
> | `hashribbon` BTC | 7,1× | +0,123 | +0,224 | **0,0×** |
> | `meanrev` ETH | 16,9× | −0,923 | −0,707 | **0,0×** |
>
> Keine darf 7×. Die 7 sind ein billiger Vorfilter vor dem Walk-Forward und
> keine Aussage über eine Strategie — `hashribbon` und `meanrev` liegen schon
> **ohne jede Kostenbelastung** unter 0,33, für sie hilft keine Frequenz.
> Und `macross` hätte bei 6,9× das Gate bestanden und wäre am Sharpe
> gescheitert.
>
> **Die schärfste Zahl im Dokument steht in der Brutto-Spalte:** die zwei
> besten Signale des Repos liegen 0,09 bzw. 0,13 über der Nachweisgrenze, und
> aus dieser Spanne muss die gesamte Ausführung bezahlt werden.
>
> ### Wofür das alles
>
> Das Ziel und der Weg dahin stehen in **`docs/ZIEL.md`**: echtes Geld,
> 12 Monate, besserer Calmar als Buy-and-Hold. Der Termin, der wirklich zählt,
> ist **2027-03-01 (Gate 1)** — bis dahin muss ein Kandidat alle Kontrollen
> bestanden haben, sonst lautet die Antwort „kein Edge gefunden".
>
> Die Zahl, die den Plan diktiert: bei 7,7 Jahren Historie und **5,1** effektiv
> unabhängigen Märkten ist ein **Sharpe ab 0,33** beweisbar (ADR-061).
> `macross` hat 0,25 — immer noch darunter.
>
> **Der billige Hebel ist damit gezogen.** Das Verbreitern der Datenbasis stand
> hier lange vor jeder Strategie-Idee, weil es rechenbar war: von 1,4 auf 5,1
> effektive Märkte hat die Schwelle von 0,67 auf 0,33 halbiert. Der nächste
> Schritt brächte 0,33 → 0,26 und bräuchte Anlageklassen, die es nicht gibt.
> Ab hier hilft nur noch ein stärkerer Edge.
>
> ### Der Stand in einem Satz
>
> **Neun Hypothesen geprüft, neun gescheitert — und alle neun Strategien haben
> jetzt eine Negativkontrolle, die keine besteht.** Dazu acht Kandidaten aus
> dem Research-Loop (fünf am 2026-09-03, drei am 2026-09-04), die zwar den Versuchszähler kosten, aber die Verdrahtung
> geprüft haben und nicht ihre Idee (ADR-065) — sie als geprüfte Hypothesen zu
> zählen wäre zu großzügig gegen uns selbst. LLM-Allokator
> **dreimal** (ADR-045/046/060), `hashribbon` (ADR-048), echtes ML (ADR-050),
> die Timeframe-Frage (ADR-047), zwei BTC-Mechanismen vor der ersten Codezeile
> (ADR-048), `macross` selbst (ADR-054) und `crossmom` (ADR-058).
>
> Der dritte Allokator-Lauf ist der, den dieser Block als Punkt 3 verlangt hat:
> `macross` mit im Korb, auf 1d, damit der Allokator etwas zu verteilen hat,
> das gewinnt. Ergebnis **Sharpe −1,50** gegen 0,00 (Gleichgewichtung) und
> +0,58 (Vol-Parität), bei 20 sauberen Aufrufen ohne einen einzigen Rückfall.
> Der Einwand „ein Allokator kann nicht verteilen, was nicht da ist" ist damit
> ausgeräumt und rettet ihn nicht (ADR-060).
>
> Die Kontrollen im Überblick, alle *Datenstand 2026-09-03*, 200 Ziehungen,
> gefordert waren 95 % (ADR-059):
>
> | | Perzentil | | Perzentil |
> |---|---|---|---|
> | `macross` | 75,5 % | `crossmom` | 45,0 % |
> | `trend` | 70,5 % | `crossrev` | 64,5 % |
> | `meanrev` | 38,5 % | `timesfm` | 66,5 % |
>
> `meanrev` liegt **unter** dem Median seiner eigenen Zufallsfassungen. Im
> Querschnitt über 26 Märkte kommen `trend` (−0,17) und `meanrev` (−0,20) auf
> negative Median-Sharpes.
>
> **`orderflow` hat seit ADR-064 auch eine: Perzentil 52,5 % auf 15m.** Damit
> haben alle neun Strategien eine Negativkontrolle, und keine besteht sie. Der
> Vorbehalt ist dort größer als sonst — bei Sharpe −42 beherrschen die
> Gebühren beide Seiten des Vergleichs, der Test ist gültig und fast blind.
>
> **Und die Zahl, auf der alles ruht, reproduziert nicht.** Aus einem frisch
> gezogenen Store liefert `macross` OOS-Sharpe **0,25** auf BTC und **0,28** auf
> ETH, nicht die 0,31/0,32, die hier bis ADR-059 standen. Die Fenstergeometrie
> erklärt nur ±0,02 davon; der Rest ist mit dem alten Store nicht mehr
> nachvollziehbar. Der Unterschied hat dieselbe Größenordnung wie der behauptete
> Effekt.
>
> Dazu ein vollständiger Code-Audit (ADR-053): neun Funde, davon fünf, die
> Zahlen verfälscht haben, ohne dass ein Test rot wurde.
>
> **Und drei Kennzahlfehler an einem Tag, keiner davon in einer Strategie.**
> ADR-066: `compute` las jede Verschlechterung einer negativen Kapitalkurve
> als Gewinn und meldete Sharpe +8,65 für ein ruiniertes Konto. ADR-067: die
> Suche nach der Gegenrichtung — vier Stellen exakt nachgerechnet, drei kleine
> pessimistische Effekte ohne Wirkung, und ein Befund *zugunsten* der Zahlen
> (kein risikofreier Zins im Sharpe, rund 0,10). ADR-068: `rebalance_order`
> kaufte für genau das Eigenkapital und ließ die Gebühr obendrauf laufen —
> 1,0056 Hebel im Median und negatives Cash in 53 % der Bars, bei einer
> Config, die „1.0 = kein Hebel" verspricht.
>
> **Die Reihenfolge ist die Lehre.** ADR-067 hat gezielt nach Fehlern gesucht
> und ADR-068 nicht gefunden — weil ein Hebel Mittelwert und Volatilität
> gleich skaliert und damit genau die Zahl in Ruhe lässt, nach der gesucht
> wurde. Gefunden hat ihn ein Blick auf einen Kontostand, der komisch aussah.
> Ein Audit findet, wonach es sucht.
>
> **Die Paper-Konten laufen trotzdem weiter, und zwar genau deswegen.** Die
> historischen Daten können die fehlenden unabhängigen Beobachtungen nicht
> liefern; Vorwärtszeit kann es. Was sich geändert hat, ist der Anspruch: die
> Konten prüfen nicht nach, ob eine belegte Strategie hält — sie sammeln die
> Evidenz, die noch fehlt.
>
> ### Was als Nächstes Sinn ergibt
>
> 1. **Der Tick feuert und meldet Erfolg, ohne stattzufinden.** Die Routine
>    `Paper-Tick macross BTC+ETH (taeglich)` läuft täglich 01:00 UTC in einer
>    frischen Sitzung. Am 2026-09-04 und am 2026-09-06 ist sie gelaufen und
>    scheiterte vor der ersten Zeile: der Container hatte **keine
>    Arbeitskopie**. Kein `.git`, kein `scripts/paper_tick.sh`, nichts zu
>    ticken. Der Lauf vom 2026-09-06 steht in der Routinen-Historie trotzdem
>    als `SUCCEEDED`.
>
>    **Am 2026-09-06 nachgesehen statt vermutet** (ADR-073). Zwei Ursachen,
>    beide belegt aus der Trigger-Konfiguration:
>
>    - `session_request.config.sources` ist **leer**. Der Routine ist kein
>      Repository hinterlegt; diese Sitzung hier hat eins, die gefeuerte nicht.
>      Über die MCP-Oberfläche lässt sich das **nicht** setzen — `update_trigger`
>      kennt nur Name, Zeitplan, Zustand, Modell und Prompt. Der dauerhafte
>      Fix gehört in die Routinen-Oberfläche und ist Handarbeit.
>    - Im Prompt stand: *„Falls das Repo im Container fehlt oder data/ohlcv
>      leer ist: das ist erwartet und kein Fehler."* Der Satz vermengte einen
>      **kalten Datenspeicher** (tatsächlich normal, das Skript zieht nach) mit
>      einem **fehlenden Repository** (fatal, es gibt nichts auszuführen). Er
>      hat aus dem Ausfall eine Erfolgsmeldung gemacht.
>
>    Der Prompt ist korrigiert: ein fehlendes Repository ist jetzt ein Fehler,
>    und Erfolg wird an `Letzter verarbeiteter Bar` gemessen, nicht am
>    Exit-Code.
>
>    **Am 2026-09-07 getestet, halb bestanden.** Der Ausfall ist jetzt
>    sichtbar — 2 Minuten und ein gemeldeter Fehler statt 5 h 38 min und
>    „SUCCEEDED". Die eingebaute Selbstheilung (`add_repo`) trägt dagegen
>    nicht: eine Sitzung ohne hinterlegte Quelle kommt so nicht an das
>    Repository. Sie ist wieder heraus; an ihrer Stelle steht der Satz, den
>    derjenige liest, der den Fehler bekommt.
>
>    **Damit ist der Weg über den Prompt ausgereizt.** Er macht einen Ausfall
>    sichtbar, er ersetzt keine fehlende Quelle. Solange `sources: []` bleibt,
>    tickt die Routine nicht und die Konten hängen an Handarbeit.
>
>    Damit ist es der **dritte** verschiedene Grund, aus dem derselbe Tick
>    nicht ankommt: toter Zweig, beschönigte Push-Meldung (beide ADR-059),
>    jetzt eine fehlende Quelle plus ein Satz, der sie für normal erklärte.
>    Alle drei sahen nicht nach einem Fehler aus. **Das ist das Muster, nicht
>    der Zufall.**
>
>    `scripts/paper_tick.sh` erkennt seit 2026-09-04 auch den Fall, dass
>    dieser Zweig **zusammengeführt** ist: dann geht der Kontostand nach
>    `main`, weil die nächste frische Sitzung dort liest. Das ist ADR-059 zum
>    dritten Mal — dort war der Zweigname fest verdrahtet, hier ist er richtig
>    und trotzdem tot.
>
>    **Beide Konten sind am 2026-09-04 neu gestartet** (ADR-068). Sie trugen
>    den Hebel aus dem alten Sizing: am 2026-09-02 long gegangen mit 0,65 % zu
>    großer Position, Cash −650,31 (BTC) und −656,55 (ETH). Das hätte sich
>    nicht von selbst korrigiert — die Abweichung liegt innerhalb des
>    Rebalancing-Bands von 5 %, also wäre sie bis zum nächsten echten
>    Ausstiegssignal stehen geblieben, bei `macross` möglicherweise Monate.
>    Ein Zustand, den eine Spot-Börse ablehnt, taugt nicht als Beweismittel
>    für Phase D, egal wie lange er läuft. Preis: zwei Tage Vorwärtszeit und
>    ein Fill. Der alte Stand liegt in Commit `e658ee8`.
>
>    **Der erste Einstieg ist am 2026-09-06 erfolgt, und er bestätigt
>    ADR-068 im Vorwärtsbetrieb.** Vorhergesagt war Cash 0,00 bzw. 0,84 aus
>    einem Replay; gemessen wurde **−0,00 (BTC) und 0,00 (ETH)**. Vor der
>    Korrektur ergab derselbe Einstieg −650,31 und −656,55. Das ist der Punkt,
>    an dem Phase D eine Divergenz gefangen hätte, wenn noch eine da wäre.
>
>    | | BTC/USD | ETH/USD |
>    |---|---|---|
>    | Menge | +1,246988 @ 79.714,96 | +40,443663 @ 2.457,83 |
>    | Cash danach | **−0,00** | **0,00** |
>    | Gebühr | 596,42 | 596,42 |
>
>    `Letzter verarbeiteter Bar` steht auf **2026-09-07** — von Hand
>    nachgeholt, nicht von der Routine, jetzt zum dritten Mal. Die Zahl allein
>    beweist nichts; sie beweist nur zusammen mit der Frage, wer sie bewegt
>    hat. **Der Test ist gelaufen und die Antwort steht:** nur die hinterlegte
>    Quelle hilft.
> 2. **Einen Edge über 0,33 suchen — der Datenhebel ist ausgereizt.** Phase A
>    ist am 2026-09-03 bestanden (ADR-061): zwölf Reihen aus Volatilität,
>    Zinsdifferenzen, Agrar, Erdgas, Kupfer, Immobilien und Japan drücken ρ̄ von
>    0,26 auf 0,18 und heben n_eff auf 5,1. Die nächste Verdopplung der Märkte
>    brächte 0,012 an ρ̄ und damit fast nichts. Was jetzt fehlt, ist nicht mehr
>    die Datenlage, sondern ein Signal.
> 3. ~~**Eine Positionsgrößen-Schicht für generierte Kandidaten festlegen.**~~
>    **Erledigt am 2026-09-04 (ADR-069):** proportional auf das Bruttobudget
>    skalieren. Vol-Targeting und 1/n sind vorab verworfen und begründet, die
>    Grenze ist keine Option des Loops — wer sie ändert, hinterlässt einen
>    Diff (wie bei den Gate-Schwellen, ADR-057).
>
>    Die Schicht allein reichte nicht: danach handelte der Kandidat fast nicht
>    mehr, Brutto 0,502 statt 1,0. Die Ursache war ein **Kategorienfehler im
>    Rebalancing-Band** — es war ein Anteil des Eigenkapitals, beschreibt aber
>    eine Toleranz um eine Position. Bei 39 Märkten war es damit 1,95× der
>    eigenen Position, also 39-mal lockerer als für eine Ein-Symbol-Strategie.
>    Dieselbe Fehlerfamilie wie ADR-053 und ADR-065, drittes Auftreten.
> 4. **Research-Loop** — am 2026-09-04 gelaufen, drei Kandidaten, keiner
>    besteht (ADR-072). Der Versuchszähler steht damit auf 24, und jeder
>    weitere Lauf verschärft die DSR-Schwelle dauerhaft für alle künftigen
>    Kandidaten (ADR-032). Gemessen: 21 → 24 Versuche heben den erwarteten
>    besten Sharpe aus **reinem Rauschen** von 1,922 auf 1,980.
>
>    **Der nächste billige Vorfilter ist die Kritik-Stufe.** Ein Kandidat mit
>    307× Umschlag pro Jahr ist vollständig durch Sandbox, Kritik und
>    Sanity-Check gelaufen; `critic_unrealistic_turnover` steht als Feld in
>    der Registry und hat nicht angeschlagen. Das ist billiger zu reparieren
>    als jede weitere Idee zu prüfen. In dieser Umgebung ist
>    `NVIDIA_API_KEY` gesetzt und ein Gate-Lauf über `--provider nim` kommt
>    durch (ADR-060); der Blocker aus `docs/ZIEL.md` Phase C.2 gilt hier nicht
>    mehr.
>
>    **Ein Lauf gegen die Stubs kostet nichts** — er schreibt nach
>    `registry_stub.duckdb` und nicht in die Registry (ADR-057). Damit lässt
>    sich die Kette über alle 39 Märkte prüfen, ohne einen Versuch auszugeben.
>    Die Entscheidung, wie viele Kandidaten ein echter Lauf erzeugt, gehört
>    einem Menschen: sie ist die einzige in diesem Repo, die sich nicht
>    zurücknehmen lässt.
>
> **Der LLM-Allokator steht nicht mehr auf dieser Liste.** Er stand hier als
> Punkt 3 mit einem berechtigten Vorbehalt; der ist geprüft und erledigt
> (ADR-060). Ein vierter Lauf wäre eine weitere Konfiguration auf denselben
> Daten — was fehlt, ist ein zweites Testfenster, und das liefert nur eine
> breitere Datenbasis oder Vorwärtszeit.
>
> **Order-Flow bleibt herabgestuft — die Begründung dafür war aber falsch.**
> Hier stand, ein Jahr Trades wären „rund 170 MB, und GitHub lehnt Dateien über
> 100 MB ab". Gemessen sind es 1,26 MB am Tag, also 460 MB im Jahr, und die
> 100-MB-Grenze fiele schon am Tag 79. Beides ist gleichgültig: die Daten
> gehören gar nicht ins Repository, weil Kraken sie auf Zuruf nachliefert
> (ADR-063).
>
> Das eigentliche Hindernis war ein anderes und stand nirgends: `write_trades`
> schrieb die ganze Datei bei jedem Anhängen neu — rund **84 GB geschriebene
> Bytes für 460 MB Ergebnis**. Behoben durch Tagesteile; ein abgeschlossener
> Tag wird nie wieder angefasst.
>
> Was übrig bleibt und die Herabstufung trägt: Order Flow lebt auf 4h, und
> ADR-047 hat für 4h gemessen, dass die Gebühren dort *jede* getestete
> Strategie von positiv auf −0,65 bis −1,60 Sharpe ziehen — Gate 1 lässt seit
> ADR-056 ohnehin nur 1d oder gröber zu.
>
> **Ein falscher Grund für eine richtige Entscheidung ist keine harmlose
> Ungenauigkeit.** Er wird zitiert, und irgendwann trifft jemand auf seiner
> Grundlage eine andere Entscheidung.
>
> **Was ausdrücklich nicht empfohlen wird:** noch eine Strategie-Idee. Nicht
> weil Ideen schlecht wären, sondern weil dieses Projekt gerade achtmal
> gezeigt hat, dass es sie zuverlässig widerlegt — und jede kostet einen
> Versuch im Nenner.

---

## Konvention für jede Zahl in diesem Dokument

**Jede Ergebnistabelle nennt ihren Datenstand.** Ohne ihn driftet sie mit
jedem `qt data pull` still weiter: der In-Sample-Faktor von `macross` stand
hier als 15,2 und war beim Nachrechnen 16,0, Buy & Hold als 16,8 und war
20,1 — nichts davon war je falsch, nur undatiert (ADR-053). Dieselbe Lehre
wie bei ADR-051/052, eine Ebene tiefer: eine Behauptung ohne Schnitt veraltet,
ohne dass es jemandem auffällt.

**Alle Renditezahlen vor dem 2026-09-04 liegen rund 0,9 % relativ zu hoch.**
Bis ADR-068 kaufte `rebalance_order` für genau das Eigenkapital und ließ die
Gebühr obendrauf laufen; das Konto lief mit 1,0056 Hebel. Die *Sharpe*-Zahlen
sind davon unberührt — ein Hebel skaliert Mittelwert und Volatilität gleich —
und damit auch jede Aussage, die an ihnen hängt. Die Tabellen unten sind
deshalb **nicht** rückwirkend geändert: sie tragen ihren Datenstand, und ein
nachträglich korrigiertes Feld ohne neuen Lauf wäre eine Zahl, die niemand
mehr nachrechnen kann.

---

## Die erste Strategie, die Geld verdient (ADR-035)

```bash
uv run qt backtest --strategy macross --symbol BTC/USD --tf 1d
uv run qt wf --strategy macross --symbol BTC/USD --tf 1d --train 1000 --test 250 --embargo 20
```

Zwei Hebel, die das Projekt nie gezogen hatte: **Tagesbasis statt 4h** (66 statt
889 Trades — Frequenz ist Kosten) und **long/flach statt long/short** (die
Gegenrichtung kämpft gegen die stärkste Drift im Datensatz).

**Out-of-Sample, Fenster 2021-10 bis 2026-08** *(Datenstand 2026-08-24)*:

| | macross | Buy & Hold |
|---|---|---|
| Faktor | **1,25** | 1,03 |
| Sharpe | **0,31** | 0,27 |
| Max Drawdown | **−51,0%** | −76,7% |

Nachgerechnet am 2026-09-01 (7 OOS-Fenster, 1000/250/20): Sharpe **0,31** auf
BTC und **0,32** auf ETH.

> **Nachgerechnet am 2026-09-03 aus einem frisch gezogenen Store: 0,25 auf BTC
> und 0,28 auf ETH.** Dieselben Fenster, dieselben Parameter, dieselbe
> Symbolquelle — nur ein Store, der neu von Coinbase geholt wurde, weil
> `/data/*` nicht versioniert ist. Verschiebt man den Anker des Walk-Forward um
> 1 bis 10 Bars, wandert der BTC-Wert zwischen 0,229 und 0,266; die
> Fenstergeometrie erklärt die Differenz also nicht. Woran es sonst liegt, ist
> ohne den alten Store nicht mehr feststellbar (ADR-059). Die Zahl, auf der die
> Auswahl von `macross` fürs Paper-Trading ruht, ist damit **nicht
> reproduzierbar**, und der Unterschied ist so groß wie der behauptete Effekt.

20 von 25 Gitterpunkten verdienen Geld, und das Muster **repliziert auf ETH
ohne Neuanpassung** (dort Faktor 1,00 gegen 0,49 bei Buy & Hold).

**Trotzdem: DSR über 0,95 bei null von 25 Punkten.** Bei Sharpe 0,64 über 1.751
Tagesbars liegt der t-Wert bei 1,4 — man kann einen Sharpe von 0,6 mit 4,8
Jahren Tagesdaten nicht beweisen. Das ist die Datenmenge, nicht die Strategie.

Damit ist `macross` der erste Kandidat für **Phase 6 (Paper-Trading)** — nicht
weil sie bewiesen wäre, sondern weil Paper-Trading genau die fehlenden
Beobachtungen sammelt und ihr Risikoprofil den Irrtum billig macht.

---

## Phase 6 — Paper-Trading (ADR-037, ADR-038)

```bash
uv run qt paper run --strategy macross --symbols BTC/USD --tf 1d
uv run qt paper status --strategy macross --symbols BTC/USD --tf 1d
uv run qt paper reset-killswitch --strategy macross --symbols BTC/USD --tf 1d --note "..."
```

**Kein Daemon.** `run_paper_tick` ist ein einzelner, sicher wiederholbarer
Aufruf — diese Sitzung hat mehrfach gezeigt, dass Hintergrundprozesse in
dieser Umgebung Container-Neustarts nicht überstehen. Bewiesen, nicht nur
behauptet: derselbe Datensatz, einmal in einem Tick und einmal mit einem
erzwungenen Neustart nach *jedem einzelnen* Bar, ergibt bitgleiche Konten.

**Wiederverwendet statt neu gebaut:** `SimBroker` (Docstring versprach das
seit Phase 1), `qt.portfolio.risk.RiskEngine` als Kill-Switch (seit Phase 2),
`LiveClock` (seit Phase 0). Neu ist nur der Zustands-Cursor
(`PaperState.last_processed_ts`) und die Erkenntnis, die ihn nötig machte.

**Gefunden im ersten echten Lauf gegen Coinbase:** `fetch_ohlcv` gab einen
Bar zurück, dessen Close-Zeit einen Tag in der Zukunft lag — die gerade erst
offene Tageskerze. `run_paper_tick` verwirft jeden Bar mit `close_ts > now`
jetzt vollständig, bevor er den Feature-Store erreicht (ADR-038). Der
Backtest-Pfad war davon nie betroffen — dort sind Bars immer längst
geschlossen.

**Warum `macross` und nicht `orderflow` oder `elliott`:** `macross` ist der
einzige Kandidat mit positiver Out-of-Sample-Kennzahl, auf BTC und auf ETH
ohne Neuanpassung. *Nicht* „mit echter Evidenz" — so stand es hier, und
ADR-054 hat es widerlegt: die Permutationskontrolle ist nicht bestanden.
Es bleibt der beste verfügbare Kandidat, nicht ein belegter.

Seit ADR-059 gilt derselbe Satz für alle: acht der neun Bibliotheksstrategien
haben eine Negativkontrolle, keine besteht sie. `macross` liegt mit Perzentil
75,5 % im Mittelfeld dieses Feldes — schlechter als nichts ist es nicht, ein
Beleg aber auch nicht.

---

## Prinzip der Phaseneinteilung

Jede Phase endet mit **etwas Sichtbarem** — einem Chart, einer Zahl, einem Report —
nicht mit "Infrastruktur fertig". Jede hat **einen Befehl**, der sie vorführt.
Man kann nach jeder Phase aufhören und hat etwas Funktionierendes.

---

## ✅ Phase 0 — Skelett + Daten

- `pyproject.toml` (uv), Paketstruktur, Docs
- `qt.data.ingest` — OHLCV via ccxt gegen Coinbase Exchange, paginiert, UTC
- `qt.data.store` — Parquet, partitioniert nach `symbol/timeframe`
- `qt.data.integrity` — Lücken, Duplikate, Monotonie, OHLC-Plausibilität

**Vorführen:** `uv run qt data pull` → `uv run qt data report`

## ✅ Phase 1 — Backtest-Engine + zwei Baseline-Strategien

- `qt.core` — Clock, Events, Typen
- `qt.features` — Point-in-Time-Feature-Store mit `LookaheadError`
- `qt.backtest.engine` — event-getriebener Bar-Loop
- `qt.backtest.costs` — Fees + Spread + Slippage, pessimistische Defaults
- `qt.backtest.broker_sim` — Fills auf dem *nächsten* Bar-Open
- `qt.strategy.library` — Donchian-Breakout, z-Score-Mean-Reversion
- `qt.report.tearsheet` — Kennzahlen + Equity-Curve-PNG inkl. Buy-&-Hold

**Vorführen:** `uv run qt backtest --strategy trend --symbol BTC/USD --tf 4h`

> Erwartungsmanagement: Diese beiden Strategien sollen *nicht* profitabel sein.
> Sie sind Testinstrumente für die Engine. Ob etwas Geld verdient, entscheidet
> frühestens Phase 2.

### Ergebnisse des ersten echten Laufs (BTC/USD + ETH/USD, 2019–2026)

*Datenstand 2026-08-24. Datenlage: 7,6 Jahre, 99,97% Abdeckung, keine
kaputten Bars.* Alle Zahlen sind **In-Sample** und wandern mit dem Datenstand
— `macross` steht am 2026-09-01 bei Faktor 16,0 / Sharpe 1,04, Buy & Hold bei
20,1 / 0,94. Die belastbaren Zahlen stehen weiter oben, out-of-sample.

Nachgezogen am 2026-09-02: der Gebührensatz im Kostenmodell stand auf der
falschen Zeile der Coinbase-Staffel (40 statt 60 bps, ADR-056). Der Round-Trip
ist von 90 auf 130 bps gestiegen, **alle vier Zeilen wurden dadurch
schlechter**, und die alten Zahlen standen bis dahin hier, als seien sie
aktuell. Nachrechnen: `uv run qt costs --symbol BTC/USD --tf 4h`.

| Lauf | Ergebnis (130 bps) | vorher (90 bps) | Buy & Hold |
|---|---|---|---|
| `trend` BTC/USD 4h | Faktor 0,14 · Sharpe −0,44 | 0,46 · −0,06 | Faktor 17,4 · Sharpe 0,92 |
| `meanrev` ETH/USD 1h | Faktor 0,00 · Sharpe −2,37 | 0,00 · −2,08 | Faktor 14,6 · Sharpe 0,84 |
| `elliott` BTC/USD 4h | Faktor 0,01 · Sharpe −0,87 | 0,03 · −0,68 | Faktor 17,4 · Sharpe 0,92 |
| **`macross` BTC/USD 1d** | **Faktor 14,15 · Sharpe 1,00 · MaxDD −58%** | 15,2 · 1,03 | Faktor 16,8 · Sharpe 0,91 · MaxDD −77% |

Die letzte Zeile ist die interessante: `macross` verliert durch 40 bps mehr
Round-Trip-Kosten **0,03 Sharpe**, `trend` auf 4h verliert 0,38. Das ist der
Frequenzeffekt aus ADR-056 in zwei Zahlen.

Beide verlieren deutlich — wie erwartet. Die Diagnose ist aber unterschiedlich, und
genau das ist der Wert dieser Phase:

- **`trend` scheitert an den Kosten, nicht am Signal.** Ohne Gebühren macht dieselbe
  Strategie Faktor 6,44, bei Maker-Gebühren 4,04, bei Coinbase-Taker-Gebühren 0,46.
  Details in ADR-009 — die Zahlen reproduzieren bis heute, aber sie gelten für
  **4h**. Auf 1d kostet dieselbe Annahme nur 0,08 bis 0,23 Sharpe (ADR-056).
  Nachzusehen mit `uv run qt costs --symbol BTC/USD --tf 1d`.
- **`elliott` scheitert ebenfalls am Signal** (ADR-033). Ohne jede Gebühr bleibt
  Faktor 0,14 bei Sharpe −0,25; mit Kosten 0,03. Die Wellenzählung findet Muster,
  aber die Muster sagen nichts über den nächsten Bar. Walk-Forward: 7 von 17
  Fenstern positiv.
- **`meanrev` scheitert am Signal.** Auch ohne jede Gebühr bleibt Faktor 0,23
  (long-only) bzw. 0,01 (mit Short). Naive Mean-Reversion, die in einem Bullenmarkt
  Rallyes shortet, ruiniert das Konto — das ist kein Kostenproblem.

Dass die Engine dabei korrekt rechnet, ist separat abgesichert: eine Immer-Long-
Strategie ohne Kosten trifft Buy-&-Hold auf 0,54% genau, mit genau einem Trade
(`tests/test_engine.py`).

---

## ✅ Phase 2 — Portfolio + Walk-Forward

- `qt.portfolio.base` — Verträge: `Allocator`, `AllocationContext`, `RiskLimits`, `combine()`
- `qt.backtest.portfolio_engine` — mehrere Strategien, gemischte Timeframes, ein Konto
- `qt.backtest.walkforward` — rollierendes Fenster mit Purging und Embargo
- `qt.portfolio.baselines` — Equal-Weight, Vol-Parity, Best-Single, Fixed
- `qt.portfolio.risk` — Vol-Targeting, Symbol- und Brutto-Caps, Drawdown-Kill-Switch

**Vorführen:**
```bash
uv run qt wf --strategy trend --symbol BTC/USD --tf 4h --train 3000 --test 800 --embargo 50
uv run qt portfolio --strategies trend,meanrev --symbols BTC/USD,ETH/USD --tf 4h
uv run qt allocators
```

### Ergebnisse

**Walk-Forward `trend` BTC/USD 4h** (17 Fenster, Train 3000 / Test 800 / Embargo 50):
OOS-Gesamtrendite −71,94%, Sharpe −0,37, **4 von 17 Fenstern mit positivem Sharpe**.
Die In-Sample-Zahlen aus Phase 1 waren also nicht zu pessimistisch, sondern zu
optimistisch. Genau dafür gibt es Walk-Forward.

**Die Risk-Engine ist die wirksamste Komponente im System** (ADR-013):

| Lauf | Gesamtrendite | Vola p.a. | Max Drawdown |
|---|---|---|---|
| `vol_parity`, ohne Risk-Engine | −99,68% | 32,8% | −99,72% |
| `equal_weight`, mit Risk-Engine | −16,90% | 3,7% | −20,11% |

Identische Strategien und Daten — der Unterschied ist allein die Risikoschicht.
Sie macht schlechte Strategien nicht gut, aber sie sorgt dafür, dass man einen
Fehler überlebt. Das ist der empirische Beleg dafür, dass "Allokator schlägt
vor, Risk-Engine entscheidet" richtig herum gebaut ist.

## 🟡 Phase 3 — LLM-Allokator (gebaut, Wirksamkeit ungeprüft)

- `qt.llm.schemas` — pydantic-Modelle; erfundene Strategie-Labels werden
  verworfen, nicht geraten
- `qt.llm.briefing` — das Blind Briefing (ADR-017): anonym, datumsfrei,
  gerundet, bitgleich für denselben Zustand
- `qt.llm.cache` — Key aus Briefing + Systemprompt + Modell + Effort +
  Schema-Version; macht Backtests reproduzierbar und beim zweiten Lauf gratis
- `qt.llm.client` — strukturierte Ausgaben über `output_format`,
  Prompt-Caching des eingefrorenen Systemprompts, `StubClient` für Läufe ohne
  API-Zugang
- `qt.portfolio.llm_allocator` — jeder Fehler wird zu Gleichgewichtung
  (ADR-018), `AllocatorTelemetry` macht stille Rückfälle sichtbar
- `qt.portfolio.gate` — der Out-of-Sample-Vergleich gegen die Baselines

**Vorführen:**
```bash
uv run qt alloc --compare-baselines            # das Gate aus ADR-004
uv run qt allocators
```

### Was geprüft ist und was nicht

**Geprüft** (31 Tests, keiner ruft ein Modell auf): dass ein *schlechtes* Modell
das System nicht beschädigen kann. Ausfallender Client, halluziniertes Label,
Gewicht 1e9, Antwort die sich zu null summiert, Cache mit falschem Modell — all
das ist abgedeckt.

**Nicht geprüft:** ob ein *gutes* Modell das System verbessert. In der
Bauumgebung gab es keinen API-Schlüssel (ADR-019). Der `StubClient`
gleichgewichtet und ist damit per Konstruktion identisch zur
Equal-Weight-Baseline — er beweist die Verdrahtung, nicht die Idee.

## ✅ Phase 4 — Pfad-Simulation

- `qt.sim.base` — `PathEnsemble`, `PathGenerator`, gewichtete Quantile. Das
  Interface lässt die Antwort "ein einzelner bester Pfad" gar nicht erst zu.
- `qt.sim.bootstrap` — stationärer Block-Bootstrap (Politis/Romano,
  geometrische Blocklängen, zirkulär) plus i.i.d.-Bootstrap als Kontrast
- `qt.sim.regimes` — GARCH(1,1) mit t-Innovationen für Vol-Pfade, HMM für
  Regimewechsel
- `qt.sim.scenarios` — Szenario-Priors: das LLM gewichtet Möglichkeiten,
  es prognostiziert keinen Preis (ADR-024)
- `qt.sim.objective` — Median-Rendite maximieren unter CVaR-Grenze auf dem
  **Drawdown** (ADR-025), Kosten eingerechnet, "kein Trade" ist ein
  mögliches Ergebnis

**Vorführen:**
```bash
uv run qt sim --symbol BTC/USD --tf 4h --paths 10000 --horizon 180
uv run qt sim --generator garch --until 2024-12-31
uv run qt sim --scenarios --stub          # LLM-Priors, ohne API-Zugang
```

### Was der erste echte Lauf zeigt

Das System reagiert auf die Daten, nicht auf eine Meinung:

| Fenster | BTC-Entwicklung | Ergebnis |
|---|---|---|
| letzte 2000 Bars | −44,8% | **kein Trade** |
| bis Ende 2024 | bullisch | Exposure 65–90% je nach Risikobasis |

Und die Modellwahl macht einen Unterschied. Gleiche Daten, gleicher Horizont,
gleiche CVaR-Grenze — nur der Generator variiert:

| Generator | Exposure | zulässige Gitterpunkte |
|---|---|---|
| `iid_bootstrap` | 80% | 16 von 20 |
| `hmm` | 80% | 16 von 20 |
| `stationary_bootstrap` | 75% | 15 von 20 |
| `garch` | **35%** | 7 von 20 |

GARCH mit t-Innovationen erzeugt die fettesten Tails und lässt deshalb am
wenigsten Exposure zu. Welches Modell recht hat, entscheidet diese Tabelle
nicht — sie zeigt nur, dass die Wahl folgenreich ist und deshalb begründet
werden muss.

## ✅ Phase 5 — Research-Loop

- `qt.research.sandbox` — AST-Whitelist statt Blockliste, plus Fassaden für
  `np` und `ta`, damit eine erlaubte *Syntax* nicht doch eine verbotene
  *Methode* erreicht (ADR-029)
- `qt.research.dsr` — Deflated Sharpe Ratio gegen alle je getesteten
  Kandidaten (ADR-005)
- `qt.research.registry` — DuckDB; der Versuchszähler ist eine abgeleitete
  Abfrage, kein gepflegtes Feld (ADR-032)
- `qt.research.generator` — Briefing **ohne jede** datenabgeleitete Zahl
- `qt.research.critic` — adversariale Kritik als billiger Vorfilter vor dem
  teuren Backtest (ADR-031)
- `qt.research.screening` — Sanity-Check, Walk-Forward, DSR
- `qt.research.loop` — die Orchestrierung samt Trichter-Telemetrie

**Vorführen:**
```bash
uv run qt research --generate 3 --screen --stub
uv run qt research --show <id>          # voller Audit-Pfad, nur lesen
```

### Die Reihenfolge ist nach Kosten sortiert

```
Generator (LLM)  ->  Sandbox (gratis)  ->  Kritik (billig)  ->  Walk-Forward + DSR (teuer)
```

Jede Stufe, die früher ablehnt, spart alle folgenden. Der Sanity-Check
zwischen Kritik und Walk-Forward verwirft nur, was technisch nichts liefert —
eine In-Sample-Schwelle auf die Rendite wäre eine Vorauswahl auf denselben
Daten, gegen die anschließend out-of-sample geprüft wird.

### Was der erste Stub-Lauf zeigt

Zwei Läufe hintereinander, je über BTC/USD 4h ab 2021:

| | Lauf 1 | Lauf 2 |
|---|---|---|
| Versuchszähler vorher | 0 | 3 |
| erzeugt / gescreent | 3 / 3 | 2 / 2 |
| DSR bestanden | 0 | 0 |
| Versuchszähler nachher | 3 | **5** |

Der Zähler überlebt den Prozesswechsel — die DSR des vierten Kandidaten
rechnet gegen 4 Versuche, nicht wieder gegen 1. Genau das ist der Unterschied
zwischen einem Overfitting-Schutz und einem, der bei jedem Neustart vergisst.

Dass alle durchfallen, ist der Normalfall: der Stub-Generator liefert
absichtlich schwache z-Score-Reversionen mit Sharpe zwischen −4 und −6.

### Promotion bleibt Handarbeit

Es gibt bewusst **keinen** Befehl, der einen bestandenen Kandidaten nach
`strategy/library/` schreibt. `--show <id>` druckt ihn, kopieren muss ein
Mensch, `--mark-promoted <id>` hält es nur fest. Eine automatische Übernahme —
auch hinter einer Bestätigung — wäre der schleichende Weg, die Freigabe
abzuschaffen.

## ✅ Phase 6 — Paper-Trading

- `qt.live.runner` — `run_paper_tick`, ein sicher wiederholbarer Aufruf statt
  eines Daemons (ADR-037). `SimBroker` und `qt.portfolio.risk.RiskEngine`
  wiederverwendet, nicht neu gebaut.
- `qt.live.state` — der einzige persistierte Zustand: Broker-Konto plus ein
  Zeitstempel-Cursor. Feature-Store und Uhr werden bei jedem Tick aus der
  durablen Bar-Historie neu aufgebaut.
- `qt.live.killswitch` — manuelles Zurücksetzen des Kill-Switches, mit
  Begründung in der Historie.
- `qt.report.daily` — Tagesreport als Text, Kill-Switch-Status zuerst.
- **`qt.live.reconcile` bewusst nicht gebaut:** Soll- vs. Ist-Vergleich setzt
  zwei unabhängige Quellen voraus. Mit nur einem `SimBroker` gibt es noch
  nichts, wogegen abgeglichen werden könnte — das wird erst mit einem
  zweiten, echten Broker in Phase 7 zu einer sinnvollen Prüfung.
- Gefunden im ersten echten Lauf: eine noch offene Tageskerze wurde von der
  Exchange als geschlossen ausgegeben (ADR-038). Behoben, bevor produktiv
  gelaufen wurde.

**Noch zu tun, keine Code-Aufgabe mehr:** über Wochen laufen lassen und
Live-vs-Backtest-Divergenz messen (siehe ROADMAP-Marker oben).

**Vorführen:**
```bash
uv run qt paper run --strategy macross --symbols BTC/USD --tf 1d
uv run qt paper status --strategy macross --symbols BTC/USD --tf 1d
```

## 🟡 Phase 7 — Live (gebaut, unverdrahtet — die Entscheidung steht noch aus)

- `qt.live.broker_ccxt` — echte Orders, entschärft per Default. Scharf nur mit
  **zwei** unabhängigen Schaltern: `scharf=True` im Aufruf *und*
  `QT_LIVE_SCHARF=ja` in der Umgebung. Harte Grenzen je Order, je Position,
  brutto und je Tick — geprüft im Broker selbst, nicht nur oben im Aufrufpfad,
  und **geworfen statt gekappt**.
- `qt.live.sizing` — Zielgewicht zu Ordermenge, gegen die Grenzen der Börse.
  Was darunter fällt, wird nicht ungenau ausgeführt, sondern gar nicht — mit
  Grund im Ergebnis statt als stille Null.
- `qt.live.reconcile` — Soll gegen Ist. Seit ADR-037 aufgeschoben, weil es
  ohne zweite Quelle keinen Gegenstand hatte; den gibt es jetzt. **Meldet nur**
  — es gibt keinen Befehl, der den lokalen Zustand nachzieht, und ein Test
  hält das fest.
- `Zugang` — Schlüssel aus der Umgebung, in keinem `repr`, nirgends
  gespeichert.

**Vorführen:**
```bash
uv run qt live status      # Kontostand von der Boerse, liest nur
uv run qt live groesse     # was von einem Zielgewicht bei diesem Kapital bleibt
uv run qt live reconcile   # lokaler Zustand gegen Boersenbestand
```

**`qt live tick` ist absichtlich nicht verdrahtet** und endet mit Exit 1. Was
fehlt, ist kein Code, sondern ein Kandidat, der Gate 1 besteht — neun
Strategien geprüft, keine hat eine Negativkontrolle bestanden (ADR-059,
ADR-062). Das bleibt eine eigene Entscheidung mit echtem Geld, keine
Fortsetzung der Bauarbeit.
