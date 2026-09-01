# UI-Plan

> **Bezug.** Dieser Plan beschreibt ein UI für das Quant-System aus Branch
> `claude/llm-quant-algo-planning-f1ohgo` (Phase 0–6 gebaut, Gate-Lauf
> ausgewertet, ADR-045). Er liegt auf `claude/ui-planung-code-session-rg7p9y`,
> das auf `main` fußt — der Code, auf den er sich bezieht, ist hier also
> **nicht** eingecheckt. Alle Dateipfade unten meinen die des Quant-Branches.
>
> **Status:** Entwurf. Kein Code geschrieben. Die offenen Fragen stehen am Ende.

---

## 1. Wofür das UI da ist

Das System produziert seine Zahlen heute an vier Orten, die nichts voneinander
wissen:

| Wo | Was | Lebensdauer |
|---|---|---|
| Terminal-Ausgabe | Walk-Forward-Tabellen, Gate-Urteil, Sim-Gitter, Research-Trichter | bis zum nächsten `clear` |
| `reports/*.png` | Tearsheets (Equity, Drawdown, Exposure) | Datei, ungitignored nur als Ordner |
| `data/paper/*.json` | Paper-Kontostand, Kill-Switch, Fills | Datei, gitignored |
| `data/research/registry.duckdb` | jeder je erzeugte Kandidat samt Versuchszähler | Datei, gitignored |
| `.llm_cache/**` | 163 Antworten des Gate-Laufs, ~4 h Rechenzeit wert | eingecheckt |

Die offenen Punkte der ROADMAP sind fast alle **Beobachtungsaufgaben**, nicht
Bauaufgaben: Paper-Trading über Wochen laufen lassen und Live-vs-Backtest-
Divergenz messen; das Gate auf einer tragfähigen Strategiemenge wiederholen;
das 301-Tage-Loch im Order-Flow-Bestand schließen. Genau dafür ist ein UI da:
**etwas täglich anschauen, ohne es jedes Mal neu zu berechnen.**

Der schärfste Befund des Gate-Laufs — der Allokator schichtet im Median 40% des
Buches je Schritt um und kehrt 28-mal komplett — entstand, indem 145
zwischengespeicherte Vorschläge von Hand durchgesehen wurden. Das ist die
Bauanleitung für ein UI: was einmal Handarbeit war und beim nächsten Lauf
wieder Handarbeit wäre, wird ein Screen.

### Wofür es ausdrücklich **nicht** da ist

* **Kein Knopf, der handelt.** Nicht `paper run`, nicht `data pull`, nicht
  `alloc`. Ein UI, das Läufe startet, braucht Prozessverwaltung,
  Fortschrittsanzeige und Fehlerbehandlung für Dinge, die stundenlang laufen —
  und die Umgebung hat mehrfach gezeigt, dass Hintergrundprozesse hier sterben
  (ADR-037). Die Kommandozeile bleibt die Steuerung, das UI ist das Fenster.
* **Kein Zurücksetzen des Kill-Switches.** Das ist bewusst Handarbeit mit
  Begründung (`qt paper reset-killswitch --note`). Ein Knopf dafür, auch hinter
  einer Bestätigung, ist der schleichende Weg, die Freigabe abzuschaffen.
* **Kein „Promote"-Knopf für Research-Kandidaten.** Dasselbe Argument, nur
  wörtlich aus der ROADMAP: „Es gibt bewusst **keinen** Befehl, der einen
  bestandenen Kandidaten nach `strategy/library/` schreibt."
* **Keine Preisprognose, kein einzelner „bester Pfad".** `PathEnsemble` hat mit
  Absicht keine Methode, die einen Pfad zurückgibt (ADR-024). Ein Chart, der
  einen zeichnet, würde diese Entscheidung durch die Hintertür aufheben.

---

## 2. Die Randbedingungen, die den Entwurf bestimmen

Fünf Eigenschaften des Systems sind keine Details, sondern legen die Bauform
fest:

**1. Der Container überlebt nicht.** `data/` und `reports/` sind gitignored und
verschwinden mit der Session — `data/paper/` ist genau deshalb heute leer. Ein
UI, das nur als laufender Server existiert, teilt dieses Schicksal. Deshalb
muss die primäre Auslieferungsform **eine Datei sein, die man mitnehmen kann.**

**2. Kein Daemon.** Aus demselben Grund darf das UI nichts sein, das dauerhaft
laufen muss, damit Daten entstehen. Es liest, was ohnehin auf der Platte liegt.

**3. In-Sample ist die gefährlichste Zahl im System.** „Alles, was `qt backtest`
ausgibt, ist In-Sample. Belastbar ist nur `qt wf`." Eine hübsche Equity-Kurve
ist genau die Darstellung, die diesen Unterschied verschwinden lässt. Das UI
muss ihn **visuell erzwingen**, nicht in eine Fußnote schreiben.

**4. Jede LLM-Zahl hat eine Herkunft.** Anbieter, Modell, Effort, Cache-Treffer
und **Rückfallquote** gehören laut ADR-018 in jeden Report — ein Allokator, der
dauerhaft zurückfällt, ist heimlich eine Baseline und wird für gut gehalten,
weil er nie auffällt. Im UI heißt das: keine LLM-Kennzahl ohne die
Telemetriezeile daneben.

**5. Ein LLM-Lauf kostet Geld und Stunden.** Der Gate-Lauf: 145 Aufrufe, 3:47
Stunden. Ein anderer Takt heißt andere Briefings, also andere Cache-Keys, also
wieder ~4 Stunden. Ein UI, das **vor** dem Start zeigt, wieviel eines geplanten
Laufs bereits im Cache liegt, spart mehr als es kostet.

---

## 3. Die tragende Entscheidung: zwei Formen, nicht eine

Vorgeschlagen wird ausdrücklich **kein** einzelnes UI, sondern zwei
Auslieferungsformen über derselben Lesecodebasis:

```
                    qt.report.model      (liest Dateien -> Datenklassen)
                          │
              ┌───────────┴───────────┐
              │                       │
   qt report html                 qt ui
   eine .html-Datei               lokaler Read-Only-Server
   ohne Server, ohne Netz         127.0.0.1, nur GET
   auf dem Handy lesbar           Filtern, Blättern, Vergleichen
   überlebt den Container         lebt so lange wie die Session
```

**`qt report html` ist die primäre Form** — festgelegt, nicht offen: das UI
wird auf dem Handy für den täglichen Blick benutzt, ein dauerhafter Rechner, der
einen Server tragen könnte, existiert nicht. Eine einzelne, in sich geschlossene
HTML-Datei: CSS inline, PNGs als `data:`-URIs eingebettet, keine externen
Requests. Warum das und nicht zuerst der Server:

* Sie überlebt. Man kann sie committen, herunterladen, in einen Chat hängen.
  Ein Server-Screenshot ist eine Behauptung, die Datei ist der Beleg.
* Sie ist auf dem Handy lesbar. Das Paper-Konto wird über Wochen **täglich**
  angeschaut — `qt.report.daily` ist genau aus diesem Grund reiner Text. Die
  HTML-Fassung ist dieselbe Überlegung eine Stufe weiter.
* Sie braucht kein laufendes Nichts. Kein Port, kein Prozess, kein „läuft der
  Server noch".

**`qt ui` ist die Ergänzung für das, was eine statische Seite nicht kann:**
durch 29 Walk-Forward-Fenster blättern, die Research-Registry filtern, zwei
Gate-Läufe nebeneinanderlegen, in die 163 Cache-Einträge hineinsehen. Optionale
Abhängigkeit (`uv sync --extra ui`), damit die Grundinstallation schlank bleibt
— dieselbe Bauform wie `--extra nim` und `--extra timesfm`.

**Read-only ist baulich, nicht eine Einstellung.** Der Server bekommt keine
einzige Route, die etwas schreibt. Nicht „geschützt", nicht „hinter einer
Bestätigung" — sie existiert nicht. Ein UI, das schreiben *könnte*, muss man
absichern; eins, das es nicht kann, nicht.

---

## 4. Die eine fehlende Zutat: Run-Manifeste

Hier liegt die einzige echte Codeänderung im Kern, und sie ist der kritische
Pfad für alles außer dem Paper-Cockpit.

**Das Problem:** Die Ergebnisse von `qt wf`, `qt alloc`, `qt sim` und
`qt portfolio` existieren heute **ausschließlich als Terminal-Ausgabe.** Das
PNG hält die Kurve fest, aber nicht die Fenstergeometrie, nicht das Urteil,
nicht die Telemetrie, nicht die Eingaben. Ein UI kann daraus nichts lesen, weil
es nichts zu lesen gibt.

**Der Vorschlag:** ein Modul `qt.report.manifest`. Jeder Lauf schreibt am Ende
eine JSON-Datei nach `reports/runs/<utc-zeitstempel>_<art>.json`:

```jsonc
{
  "kind": "walkforward",              // backtest | walkforward | gate | sim | research | paper
  "created_at": "2026-09-01T11:24:03Z",
  "git_commit": "f6c6038",            // damit eine Zahl auf einen Codestand zeigt
  "command": "qt wf --strategy macross --symbol BTC/USD --tf 1d ...",
  "inputs":   { "strategy": "macross", "symbols": ["BTC/USD"], "timeframe": "1d",
                "since": null, "until": null, "data_range": ["2019-01-01", "2026-08-24"] },
  "geometry": { "train_bars": 1000, "test_bars": 250, "embargo_bars": 20, "n_windows": 17 },
  "evidence": "oos",                  // "in_sample" | "oos" | "simulated" | "paper"
  "metrics":  { /* Metrics.as_dict() */ },
  "windows":  [ /* eine Zeile je Fenster */ ],
  "llm":      { "provider": "nim", "model": "...", "effort": "low",
                "calls": 145, "cache_hits": 0, "fallback_rate": 0.0,
                "hallucinated_labels": 0, "deliberate_flat": 3 },
  "artifacts": { "tearsheet": "reports/macross_btc_1d.png" }
}
```

Drei Punkte, die daran nicht beliebig sind:

* **`evidence` ist ein Pflichtfeld, kein Kommentar.** Es entscheidet, wie das
  UI die Zahl darstellt (siehe §6). Ein Lauf ohne dieses Feld wird nicht
  gerendert, statt als vertrauenswürdig durchzugehen.
* **`git_commit` gehört dazu.** Die ROADMAP korrigiert an mehreren Stellen
  frühere Zahlen, weil sich der Code darunter geändert hatte. Eine Zahl ohne
  Codestand ist ein Screenshot.
* **Das Manifest ist eine Ausgabe, keine Datenbank.** Es wird geschrieben und
  nie wieder verändert. Aggregiert wird beim Lesen. Der Grund ist derselbe wie
  beim Versuchszähler der Research-Registry (ADR-032): ein abgeleiteter Wert,
  den niemand pflegen muss, kann nicht falsch gepflegt werden.

Aufwand: klein — jeder Befehl hat die Werte am Ende ohnehin in der Hand. Nutzen:
ohne das gibt es kein UI außerhalb des Paper-Kontos.

---

## 5. Die Screens

Neun Screens, abgeleitet aus den Daten, die tatsächlich existieren. Jeder hat
eine Quelle und eine Entwurfsregel.

### 5.1 Cockpit — der eine Screen, den man täglich ansieht

**Quelle:** `data/paper/*.json` (`PaperState`), `qt.data.integrity.check`, neueste
Manifeste.

Aufbau, in dieser Reihenfolge — sie ist von `qt.report.daily` übernommen, wo der
Kill-Switch-Status bewusst **oben** steht, weil es die eine Zeile ist, die man
beim Überfliegen nicht übersehen darf:

1. **Kill-Switch.** Läuft / ausgelöst. Bei Auslösung: die letzten drei Gründe,
   groß, und der Befehl zum Zurücksetzen als kopierbarer Text — nicht als Knopf.
2. **Frische.** „Letzter verarbeiteter Bar: vor 6 Stunden." Ab einer
   Timeframe-abhängigen Schwelle (z. B. 3× Bar-Dauer) wird die Kachel laut.
   *Das ist die wichtigste einzelne Neuerung gegenüber der Textausgabe:* ein
   Paper-Konto, das über Wochen laufen soll, scheitert nicht spektakulär, es
   hört einfach auf getickt zu werden. Ein Kontostand ohne Alter sieht dabei
   jeden Tag gleich gesund aus.
3. **Konto.** Eigenkapital, Höchststand, Abstand zur Kill-Switch-Schwelle als
   Balken. Cash, Positionen, Fills, Gebühren, Umsatz.
4. **Datenbestand.** Je (Symbol, Timeframe) eine Zeile: Abdeckung, letzter Bar.
   Rot, wenn `IntegrityReport.ok` falsch ist.

**Regel:** Auf diesem Screen steht keine Backtest-Zahl. Er beantwortet „läuft
es und ist es gesund", nicht „ist es gut".

### 5.2 Daten — die Abdeckungsleiste

**Quelle:** `qt.data.store.available()` + `qt.data.integrity.check()`,
`qt.data.trades` für den Order-Flow-Bestand.

Je (Symbol, Timeframe) eine **Zeitleiste**: eine waagerechte Leiste über die
Gesamtspanne, vorhandene Bars gefüllt, Lücken ausgespart, Länge der Lücke im
Tooltip.

**Warum das der höchste Einzelnutzen im ganzen Plan ist:** Der Order-Flow-Bestand
sieht als Zusammenfassung gesund aus — 2,4 Mio Trades über 361 Tage Spanne — und
ist es nicht: in der Mitte liegt ein **301-Tage-Loch**, real sind zwei Blöcke von
33 und 26 Tagen. Diese Diskrepanz ist in einer Zahlenzeile unsichtbar und in
einer Leiste auf den ersten Blick da. Eine Kennzahl, die „99,97% Abdeckung"
sagt, verbirgt genau den Fall, der ein Train/Test/Embargo-Fenster unmöglich
macht.

Darunter die Tabelle aus `qt data report`, unverändert: Bars, Zeitraum,
Abdeckung, Duplikate, Monotonie-Verstöße, OHLC-Plausibilität.

### 5.3 Strategie (In-Sample) — der Screen mit der Warnbanderole

**Quelle:** Manifest `kind: backtest`, `evidence: in_sample` + Tearsheet-PNG.

Inhalt: das bestehende Drei-Panel-Tearsheet, die Kennzahlentabelle, der
Buy-&-Hold-Vergleich.

**Regel — und das ist die wichtigste Entwurfsregel des ganzen UIs:** Alles mit
`evidence: in_sample` bekommt eine permanente, nicht wegklickbare Kennzeichnung
(getönter Hintergrund, Banderole „In-Sample — nicht belastbar", gedämpfte
Farben) und daneben einen Verweis auf den Walk-Forward desselben Setups, sofern
ein Manifest dafür existiert. Existiert keiner, steht dort der Befehl, der ihn
erzeugt.

Der Grund steht im README: bei `trend` waren die In-Sample-Zahlen „nicht zu
pessimistisch, sondern zu optimistisch". Ein UI, das beide Zahlenarten gleich
hübsch darstellt, macht diesen Fehler wieder — nur schneller und in Farbe.

### 5.4 Walk-Forward — die Streuung, nicht der Gesamtwert

**Quelle:** Manifest `kind: walkforward`.

* Die Fenstertabelle (`#`, Test-Beginn, Rendite, Sharpe, MaxDD, Trades).
* **Ein Balken je Fenster, Sharpe, um die Nulllinie** — grün über, rot unter.
  Groß, oben, vor den Gesamtzahlen.
* Die Kennzahl „positiver Sharpe in X von N Fenstern" als Überschrift.
* Die verkettete OOS-Kurve darunter, nicht darüber.

**Regel:** Der Gesamtsharpe steht bewusst nicht an erster Stelle. „Aussage-
kräftiger als der Gesamtsharpe ist die Streuung über die Fenster — eine
Strategie, die in einem von siebzehn Fenstern alles verdient, ist eine
Zufallsstichprobe und keine Kante." Die Leserichtung des Screens ist die
Übersetzung dieses Satzes.

### 5.5 Gate — Kandidat gegen alle Baselines

**Quelle:** Manifest `kind: gate`.

* Die Vergleichstabelle wie in ADR-045: je Allokator Sharpe, Rendite, MaxDD,
  Umsatz, Trades. Umsatz und `time_in_market` stehen mit drin, weil zwei
  Allokatoren mit gleichem Sharpe nicht gleich gut sind (ADR-009, ADR-016).
* **Die Gewinn-Matrix:** je Baseline „gewonnen in 23 von 29 Fenstern", als
  Balken. Das ist die Darstellung, die den Teilerfolg sichtbar macht, ohne ihn
  zum Bestehen umzudeuten.
* Das Urteil im Klartext, samt Namen der Baseline, an der er gescheitert ist.
* **Die Telemetriezeile, gleichrangig neben der Tabelle:** Aufrufe, aus Cache,
  Rückfälle (%), bewusste Ausstiege, halluzinierte Labels. Eine Rückfallquote
  über null färbt den ganzen Screen — bei 100% wäre die Tabelle eine Messung
  von Equal-Weight unter falschem Namen.

### 5.6 Allokator-Verhalten — der Screen, den der Gate-Lauf verlangt hat

**Quelle:** die je Fenster gespeicherten Vorschläge (Manifest + `.llm_cache`).

Der neue Screen, für den es heute kein Kommando gibt:

* **Gewichte über Zeit**, gestapelte Fläche je Strategie-Label.
* **Umsatz je Allokationsschritt** — Median als Linie. (Der Befund: Median 40%
  des Buches.)
* **Vollständige Umkehrungen** als Marker auf der Zeitachse. (28 Stück.)
* Die **bewussten Ausstiege** (Vorschlag: alles flach) hervorgehoben — drei im
  Gate-Lauf, und sie sind das Interessanteste am Modellverhalten.
* Nebeneinander: dasselbe für `best_single(720)`, den langsamsten Gegner im
  Feld — der gewonnen hat.

**Warum eigener Screen:** Die drei Schritte der ROADMAP beginnen mit „langsamer
schalten". Diese Hypothese kam aus Handarbeit an 145 Cache-Einträgen. Ein
Screen macht sie beim nächsten Lauf zur Beobachtung statt zur Ausgrabung.

### 5.7 Simulation — eine Verteilung, nie ein Pfad

**Quelle:** Manifest `kind: sim`.

* **Fächerdiagramm:** Quantilbänder (5/25/50/75/95) des Ensembles über den
  Horizont. **Nie ein einzelner Pfad, auch nicht „beispielhaft".**
* Die Exposure-Tabelle: Exposure, Median-Rendite, CVaR, P(Verlust), zulässig.
  Unzulässige Zeilen ausgegraut, die gewählte markiert.
* Das Ergebnis im Klartext, inklusive „kein Trade" als vollwertiges Ergebnis —
  es ist auf den letzten 2000 BTC-Bars das tatsächliche.
* **Generator-Vergleich:** dieselbe Frage über alle vier Generatoren
  (`iid_bootstrap` 80% … `garch` 35%). Nicht um zu zeigen, welcher recht hat,
  sondern dass die Wahl folgenreich ist.
* Bei `--scenarios`: die LLM-Priors als Eigenschaft/Richtung/Stärke, der
  gemessene Tilt, und der Hinweis, wenn auf das ungewichtete Ensemble
  zurückgefallen wurde.

### 5.8 Research — der Trichter und ein Budget, das sich verbraucht

**Quelle:** `data/research/registry.duckdb`.

* **Der Trichter** als Stufenbild: erzeugt → Sandbox → Kritik → Sanity →
  Walk-Forward → DSR bestanden. Mit den Kosten je Stufe beschriftet, weil die
  Reihenfolge nach Kosten sortiert ist.
* **Der Versuchszähler ganz oben, als Verbrauchsanzeige.** Er ist kein Zähler,
  er ist ein Budget: „Jeder weitere Lauf verschärft die DSR-Schwelle
  dauerhaft." Ein UI, das ihn wie eine Statistik neben andere stellt, verfehlt
  seine Bedeutung.
* Kandidatentabelle, filterbar: Status, DSR, Sharpe, Modell, Effort, Datum.
* **Kandidaten-Detail = der Audit-Pfad aus `--show`**, unverändert: Sandbox-
  Urteil samt Gründen, Kritik samt Begründung, Screening, DSR gegen wieviele
  Versuche, die Begründung des Generators, der Quelltext.
* Beim Quelltext: „Kopieren"-Knopf **und** der Hinweistext, dass der Kandidat
  im Sandbox-Dialekt weiterläuft und wer ihn umschreibt einen anderen
  Kandidaten testet als den, der die DSR bestanden hat. Kein Knopf, der
  schreibt.

### 5.9 LLM-Kosten und Cache-Vorschau — der Screen, der sich selbst bezahlt

**Quelle:** `.llm_cache/**`, Manifeste.

* Bestand: Einträge je Modell/Anbieter/Effort/Schema-Version.
* Je Lauf: Aufrufe, Treffer, Latenzverteilung (Anthropic Sekunden, NIM 90–155 s
  — der Unterschied ist Planungsgröße, nicht Fußnote).
* **Cache-Vorschau:** Eingabemaske für eine geplante Konfiguration (Strategien,
  Symbole, TF, Fenster, `allocate_every`, Anbieter, Modell, Effort) → das UI
  baut die Briefings, hasht die Keys und sagt: „von 145 erwarteten Aufrufen
  liegen 0 im Cache, geschätzt 3,8 h."

  Der konkrete Anlass steht in der ROADMAP: `--allocate-every 384` statt 96
  erzeugt andere Briefings, also andere Cache-Keys, also wieder ~4 Stunden. Das
  ist heute eine Warnung im Fließtext. Als Screen ist es eine Zahl vor dem
  Start.

  **Achtung — und das ist die Grenze dieses Screens:** die Vorschau muss
  dieselbe Briefing-Funktion aufrufen wie der echte Lauf. Wenn sie den Key
  nachbaut, ist sie beim nächsten Schema-Bump falsch, und zwar leise. Sie darf
  nur aus `qt.llm.cache.LLMCache.key` gefüttert werden, nie aus einer Kopie
  dieser Logik.

---

## 6. Querregeln

Diese gelten auf jedem Screen. Sie sind der eigentliche Inhalt des Plans — die
Screens sind austauschbar, die Regeln nicht.

**R1 — Jede Zahl trägt ihre Herkunft.** Unter jedem Block eine Zeile: Befehl,
Commit, Datenspanne, Fenstergeometrie, Zeitpunkt. Wer sie nicht liefern kann,
wird nicht gerendert.

**R2 — Evidenzgrad ist eine Farbe.** Vier Stufen, durchgängig:

| `evidence` | Darstellung | Bedeutung |
|---|---|---|
| `oos` | normal, volle Farbe | Walk-Forward, Gate — belastbar |
| `paper` | normal, mit Alter | forward, aber kurz |
| `simulated` | gemustert | Modellannahme, keine Beobachtung |
| `in_sample` | getönt + Banderole | optimistisch, nicht belastbar |

**R3 — Alter wird nie verschwiegen.** Jeder Zustand zeigt sein `updated_at`
relativ. Ein Kontostand von vorgestern wird als „vorgestern" gezeigt, nicht als
Kontostand.

**R4 — Leere Zustände nennen den Befehl.** Genau wie die CLI („Store ist leer.
Erst ziehen: `qt data pull`"). Ein leerer Screen ohne nächsten Schritt ist ein
Fehler im UI, nicht im Datenbestand.

**R5 — Keine Schreibroute existiert.** Nicht deaktiviert, nicht geschützt —
nicht vorhanden. Befehle erscheinen im UI ausschließlich als kopierbarer Text.

**R6 — Nichts wird gerundet, was gerundet schon woanders steht.** Die Zahlen
kommen aus `Metrics.as_dict()` und `IntegrityReport`, in derselben Formatierung
wie im Terminal. Zwei Darstellungen derselben Größe, die sich in der letzten
Stelle unterscheiden, kosten mehr Vertrauen als sie an Schönheit bringen.

**R7 — Das UI rechnet nichts nach.** Es liest Manifeste und Zustandsdateien.
Sobald es Kennzahlen selbst berechnet, gibt es zwei Implementierungen derselben
Metrik, und die driften — dasselbe Argument, mit dem das System einen
Backtest-Pfad statt zweier hat (ADR-001). Einzige Ausnahme: die Cache-Vorschau,
und die ruft die Originalfunktion auf.

---

## 7. Technik

**Vorschlag:**

| Schicht | Wahl | Warum |
|---|---|---|
| Lesen | `qt.report.model` — reine Funktionen, Datei → Datenklasse | testbar ohne HTTP, von beiden Formen geteilt |
| Statisch | Jinja2 → eine `.html`, PNGs als `data:`-URI | eine Datei, kein Netz, kein Server |
| Interaktiv | FastAPI + Uvicorn, nur GET, `127.0.0.1` | schon `pydantic` im Haus; kein zweites Ökosystem |
| Charts | weiter matplotlib → PNG | genau eine Chart-Engine im Projekt |
| Frontend | HTML + CSS + etwas Vanilla-JS, kein Build | ein Python-Repo bleibt ein Python-Repo |
| Abhängigkeit | `[project.optional-dependencies] ui = ["fastapi", "uvicorn", "jinja2"]` | wie `nim` und `timesfm` |

**Was bewusst nicht:**

* **Kein React/npm.** Ein Node-Toolchain neben `uv` ist eine zweite
  Build-Welt für ein Leseprogramm. Die Screens sind Tabellen, Balken und Bilder.
* **Kein Plotly/Bokeh.** Sie brächten Zoom und Hover — und eine zweite
  Chart-Engine, deren Ausgabe von den PNGs abweicht, die in `reports/` liegen.
  **Der Preis ist echt:** die Fensterbalken und die Abdeckungsleiste hätten mit
  einer JS-Bibliothek Tooltips. Der Vorschlag ist, das zunächst mit `<title>`
  und CSS zu lösen und die Entscheidung erst zu revidieren, wenn ein konkreter
  Screen daran scheitert.
* **Kein Streamlit/Gradio.** Sie sind Server-gebunden und damit genau die Form,
  die dieser Container nicht hält. Und sie machen Schreibknöpfe zu leicht.
* **Kein Grafana/Prometheus.** Setzt einen laufenden Scraper voraus. Hier gibt
  es keine Zeitreihe von Metriken, sondern eine Handvoll teurer Läufe.

---

## 8. Phasen

Dasselbe Prinzip wie die Roadmap des Systems: jede Phase endet mit etwas
Sichtbarem und hat **einen Befehl**. Man kann nach jeder aufhören.

### UI-Phase 1 — Cockpit als Datei
`qt report html --paper --strategy macross --symbols BTC/USD --tf 1d`

Screens 5.1 und 5.2. Braucht **kein** Manifest — `PaperState` und
`IntegrityReport` liegen schon vor. Ergebnis: eine HTML-Datei, die man täglich
auf dem Handy ansieht, und die Abdeckungsleiste, die das 301-Tage-Loch zeigt.

*Das ist die Phase mit dem besten Verhältnis von Aufwand zu Nutzen, und sie ist
von allem anderen unabhängig.*

### UI-Phase 2 — Manifeste + Ergebnis-Screens
`qt report html --runs` (alle Läufe aus `reports/runs/`)

`qt.report.manifest` bauen, die fünf Befehle schreiben lassen, Screens 5.3–5.5
und 5.7. Ab hier hat jede Zahl im System einen Ort, an dem sie den Terminalpuffer
überlebt.

### UI-Phase 3 — der interaktive Server
`qt ui --port 8765`

Screens 5.6, 5.8, 5.9. Erst hier, weil Filtern und Blättern erst lohnt, wenn es
mehr als einen Lauf gibt.

### UI-Phase 4 — Divergenz
Der Screen, den die ROADMAP eigentlich verlangt: **Paper-Konto gegen den
Backtest derselben Strategie im selben Zeitraum**, eine Kurve über der anderen,
Abweichung darunter. „Über Wochen laufen lassen und Live-vs-Backtest-Divergenz
messen" — das ist die Messung.

Bewusst zuletzt: sie braucht Wochen Paper-Historie, die es heute nicht gibt
(`data/paper/` ist leer). Vorher wäre der Screen leer und die Arbeit spekulativ.

---

## 9. Risiken

**Das UI macht schlechte Zahlen hübsch.** Die größte Gefahr. Vier Allokatoren,
die alle dreistellig verlieren, sehen als Dashboard nach Betrieb aus. Gegenmittel
sind R1/R2 und die Leserichtung der Screens (Streuung vor Gesamtwert, Urteil vor
Tabelle) — aber es bleibt eine Gefahr, die kein Feature abstellt.

**Manifeste driften von der Terminalausgabe ab.** Wenn ein Befehl seine Zahlen
zweimal formatiert — einmal für `typer.echo`, einmal fürs Manifest — driften die
zwei. Gegenmittel: das Manifest wird aus denselben Objekten geschrieben
(`Metrics`, `GateResult`, `IntegrityReport`), nicht aus geparster Ausgabe, und
ein Test hält je eine Kennzahl gegen die gedruckte Zeile.

**Der Server wird zur Steuerung.** „Nur ein kleiner Knopf für `paper run`" ist
der erste Schritt, und danach ist das UI ein Betriebssystem mit
Prozessverwaltung. Gegenmittel: R5, und dass es keine Schreibroute *gibt*.

**Aufwand für einen Nutzer.** Das System hat einen Benutzer. Ein UI, das mehr
Pflege kostet als es an Blicken spart, ist ein Verlust. Deshalb Phase 1 zuerst
und einzeln bewertbar: wenn die tägliche HTML-Datei nach zwei Wochen nicht
angesehen wird, ist das die Antwort auf die Frage, ob Phase 2 gebaut wird.

---

## 10. Offene Fragen

*Beantwortet und damit Voraussetzung, nicht mehr Frage:* Das UI wird auf dem
Handy für den täglichen Blick benutzt. Deshalb ist die statische Datei die
primäre Form und `qt ui` die spätere Ergänzung. Sollte später doch eine
Maschine dazukommen, die dauerhaft läuft — dieselbe, die laut ROADMAP das
Paper-Konto tragen müsste —, verschiebt sich die Gewichtung zugunsten des
Servers, aber nicht die Screens und nicht die Querregeln.

1. **Ein Konto oder mehrere?** `state_path` erlaubt beliebig viele Paper-Konten
   (je Strategie/Symbole/TF eins). Das Cockpit ist für **eines** entworfen. Bei
   mehreren braucht es eine Auswahlebene davor — machbar, aber ein anderer
   Screen.
2. **Deutsch oder Englisch?** Der Plan folgt Repo und Code (Deutsch). Falls das
   UI je geteilt wird, ist das eine frühe Entscheidung, keine späte.
3. **Reicht der PNG-Weg?** Siehe §7: Tooltips fehlen. Der Vorschlag ist, es
   auszuprobieren und erst bei konkretem Scheitern eine JS-Chart-Bibliothek zu
   holen.

---

## 11. ADR-Kandidaten

Wird umgesetzt, gehören diese Entscheidungen ins Log (nächste freie Nummer:
ADR-046):

* **Das UI ist read-only, baulich.** Keine Schreibroute, kein Start von Läufen,
  kein Kill-Switch-Reset, keine Promotion.
* **Zwei Auslieferungsformen über einer Lesecodebasis** — die statische Datei
  ist die primäre, weil der Container nicht überlebt.
* **Run-Manifeste als unveränderliche Ausgabe je Lauf**, mit `evidence` und
  `git_commit` als Pflichtfeldern.
* **Evidenzgrad ist eine Darstellungsregel, keine Beschriftung** — In-Sample
  bleibt sichtbar In-Sample, dauerhaft und nicht wegklickbar.
