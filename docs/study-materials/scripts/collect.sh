#!/usr/bin/env bash
# collect.sh — packs up one finished session so the participant can send it back.
#
#   ./collect.sh <participant-code>
#   ./collect.sh p04
#
# It gathers the two workspaces, the session logs, and the Claude Code
# transcripts into one zip on the Desktop. It does not touch the screen or audio
# recording, which the experimenter keeps from the call.
set -uo pipefail

CODE="${1:?usage: collect.sh <participant-code>, e.g. ./collect.sh p04}"
WORK="$HOME/codoc-study"
OUT="$HOME/Desktop/codoc-study-$CODE.zip"
TMP="$(mktemp -d)"
DEST="$TMP/codoc-study-$CODE"
mkdir -p "$DEST"

echo "Collecting session ${CODE}."

for w in scribe scribe-baseline tally tally-baseline; do
  if [ -d "$WORK/$w" ]; then
    # Skip the Python environments and the built site. They are large and we can
    # rebuild both from the source we are keeping.
    rsync -a --exclude '.venv' --exclude '_site' --exclude '__pycache__' \
             --exclude '*.egg-info' --exclude '.pytest_cache' \
             "$WORK/$w" "$DEST/" 2>/dev/null && echo "  workspace: $w"
  fi
done

if [ -d "$WORK/session-logs" ]; then
  rsync -a "$WORK/session-logs" "$DEST/" 2>/dev/null && echo "  session logs"
  n=$(ls "$WORK/session-logs"/interaction-*.jsonl 2>/dev/null | wc -l | tr -d ' ')
  echo "  interaction logs: $n"
  [ "$n" = "0" ] && echo "    WARNING: none found. Tell the experimenter before you close the call."
fi

mkdir -p "$DEST/claude-transcripts"
found=0
for d in "$HOME"/.claude/projects/*scribe* "$HOME"/.claude/projects/*tally*; do
  [ -d "$d" ] || continue
  rsync -a "$d" "$DEST/claude-transcripts/" 2>/dev/null && found=$((found+1))
done
echo "  Claude Code transcripts: $found"

{
  echo "participant: $CODE"
  echo "collected:   $(date -Iseconds)"
  echo "machine:     $(uname -srm)"
} > "$DEST/collection.meta"

rm -f "$OUT"
( cd "$TMP" && zip -qr "$OUT" "codoc-study-$CODE" )
rm -rf "$TMP"

echo
echo "Done. Send this file to the experimenter:"
echo "  $OUT"
echo "  size: $(du -h "$OUT" | cut -f1)"
echo
echo "Nothing in it holds your name, only the code $CODE."
