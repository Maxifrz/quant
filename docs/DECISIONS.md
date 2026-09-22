# Entscheidungs-Log (ADR)

Kurze Begründungen, damit nichts im Kopf gehalten werden muss.
Neueste zuerst. Format: Entscheidung — Warum — Konsequenz.

---

## ADR-079 — Die halb behobenen Befunde zu Ende gebracht
**Datum:** 2026-09-22

Nach ADR-078 kam die Frage, ob jeder Befund behoben ist. Ehrliche Antwort:
nein. Zwei Befunde waren nur halb behoben, und aus dem TradingAgents-Vergleich
(ADR-077) standen noch zwei offene Punkte da. Dieser Eintrag schließt alle vier.

### 1. `openai` ist Pflichtpaket, und der Grund aus ADR-074 ist jetzt reproduziert

ADR-078 hatte den Weg `uv sync --extra nim` in die Fehlermeldung geschrieben.
Das behob das Symptom, nicht die Ursache. **`uv run` synchronisiert nur die
Kernabhängigkeiten.** Jede frische Sitzung, auch jede Routine, startet also
ohne `openai`, und `--provider nim` ist dort tot, bis jemand an das Extra denkt.

Gemessen in einem frischen Klon von `main`, nur mit `uv sync --extra dev`:
`tests/test_nim_live.py` ergibt **3 failed in 1,98 s**, alle mit
`ModuleNotFoundError`. Zwei Sekunden schließen echte API-Aufrufe aus, denn ein
NIM-Aufruf dauert hier 40 bis 75 Sekunden. Nach dem Wechsel dieselben drei
Tests ohne jedes Extra (`rm -rf .venv`, dann nur `uv run --with pytest`):
**3 passed in 74 s**.

* `openai>=1.40` steht jetzt in den Kernabhängigkeiten. Das Extra `nim` bleibt
  als leere Hülle stehen, damit alte Befehle mit `--extra nim` nicht brechen.
  `uv.lock` ändert sich um zwei Zeilen, keine Paketversion ändert sich.
* Der Nachtrag in ADR-074 Punkt 3 lautet jetzt „reproduziert“ statt „sehr
  wahrscheinlich“.
* `test_das_nim_sdk_ist_pflichtpaket_und_kein_extra` liest `pyproject.toml`
  und schlägt an, wenn `openai` wieder in ein Extra wandert.

### 2. Die Routine hinterließ jeden Tag einen Branch

Auf dem Remote lagen zwölf `claude/lucid-meitner-*`-Branches. Zehn davon
stammen aus der Zeit vom 2026-09-09 bis 2026-09-21, und jeder steht exakt auf
dem Tick-Commit seines Tages, **ohne eigenen Inhalt**. Elf sind vollständig in
`main` enthalten. Nur `2n3781` (`9ad3910`) trägt einen überholten Stand vom
2026-09-08, der nicht in `main` liegt.

Die Ursache ist das Skript, nicht der Agent. `paper_tick.sh` pusht den
Kontostand per `HEAD:main`, aber der lokale Session-Branch hat danach **keinen
Upstream**. Ein Prüfhaken der Sitzung meldet deshalb „unpushed commit“, und
der Agent pusht den Branch, um ihn zu beruhigen. Das ist eine vernünftige
Reaktion auf eine falsche Meldung.

* **`verfolge_ziel`:** Landet der Kontostand in `main`, verfolgt der
  Session-Branch danach `origin/main`. Dann steht er „up to date“ da, und es
  gibt nichts zu pushen. Das gilt auch für einen Tick ohne neuen Kontostand.
  Beim Rückfall auf den eigenen Branch (`main` lehnt ab) bleibt der Upstream
  der eigene Branch, denn dort liegt der Stand dann wirklich.
* **Nebenbefund, dieselbe Fehlerklasse wie ADR-059:** Nach dem Rückfall
  meldete das Skript trotzdem „Kontostand auf main gesichert“, also genau dort,
  wo er *nicht* lag. Jetzt nennt die Meldung den Ort, an dem der Push wirklich
  gelungen ist.
* `tests/test_paper_tick_skript.py`, 4 Tests gegen ein nacktes Git-Remote mit
  falschem `uv` und `sleep` und ausgeblendeter globaler Git-Konfiguration.
  **Gegen das alte Skript scheitern 3 der 4.**
* Der Prompt der Routine sagt zusätzlich ausdrücklich, dass der
  Session-Branch nicht gepusht wird. Ein zweites Schloss, denn der Haken
  könnte aus einem anderen Grund anschlagen.
* **Aufräumen kann nur ein Mensch.** Aus dieser Sitzung endete der Push des
  Archiv-Tags mit HTTP 403, und das Löschen fremder Branches ist hier nicht
  freigegeben. Die Befehle sichern zuerst `9ad3910` als Tag, damit kein
  Commit verloren geht:

  ```bash
  git fetch origin
  git push origin origin/claude/lucid-meitner-2n3781:refs/tags/archiv/paper-konto-2026-09-08
  git push origin --delete $(git branch -r | grep -o 'claude/lucid-meitner-[a-z0-9]*')
  ```

### 3. `qt research --resume` (ADR-077 A)

Ein Container-Neustart mitten in einem `--generate N`-Lauf kostete bisher den
ganzen Lauf samt bezahlter Modellaufrufe. Jetzt:

* Die Registry hat eine Tabelle `runs` mit den Parametern, der Liste „bereits
  geprüft“ vom Start und einem Fingerabdruck der Bars. Jeder Kandidat trägt
  `run_id`, `run_index` und `proposal_name`. Die Migration bleibt additiv.
  Eine Kopie der echten Registry hat danach weiter 24 Versuche und 46 Zeilen.
* **Fertige Kandidaten werden nie noch einmal gescreent.** Der
  Versuchszähler zählt also nichts doppelt (ADR-005).
* **Ein halb geprüfter Kandidat läuft aus seinem gespeicherten Code zu Ende**,
  ohne neuen Generator-Aufruf. Liegt die Kritik schon vor, wird sie nicht noch
  einmal bezahlt. Ein neuer Aufruf hätte einen anderen Kandidaten für denselben
  Platz geliefert.
* **Leere Plätze bekommen dieselben Briefings wie ohne Unterbrechung.** Dafür
  wird „bereits geprüft“ beim Fortsetzen *nicht* neu berechnet. Bis dahin
  stehen die eigenen Kandidaten des Laufs in der Registry, und das Modell
  bekäme sie als „gescheitert“ vorgesetzt.
* **Die Parameter kommen aus dem Lauf, nicht von der Befehlszeile.** Die Bars
  werden auf den Stand beim Start gekürzt. Neue Bars sind kein Problem. Haben
  sich aber Kurse rückwirkend geändert (Tiingo, ADR-055), wird nicht
  fortgesetzt: eine Charge, deren eine Hälfte gegen andere Daten lief, ist
  keine Charge mehr.
* **Ein Lauf mit Generator-Fehlern bleibt offen.** Nach einem Lauf ohne Zugang
  (ADR-078) holt `qt research --resume` die leeren Plätze nach, sobald der
  Schlüssel steht. Beide Fehlermeldungen nennen den Befehl.

Was bleibt: Ein Absturz *während* eines Modellaufrufs kostet diesen einen
Aufruf, falls die Antwort noch nicht im Cache lag. Kandidaten aus der Zeit vor
diesem ADR haben keine `run_id`, sie lassen sich nicht fortsetzen, verloren
geht aber nichts.

`tests/test_research_resume.py`, 13 Tests. Zwei davon sind per Mutation
geprüft: Neu berechnete Briefings und ein zweiter Kritik-Aufruf lassen je den
zuständigen Test scheitern.

### Konsequenz

* `openai` ist Kernabhängigkeit, `uv sync --extra dev` reicht für alles.
* `paper_tick.sh` lässt keinen Branch mehr zurück, der gepusht werden will,
  und meldet den Ort, an dem der Kontostand wirklich liegt.
* `qt research --resume` setzt den jüngsten offenen Lauf fort, mit dessen
  Parametern und gegen dessen Datenstand.
* Offen bleibt nur, was ein Mensch tun muss: `ANTHROPIC_API_KEY` in den
  Umgebungseinstellungen hinterlegen. Ohne ihn kommt `qt research` mit dem
  Standard-Anbieter nur so weit, wie der Cache reicht.

---

## ADR-078 — „Nicht gelaufen“ ist nicht „nicht bestanden“
**Datum:** 2026-09-22

Auslöser war eine einfache Frage: welche Umgebungsvariablen sind hier
gesetzt? Die Antwort (`TIINGO_API_KEY` und `NVIDIA_API_KEY` ja,
`ANTHROPIC_API_KEY` und die Börsenschlüssel nein) enthielt drei Befunde. Beim
Beheben sind zwei davon größer geworden, als sie aussahen, und einer hat sich
als falsch begründet herausgestellt.

### Befund 1: `qt research` ohne Anthropic-Zugang meldete sich als Normalfall

Reproduziert mit dem **echten** SDK, Registry und Cache im Temp-Verzeichnis:

```
  erzeugt                      0
  Generator-Fehler             2
  ...
Kein Kandidat hat bestanden. Das ist der Normalfall und kein Fehler (ADR-005).
Exit 0
```

Der Loop fängt Generator-Fehler je Kandidat ab, damit ein einzelner
Netzwackler nicht die Charge kostet — richtig so. Scheitert aber **jeder**
Kandidat, hat der Lauf nichts geprüft, und die Schlusszeile behauptete
trotzdem das Gegenteil. Exit 0 heißt für jeden Aufrufer, ob Routine oder
Skript: gelungen. Dieselbe Fehlerklasse wie ADR-073 (die Routine meldete
SUCCEEDED ohne Arbeitskopie) und wie „Silent Hold“ im CHANGELOG von
TradingAgents (ADR-077).

Der Schaden blieb aus, weil nichts gezählt wurde: `record_generated` läuft erst
nach einem erfolgreichen Vorschlag, und der Versuchszähler stand vorher wie
nachher. Falsch war nur die Meldung, und zwar in die bequeme Richtung.

**Die Korrektur:**

* Kein einziger Kandidat erzeugt → „Lauf GESCHEITERT … Das ist kein ‚nicht
  bestanden‘, sondern ‚nicht gelaufen‘“, **Exit 1**, dazu was fehlt und was
  hier stattdessen geht.
* Teilweise gescheitert → weiterhin Exit 0, aber mit der Zahl: „Geprüft
  wurden aber nur 1 von 2“.
* Vor dem Datenladen ein Hinweis, wenn der Anbieter hier nicht nutzbar
  aussieht, und zwar **kein Abbruch**: ein Lauf gegen gefüllten Cache kommt
  ohne Schlüssel aus, und ob der Cache reicht, weiß man vorher nicht.
* `qt alloc` hatte dasselbe Muster eine Ebene tiefer: bei 100 % Rückfall auf
  Gleichgewichtung verglich das Urteil Gleichgewichtung mit sich selbst und
  endete mit Exit 2, also „Allokator geprüft, nicht bestanden“. Jetzt Exit 1
  mit Begründung.

**Der Standard-Anbieter bleibt Anthropic.** Automatisch auf NIM zu wechseln lag
nahe und ist verworfen: `cli.py` hält fest, dass jeder Cache-Eintrag und
jeder ADR am Anthropic-Default hängt. Ein stiller Wechsel hätte einen
Cache-Replay unbemerkt in einen neuen, kostenpflichtigen Lauf verwandelt, der
Versuche verbraucht. Die Meldung nennt den Ausweg, gehen muss ihn ein Mensch.

### Die erste Fassung von Befund 1 war zu schnell

„`ANTHROPIC_API_KEY` leer, also kein Zugang“ stimmt so nicht. Das SDK sucht
der Reihe nach `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, ein Profil aus
`ant auth login` (`~/.config/anthropic/`, Auswahl über `ANTHROPIC_PROFILE`)
und Workload Identity Federation. Nachgeprüft ist jetzt alles davon: `ant`
ist nicht installiert, es gibt kein Profil, keine der Variablen ist gesetzt,
und ein Aufruf mit dem echten SDK scheitert vor dem Senden. Das Ergebnis
bleibt dasselbe, die Begründung war trotzdem unvollständig. Die neue
Zugangserkennung (`qt.llm.providers.zugang_vorhanden`) kennt alle Quellen
und liest nie einen Wert.

### Befund 1b: ein Schlüssel ohne SDK ist kein Zugang, und das betraf NIM

Beim Testlauf fielen drei Tests aus `tests/test_nim_live.py`, und zwar nicht an
der API, sondern an `ModuleNotFoundError: No module named 'openai'`. Der
frische Container hatte das `nim`-Extra nicht installiert. **`--provider nim`
war hier tot, obwohl `NVIDIA_API_KEY` gesetzt war.** Meine Auskunft „Daten und
NIM-Loop laufen“ stützte sich allein auf den Schlüssel. Sie war falsch, und
die erste Fassung der neuen Fehlermeldung hätte genau diesen toten Weg als
Ausweg empfohlen.

Gemessen: nach `uv sync --extra dev --extra nim` (ein Paket, `openai 3.6.0`)
bestehen alle drei Live-Tests gegen NIM, 43 Sekunden, mit echten Antworten.
Schlüssel und Endpunkt sind also in Ordnung, es fehlte nur die Installation.
Die Zugangserkennung prüft jetzt auch das SDK und sagt, wie es hineinkommt
(`uv sync --extra nim`). Im Sitzungsstart-Block der ROADMAP steht der Befehl
jetzt an erster Stelle.

> **Nachtrag ADR-079:** Das Extra war nur das Symptom. `uv run` installiert
> keine Extras, also fehlte `openai` in *jeder* frischen Sitzung. Seit ADR-079
> ist es Kernabhängigkeit, und die Meldung sagt nur noch `uv sync`.

### Nebenbefund: ADR-074 Punkt 3 nennt sehr wahrscheinlich den falschen Grund

Dort steht, die drei Fehlschläge von `test_nim_live.py` im Probelauf des
frischen Klons seien „echte API-Aufrufe, die scheiterten“. Der Probelauf lief
mit `uv sync --extra dev`, also ohne das `nim`-Extra. Heute schlagen dieselben
drei Tests unter denselben Bedingungen fehl, und zwar bevor überhaupt ein
Aufruf das Netz erreicht. Beweisen lässt es sich im Nachhinein nicht, weil
die Ausgabe von damals nicht erhalten ist. Aber „echte API-Aufrufe“ ist die
schwächere der beiden Erklärungen, und genau so ein Grund wird später
zitiert. Die Schlussfolgerung von ADR-074 bleibt davon unberührt: in der
Action gibt es keinen Schlüssel, die Tests überspringen sich dort.

### Befund 2: Börsenschlüssel leer — geprüft, bewusst nicht geändert

`qt live status` und `qt live tick` brechen ohne `QT_EXCHANGE_*` mit Exit 1 ab
(„Kein Boersenzugang: … ist nicht gesetzt“ bzw. die Weigerung ohne
validierten Edge). Das ist der gewollte Zustand für Phase 7, „gebaut,
unverdrahtet“. Gesetzt werden die Schlüssel erst, wenn ein Mensch entschieden
hat, mit echtem Geld zu handeln.

### Konsequenz

* `qt research`: gar nichts erzeugt → Exit 1 und „nicht gelaufen“; teilweise
  erzeugt → Exit 0 mit der Zahl der tatsächlich geprüften Kandidaten.
* `qt alloc`: 100 % Rückfall → Exit 1.
* `qt.llm.providers.zugang_vorhanden` / `zugangs_hinweis`: prüfen SDK **und**
  alle Zugangsquellen, nennen den nutzbaren Ausweg, lesen nie einen Wert.
* 14 Tests in `tests/test_research_ohne_zugang.py`, die Verhaltenstests
  zuerst gegen den alten Stand rot.
* ROADMAP: `uv sync --extra dev --extra nim` im Sitzungsstart; die Behauptung
  „NIM kommt hier durch“ verweist jetzt auf den Test statt auf sich selbst.

---

## ADR-077 — TradingAgents verglichen: zwei Lücken geschlossen, eine bewusst nicht
**Datum:** 2026-09-17

Auslöser war eine Nutzeranfrage: taugt `TauricResearch/TradingAgents` (Paper
arXiv:2412.20138) als Vorbild? Geprüft wurden das Paper vollständig und das
Repo im aktuellen Stand (`v0.4.0`, Commit `be952b8`), nicht nur das README.

### Das Urteil, kurz, weil es die Einordnung der drei Befunde unten trägt

Nach dem eigenen Beweismaßstab dieses Projekts ist TradingAgents nicht
überlegen. Drei Belege dafür, jeder nachgerechnet oder nachgezählt:

**Die berichteten Sharpe-Werte sind auf ihrem eigenen Datenfenster nicht
beweisbar.** Backtest: drei Monate (01.01.–29.03.2024), ein Pfad, ein Markt
je Auswertung. Berichtet: AAPL 8,21, GOOGL 6,39, AMZN 5,60. Mit
`t = S·√T/√(1+S²/2)` und `T = 0,25` Marktjahren:

```
Ticker   SR (Paper)   t-Wert   (verlangt: 2,0)
AAPL         8,21      0,697
GOOGL        6,39      0,690
AMZN         5,60      0,686
```

Bei `T = 0,25` liegt die Obergrenze von `t` — für **jeden** Sharpe, auch
S → ∞ — bei `√(2·T) = 0,707`. Kein Wert, so hoch er auch berichtet würde,
könnte auf diesem Fenster die Latte von 2,0 reißen. Die Autoren selbst,
Fußnote zu Abschnitt 6.1.2: *„The highest Sharpe Ratio exceeds our expected
empirical range... We believe the exceptionally high SR resulted from the
phenomenon that there were few pullbacks... We report results as they are in
our experiments faithfully."* Redlich beobachtet, ohne Korrektur geblieben.

**Keine Kostenmodellierung im gesamten Repository.** `grep -rniE
"commission|slippage|transaction.?cost|spread"` über den vollständigen
Quellcode: null Treffer. `backtrader` ist Abhängigkeit und unterstützt
Kommissionsmodelle, sie sind nirgends konfiguriert. Bei elf LLM- und über
zwanzig Tool-Aufrufen je Tagesentscheidung ist nicht einmal der Umschlag
ausgewiesen.

**Das eigene CHANGELOG dokumentiert wiederholte Lookahead-Lecks, spät
gefunden.** `v0.4.0` (2026-08-31), rund 20 Monate nach der Publikation,
Überschrift: *„Look-ahead and point-in-time fixes across the data and memory
layers"*. Drei Klassen: FRED-Makrodaten liefen gegen den heutigen
Revisionsstand (*„leaking later revisions into a backtest"*), Social-Media-
Daten ohne Datumsgrenze (*„a historical run showed today's chatter as if it
were from the as-of date"*), und `get_past_context` gab jede aufgelöste
Lektion unabhängig vom Laufdatum zurück. Eine davon kam per externem Report
(#475), nicht aus eigener Prüfung. Dazu im selben Release zwei weitere Funde,
die den Kern des Papers treffen: **„Silent Hold"** — eine nicht parsbare
Risk-Manager-Bewertung wurde still zu einer handelbaren Entscheidung
umgedeutet, dieselbe Fehlerklasse wie ADR-073 (ein Ausfall, der sich als
Erfolg meldet). Und **„Debate opening fabrication"** — der jeweils erste
Redner jeder Bull/Bear-Debattenrunde widerlegte eine leere Gegenposition,
erfand sie also. Die Debatte ist das methodische Kernargument des Papers
(Abschnitt 4.2); für einen unbekannten Zeitraum hatte sie keine echte
Gegenseite.

### Trotzdem: drei Befunde, wo etwas dort wirklich besser war

Jeder einzeln geprüft, nicht pauschal übernommen — mit dem Ergebnis, dass
einer davon beim Nachprüfen nicht in der behaupteten Form haltbar war.

**(A) Checkpoint/Resume für mehrstufige LLM-Läufe — echte Lücke, wird
Aufgabe.** TradingAgents hält den Zustand seines Agentengraphen über einen
LangGraph-Checkpointer (SQLite) und kann einen unterbrochenen Lauf über
`--checkpoint` fortsetzen statt neu zu starten (`v0.2.4`–`v0.4.0`, mehrere
Härtungsrunden). Nachgeprüft: `qt research` hat keine Entsprechung. Das
einzige `resume` im CLI (`src/qt/cli.py:1111`) gehört zu `qt data trades`
und setzt die Kraken-Seitenpaginierung fort — nichts, was einen
`qt research --generate N --screen`-Lauf betrifft. Ein solcher Lauf ist
mehrstufig und nicht billig: mehrere generierte Kandidaten, je einer durch
Sandbox, Kritik, Walk-Forward und DSR. Ein Container-Neustart mittendrin
(in dieser Umgebung dokumentiert real — `scripts/paper_tick.sh`: *„Hintergrundprozesse in dieser Sitzung sind wiederholt an Container-Neustarts
gestorben"*) verliert den ganzen Lauf samt bereits bezahlter LLM-Aufrufe.
→ Aufgabe in `docs/ROADMAP.md`, „Was als Nächstes Sinn ergibt".

**(B) Punkt-in-Zeit-Disziplin für Wirtschaftsdaten — richtig erkannt, falsch
begründet, jetzt korrigiert.** Im Gespräch stand, TradingAgents habe
„dedizierte Lookahead-Tests pro Datenquelle" voraus. Nachgeprüft: **das
stimmt so nicht.** `test_macross.py`, `test_elliott.py`, `test_onchain.py`
und `test_ml_dataset.py` haben längst je einen eigenen, so benannten
Lookahead-Test — dieselbe Disziplin, nur nie als Regel aufgeschrieben. Ein
falscher Grund für eine sonst richtige Beobachtung, hiermit korrigiert statt
stehen gelassen.

Die tatsächliche Lücke ist enger und liegt woanders: Tiingos retroaktive
Adjustierung (ADR-055) ist ein **Reproduzierbarkeits**problem — der Faktor
wirkt gleichmäßig auf die ganze Historie, verändert kein Signal relativ zu
seinem Zeitpunkt. TradingAgents' FRED-Fehler war ein echtes **Vintage**problem:
der heutige revidierte Wert wurde als historischer Stand ausgegeben. Diese
Fehlerklasse hat dieses Projekt noch nie berührt, weil es noch keine Reihe
zieht, die revidiert wird — Kurse und On-Chain-Kennzahlen werden nicht
nachträglich korrigiert, Wirtschaftsdaten (NFP, CPI, BIP) schon. Genau das
träfe zum ersten Mal die in ADR-075 zurückgestellte Makro-Überraschungs-
strategie, sollte sie je aufgenommen werden. → keine eigene Aufgabe, solange
die Richtung nicht verfolgt wird — aber eine Vorbedingung, an ADR-075
angehängt: Vintage-Quelle (ALFRED-Stil, nicht die aktuelle FRED-Ausgabe) und
ein eigener Punkt-in-Zeit-Test nach demselben Vorbild wie `test_macross.py`,
**bevor** die erste Zeile Strategie entsteht.

**(C) Breite der Datenanbindung — geprüft, bewusst nicht übernommen.**
News, Sentiment, Insider-Transaktionen, Fundamentaldaten: TradingAgents zieht
alles davon. Das ist keine Lücke in diesem Projekt, sondern eine andere
Wette, seit ADR-Linie und `docs/ZIEL.md` bestehend: Text-Signale sind ohne
Kostenidentität und DSR-Einordnung nicht beweisbar, und die Breite allein
liefert keine unabhängige Evidenz. TradingAgents selbst ist das Gegenbeispiel
dafür, wie leicht das bricht — drei der eigenen Lookahead-Lecks saßen genau
in dieser Breite (Makrodaten, Sentiment, Memory) und blieben monatelang
unentdeckt. Nicht übernommen, mit Begründung stehen gelassen statt
stillschweigend verworfen.

---

## ADR-076 — `bars_seen` war ein Laufzettel und keine Zahl
**Datum:** 2026-09-09

Gefunden beim Nachlesen eines Routinelaufs, der sich selbst als unauffällig
gemeldet hatte — und der Bericht stimmte auch: „ein neuer Bar pro Konto,
keine Fills". Im gesicherten Kontostand stand daneben:

```
  06.09.  2.805      08.09.  8.418
  07.09.  5.611      09.09.  8.529   (+8.418 an einem Tag)
```

`bars_seen` wuchs pro Tick um die volle Storegröße statt um die neuen Bars.

### Die Ursache

`run_paper_tick` lädt die **ganze** Historie und spielt sie in einen frisch
angelegten `FeatureStore` ein; nur die Bars nach `last_processed_ts` lösen
eine Entscheidung aus, der Rest ist Kontext. Beide Zweige der Schleife zählen
`bars_seen` hoch — richtig, denn der Store hat diese Bars wirklich gesehen.

Der Fehler saß eine Zeile davor: `bars_seen = dict(state.bars_seen)`. Der
Zähler wurde aus dem Zustand **geladen** und dann von derselben Historie noch
einmal hochgezählt. Er beantwortet damit keine Frage: „seit Kontoeröffnung"
ist es nicht, denn er zählt dieselben Bars mehrfach; „im letzten Tick" ist es
auch nicht, denn er trägt die vorigen mit.

### Warum es aufgefallen ist und trotzdem nichts kaputt war

`bars_seen` trägt genau eine Entscheidung, die Warmup-Prüfung:

```python
warm = all(bars_seen.get(sym, 0) >= strategy.warmup_bars for sym in bars_by_symbol)
```

Ein zu **großer** Wert meldet ein Konto zu früh als warm. Bei den beiden
laufenden Konten war der echte Wert (2.809 Bars) ohnehin weit über dem Warmup,
der Fehler also folgenlos — **zufällig folgenlos**. Bei einem frisch
aufgesetzten Konto oder nach einem beschnittenen Store wäre es der Unterschied
zwischen „handelt mit genug Historie" und „handelt". Und zwar still: eine
Strategie, die auf zu wenig Historie entscheidet, stürzt nicht ab, sie
entscheidet nur schlechter.

Das ist dieselbe Fehlerklasse wie ADR-069 (das Rebalancing-Band maß das
Eigenkapital statt der Position) und ADR-075 (der `--since`-Default): eine
Größe, die etwas anderes misst, als ihr Name sagt, und deren Fehler in die
bequeme Richtung zeigt.

### Die Korrektur

Der Zähler wird pro Tick neu gebildet. Er bedeutet damit, was die
Warmup-Prüfung braucht: **Tiefe des Feature-Stores je Symbol in diesem Tick.**
Eine Migration entfällt — der Wert wird beim nächsten Tick mit einem neuen Bar
überschrieben. (Ein Leerlauf-Tick sichert nichts, also steht der aufgeblähte
Wert bis dahin noch in der Datei; das ist bekannt und harmlos.)

Zwei Tests halten es fest, und der erste ist zuerst rot geschrieben worden:
drei Ticks mit je einem neuen Bar müssen 31, 32, 33 ergeben und nicht 31, 63,
96. Der zweite setzt 999.999 in eine Zustandsdatei und prüft, dass ein Tick
das heilt.

### Was der Bericht des Laufs richtig gemacht hat

Die gemeldete Zahl „ein neuer Bar pro Konto" stammt aus `new_bars` und war
korrekt. Der Fehler stand nur im gesicherten Zustand, den niemand liest,
solange er plausibel aussieht — und 8.529 sieht neben 8.418 plausibel aus.
Aufgefallen ist er erst im Vergleich zweier Kontostände, die dasselbe Konto an
verschiedenen Tagen beschreiben. Das ist die Lehre: der Tagesbericht kann nur
prüfen, was er ausgibt.

### Nebenbefund aus demselben Lauf, keine Codeänderung

Der Lauf hat einen nach dem Merge gelöschten Remote-Branch **wiederhergestellt**,
damit ein Hook keinen „unpushed commit" mehr meldet. Der Branch zeigt jetzt
auf denselben Commit wie `main` und enthält nichts, was dort nicht steht. Ein
Push, um ein Werkzeug zufriedenzustellen statt um Inhalt zu sichern, ist die
Umkehrung von ADR-051: dort war die Lehre, dass eine grüne Meldung nichts über
den Zustand sagt. Der Branch bleibt vorerst stehen (er schadet nicht); die
Meldung des Hooks ist der Teil, der falsch liegt.

---

## ADR-075 — Die Nachweisgrenze hing an einem Default, nicht an den Daten
**Datum:** 2026-09-08

Auslöser war eine ganz andere Frage: taugt eine Makro-Überraschungsstrategie
(NFP, CPI, FOMC — Ist minus Konsens) als nächste Richtung? Beim Nachrechnen
ihrer Anforderung fiel auf, dass die bindende Größe gar nicht der Signaltyp
ist, sondern die **Kalenderspanne** — und dass die 7,7 Jahre, mit denen dieses
Projekt seit ADR-054 rechnet, keine Eigenschaft der Datenquelle sind.

### Der Fund

`qt data stocks --since` stand auf `2019-01-01`. Das ist das richtige Datum
für Krypto — Coinbase fängt dort an — und es war nie für die ETFs gedacht.
Trotzdem galt es für sie, weil sie mit demselben Befehl gezogen wurden. Alle
25 ETF-Reihen im Store begannen am 2019-01-02.

Was die Quelle wirklich hergibt, abgefragt statt vermutet:

| Ticker | ab | | Ticker | ab |
|---|---|---|---|---|
| SPY | 1993-01-29 | | GLD | 2004-11-18 |
| EWJ | 1996-04-01 | | FXE | 2005-12-12 |
| QQQ | 1999-03-10 | | DBC | 2006-02-06 |
| EFA | 2001-08-17 | | SLV | 2006-04-28 |
| IEF/SHY/TLT | 2002-07-26 | | MUB | 2007-09-10 |
| EEM | 2003-04-14 | | VIXY | 2011-01-04 |
| TIP | 2003-12-05 | | CPER | 2011-11-15 |

Der Abzug hat aus bis zu 33 Jahren 7,7 gemacht, und zwar geräuschlos: im
Store steht danach nur noch das Ergebnis.

### Was der neue Abzug gebracht hat

25 Reihen, 48.000 → **130.000 Bars**. Auf der Überlappung 2019–2026 sind alt
und neu **bitgleich** — größte Renditeabweichung über alle 25 Reihen
`0,0e+00`. Das ist mehr als eine Formalie: die retroaktive Adjustierung, vor
der ADR-055 warnt, hat diesmal nicht zugeschlagen, und **jede bisher
gemessene Zahl auf ETF-Daten bleibt gültig**. Die Erweiterung ist rein
additiv.

Die Integritätsprüfung meldet alle 25 als `ok`. Die gefundenen Lücken sind
echte Börsenschließungen und lesen sich wie eine Stichprobe der letzten
25 Jahre: 2001-09-10 → 2001-09-17 (NYSE nach dem 11. September),
2012-10-26 → 2012-10-31 (Hurrikan Sandy), 2006-12-29 → 2007-01-03
(Staatstrauer Gerald Ford). Der Lückendetektor hat damit nebenbei sich selbst
geprüft.

### n_eff auf dem echten Fenster

Gemessen mit `qt placebo cross`, also dem Instrument des Projekts:

| Universum | ρ̄ | n_eff | vorher |
|---|---|---|---|
| 25 ETFs | 0,11 | **7,1** | 5,96 auf dem 2019er-Fenster |
| alle 38 Märkte | 0,16 | **5,4** | 5,1 (ADR-061) |
| nur die 14 Krypto-Paare | 0,69 | 1,41 | — |

Zwei Dinge daran waren nicht erwartet.

**Erstens: das längere Fenster ist das *weniger* korrelierte.** Die Vermutung
war das Gegenteil — Krisen korrelieren alles, und ein Fenster über 2008 hinweg
sollte ρ̄ heben. Es hebt es nicht, weil das alte Fenster ausgerechnet aus
Covid und der Zinswende 2022 bestand. Rollierende Dreijahresblöcke:

```
  2012-2014   rho 0,091   n_eff 7,85      2020-2022   rho 0,145   n_eff 5,59
  2014-2016   rho 0,080   n_eff 8,58      2022-2024   rho 0,163   n_eff 5,10
  2016-2018   rho 0,097   n_eff 7,49      2024-2026   rho 0,124   n_eff 6,29
  2018-2020   rho 0,104   n_eff 7,13
```

Das schlechteste Einzeljahr ist 2022 mit ρ̄ 0,185 und n_eff 4,60: Anleihen und
Aktien fielen zusammen, die Diversifikation verschwand genau dann, als sie
gebraucht wurde. Das alte 7,7-Jahre-Fenster enthielt beide schlechten Regime
und keines der ruhigen — die 5,1 aus ADR-061 waren auf einem unrepräsentativ
korrelierten Ausschnitt gemessen.

**Zweitens: Krypto senkt n_eff.** 25 ETFs allein kommen auf 7,1; mit den 14
Krypto-Paaren dazu sind es 5,4. Die Paare heben n um 14 und ρ̄ von 0,11 auf
0,16, und das Zweite wiegt schwerer. Das dreht die Erzählung seit ADR-054 um:
dort waren die ETFs die Erweiterung eines Krypto-Bestands. Gemessen ist der
Krypto-Block der Teil, der die effektive Marktzahl drückt.

**Eine Ungenauigkeit im eigenen Instrument, offen benannt.**
`mean_pairwise_correlation` rechnet jedes Paar auf **seinem** gemeinsamen
Fenster — SPY/EWJ ab 1996, CPER/VIXY ab 2011. Das ist für eine Korrelation
richtig, ergibt aber kein einheitliches T für die Schwellenformel. Auf dem
**strengen** gemeinsamen Fenster aller 25 (ab 2011-11-15) sind es ρ̄ 0,1172
und n_eff **6,56**. Gerechnet wird unten mit 6,56, nicht mit 7,1.

### Die Schwelle wird trotzdem nicht gesenkt

`t = S·√T / √(1+S²/2) ≥ 2` mit `T = Jahre × n_eff` ergibt:

| | Spanne | n_eff | Schwelle |
|---|---|---|---|
| bisher | 7,7 J | 4,89 | 0,335 |
| **jetzt** | **14,8 J** | **6,56** | **0,205** |

Und die drei Bedingungen aus `gate.py`, unter denen `MIN_SHARPE` angefasst
werden darf, wären alle erfüllt: die Regel stand vorher, gesenkt hätte die
Datenlage, und kein Kandidat gewönne dadurch.

**Sie bleibt bei 0,33.** Der Grund kam erst beim Nachzählen heraus: diese
Konstante hat **noch nie eine Entscheidung getroffen.** Von 16
durchgerechneten Kandidaten in der Registry scheiterte keiner allein an ihr —
jede Ablehnung kam von der DSR, meist von beiden:

```
  Kandidat                  Sharpe     DSR   nötig für DSR   Wer sagt Nein?
  MomentumTrend              0,674   0,264       1,355       nur DSR
  VolRegime                  0,554   0,064       1,338       nur DSR
  DonchianBreakout           0,486   0,184       1,329       nur DSR
  ZScoreMeanReversion        0,443   0,035       1,347       nur DSR
  ... 12 weitere              < 0     0,000    0,22 - 1,66   beide

  Kandidaten, die allein an MIN_SHARPE scheiterten: 0
```

Eine Schwelle zu senken, die nicht bindet, ändert kein Urteil und sieht nur
wie Fortschritt aus. Der Diff wäre die Sorte, die später zitiert wird —
„damals wurde die Latte gesenkt" — ohne dass ihm anzusehen wäre, dass er
folgenlos war. Die Herleitung im Kommentar ist korrigiert, die Zahl steht.

### Wo der Gewinn wirklich liegt: die DSR

Die DSR misst ihr T nicht in Märkten, sondern in **Bars der Portfoliokurve**;
n_eff kommt darin gar nicht vor, weil über die Märkte schon aggregiert wurde.
Ihr Maßstab fällt mit √T, und damit hängt sie fast vollständig an der Spanne.
Bei 25 Versuchen und normalverteilt unterstellten Renditen:

| OOS-Bars | entspricht | nötiger Sharpe für DSR ≥ 0,95 |
|---|---|---|
| 1.928 | ETFs ab 2019 | **1,318** |
| 3.722 | 25 ETFs ab 2011-11 | 0,948 |
| 5.501 | Walk-Forward ohne die toten Fenster | 0,780 |
| 7.251 | Walk-Forward wie ausgegeben | 0,679 |

Das ist der eigentliche Ertrag dieses Abzugs: **die Hürde, die wirklich
bindet, ist halbiert** — von 1,32 auf 0,68 — ohne eine einzige neue Idee.

**Der Haken daran, und er ist wichtig.** Der Walk-Forward über die ETFs
liefert jetzt 29 Fenster ab 1997-02-11 statt 7. Die ersten sieben haben
**null Trades und 0,00 % Rendite**: vor 2004 existieren nur SPY, EWJ, QQQ und
EFA, und `crossmom` braucht einen Querschnitt. 1.750 der 7.251 OOS-Bars sind
damit Nullen. Für die DSR ist das kein neutraler Ballast, sondern ein Fehler
in die *freundliche* Richtung: Nullen verlängern T und drücken gleichzeitig
die Streuung. Die ehrliche Länge ist 5.501 Bars und der ehrliche Bedarf
0,78 — nicht 0,68.

Daraus folgt, wo ein Panel anfangen darf: nicht beim ersten verfügbaren Bar,
sondern sobald genug Namen leben. `MIN_NAMEN = 8` steht schon in
`qt.research.ic` („eine Rangkorrelation über vier Werte ist Rauschen mit
Dezimalstellen"). Acht ETFs gibt es ab **2003-04-14** — 23,4 Jahre, 5.887
Bars. Das ist der Startpunkt, den die eigenen Konstanten des Projekts
vorgeben, und keine neue Wahl.

### Was gemessen wurde, nicht nur gerechnet

`crossmom` auf den 25 ETFs, voller Walk-Forward: OOS-Sharpe **+0,04**
(vorher −0,24), 29 Fenster, 11 davon positiv, Umschlag 5,3× (im Budget),
DSR 0,038. Besser als vorher, weiterhin weit unter jeder Schwelle. `crossrev`
fällt weiter am Umschlag (13,3×). **Kein Kandidat wird durch diesen Abzug
gerettet** — was die dritte Bedingung nicht nur formal, sondern gemessen
erfüllt.

Alle 1047 Tests laufen gegen den neuen Store durch.

### Eine Behauptung in der ROADMAP ist damit widerlegt

Dort stand: *„Der nächste Schritt brächte 0,33 → 0,26 und bräuchte
Anlageklassen, die es nicht gibt. Ab hier hilft nur noch ein stärkerer
Edge."* Der nächste Schritt brauchte keine neue Anlageklasse, sondern ein
anderes Datum in einem Default, und er bringt 0,33 → 0,205 — mehr als die
0,26, die als unerreichbar galten. Der Satz war zum Zeitpunkt seiner
Niederschrift plausibel und falsch, und er hat den billigsten verbliebenen
Hebel für erledigt erklärt.

### Und die Makro-Frage, von der alles ausging

Sie ist damit **nicht** beantwortet, aber ihre Voraussetzungen sind geklärt:

* **n_eff steigt durch eine Makro-Reihe nicht.** `effektive_maerkte` zählt
  `MarketRun`-Objekte, also Märkte. Eine Überraschungsreihe ist ein Merkmal:
  sie erzeugt keinen zusätzlichen unabhängigen Test und kostet einen
  Freiheitsgrad im DSR-Nenner. (Das korrigiert eine frühere Aussage von mir,
  `qt placebo cross` könne zeigen, ob n_eff dadurch steigt — kann es nicht.)
* **Konsens-Historie gibt es frei nur quartalsweise.** Philadelphia-Fed-SPF:
  HTTP 200, 554 KB, kein Schlüssel, 58 Reihen, 232 Quartale ab 1968Q4. Die
  EZB-SPF ebenso. BLS v1 liefert Ist-Werte samt `preliminary`-Fußnoten, also
  auch Revisionen. Monatlicher und wöchentlicher Konsens ist überall
  kostenpflichtig; der Gastzugang von TradingEconomics antwortet mit HTTP 410
  („the guest account has been discontinued").
* **Das trifft genau die Frequenzen, die ohnehin aussichtslos sind.** Bei
  65 bps einweg und nach Zeit-im-Markt korrigiert braucht eine
  Erstanträge-Routine (52×/Jahr, 1 Tag halten) brutto 4,40 Sharpe, alle
  Monatsdaten 2,59 — gegen 0,45 für ein durchgehend gehaltenes
  Quartalssignal. Frei verfügbar ist genau die eine Frequenz, die als einzige
  eine Chance hat.

Ein Quartalssignal braucht bei Drag 0,118 netto 0,205, also **brutto 0,323**.
Das beste gemessene Bruttosignal im Repo (`trend`, +0,459) liegt erstmals
darüber. Das macht die Richtung diskutabel; es macht sie nicht zur nächsten
Aufgabe.

**Nachtrag ADR-077, falls sie doch aufgenommen wird:** Wirtschaftsdaten werden
revidiert, anders als alles, was dieser Store bisher zieht. Vor der ersten
Zeile Strategie gehört eine Vintage-Quelle (ALFRED-Stil, nicht die laufend
aktualisierte FRED-Ausgabe) und ein eigener Punkt-in-Zeit-Test nach dem
Vorbild von `test_macross.py` — sonst genau der Fehler, den TradingAgents'
eigenes CHANGELOG als „FRED macro look-ahead" führt, rund 20 Monate nach
dessen Publikation gefunden.

### Konsequenz

* `qt data stocks --since` steht auf `1993-01-01`. Gezogen wird, was die
  Quelle hat; welches Fenster eine Auswertung nimmt, entscheidet die
  Auswertung.
* `MIN_SHARPE` bleibt 0,33. Der Kommentar nennt jetzt die korrigierte
  Herleitung (0,205) **und** den Grund, warum die Zahl trotzdem steht.
* Zwei Tests in `test_tiingo.py` sichern den Default und den Bestand.
* Der Kalenderschnitt ist als Fehlerklasse benannt: ein Ingest-Default aus
  einer Anlageklasse, der still für eine andere gilt.


### Nachtrag 2026-09-09 — das Panel ab 2003, und drei eigene Zahlen korrigiert

Der Abschnitt oben endet mit einem Vorschlag: das Panel nicht beim ersten Bar
beginnen zu lassen, sondern sobald genug Namen leben — acht, weil
`qt.research.ic` das seit jeher als Untergrenze für einen Querschnitt setzt.
Das ist jetzt gemessen, und dabei sind drei Zahlen aus dem Text darüber
gefallen.

#### Erstens: „Jahre × n_eff" ist für ein wachsendes Panel falsch

`23,4 Jahre × n_eff 7,0` unterstellt, dass 2003 schon sieben effektive Märkte
lieferten. 2003 leben acht Reihen, 2007 neunzehn, erst ab 2013 alle 25.
Richtig ist das Integral: für jeden Tag die effektive Marktzahl aus den an
diesem Tag lebenden Reihen, summiert und durch 252 geteilt.

| Panel ab | Kalenderjahre | naiv `J × n_eff` | **effektive Marktjahre** | Schwelle naiv | **Schwelle ehrlich** |
|---|---|---|---|---|---|
| 2011-11-15 | 14,8 | 104,8 | 104,6 | 0,197 | **0,197** |
| 2007-09-10 | 19,0 | 134,4 | 129,5 | 0,174 | 0,177 |
| 2004-09-29 | 21,9 | 155,2 | 140,8 | 0,162 | 0,170 |
| **2003-04-14** | **23,4** | 165,6 | **145,3** | 0,156 | **0,167** |
| 2001-08-17 | 25,0 | 177,3 | 148,8 | 0,151 | 0,165 |

Der naive Wert ist nur um 0,01 zu optimistisch — die Korrektur ändert die
Entscheidung nicht, aber die Rechnung war falsch, und ein richtiges Ergebnis
aus einer falschen Rechnung wird zitiert.

**Korrektur 1:** oben steht **0,205** für das 25er-Panel. Richtig sind
**0,197**. Die 0,205 kamen aus `n/(1+(n-1)·ρ̄)` mit einem ρ̄, das über Paare
mit `min_periods=250` gemittelt war; die exakte Form `n²/Σρ_ij` gibt 7,08
statt 6,56. Die Richtung des Fehlers war die vorsichtige.

**Wo es aufhört zu helfen:** von 2003 auf 2001 sind es 1,6 Kalenderjahre, aber
nur 3,5 effektive Marktjahre und 0,167 → 0,165. Die 2003er-Grenze ist damit
nicht nur die, die `MIN_NAMEN = 8` vorgibt, sondern auch die, hinter der
nichts mehr kommt.

#### Zweitens: die toten Fenster verschwinden, und zwar vollständig

`crossmom`, Walk-Forward, 1.000/250/20:

| Panel ab | Fenster | OOS-Bars | davon ohne Trades | Sharpe | positive Fenster |
|---|---|---|---|---|---|
| 1993 (alles) | 29 | 7.251 | **7 (1.750 Bars)** | +0,04 | 11/29 |
| 2011-11-15 | 10 | 2.501 | 0 | −0,38 | 4/10 |
| **2003-04-14** | **19** | **4.751** | **0** | **−0,08** | 9/19 |

Das erste Fenster des 2003er-Panels handelt 268-mal. Die Acht-Namen-Regel ist
damit nicht bloß plausibel, sondern trifft empirisch genau den Punkt, ab dem
der Querschnitt arbeitet.

**Korrektur 2:** oben steht, die ehrliche OOS-Länge sei **5.501 Bars** und der
DSR-Bedarf **0,78**. Beides war gerechnet (7.251 − 1.750) statt gemessen. Der
Walk-Forward schneidet neu, wenn das Panel später beginnt: es sind **4.751
Bars** und **0,839**. Subtraktion ist kein Ersatz für einen Lauf.

**Korrektur 3:** oben steht `crossmom` **+0,04** als Ergebnis der Erweiterung.
Dieser Wert stammt aus dem Lauf mit den sieben toten Fenstern. 1.750 Bars mit
Rendite exakt null heben einen negativen Sharpe in Richtung null — der Fund
war ein Messartefakt derselben Sorte, vor der ADR-066 warnt und an der
ADR-069 schon einmal fast vorbeigelaufen wäre. Auf dem sauberen Panel steht
`crossmom` bei **−0,08**. Besser als die −0,24 von vorher, und weiterhin
nirgends in der Nähe einer Schwelle.

#### Der Stand nach dem Nachtrag

| | Nachweisgrenze | DSR-Bedarf bei 25 Versuchen |
|---|---|---|
| alter Stand (ETFs ab 2019) | 0,277 | 1,318 |
| 25 ETFs ab 2011-11-15 | 0,197 | 1,157 |
| **≥ 8 ETFs ab 2003-04-14** | **0,167** | **0,839** |

Die 0,277 in der ersten Zeile sind **nicht** die 0,335 aus dem Abschnitt
darüber, und der Unterschied ist Methode, nicht Messung: 0,335 gilt für 38
Märkte samt Krypto mit einem ρ̄ aus dem 7,7-Jahre-Fenster, 0,277 für die 25
ETFs allein mit ρ̄ aus der Gesamtstichprobe. Nur so sind die drei Zeilen
untereinander vergleichbar — der Vorher-Nachher-Vergleich gegen 0,335 wäre
sonst zum Teil ein Methodenwechsel, der als Fortschritt gelesen wird.

Beide Hürden zusammen sind halbiert, und weiterhin ohne eine neue Idee. Die
DSR bleibt die bindende: 0,839 gegen 0,167. Das beste gemessene Bruttosignal
des Repos (`trend`, +0,459) liegt jetzt deutlich über der Nachweisgrenze und
weiterhin deutlich unter dem DSR-Bedarf — **die Reihenfolge, in der die
Kriterien beißen, hat sich durch den ganzen Vorgang nicht geändert.**

`MIN_SHARPE` bleibt aus demselben Grund wie oben bei 0,33.

---

## ADR-074 — Die Tests laufen jetzt auch, wenn niemand sie startet
**Datum:** 2026-09-08

**Der Anlass steht in ADR-073:** vier Ausfälle in Folge, vier verschiedene
Ursachen, und **keiner war ein Fehler im getesteten Code** — alle saßen im
Drumherum. Bei 1047 grünen Tests ist die Fehlerquelle nicht mehr die Engine,
sondern alles, was sie startet. Und die 1047 liefen bis heute nur dort, wo
jemand sie von Hand ausführte.

### Was der Probelauf gefunden hat

Bevor irgendetwas gepusht wurde: ein **frischer Klon von `main`**, also genau
das, was die Action sieht. Ergebnis **7 rot** — und keiner davon aus dem Code.

**1. `uv sync` installiert `pytest` nicht.** `dev` steht in
`[project.optional-dependencies]`, ist also ein *Extra* und keine
Abhängigkeitsgruppe. Ohne `--extra dev` fehlt pytest in der Umgebung,
`uv run pytest` greift auf ein pytest **außerhalb** davon zurück, und der
Lauf stirbt an `ModuleNotFoundError: No module named 'numpy'` — obwohl numpy
2.4.6 installiert und importierbar ist.

Das ist die unangenehmste Sorte Fehlermeldung: sie zeigt auf eine fehlende
Bibliothek, und die Ursache ist ein fehlendes Kommandozeilen-Flag.

**2. Vier Tests lasen den echten Bar-Store.** `test_research_pipeline.py`
holte sich BTC/USD 4h über `read_bars` und fiel im Klon mit
`FileNotFoundError` durch. `/data/` ist nicht versioniert; ein frischer
Checkout hat den Store nie.

**3. Drei Tests sind gar kein CI-Problem gewesen.** `test_nim_live.py`
überspringt sich schon selbst, wenn kein Schlüssel gesetzt ist. Im Probelauf
lief es trotzdem los, weil die Testumgebung den Schlüssel aus dem Container
geerbt hatte — die drei Fehlschläge waren echte API-Aufrufe, die scheiterten.
In der Action gibt es keinen Schlüssel, also überspringen sie.

> **Nachtrag ADR-078/079:** „echte API-Aufrufe“ war der falsche Grund, und
> das ist inzwischen **reproduziert**, nicht nur vermutet. Der Probelauf lief
> mit `uv sync --extra dev`, also ohne das `nim`-Extra. In einem frischen Klon
> unter genau diesen Bedingungen scheitern dieselben drei Tests in 1,98 s an
> `ModuleNotFoundError: openai`, bevor ein Aufruf das Netz erreicht. Ein echter
> NIM-Aufruf dauert 40 bis 75 s. Seit ADR-079 ist `openai` Pflichtpaket. Die
> Schlussfolgerung für die Action bleibt unberührt.

Bemerkenswert daran ist die Reihenfolge: hätte ich die Action ohne Probelauf
gepusht, wäre sie rot geworden, und zwei der drei Ursachen hätten wie
Codefehler ausgesehen.

### Die Korrektur

Punkt 2 folgt der Konvention, die im Repo **schon steht** —
`test_walkforward.py` und `test_macross.py` machen es seit jeher so:

```python
if not parquet_path("BTC/USD", "4h").exists():
    pytest.skip("Keine gespeicherten Daten -- qt data pull")
```

Bewusst *übersprungen* und nicht durch synthetische Bars ersetzt: diese vier
sind der End-to-End-Beleg der Pipeline gegen echte Daten. Ein Ersatz wäre ein
anderer Test, der so tut, als wäre er dieser.

Dieselbe Lehre wie beim `timesfm`-Test (ADR-063), nur andersherum: dort hing
ein Test daran, dass ein Paket **fehlt**, hier daran, dass Daten **da sind**.
Beide Male prüfte er die Umgebung statt den Code.

### Was die Action tut

`push` auf `main` und **jeder** Pull Request, 20 Minuten Zeitlimit,
`concurrency` mit `cancel-in-progress` (ein zweiter Push macht den ersten Lauf
gegenstandslos). Drei Schritte: `uv sync --extra dev`, `ruff check src tests`,
`pytest -q -p no:randomly`.

Die feste Reihenfolge ist Absicht: ein Lauf, der mal grün und mal rot ist,
kostet mehr Vertrauen, als er einbringt.

**Gemessen unter genau den Bedingungen der Action** — frischer Klon, kein
Store, kein Schlüssel:

| | |
|---|---|
| Ergebnis | **1036 grün, 11 übersprungen, 0 rot** |
| Laufzeit | 32 Sekunden |
| lokal, mit Store und Schlüssel | 1047 grün in 4:23 |

Die 11 Übersprungenen nennen jeweils ihren Grund. Das ist der Unterschied
zwischen „läuft nicht" und „läuft hier nicht, und zwar deshalb".

### Was sie ausdrücklich nicht tut

- **Keine Secrets.** Die Live-Tests gegen NIM kosten Geld und 90 bis 155
  Sekunden pro Aufruf (ADR-040). Sie gehören an eine Hand, nicht an einen
  Zeitplan.
- **Keinen Datenspeicher nachziehen.** Ein `qt data pull` in der Action wäre
  20 Minuten und ein Abhängigkeit von zwei Börsen-APIs bei jedem Commit. Die
  vier Tests, die ihn brauchen, laufen dort, wo er liegt.
- **Nichts erzwingen.** Die Action meldet, sie blockiert nicht. Ob ein roter
  Lauf einen Merge verhindert, ist eine Einstellung am Repository und eine
  Entscheidung des Menschen, dem es gehört.

### Nachtrag: der erste echte Lauf war rot, und das war verdient

Der Probelauf oben hat `pytest` unter CI-Bedingungen geprüft — und `ruff`
**nicht**. Den habe ich im Arbeitsverzeichnis laufen lassen, wo er global
installiert ist. Der erste Lauf der Action starb nach 15 Sekunden:

```
error: Failed to spawn: `ruff`
  Caused by: No such file or directory (os error 2)
```

`ruff` stand nirgends in `pyproject.toml`. `uv run ruff` fiel auf den PATH
durch, und ein GitHub-Runner hat dort keinen. **Genau derselbe Fehler, den
dieses ADR eine Seite weiter oben beschreibt** — ein Werkzeug, dessen
Verfügbarkeit davon abhängt, wer es startet. Ich habe ihn beim Prüfen
wiederholt, statt ihn zu vermeiden.

Beim Eintragen kam der zweite Fund. `uv lock` wählte **ruff 0.16.6**, und
damit meldet derselbe unveränderte Code **183 Befunde** statt null. Das
Projekt hat keine `[tool.ruff]`-Sektion, ruff läuft also auf Standardregeln,
und die ändern sich zwischen Nebenversionen.

Das sind keine Funde am Code, sondern ein **neuer Maßstab**. Deshalb steht in
`pyproject.toml` eine Obergrenze:

```toml
dev = ["pytest>=8.0", "ruff>=0.15,<0.16"]
```

Einen Maßstab zu wechseln ist eine eigene Entscheidung mit eigenem Diff —
dieselbe Regel wie für die Gate-Schwellen (ADR-057). Wer 0.16 will, hebt die
Grenze und räumt die 183 auf, in einem Commit, den man lesen kann. Sie hier
nebenbei mitzunehmen hätte 183 Änderungen in einen PR geschmuggelt, der von
CI handelt.

**Danach im Klon, alle drei Schritte:**

```
uv sync --extra dev   ->  ruff==0.15.22
ruff check src tests  ->  All checks passed!
pytest                ->  1036 passed, 11 skipped   (35 s)
```

Die Lehre ist unbequem und passt zum Rest: **eine Prüfung, die nur den Teil
abdeckt, an den man gedacht hat, ist keine.** Der Probelauf hat sieben
Fehler gefunden und einen achten übersehen, weil ich `ruff` für
selbstverständlich hielt. Gefunden hat ihn die Action, in ihrem ersten Lauf —
also genau das Werkzeug, das sie sein soll.

### Die Grenze, offen benannt

Die Action fängt genau die Fehlerklasse **nicht**, die ADR-073 beschreibt.
Kein Test der Welt hätte gemeldet, dass der Routine keine Quelle hinterlegt
ist oder dass ein Prompt einen fatalen Zustand für normal erklärt — das steht
in der Konfiguration einer Plattform, nicht im Repository.

Was sie leistet, ist bescheidener und trotzdem neu: **die 1047 Tests sind ab
jetzt eine Zusage und keine Momentaufnahme.** Bisher galt „grün", weil ich es
zuletzt gesehen hatte.

---

## ADR-073 — Ein Ausfall, der sich als Erfolg meldete
**Datum:** 2026-09-06

Die tägliche Paper-Tick-Routine ist am 2026-09-06 um 01:08 UTC gefeuert, lief
5 Stunden 38 Minuten und wurde als **`SUCCEEDED`** protokolliert. Getickt hat
sie nichts: beide Konten standen danach noch auf dem `Letzter verarbeiteter
Bar` vom 2026-09-04.

Das ist das dritte Mal, dass derselbe Tick nicht ankommt, und das dritte Mal
mit einer anderen Ursache. Nach dem toten Zweig und der beschönigten
Push-Meldung (beide ADR-059) jetzt zwei, die zusammenwirken.

### Ursache 1: der Routine ist kein Repository hinterlegt

Aus der Trigger-Konfiguration, nachgesehen statt vermutet:

```
session_request.config.sources = []
```

Die Routine startet je Firing eine **frische** Sitzung
(`persist_session: false`). Ohne Quelle ist der Container leer — kein `.git`,
kein `scripts/paper_tick.sh`. Diese Sitzung hier hat dieselbe Umgebung
(`env_01JAALC8vcs6kZEYZGJpbDy7`) und trägt die Quelle in ihrem eigenen
`session_context`; die Umgebung liefert sie also nicht mit.

**Von hier aus nicht behebbar.** `update_trigger` kennt Name, Zeitplan,
Zustand, Modell und Prompt — `sources` nicht. Der dauerhafte Fix gehört in
die Routinen-Oberfläche und ist Handarbeit.

### Ursache 2: ein Satz, der den Ausfall für normal erklärte

Im Prompt der Routine stand, seit ihrer Anlage am 2026-09-02:

> „Falls das Repo im Container fehlt oder data/ohlcv leer ist: das ist
> erwartet und kein Fehler. Das Skript zieht bei kaltem Store selbst genug
> Historie nach."

Der Satz vermengt zwei Dinge, die nichts miteinander zu tun haben:

| | Bedeutung |
|---|---|
| **leerer Datenspeicher** | tatsächlich normal — `COLD_START_BUFFER` zieht nach |
| **fehlendes Repository** | fatal — es gibt nichts auszuführen |

Weil beides in einem Satz stand und die zweite Hälfte des Satzes nur die
erste Hälfte begründet, hat die gefeuerte Sitzung den leeren Container
korrekt gegen ihre Anweisung geprüft, ihn für erwartet befunden und sich ohne
Kommentar beendet. **Der Prompt war nicht nur falsch, er war eine Anleitung
zum Wegsehen.**

### Was das teuer macht

Ursache 1 allein wäre ein sichtbarer Fehlschlag gewesen: ein Lauf, der
`bash: scripts/paper_tick.sh: No such file` meldet, fällt auf. Ursache 2 hat
daraus eine grüne Zeile in der Routinen-Historie gemacht. **Ein Ausfall, der
sich als Erfolg meldet, ist teurer als einer, der abstürzt** — er verbraucht
kein Vertrauen, er baut falsches auf.

Dasselbe Muster wie ADR-051 (das Paper-Konto lief wochenlang nicht, während
die Prosa sagte, es laufe) und ADR-059 (vier gescheiterte Push-Versuche,
gemeldet als „Kontostand gesichert"). Dreimal derselbe Bauplan: eine
Behauptung über den Zustand, die den Zustand nicht prüft.

**Die grüne Zeile bedeutet ohnehin nicht, was sie zu bedeuten scheint.** Das
ist keine Schlussfolgerung aus dem Verhalten, sondern steht so in der
Dokumentation der Routinen:

> „A green status in the run list means the session started and exited
> without an infrastructure error. **It does not mean the task in your prompt
> succeeded.** Open the run to read the transcript and confirm what Claude
> actually did."
>
> — [code.claude.com/docs/en/routines](https://code.claude.com/docs/en/routines)

Der Status beantwortet also eine Frage über die *Infrastruktur*, und gelesen
wurde er als Antwort über die *Aufgabe*. Genau deshalb prüft der korrigierte
Prompt jetzt selbst nach (Schritt 3): die einzige Zusage, die der Status
gibt, ist die, die uns nicht interessiert.

### Die Korrektur am Prompt

- Ein **fehlendes Repository ist ein Fehler**, kein erwarteter Zustand. Der
  leere Datenspeicher bleibt ausdrücklich normal — die beiden Fälle stehen
  jetzt getrennt und mit dem Grund für die Unterscheidung.
- Die Sitzung **holt das Repository selbst** (`add_repo`, klonen,
  `register_repo_root`, `main` auschecken), bevor sie irgendetwas anderes
  tut. Gelingt das nicht, ist der Lauf gescheitert und wird so gemeldet.
- **Erfolg wird an der bewegten Zahl gemessen, nicht am Exit-Code.** Schritt 3
  liest `Letzter verarbeiteter Bar` und verlangt, dass er weitergewandert ist.
- Der Zweigname ist raus. Er nannte noch
  `claude/llm-quant-algo-planning-f1ohgo` — den Zweig aus ADR-059, dessen
  Pull Request längst zusammengeführt ist. Das Skript wählt seit ADR-069
  selbst, wohin es schreibt.

### Ein Nebenertrag: ADR-068 ist im Vorwärtsbetrieb bestätigt

Die zwei nachgeholten Bars haben beide Konten long gehen lassen — der erste
echte Einstieg seit dem Neustart, und damit der erste Test der
Kaufkraft-Korrektur außerhalb eines Replays.

| | BTC/USD | ETH/USD |
|---|---|---|
| Menge | +1,246988 @ 79.714,96 | +40,443663 @ 2.457,83 |
| Cash danach | **−0,00** | **0,00** |
| vorhergesagt (Replay) | 0,00 | 0,84 |
| vor der Korrektur | −650,31 | −656,55 |

Genau an dieser Stelle hätte Phase D eine Divergenz zwischen Backtest und
Ausführung gefangen. Es ist keine mehr da.

### Nachtrag 2026-09-07: die Notlösung trägt nicht

Der Lauf vom 2026-09-07 um 01:09 ist der Test des korrigierten Prompts. Er
fällt zweigeteilt aus.

**Was funktioniert hat:** der Ausfall ist sichtbar. Am 2026-09-06 lief
dieselbe Routine 5 Stunden 38 Minuten und meldete Erfolg; am 2026-09-07 bricht
sie nach **2 Minuten** mit einem Fehler ab, den der Nutzer gemeldet bekommt.
Genau dafür war die Änderung da, und mehr war von einer Prompt-Änderung auch
nicht zu erwarten.

**Was nicht funktioniert hat:** die Selbstheilung. Die Anweisung, das
Repository über `add_repo` selbst zu holen, führt nicht zu einem
Arbeitsverzeichnis — die Sitzung trägt weiterhin `sources: []` und den Tag
`config:routine-lineage-none`, und eine Sitzung ohne hinterlegte Quelle kommt
so nicht an das Repository heran. Die Konten standen danach unverändert auf
dem Bar vom 2026-09-06, und auf `main` liegt kein Tick-Commit.

**Damit ist der Weg über den Prompt ausgereizt.** Er kann einen Ausfall
sichtbar machen; er kann keine fehlende Quelle ersetzen. Die
`add_repo`-Anleitung ist wieder heraus — sie kostet zwei Minuten und
verwässert den Fehlerbericht. An ihrer Stelle steht jetzt der Satz, den
derjenige liest, der den Fehler bekommt: *„Der Fix gehört in die
Routinen-Oberfläche: `Maxifrz/quant` als Quelle der Routine eintragen."*

Eine Alternative wäre gewesen, die Routine an eine **dauerhafte** Sitzung zu
binden, die das Repository trägt (`persistent_session_id`). Verworfen nach
Rücksprache: das hält eine Sitzung auf unbestimmte Zeit am Leben, deren
Kontext mit jedem Tick wächst, und tauscht ein Konfigurationsproblem gegen
eine Sonderkonstruktion mit eigener Wartung.

### Konsequenzen

- **Der Prompt ist repariert, die fehlende Quelle nicht** — und sie ist von
  innen auch nicht reparierbar. `update_trigger` kennt Name, Zeitplan,
  Zustand, Modell und Prompt; `sources` gehört zur Session-Konfiguration der
  Routine und wird in ihrer Oberfläche gesetzt. **Bis das geschieht, tickt
  die Routine nicht**, und die Konten hängen an Handarbeit.
- **Vier Ausfälle, vier Ursachen, ein Muster.** Toter Zweig, beschönigte
  Push-Meldung (beide ADR-059), fehlende Quelle plus ein Satz, der sie für
  normal erklärte, und jetzt eine Notlösung, die nicht greifen konnte. Keiner
  davon war ein Fehler im getesteten Code — alle vier saßen im Drumherum, das
  kein Test abdeckt.
- **Für die Prompts automatischer Routinen gilt dieselbe Regel wie für
  Prosa im ROADMAP** (ADR-052): kein Satz, der einen Zustand behauptet, ohne
  ihn zu prüfen. Ein Prompt ist Code mit schlechterem Werkzeug — und ohne
  Test.

---

## ADR-072 — Der erste Loop-Lauf, dessen Zahlen etwas bedeuten
**Datum:** 2026-09-04

Drei Kandidaten, `--provider nim`, alle 39 Märkte auf 1d, Walk-Forward
1500/400/20. Versuchszähler **21 → 24**. Die Zahl der Kandidaten war eine
bewusste Entscheidung eines Menschen: sie ist die einzige in diesem Repo, die
sich nicht zurücknehmen lässt.

| | OOS-Sharpe | DSR | gegen |
|---|---|---|---|
| `keltner_breakout` | **−0,16** | 0,013 | 22 Versuche |
| `dual_momentum` | −0,87 | 0,000 | 24 Versuche |
| `volume_z_momentum` | −5,01 | 0,000 | 23 Versuche |

Keiner besteht. Das ist der Normalfall (ADR-005).

### Warum dieser Lauf trotzdem anders ist

**Die beiden Blocker aus ADR-065 sind beide weg, und beide haben gewirkt.**

*Erstens, das Gedächtnis.* Am 2026-09-03 waren drei von fünf Kandidaten
Neuauflagen von `macross`, `trend` und `meanrev` — drei Versuche für längst
verworfene Hypothesen. Heute: Keltner-Ausbruch, Volumen-z-Momentum, Dual
Momentum. Keine davon steht in der Bibliothek. (Ehrlich dazu: `dual_momentum`
ist Momentum-Familie und damit thematisch in der Nähe von `trend` — es ist
eine andere Regel, keine andere Idee.)

*Zweitens, die Positionsgrößen-Schicht.* Nachgeprüft an jedem der drei, über
alle 39 Märkte:

| | Sharpe (voll) | Brutto Median | Cash min | Umschlag | ruiniert |
|---|---|---|---|---|---|
| `KeltnerBreakout` | +0,293 | 0,317 | −57.594 | 12,4× | nein |
| `VolumeZMomentum` | −1,560 | 0,501 | −88.441 | **307,7×** | nein |
| `DualMomentum` | −0,306 | 0,258 | +15.633 | 33,9× | nein |

**Keiner ruiniert das Konto.** Am 2026-09-03 taten es alle fünf, und die
kaputte Kennzahl meldete dafür Sharpe +0,59 (ADR-066).

### Ein Brutto von 5,94 — nachgesehen, nicht weggewunken

`VolumeZMomentum` erreicht in der Spitze ein Bruttoexposure von 5,94. Das
sieht nach einem Versagen der Schicht aus und ist keins:

```
Maximum 5,943 am 2021-05-21
  Eigenkapital     4.138   (Start 100.000)
  Positionswert   24.592
Endkapital              17
```

**Der Nenner bricht weg, nicht die Grenze reißt.** Über alle 527 Bars mit
Brutto > 1,01 liegt das Eigenkapital im Median bei **94 USD** von ursprünglich
100.000. In diesem Bereich ist das Verhältnis arithmetisch bedeutungslos.
Gemessen am *Startkapital* — wo der Nenner nicht wegbrechen kann — liegt der
Positionswert im Median bei 0,000.

Die Schicht hält also, was sie verspricht: **sie verhindert Hebel, nicht
schlechte Ideen.** Eine Strategie mit 307,7× Umschlag zahlt unter 130 bps
Round-Trip rund 400 % des Eigenkapitals pro Jahr an Gebühren; dass davon 17
USD übrig bleiben, ist Arithmetik und kein Fehler.

### Was der Lauf über den Trichter sagt

**Alle drei reißen das Umschlagbudget** (12,4×, 307,7×, 33,9× gegen 7×) — und
nach ADR-071 wäre ihr *erlaubter* Umschlag ohnehin 0×, weil schon der
Netto-OOS-Sharpe negativ ist. Der Vorfilter greift also nicht zufällig
richtig.

**Die Kritik-Stufe hat keinen einzigen abgelehnt.** Drei Kandidaten, von denen
einer 307× pro Jahr umschlägt, sind vollständig durch Sandbox, Kritik und
Sanity-Check gelaufen. `critic_unrealistic_turnover` ist ein Feld in der
Registry — es hat hier nicht angeschlagen. Das ist der nächste billige
Vorfilter, der offensichtlich noch nicht filtert.

### Ein Vorbehalt zur Registry

Die sechs Zeilen vom 2026-09-03 tragen Sharpes zwischen **+0,67 und −0,42**,
und sie sind mit der kaputten Kennzahl aus ADR-066 gemessen — auf ruinierten
Konten. Sie stehen bewusst unverändert da: die Registry ist ein Protokoll
dessen, was ein Lauf gemeldet hat, kein nachgeführter Bestand. Wer sie
zitiert, braucht diesen Absatz. Ein Neuberechnen würde den Versuchszähler
nicht bewegen (er zählt Zeilen, nicht Läufe), aber es würde das Protokoll
überschreiben.

Damit ist **−0,16 die beste belastbare Zahl, die dieser Loop je geliefert
hat**: die Charge vom 2026-08-31 lag zwischen −1,84 und −8,21, die vom
2026-09-03 ist nicht verwendbar.

### Konsequenzen

- Versuchszähler **24**. Erwarteter bester Sharpe aus reinem Rauschen:
  **1,980** (vorher 1,922 bei 21).
- **Der nächste billige Vorfilter ist die Kritik-Stufe.** Ein Kandidat mit
  307× Umschlag hätte vor dem Walk-Forward auffallen müssen und ist
  durchgewinkt worden.
- **Die Schicht ist im echten Lauf bestätigt**, mit einem nachgesehenen und
  erklärten Randfall statt einer Behauptung.

---

## ADR-071 — Das Umschlagbudget bindet bei keiner Strategie
**Datum:** 2026-09-04

**Die Frage:** Nach ADR-069 scheitern **alle neun** Bibliotheksstrategien am
Umschlagbudget von 7×/Jahr — keine kommt bis zum Sharpe. Der naheliegende
Schluss wäre: das Budget ist die Hürde, und wer es senkt, kommt weiter. Der
naheliegende Reflex wäre, eine Strategie langsamer zu stellen, bis sie
darunter liegt.

Beides ist falsch, und die Identität aus ADR-056 zeigt es, ohne dass ein
Parameter gesucht werden muss.

### Rückwärts gerechnet

```
Drag p.a. = Umschlag × Einwegkosten
Brutto    = Netto + Drag / Vola
erlaubt   = (Brutto − 0,33) × Vola / Einwegkosten
```

`Netto` ist der OOS-Sharpe der verketteten Walk-Forward-Kurve — die Zahl,
gegen die Gate 1 prüft. Daraus ergibt sich der Brutto-Sharpe und damit die
Frequenz, bei der die Strategie gerade noch über der Nachweisgrenze landet.

**Datenstand 2026-09-04, 1d, `coinbase_taker`, Walk-Forward 1000/250/20:**

| Strategie | Markt | Umschlag ist | OOS netto | Drag | OOS brutto | **erlaubt** |
|---|---|---|---|---|---|---|
| `macross` | BTC/USD | 8,7× | +0,250 | 5,68 % | +0,418 | **4,6×** |
| `macross` | ETH/USD | 8,7× | +0,276 | 5,66 % | +0,403 | **5,0×** |
| `trend` | BTC/USD | 15,2× | +0,160 | 9,89 % | +0,459 | **6,6×** |
| `elliott` | BTC/USD | 13,2× | +0,145 | 8,56 % | +0,334 | **0,3×** |
| `hashribbon` | BTC/USD | 7,1× | +0,123 | 4,60 % | +0,224 | **0,0×** |
| `meanrev` | ETH/USD | 16,9× | −0,923 | 10,98 % | −0,707 | **0,0×** |

**Keine einzige darf 7×.** Das globale Budget ist bei keiner die bindende
Grenze — es ist durchweg *großzügiger* als das, was die Strategie sich
tatsächlich leisten kann.

### Was daraus folgt

**Der Satz „scheitert nicht am Signal, sondern an der Handelsfrequenz"
(ADR-057) ist nur halb richtig.** Richtig ist: `macross` kann sich seine 8,7×
nicht leisten. Falsch ist die Umkehrung — bei 6,9× hätte es das Gate bestanden
und wäre am Sharpe gescheitert, weil seine eigene Grenze bei 4,6× liegt.

**Und für zwei ist es ganz falsch.** `hashribbon` (brutto +0,224) und
`meanrev` (brutto −0,707) liegen schon **ohne jede Kostenbelastung** unter der
Nachweisgrenze. Für sie gibt es keine Frequenz, die hilft, auch nicht die
Frequenz null. Ihre Ablehnung ist richtig, der berichtete Grund ist es nicht.

**Der Umschlag ist ein Vorfilter, keine Aussage.** Genau so ist er in ADR-056
gedacht und in ADR-057 gebaut: er kostet einen Bruchteil eines Walk-Forward
und fängt ab, was ohnehin nicht durchkommt. Die Zahl, die etwas über die
Strategie sagt, braucht den Walk-Forward — also genau den Schritt, den der
Vorfilter spart. Beides gleichzeitig geht nicht, und die Reihenfolge ist
richtig gewählt.

**Nicht geändert: die 7×.** Sie als strategiespezifische Grenze auszulegen
hieße, den Vorfilter durch den Schritt zu ersetzen, den er spart. Und sie
anzuheben, weil sie „ohnehin nicht bindet", wäre die Latte zu senken, ohne
dass eine einzige Zahl besser würde.

### Ein Nebenbefund, der einer Korrektur bedarf

Auf **Brutto**-OOS-Sharpe ist `trend` mit **+0,459** die beste Strategie des
Repos — vor `macross` mit +0,418. Seit ADR-035 gilt `macross` als „die einzige
Hoffnung des Projekts"; das stimmt für den Netto-Sharpe und nicht für das
Signal darunter.

Der Vorbehalt gehört unmittelbar dazu und ist groß: Brutto-Sharpe ist eine
**abgeleitete** Größe aus einer Näherungsidentität, kein Messwert. Und die
Rechnung unterstellt, dass eine langsamere Fassung derselben Idee denselben
Brutto-Sharpe hätte — was sie nicht tut, weil weniger Handeln ein anderes
Signal ist. Wer aus dieser Zeile „bau ein langsameres `trend`" liest, hat
einen Versuch ausgegeben, um eine Zahl anzupassen, die er selbst erzeugt hat.

Der methodische Wert liegt woanders: **die 0,33 ist erreichbar, aber knapp.**
Die zwei besten Signale des Repos liegen brutto 0,09 bzw. 0,13 darüber, und
diese Spanne muss die gesamte Ausführung bezahlen. Das ist eine schärfere
Formulierung des Befunds aus ZIEL.md („was fehlt, ist ein Signal") als jede
Zählung gescheiterter Hypothesen.

### Konsequenzen

- **`erlaubter_umschlag()`** in `qt.research.gate`, mit vier Tests. Eine
  Diagnose, keine Hürde — die Gate-Schwellen bleiben unverändert und
  hartverdrahtet (ADR-057).
- **Die Umschlagzeile in ROADMAP und ZIEL.md bekommt ihren Vorbehalt.** Ein
  falscher Grund für eine richtige Entscheidung wird zitiert, bis jemand auf
  seiner Grundlage anders entscheidet.
- **Methodisch:** die Zahlen oben sind gemischt — Umschlag aus dem vollen
  Lauf (so rechnet das Gate), Sharpe und Vola aus der OOS-Kette. Sauberer
  wäre der Umschlag je Testfenster; die Größenordnung ändert das nicht, die
  dritte Stelle schon.
- **Der erste Anlauf dieser Rechnung war falsch** und hätte behauptet,
  `macross` könne sich 56× leisten: er benutzte den In-Sample-Sharpe von
  +1,026 statt der OOS-Zahl +0,250. Der Faktor zwischen beiden ist 4 — und
  genau dieser Faktor ist der Grund, warum es das Gate überhaupt gibt.

---

## ADR-070 — Der Spread ist messbar, die Gebühr nicht
**Datum:** 2026-09-04

Phase B (ADR-056) endete mit drei ausdrücklich offenen Punkten: der reale
Spread, die Warteschlangenposition bei Limit-Orders, und ob Coinbases 0,60 %
stimmen. Für alle drei stand dort „brauchen echte Fills". Für einen davon
stimmt das nicht.

### Der Spread — vier gescheiterte Schätzungen, und warum

| Versuch | Datenbasis | Ergebnis |
|---|---|---|
| Corwin/Schultz | Tages-OHLC | klemmt bei null (ADR-056) |
| Abdi/Ranaldo | Tages-OHLC | zu hoch, Verzerrung wächst mit der Vola (ADR-056) |
| Tick-Median | 1,48 Mio. Ticks | von der Tickgröße dominiert (ADR-067) |
| Minutenweise | 1,48 Mio. Ticks | **negative** Spannen, fängt Kursdrift (ADR-067) |

Vier Anläufe, ein Befund: **ohne Quotes geht es nicht.** Das ist bestätigt und
nicht widerlegt.

**Quotes gibt es.** Öffentlich, ohne Schlüssel, im Orderbuch. Was in ADR-056
und ADR-067 fehlte, war nicht die Möglichkeit, sondern die Frage — beide Male
wurde versucht, den Spread aus Kursreihen zu *rekonstruieren*, statt ihn dort
zu holen, wo er steht.

### Gemessen, 2026-09-04, Coinbase

Nicht an der Spitze des Buchs — dort steht eine Spanne für eine unendlich
kleine Order, und die hat noch nie jemand gehandelt. Gemessen wird der
volumengewichtete Preis bis zur gewünschten Menge:

**2.560 USD** (ein Markt von 39 bei 100k Konto):

| Markt | Kauf | Verkauf | halb |
|---|---|---|---|
| BTC-USD | 0,001 | 0,001 | **0,001** |
| ETH-USD | 0,188 | 0,647 | 0,418 |
| LTC-USD | 1,648 | 2,199 | 1,924 |
| ADA-USD | 2,456 | 2,217 | 2,337 |
| ALGO-USD | 6,266 | 7,080 | **6,673** |
| XLM-USD | 3,057 | 4,632 | 3,845 |
| DOGE-USD | 2,479 | 1,915 | 2,197 |
| **Median** | | | **2,197** |

**25.000 USD** (eine konzentrierte Position): Median **6,357**, BTC 0,236,
ALGO 22,532.

### Was das heißt — und was nicht

**Die Annahme von 2 bps ist auf dem Median richtig**, für die Ordergröße, die
eine diversifizierte Strategie tatsächlich handelt: 2,197 gemessen gegen 2,0
angenommen. ADR-067 hat sie unter „vermutlich zu hoch" geführt und den Effekt
auf rund 0,004 Sharpe geschätzt. Das war ein Bauchgefühl in die falsche
Richtung — sie ist eher minimal zu **niedrig**.

**Falsch ist nicht ihr Niveau, sondern dass es eine einzige Zahl ist.** Über
die Märkte streut sie um drei Größenordnungen (BTC 0,001, ALGO 6,673), und mit
der Ordergröße verdreifacht sie sich. Das Kostenmodell kennt seit ADR-055
bereits Gebühren je Symbol; der halbe Spread ist der verbliebene globale Wert.

**Nicht geändert, und der Grund zählt.** Eine Momentaufnahme ist keine
Historie. Gemessen ist, was heute in einem ruhigen Moment gilt — nicht was
2019 galt und erst recht nicht, was im nächsten Absturz gilt, also genau dann,
wenn eine Trendstrategie handelt. Den Default auf eine Momentaufnahme zu
setzen, hieße eine belegte Annahme durch eine schlechter belegte zu ersetzen.
Was sich ändert, ist der Status: aus „ANNAHME, nicht gemessen" wird „Annahme,
auf dem Median einer Momentaufnahme bestätigt, streut je Markt um drei
Größenordnungen".

**Reproduzierbar statt einmalig:** `qt spread`. Eine Zahl, die nur in einem
ADR steht, veraltet still (ADR-053).

### Die Gebühr bleibt unbelegbar

Die 0,60 % stehen auf drei übereinstimmenden Sekundärquellen. Vier Versuche
an Primärquellen, heute:

```
api.exchange.coinbase.com/fees                          401
api.coinbase.com/api/v3/brokerage/transaction_summary   401
www.coinbase.com/advanced-fees                          403  (Cloudflare)
api.exchange.coinbase.com/products/BTC-USD              200  (keine Gebühren)
```

**Die beiden Endpunkte, die es beantworten würden, sind genau die hinter der
Anmeldung.** Das ist kein Netzproblem und keine Nachlässigkeit: der
Gebührensatz ist kontoabhängig (er hängt am 30-Tage-Volumen), deshalb gibt es
ihn nicht ohne Konto. Die Frage wird von der **ersten echten Order**
beantwortet und von nichts davor.

Der dritte Punkt, die Warteschlangenposition, bleibt aus demselben Grund
offen: `qt maker` liefert eine Obergrenze (ADR-064), die Warteschlange selbst
braucht eigene Fills.

### Konsequenzen

- **`qt spread`** misst den effektiven halben Spread am Orderbuch, bei
  wählbarer Ordergröße. Drei Tests, keiner braucht Netz.
- **Der Default bleibt bei 2,0 bps**, jetzt mit Beleg statt mit „ANNAHME".
- **Ein Kandidat für später, nicht für jetzt:** halber Spread je Symbol,
  analog zu den Gebühren. Dafür fehlt Historie, nicht Code — eine
  Momentaufnahme je Markt wäre 39 Zahlen mit demselben Vorbehalt.
- **Zwei der drei offenen Phase-B-Punkte bleiben offen**, und jetzt steht
  präzise da, warum: nicht „brauchen echte Fills" als Sammelbegründung,
  sondern zweimal HTTP 401 auf die einzigen Endpunkte, die antworten würden.

---

## ADR-069 — Die Positionsgrößen-Schicht, und das Band maß die falsche Größe
**Datum:** 2026-09-04

**Die Aufgabe:** ADR-065 hat den Zustand so beschrieben — über 38 Märkte
summiert sich das Bruttoexposure auf 38× und ruiniert das Konto, bei
Normierung auf 1 handelt keiner mehr, und **dazwischen liege keine
Einstellung, die das Ergebnis der Idee zeigen würde.** Das war der letzte
offene Punkt vor dem nächsten Research-Loop.

Es gibt sie. Sie besteht aus zwei Teilen, und der zweite stand nicht in der
Aufgabe.

---

### Teil 1 — Die Regel, vorab entschieden

**Proportional auf das Bruttobudget skalieren, sonst nichts:**

```
w_i  ->  w_i * grenze / Σ|w|   , falls Σ|w| > grenze
w_i  ->  w_i                   , sonst
```

Damit teilen sich die Märkte, in denen ein Kandidat eine Meinung hat, das
Konto gleichmäßig — und ein Kandidat mit einer Meinung in einem einzigen
Markt bekommt dort das volle Gewicht.

Zwei Alternativen sind **vorab** verworfen, damit die Entscheidung nicht davon
abhängt, wie der nächste Lauf ausgeht:

| | verworfen, weil |
|---|---|
| **Vol-Targeting** | braucht Schätzer, Rückschaufenster und Zielvola — drei Freiheitsgrade, die jeder im DSR-Nenner bezahlt werden (ADR-005). Und es hat einen eigenen Effekt auf den Sharpe: ein Ergebnis aus Signal *und* Größensteuerung beantwortet keine der beiden Fragen. |
| **Gleichgewichtung 1/n** | bestraft Selektivität. Ein Kandidat mit einer Position in 1 von 38 Märkten bekäme 1/38 Exposure — von Rauschen nicht zu unterscheiden, egal wie gut das Signal ist. |

Der ausschlaggebende Punkt: **das ist keine Strategieentscheidung, sondern die
Bilanz.** `max_gross_exposure` heißt laut Config „1.0 = kein Hebel". Von den
drei Kandidaten setzt nur die proportionale Skalierung genau diese Grenze
durch, statt nebenbei eine Meinung zu haben. Sie ist auch nicht neu:
`CrossSectionalStrategy.gewichte_aus_score` teilt seit ADR-058 durch Σ|w|.
Diese Schicht macht daraus die Regel für alle statt für eine Familie.

### Wo sie sitzt — nach zwei gemessenen Fehlversuchen

Der naheliegende Ort war `rebalance_order`. Beide Anläufe dort sind
gescheitert, und beide Male hat die Messung es gezeigt, nicht das Nachdenken:

**Anlauf 1, Skalierung des Ziel-Dicts der Engine.** Die Engine arbeitet die
Bars eines Zeitpunkts nacheinander ab; währenddessen ist das Ziel-Dict eine
Mischung aus alten und neuen Gewichten. Bei `crossmom` liegt diese Mischung im
Median bei **1,09** Brutto, obwohl weder der alte noch der neue Stand 1,0
reißt. Die Grenze griff in **77,7 %** der Bars, sparte Gebühren und hob den
Sharpe von **0,21 auf 0,38** — über die Gate-Schwelle von 0,33.

Das ist der Grund, warum dieser Anlauf im Papierkorb liegt und nicht im Code:
**eine Verbesserung aus einem Messartefakt**, genau die Sorte Fund, vor der
ADR-066 warnt. Aufgefallen ist sie, weil die Zahl in die angenehme Richtung
sprang.

**Anlauf 2, Budget gegen die gehaltenen Positionen.** Kein Artefakt mehr, aber
`crossmom` zielt konstruktionsbedingt auf Brutto genau 1,0. Eine harte Grenze
auf demselben Wert liegt dauernd auf der Kante: jede Kursbewegung beschneidet
die nächste Order, das erzeugt eine Gegenbewegung. Ausführungen 308 → 923,
Sharpe 0,21 → −0,21.

Beide Male dieselbe Ursache: **die Engine sieht nie einen kohärenten
Zielvektor.** Die Querschnittsfamilie löst das seit ADR-058 selbst, die
Bibliotheksstrategien halten je ein Symbol. Übrig bleibt genau der Fall, für
den die Schicht da ist — ein Kandidat, dessen Gewichte je Symbol unabhängig
entstehen. Für den ist die Mischung kein Artefakt, sondern der Zustand: 38
Märkte mit je 1,0 summieren sich zu 38, egal aus welchem Bar der einzelne Wert
stammt.

Deshalb sitzt sie als Hülle um den Kandidaten, im Research-Loop, hinter dem
Probelauf und vor allem, was eine Zahl erzeugt.

---

### Teil 2 — Und dann handelte er nicht mehr

Die Schicht allein reproduzierte die andere Hälfte des ADR-065-Satzes. Über 39
Märkte, `immer_long` als Kandidat:

| | ohne Schicht | mit Schicht |
|---|---|---|
| Brutto im Median | **16,2** | 0,502 |
| Brutto maximal | 25.505 | 2,31 |
| tiefstes Cash | −25.353.646 | −128.439 |
| Konto ruiniert | **ja** | nein |
| Endwert | −30 | 215.256 |

Brutto **0,502** bei einem Ziel von 1,0: ein Portfolio, das die Hälfte dessen
hält, was es will — eingefroren aus der Anlaufphase, 49 Ausführungen in 7,7
Jahren.

**Die Ursache ist ein Kategorienfehler im Rebalancing-Band.** Es war ein
Anteil des **Eigenkapitals** (5 %). Was es aber beschreibt, ist eine Toleranz
um eine **Position**. Solange ein Konto ein Symbol mit Gewicht 1,0 hält, ist
das dasselbe. Sonst nicht:

| Märkte | Position | altes Band, in Vielfachen der Position |
|---|---|---|
| 1 | 100,00 % | **0,05×** |
| 13 | 7,69 % | 0,65× |
| 26 | 3,85 % | 1,30× |
| 39 | 2,56 % | **1,95×** |

Eine Position musste sich also fast verdreifachen oder verschwinden, bevor
gehandelt wurde. Das ist dieselbe Fehlerfamilie wie ADR-053 und ADR-065:
**eine Größe, die für ein Symbol gedacht ist, wird gegen ein Portfolio
geprüft.** Drittes Auftreten, dritter Ort.

Behoben, indem das Band misst, was es beschreibt:

```python
bezug = max(|ziel_menge|, |ist_menge|) * preis
schwelle = max(min_trade_notional, rebalance_band * bezug)
```

Bei Vollgewicht auf einem Symbol ist das **exakt** die alte Zahl. Danach hält
derselbe Kandidat Brutto **0,995** statt 0,502, bei 3,90× Umschlag gegen ein
Budget von 7 — die Einstellung zwischen Bankrott und Untätigkeit, die es laut
ADR-065 nicht gab.

---

### Was das an bestehenden Zahlen bewegt

**Bitgleich geblieben**, weil sie nur Gewicht 1,0 oder 0 handeln:

| | Sharpe vorher | nachher | Fills |
|---|---|---|---|
| `macross` BTC/USD | +1,0263 | +1,0263 | 67 → 67 |
| `macross` ETH/USD | +1,0091 | +1,0091 | 67 → 67 |
| `hashribbon` BTC/USD | +0,7467 | +0,7467 | 54 → 54 |

**Leicht bewegt**, weil sie Bruchgewichte handeln — alle drei nach unten, also
in die unbequeme Richtung:

| | vorher | nachher | Δ |
|---|---|---|---|
| `trend` BTC/USD | +0,4291 | +0,4284 | −0,0007 |
| `meanrev` ETH/USD | −1,0724 | −1,0743 | −0,0019 |
| `elliott` BTC/USD | +0,1318 | +0,1284 | −0,0034 |

**Und einer deutlich — mit einer unbequemen Folge.**

`crossmom` war die einzige Strategie des Projekts, die das Umschlagbudget
bestand (2,9× gegen 7×) und bis in den Walk-Forward kam. Mit dem korrigierten
Band schlägt sie **10,8×** um und bricht vor dem Sharpe ab. `crossrev` geht
von 13,5× auf 27,0×.

Der Grund ist kein neues Verhalten der Strategie, sondern das Ende einer
stillen Subvention: bei 2,56 % Positionsgröße bekam sie eine Toleranz von
1,95× ihrer eigenen Position, also das **39-fache** dessen, was `macross`
bekam. Ihre 2,9× Umschlag waren nicht die Kosten ihrer Idee, sondern die
Kosten des Rebalancings, das die Engine nicht ausgeführt hat.

**Damit steht die Schlussfolgerung aus ADR-058 weiter, aber mit anderem
Grund.** Dort scheiterte `crossmom` am OOS-Sharpe von −0,24. Jetzt kommt sie
gar nicht mehr so weit: sie kann sich ihre eigene Umschichtung nicht leisten —
dieselbe Diagnose wie bei `macross` (ADR-057). Ein falscher Grund für eine
richtige Entscheidung wird zitiert, bis jemand auf seiner Grundlage eine
andere Entscheidung trifft; deshalb steht er hier.

### Konsequenzen

- **Der Research-Loop hat jetzt eine Positionsgrößen-Schicht** (`BRUTTOGRENZE
  = 1.0`, `qt.research.groesse`) — bewusst **keine** Option des Loops. Wer sie
  ändert, hinterlässt einen Diff, so wie bei den Gate-Schwellen (ADR-057).
- **Der Bestand ist damit vollständig durch das Umschlagbudget gefallen.**
  Neun Strategien, keine unter 7× — die knappste ist `hashribbon` mit 7,1×.
  Was Gate 1 heute blockiert, ist nicht der Sharpe, sondern die
  Handelsfrequenz.
- **Die Hülle behält den Namen des Kandidaten.** Ein zweiter Name wäre eine
  zweite Hypothese im Versuchszähler und damit eine Erhöhung der DSR-Hürde
  durch die Hintertür (ADR-032).
- **Elf Tests**, davon einer, der den Ausgangszustand festhält: ohne Schicht
  muss ein Kandidat über 20 Märkte das Konto ruinieren. Fällt er, misst die
  Korrektur nicht mehr, was sie soll.
- **Offen benannte Grenze:** ein generierter Kandidat, der seinen ganzen
  Vektor auf einmal umschichtet, fällt in dieselbe Falle wie Anlauf 1. Ein
  solcher ist bisher nicht aufgetreten; taucht einer auf, gehört die Frage neu
  entschieden und nicht stillschweigend gelöst.
- **Kein einziger Test wurde rot,** als das Band die Bedeutung wechselte —
  eine Änderung, die die Order-Schwelle jedes Backtests im Repo betrifft. Die
  Semantik des Bandes war nirgends festgehalten. Jetzt ist sie es.

---

## ADR-068 — „1.0 = kein Hebel" stimmte nicht, und das Paper-Konto zeigte es
**Datum:** 2026-09-03

**Der Fehler:** `rebalance_order` rechnete `target_qty = gewicht × equity /
preis`. Ein Kauf über das volle Eigenkapital kostet damit **genau** das
Eigenkapital an Gegenwert — und die Gebühr kommt obendrauf. Das Konto rutscht
um sie ins Minus und hält mehr Position, als es Kapital hat.

`BacktestConfig.max_gross_exposure` beschreibt sich daneben mit
„**1.0 = kein Hebel**".

---

### Wie er aufgefallen ist

Nicht durch eine Suche, sondern beim Zustandscheck nach einem
Container-Neustart. Das Paper-Konto meldete:

```
Cash: -650.31
Positionen: BTC/USD  Menge +1.292012  Einstand 77437.40  Wert 100,568.52
Fills gesamt 1, Gebuehren 600.30
```

Nachgerechnet: 1,292012 × 77.437,40 = 100.050,05 Gegenwert, plus 600,30
Gebühr = **100.650,35** aus 100.000 Startkapital. Der Fehlbetrag ist exakt
die Gebühr.

Über die volle Historie von `macross` auf BTC/USD 1d:

| | vorher |
|---|---|
| Hebel im Markt (Median) | **1,0056** |
| Hebel maximal | 1,0085 |
| Bars mit negativem Cash | **1.496 von 2.803** (53 %) |
| tiefstes Cash | −12.928,47 |

53 % — also jeder Bar, an dem eine Position gehalten wird.

### Warum es niemandem aufgefallen ist

**Der Sharpe merkt es nicht.** Ein Hebel skaliert Mittelwert und Volatilität
mit demselben Faktor; der Quotient bleibt. Gemessen:

| | vorher | nachher |
|---|---|---|
| Sharpe | +1,0146 | **+1,0148** |
| Gesamtrendite | +1394,30 % | **+1381,87 %** |
| Max Drawdown | −58,39 % | −58,19 % |
| Trades | 67 | 67 |

Und der Sharpe ist die Zahl, an der in diesem Projekt **jedes** Kriterium
hängt — Gate 1, DSR, Permutationskontrolle. Ein Fehler, der genau ihn nicht
bewegt, ist in dieser Umgebung praktisch unsichtbar.

Die Rendite hat er bewegt, um rund 0,9 % relativ. Nach oben.

### Warum es trotzdem repariert gehört

**Weil eine Spot-Börse kein negatives Guthaben kennt.** Der Backtest
modelliert etwas, das die Ausführung nicht kann: die erste echte Order über
das volle Gewicht bekäme „insufficient funds". Genau diese Divergenz soll
Phase D fangen (`docs/ZIEL.md`: „Divergenz hier ist ein Stopp") — nur wäre
sie beim allerersten Fill aufgetreten, nicht nach Monaten.

Der `CcxtBroker` aus ADR-062 hätte den Fehler also gefunden. Nur eben mit
echtem Geld und einer abgelehnten Order statt hier.

### Die Korrektur

Die Order trägt ihre eigenen Kosten:

```python
kosten = cfg.costs_by_symbol.get(symbol, cfg.costs)
target_qty = capped * equity / (preis * kaufkraft_faktor(kosten))
```

Der Faktor ist **multiplikativ**, nicht `1 + one_way_bps/10_000`:

```python
(1 + (half_spread + slippage)/10_000) * (1 + taker_fee/10_000)
```

Die Gebühr fällt auf den bereits um Spanne und Slippage verschlechterten
Ausführungspreis an, nicht auf den Referenzpreis. Der Unterschied ist das
Produkt der beiden Anteile — bei den Defaults 3·10⁻⁶, also **0,30 auf
100.000**. Der erste Anlauf hier stand mit der linearen Näherung im Code, und
genau diese 0,30 blieben als negatives Guthaben stehen. Der Test aus diesem
ADR hat sie gefangen; eine Näherung ist an dieser Stelle kein Rundungsfehler,
sondern das Vorzeichen, um das es geht.

Exakt ist der Faktor für `FlatFillModel` (den Default). Unter
`SizeAwareFillModel` hängt der Aufschlag von der Ordergröße ab, die beim
Sizing noch nicht feststeht — dort bleibt er eine Untergrenze und ein kleiner
Rest Hebel möglich. Ihn zu beseitigen hieße, den Fixpunkt zu lösen; das steht
so im Docstring und ist bewusst nicht getan.

Danach über die volle Historie von `macross` BTC/USD 1d:

| | vorher | nachher |
|---|---|---|
| tiefstes Cash | −12.928,47 | **−140,98** |
| Bars mit negativem Cash | 1.496 (53 %) | **302 (11 %)** |
| Hebel im Markt (Median) | 1,0056 | **1,0000** |
| Hebel maximal | 1,0085 | **1,0002** |

**Der Rest ist echt und bleibt.** Was übrig bleibt, stammt aus der Lücke
zwischen Entscheidung und Ausführung: bewertet wird mit dem Close des
geschlossenen Bars, gefüllt wird zum Open des nächsten. Springt der Kurs
dazwischen nach oben, kostet die Order mehr als reserviert. Das ist das
Realismusmodell dieser Engine und kein Fehler — ein echter Broker würde dort
teilfüllen oder ablehnen, und das zu modellieren ist eine eigene Frage.

### Ein Test aus ADR-067 hat den falschen Nenner geprüft

`test_ein_round_trip_kostet_genau_was_das_modell_sagt` maß den Verlust gegen
das **Startkapital** und traf damit 130,0 bps nur, solange das Konto genau
sein volles Eigenkapital umsetzte — also nur wegen des Fehlers. Nach der
Korrektur wurde er rot.

Nachgerechnet, ein Konto, konstanter Kurs, ein Rein und ein Raus:

```
Modell:                         130,0000 bps je Round-Trip
Verlust / gehandeltem Notional: 130,0000 bps   <- die Aussage
Verlust / Startkapital:         129,1601 bps   <- die Folge daraus
```

Beide Sätze sind wahr; ADR-067 hat sie verwechselt. Der Test prüft jetzt
beides und rechnet den Faktor aus den Config-Feldern nach statt aus
`kaufkraft_faktor` — sonst prüfte er die Funktion gegen sich selbst.

### Konsequenzen

- **Alle Renditezahlen des Projekts sinken um rund 0,9 % relativ.** Die
  Sharpe-Zahlen bleiben, wo sie sind — und damit jede Aussage aus ADR-035,
  ADR-054, ADR-058, ADR-061 und ADR-064, die an ihnen hängt.
- **Ein Test hält die Zusage der Config fest**: ein Konto mit Zielgewicht 1,0
  darf weder negatives Cash noch Bruttoexposure über 1,0 haben. Er fällt
  gegen den alten Code durch.
- **Die beiden Paper-Konten trugen den Fehler weiter und sind neu gestartet.**
  Beide waren am 2026-09-02 long gegangen — gefüllt vom alten Sizing, Position
  0,65 % zu groß, Cash −650,31 (BTC) und −656,55 (ETH). Das hätte sich
  **nicht** von selbst korrigiert: die Abweichung liegt innerhalb des
  Rebalancing-Bands von 5 %, es wird also keine Ausgleichsorder erzeugt, und
  der Rest wäre bis zum nächsten echten Ausstiegssignal stehen geblieben —
  bei `macross` möglicherweise Monate.

  Der Zustand vor dem Neustart, damit er nachlesbar bleibt (Commit `e658ee8`):

  | | BTC/USD | ETH/USD |
  |---|---|---|
  | Cash | −650,31 | −656,55 |
  | Position | 1,292012 @ 77.437,40 | 41,369325 @ 2.418,61 |
  | Fills / Gebühren | 1 / 600,30 | 1 / 600,34 |
  | erstellt | 2026-09-01 | 2026-09-01 |

  **Warum Neustart und nicht Weiterlaufen:** die Konten existieren für Phase D,
  also für den Abgleich gegen eine echte Ausführung — und genau diesen Zustand
  hätte eine Spot-Börse abgelehnt. Ein Konto, das der Live-Pfad nicht
  nachbilden kann, ist als Beweismittel wertlos, egal wie lange es läuft. Der
  Preis sind zwei Tage Vorwärtszeit und ein Fill.

  **Warum nicht von Hand korrigiert:** ein auf die Größe gesetztes Konto, die
  es *gehabt hätte*, ist kein beobachtetes mehr. Der Runner legt bei fehlendem
  Zustand ein flaches Konto an und verankert es am jüngsten bekannten Bar
  (`runner.py`) — es handelt also nicht rückwirkend, sondern ab jetzt.

  **Was der erste Tick des neuen Kontos tun wird, ist gemessen und nicht
  gehofft.** Dieselbe Mechanik einen Tag zurückversetzt, mit der Risk-Config
  der CLI (Formung aus, ADR-053), gegen den echten Store:

  | | BTC/USD | ETH/USD |
  |---|---|---|
  | Cash nach dem Einstieg | **0,00** | **0,84** |
  | Bruttoexposure | **1,000000** | **0,999992** |
  | Gebühr | 596,42 | 596,42 |

  Der erste Anlauf dieser Messung lief ohne `risk_cfg` und landete bei 25 %
  Gewicht — die Portfolio-Defaults der Risk-Engine. Genau der Fehler aus
  ADR-053, diesmal im Prüfstand statt im Konto: ein Nachweis, der eine andere
  Kontogröße misst als die, die läuft, beweist nichts.
- **Dritter Fund derselben Familie an einem Tag.** ADR-066: eine Kennzahl
  meldete Positives über ein ruiniertes Konto. ADR-067: die Suche nach der
  Gegenrichtung. Und jetzt einer, den ADR-067 nicht gefunden hat — weil er
  genau die Zahl in Ruhe lässt, nach der gesucht wurde.

  Das ist die Lehre und sie ist unbequem: **ein Audit findet, wonach es
  sucht.** Gefunden hat diesen hier ein Blick auf einen Kontostand, der
  komisch aussah.

---

## ADR-067 — Die Gegenrichtung geprüft: wo machen wir uns schlechter, als wir sind?
**Datum:** 2026-09-03

**Der Anlass:** ADR-066 endet mit einem unbequemen Satz — der Fehler fiel nur
auf, weil er die Zahlen **zu gut** aussehen ließ, und einer in der
Gegenrichtung wäre niemandem aufgefallen. Dieses ADR ist die Suche danach.

**Das Ergebnis vorweg:** vier Stellen exakt gegen von Hand gerechnete Werte
geprüft, keine Abweichung. Drei kleine pessimistische Effekte gefunden, alle
ohne Wirkung auf eine Schlussfolgerung. Und ein Befund in der **anderen**
Richtung, der größer ist als alles Pessimistische zusammen.

---

### Was geprüft wurde und stimmt

**Kosten je Round-Trip.** Ein Konto, konstanter Kurs, genau ein Rein und ein
Raus:

```
Modell:     130.0 bps
Gemessen:   130.0 bps   (Gebuehr 1.200 + Slippage 100 auf 100.000)
Abweichung: +0.0 bps
```

**Ordergrößen.** `rebalance_order` bildet `delta = ziel_menge − ist_menge`.
Gebühren fallen auf die *Differenz* an, nicht auf das Zielgewicht — der
naheliegendste Weg zu sechsfach zu hohen Kosten ist nicht beschritten.

**Annualisierung bei fremden Handelskalendern.** Die Sorge aus ADR-055: eine
Aktienreihe mit 252 Handelstagen, annualisiert mit 365, hätte eine um Faktor
1,20 zu hohe Vola und einen entsprechend zu niedrigen Sharpe.

| Reihe | Perioden/Jahr gemessen | Sharpe von Hand | Sharpe laut System |
|---|---|---|---|
| nur Börsentage | 260,9 | +0,112 | +0,114 |
| alle Tage | 365,2 | +0,135 | +0,135 |

`observed_periods_per_year` misst die **tatsächliche** Bar-Dichte statt sie
aus dem Timeframe zu raten. Die 1,5 % Abweichung oben sind mein synthetischer
Kalender ohne Feiertage — und sie gehen nach oben, nicht nach unten.

**Gebühren im Walk-Forward.** `n_trades`, `turnover` und `fees_paid` eines
Fensters kommen alle aus derselben, auf das Testfenster **gefilterten**
Fill-Liste. Kein Doppelzählen von Warmup-Gebühren.

### Drei kleine pessimistische Effekte

**1. Erzwungene Wiedereinstiege an den Fenstergrenzen.** Der Walk-Forward
setzt die Strategie je Fenster neu auf; sie startet flach und muss ihre
Position neu kaufen. Gemessen an `macross`: **3 von 47 Trades** (6 %) liegen
im ersten Bar eines Testfensters.

Der Effekt ist kleiner als die Zahl vermuten lässt, weil dieselbe Grenze auch
den *Ausstieg* der Vorperiode verschluckt — die offene Position verschwindet
mit dem Fenster, ohne Gebühr. Ein zusätzlicher Einstieg gegen einen
gesparten Ausstieg hebt sich weitgehend auf.

**2. Die angenommene halbe Spanne von 2 bps ist vermutlich zu hoch.** Und
hier ist ein zweiter Fehlschlag festzuhalten: ich habe zweimal versucht, sie
aus 1,48 Mio. Tick-Daten mit Aggressor-Seite zu messen, und beide Schätzer
haben Artefakte geliefert.

| Schätzer | Ergebnis | warum unbrauchbar |
|---|---|---|
| Preisabstand bei Seitenwechsel | Median 0,014 bps | von der Tickgröße dominiert |
| Kauf- minus Verkaufsmittel je Minute | Mittelwert **−0,13 bps** | negative Spannen — er misst Kursdrift |

Eine negative Spanne gibt es nicht. **ADR-056 ist damit bestätigt und nicht
widerlegt:** die Spanne ist auch mit Tickdaten nicht sauber messbar, sie
braucht Quotes.

Was sich sagen lässt: das 90-%-Quantil der Minutenschätzung liegt bei 0,8 bps
*voller* Spanne, die Annahme im Modell entspricht 4 bps. Sie ist also
wahrscheinlich um 2 bis 3 bps zu teuer — auf einen Round-Trip von 130. Der
Unterschied macht **3 %** der Kosten aus; bei `macross` mit 8,8× Umschlag sind
das rund 0,004 Sharpe.

**3. Der Versuchszähler ist absichtlich zu hoch.** ADR-057 hat die sieben
handgeschriebenen Hypothesen nachgetragen, obwohl ADR-032 formal nur
abgeschlossenes Screening zählt. Das ist eine bewusste Entscheidung in die
strengere Richtung, im Modul begründet: „Eine Korrektur der Buchführung, die
das eigene Ergebnis verbessert, wäre verdächtig; diese verschlechtert es."
Bleibt so.

### Der Befund in der anderen Richtung

`metrics.sharpe` zieht **keinen risikofreien Zins ab**. Bei 4 bis 5 % Zins und
44 % Jahresvolatilität überschätzt das den Sharpe um rund **0,10** — knapp ein
Drittel der Gate-1-Schwelle von 0,33.

Es ist nicht schlicht falsch, und das ist der Grund, warum es hier als offene
Frage steht und nicht als Korrektur:

* **Für die Nachweisbarkeit ist es richtig.** DSR und Permutationskontrolle
  fragen, ob der Mittelwert von **null** verschieden ist. Die 0,33 aus
  ADR-061 ist eine Nachweisgrenze aus `t ≥ 2`, keine ökonomische Hürde — sie
  ist gegen dieselbe Größe definiert, die gemessen wird.
* **Für die Frage „lohnt sich das?" ist es falsch.** Eine Strategie mit
  Sharpe 0,33 gegen null kann gegen Tagesgeld bei null liegen. Das Ziel in
  `docs/ZIEL.md` ist ein ökonomisches („echtes Geld, 12 Monate, besserer
  Calmar als Buy-and-Hold"), und dort gehört der Zins hinein.
* **Der Vergleich bleibt fair,** solange Buy-and-Hold genauso gerechnet wird —
  und das wird es.

**Nicht geändert, weil die Änderung jede dokumentierte Zahl im Repo bewegen
würde** und die Entscheidung davon abhängt, welche der beiden Fragen eine
Kennzahl beantworten soll. Das gehört vorab entschieden, nicht nebenbei.

### Was das über den Audit selbst sagt

Vier exakte Treffer und drei Effekte unter einem Prozent sind ein
beruhigendes Ergebnis — und ein begrenztes. Geprüft wurde, wo ich **vermutet**
habe, dass ein Pessimismus sitzt. ADR-066 ist nicht durch Suchen gefunden
worden, sondern weil ein Ergebnis auffällig gut war; die Gegenrichtung hat
diesen Alarm nicht. Ein Fehler, der alles gleichmäßig um zehn Prozent
schlechter macht, sähe genau wie dieses Projekt aus.

---

## ADR-066 — Ein ruiniertes Konto meldete Sharpe +8,65
**Datum:** 2026-09-03

**Der Fehler:** `qt.backtest.metrics.compute` rechnete `equity.pct_change()`
ohne Untergrenze. Sobald eine Kapitalkurve durch null geht, liest diese Zeile
jede **Verschlechterung** als Gewinn — von −10.000 auf −20.000 sind
rechnerisch +100 %.

Minimalbeispiel, gegen den alten Code gemessen:

```
Kurve       [100.000, -10.000, -20.000, -40.000, -80.000]
pct_change  [-1,1, +1,0, +1,0, +1,0]      Mittelwert +0,475
gemeldet    Sharpe +8,65 bei Gesamtrendite -180 %
```

**Das ist ADR-026, eine Ebene höher.** Dort stand derselbe Satz für
`qt.sim`: „`prod(1 + r)` ohne Untergrenze … negatives Kapital, das sich
rechnerisch erholt." Korrigiert wurde er damals in der Pfad-Simulation. Im
Backtest nicht — und dort ist er schlimmer, weil hier die Zahlen entstehen,
die in ADRs landen.

---

### Wie er aufgefallen ist

Nicht durch einen Test. Durch den ersten Research-Lauf gegen die erweiterte
Marktbasis (ADR-065): fünf Kandidaten, vier davon mit **positivem**
OOS-Sharpe, einer bei +0,67 — die ersten positiven Zahlen, die der
Research-Loop je geliefert hat. Das war zu gut, um es ungeprüft zu glauben.

`MomentumTrend`, der beste davon, nachgerechnet:

| | |
|---|---|
| Startkapital | 100.000 |
| Minimum der Kurve | **−96.506** |
| Endwert | **0** |
| Gesamtrendite | **−100 %** |
| Punkte unter null | **979 von 1.751** |
| gemeldeter Sharpe | **+0,59** |

Alle fünf Kandidaten hatten das Konto ruiniert, zwischen 2021-10 und 2024-01.
Nach der Korrektur:

| Kandidat | vorher | jetzt | ruiniert am |
|---|---|---|---|
| `SMACross50_200` | −0,42 | **−3,09** | 2021-12-05 |
| `DonchianBreakout` | +0,49 | **−2,70** | 2022-02-06 |
| `VolRegime` | +0,55 | **−2,12** | 2021-12-05 |
| `ZScoreMeanReversion` | +0,44 | **−1,14** | 2024-01-12 |
| `MomentumTrend` | +0,67 | **−0,91** | 2021-10-28 |

### Was **nicht** betroffen ist, nachgeprüft statt gehofft

Die naheliegende Sorge ist, dass die dokumentierten Zahlen des Projekts auf
demselben Fehler stehen. Jede Bibliotheksstrategie über denselben Store
nachgerechnet:

| Strategie | Sharpe | Ruin |
|---|---|---|
| `macross` | +0,25 | – |
| `trend` | +0,16 | – |
| `elliott` | +0,15 | – |
| `orderflow` | +0,00 | – |
| `timesfm` | −0,09 | – |
| `crossmom` | −0,08 | – |
| `meanrev` | −0,42 | – |
| `crossrev` | −0,77 | – |

**Keine einzige ruiniert das Konto.** Der Fehler hat also keine Zahl in
ADR-035, ADR-054, ADR-058 oder ADR-061 verfälscht — er traf ausschließlich
die generierten Kandidaten, weil nur die mit ungebremster Hebelwirkung über
38 Märkte laufen (ADR-065).

Das ist die beruhigende Hälfte des Befunds. Die andere: **der Fehler hätte
jede dieser Zahlen treffen können**, und aufgefallen wäre er nur, weil das
Ergebnis diesmal *zu gut* aussah. Ein Fehler, der zu schlechte Zahlen
produziert hätte, läge noch drin.

### Die Korrektur

`absorbiere_ruin(equity)` schneidet die Kurve am ersten Punkt ≤ 0 ab und hält
sie dort — dieselbe Regel wie `growth_factors()` in ADR-026. Zurückgegeben
wird **die Kurve und der Zeitpunkt**, nicht nur die Kurve: wer nur
abschneidet, verwandelt einen Totalverlust in eine flache Linie, die wie
„hat nicht gehandelt" aussieht. `Metrics.ruined_at` trägt ihn, `as_dict()`
zeigt ihn.

Ein zweiter Schritt war nötig und stand nicht im ersten Entwurf: nach der
Absorption liefert `pct_change` für `0/0` ein `nan`, `dropna()` macht daraus
eine Reihe mit einem Wert, und der Sharpe fiel auf **0,0** zurück — ein
ruiniertes Konto las sich wie eines, das nichts getan hat. Ein totes Konto
hat Rendite **null**, nicht undefiniert; die Division steht deshalb jetzt
explizit da. Erst damit meldet das Beispiel oben **−9,56** statt +8,65.

### Konsequenz

- **Jede gemeldete Kennzahl läuft durch die Absorption**, weil `compute` die
  einzige Stelle ist, an der `Metrics` entsteht.
- **Drei Tests**, einer davon mit einer Zusicherung über den Testfall selbst:
  er prüft vorab, dass die Rohreihe einen positiven Mittelwert hat. Ohne das
  prüft er nichts — mein erster Entwurf hatte eine Kurve, die auch im alten
  Code negativ herauskam, und wäre grün durchgegangen.
- **Offen:** die Absorption sitzt in der Berichtsschicht, nicht in der
  Engine. Ein Broker, dessen Konto null erreicht, handelt weiter. Das ist
  folgenlos, solange jede Zahl durch `compute` geht — aber es ist die Sorte
  Annahme, die dieses Projekt schon zweimal eingeholt hat.

---

## ADR-065 — Phase C: der Loop hat kein Gedächtnis, die Kette hat ein Loch
**Datum:** 2026-09-03

**Die Entscheidung:** Der Research-Loop läuft erstmals gegen die erweiterte
Marktbasis. Dabei fielen drei Dinge auf, die alle dieselbe Form haben wie die
Funde aus ADR-059: **etwas fehlte, und das sah aus wie etwas, das da war.**

Der Versuchszähler steht danach bei **21** statt 16. Fünf Versuche, dauerhaft,
und was sie gekauft haben, steht unten.

---

### Der Lauf

```bash
uv run qt research --generate 5 --screen --provider nim --tf 1d \
    --symbols <38 Märkte> --train 1000 --test 250 --embargo 20
```

Fünf Kandidaten erzeugt, fünf durch Sandbox, Kritik und Sanity, fünf
gescreent, null bestanden. Das ist der Normalfall (ADR-005).

Die berichteten Sharpes waren **positiv** — die ersten, die dieser Loop je
geliefert hat; die acht Kandidaten vom 2026-08-31 lagen zwischen −1,84 und
−8,21. Das war zu gut, und es war falsch: alle fünf hatten das Konto ruiniert,
und die Kennzahl hat es nicht gemerkt. Der Fund hat ein eigenes ADR bekommen
(**ADR-066**), weil er nicht den Loop betrifft, sondern jede Zahl des Systems.

### Fund 1: der Generator kannte die Bibliothek nicht

Von fünf Kandidaten waren drei Neuauflagen dessen, was seit Phase 1 im Repo
steht:

| erzeugt | ist in Wahrheit |
|---|---|
| `SMACross50_200` | `macross` |
| `DonchianBreakout` | `trend` |
| `ZScoreMeanReversion` | `meanrev` |

**Drei von fünf Versuchen für Hypothesen, die dieses Projekt längst verworfen
hat** — und der Zähler vergisst sie nie (ADR-032).

Das Briefing kannte die **laufende Charge** (`previous`) und sonst nichts.
Der Kommentar dazu benannte den Mechanismus sogar präzise: „zwanzig Varianten
derselben Idee sind für die Deflated Sharpe Ratio trotzdem zwanzig Versuche."
Nur reichte das Gedächtnis genau bis zum Ende des Laufs.

**Behoben:** `bereits_geprueft(registry)` sammelt die Namen aus Bibliothek
*und* Registry — derzeit 42 — und das Briefing führt sie als „bereits geprüft
und gescheitert". Es weicht das blinde Briefing nicht auf (ADR-003): eine
Liste von Ansatznamen enthält keine Kurse, keine Kennzahlen, keinen Markt.
**Nur Namen, keine Ergebnisse** — wer dem Generator sagt, welcher Ansatz wie
gut war, lässt ihn in der Nähe der besten bisherigen Zahl suchen, und das ist
Overfitting mit einem Umweg über ein Sprachmodell. Ein Test hält beides fest.

### Fund 2: die Kette endete bei der DSR

`docs/ZIEL.md` Phase C.3 schreibt die Reihenfolge vor: Sandbox → Kritik →
Walk-Forward → DSR → `qt placebo shuffle` → `qt placebo cross`. Die letzten
beiden Stufen liefen nie. `screen_candidate` hörte nach der DSR auf.

**Bemerkt hat es niemand, weil nie ein Kandidat bis dorthin kam.** Eine
fehlende Stufe hinter einer nie genommenen Hürde sieht genauso aus wie eine
vorhandene.

**Behoben:** die Permutationskontrolle läuft jetzt für Kandidaten, die die DSR
bestehen — **nach** ihr, nicht davor: 200 Ziehungen Walk-Forward kosten ein
Vielfaches des Screenings, und ein an der DSR gescheiterter Kandidat ist
ohnehin tot. Dieselbe Kostenreihenfolge wie im übrigen Trichter. Ein Test
prüft die Reihenfolge im Quelltext, ein zweiter, dass ein Kandidat mit
bestandener DSR und durchgefallenem Placebo als durchgefallen gilt.

### Fund 3: der Loop hat keine Positionsgrößen-Schicht

Warum ruinierten die Kandidaten das Konto? Weil sie über 38 Märkte laufen und
jedes Gewicht für sich vergeben. `qt backtest` und `qt wf` rufen keine
Risk-Engine auf (ADR-053) — das Bruttoexposure summiert sich damit auf bis zu
**38×** Eigenkapital.

Gegenprobe, dieselben Kandidaten mit auf 1 normiertem Brutto:

| | Brutto 38 | Brutto 1 |
|---|---|---|
| alle fünf | −0,91 bis −3,09 | **+0,00** |

Bei Brutto 1 fällt jedes Gewicht unter das Rebalancing-Band und **keiner
handelt mehr**. Zwischen Bankrott und Untätigkeit liegt keine Einstellung, die
das Ergebnis der *Idee* zeigen würde.

**Das ist nicht behoben, und der Grund steht hier:** eine Positionsgrößen-
Schicht für generierte Kandidaten ist eine Entwurfsentscheidung mit Folgen für
jede künftige Messung — Vol-Targeting, Gleichgewichtung, Brutto-Cap sind drei
verschiedene Strategien, nicht drei Einstellungen. Sie gehört vorab
festgelegt, nicht nebenbei gewählt, weil ein Lauf sonst schlecht aussah.

**Die fünf Versuche haben damit die Verdrahtung geprüft, nicht die Signale.**
Sie zählen trotzdem — ADR-032 kennt keine Ausnahme für „falsch aufgesetzt",
und eine solche Ausnahme wäre die bequemste Hintertür im ganzen System.

### Stand von Phase C

| Punkt | Stand |
|---|---|
| 1. Mindest-Sharpe vorab festgeschrieben | erledigt (ADR-057, aktualisiert in ADR-061 auf 0,33) |
| 2. Research-Loop gegen die erweiterte Marktbasis | **gelaufen** — fünf Kandidaten, null bestanden, drei Funde |
| 3. Volle Kette je Kandidat | **jetzt vollständig** — Stufe 5 fehlte bis heute |

Offen bleibt Punkt 3 in einer Hinsicht: `qt placebo cross` ist noch nicht Teil
des automatischen Screenings. Für eine Timing-Strategie über 38 Märkte wäre
er ein Lauf je Markt und damit teurer als alles davor; er bleibt der
Handgriff vor einer Promotion.

---

## ADR-064 — Order Flow auf feineren Timeframes: die Frage ist nicht der Takt, sondern die Gebühr
**Datum:** 2026-09-03

**Die Frage:** Wäre `orderflow` auf kleineren Timeframes wirksamer? Die
Vermutung dahinter ist gut: aggressiver Fluss ist ein kurzfristiges Signal.
Was er über die nächsten Minuten sagt, hat er über die nächsten vier Stunden
längst gesagt.

**Die Antwort:** Die Vermutung stimmt vermutlich für das *Signal* und ist für
das *Ergebnis* gleichgültig — unter diesem Kostenregime. Das lässt sich
ausrechnen, ohne eine Zeile zu backtesten, und die Rechnung ist der Grund,
warum unten trotzdem ein Lauf steht.

---

### Das Umschlagbudget hängt nicht am Timeframe

Aus ADR-056: **Drag p. a. ≈ (Umschlag/EK/Jahr) × einfache Kosten** und
**ΔSharpe ≈ −Drag/Vola**. Gate 1 erlaubt 7× Umschlag pro Jahr. Ein Round-Trip
schlägt zweimal das Eigenkapital um, also:

> **Das Budget erlaubt 3,5 Round-Trips pro Jahr — auf jedem Timeframe.**

Es ist ein Jahresbudget, kein Bar-Budget. Ein feinerer Takt bekommt dadurch
nicht mehr Spielraum, er macht es nur schwerer, im Budget zu bleiben:

| Timeframe | Bars/Jahr | Haltedauer für 3,5 Round-Trips |
|---|---|---|
| 1d | 365 | 104 Bars |
| 4h | 2.190 | 626 Bars |
| 1h | 8.760 | 2.503 Bars |
| 15m | 35.040 | 10.011 Bars |
| 5m | 105.120 | 30.034 Bars |

Eine 5m-Strategie, die eine Position 30.034 Bars hält, ist keine
5m-Strategie. Sie ist eine Jahresstrategie mit teurer Datenbeschaffung.

### Was ein schnelleres Signal leisten müsste

Andersherum gefragt: angenommen, `orderflow` hält im Schnitt 20 Bars — für
einen Flussindikator eher träge. Welchen **Brutto**-Sharpe bräuchte das
Signal, damit nach Kosten die 0,33 aus ADR-061 übrig bleiben?

| Timeframe | Umschlag/Jahr | taker 130 bps | maker 80 bps | ADR-009 16 bps |
|---|---|---|---|---|
| 1d | 36× | 1,0 | 0,7 | 0,4 |
| 4h | 219× | 4,3 | 2,9 | 0,7 |
| 1h | 876× | 13,3 | 8,4 | **1,9** |
| 15m | 3.504× | 52 | 32 | 6,7 |
| 5m | 10.512× | 156 | 96 | 19,4 |

Die höchste je in diesem Repo gemessene Kennzahl ist Sharpe **1,04**, und die
ist in-sample. Unter `coinbase_taker` — dem Regime, unter dem laut ADR-056
gesucht wird — ist damit alles unter 1d erledigt, bevor ein Backtest läuft.

**Eine Zelle in dieser Tabelle ist aber nicht absurd.** Bei den 16 bps aus
ADR-009 braucht 1h einen Brutto-Sharpe von 1,9. Das ist ambitioniert und
nicht unmöglich. Genau dort, und nur dort, lebt die Vermutung weiter: **die
Frage ist nicht der Takt, sondern ob passiv gefüllt wird.**

Dafür gibt es seit Phase 1 einen Befehl, `qt maker` — „Wären die Orders dieses
Laufs passiv überhaupt gefüllt worden?". Er ist die richtige Frage an ein
schnelleres Order-Flow-Signal, und sie ist nicht dieselbe wie „ist der
Backtest positiv".

### Die zweite Grenze, die keine Gebühr auflöst

Feinere Bars sehen aus wie mehr Daten. ADR-047 hat gemessen, dass der
Standardfehler eines annualisierten Sharpe an der **Kalenderspanne** hängt und
nicht an der Bar-Frequenz:

| Zeitraum | Bars auf 5m | beweisbarer Sharpe (t ≥ 2, ein Markt) |
|---|---|---|
| 1 Monat | 8.640 | 6,98 |
| 3 Monate | 25.920 | 4,03 |
| 1 Jahr | 105.120 | 2,00 |
| 7,7 Jahre | 809.280 | 0,72 |

Ein Monat 5m-Daten ist eine imposante Zeilenzahl und ein Monat Evidenz. Wer
auf feinere Timeframes ausweicht, weil die Historie knapp ist, tauscht ein
Problem gegen seine Illusion.

### Was daraus folgt

Nicht: „Order Flow funktioniert nicht." Das ist ungeprüft, und ungeprüft ist
nicht widerlegt.

Sondern: **die Timeframe-Frage ist unter diesem Kostenregime entschieden,
bevor Daten gezogen werden.** Offen blieb nur die eine Zelle — Maker-Gebühren
auf einem feinen Takt. Die ist jetzt gemessen.

---

### Nachgemessen: `orderflow` auf 15m

1,7 Mio. Trades von Kraken gezogen, davon **28 zusammenhängende Tage**
(2026-05-06 bis 2026-06-04); der Rest hat eine Lücke. Auf 15m sind das 2.760
Bars — genug für zehn Walk-Forward-Fenster, und der feinste Takt, den diese
Datenlage trägt.

**Der Lauf selbst bestätigt die Rechnung oben:**

| | |
|---|---|
| Sharpe | **−42,3** |
| Gesamtrendite | −63,6 % (Buy & Hold −21,4 %) |
| Trades | 148 |
| Umschlag | 95× Eigenkapital in 28 Tagen ≈ **1.240×/Jahr** |

Gegen ein Budget von 7×. Die Tabelle oben sagte für 15m einen nötigen
Brutto-Sharpe von 52 voraus; das Ergebnis ist damit keine Überraschung,
sondern eine Bestätigung.

**Die Negativkontrolle** (200 Ziehungen, zehn Fenster):

```
  Strategie selbst      -42.693
  Abspieler (Referenz)  -42.693  Pfadabhaengigkeit 0.0000
  Trades   echt   110   Ziehungen Median   110  (87-132)
  Perzentil der echten Strategie: 52.5%
```

Sauber kalibriert — Pfadabhängigkeit null, identische Reibung — und
**durchgefallen**. `orderflow` ist von der zufälligen Platzierung seiner
eigenen Episoden nicht zu unterscheiden. Damit haben **alle neun Strategien
der Bibliothek eine Negativkontrolle, und keine besteht sie.**

Der Vorbehalt gehört dazu, und er ist größer als sonst: bei Sharpe −42
beherrschen die Gebühren beide Seiten des Vergleichs. Die Ziehungen liegen
zwischen −45,9 und −38,3 — in diesem Band ist für ein Signal kaum Platz. Der
Test ist gültig und fast blind.

### Und die letzte Tür: `qt maker`

```
Symbol      n  marktnah  passiv  Ausfall  Maker%
BTC/USD   148       104      42        2     28%
```

**Nur 28 % der Orders wären passiv gefüllt worden**, und das ist eine
Obergrenze — die Warteschlangenposition ist nicht modelliert. 104 von 148
Limits waren schon beim Open erreichbar: die Order geht durch, zahlt aber
Taker.

Damit ist auch die einzige nicht-absurde Zelle der Tabelle geschlossen. Das
Maker-Regime aus ADR-009 steht dieser Strategie auf diesem Takt nicht zur
Verfügung, und die 1,9 sind keine Zielmarke, sondern eine Rechnung unter einer
Annahme, die gerade widerlegt wurde.

**Was offen bleibt:** 28 Tage sind ein Monat Evidenz. Nach der Tabelle oben
müsste ein Signal dort Sharpe 7 erreichen, um überhaupt zeigbar zu sein. Der
Befund lautet also nicht „Order Flow funktioniert nicht", sondern: **unter
diesem Kostenregime ist er auf keinem Takt prüfbar, auf dem er interessant
wäre.** Das ist eine Aussage über das Regime, nicht über das Signal.

---

## ADR-063 — Aufteilen hilft, aber nicht in git: die Trade-Ablage war quadratisch
**Datum:** 2026-09-03

**Die Frage:** Lässt sich das Order-Flow-Problem durch Aufteilen der Dateien
lösen? `docs/ROADMAP.md` führte Order Flow herabgestuft, unter anderem mit der
Begründung, ein Jahr Trades wäre „rund 170 MB, und GitHub lehnt Dateien über
100 MB ab".

**Die Antwort:** Aufteilen löst genau dieses Hindernis — und dieses Hindernis
war weder das größte noch das eigentliche. Die Ablage ist jetzt in Tagesteilen,
aus einem Grund, der mit git nichts zu tun hat. Order Flow bleibt herabgestuft,
aber die Begründung dafür stimmte nicht.

---

### Erst messen, was da überhaupt anfällt

Zwei Tage BTC/USD von Kraken gezogen, 2026-09-03:

| | |
|---|---|
| Trades | 210.331 in 2,00 Tagen |
| Dateigröße | 2,52 MB → **1,26 MB/Tag** |
| hochgerechnet | **460 MB/Jahr** |

Die 170 MB aus dem ROADMAP waren also zu niedrig, und die 100-MB-Grenze ist
schon **am Tag 79** erreicht, nicht nach einem Jahr.

**Kompression ist nicht der Hebel.** `df.to_parquet(path)` schrieb ohne
Angabe, also Snappy. Gemessen an denselben Daten:

| Variante | MB/Jahr | gegen heute |
|---|---|---|
| heute (snappy) | 460 | 100 % |
| zstd | 374 | 81 % |
| brotli | 343 | 75 % |

Ein Viertel weniger löst kein Problem, das um den Faktor fünf zu groß ist.
Die Daten sind schlicht groß: 105.000 Trades am Tag, jeder mit Zeitstempel,
Preis, Menge und Seite.

### Das größere Problem stand nicht im ROADMAP, weil es niemand gesucht hat

`write_trades` legte alles in **einer** Datei je Symbol ab und schrieb sie bei
jedem Anhängen komplett neu — lesen, zusammenführen, entdoppeln, sortieren,
ganz zurückschreiben. Was das kostet, hängt daran, wie oft angehängt wird:

| | geschriebene Bytes |
|---|---|
| ein Jahresabzug am Stück (≈139 Zwischenspeicherungen) | **≈ 32 GB** |
| täglich fortgeschrieben über ein Jahr (365 Schreibvorgänge) | **≈ 84 GB** |

Für 460 MB Ergebnis. Der Abzug ist **quadratisch in seinem eigenen
Ausgabevolumen**, und das ist der Grund, warum ein langer Abzug in der Praxis
nicht durchführbar war — nicht die Dateigröße. Kein Test war rot; es dauerte
nur immer länger.

Genau hier hilft Aufteilen, und zwar dramatisch:

| Ablage | Dateigröße | in git über ein Jahr |
|---|---|---|
| eine Datei, täglich neu geschrieben | 460 MB | ≈ 84 GB |
| monatliche Teile | 38 MB | ≈ 7,0 GB |
| **tägliche Teile, je einmal geschrieben** | **1,3 MB** | **0,46 GB** |

Ein abgeschlossener Tag wird nie wieder angefasst. Damit ist der Abzug linear,
und der Dateiname wird zum Index: `read_trades` sortiert Teile außerhalb des
Zeitraums am Namen aus, ohne sie zu öffnen.

### Und trotzdem gehört es nicht ins Repository

Die Aufteilung macht die Daten *versionierbar* — unveränderliche Dateien
speichert git einmal. Die Frage ist, ob sie dorthin gehören, und die Antwort
steht in `.gitignore`: versioniert wird nur, was sich **nicht rekonstruieren
lässt** (der Kontostand, die Kandidaten-Registry).

Gemessen statt vermutet, `fetch_trades` gegen Kraken:

| `since` | Antwort |
|---|---|
| 2019-06-01 | 1000 Trades, erster 2019-06-01 00:00 |
| 2021-06-01 | 1000 Trades, erster 2021-06-01 00:00 |
| 2023-06-01 | 1000 Trades, erster 2023-06-01 00:00 |
| 2025-06-01 | 1000 Trades, erster 2025-06-01 00:00 |

Kraken liefert die Historie ab 2019 auf Zuruf. **Die Trades sind nicht
verderblich, sondern reproduzierbar** — genau deshalb wurde Kraken in ADR-034
gewählt, und genau das ist beim Formulieren des Speicherproblems untergegangen.
Sie gehören damit unter `/data/*` wie die Bars, nicht ins Repository.

### Was daraus für Order Flow folgt

**Die Begründung im ROADMAP war falsch, die Schlussfolgerung bleibt richtig.**
Order Flow ist nicht an einem Speicherproblem herabgestuft, sondern an zwei
Dingen, die beide gemessen sind:

1. **Zeit.** 19 Anfragen je Tag Historie, 1,2 s Wartezeit je Anfrage. Ein Jahr
   sind 6.935 Anfragen, rund **2,3 Stunden** — und in einem Container, dessen
   Store bei jedem Start leer ist, fällt das **jedes Mal** an. Für sieben
   Walk-Forward-Fenster auf 4h braucht es rund 460 Tage Historie, also gut
   drei Stunden.
2. **Das Kostenregime.** Order Flow lebt auf 4h. ADR-047 hat für 4h gemessen,
   dass die Gebühren dort *jede* getestete Strategie von positiv auf −0,65 bis
   −1,60 Sharpe ziehen, und Gate 1 lässt seit ADR-056 nur 1d oder gröber zu.

Der erste Punkt ist jetzt ein Preis und kein Hindernis mehr: der Abzug ist
linear, er läuft durch. Die Negativkontrolle für `orderflow` — die letzte, die
fehlt (ADR-059) — kostet damit drei Stunden Ziehen und keine Grundsatzfrage.

### Konsequenzen

- **`data/trades/<SYMBOL>/<YYYY-MM-DD>.parquet`** statt einer Datei je Symbol,
  mit zstd. Ein abgeschlossener Tag wird nie wieder geschrieben; ein Test hält
  das an der Änderungszeit der Nachbardateien fest.
- **`read_trades` liest über die Teile hinweg** und sortiert am Dateinamen vor.
  Ein Altbestand aus der Zeit davor wird weiter mitgelesen — ihn still zu
  übergehen wäre der Weg, auf dem Daten verschwinden, ohne dass etwas
  fehlschlägt.
- **Die Begründung im ROADMAP ist korrigiert.** Ein falscher Grund für eine
  richtige Entscheidung ist keine harmlose Ungenauigkeit: er wird zitiert, und
  irgendwann trifft jemand auf seiner Grundlage eine andere Entscheidung.
- **Offen bleibt der Abzug selbst.** Drei Stunden je Sitzung sind der Preis;
  ob er sich lohnt, hängt an Punkt 2 oben und ist keine technische Frage mehr.

---

## ADR-062 — Der Live-Pfad ist gebaut und bleibt unverdrahtet
**Datum:** 2026-09-03

**Die Entscheidung:** `qt.live.broker_ccxt`, `qt.live.sizing` und
`qt.live.reconcile` existieren, mit Tests. `qt live tick` ist **absichtlich
nicht verdrahtet** und beendet sich mit Exit 1 und einer Begründung. Was zum
Handeln fehlt, ist kein Code mehr, sondern die Bedingung aus `docs/ZIEL.md`:
ein Kandidat, der Gate 1 besteht.

---

### Warum überhaupt bauen, wenn nichts handeln darf

`docs/ZIEL.md` sortiert den Live-Pfad ausdrücklich ans Ende: „ein Live-Pfad
ohne validierten Edge ist ein Weg, schneller Geld zu verlieren." Der Satz gilt
weiter, und er wird durch dieses ADR nicht abgeschwächt.

Trotzdem ist die Arbeit nicht verfrüht, und zwar aus einem messbaren Grund:
**der Live-Pfad stellt Fragen, die der Backtest nicht stellt** — Mindest-
ordergrößen, Rundungsraster, was eine Börse als Fill zurückmeldet, was
passiert, wenn eine Order abgelehnt wird. Jede dieser Fragen kann eine
Strategie unbrauchbar machen, und keine davon steht in einem Kursverlauf.
Eine davon ist unten schon beantwortet, und die Antwort ist kleiner als
erwartet.

Was hier **nicht** passiert ist: eine echte Order. Kein Schlüssel wurde
benutzt, nichts wurde gesendet. Die einzigen Netzzugriffe waren öffentliche
Marktdaten.

---

### Die zwei Schalter

Scharf ist der Broker nur, wenn **beides** gilt:

```python
CcxtBroker(..., scharf=True)     # im Aufruf
QT_LIVE_SCHARF=ja                # in der Umgebung
```

Zwei unabhängige Schalter, weil ein einzelner zu leicht aus Versehen steht:
ein vergessener Default im Code, eine geerbte Umgebungsvariable in einer
Routine. Der Wert ist `ja` und nicht `1` oder `true` — die beiden stehen zu
leicht irgendwo herum.

Ein unscharfer Broker **liest** normal (Kontostand, Positionen, Marktgrenzen)
und wirft bei jedem Sendeversuch `NichtScharf` — mit der Order im Text, damit
ein Trockenlauf zeigt, was passiert wäre. Der Test dazu prüft nicht nur, dass
geworfen wurde, sondern dass die Fake-Börse **nichts empfangen** hat; bei
einer Sicherung ist das der Unterschied zwischen geprüft und angenommen.

### Die Grenzen liegen im Broker, nicht nur darüber

`Limits` wird in `broker_ccxt` geprüft, obwohl die Risk-Engine oben im
Aufrufpfad schon Caps hat. Eine Grenze, die nur an einer Stelle steht,
schützt genau so lange, wie dieser Pfad der einzige ist — und ADR-053 hat
gezeigt, wie leise ein zweiter Pfad entsteht.

Verletzungen werden **geworfen, nicht gekappt.** Eine still zurechtgestutzte
Order ist eine Order, die niemand so gewollt hat, und der Kontostand danach
passt zu keiner Absicht. Die Defaults sind klein (100 je Order, 500 je
Position, 1000 brutto): wer mit echtem Geld anfängt, soll die Zahl bewusst
hochsetzen müssen.

### Was von der Börse kommt, bleibt von der Börse

Der Fill wird aus der Antwort gebaut — Preis, Menge, Gebühr. Das Kostenmodell
aus ADR-056 wird hier **nicht** angewandt: es war die Schätzung, die diese
Zahlen vorhersagen sollte, und sie jetzt darüberzulegen hieße, die Prüfung zu
verhindern, für die der Live-Pfad da ist.

Fehlt in der Antwort der Durchschnittspreis, wird **nicht** der letzte bekannte
Kurs eingesetzt. Dann stünde im Konto eine Zahl, die nicht von der Börse kommt
und trotzdem so aussieht. Stattdessen: `OrderAbgelehnt` mit dem Hinweis auf
`qt live reconcile`.

### `reconcile` meldet und korrigiert nicht

ADR-037 hat dieses Modul mit einer Begründung ausgelassen, die bis heute galt:
ein Abgleich braucht zwei unabhängige Quellen. Mit `broker_ccxt` gibt es die
zweite, also gibt es jetzt den Abgleich.

Es gibt **keine** Funktion, die den lokalen Zustand nachzieht, und ein Test
hält das fest (er verbietet die Namen `angleichen`, `sync`, `fix`, `apply`).
Eine Abweichung heißt, dass eine Annahme falsch war — eine Order kam nicht
durch, eine Teilfüllung wurde übersehen, eine Gebühr wurde in der
Basiswährung abgezogen. Wer den Zustand nachzieht, löscht die Spur und fährt
mit demselben Fehler weiter, nur unsichtbar. Der fehlende Befehl ist die
Sicherung, wie bei der Promotion im Research-Loop (ADR-032).

Zwei Details, die aus dem Nachdenken über den schlimmsten Fall kommen:

* Verglichen wird über die **Vereinigung** beider Symbolmengen, nicht über
  die des Solls. Der gefährlichste Fall ist die Position, die es lokal gar
  nicht gibt — eine Order, die durchkam, obwohl sie als abgelehnt verbucht
  wurde. Wer nur über das Soll iteriert, sieht genau die nicht.
* Die relative Abweichung bezieht sich auf die **größere** der beiden Mengen.
  Auf `soll` bezogen bräche sie bei soll = 0 und ist = 0,3 — wieder genau im
  interessantesten Fall.

---

### Der eine Befund, und er ist kleiner als die Überschrift verspricht

Eine Börse hat Mindestordergrößen. Was darunter fällt, wird nicht ungenau
ausgeführt, sondern **gar nicht** — die Mindestordergröße wirkt also wie ein
zweites Rebalancing-Band, das kein Backtest modelliert.

Die erste Fassung dieses Absatzes stand hier als „bei Minimalkapital handelt
dieselbe Strategie anders". Das ist zu stark, und der Test dazu ist an
gewählten statt gemessenen Zahlen durchgefallen. Gemessen über
`qt live groesse` gegen `api.exchange.coinbase.com`, BTC/USD, 2026-09-03:

| | |
|---|---|
| Mindestmenge | **keine** |
| Mindestgegenwert | **1 USD** |
| Mengenraster | 1e-8 |

Eine Anpassung fällt damit erst aus, wenn ihr Gegenwert unter einem Dollar
liegt — bei einem Prozent Rebalancing-Band also unterhalb von rund **100 USD**
Kontogröße. Der Mechanismus ist echt, seine Reichweite ist klein, und beides
steht jetzt als Test da: einer zeigt den Mechanismus an gewählten Zahlen, der
zweite pinnt die gemessene Wirklichkeit fest.

Das ist dieselbe Lehre wie ADR-056, wo ein Satz über 4h überall zitiert wurde,
als gälte er allgemein. Ein Befund ohne seinen Geltungsbereich ist eine
Behauptung, die auf ihre Widerlegung wartet.

---

### Was fehlt, und was ausdrücklich nicht fehlt

**Nicht mehr offen:** `broker_ccxt`, `sizing`, `reconcile`, harte
Positionslimits, Schlüsselverwahrung (`Zugang` liest aus der Umgebung, zeigt
den Schlüssel in keinem `repr` und schreibt ihn nirgends hin). 30 Tests, keiner
braucht Netz.

**Offen und bewusst offen:**

* **`qt live tick` ist nicht verdrahtet.** Neun Strategien geprüft, keine hat
  eine Negativkontrolle bestanden (ADR-059). Diesen Befehl zu verdrahten,
  bevor Gate 1 fällt, hieße das Abbruchkriterium des Projekts zu umgehen —
  mit echtem Geld.
* **Kein echter Schlüssel wurde je benutzt.** `qt live status` und
  `qt live reconcile` sind gegen die Fake-Börse geprüft, nicht gegen eine
  echte. Der erste Lauf mit echten Schlüsseln wird Dinge finden; das ist der
  Zweck von `status` als erstem Befehl.
* **Teilfüllungen** werden als ein Fill verbucht, wenn die Börse sie so
  meldet. Ob das reicht, entscheidet der erste echte Fill.
* Die **Slippage gegen den erwarteten Kurs** wird nicht in den Fill
  geschrieben. Sie ist eine Größe des Abgleichs zwischen Erwartung und
  Ausführung und gehört nicht in eine Zahl, die von der Börse kommt.

---

## ADR-061 — Phase A bestanden: n_eff 5,1, und die Schwelle sinkt deshalb auf 0,33
**Datum:** 2026-09-03

**Die Entscheidung:** Zwölf Reihen aus Anlageklassen, die der Bestand nicht
hatte, heben die effektive Marktzahl von 3,4 auf **5,1**. Das vorab gesetzte
Kriterium aus `docs/ZIEL.md` Phase A (n_eff ≥ 4) ist damit erfüllt, und die
daraus abgeleitete Nachweisgrenze `MIN_SHARPE` sinkt von 0,41 auf **0,33**.

---

### Warum mehr vom Gleichen nicht geholfen hätte

`n_eff = n/(1+(n−1)·ρ̄)` hängt fast nur an der **mittleren paarweisen
Korrelation**, kaum an der Zahl der Märkte. Ausgerechnet:

| Märkte | ρ̄ für n_eff ≥ 4 |
|---|---|
| 30 | ≤ 0,224 |
| 38 | ≤ 0,230 |
| 60 | ≤ 0,236 |

Von 30 auf 60 Märkte zu verdoppeln lockert die Anforderung um 0,012. Der
Bestand lag bei ρ̄ = 0,264. Gebraucht wurden also nicht *mehr* Reihen, sondern
Reihen, die mit dem Vorhandenen **nicht mitlaufen**.

### Der Korb stand vor der ersten Messung

Das ist der Teil, der zählt. Wer Kandidaten durchprobiert, bis n_eff über 4
steht, hat die Kennzahl optimiert und nicht die Evidenz verbreitert — genau
der Fehler, den ADR-055 an sich selbst dokumentiert, als dort ein anderer
Schätzer gesucht wurde, nachdem 3,4 das Kriterium verfehlte.

Vier Regeln, festgelegt vor dem ersten Ziehen, die den bequemen Weg
ausschließen:

1. **Keine inversen, keine gehebelten Produkte.** `SH` ist rechnerisch −SPY.
   Es hätte Korrelation −1 zum Bestand, drückte ρ̄ kräftig — und trüge **null**
   neue Information. Die Formel ist so zu schlagen, ohne dass ein einziger
   Standardfehler kleiner wird.
2. **Keine Geldmarktnähe.** `BIL` und Verwandte haben kaum Varianz und damit
   Korrelation nahe null zu allem. Derselbe Trick, nur leiser. Ein Markt ohne
   Bewegung ist kein Test.
3. **Historie mindestens bis 2021-09-30** — der Anfang des gemeinsamen
   Fensters (AVAX). Eine kürzere Reihe verkürzt die Korrelationsmatrix für
   alle.
4. **Nur physisch oder über Futures hinterlegte Long-Instrumente.**

Übrig bleiben die drei Lücken, die `docs/ZIEL.md` selbst benennt
(Volatilität, Zinsdifferenzen, Einzelwerte), plus eine vierte, die dort fehlt:
Gold, Silber, Rohöl und ein breiter Korb waren da — **Agrarrohstoffe** nicht.
Deren Treiber ist das Wetter, und das schert sich nicht um Notenbanken.

| Klasse | Ticker |
|---|---|
| Volatilität | VIXY |
| Anleihen | SHY, TIP, MUB |
| Rohstoffe | DBA, CORN, WEAT, SOYB, UNG, CPER |
| Immobilien | VNQ |
| Aktien | EWJ |

**Einzelwerte bleiben draußen, obwohl ZIEL.md sie nennt.** Es gibt keine
neutrale Regel, fünf Namen aus viertausend zu wählen — und diese Wahl ließe
sich hinterher auf n_eff hin treffen. Dazu trägt sie nicht: ein
US-Großunternehmen läuft zu 0,5 bis 0,7 mit SPY und höbe ρ̄, statt es zu
senken. Für Nebenwerte gälte die Vermutung aus ZIEL.md eher — deren
Liquidität steht aber der Kostenannahme aus ADR-056 entgegen.

### Das Ergebnis, und die Gegenprobe dazu

```
uv run qt placebo cross --strategy macross --tf 1d
  Median-Sharpe ueber 38 Maerkte: +0.09
  mittlere paarweise Korrelation der Maerkte: 0.18
  -> effektiv 5.1 unabhaengige Maerkte (ADR-052), nicht 38
```

Eine einzelne Reihe darf das Ergebnis nicht tragen — `VIXY` ist der
naheliegende Verdacht, weil implizite Volatilität in der Praxis überwiegend
eine Gegenbewegung zu Aktien ist. Klassenweise weggelassen:

| Auswahl | n | ρ̄ | n_eff |
|---|---|---|---|
| alle 38 | 38 | 0,176 | **5,07** |
| ohne VIXY | 37 | 0,199 | 4,53 |
| ohne Agrar | 34 | 0,200 | 4,48 |
| ohne die neuen Anleihen | 35 | 0,185 | 4,80 |
| ohne UNG + CPER | 36 | 0,183 | 4,86 |
| **ohne VNQ + EWJ** | 36 | 0,171 | **5,15** |
| Bestand vor diesem ADR | 26 | 0,264 | 3,42 |

**Keine einzelne Klasse trägt das Ergebnis** — die schlechteste Auslassung
landet bei 4,48, immer noch über dem Kriterium. Und die vorletzte Zeile ist
der Beleg, dass der Korb nicht auf die Zahl hin gewählt wurde: `VNQ` und
`EWJ` laufen mit Aktien mit und **verschlechtern** n_eff. Sie sind drin, weil
Immobilien und Japan eigene Anlageklassen sind, nicht weil sie helfen.

### Die Schwelle sinkt — und warum das kein Torpfostenverschieben ist

`MIN_SHARPE` in `qt.research.gate` geht von 0,41 auf 0,33. Das ist die
gefährliche Richtung, deshalb die drei Bedingungen, unter denen es zulässig
ist, alle nachprüfbar:

* **Die Regel stand vorher.** ZIEL.md Phase C.1: „Mindest-Sharpe aus Phase A
  ableiten und **vorab** als ADR festschreiben." Die Schwelle ist eine
  Funktion von n_eff, keine freie Zahl.
* **Gesenkt hat sie die Datenlage, nicht ein verfehltes Ergebnis.** Der
  Standardfehler ist wirklich kleiner geworden.
* **Kein Kandidat gewinnt dadurch.** Nachgerechnet nach der Senkung:

| | vor Phase A | nach Phase A |
|---|---|---|
| `macross` | Umschlag 8,8× > 7× | **unverändert 8,8×**, bricht vor dem Sharpe ab |
| `crossmom` OOS-Sharpe | −0,24 | **−0,08**, verlangt ≥ 0,33 |
| `crossmom` Umschlag | 4,6× | **3,0×** |
| `crossmom` DSR | 0,010 | 0,023, verlangt ≥ 0,95 |

Die Senkung rettet nichts, was vorher gescheitert ist. Sie dürfte es sonst
auch nicht.

### Was die breitere Basis tatsächlich bewegt hat

`crossmom` ist die Strategie, für die der Querschnitt gebaut wurde, und sie
reagiert in genau der Richtung, die der Plan vorhergesagt hat:

| | 27 Märkte (ADR-058) | 39 Märkte |
|---|---|---|
| mittlerer Rank IC | +0,0307 | **+0,0346** |
| effektive Datenpunkte | 348 | **413** |
| t korrigiert | +1,42 | **+1,87** (p = 0,062) |
| Umschlag/EK/Jahr | 4,6× | **3,0×** |
| OOS-Sharpe | −0,24 | **−0,08** |

Jede Zeile geht in die richtige Richtung, und **keine** kommt über ihre
Schwelle. `|t| ≥ 1,96` ist verfehlt, der Sharpe ist negativ. Das ist der
ehrliche Zwischenstand: die Datenbasis war ein echter Hebel, und sie reicht
trotzdem nicht.

### Nebenfund: das Gate meldete eine Nebenwirkung, die es nicht hat

Der `crossmom`-Lauf schrieb: „Dieser Lauf zaehlt als Versuch. Zaehler danach:
17 (ADR-032)." `qt trials` sagte danach unverändert **16**.

Beides stimmte für sich: das Gate rechnet die DSR gegen `trial_count() + 1`,
schreibt aber bewusst nichts in die Registry (ADR-057). Nur die Meldung
behauptete den dauerhaften Effekt. Bei einer Zahl, die das gesamte
Overfitting-Budget des Projekts trägt, ist das die schlechteste Stelle für
eine falsche Zusage — sie lässt künftige DSR-Rechnungen zu optimistisch
aussehen, und niemand sieht nach, weil die Meldung ja das Gegenteil sagte.
Behoben, mit einem Test, der gegen den alten Text durchfällt.

### Konsequenzen

- **n_eff 5,1** statt 3,4; ρ̄ 0,18 statt 0,26; 38 prüfbare Märkte statt 26.
- **`MIN_SHARPE` 0,33.** Der Test in `tests/test_gate1.py` hält die Zahl fest,
  damit ihre Änderung zwei Diffs kostet statt keinen.
- **Phase A in ZIEL.md ist erledigt**, beim zweiten Anlauf und mit der
  Begründung, warum der erste zu kurz griff: dort wurden Anlageklassen
  hinzugefügt, hier wurden sie danach ausgesucht, ob sie mit dem Bestand
  mitlaufen.
- **Der Versuchszähler steht unverändert bei 16.** Kein Lauf dieses ADRs hat
  einen gekostet.
- **Offen bleibt die Grenze der Kennzahl selbst:** n_eff misst die
  Korrelationsstruktur, nicht die Güte der Reihen. 38 Märkte mit ρ̄ = 0,18
  sind kein Beweis, dass sich in ihnen etwas finden lässt — nur, dass ein
  Fund dort eher zeigbar wäre.

---

## ADR-060 — Der LLM-Allokator, drittes Mal, mit einem fairen Korb: −1,50
**Datum:** 2026-09-03

**Die Entscheidung:** Der Einwand aus dem ROADMAP-Block ist ausgeräumt, und er
rettet den Allokator nicht. Er bekommt keinen vierten Lauf, solange sich an
den Daten nichts ändert.

### Der Einwand, der geprüft werden musste

ADR-045 und ADR-046 haben den LLM-Allokator zweimal am Gate aus ADR-004
scheitern lassen. Der ROADMAP-Block führte dagegen einen Vorbehalt, und der war
berechtigt:

> Alle bisherigen Läufe verteilten `trend` und `meanrev` auf 4h — beide
> verlieren dort dreistellig. Ein Allokator kann nicht verteilen, was nicht da
> ist.

Ein Allokator, der nur zwischen zwei Verlustquellen wählen darf, wird
zwangsläufig schlecht aussehen. Der faire Test gibt ihm etwas, das gewinnt.
`macross` auf 1d ist die einzige Strategie des Projekts mit positiver
OOS-Kennzahl.

### Der Lauf

```bash
NVIDIA_API_KEY=... uv run qt alloc --compare-baselines \
    --strategies macross,trend,meanrev --tf 1d \
    --allocate-every 24 --effort low --provider nim
```

20 Aufrufe, keiner aus dem Cache, rund eine halbe Stunde. **Der Blocker aus
`docs/ZIEL.md` Phase C.2 („in dieser Umgebung ist kein API-Schlüssel gesetzt")
gilt in dieser Umgebung nicht mehr** — die Kette läuft gegen echte Modelle
durch.

| Allokator | Sharpe | Rendite | MaxDD | Zeit i. M. | Umsatz | Trades |
|---|---|---|---|---|---|---|
| **llm** | **−1,50** | −16,1 % | −16,4 % | 25,0 % | 507.926 | 28 |
| equal_weight | 0,00 | −0,5 % | −9,2 % | 74,4 % | 1.053.279 | 72 |
| vol_parity | **+0,58** | +8,0 % | −10,4 % | 84,0 % | 1.164.209 | 79 |
| best_single | −1,72 | −9,4 % | −9,4 % | 5,8 % | 213.814 | 8 |

Durchgefallen an vier Kriterien gleichzeitig, darunter dem absoluten: der
Kandidat verdient out-of-sample kein Geld, unabhängig von jeder Baseline.

### Was der Lauf ausschließt, und was nicht

**Ausgeschlossen ist die bequeme Erklärung.** Die Telemetrie sagt: 20 Aufrufe,
**0 Rückfälle auf Gleichgewichtung, 0 halluzinierte Labels, 0 bewusste
Ausstiege**. Das Modell hat also sauber geantwortet, in gültigem Schema, mit
gültigen Strategienamen — es hat schlecht verteilt. ADR-018 (jeder Fehler wird
zu Gleichgewichtung) hat nichts zu tun gehabt; wäre der Allokator
zusammengebrochen, stünde hier die Gleichgewichtungszeile.

**Wie er verliert, ist die interessante Zeile.** Zeit im Markt 25,0 % gegen
74,4 % bei Gleichgewichtung. Der Allokator hat sich weitgehend
herausgehalten — und lag damit in einem Fenster falsch, in dem
Dabeibleiben die bessere Wahl war. Genau deshalb gibt es die Untergrenze aus
ADR-016: wer ein Viertel der Zeit investiert ist, beantwortet eine andere
Frage als die Baselines, und ein Vergleich der Sharpes wäre dann kein
Vergleich.

**Nicht ausgeschlossen ist Zufall.** Es ist **ein** OOS-Fenster. Das Gate sagt
das selbst („ein Vorsprung, der nicht in der Mehrheit der Fenster steht, ist
eine Zufallsstichprobe"), und der Satz gilt in beide Richtungen: ein Rückstand
in einem Fenster ist auch keiner. Mit 2.803 Tagesbars und der
Fenstergeometrie 2000/24/500 gibt es kein zweites — mehr Fenster brauchen
mehr Kalenderzeit oder mehr Märkte, nicht mehr Aufrufe.

### Konsequenz

Der Punkt „Gate mit `macross` im Korb" verschwindet aus der Liste im
ROADMAP-Block: er ist gelaufen. Die Zeile über den LLM-Allokator lautet jetzt
**dreimal geprüft, dreimal gescheitert**, und beim dritten Mal ohne die Ausrede
des schlechten Korbs.

Ein vierter Lauf wäre eine weitere Konfiguration auf denselben Daten. Was
fehlt, ist kein besserer Prompt, sondern ein zweites Testfenster — und das
liefert nur eine breitere Datenbasis (ADR-055) oder Vorwärtszeit.

**Der Cache liegt im Repo.** Die 20 Einträge dieses Laufs sind per
`git add -f` aufgenommen, wie es die Konvention in `.gitignore` für einen
teuren Lauf vorsieht — ein Nachvollziehen kostet damit null Aufrufe statt
zwanzig. Vorher geprüft statt der Zusage geglaubt: keine der zwanzig Dateien
enthält einen Schlüssel, einen Symbolnamen oder ein Datum. Die Anonymisierung
aus ADR-003/017 hält also nicht nur im Briefing, sondern auch in dem, was
davon liegen bleibt.

---

## ADR-059 — Negativkontrollen für den Rest: drei Wege, wie eine Kontrolle lügt
**Datum:** 2026-09-03

**Die Entscheidung:** Jede registrierte Strategie bekommt eine
Negativkontrolle. Dabei wurden **drei** Stellen gefunden, an denen die
vorhandene Kontrolle ein Urteil ausgab, das keines war — dazu eine im
Datenpfad darunter, die den Store still um fünf Märkte kürzte, und eine im
täglichen Tick, der seinen Zustand auf einen zusammengeführten Zweig schrieb
und den Fehlschlag als Erfolg meldete.

Alle fünf haben dieselbe Form: **etwas fiel aus und sah aus wie ein
Ergebnis.** Das ist dasselbe Muster wie ADR-051 (ein Konto lief nicht,
während hier stand, es laufe) und ADR-053 (fünf Funde, kein Test rot) — nur
jetzt in den Kontrollen selbst, also in der Schicht, die genau das verhindern
soll.

---

### Fund 1: der Store verlor fünf von vierzehn Märkten, ohne es zu sagen

Der Container ist frisch, `/data/*` ist nicht versioniert, also wurden die
14 Krypto-Märkte aus ADR-050 neu gezogen. Geschrieben wurden **neun**:

```
$ uv run qt data pull --symbols BTC/USD,...,AVAX/USD --tf 1d --since 2019-01-01
     ALGO/USD  1d    2,577 Bars      ...  (9 Zeilen)
```

Fehlend: ADA, DOGE, DOT, SOL, AVAX — exakt die fünf, die ADR-050 mit „ab
2021" führt. Kein Fehler, keine Warnung, neun Zeilen sehen so vollständig aus
wie vierzehn.

**Die Ursache, gemessen statt vermutet.** Coinbase beantwortet
`fetch_ohlcv(since=X, limit=300)` mit dem **Fenster** `[X, X+300 Tage)`, nicht
mit „alles ab X". Für SOL/USD, gelistet am 2021-06-17:

| `since` | Antwort |
|---|---|
| 2019-01-01 | **leere Liste** |
| 2021-01-01 | 133 Bars ab 2021-06-17 |

`fetch_ohlcv` brach bei der ersten leeren Seite ab — richtig am Ende der
Historie, falsch davor. Eine leere Seite heißt zweierlei, und der Code kannte
nur eine Bedeutung.

**Behoben:** vor der ersten Zeile wird der Cursor weitergeschoben statt
abgebrochen; nach der ersten Zeile bleibt der Abbruch. Zwei Tests, einer
fällt gegen den alten Code durch. `qt data pull` nennt jetzt außerdem die
Symbole, die nichts geliefert haben.

**Warum das mehr ist als ein Datenfehler:** `n_eff`, der Querschnitts-Median
und jede Zahl aus `qt placebo cross` rechnen über die Märkte, *die da sind*.
Ein stiller Ausfall verwandelt einen unvollständigen Test in einen, der
vollständig aussieht — genau das, wogegen `cross_market_control` im
Kommentar argumentiert, eine Ebene tiefer.

---

### Fund 2: die Kontrolle war für pfadabhängige Strategien nicht anwendbar

`qt placebo shuffle --strategy trend` lieferte kein Ergebnis, sondern
Kalibrierfehler **0,0998** und Exit 1. Die Kalibrierprobe hat also
funktioniert; nur war die Diagnose unvollständig.

Der Grund ist kein Fehler in einer der beiden Seiten. `signal_series` zeichnet
**einen durchgehenden Lauf** über die ganze Historie auf. `walk_forward` setzt
die Strategie **je Fenster neu auf**. Für `macross` ist das gleichgültig — sie
ist eine reine Funktion ihres Fensters, Abweichung 0,0000 (ADR-054). `trend`
trägt einen Trailing-Stop über Bars; ihre beiden Fassungen sind verschieden,
und zwar **bevor** irgendetwas gewürfelt wurde.

**Die Konsequenz war, dass jede zustandsbehaftete Strategie prinzipiell keine
Kontrolle bekommen konnte** — `trend`, `meanrev`, `crossmom`, `crossrev` und
`timesfm`, also fünf von neun.

**Behoben durch einen Wechsel des Vergleichspunkts.** Verglichen wird gegen
den **Abspieler mit den echten Gewichten**, nicht gegen die Strategie: nur er
ist mit den Ziehungen konstruktionsgleich — gleiche Bauform, gleiche
Fenstergeometrie, nur ohne Würfel. Der Abstand zur Strategie verschwindet
nicht, er bekommt einen Namen und steht im Bericht:

```
  Strategie selbst      +0.158
  Abspieler (Referenz)  +0.059  Pfadabhaengigkeit 0.0998
```

Für eine zustandslose Strategie muss diese Zahl null sein; dort ist sie
weiterhin ein Kalibrierfehler. `macross` liefert unverändert 0,0000, und
ADR-054 bleibt damit gültig — nachgerechnet: Perzentil 75,5 % bei 200
Ziehungen gegen 74,3 % bei 1000 in ADR-054.

---

### Fund 3: eine Querschnittsstrategie fiel durch, ohne je gehandelt zu haben

```
$ uv run qt placebo shuffle --strategy crossmom --symbol BTC/USD --tf 1d
  Ziehungen mindestens so gut wie die echte: 5 von 5
  Perzentil der echten Strategie: 0.0%
DURCHGEFALLEN
```

Eine Rangfolge über **einen** Namen gibt es nicht (`MIN_NAMEN = 8`), also war
jedes Gewicht `nan`, jede Rendite null und jede Ziehung null. Null gegen null
ergibt Perzentil 0 %, und die Kalibrierprobe war zufrieden: beide Seiten
stimmten überein, weil beide nichts taten.

Drei Änderungen, jede gegen einen eigenen Weg in dieses Ergebnis:

1. `permutation_control` bricht ab, wenn der Abspieler out-of-sample keinen
   Trade macht oder das Signal durchgehend flach ist.
2. `qt placebo shuffle` nimmt eine Symbolliste und gibt einer
   Querschnittsstrategie ohne Angabe **den ganzen Store** — dieselbe Regel,
   die `qt gate` seit ADR-058 anwendet.
3. `qt placebo cross` **verweigert** Querschnittsstrategien und nennt den
   richtigen Befehl. Ein Lauf je Markt einzeln ist bei ihnen keine
   schwächere Prüfung, sondern gar keine.

---

### Fund 4: die erste Fassung der Querschnittskontrolle hätte bestanden

Eine Querschnittsstrategie behauptet nicht „ich weiß **wann**", sondern „ich
weiß **welcher Markt**". Die Episoden-Permutation aus ADR-054 ist für sie die
falsche Kontrolle: sie tauscht je Symbol getrennt und zerstört damit die
Nettoneutralität, die die Strategie ausmacht.

Die neue Kontrolle würfelt entsprechend die **Zuordnung**. Die erste Fassung
loste sie je Halteblock neu aus — gleiche Blöcke, gleiches Brutto, gleiches
Netto, jeden Monat eine neue Zuordnung. Sie sah strenger aus. Das Ergebnis:

```
  Ziehungen mindestens so gut wie die echte: 0 von 3
  Perzentil der echten Strategie: 100.0%
BESTANDEN
```

**Das wäre die erste bestandene Negativkontrolle in der Geschichte dieses
Projekts gewesen.** Sie war ein Artefakt, und die Reibungszeile daneben sagt,
warum:

| | echt | je Ziehung |
|---|---|---|
| Trades | 352 | **3.452** |
| Umsatz | 2,38 Mio. | **29,1 Mio.** |

Die Kontrolle handelte zehnmal so oft und verlor an den Gebühren, nicht an
der Information. Der Mechanismus ist das Rebalancing-Band (ADR-008): eine
Rangfolge aus 12-Monats-Momentum wandert langsam, die meisten
Gewichtsänderungen bleiben unter dem Band und kosten nichts. Eine frei
ausgeloste Zuordnung springt jedes Mal darüber.

**Ersetzt durch eine Umbenennung der Märkte:** *eine* Permutation für die
ganze Historie, der komplette Gewichtsverlauf von Markt A geht an `π(A)`.
Damit bleibt jeder Gewichtssprung erhalten — nach Größe und Zeitpunkt — und
zufällig ist ausschließlich, welcher Markt gemeint ist. Nachgemessen: 352
Trades echt gegen Median 291 je Ziehung, 2,38 gegen 1,98 Mio. Umsatz. Der
Rest der Differenz kommt daher, dass die Märkte verschieden lange Historien
haben; er geht **zugunsten der Kontrolle**, ein Durchfallen ist damit die
sichere Richtung.

Der Preis ist Trennschärfe: eine Ziehung ist eine Auslosung, nicht sechzig.
Dieselbe Grenze wie in ADR-054 — „nicht gezeigt" heißt nicht „gezeigt, dass
nichts da ist".

**Die Reibungszeile steht jetzt im Bericht statt in einem ADR.** In ADR-054
war sie Prosa („Die gewürfelten Fassungen zahlen dieselbe Reibung"). Als
Prosa hätte sie diesen Fund nicht verhindert — als Zeile im Bericht hat sie
ihn geliefert.

---

### Das Ergebnis: acht Strategien geprüft, acht durchgefallen

*Datenstand 2026-09-03, 27 Märkte, Walk-Forward 1000/250/20, 200 Ziehungen.*

| Strategie | Kontrolle | Ergebnis | Urteil |
|---|---|---|---|
| `macross` BTC/USD | Episoden | Perzentil **75,5 %** | durchgefallen |
| `trend` BTC/USD | Episoden | Perzentil **70,5 %** | durchgefallen |
| `meanrev` BTC/USD | Episoden | Perzentil **38,5 %** | durchgefallen |
| `timesfm` BTC/USD | Episoden | Perzentil **66,5 %** | durchgefallen |
| `crossmom` 27 Märkte | Umbenennung | Perzentil **45,0 %** | durchgefallen |
| `crossrev` 27 Märkte | Umbenennung | Perzentil **64,5 %** | durchgefallen |
| `trend` 26 Märkte | Querschnitt | Median-Sharpe **−0,17**, 38 % positiv | durchgefallen |
| `meanrev` 26 Märkte | Querschnitt | Median-Sharpe **−0,20**, 38 % positiv | durchgefallen |

Die Reibungsprobe hält in allen sechs Permutationsläufen: `macross` 47 Trades
gegen Median 46, `crossmom` 352 gegen 291, `crossrev` 1.041 gegen 962. Wo die
Ziehungen abweichen, tun sie es nach unten — sie zahlen weniger Gebühren als
das Original, das Urteil fällt also in die sichere Richtung.

**Zwei Lücken, ausdrücklich als Lücken:**

`orderflow` hat keine Kontrolle, weil sie Handelsdaten braucht und die aus dem
in ROADMAP.md genannten Grund nicht im Repo liegen (27,8 MB für 59 Tage).
Ungeprüft ist nicht bestanden.

`timesfm` hat eine, die kaum etwas zeigt: vier Trades über sieben OOS-Fenster.
Ein Perzentil aus vier Entscheidungen sagt fast nichts. Der Querschnittslauf
über 27 Märkte wurde vom Betriebssystem nach 17 Märkten abgebrochen — das
Modell wird je Markt neu geladen. Beides bleibt so stehen; nach ADR-022 ist
ein Backtest dieser Strategie ohnehin strukturell nicht vertrauenswürdig, das
Pretraining kennt die Kursreihen möglicherweise.

**`meanrev` liegt unter dem Median seiner eigenen Ziehungen** (38,5 %): die
Strategie ist schlechter als ihre zufällig platzierte Fassung. Sie war schon
vorher als Testinstrument markiert (Phase 1); jetzt steht eine Zahl dabei.

---

### Nebenbefund: die dokumentierte Kennzahl von `macross` reproduziert nicht

Aus einem frisch gezogenen Store, mit denselben Fenstern:

| | ROADMAP (Stand 2026-09-01) | gemessen (Stand 2026-09-03) |
|---|---|---|
| `macross` BTC/USD OOS-Sharpe | 0,31 | **0,25** |
| `macross` ETH/USD OOS-Sharpe | 0,32 | **0,28** |

Die Fenstergeometrie erklärt es nicht: verschiebt man den Anker des
Walk-Forward um 1 bis 10 Bars, bewegt sich der BTC-Wert zwischen 0,229 und
0,266 — die Kennzahl ist auf dieser Skala wackelig, aber 0,31 liegt außerhalb.
Woran die Differenz sonst liegt, ist mit dem heutigen Store nicht
rekonstruierbar; der alte existiert nicht mehr.

**Festgehalten wird deshalb, was messbar ist:** die Zahl, auf der die
Paper-Konten und die ganze Begründung aus ADR-035 ruhen, ist aus einer
frischen Umgebung nicht reproduzierbar, und der Unterschied hat dieselbe
Größenordnung wie der behauptete Effekt. Das ist die Konvention aus ROADMAP.md
(„jede Ergebnistabelle nennt ihren Datenstand") in ihrer unangenehmen Form:
sie hilft nicht nur beim Einordnen, sie zeigt auch, wenn nichts einzuordnen
ist.

---

### Fund 5: der tägliche Tick schrieb auf einen zusammengeführten Zweig

Beim Nachsehen des Laufzeitzustands — der erste Punkt des HIER-WEITER-Blocks —
stand in beiden Konten der 2026-09-02 als letzter verarbeiteter Bar, bei einem
Kalendertag 2026-09-03. Kein Commit im Repo, weder auf `main` noch auf dem
alten Sitzungszweig, schreibt den Kontostand über den 2026-09-02T05:32Z
hinaus.

Zwei Dinge in `scripts/paper_tick.sh` erklären, warum das niemandem auffiel:

1. `ZWEIG` stand fest auf `claude/llm-quant-algo-planning-f1ohgo`, und dessen
   Pull Request ist zusammengeführt. `git push -u` legt einen gelöschten Zweig
   wortlos neu an — der Zustand landet daneben statt dort, wo er gelesen
   wurde. Jetzt schreibt das Skript auf den Zweig, auf dem sein Checkout
   steht; ohne Zweig bricht es ab, statt zu raten.
2. Nach der Push-Schleife stand **unbedingt** `echo "Kontostand gesichert."` —
   auch wenn alle vier Versuche gescheitert waren. Ein Commit ohne Push
   überlebt den Container nicht, und die Zeile behauptete das Gegenteil.
   Jetzt meldet das Skript den Fehlschlag und endet mit Code 1.

Ob die Routine überhaupt feuert, ist damit **nicht** beantwortet — das sagt
erst der nächste Tag. Der von Hand nachgeholte Tick hat die beiden Konten
zum ersten Mal überhaupt handeln lassen: BTC zu 77.437,40, ETH zu 2.418,61,
je 600 Gebühr. Die Order lag seit dem 2026-09-02 vorgemerkt.

Das ist ADR-051 zum dritten Mal, in einer neuen Verkleidung. Die Lehre bleibt
dieselbe und ist im ROADMAP-Block jetzt ein Befehl: `Letzter verarbeiteter
Bar` muss von Tag zu Tag weiterwandern; ein „Status: laeuft" allein sagt
nichts.

---

### Konsequenzen

- **`qt placebo shuffle` wählt die Kontrolle nach dem Typ der Strategie.**
  Timing → Lage der Episoden, Querschnitt → Zuordnung der Märkte. Die falsche
  Kontrolle ist kein schwächerer Test, sondern ein anderer.
- **Der Bericht führt die Reibung mit.** Trades und Umsatz, echt gegen
  Ziehungen. Fund 4 ist genau daran aufgefallen.
- **Kein Lauf hat einen Versuch gekostet.** Negativkontrollen befragen keinen
  neuen Kandidaten out-of-sample; der Zähler steht unverändert bei 16
  (ADR-032).
- **`fetch_ohlcv` unterscheidet die leere Seite vor der Notierung von der am
  Ende der Historie.** Zwei Tests, einer fällt gegen den alten Code durch.
- **`scripts/paper_tick.sh` schreibt zurück, wo es gelesen hat, und meldet
  einen gescheiterten Push als Fehlschlag.**
- **Acht von neun Strategien haben jetzt eine Negativkontrolle, und keine
  besteht sie.** Der Satz aus ZIEL.md — „Nichts im Repo hat je eine
  Negativkontrolle bestanden" — ist damit nicht mehr eine Beobachtung über
  drei Strategien, sondern über acht.

---

## ADR-058 — Querschnitt statt Timing: was aus NVIDIAs Blueprint taugt, und was nicht
**Datum:** 2026-09-03

**Die Entscheidung:** Aus `NVIDIA-AI-Blueprints/quantitative-signal-discovery-agent`
(Apache-2.0, Stand `9a89d24`) werden **drei Ideen** übernommen und **keine Zeile
Code**: der Querschnitts-Rank-IC als Kennzahl, ein benanntes Operator-Vokabular,
und die Aritätsprüfung als billiger Vorfilter. Ihre Auswertungsmethodik wird
ausdrücklich **nicht** übernommen — sie ist an drei Stellen schwächer als die
hiesige, und das lässt sich an diesen Daten zeigen.

---

### Warum der Querschnitt überhaupt interessant ist

Alle bisherigen sieben Strategien sind **Timing**-Strategien: ein Markt, eine
Entscheidung. Über 27 Märkte laufen sie 27-mal getrennt, und weil die Märkte zu
0,26 korrelieren, sind das 3,4 effektive Tests (ADR-055). **Die Korrelation ist
dabei reiner Verlust.**

Eine Querschnittsstrategie fragt anders: welche Märkte laufen besser als die
übrigen? Sie kauft die obere Hälfte, verkauft die untere, ist in Summe neutral.
Was allen gemeinsam ist — der Krypto-Zyklus, die Aktienrallye — fällt heraus.
Dieselbe Korrelation, die 26 Einzeltests entwertet, ist hier das, was
neutralisiert wird.

Diese Familie war vor Phase A nicht möglich: ein Querschnitt aus dreizehn
Wetten auf dieselbe Sache ist keiner.

---

### Ihr Annahmekriterium, gemessen an unseren Daten

Der Blueprint akzeptiert ein Signal bei `|IC| ≥ 0,02` und `p < 0,05`, wobei
`t = mittel / (std / √T)` mit T = Zahl der Tage. Das unterstellt, die täglichen
IC-Werte seien unabhängig.

Sie sind es nicht. Ein Signal mit 252 Tagen Rückschau ändert sich von Tag zu Tag
kaum, und die Vorwärtsrenditen überlappen sich zu 20 von 21 Tagen. Gemessen an
`crossmom` über diesen Store:

| | |
|---|---|
| mittlerer Rank IC | +0,0307 |
| Autokorrelation der IC-Reihe | **ρ = +0,750** |
| Datenpunkte | 2437 |
| effektive Datenpunkte | **348** |
| t nach ihrer Rechnung | **+3,75** (p = 0,0002) |
| t nach Korrektur | **+1,42** (p = 0,157) |
| **Aufblähung** | **Faktor 2,64** |

**Dasselbe Signal besteht ihr Kriterium und fällt bei ehrlicher Rechnung
durch.** Die Korrektur ist deshalb kein Schalter: `bestanden` hängt am
korrigierten Wert, der naive steht nur zum Vergleich daneben.

**Es ist nicht die Formel aus ADR-052.** Dort ging es um den Mittelwert von n
gleichzeitig beobachteten, korrelierten Reihen: `n/(1+(n−1)ρ̄)`. Hier um den
Mittelwert **einer** Reihe mit Autokorrelation über die Zeit: `T·(1−ρ)/(1+ρ)`.
Beide heißen „effektive Stichprobe" und sind verschiedene Größen. Sie zu
verwechseln wäre derselbe Fehler wie in ADR-055, nur andersherum.

Zur Fairness, gemessen und nicht vermutet: gegen **reines** Rauschen hält ihre
`|IC| ≥ 0,02`-Schwelle (0 von 20 Läufen angenommen). Der p-Wert ist bei ihnen
also nicht das bindende Gate. Das bindende Problem ist Selektion, siehe unten.

---

### Was nicht übernommen wird, und warum

| Ihre Stelle | Befund |
|---|---|
| `exec(code, namespace)`, Zeile 375 | LLM-Code läuft ungeprüft im Prozess. ADR-029 ist dem um Klassen voraus. |
| `execute_signal_code` | Wählt bei mehreren Signalfunktionen die mit dem höchsten IC **über die volle Historie** und berichtet dann genau diesen IC. `grep` nach train/test/split findet in `src/` nichts. |
| `abs(mean_ic)` | Vorzeichenblind. Ein Signal, das das Gegenteil vorhersagt, gilt als gleich gut — verdoppelt die effektive Versuchszahl. |
| Kostenmodell | Existiert nicht. Nirgends `fee`, `slippage`, `turnover`. Nach ADR-056 ist ein IC ohne Umschlagbudget keine handelbare Aussage. |
| Optimierungsschleife | `max_iterations: 3 × num_signals: 2`, jede Runde bekommt Feedback aus dem IC der letzten. Sechs Versuche gegen die Testmetrik, ohne DSR, ohne Versuchszähler. |

Zur zweiten Zeile gehört eine eigene Korrektur bei uns: unsere
Vorwärtsrendite beginnt am **Open t+1**, nicht am Close t. Der Blueprint rechnet
`close[t+k]/close[t]` — Einstieg zu einem Kurs, den man gerade erst benutzt hat,
um sich zu entscheiden. Die Umstellung allein senkt den gemessenen IC von
**0,0369 auf 0,0307**.

---

### Der erste Lauf, und zwei Fehler unterwegs

**Der Panelaufbau hat einen ganzen Lauf gekostet.** Der erste Entwurf verlangte,
dass alle Symbole einen Bar zum aktuellen Zeitpunkt haben. Die Engine arbeitet
die Bars eines Zeitpunkts aber **nacheinander** ab — fragt sie das erste Symbol
zur Zeit T, haben die übrigen ihren T-Bar noch nicht im Store. Die Bedingung war
für alle außer dem zuletzt bearbeiteten Symbol unerfüllbar, und die Strategie
machte über die ganze Historie **null Ausführungen**. Kein Test war rot; sie
handelte einfach nicht.

Gerechnet wird deshalb auf dem letzten Zeitpunkt **vor** `jetzt`, den genug
Symbole gemeinsam haben — vollständig, weil die Engine ihn abgeschlossen hat.
Der Preis ist ein Bar Verzögerung, und der löst zugleich das Kalenderproblem:
Krypto handelt sonntags, ETFs nicht.

**Ohne Umschichtrhythmus ist die Familie von den Kosten erledigt.** Täglich
umgeschichtet schlägt `crossmom` über 27 Märkte **79,0× sein Eigenkapital pro
Jahr** um, gegen ein Budget von 7 (ADR-056). Monatlich — 21 Bars, die Frequenz
der Querschnittsliteratur seit Jegadeesh/Titman 1993 und **keine an diesen Daten
gewählte Zahl** — sind es 4,6×.

**Gate 1, `crossmom` auf 1d:**

| Kriterium | Wert | verlangt |
|---|---|---|
| Ausführungen | 494 | ≥ 20 |
| Umschlag/EK/Jahr | **4,6×** | ≤ 7× |
| OOS-Sharpe | **−0,24** | ≥ 0,41 |
| DSR gegen 16 Versuche | 0,010 | ≥ 0,95 |

**Das ist die erste Strategie des Projekts, die Aktivität und Umschlagbudget
besteht und bis in einen Walk-Forward kommt.** Sie scheitert dort an der Zahl,
auf die es ankommt. Der Lauf zählt als Versuch; der Zähler steht auf **16**.

`crossrev` (Kurzfrist-Umkehr, das entgegengesetzte Vorzeichen) bricht bei
19,2× Umschlag ab und kostet keinen Versuch. Sie bleibt trotzdem in der
Bibliothek: erst zwei Strategien mit entgegengesetztem Vorzeichen zeigen, dass
die Mechanik das Vorzeichen überhaupt durchreicht — ihre ICs sind +0,031 und
−0,014.

---

### Konsequenzen

- **`qt ic`** misst den Querschnitts-Rank-IC und stellt beide t-Werte
  nebeneinander. Exit 0 nur bei |t_korrigiert| ≥ 1,96.
- **`qt.research.operators`** — 29 benannte Primitive (`TS_*` über die Zeit,
  `CS_*` über den Querschnitt) plus `pruefe_aufrufe`, eine Aritätsprüfung
  **auf dem AST, ohne Ausführung**. Ein Test hält fest, dass dabei nichts
  läuft — der Unterschied zu ihrer `exec`-Zeile.
- **Kein Operator schaut nach vorn**, geprüft am Quelltext statt am Vertrauen.
- **`qt gate` kennt Querschnittsstrategien** und gibt ihnen den ganzen Store
  statt eines Hauptmarkts.
- **Versuchszähler 16.** `crossmom` ist eingebucht, `crossrev` nicht — sie hat
  die Daten nie out-of-sample befragt (ADR-032).
- **Offen:** 27 Märkte sind ein dünner Querschnitt. Der Blueprint arbeitet mit
  500 Namen, und die IC-Streuung von 0,40 bei uns gegen deren dichteres Panel
  ist der Grund, warum hier auch ein echter Effekt schwer zu zeigen wäre. Das
  ist dieselbe Datenknappheit wie überall in diesem Projekt, nur an einer
  anderen Achse.

---

## ADR-057 — Gate 1 ist jetzt ein Programm, und die Buchführung war an drei Stellen falsch
**Datum:** 2026-09-02

**Die Entscheidung:** `qt gate` prüft alle Kriterien aus `ZIEL.md` in einem
Lauf, in Kostenreihenfolge, mit **fest verdrahteten Schwellen und ohne eine
einzige Option, die eine davon setzt.** Wer die Latte senken will, ändert eine
Konstante in `qt.research.gate` — und das erscheint in einem Diff.

Phase C sollte die Suche sein. Bevor sie beginnen konnte, war zu klären, wonach
gesucht wird. Dabei kam heraus, dass drei Zahlen, auf denen die Suche steht,
falsch geführt waren — alle drei zu unseren Gunsten.

---

### Warum das Gate ein Programm sein muss

Die sechs Kriterien standen als Prosa in `ZIEL.md`, die Werkzeuge als sechs
einzelne Befehle daneben. Niemand erzwang die Reihenfolge, niemand führte das
Ergebnis zusammen.

Das ist die Bauform, an der dieses Projekt schon zweimal gescheitert ist: das
Paper-Konto stand wochenlang still, während die ROADMAP behauptete, es laufe
(ADR-051); ein Dokument widersprach sich in zwei aufeinanderfolgenden Absätzen
(ADR-052). **Ein Kriterium, das nur ein Mensch anwendet, ist ein Vorsatz.**

Die Schwellen als Konstanten und nicht als Optionen ist der eigentliche
Bauentscheid. Nach einem verfehlten Kriterium ist die Versuchung, den Maßstab
nachzubessern, am größten — ADR-055 hält fest, wie nah dieses Projekt daran
schon einmal war. Ein `--min-sharpe 0.30` hinterließe keine Spur. Ein
`test_das_gate_kennt_keine_option_die_eine_schwelle_setzt` schlägt fehl, wenn
jemand eine einbaut.

---

### Fehler 1: Der Versuchszähler kannte sieben Hypothesen nicht

Der Zähler stand bei **8**. Alle acht stammten aus dem LLM-Loop vom 31.08. Die
sieben handgeschriebenen Strategien in `qt/strategy/library/` — `trend`,
`meanrev`, `elliott`, `macross`, `hashribbon`, `orderflow`, `timesfm` — waren
nicht dabei.

Jede von ihnen ist eine Hypothese, die an *diesen* Daten geprüft wurde. Jede
hat ein ADR mit einem Walk-Forward-Ergebnis. `ZIEL.md` sagt selbst: „Sieben
Hypothesen geprüft, sieben gescheitert." Das Projekt wusste also von sieben
Blicken — nur die Zahl, die in die Deflated Sharpe Ratio eingeht, wusste es
nicht.

ADR-032 sagt „nur abgeschlossenes Screening zählt". Das war eine Entscheidung
über die Buchführung des Research-Loops, nicht darüber, was ein Blick auf die
Daten ist. ADR-005 ist älter und eindeutiger: die Registry zählt **alle je
getesteten** Kandidaten. Die sieben über eine Formalie auszunehmen — sie kamen
nicht durch den Loop — wäre genau die Technikalität, die einen Schutz zur Zierde
macht.

Nachgetragen über `qt trials --backfill`, idempotent. **Der Zähler steht jetzt
bei 15.** Der erwartete beste Sharpe aus reinem Rauschen steigt damit von 1,459
auf 1,771 — die Hürde wird für jeden künftigen Kandidaten **härter**. Eine
Korrektur der Buchführung, die das eigene Ergebnis verbessert, wäre verdächtig;
diese verschlechtert es.

### Fehler 2: Ein Rauchtest hob den Versuchszähler

Beim End-to-End-Test der Kette mit `qt research --generate 3 --stub` sprang der
Zähler von 15 auf **18**. Der Stub-Lauf existiert, um die Verdrahtung ohne
API-Schlüssel zu prüfen; seine Kandidaten stehen fest, unabhängig davon, was
die Kurse sagen. Sie sind damit kein Selektionsereignis im Sinne von
Bailey/López de Prado und dürfen den Nenner nicht belasten.

Drei Zeilen entfernt, Zähler zurück auf 15. `--stub` schreibt jetzt nach
`registry_stub.duckdb`. Die volle Schreibstrecke wird weiterhin geprüft — nur
eben in einer Datei, die niemanden etwas kostet.

### Fehler 3: Die Registry war nicht versioniert, und die `.gitignore`-Regel, die sie hätte retten sollen, funktionierte nicht

`qt/research/registry.py` begründet über zwanzig Zeilen, warum die Registry
eine Datenbank mit Transaktionen sein muss: geht sie verloren, fällt der
Zähler zurück und **jede künftige DSR wird zu optimistisch** — „ein zu
optimistischer Overfitting-Schutz ist schlimmer als gar keiner".

Sie lag unversioniert in `data/`, das komplett gitignored ist, in einem
Container, der laut Umgebungsbeschreibung nach Inaktivität eingezogen wird.
Überlebt hat sie, weil das Arbeitsverzeichnis auf einem persistenten Volume
liegt — nicht, weil irgendetwas sie geschützt hätte.

Beim Beheben fiel der eigentliche Fehler auf. Die `.gitignore` hatte längst
eine sorgfältig begründete Ausnahme:

```
/data/
!/data/paper/     <- wirkungslos
```

**Git kann einen Pfad nicht wieder aufnehmen, dessen Elternverzeichnis
ausgeschlossen ist.** Die Ausnahme tat seit Wochen nichts. Der Paper-Kontostand
lag nur deshalb im Repo, weil `scripts/paper_tick.sh` mit `git add -f` schreibt
und den Ausschluss dabei umgeht. Der Kommentar darüber erklärte also eine Regel,
die es nicht gab — und behauptete nebenbei, das Paper-Konto sei „das einzige
nicht rekonstruierbare Stück im Datenverzeichnis". Das stimmte nie.

`/data/*` statt `/data/`, beide Ausnahmen wirken jetzt, geprüft an drei
Pfaden.

---

### Der erste Lauf über die Bibliothek

Alle sieben, auf 1d, gegen den vollen Store:

| Strategie | Ausführungen | Umschlag/EK/Jahr | Urteil |
|---|---|---|---|
| trend | ✓ | **15,4×** | durchgefallen (Umschlag) |
| meanrev | ✓ | **15,7×** | durchgefallen (Umschlag) |
| elliott | ✓ | **13,3×** | durchgefallen (Umschlag) |
| macross | ✓ | **8,8×** | durchgefallen (Umschlag) |
| hashribbon | ✓ | **7,1×** | durchgefallen (Umschlag) |
| orderflow | **1** | — | durchgefallen (Aktivität) |
| timesfm | **0** | — | durchgefallen (Aktivität) |

**Keine einzige kommt bis zum Walk-Forward.** Fünf scheitern am
Umschlagbudget, zwei daran, dass sie gar nicht handeln.

`hashribbon` scheitert mit 7,1 gegen 7,0. Das ist knapp, und genau deshalb
bleibt die Schwelle stehen: sie wurde in ADR-056 hergeleitet, **bevor** diese
Zahl bekannt war. Sie jetzt auf 7,5 zu setzen, wäre kein besserer Maßstab,
sondern ein Maßstab, der sich an ein Ergebnis anlehnt.

**Ein Kriterium, das sich durch Nichtstun erfüllen lässt, ist keines.** Im
ersten Lauf „bestanden" `orderflow` und `timesfm` das Umschlagbudget mit 0,1×
und 0,0× — weil beiden die Datenquelle fehlt und sie schlicht nicht handeln.
Deshalb steht jetzt eine Aktivitätsschwelle davor: mindestens 20 Ausführungen.
Sie ist keine Meinung über gute Strategien, sondern die Grenze, unterhalb derer
die späteren Prüfungen nichts mehr messen können — die Permutationskontrolle
vertauscht Episodenlängen, und bei fünf Episoden gibt es kaum etwas zu
vertauschen.

**Kein Lauf hat einen Versuch gekostet.** Alle sieben brachen vor dem
Walk-Forward ab, der Zähler steht unverändert bei 15. Das ist der Zweck der
Kostenreihenfolge, nicht nur Sparsamkeit.

---

### Was das für die Suche heißt

Der Generator kannte die Grenzen nicht. Die acht Kandidaten vom 31.08. —
SmaTrend, DonchianVolBreakout, ATRChannelReversion, RocMomentum,
ZScoreMomentum, BollingerReversion, VWAPReversion, DonchianMeanReversion —
stammen aus derselben Familie wie die fünf, die am Umschlag scheitern. Ein
Generator, der das nicht weiß, läuft gegen eine Wand, die er nicht sieht.

Die zwei harten Grenzen stehen deshalb jetzt im Generator-Briefing. **Das
weicht das blinde Briefing nicht auf** (ADR-003): blind heißt keine Kurse,
keine Kennzahlen, keine Zeiträume, keine Marktnamen — nichts, woran sich eine
Idee an *diese* Daten anpassen ließe. Die Handelsfrequenz ist nichts davon; sie
folgt aus dem Gebührenplan der Börse und stünde genauso fest, wenn die Daten
andere wären.

**Die Suche selbst steht aus und braucht einen API-Schlüssel.** In dieser
Umgebung ist keiner gesetzt. Die Kette ist gegen die Stubs end-to-end geprüft;
was fehlt, ist der Zugang, nicht die Verdrahtung.

---

### Konsequenzen

- **Versuchszähler: 15.** Jeder künftige Kandidat wird gegen 16 deflationiert.
- **Sechs Kriterien plus Aktivitätsschwelle**, alle als Konstanten, keine als
  Option. `qt gate` gibt Exit 0 nur bei vollständigem Bestehen.
- **Die Registry liegt im Repo.** Eine Datei, keine Textkopie daneben — zwei
  Zahlen an verschiedenen Orten laufen auseinander.
- **Kein Kandidat aus dem Bestand ist ein Kandidat.** Die Suche startet ohne
  Vorlage, und das ist ein Ergebnis: `macross`, seit ADR-035 die einzige
  Hoffnung des Projekts, scheitert nicht am Signal, sondern daran, dass es sich
  seine eigene Handelsfrequenz nicht leisten kann.
- **Der Abbruch vor dem Walk-Forward ist Teil des Schutzes**, nicht eine
  Optimierung: was nicht gerechnet wurde, hat die Daten nicht befragt.

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
