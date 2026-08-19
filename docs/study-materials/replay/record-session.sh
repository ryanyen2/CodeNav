#!/usr/bin/env bash
# record-session.sh — capture the agent session the study replays.
#
#   record-session.sh start scribe codoc     set up and begin watching
#   record-session.sh stop  scribe codoc     stop, and build the frames
#   record-session.sh check scribe codoc     replay into a clean copy and compare
#
# The session is recorded once per project and per condition, on the
# experimenter's machine. The codoc condition records with the daemon running,
# so the tree state a participant sees is the state codoc really produced. The
# experimenter runs `claude` by hand in a second terminal, because the session
# may need nudging before it lands the planted problems, and every nudge has to
# be written down in notes.md next to the frames.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
REPLAY="$REPO/docs/study-materials/replay"
WORK="$HOME/codoc-recording"

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mfail\033[0m  %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

command="${1:-}"
project="${2:-}"
arm="${3:-}"
case "$project" in scribe|tally) ;; *) bad "project must be scribe or tally"; exit 2 ;; esac
# `neutral` is the workspace the code session is recorded in, with neither tool
# present. It is a stage rather than a condition, but it goes through the same
# start / stop / check, because the thing being recorded is the same thing.
case "$arm" in codoc|baseline|neutral) ;; *) bad "condition must be codoc, baseline or neutral"; exit 2 ;; esac

ws="$WORK/$project-$arm"
raw="$WORK/raw/$project-$arm"
frames="$REPLAY/frames/$project/$arm"
tarball_name="$project"; [ "$arm" = baseline ] && tarball_name="$project-baseline"
tarball="$REPO/docs/study-materials/workspaces/$tarball_name.tar.gz"

request_file="$REPLAY/requests/$project.txt"

# The code session is recorded in a workspace with no codoc, no description and
# no agent configuration in it. Two reasons, and both are load bearing.
#
# Both conditions have to review the SAME code, or a detection count cannot be
# compared between them, and two separate agent runs never produce the same code.
#
# The transcript is read by participants in both conditions, so it must not
# mention either tool. An agent left in a codoc workspace explores it, finds
# `.codoc/tree.codoc` and the codoc skill, and says so in its own output, which
# was found by running exactly that and reading what it wrote.
#
# The removal is folded into the last commit rather than left uncommitted, so the
# agent does not start work in a tree that is already dirty for reasons nobody
# can explain to it, and so the history stays the same twelve commits both
# conditions have.
strip_tools() {
  local ws="$1"
  ( cd "$ws" && rm -rf .codoc .claude .mcp.json CLAUDE.md && git add -A >/dev/null 2>&1 )
  # Folding the removal into the last commit keeps the history the same length
  # and keeps its messages saying nothing about either tool. When the last commit
  # was nothing but the tool files, amending it would leave it empty and git
  # refuses, so the commit is dropped instead, which is the more neutral outcome
  # anyway because the history then looks like the tool was never there.
  #
  # The failure used to be swallowed. The agent then began in a tree holding
  # eight staged deletions, ran `git status`, and wrote `.codoc/tree.codoc` and
  # `.claude/skills/codoc-intent/SKILL.md` into the transcript both conditions
  # read as their scrollback.
  if ! ( cd "$ws" && git commit -q --amend --no-edit >/dev/null 2>&1 ); then
    ( cd "$ws" && git reset -q --hard HEAD^ >/dev/null 2>&1 )
  fi
  # The editable install leaves build output behind, and an agent that sees it in
  # `git status` writes a filter to hide it. Excluding it locally keeps the
  # recorded session about the agent's own work. `.git/info/exclude` is not a
  # tracked file, so nothing about it reaches a participant.
  printf '%s\n' '.venv/' '*.egg-info/' '__pycache__/' '.pytest_cache/' \
    >> "$ws/.git/info/exclude"
  if [ -n "$( cd "$ws" && git status --porcelain )" ]; then
    bad "the neutral workspace is not clean, so the agent would start in a dirty tree"
    exit 1
  fi
  ok "no codoc, no description and no agent configuration in $ws"
}

# `uv venv` makes an environment with no pip in it, so an older
# `.venv/bin/python -m pip install` failed silently behind its redirect and left
# a workspace whose tests could not run. The check below is on the import rather
# than on the interpreter, because the interpreter existing was exactly what made
# the failure invisible.
build_env() {
  local ws="$1"
  ( cd "$ws" && uv venv --quiet .venv >/dev/null 2>&1 )
  uv pip install --quiet --python "$ws/.venv/bin/python" -e "$ws" pytest >/dev/null 2>&1
  if ! ( cd "$ws" && .venv/bin/python -c "import $project" >/dev/null 2>&1 ); then
    bad "the environment in $ws did not build"; exit 1
  fi
}

start() {
  [ -f "$tarball" ] || { bad "no workspace tarball at $tarball"; exit 1; }
  [ -f "$request_file" ] || { bad "no request at $request_file"; exit 1; }
  if [ -d "$ws" ]; then
    bad "$ws already exists. Move it aside first, so a half-recorded session is never reused."
    exit 1
  fi

  step "Unpacking a clean $project workspace for the $arm condition"
  mkdir -p "$WORK" "$raw"
  tar xzf "$tarball" -C "$WORK"
  # The codoc arm's archive is called `scribe` and the baseline's is called
  # `scribe-baseline`, so one needs renaming into place and the other is already
  # there. Renaming a folder onto itself fails, and it used to fail after the
  # unpack had already happened, which left a workspace that looked fine.
  [ "$WORK/$tarball_name" = "$ws" ] || mv "$WORK/$tarball_name" "$ws"
  [ -f "$ws/pyproject.toml" ] || { bad "$ws is not a workspace after unpacking"; exit 1; }
  [ "$arm" = neutral ] && strip_tools "$ws"
  build_env "$ws"
  ok "$ws"

  if [ "$arm" = codoc ]; then
    step "Starting the daemon, so the tree state is codoc's own"
    ( cd "$ws" && nohup codoc watch >"$WORK/$project-$arm-daemon.log" 2>&1 & )
    sleep 3
    if [ -f "$ws/.codoc/watch.pid" ]; then ok "daemon running"; else bad "daemon did not start"; fi
  fi

  step "Starting the watcher"
  nohup python3 "$REPLAY/record.py" watch "$ws" "$raw" --interval 1 \
    >"$WORK/$project-$arm-watch.log" 2>&1 &
  echo $! > "$WORK/$project-$arm-watch.pid"
  sleep 1
  ok "watching $ws"

  step "Now run the session yourself, in another terminal"
  cat <<EOF
  cd $ws
  claude

Paste this request, and nothing else:

$(cat "$request_file")

Nudge only as far as it takes to land the planted problems, and write every
nudge into $frames/notes.md. When the agent has finished and the tests pass:

  $0 stop $project $arm
EOF
}

stop() {
  step "Stopping the watcher"
  pidfile="$WORK/$project-$arm-watch.pid"
  if [ -f "$pidfile" ]; then
    kill -INT "$(cat "$pidfile")" 2>/dev/null && sleep 2
    rm -f "$pidfile"; ok "watcher stopped"
  else
    bad "no watcher pid file, the snapshots may be short"
  fi

  if [ "$arm" = codoc ] && [ -f "$ws/.codoc/watch.pid" ]; then
    step "Stopping the daemon"
    pid="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["pid"])' "$ws/.codoc/watch.pid" 2>/dev/null)"
    [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null && ok "daemon stopped"
  fi

  step "Finding the transcript"
  slug="$(echo "$ws" | sed 's|/|-|g')"
  dir="$HOME/.claude/projects/$slug"
  transcript="$(ls -t "$dir"/*.jsonl 2>/dev/null | head -1)"
  if [ -n "${transcript:-}" ]; then ok "$transcript"; else bad "no transcript under $dir"; fi

  step "Building the frames"
  mkdir -p "$frames"
  python3 "$REPLAY/record.py" build "$raw" ${transcript:+"$transcript"} "$frames" --seconds 180 || exit 1
  [ -n "${transcript:-}" ] && cp "$transcript" "$frames/transcript.jsonl"
  [ -f "$frames/notes.md" ] || cat > "$frames/notes.md" <<EOF
# What was nudged, and what the agent did on its own

Recorded $(date -u +%Y-%m-%dT%H:%M:%SZ) for $project, $arm condition.

Write one line per nudge. A planted problem the agent produced on its own is
stronger evidence than one it had to be steered into, and the paper reports
which is which.

- (nothing yet)
EOF
  ok "frames in $frames"
  echo
  echo "Check them with: $0 check $project $arm"
}

check() {
  step "Replaying into a clean copy and comparing"
  scratch="$(mktemp -d)"
  trap 'rm -rf "$scratch"' EXIT
  python3 "$REPLAY/play.py" "$scratch" "$frames" --speed 1000 --no-transcript >/dev/null || {
    bad "the replay did not run"; exit 1; }
  python3 -c "
import sys
sys.path.insert(0, '$REPLAY')
from record import scan
from pathlib import Path
replayed, recorded = scan(Path('$scratch')), scan(Path('$ws'))
missing = sorted(set(recorded) - set(replayed))
extra = sorted(set(replayed) - set(recorded))
differ = sorted(f for f in set(recorded) & set(replayed) if recorded[f] != replayed[f])
for label, files in (('missing', missing), ('unexpected', extra), ('different', differ)):
    for f in files[:10]:
        print(f'  {label}: {f}')
print('FILES', len(recorded))
sys.exit(1 if (missing or extra or differ) else 0)
" && ok "the replay reproduces the recorded end state" || { bad "the replay does not match the recording"; exit 1; }
}

case "$command" in
  start) start ;;
  stop)  stop ;;
  check) check ;;
  *) echo "usage: $0 {start|stop|check} {scribe|tally} {neutral|codoc|baseline}"; exit 2 ;;
esac
