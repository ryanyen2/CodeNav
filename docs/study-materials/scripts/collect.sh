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

# Named for the project alone; which arm each one was is in its own
# .vscode/settings.json, which travels with it.
for w in scribe tally; do
  if [ -d "$WORK/$w" ]; then
    # Skip the Python environments and the built site. They are large and we can
    # rebuild both from the source we are keeping.
    #
    # And skip the two keys, which live inside the workspace: the assistant's in
    # .claude-study/api-key and codoc's in .env. This zip is mailed, uploaded and
    # then archived with the study data, and a key that travels that way is a key
    # that has to be rotated. Nothing in the analysis needs them.
    rsync -a --exclude '.venv' --exclude '_site' --exclude '__pycache__' \
             --exclude '*.egg-info' --exclude '.pytest_cache' \
             --exclude 'api-key' --exclude 'api-key.sh' --exclude '.env' \
             "$WORK/$w" "$DEST/" 2>/dev/null && echo "  workspace: $w"
  fi
done

if [ -d "$WORK/session-logs" ]; then
  # The snapshots and this participant's interaction logs, and nothing else.
  #
  # An interaction log is named after the workspace it came from, and the logger
  # used to write one for EVERY project opened in VS Code, since it is installed
  # globally and days before the session. Sweeping the folder wholesale therefore
  # mailed the researcher a list of file paths from the participant's own repos,
  # which their consent does not cover. The logger no longer writes those, and
  # this will not send one it finds from before that fix.
  #
  # The mirror's own state is never collected either. It is bookkeeping, a byte
  # offset and a sequence number, wrapped around a Firebase refresh token, and that
  # token holds the participant's `mirror` device slot. The rules let a slot
  # holder read `secrets`, so the token is a second way to reach the two study
  # keys that api-key and .env are already excluded to keep out of this zip.
  rsync -a --exclude 'interaction-*.jsonl' --exclude '*.mirror.json' \
           --exclude 'mirror-identity.json' \
           "$WORK/session-logs" "$DEST/" 2>/dev/null && echo "  session logs"
  n=0; skipped=0
  for f in "$WORK/session-logs"/interaction-*.jsonl; do
    [ -f "$f" ] || continue
    # Keep it only if its own records carry this participant's code. The name is
    # not evidence: a participant may well have a project of their own called
    # scribe, and the code inside the file is the only thing that decides.
    if head -c 200000 "$f" | grep -q "\"p\":\"$CODE\""; then
      rsync -a "$f" "$DEST/session-logs/" 2>/dev/null && n=$((n + 1))
    else
      skipped=$((skipped + 1))
    fi
  done
  echo "  interaction logs: $n"
  [ "$skipped" != "0" ] && echo "    ($skipped log(s) from other projects left on your machine)"
  [ "$n" = "0" ] && echo "    WARNING: none found. Tell the experimenter before you close the call."
fi

# The transcripts. Each workspace runs the assistant under its own config
# directory, which is what keeps the study off the participant's own account, so
# they are in the workspace and NOT in ~/.claude/projects, which is where this
# looked while reporting "transcripts: 0" on a session that had recorded every
# one of them. Their own folder is still swept, for the case where somebody
# started the assistant with plain `claude` and that is where the session went.
mkdir -p "$DEST/claude-transcripts"
for w in scribe tally; do
  src="$WORK/$w/.claude-study/projects"
  [ -d "$src" ] || continue
  rsync -a "$src/" "$DEST/claude-transcripts/$w/" 2>/dev/null
done
# The recovery sweep, for a session started with plain `claude` instead of the
# launcher. Claude Code names a project folder after its path with the slashes
# turned into dashes, so the study workspaces have exactly predictable names and
# this asks for those exact names rather than for anything matching "scribe" or
# "tally". Globbing on the name swept in unrelated sessions of the researcher's
# own that merely had the word in their path, and on a participant's machine it
# would do the same to any project of theirs that happened to be called either.
for w in scribe tally; do
  d="$HOME/.claude/projects/$(printf '%s' "$WORK/$w" | tr '/.' '--')"
  [ -d "$d" ] || continue
  rsync -a "$d" "$DEST/claude-transcripts/their-own-account/" 2>/dev/null
done
found=$(find "$DEST/claude-transcripts" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
echo "  Claude Code transcripts: $found"
[ "$found" = "0" ] && echo "    WARNING: none found. Tell the experimenter before you close the call."

{
  echo "participant: $CODE"
  echo "collected:   $(date -Iseconds)"
  echo "machine:     $(uname -srm)"
} > "$DEST/collection.meta"

rm -f "$OUT"
mkdir -p "$(dirname "$OUT")" 2>/dev/null
( cd "$TMP" && zip -qr "$OUT" "codoc-study-$CODE" ) 2>/dev/null
# The Desktop is not guaranteed, and this is the last step of a session: a "Done"
# printed over a zip that was never written loses the whole thing, because by
# then the call is over and the folders are the participant's to delete.
if [ ! -s "$OUT" ]; then
  OUT="$HOME/codoc-study-$CODE.zip"
  rm -f "$OUT"
  ( cd "$TMP" && zip -qr "$OUT" "codoc-study-$CODE" ) 2>/dev/null
fi
rm -rf "$TMP"

echo
if [ -s "$OUT" ]; then
  echo "Done. Send this file to the experimenter:"
  echo "  $OUT"
  echo "  size: $(du -h "$OUT" | cut -f1)"
  echo
  echo "Nothing in it holds your name, only the code $CODE."
else
  echo "The archive could not be written, so nothing has been collected yet."
  echo "Tell the experimenter now, before the call ends and this machine is tidied up."
  exit 1
fi
