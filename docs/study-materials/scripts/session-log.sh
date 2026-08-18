#!/usr/bin/env bash
# session-log.sh — the fallback recorder. You almost certainly do not need this.
#
# The study logger extension records the project every 20 seconds on its own, in
# both conditions, from the moment VS Code opens. Nobody has to start anything.
# Check it with "Study logger: show what is being recorded" (Cmd+Shift+P), which
# prints how many snapshots it has taken.
#
# Run this ONLY if that says snapshots are off or failing:
#
#   ./session-log.sh <workspace> <label> [output-dir]
#   ./session-log.sh ~/codoc-study/scribe p04-scribe
#
# It does the same two things the extension does. It commits the working tree to
# a shadow ref, refs/study/<label>, using git plumbing, so HEAD, the branch, the
# index and the working tree are all left exactly as they were — an earlier
# version of this script ran `git checkout -b`, which moved the participant onto a
# study branch and made their own `git log` part of the instrument. And it copies
# the description and codoc's control files.
#
# The virtual environment, .claude-study/api-key and .env are excluded. The keys
# matter: collect.sh leaves both out of the zip by name, but a commit lives inside
# .git and .git travels with the workspace, so snapshotting them would mail the
# keys home inside the history while the exclusion looked like it was working.
#
# Stop it with the line the script prints when it starts. It does not record the
# screen or the audio. Start those yourself.
set -u

WS="${1:?usage: session-log.sh <workspace> <label> [output-dir]}"
LABEL="${2:?usage: session-log.sh <workspace> <label> [output-dir]}"
BASE="${3:-$HOME/codoc-study/session-logs}"

[ -d "$WS" ] || { echo "no such workspace: $WS"; exit 1; }
WS="$(cd "$WS" && pwd)"

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$BASE/$LABEL-$STAMP"
mkdir -p "$LOG/codoc-states"
REF="refs/study/$LABEL"
export GIT_INDEX_FILE="$LOG/snapshot.index"
export GIT_AUTHOR_NAME="codoc study" GIT_AUTHOR_EMAIL="study@codoc.local"
export GIT_COMMITTER_NAME="codoc study" GIT_COMMITTER_EMAIL="study@codoc.local"

# The long :(exclude) form, not :! — git reads what follows :! as more pathspec
# magic, so `:!_site` dies and takes the whole snapshot with it.
SKIP=()
for e in .venv node_modules _site __pycache__ .pytest_cache '*.egg-info' \
         .claude-study .env api-key api-key.sh; do
  SKIP+=( ":(exclude)$e" ":(exclude)$e/**" )
done

{
  echo "workspace: $WS"
  echo "label:     $LABEL"
  echo "ref:       $REF"
  echo "started:   $(date -Iseconds)"
  echo "head:      $(git -C "$WS" rev-parse HEAD 2>/dev/null || echo none)"
} > "$LOG/snapshot.meta"

snapshot() {
  git -C "$WS" add -A -- . "${SKIP[@]}" >/dev/null 2>&1 || return 1
  local tree parent commit
  tree="$(git -C "$WS" write-tree 2>/dev/null)" || return 1
  parent="$(git -C "$WS" rev-parse --verify --quiet "$REF" 2>/dev/null \
            || git -C "$WS" rev-parse --verify --quiet HEAD 2>/dev/null)"
  if [ -n "$parent" ]; then
    commit="$(git -C "$WS" commit-tree "$tree" -p "$parent" -m "snapshot $(date +%H%M%S)" 2>/dev/null)"
  else
    commit="$(git -C "$WS" commit-tree "$tree" -m "snapshot $(date +%H%M%S)" 2>/dev/null)"
  fi
  [ -n "$commit" ] && git -C "$WS" update-ref "$REF" "$commit"
}

snapshot
(
  while true; do
    sleep 20
    TS="$(date +%H%M%S)"
    snapshot
    if [ -d "$WS/.codoc" ]; then
      D="$LOG/codoc-states/$TS"; mkdir -p "$D"
      for f in tree.codoc tree.doc.json tree.bindings.json status.json activity.json \
               drift.json edits.json inbox.json ask.json realize.md realize.json realized.jsonl; do
        [ -f "$WS/.codoc/$f" ] && cp "$WS/.codoc/$f" "$D/" 2>/dev/null
      done
      [ -f "$WS/CLAUDE.md" ] && cp "$WS/CLAUDE.md" "$D/" 2>/dev/null
      rmdir "$D" 2>/dev/null
    fi
  done
# Detach the loop from this terminal, so it never prints over the participant's
# work and so piping this script's output does not hang waiting for it.
) >/dev/null 2>&1 &
echo $! > "$LOG/logger.pid"

cat <<EOF

Logging to: $LOG
Snapshots:  $REF  (your own branch is untouched)

Stop it with:
  kill \$(cat "$LOG/logger.pid")

At the end of the session, run collect.sh — it packs the transcripts with
everything else. They are NOT in ~/.claude/projects: each workspace runs the
assistant under its own config directory, which is what keeps the study off the
participant's account, so they are here:
  $WS/.claude-study/projects/
EOF
