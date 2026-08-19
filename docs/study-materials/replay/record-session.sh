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
case "$arm" in codoc|baseline) ;; *) bad "condition must be codoc or baseline"; exit 2 ;; esac

ws="$WORK/$project-$arm"
raw="$WORK/raw/$project-$arm"
frames="$REPLAY/frames/$project/$arm"
tarball_name="$project"; [ "$arm" = baseline ] && tarball_name="$project-baseline"
tarball="$REPO/docs/study-materials/workspaces/$tarball_name.tar.gz"

request_file="$REPLAY/requests/$project.txt"

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
  ( cd "$ws" && uv venv --quiet .venv >/dev/null 2>&1 && \
    .venv/bin/python -m pip install -q -e . >/dev/null 2>&1 )
  [ -x "$ws/.venv/bin/python" ] || { bad "the environment in $ws did not build"; exit 1; }
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
  *) echo "usage: $0 {start|stop|check} {scribe|tally} {codoc|baseline}"; exit 2 ;;
esac
