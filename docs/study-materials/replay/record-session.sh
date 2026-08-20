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
#
# `write` takes no condition: it does every stage of the project in one go, which
# is the point of it.
if [ "$command" != write ]; then
  case "$arm" in codoc|baseline|neutral) ;; *) bad "condition must be codoc, baseline or neutral"; exit 2 ;; esac
fi

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
  # SIGTERM rather than SIGINT, and then check. A job started with `nohup ... &`
  # from a script inherits SIGINT set to ignore, so `kill -INT` did nothing here
  # and reported success. The watcher it failed to stop then ran alongside the
  # next one and silently destroyed a recording.
  #
  # The raw directory's own pid file is the authority, because it is written by
  # the watcher itself rather than by whoever started it.
  pidfile="$raw/watcher.pid"
  [ -f "$pidfile" ] || pidfile="$WORK/$project-$arm-watch.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    kill -TERM "$pid" 2>/dev/null
    for _ in 1 2 3 4 5; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    if kill -0 "$pid" 2>/dev/null; then
      bad "watcher $pid is still running. Stop it before recording anything else."
      exit 1
    fi
    rm -f "$WORK/$project-$arm-watch.pid"; ok "watcher stopped"
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
  # Stop the recording workspace's daemon first. The comparison is against that
  # workspace, and a daemon still running in it keeps amending descriptions after
  # the last frame was cut — so every codoc check failed on tree.codoc and
  # status.json whatever the recording had done, which is a check that cannot pass
  # and therefore says nothing.
  stop_daemon_in "$ws"
  step "Replaying into a clean copy and comparing"
  scratch="$(mktemp -d)"
  trap 'rm -rf "$scratch"' EXIT
  python3 "$REPLAY/play.py" "$scratch" "$frames" --speed 1000 --no-transcript >/dev/null || {
    bad "the replay did not run"; exit 1; }
  python3 -c "
import sys
sys.path.insert(0, '$REPLAY')
from record import comparable, scan
from pathlib import Path
replayed, recorded = scan(Path('$scratch')), scan(Path('$ws'))
missing = sorted(set(recorded) - set(replayed))
extra = sorted(set(replayed) - set(recorded))
# Every file must be THERE — a recording that lost activity.json would leave the
# presence surface dark and this is the gate that would say so. What is exempt is
# only the comparison of bytes, for the files the player retargets on purpose
# (record.REPLAY_RETARGETED).
by_bytes = comparable(recorded)
differ = sorted(f for f in set(by_bytes) & set(replayed) if by_bytes[f] != replayed[f])
for label, files in (('missing', missing), ('unexpected', extra), ('different', differ)):
    for f in files[:10]:
        print(f'  {label}: {f}')
print('FILES', len(recorded))
sys.exit(1 if (missing or extra or differ) else 0)
" && ok "the replay reproduces the recorded end state" || { bad "the replay does not match the recording"; exit 1; }
}

# ── the written session, end to end ──────────────────────────────────────────
#
#   record-session.sh write scribe
#
# The agent's half of a session is written down (`script/<project>/`), so there
# is nothing to sit and watch and no key to spend on it. codoc's half is still
# not written: each condition is DERIVED by replaying the frames into a live
# workspace and recording what that condition's own machinery did.
#
# It is a subcommand rather than three commands in a README because the order is
# load bearing in a way that is invisible when it goes wrong:
#
#   • the checkpoints are cut on the NEUTRAL frames and BEFORE `derive`, because
#     derive has to know where they are: it keeps the store at each stop and
#     settles the daemon there whatever `--settle-every` says. A checkpoint added
#     afterwards gives a participant a plan they cannot see, projected from a
#     store that does not have it in;
#   • each condition derives from a CLEAN workspace, because deriving into the
#     one the last run ended in records a session that changes nothing;
#   • the codoc arm needs its daemon running and the baseline arm must not have
#     one, which is the whole difference between them.
#
# `simulate` writes the checkpoints straight from the script, so `checkpoint` is
# only needed to change them on a recording that already exists.
# `write <project>` does every stage. `write <project> codoc` re-derives one arm
# from the neutral frames that are already there, which is what a fault found in
# one condition wants: the other arm's derive costs an LLM pass and is not at
# fault, and re-running it would change frames nobody asked to change.
write_session() {
  local script_dir="$REPLAY/script/$project"
  [ -d "$script_dir" ] || { bad "no written session at $script_dir"; exit 1; }
  local only="$arm"

  if [ -z "$only" ] || [ "$only" = neutral ]; then
    fresh_workspace neutral
    strip_tools "$WORK/$project-neutral"
    build_env "$WORK/$project-neutral"
    step "Writing the session into frames/$project/neutral"
    python3 "$REPLAY/record.py" simulate "$script_dir" "$WORK/$project-neutral" \
      "$REPLAY/frames/$project/neutral" || { bad "the script did not play"; exit 1; }
  fi
  [ -f "$REPLAY/frames/$project/neutral/manifest.json" ] || {
    bad "no neutral frames to derive from — run: $0 write $project"; exit 1; }

  [ -z "$only" ] || [ "$only" = codoc ] && derive_codoc
  [ -z "$only" ] || [ "$only" = baseline ] && derive_baseline

  step "Now check what you derived"
  [ -z "$only" ] || [ "$only" = codoc ] && echo "  $0 check $project codoc"
  [ -z "$only" ] || [ "$only" = baseline ] && echo "  $0 check $project baseline"
  return 0
}

derive_codoc() {
  local codoc_bin; codoc_bin="$(command -v codoc || true)"
  [ -x "$codoc_bin" ] || { bad "codoc is not on PATH, and deriving needs it"; exit 1; }
  fresh_workspace codoc
  build_env "$WORK/$project-codoc"
  step "Starting the daemon, so the tree state is codoc's own"
  ( cd "$WORK/$project-codoc" && nohup codoc watch >"$WORK/$project-codoc-daemon.log" 2>&1 & )
  sleep 4
  [ -f "$WORK/$project-codoc/.codoc/watch.pid" ] || { bad "the daemon did not start"; exit 1; }
  ok "daemon running in $WORK/$project-codoc"
  step "Deriving the codoc condition"
  python3 -u "$REPLAY/record.py" derive "$REPLAY/frames/$project/neutral" \
    "$WORK/$project-codoc" "$REPLAY/frames/$project/codoc" \
    --pace --settle-every 4 --codoc "$codoc_bin" \
    2>&1 | tee "$WORK/$project-derive-codoc.log"
  [ "${PIPESTATUS[0]}" = 0 ] || { bad "the codoc derive failed"; exit 1; }
}

derive_baseline() {
  fresh_workspace baseline
  build_env "$WORK/$project-baseline"
  step "Deriving the baseline condition"
  python3 -u "$REPLAY/record.py" derive "$REPLAY/frames/$project/neutral" \
    "$WORK/$project-baseline" "$REPLAY/frames/$project/baseline" \
    --after "$BASELINE_AFTER" \
    2>&1 | tee "$WORK/$project-derive-baseline.log"
  [ "${PIPESTATUS[0]}" = 0 ] || { bad "the baseline derive failed"; exit 1; }
}

# A workspace the last run did not leave anything in. Deriving into the state the
# previous derive ended in records a session in which nothing changes, and the
# only sign of it is frames that are all empty.
# Nothing may still own the workspace when it is replaced. A daemon started in it
# follows the directory when it is moved aside and keeps writing into a recording
# that is over; started in one that is DELETED, it survives with a cwd that is not
# there, fails every cycle, and writes a traceback per second into the log the next
# run is reading. Both happened, and the second is what made a good derive look
# broken.
stop_daemon_in() {
  local dir="$1"
  local pid
  pid="$(python3 -c "
import json, sys
try:
    print(json.load(open('$dir/.codoc/watch.pid'))['pid'])
except Exception:
    pass" 2>/dev/null)"
  # Also anything whose working directory IS this workspace, which is how a daemon
  # from an earlier attempt survives: its pid file went with the directory.
  local strays
  strays="$(pgrep -f 'codoc watch' 2>/dev/null || true)"
  for p in $pid $strays; do
    case " $(lsof -a -p "$p" -d cwd -Fn 2>/dev/null | grep '^n' | cut -c2-) " in
      *" $dir "*) kill "$p" 2>/dev/null && ok "stopped the daemon in $dir ($p)" ;;
      *) [ "$p" = "$pid" ] && kill "$p" 2>/dev/null || true ;;
    esac
  done
  sleep 2
}

fresh_workspace() {
  local which="$1"
  local dir="$WORK/$project-$which"
  stop_daemon_in "$dir"
  local name="$project"; [ "$which" = baseline ] && name="$project-baseline"
  local ball="$REPO/docs/study-materials/workspaces/$name.tar.gz"
  [ -f "$ball" ] || { bad "no workspace tarball at $ball"; exit 1; }
  step "Unpacking a clean workspace for $which"
  if [ -d "$dir" ]; then
    local aside="$dir.$(date +%Y%m%d-%H%M%S)"
    mv "$dir" "$aside"
    ok "moved the old one to $aside"
  fi
  mkdir -p "$WORK"
  tar xzf "$ball" -C "$WORK"
  # The codoc arm's archive is called `scribe` and the baseline's `scribe-baseline`,
  # so one needs renaming into place. Doing this by hand and forgetting it leaves a
  # directory holding nothing but a .venv, and the derive that follows records twelve
  # frames in which nothing happens — which is why it is in the script.
  [ "$WORK/$name" = "$dir" ] || mv "$WORK/$name" "$dir"
  [ -f "$dir/pyproject.toml" ] || { bad "$dir is not a workspace after unpacking"; exit 1; }
  if [ "$which" = codoc ] && [ ! -d "$dir/.codoc" ]; then
    bad "$dir has no .codoc/, so there is no tree for the daemon to keep"
    exit 1
  fi
  ok "$dir"
}

# The baseline arm's own record pass: the doc-maintenance skill that ships in that
# workspace, run once at the end the way it runs at the end of any session there.
# Its own transcript is discarded — the participant's scrollback is the neutral
# recording, and the daemon's Loop A passes are not in a transcript either, so
# discarding it is what keeps the two conditions symmetrical.
#
# The key stays in the environment and never in the string, because this string
# reaches the log, the scrollback, and anything that quotes them.
BASELINE_AFTER="${BASELINE_AFTER:-claude -p 'Use the doc-maintenance skill: bring CLAUDE.md back in line with the code as it now stands. Read the diff first.' --allowedTools Read,Grep,Glob,Edit,Write,Bash --permission-mode acceptEdits}"

case "$command" in
  start) start ;;
  stop)  stop ;;
  check) check ;;
  write) write_session ;;
  *) echo "usage: $0 {start|stop|check} {scribe|tally} {neutral|codoc|baseline}"
     echo "       $0 write {scribe|tally} [neutral|codoc|baseline]"; exit 2 ;;
esac
