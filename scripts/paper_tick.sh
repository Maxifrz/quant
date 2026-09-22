#!/usr/bin/env bash
# Ein Paper-Tick, containerfest: ziehen, ticken, Zustand sichern.
#
# Git steht bewusst hier und nicht in der CLI. Eine Anwendung, die selbst
# committet, vermischt zwei Verantwortungen und ist schlechter testbar --
# `qt paper run` soll ein Konto fortschreiben, nicht ein Repository pflegen.
#
# Der Tick ist sicher wiederholbar: ein zweiter Aufruf ohne neue Bars aendert
# nichts. Das Skript darf also oefter laufen als noetig.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# Der Zweig, auf dem dieser Checkout steht -- **kein fester Name**. Bis
# ADR-059 stand hier `claude/llm-quant-algo-planning-f1ohgo`, und dessen Pull
# Request ist laengst zusammengefuehrt. Jeder Tick schrieb den Kontostand
# damit auf einen Zweig, den niemand mehr zusammenfuehrt: `git push -u` legt
# ihn wortlos neu an, der Zustand landet daneben statt in `main`, und nichts
# davon sieht nach einem Fehler aus.
#
# Zurueckschreiben, wo gelesen wurde, ist die einzige Regel, die sich selbst
# konsistent haelt: der Zustand, den der naechste Tick vorfindet, ist der,
# den dieser hinterlassen hat.
ZWEIG="$(git rev-parse --abbrev-ref HEAD)"
if [ "$ZWEIG" = "HEAD" ]; then
  echo "!! Loser HEAD -- ohne Zweig gibt es keinen Ort, an dem der Kontostand"
  echo "   ueberlebt. Erst auschecken, dann ticken."
  exit 1
fi

# **Und wenn dieser Zweig laengst zusammengefuehrt ist?** Dann ist "dorthin
# zurueckschreiben, wo gelesen wurde" nicht mehr dieselbe Regel, sondern eine
# Sackgasse: der Zustand landet auf einem Zweig, den niemand mehr
# zusammenfuehrt, waehrend die naechste frische Sitzung den Standardzweig
# auscheckt und einen Kontostand von vor der Zusammenfuehrung vorfindet.
#
# Das ist ADR-059 zum dritten Mal, mit einem neuen Grund: dort war der
# Zweigname fest verdrahtet, hier ist er richtig und trotzdem tot. Beide Male
# sieht das Ergebnis nicht nach einem Fehler aus -- der Push gelingt, die
# Meldung sagt "gesichert", und der Zustand ist weg.
#
# Entschieden wird nach dem einzigen Kriterium, das zaehlt: **wo liest der
# naechste Tick?** Eine frische Sitzung bekommt den Standardzweig. Enthaelt
# der bereits alles, was auf diesem Zweig liegt, gehoert der Kontostand
# dorthin.
git fetch -q origin main 2>/dev/null || true
STANDARD="main"
ZIEL="$ZWEIG"
if git rev-parse --verify -q origin/"$STANDARD" >/dev/null; then
  if [ "$ZWEIG" != "$STANDARD" ] && \
     git merge-base --is-ancestor HEAD origin/"$STANDARD" 2>/dev/null; then
    if git merge-base --is-ancestor origin/"$STANDARD" HEAD 2>/dev/null; then
      # Zweig und Standard stehen auf demselben Commit: der Zweig ist
      # erschoepft, der Zustand gehoert dorthin, wo er gefunden wird.
      ZIEL="$STANDARD"
      echo "Hinweis: '${ZWEIG}' ist zusammengefuehrt und deckungsgleich mit"
      echo "         '${STANDARD}'. Der Kontostand geht nach '${STANDARD}',"
      echo "         weil die naechste frische Sitzung dort liest."
    else
      echo "!! '${ZWEIG}' ist in '${STANDARD}' enthalten, aber aelter als"
      echo "   dieser. Dieser Checkout ist veraltet; der Kontostand wird"
      echo "   trotzdem gesichert, aber eine frische Sitzung sieht ihn nicht."
      echo "   Erst '${STANDARD}' auschecken, dann ticken."
    fi
  fi
fi
# **Der Session-Branch soll verfolgen, wohin der Zustand gegangen ist.**
# Geht der Kontostand nach `main`, steht der lokale Branch danach auf
# demselben Commit wie `origin/main` -- hat aber keinen Upstream. Ein
# Pruefhaken der Sitzung meldet dann "unpushed commit", und der Agent pusht
# den Branch, um ihn zufriedenzustellen: zwischen 2026-09-09 und 2026-09-21
# zehn `claude/lucid-meitner-*`-Branches, jeder exakt auf dem Tick-Commit des
# Tages, keiner mit eigenem Inhalt (ADR-079). Mit `origin/main` als Upstream
# ist der Branch "up to date", und es gibt nichts zu pushen.
verfolge_ziel() {
  if [ "$ZIEL" != "$ZWEIG" ]; then
    git fetch -q origin "$ZIEL" 2>/dev/null || true
    if git merge-base --is-ancestor HEAD "origin/${ZIEL}" 2>/dev/null; then
      git branch -q --set-upstream-to="origin/${ZIEL}" "$ZWEIG" 2>/dev/null \
        && echo "Branch '${ZWEIG}' verfolgt jetzt 'origin/${ZIEL}' -- nichts weiter zu pushen."
    fi
  fi
}

KONTEN=("BTC/USD" "ETH/USD")
gemeldet=0

for symbole in "${KONTEN[@]}"; do
  echo "=== macross ${symbole} 1d"
  uv run qt paper run --strategy macross --symbols "$symbole" --tf 1d
  code=$?
  # Exit 2 heisst Kill-Switch. Das ist meldepflichtig, aber kein Fehlschlag
  # des Skripts -- der Zustand ist gueltig und gehoert gesichert.
  if [ "$code" -eq 2 ]; then
    echo "!! Kill-Switch bei ${symbole} -- Zustand wird trotzdem gesichert"
    gemeldet=2
  elif [ "$code" -ne 0 ]; then
    echo "!! Tick fuer ${symbole} fehlgeschlagen (Code ${code})"
    gemeldet=1
  fi
done

git add -f data/paper 2>/dev/null
if git diff --cached --quiet; then
  echo "Kein neuer Kontostand -- nichts zu sichern."
  verfolge_ziel
  exit "$gemeldet"
fi

git commit -q -m "Paper-Konto: Zustand nach Tick $(date -u +%Y-%m-%dT%H:%MZ)

Automatischer Tick von scripts/paper_tick.sh. Der Kontostand ist das einzige
im Datenverzeichnis, das sich nicht rekonstruieren laesst.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0151sxRxJe3uZnwCgVP5PMWT"

gesichert=1
gesichert_auf=""
for warte in 2 4 8 16; do
  if git push origin "HEAD:${ZIEL}"; then
    gesichert=0
    gesichert_auf="$ZIEL"
    break
  fi
  sleep "$warte"
done

# Ein geschuetzter Standardzweig lehnt den Push ab. Dann ist der Zweig immer
# noch besser als nichts -- aber die Meldung muss sagen, dass der Zustand
# nicht dort liegt, wo die naechste Sitzung liest.
if [ "$gesichert" -ne 0 ] && [ "$ZIEL" != "$ZWEIG" ]; then
  echo "!! Push nach '${ZIEL}' gescheitert -- Rueckfall auf '${ZWEIG}'."
  echo "   Der Kontostand ueberlebt dort, aber eine frische Sitzung auf"
  echo "   '${ZIEL}' findet ihn nicht. Das ist ein Fall fuer einen Menschen."
  if git push -u origin "$ZWEIG"; then
    gesichert=0
    gesichert_auf="$ZWEIG"
  fi
fi

# Bis ADR-059 stand hier unbedingt "Kontostand gesichert." -- auch wenn alle
# vier Versuche gescheitert waren. Ein Commit ohne Push ueberlebt den
# Container nicht, und die Zeile behauptete das Gegenteil.
if [ "$gesichert" -ne 0 ]; then
  echo "!! Vier Push-Versuche auf ${ZIEL} gescheitert -- der Commit liegt nur"
  echo "   lokal und ist mit dem Container weg. Das ist der einzige Zustand"
  echo "   im Datenverzeichnis, der sich nicht rekonstruieren laesst."
  exit 1
fi
# `verfolge_ziel` setzt den Upstream nur, wenn der Commit nach dem Fetch
# wirklich in origin/ZIEL liegt. Nach dem Rueckfall auf den eigenen Zweig ist
# das nicht so -- dort bleibt der Upstream der eigene Zweig (`push -u`).
verfolge_ziel
# Bis ADR-079 stand hier "auf ${ZIEL}" -- auch nach dem Rueckfall, wenn der
# Stand gerade *nicht* dort lag. Dieselbe Sorte Erfolgsmeldung wie vor ADR-059.
echo "Kontostand auf ${gesichert_auf} gesichert."
exit "$gemeldet"
