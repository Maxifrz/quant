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
  exit "$gemeldet"
fi

git commit -q -m "Paper-Konto: Zustand nach Tick $(date -u +%Y-%m-%dT%H:%MZ)

Automatischer Tick von scripts/paper_tick.sh. Der Kontostand ist das einzige
im Datenverzeichnis, das sich nicht rekonstruieren laesst.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Mv9TxGF52xLNLFA7qmQTmM"

gesichert=1
for warte in 2 4 8 16; do
  if git push -u origin "$ZWEIG"; then
    gesichert=0
    break
  fi
  sleep "$warte"
done

# Bis ADR-059 stand hier unbedingt "Kontostand gesichert." -- auch wenn alle
# vier Versuche gescheitert waren. Ein Commit ohne Push ueberlebt den
# Container nicht, und die Zeile behauptete das Gegenteil.
if [ "$gesichert" -ne 0 ]; then
  echo "!! Vier Push-Versuche auf ${ZWEIG} gescheitert -- der Commit liegt nur"
  echo "   lokal und ist mit dem Container weg. Das ist der einzige Zustand"
  echo "   im Datenverzeichnis, der sich nicht rekonstruieren laesst."
  exit 1
fi
echo "Kontostand auf ${ZWEIG} gesichert."
exit "$gemeldet"
