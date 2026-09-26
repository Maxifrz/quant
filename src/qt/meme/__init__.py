"""Memecoin-Papiertest (ADR-081): eine vorab registrierte Frage an pump.fun.

Die Frage: Verdient eine einfache, systematische Regel beim Handel neuer
pump.fun-Tokens Geld, wenn man **keinen Geschwindigkeitsvorteil** hat --
also nicht im selben Block kauft wie der Ersteller, sondern eine Minute
spaeter, mit realistischen Kosten?

Aufbau:

* `registrierung` -- jede Zahl des Tests, festgelegt bevor ein einziger
  Testtag existierte. Wer eine davon aendert, hinterlaesst einen Diff.
* `pumpfun` -- Ereignisse aus Transaktionen lesen, die Bonding-Curve exakt
  nachrechnen.
* `rpc` -- oeffentliche Solana-RPC mit Drosselung und Rueckfall.
* `sammeln` -- je UTC-Tag eine Zufallsstichprobe aller Starts, samt der
  toten, und die Kurvenstaende zu den registrierten Zeitpunkten.
* `papier` -- was die Regeln R0 und R1 mit diesen Staenden verdient haetten.
* `auswertung` -- Vollstaendigkeit jederzeit, Ergebnis erst nach dem letzten
  Testtag.

**Dieser Code sendet keine Transaktion und haelt keinen Schluessel.** Er
liest oeffentliche Daten und rechnet.
"""
