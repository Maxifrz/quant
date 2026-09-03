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

ZWEIG="claude/llm-quant-algo-planning-f1ohgo"
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

for warte in 2 4 8 16; do
  git push -u origin "$ZWEIG" && break
  sleep "$warte"
done
echo "Kontostand gesichert."
exit "$gemeldet"
