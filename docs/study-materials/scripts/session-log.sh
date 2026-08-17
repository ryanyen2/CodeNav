#!/usr/bin/env bash
# session-log.sh — records one study session.
#
#   ./session-log.sh <workspace> <label> [output-dir]
#   ./session-log.sh ~/codoc-study/scribe p04-codoc
#
# Every 20 seconds it commits the whole workspace to a shadow git branch and
# copies each codoc control file into a timestamped folder. The participant's own
# git state is left alone, because every commit is made on the shadow branch.
#
# Stop it with the line the script prints when it starts.
#
# It does not record the screen or the audio. Start those yourself.
set -u

WS="${1:?usage: session-log.sh <workspace> <label> [output-dir]}"
LABEL="${2:?usage: session-log.sh <workspace> <label> [output-dir]}"
BASE="${3:-$HOME/codoc-study/session-logs}"

[ -d "$WS" ] || { echo "no such workspace: $WS"; exit 1; }
WS="$(cd "$WS" && pwd)"

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$BASE/$LABEL-$STAMP"
mkdir -p "$LOG/codoc-states"

{
  echo "workspace: $WS"
  echo "label:     $LABEL"
  echo "started:   $(date -Iseconds)"
  echo "head:      $(git -C "$WS" rev-parse HEAD 2>/dev/null || echo none)"
} > "$LOG/session.meta"

BRANCH="study/$LABEL-$STAMP"
git -C "$WS" checkout -q -b "$BRANCH" 2>/dev/null || true
git -C "$WS" add -A >/dev/null 2>&1
git -C "$WS" commit -q -m "session start: $LABEL" --allow-empty >/dev/null 2>&1
git -C "$WS" rev-parse HEAD > "$LOG/start-commit.txt" 2>/dev/null

(
  while true; do
    TS="$(date +%H%M%S)"
    git -C "$WS" add -A >/dev/null 2>&1
    git -C "$WS" commit -q -m "snapshot $TS" --allow-empty >/dev/null 2>&1
    if [ -d "$WS/.codoc" ]; then
      D="$LOG/codoc-states/$TS"; mkdir -p "$D"
      for f in tree.codoc tree.doc.json tree.bindings.json status.json activity.json \
               drift.json edits.json inbox.json realize.md realize.json realized.jsonl; do
        [ -f "$WS/.codoc/$f" ] && cp "$WS/.codoc/$f" "$D/" 2>/dev/null
      done
    fi
    sleep 20
  done
# Detach the loop from this terminal, so it never prints over the participant's
# work and so piping this script's output does not hang waiting for it.
) >/dev/null 2>&1 &
echo $! > "$LOG/logger.pid"

cat <<EOF

Logging to: $LOG
Git branch: $BRANCH

Stop it with:
  kill \$(cat "$LOG/logger.pid")

At the end of the session, run collect.sh — it packs the transcripts with
everything else. They are NOT in ~/.claude/projects: each workspace runs the
assistant under its own config directory, which is what keeps the study off the
participant's account, so they are here:
  $WS/.claude-study/projects/
EOF
