#!/usr/bin/env bash
# setup.sh — sets up one participant machine for the codoc study.
#
# This script ships inside the participant bundle, next to the .vsix, the wheel,
# and the four workspace archives. It is safe to run more than once.
#
# Four archives ship, but only TWO are unpacked, because a participant does one
# project each way. Which two depends on the order, and both land in folders
# named for the project alone: ~/codoc-study/scribe and ~/codoc-study/tally.
#
#   ./setup.sh p-abcdefghjkmn codoc-first            install everything
#   ./setup.sh p-abcdefghjkmn codoc-first zh-Hans    the same, in Simplified Chinese
#   ./setup.sh --check                               only check what is installed
#
# The researcher gives you the code and the order. Both are on the link they
# send you, and both are needed here: the code is what your session is filed
# against, and without it nothing you do reaches the researcher.
#
# It installs uv, installs the codoc command, unpacks your two workspaces, and
# builds a Python environment for each one. It does not install Claude Code or
# VS Code, because both need you to sign in. It tells you if either is missing.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$HOME/codoc-study"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

# The participant code, taken before anything is installed. Getting it wrong is
# cheap to fix here and expensive to notice later: a session that ran with no
# code looks normal on the screen and arrives nowhere.
CODE="${1:-${CODOC_STUDY_PARTICIPANT:-}}"
ORDER="${2:-${CODOC_STUDY_ORDER:-}}"
# The language the WHOLE session runs in. It comes off the participant's link and
# decides which workspace archives are unpacked. Optional and English by default,
# so an older link keeps working.
LANG_CODE="${3:-${CODOC_STUDY_LANG:-en}}"
[ "$CHECK_ONLY" = 1 ] && { CODE=""; ORDER=""; }

# The two keys the study pays for are FETCHED with the code, not pasted.
#
# A key that has to be copied by hand is a key that ends up in the wrong window,
# and the copying is the step that fails while somebody is on a call. The code is
# already on their study page and already has to be typed; nothing else does.
STUDY_ANTHROPIC_KEY=""
STUDY_OPENAI_KEY=""

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '  \033[33mtodo\033[0m  %s\n' "$1"; }
bad()  { printf '  \033[31mfail\033[0m  %s\n' "$1"; }
# Below todo: true, worth saying, and nothing for anybody to do. `todo` is what
# the participant is told to report, so it cannot also mean "this is fine".
note() { printf '  \033[2mnote\033[0m  %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

FAILED=0
TODO=0

# Run a command, give up after N seconds, print whatever it managed to say.
# Written out rather than using `timeout`, which macOS does not ship: calling it
# there fails as "command not found", which reads exactly like the command under
# test failing, and would have flagged every Mac participant.
with_deadline() {
  local secs="$1"; shift
  local out; out="$(mktemp)"
  "$@" >"$out" 2>/dev/null &
  local pid=$!
  ( sleep "$secs"; kill -9 "$pid" 2>/dev/null ) >/dev/null 2>&1 &
  local killer=$!
  # Forget the timer, so killing it does not print "Killed: 9" over the setup
  # output. A participant reading that reasonably thinks something went wrong.
  disown "$killer" 2>/dev/null || true
  wait "$pid" 2>/dev/null
  local rc=$?
  kill -9 "$killer" 2>/dev/null
  cat "$out"; rm -f "$out"
  return "$rc"
}

# ------------------------------------------------------- is it instrumented
# The two things that decide whether a session produces data at all, read the way
# the things that depend on them read it rather than by asking whether a file
# exists. Defined up here because --check and the end of a full run both need
# them, and because a machine that installs perfectly and records nothing is the
# one failure that costs a whole session and shows nothing at the time.
filed_under() {
  python3 -c "
import json,sys
try: print(json.load(open(sys.argv[1])).get('codocStudyLogger.participant',''))
except Exception: print('')" "$1/.vscode/settings.json" 2>/dev/null
}

# 1: no hook. 2: a hook that records prompts under no code, or under the wrong
# one, which reads as working right up until the logs are sorted by participant.
records_prompts() {
  python3 -c "
import json, re, sys
want = sys.argv[2]
try:
    hooks = json.load(open(sys.argv[1]))['hooks']['UserPromptSubmit']
except Exception:
    raise SystemExit(1)
ours = [hook.get('command', '') for entry in hooks
        for hook in entry.get('hooks', []) if 'prompt-hook' in hook.get('command', '')]
if not ours:
    raise SystemExit(1)
codes = set()
for command in ours:
    found = re.search(r'CODOC_STUDY_PARTICIPANT=(\S*)', command)
    codes.add(found.group(1) if found else '')
if '' in codes or (want and codes != {want}):
    raise SystemExit(2)
raise SystemExit(0)" "$1/.claude/settings.json" "${2-}" 2>/dev/null
}

# Every absolute path a workspace is wired to that is not on this machine.
#
# Everything here is an absolute path: the interpreter in each hook command, and
# codoc's MCP server. Two ways they go wrong, and neither shows at the time. The
# copies committed inside the archive belong to the machine it was built on. And
# a hook installed by a python that has since gone points nowhere in particular.
# This is what tells a workspace that records from one that only looks like it.
paths_missing() {
  python3 -c "
import json, os, shlex, sys
root, with_mcp = sys.argv[1], sys.argv[2] == 'with-mcp'
bad = []
def check(command):
    parts = shlex.split(command or '')
    # Step over a leading VAR=VALUE, which is how the prompt hook carries the
    # participant code, and land on the interpreter itself.
    while parts and not parts[0].startswith('/') and '=' in parts[0]:
        parts = parts[1:]
    if parts and parts[0].startswith('/') and not os.path.exists(parts[0]):
        bad.append(parts[0])
try:
    settings = json.load(open(os.path.join(root, '.claude', 'settings.json')))
except Exception:
    raise SystemExit('.claude/settings.json is missing or unreadable')
for entries in settings.get('hooks', {}).values():
    for entry in entries:
        for hook in entry.get('hooks', []):
            check(hook.get('command'))
if with_mcp:
    try:
        servers = json.load(open(os.path.join(root, '.mcp.json')))['mcpServers']
    except Exception:
        raise SystemExit('.mcp.json is missing or unreadable')
    for server in servers.values():
        check(server.get('command'))
print('\n'.join(sorted(set(bad))))" "$1" "${2-}" 2>&1
}

# One workspace. `want` empty means any code will do, which is what --check knows.
instrument_check() {
  local w="$1" want="${2-}" d="$WORK/$1" seen
  seen="$(filed_under "$d")"
  if [ -n "$seen" ] && { [ -z "$want" ] || [ "$seen" = "$want" ]; }; then
    ok "$w is filed under $seen"
  elif [ -z "$seen" ]; then
    bad "$w has no participant code, so its session would arrive nowhere"
    FAILED=1
  else
    bad "$w is filed under $seen, not $want. Its session would be filed as somebody else's"
    FAILED=1
  fi
  records_prompts "$d" "$want"
  case "$?" in
    0) ok "$w records prompts${want:+ under $want}" ;;
    2) bad "$w records prompts under the wrong code, or under none at all"; FAILED=1 ;;
    *) bad "$w does not record prompts, so those measures would have no data"; FAILED=1 ;;
  esac
}

# `with_mcp` for the codoc workspace; the other arm has no MCP server and is
# checked for its hooks alone.
wiring_check() {
  local w="$1" with_mcp="${2-}" missing what
  what="its hooks"; [ "$with_mcp" = with-mcp ] && what="codoc's hooks and MCP server"
  missing="$(paths_missing "$WORK/$w" "$with_mcp")"
  if [ -z "$missing" ]; then
    ok "$w: $what point at this machine"
  else
    bad "$w is wired to paths that do not exist here:"
    # One line per path, not one per word: the reason can be a sentence.
    printf '%s\n' "$missing" | sed 's/^/          /'
    if [ "$with_mcp" = with-mcp ]; then
      echo "          Run: cd $WORK/$w && $WORK/codoc install-hooks"
    else
      echo "          Run setup again: ./setup.sh <your code> <your order>"
    fi
    FAILED=1
  fi
}

# ------------------------------------------------------------- who you are here
# Asked first, so a typo costs a second rather than a whole install. The code is
# not secret and identifies nobody. It is how your work is filed.
if [ "$CHECK_ONLY" = 0 ]; then
  while ! printf '%s' "$CODE" | grep -Eq '^(p|pilot)-[a-z0-9]+$'; do
    [ -n "$CODE" ] && echo "  That does not look like a code. They look like p-abcdefghjkmn."
    printf 'Participant code from the researcher: '
    read -r CODE || { echo; echo "No code given, so there is nothing to set up."; exit 1; }
  done
  while [ "$ORDER" != "codoc-first" ] && [ "$ORDER" != "baseline-first" ]; do
    [ -n "$ORDER" ] && echo "  Type codoc-first or baseline-first."
    printf 'Order (codoc-first or baseline-first): '
    read -r ORDER || { echo; echo "No order given."; exit 1; }
  done

fi

# ---8<--- which arm is which (test-setup.sh runs this block too)
# Which archive each project is unpacked from, for this participant.
#
# The folders are named for the PROJECT alone: ~/codoc-study/scribe and
# ~/codoc-study/tally. They used to be named for the condition as well, so a
# participant spent half the session typing "scribe-baseline" into a terminal.
# "Baseline" tells somebody they are in the control arm, and they then answer a
# questionnaire about how the two compared. The manipulation is not a secret —
# one folder has a feature tree in it and the other a CLAUDE.md — but it does not
# need a name that ranks them.
#
# scribe is always the first project and tally the second, so the order decides
# which condition each one carries. Only these two are unpacked; the other two
# archives are the same projects the other way round and this participant never
# opens them.
if [ "$ORDER" = "codoc-first" ]; then
  SCRIBE_FROM=scribe;           SCRIBE_COND=codoc
  TALLY_FROM=tally-baseline;    TALLY_COND=baseline
  CODOC_ARM=scribe
else
  SCRIBE_FROM=scribe-baseline;  SCRIBE_COND=baseline
  TALLY_FROM=tally;             TALLY_COND=codoc
  CODOC_ARM=tally
fi
PROJECTS="scribe tally"

# The archives for this participant's language.
#
# A session runs entirely in ONE language: this machine, their study page, the
# questions, the task cards and BOTH descriptions. Translating one arm and not
# the other would make language vary with condition, and every result would then
# be as attributable to reading in a second language as to the tool. So the suffix
# is chosen once, here, and applies to both workspaces.
case "$LANG_CODE" in
  en|'')      LANG_CODE=en; SUFFIX='' ;;
  zh-Hans)    SUFFIX='.zh-Hans' ;;
  *)          echo "  unknown language '$LANG_CODE'; using English."; LANG_CODE=en; SUFFIX='' ;;
esac

# The archive a project comes from, and the condition it carries.
archive_for() { case "$1" in scribe) echo "$SCRIBE_FROM" ;; tally) echo "$TALLY_FROM" ;; esac; }
condition_for() { case "$1" in scribe) echo "$SCRIBE_COND" ;; tally) echo "$TALLY_COND" ;; esac; }

# What a workspace on disk says it is.
#
# --check is run without an order, so it cannot work the condition out the way
# the lines above do. It reads what setup already wrote, which is also the only
# thing the logger reads, so a check and a session can never disagree about which
# arm a folder is.
# One reader for both, because they are read the same way and for the same
# reason: to catch a folder that was built for a different session than the
# one this link describes.
setting_on_disk() {
  python3 - "$WORK/$1/.vscode/settings.json" "$2" 2>/dev/null <<'PY' || true
import json, sys
try:
    with open(sys.argv[1]) as f: print(json.load(f).get(sys.argv[2], ''))
except Exception: pass
PY
}
condition_on_disk() { setting_on_disk "$1" codocStudyLogger.condition; }
lang_on_disk()      { setting_on_disk "$1" codocStudyLogger.lang; }
# --->8--- end of the arm block

# ---------------------------------------------------------------- prerequisites
step "Checking what you already have"

# Which bundle this is, first, so every pasted line of output says so.
#
# The download is one unversioned zip at one URL, on purpose: a versioned name is
# a name somebody can be sent while a different one is on the page. The cost is
# that a machine set up from last week's download is indistinguishable from one
# set up this morning, and the failures that follow look like new faults rather
# than like a stale copy. Twice now they have been read as new faults.
STAMP="$(cat "$HERE/bundle.stamp" 2>/dev/null | head -1)"
if [ -n "$STAMP" ]; then
  note "bundle $STAMP"
else
  warn "this download is older than 2026-08-19. Download it again from your study"
  echo  "          page before you go on: the setup it runs has since been fixed."
fi

case "$(uname -s)" in
  Darwin|Linux) ok "operating system is $(uname -s)" ;;
  *) bad "this script needs macOS or Linux. On Windows, run it inside WSL."; exit 1 ;;
esac

command -v curl >/dev/null 2>&1 && ok "curl is installed" || { bad "curl is missing"; FAILED=1; }
command -v tar  >/dev/null 2>&1 && ok "tar is installed"  || { bad "tar is missing";  FAILED=1; }

if command -v claude >/dev/null 2>&1; then
  ok "Claude Code is installed, version $(claude --version 2>/dev/null | head -1)"
  # Installed is all that is needed. The study supplies the account, and the key
  # written into each workspace takes precedence over any claude.ai login, so
  # there is nothing to sign in to and nothing of the participant's to spend.
  # Whether it actually works is checked later, against the key we wrote.
else
  warn "Claude Code is not installed. Install it with:"
  echo  "          curl -fsSL https://claude.ai/install.sh | bash"
  echo  "          You do not need to sign in or buy a plan. The study provides the account."
  TODO=1
fi

if command -v code >/dev/null 2>&1; then
  ok "VS Code is installed, version $(code --version 2>/dev/null | head -1)"
else
  warn "The 'code' command is not on your PATH."
  echo  "          Install VS Code from https://code.visualstudio.com"
  echo  "          Then open it, press Cmd+Shift+P (Ctrl+Shift+P on Linux),"
  echo  "          and run: Shell Command: Install 'code' command in PATH"
  TODO=1
fi

if [ "$CHECK_ONLY" = 1 ]; then
  step "Checking the workspaces"
  for w in $PROJECTS; do
    if [ -d "$WORK/$w" ]; then ok "$WORK/$w exists"; else bad "$WORK/$w is missing"; FAILED=1; fi
    if [ -x "$WORK/$w/.venv/bin/python" ]; then ok "$w has its Python environment"; else bad "$w has no Python environment"; FAILED=1; fi
  done
  # Check the launcher, not the PATH. `uv tool update-shell` only takes effect in a
  # new shell, so testing the PATH here would pass or fail for the wrong reason.
  if [ -x "$WORK/codoc" ] && "$WORK/codoc" --help >/dev/null 2>&1; then
    ok "the codoc command runs from $WORK/codoc"
  else
    bad "the codoc command does not run from $WORK/codoc"; FAILED=1
  fi
  if code --list-extensions 2>/dev/null | grep -qi '^codoc\.codoc$'; then ok "the codoc VS Code extension is installed"; else bad "the codoc VS Code extension is not installed"; FAILED=1; fi
  if code --list-extensions 2>/dev/null | grep -qi '^codoc\.codoc-study-logger$'; then ok "the study logger is installed"; else bad "the study logger is not installed"; FAILED=1; fi

  # Installed is not the same as running.
  #
  # VS Code asks whether you trust a folder the first time it is opened, and
  # until you answer it runs in Restricted Mode with every extension disabled.
  # Nothing about the editor looks wrong, and the logger simply never starts. The
  # proof that it did start is its own log: it writes a `session` line the moment
  # it activates, so a workspace that has been opened and has no log was opened
  # in Restricted Mode.
  for w in $PROJECTS; do
    log="$HOME/codoc-study/session-logs/interaction-$w.jsonl"
    # The log has to belong to the folder as it is NOW. It lives outside the
    # workspace, so it survives that folder being deleted and set up again, and a
    # machine that has been used for an earlier pilot has one for a code that is
    # no longer the one on disk. Read that way it said the logger had run in a
    # folder nobody had opened, and then failed the snapshot check underneath it.
    filed="$(filed_under "$WORK/$w")"
    if [ -s "$log" ] && [ -n "$filed" ] \
       && grep -q "\"p\":\"$filed\"" "$log" 2>/dev/null; then
      ok "$w: the logger has run there"
      # And it takes the 20-second snapshots itself, so the session can be
      # replayed. That recorder used to be a script somebody started by hand, and
      # on the first pilot nobody did, in either condition — the only way to see
      # it was to go looking, hours later, at collection. The proof is a ref it
      # writes on its first pass, so the moment the logger has run at all, this
      # says whether the replay is being recorded too.
      if git -C "$WORK/$w" for-each-ref --format='%(refname)' refs/study 2>/dev/null | grep -q .; then
        ok "$w: it is being snapshotted, so the session can be replayed"
      else
        # Say WHICH of the four things it is. "Nothing is snapshotting it" is
        # true and useless: every cause below has a different fix, and the person
        # reading it is usually the participant, on a call, minutes before a
        # session. Everything needed to tell them apart is already on this
        # machine.
        bad "$w: nothing is snapshotting it, so that session could not be replayed"
        FAILED=1
        if ! git -C "$WORK/$w" rev-parse --git-dir >/dev/null 2>&1; then
          echo "          It is not a git repository, or git refuses to read it."
          echo "          Run: git -C $WORK/$w status"
          echo "          If it says dubious ownership, run the command it prints."
        elif [ "$(setting_on_disk "$w" codocStudyLogger.snapshots)" = "False" ]; then
          echo "          Recording is switched off in $WORK/$w/.vscode/settings.json."
          echo "          Set codocStudyLogger.snapshots back to true."
        elif find "$HOME/codoc-study/session-logs/snapshots" -name snapshot.lock \
                  -path "*/$w/*" -mmin -1 2>/dev/null | grep -q .; then
          echo "          Another VS Code window has $w open and is recording it."
          echo "          Close every window but one, then reopen the folder."
        else
          why="$(grep -h '"ev":"snapshot"' "$log" 2>/dev/null | grep '"ok":false' \
                 | tail -1 | sed -n 's/.*"detail":"\([^"]*\)".*/\1/p')"
          if [ -n "$why" ]; then
            echo "          git refused the snapshot: $why"
          else
            # Usually the log is simply older than the folder. The log lives in
            # session-logs/ and survives a folder being deleted and set up again,
            # so "the logger has run there" can be true of a workspace that has
            # never been opened since. Opening it is the whole fix, and it is the
            # cheap one, so it goes first.
            echo "          Most likely it has not been opened since it was set up."
            echo "          Open $WORK/$w in VS Code, answer the trust prompt, wait"
            echo "          half a minute, then run ./setup.sh --check again."
            ver="$(code --list-extensions --show-versions 2>/dev/null \
                   | grep -i '^codoc.codoc-study-logger@' | head -1)"
            echo "          If it still fails, tell the researcher this line:"
            echo "          logger ${ver:-not reported by VS Code}"
          fi
        fi
      fi
    fi
  done

  # The change under review, and the proof that nobody has already seen it.
  #
  # The first turn of a session plays a recording into the workspace. A workspace
  # where that has already happened has the change in it, uncommitted, before the
  # participant has asked for anything, and a session started there reviews a
  # change that arrived with no request. Rehearsing on a real bundle is how it
  # happens, and it is silent, so it is checked here.
  for w in $PROJECTS; do
    arm="$(condition_on_disk "$w")"
    [ -n "$arm" ] || continue
    if [ -f "$WORK/replay/frames/$w/$arm/manifest.json" ]; then
      ok "$w: the session it reviews is here"
    else
      bad "$w: no recorded session for this folder, so the task cannot start"
      FAILED=1
    fi
    if [ -f "$WORK/$w/.claude-study/handover.json" ]; then
      bad "$w: the session has already been played there, so this folder is used"
      FAILED=1
    fi
  done

  # The slash commands, in the arm that has them. install-hooks writes one file
  # per command, and the verify step used to delete any that the archive was too
  # old to have tracked — so the codoc condition ran without /codoc:ask, which is
  # the command the first task is built around, and nothing said so.
  for w in $PROJECTS; do
    [ "$(condition_on_disk "$w")" = codoc ] || continue
    missing=""
    for c in ask plan sync; do
      [ -f "$WORK/$w/.claude/commands/codoc/$c.md" ] || missing="$missing /codoc:$c"
    done
    if [ -z "$missing" ]; then
      ok "$w: /codoc:ask, /codoc:plan and /codoc:sync are all there"
    else
      bad "$w is missing$missing. Run: (cd $WORK/$w && $WORK/codoc install-hooks)"
      FAILED=1
    fi
    # And that the assistant may actually USE the MCP server the commands drive.
    # Unapproved, it sits at "pending approval" and the participant is asked about
    # it in their first run; decline it and the codoc condition has no codoc tools.
    if python3 - "$WORK/$w/.claude-study/settings.json" 2>/dev/null <<'MCP_PY'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        raise SystemExit(0 if "codoc" in (json.load(fh).get("enabledMcpjsonServers") or []) else 1)
except SystemExit:
    raise
except Exception:
    raise SystemExit(1)
MCP_PY
    then
      ok "$w: codoc's MCP server is approved, so nothing is asked mid-task"
    else
      bad "$w would ask the participant to approve codoc's MCP server mid-task."
      echo  "          Run setup again: ./setup.sh <your code> <your order>"
      FAILED=1
    fi
  done

  for w in $PROJECTS; do
    log="$HOME/codoc-study/session-logs/interaction-$w.jsonl"
    if [ -s "$log" ]; then
      :
    else
      note "$w has not been opened in VS Code yet. Nothing to do: you open it"
      echo  "          together on the day. When you do, answer YES to \"Do you trust"
      echo  "          the authors\": Restricted Mode turns the extensions off, and"
      echo  "          the session would record nothing."
    fi
  done
  # Checked last and reported loudest. Everything above can be fixed after the
  # session; a session that ran without a code recorded nothing and cannot.
  # Both, not just the first: they are configured one at a time, so a good one
  # says nothing about the other, and the other is half the study.
  before="$FAILED"
  for w in $PROJECTS; do
    instrument_check "$w"
    # Only the codoc arm has an MCP server to point anywhere, so only it is
    # checked for one. Which arm this folder is comes from what setup wrote.
    if [ "$(condition_on_disk "$w")" = codoc ]; then
      wiring_check "$w" with-mcp
    else
      wiring_check "$w"
    fi
  done
  [ "$FAILED" != "$before" ] \
    && echo "          If any of those are missing, run: ./setup.sh <your code> <your order>"
  # The keys, by presence only. Whether they still work is a question for the
  # researcher's console, and checking would spend the study's money every time
  # somebody re-runs this.
  # Where setup actually puts it: the study's own config tree, read by the
  # apiKeyHelper in .claude-study/settings.json. NOT .claude/settings.json, which
  # is the workspace's own and which codoc writes — this check used to read that
  # one, so it reported four missing keys on a machine that had them.
  # The file is tested before it is read. A redirection from a file that is not
  # there is the shell's error, not the command's, so 2>/dev/null on the command
  # does not stop it printing a raw path at somebody who is only running a check.
  for w in $PROJECTS; do
    key="$WORK/$w/.claude-study/api-key"
    { [ -f "$key" ] && [ -n "$(tr -d '[:space:]' < "$key")" ]; } \
      || { bad "$w has no Anthropic key, so the agent there has no account"; FAILED=1; }
    # The OpenAI key runs codoc, so only the codoc arm needs one. Asking the
    # baseline arm for it would report a failure on a workspace that is correct.
    if [ "$(condition_on_disk "$w")" = codoc ]; then
      grep -q '^OPENAI_API_KEY=..' "$WORK/$w/.env" 2>/dev/null \
        || { bad "$w has no OpenAI key, so codoc there has no account"; FAILED=1; }
    fi
  done
  [ "$FAILED" = 0 ] && ok "the keys are in place in both workspaces"
  step "Result"
  [ "$FAILED" = 0 ] && [ "$TODO" = 0 ] && echo "  Everything is ready." || echo "  Some things are not ready. See the lines marked fail or todo above."
  exit "$FAILED"
fi

[ "$FAILED" = 1 ] && { echo; echo "Install the missing programs above, then run this script again."; exit 1; }

# ------------------------------------------------------------------------- uv
step "Installing uv"
# uv is a Python installer and package manager. codoc and the two projects all
# use it, so you do not need to install Python yourself.
if command -v uv >/dev/null 2>&1; then
  ok "uv is already installed, version $(uv --version)"
else
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
  export PATH="$HOME/.local/bin:$PATH"
  if command -v uv >/dev/null 2>&1; then ok "installed uv $(uv --version)"; else bad "could not install uv"; exit 1; fi
fi
export PATH="$HOME/.local/bin:$PATH"

step "Installing Python 3.11"
uv python install 3.11 >/dev/null 2>&1 && ok "Python 3.11 is available to uv" || { bad "could not install Python 3.11"; exit 1; }

# ----------------------------------------------------------------------- codoc
step "Installing the codoc command"
WHEEL="$(ls "$HERE"/codoc-*.whl 2>/dev/null | head -1)"
if [ -z "$WHEEL" ]; then bad "no codoc wheel found next to this script"; exit 1; fi
uv tool install --force --python 3.11 "$WHEEL" >/dev/null 2>&1 \
  && ok "installed $(basename "$WHEEL")" || { bad "could not install the codoc wheel"; exit 1; }
uv tool update-shell >/dev/null 2>&1 || true
CODOC="$(uv tool dir --bin 2>/dev/null)/codoc"
[ -x "$CODOC" ] || CODOC="$(command -v codoc || true)"
[ -x "$CODOC" ] && ok "the codoc command is at $CODOC" || { bad "the codoc command was not found after install"; exit 1; }

# ------------------------------------------------------------------ workspaces
step "Unpacking your two workspaces into $WORK"
mkdir -p "$WORK"
# A launcher with a fixed path, so nothing later depends on the PATH. `uv tool
# update-shell` only takes effect in a new shell, and the session starts in the
# shell the participant already has open.
ln -sf "$CODOC" "$WORK/codoc"
ok "made a launcher at $WORK/codoc"

# The recorded agent session the participant reviews. The researcher plays it
# during the session; nothing here starts it, and a bundle built before the
# recording exists simply has no frames yet.
if [ -d "$HERE/replay" ]; then
  rm -rf "$WORK/replay"
  cp -R "$HERE/replay" "$WORK/replay"
  chmod +x "$WORK/replay/play.py" 2>/dev/null || true
  frames="$(find "$WORK/replay/frames" -name manifest.json 2>/dev/null | wc -l | tr -d ' ')"
  if [ "$frames" -gt 0 ]; then
    ok "unpacked the recorded session ($frames to choose from)"
  else
    warn "the bundle carries no recorded session, so there is nothing to replay"
  fi
fi
for name in $PROJECTS; do
  arc="$(archive_for "$name")"
  src="$HERE/$arc$SUFFIX.tar.gz"
  [ -f "$src" ] || { bad "$arc$SUFFIX.tar.gz is missing from the bundle"; exit 1; }
  # --strip-components drops the archive's own top folder, so scribe-baseline.tar.gz
  # unpacks into a folder called scribe. The archives keep their names because
  # the researcher builds and diffs them by condition; the participant does not.
  unpack() { mkdir -p "$WORK/$name" && tar xzf "$src" -C "$WORK/$name" --strip-components=1; }
  # A folder that exists but has no pyproject.toml is not a workspace, it is what
  # an interrupted unpack leaves behind. Left alone it fails two steps later with
  # a message about something else.
  if [ -d "$WORK/$name" ] && [ ! -f "$WORK/$name/pyproject.toml" ]; then
    warn "$WORK/$name looks half-unpacked, so it is being unpacked again"
    unpack && ok "unpacked $name"
  elif [ -d "$WORK/$name" ]; then
    # An existing folder from a DIFFERENT arm is worse than no folder: it would
    # be silently kept, and the participant would spend the session in the
    # condition they were not assigned. Only the arm is checked, never the work.
    was="$(condition_on_disk "$name")"
    want="$(condition_for "$name")"
    if [ -n "$was" ] && [ "$was" != "$want" ]; then
      bad "$WORK/$name is set up for the other way of working."
      echo  "          Your link says $ORDER, so $name should be the $want one."
      echo  "          Delete it and run this again, or tell the researcher:"
      echo  "            rm -rf $WORK/$name"
      exit 1
    fi
    # And the language, for the same reason and with a worse consequence. The
    # archives differ by language, so a folder unpacked in one while the study
    # page runs in another gives a participant Chinese questions about an English
    # description. Nothing on screen says so: both halves look like a working
    # setup, and only this file records which language the FOLDER was built in.
    wasl="$(lang_on_disk "$name")"
    if [ -n "$wasl" ] && [ "$wasl" != "$LANG_CODE" ]; then
      bad "$WORK/$name was set up in $wasl, and your link says $LANG_CODE."
      echo  "          The workspaces and your study page have to be the same"
      echo  "          language, or the questions will not match the description."
      echo  "          Delete both and run this again, or tell the researcher:"
      echo  "            rm -rf $WORK/scribe $WORK/tally"
      exit 1
    fi
    warn "$WORK/$name already exists, leaving it alone"
  else
    unpack && ok "unpacked $name"
  fi
done

# Everything this script writes into a workspace, told to git in the one place
# that is not the participant's own config. Two things depend on it. The
# participant's first `git status` has to be clean, or their session starts by
# reading somebody else's changes. And the verify step at the bottom of this
# script puts the workspace back with `git clean`, which without this deletes the
# participant code, the prompt hook, and the assistant profile it just wrote —
# a machine that then looks set up and records nothing.
#
# `.claude/commands/` is on the list for a different reason, and it cost the
# feature the first task is built around. `codoc install-hooks` writes one file
# per slash command, and the archives were seeded when there were two of them, so
# /codoc:ask arrives as an UNTRACKED file — which the skip-worktree hold below
# does not cover (it only lists tracked-and-modified files) and `git clean`
# therefore deletes. The workspace ends up with /codoc:plan and /codoc:sync and
# no /codoc:ask, on a machine where setup reported success, and the same happens
# to every command codoc adds after an archive is built.
exclude_local() {
  local d="$1" pat
  [ -d "$d/.git" ] || return 0
  for pat in '.vscode/' '.venv/' '.env' '.claude-study/' 'claude-study' \
             '.claude/settings.json' '.claude/settings.local.json' \
             '.claude/commands/'; do
    grep -qxF "$pat" "$d/.git/info/exclude" 2>/dev/null \
      || printf '%s\n' "$pat" >> "$d/.git/info/exclude"
  done
}
for name in $PROJECTS; do exclude_local "$WORK/$name"; done

step "Building a Python environment for each workspace"
# This step downloads pytest and builds each project, so it is the first thing a
# blocked or dropping network breaks. Keep what it says when it fails: "could not
# build scribe" on its own sends the participant back to the researcher with
# nothing, and the reason is almost always in the last three lines.
#
# All four are tried before giving up. Stopping at the first one means a second
# round trip to find out about the second one.
BUILD_FAILED=0
for name in $PROJECTS; do
  d="$WORK/$name"
  [ -d "$d" ] || { bad "$d is missing"; exit 1; }
  # Reuse an environment that already works. uv refuses to write over an existing
  # .venv, so a second run of this script used to fail on the first workspace and
  # stop — and a second run is exactly what somebody does after the first one
  # went wrong. Anything that is not a working 3.11 is removed rather than
  # argued with: half-made environments are what an interrupted run leaves.
  if ! "$d/.venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' >/dev/null 2>&1; then
    rm -rf "$d/.venv"
  fi
  BUILD_LOG="$(mktemp)"
  if ( cd "$d" \
       && { [ -x .venv/bin/python ] || uv venv --python 3.11 --quiet .venv; } \
       && VIRTUAL_ENV="$d/.venv" uv pip install --quiet -e '.[dev]' ) >"$BUILD_LOG" 2>&1; then
    ok "built $name"
  else
    bad "could not build $name. uv said:"
    tail -3 "$BUILD_LOG" | sed 's/^/          /'
    echo  "          Most often this is the network. Try again on a better one, or run"
    echo  "          it yourself to see all of it:"
    echo  "            cd $d && rm -rf .venv && uv venv --python 3.11 .venv && VIRTUAL_ENV=$d/.venv uv pip install -e '.[dev]'"
    BUILD_FAILED=1
  fi
  rm -f "$BUILD_LOG"
done
[ "$BUILD_FAILED" = 0 ] || exit 1

# -------------------------------------------------------------- the study code
step "Filing this machine under $CODE"
# Written per workspace rather than into your own VS Code settings, so nothing
# outside these folders is touched and you can delete them and be rid of it.
# The condition is written down here rather than guessed from the folder name.
# It could not be guessed now even in principle: the folders are named for the
# project alone, so this file is the only thing on the machine that says which
# arm a workspace is, and the logger and --check both read it from here.
for name in $PROJECTS; do
  d="$WORK/$name/.vscode"
  mkdir -p "$d"
  cond="$(condition_for "$name")"
  CODE="$CODE" ORDER="$ORDER" COND="$cond" LANG_CODE="$LANG_CODE" \
  python3 - "$d/settings.json" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    with open(path) as f: settings = json.load(f)
except Exception:
    settings = {}            # absent, or edited into something unreadable
settings.update({
    'codocStudyLogger.participant': os.environ['CODE'],
    'codocStudyLogger.order': os.environ['ORDER'],
    'codocStudyLogger.condition': os.environ['COND'],
    'codocStudyLogger.lang': os.environ['LANG_CODE'],
})
with open(path, 'w') as f: json.dump(settings, f, indent=2); f.write('\n')
PY
  [ $? = 0 ] && ok "$name is filed under $CODE" \
    || { bad "could not write $d/settings.json"; FAILED=1; }
done

# ------------------------------------------------- wire codoc into the workspace
step "Recording prompts in both workspaces"
# The study owns this hook and installs it everywhere. codoc has a prompt hook of
# its own, but only in its own condition, and a measure that exists on one side
# and not the other is not a comparison. It merges into whatever is already
# there rather than replacing it.
# Copy the hook out of the bundle first. Installing it from where it was unzipped
# would leave every project pointing at a folder the participant is free to
# delete, and prompts would stop being recorded with nothing to show it.
if [ -d "$HERE/logger" ]; then
  mkdir -p "$WORK/logger"
  cp "$HERE"/logger/*.py "$WORK/logger/" 2>/dev/null || true
fi
HOOK="$WORK/logger/install-prompt-hook.py"
if [ -f "$HOOK" ]; then
  for name in $PROJECTS; do
    # Installed BY the interpreter it should run with, because the installer
    # writes whichever python ran it into the hook command. Run with a bare
    # `python3` that resolves to a virtualenv somebody happened to have active,
    # the hook points at an interpreter that is gone by the session and stops
    # recording with nothing to show for it. The workspace's own 3.11 is built by
    # the step above and is still there tomorrow.
    PY="$WORK/$name/.venv/bin/python"
    [ -x "$PY" ] || PY=python3
    # With the code, which the hook writes into every record it makes. Installed
    # without it the prompts still arrive, each one stamped with an empty
    # participant — a log that can only be attributed by the folder it was found
    # in, which is exactly the attribution that goes missing first.
    "$PY" "$HOOK" "$WORK/$name" --participant "$CODE" >/dev/null 2>&1 \
      && ok "$name: prompts will be recorded under $CODE" \
      || { bad "could not install the prompt hook in $name"; FAILED=1; }
  done
else
  warn "no prompt hook in this bundle, so prompts will not be recorded"
  TODO=1
fi

step "Connecting codoc to the workspace that uses it"
# This rewrites .claude/settings.json and .mcp.json with the paths on YOUR machine.
# Both files are committed in the archive, holding the paths of the machine the
# workspace was built on, which exist nowhere else.
#
# One workspace, not both: only one of the two carries codoc for any participant,
# and which one depends on their order.
for name in $CODOC_ARM; do
  ( cd "$WORK/$name" && "$CODOC" install-hooks >/dev/null 2>&1 ) \
    && ok "$name: wrote .claude/settings.json and .mcp.json" \
    || { bad "codoc install-hooks failed in $WORK/$name. Run it there to see why."; FAILED=1; }
done

# Hold those rewrites against git, now that everything that edits a tracked file
# has run. Two reasons, and the second one cost a pilot session.
#
# `git status` stays clean, so the participant does not open their first task on
# top of somebody else's diff. And `git checkout -- .` leaves them alone — the
# verify step at the bottom of this script runs one, and without this it puts
# back the committed versions: the prompt hook gone, and every codoc hook and the
# MCP server pointing at the build machine's paths. The workspace then looks
# perfect and is not instrumented at all.
for name in $PROJECTS; do
  ( cd "$WORK/$name" 2>/dev/null || exit 0
    [ -d .git ] || exit 0
    # Whatever setup actually changed, rather than a list here that goes stale
    # the next time install-hooks learns to write another file.
    changed="$(git diff --name-only 2>/dev/null)"
    [ -n "$changed" ] || exit 0
    printf '%s\n' "$changed" | tr '\n' '\0' | xargs -0 git update-index --skip-worktree 2>/dev/null )
done

# ------------------------------------------------------------ which model runs
step "Fetching this session's keys"
# Fetched with the code rather than pasted. The participant never sees a key.
#
# Anything holding a participant link can read that participant's copy, which is
# the price of not pasting. It is why these are keys issued FOR the study, with a
# hard spend cap, revoked when the sessions end — and why the dashboard has a
# Revoke button for the day one leaks.
FIREBASE_KEY="AIzaSyCeIFBc8HhCmtw9-pXjUm1qT3CUyo5GbkY"
FIRESTORE="https://firestore.googleapis.com/v1/projects/codoc-11b10/databases/(default)/documents"

# What went wrong, in the database's own words. A refusal and a code with no keys
# yet are one message on this screen and two different fixes on the researcher's,
# so the reason has to survive as far as the person who has to act on it.
say_error() {
  python3 -c "
import sys, json
try:
    doc = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
err = doc.get('error') if isinstance(doc, dict) else None
if isinstance(err, dict):
    print(err.get('status') or err.get('message') or 'refused')" 2>/dev/null
}

fetch_keys() {
  local token uid claimed body why
  # An anonymous account, then claim this code's setup slot. The rules hand the
  # keys to a registered device and to nobody else, so a stranger who guessed
  # the URL gets nothing.
  token="$(curl -s -X POST \
    "https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=$FIREBASE_KEY" \
    -H 'content-type: application/json' -d '{"returnSecureToken":true}' \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('idToken',''))" 2>/dev/null)"
  [ -n "$token" ] || { echo "could not reach the study's database to sign in" >&2; return 1; }
  uid="$(printf '%s' "$token" | python3 -c "
import sys, base64, json
part = sys.stdin.read().split('.')[1]
part += '=' * (-len(part) % 4)
print(json.loads(base64.urlsafe_b64decode(part)).get('user_id', ''))" 2>/dev/null)"
  [ -n "$uid" ] || { echo "could not read the sign-in this machine was given" >&2; return 1; }

  # Taken again on every run: this account is thrown away when the script ends,
  # so the slot has to be retakeable or setup works exactly once per code.
  claimed="$(curl -s -X PATCH "$FIRESTORE/participants/$CODE/devices/setup" \
    -H "authorization: Bearer $token" -H 'content-type: application/json' \
    -d "{\"fields\":{\"uid\":{\"stringValue\":\"$uid\"},\"kind\":{\"stringValue\":\"setup\"},\"registeredAt\":{\"integerValue\":\"$(date +%s)000\"}}}")"
  why="$(printf '%s' "$claimed" | say_error)"
  [ -n "$why" ] && echo "registering this machine under $CODE was refused ($why)" >&2

  body="$(curl -s -H "authorization: Bearer $token" \
    "$FIRESTORE/participants/$CODE/secrets/session")"
  why="$(printf '%s' "$body" | say_error)"
  if [ -n "$why" ]; then
    case "$why" in
      NOT_FOUND) echo "$CODE has no keys yet: they were never issued to this code" >&2 ;;
      PERMISSION_DENIED) echo "the database refused to hand $CODE its keys (PERMISSION_DENIED)" >&2 ;;
      *) echo "reading the keys for $CODE failed ($why)" >&2 ;;
    esac
    return 1
  fi
  printf '%s' "$body" | python3 -c "
import sys, json
try:
    fields = json.load(sys.stdin).get('fields', {})
except Exception:
    raise SystemExit(1)
get = lambda name: (fields.get(name) or {}).get('stringValue', '')
print(get('anthropicApiKey'))
print(get('openaiApiKey'))"
}

FETCH_LOG="$(mktemp)"
KEYS="$(fetch_keys 2>"$FETCH_LOG")"
STUDY_ANTHROPIC_KEY="$(printf '%s' "$KEYS" | sed -n 1p)"
STUDY_OPENAI_KEY="$(printf '%s' "$KEYS" | sed -n 2p)"

if [ -z "$STUDY_ANTHROPIC_KEY" ]; then
  bad "no keys reached this machine for $CODE. Tell the researcher before you start;"
  echo  "          everything else is set up, so this is a one-minute fix on their side."
  # Word for word, so the researcher is not diagnosing this from a paraphrase.
  while IFS= read -r line; do
    [ -n "$line" ] && printf '          %s\n' "$line"
  done < "$FETCH_LOG"
  FAILED=1
else
  ok "fetched this session's keys"
fi
rm -f "$FETCH_LOG"

step "Setting up an assistant profile that is not yours"
# The whole config tree lives inside the study folder. Their own ~/.claude is
# neither read nor written, so nothing here can disturb the setup they use for
# their real work, and nothing they have already set can leak into a session.
#
# Authentication goes through apiKeyHelper rather than ANTHROPIC_API_KEY on
# purpose: setting the variable makes Claude Code ask once whether to trust the
# key, which is a prompt with no benefit in the middle of a session.
write_profile() {
  local d="$1" profile="$1/.claude-study"
  mkdir -p "$profile"
  # Only when there is one. An empty key file is worse than none: it makes a
  # workspace look set up, and it is what the check would then be reading.
  # A key already here is left alone, because a fetch that failed on the network
  # is not a reason to take away one that works.
  if [ -n "$STUDY_ANTHROPIC_KEY" ]; then
    printf '%s\n' "$STUDY_ANTHROPIC_KEY" > "$profile/api-key"
    chmod 600 "$profile/api-key"
  fi
  printf '#!/bin/sh\ncat "%s" 2>/dev/null\n' "$profile/api-key" > "$profile/api-key.sh"
  chmod 700 "$profile/api-key.sh"
  HELPER="$profile/api-key.sh" PROFILE="$profile/settings.json" python3 - <<'PROFILE_PY'
import json, os
path = os.environ["PROFILE"]
try:
    with open(path) as handle:
        settings = json.load(handle)
except Exception:
    settings = {}
settings.update({
    "apiKeyHelper": os.environ["HELPER"],
    "model": "claude-sonnet-5",
    # Approve the project's own MCP server ahead of time. Without this, a
    # `.mcp.json` in the workspace sits at "pending approval" and the assistant
    # asks about it in the participant's first run — `claude mcp list` in a fresh
    # config dir says so in as many words. The pilot's participant did answer it,
    # so codoc's tools were live for that session, but it is a trust dialog in
    # the first minute of a timed task, and a participant who declines runs the
    # codoc condition with no codoc tools at all and nothing says so.
    #
    # Set in BOTH profiles even though only one workspace has a `.mcp.json`: the
    # two arms should differ in what they are given, not in how their assistant
    # was configured.
    "enabledMcpjsonServers": ["codoc"],
    # Low, matching the launcher's --effort. The session is timed and the task is
    # small enough that the model one-shots it either way, so deliberation the
    # participant sits and watches buys the study nothing. Set in BOTH places so a
    # participant who types plain `claude` gets the same pace as the launcher.
    "effortLevel": "low",
    "theme": "light",
    "env": {
        # The version is part of the condition. An assistant that upgraded
        # itself between participant three and participant four would be a
        # confound nobody could reconstruct afterwards.
        "DISABLE_AUTOUPDATER": "1",
        "ANTHROPIC_MODEL": "claude-sonnet-5",
    },
})
with open(path, "w") as handle:
    json.dump(settings, handle, indent=2)
    handle.write("\n")
PROFILE_PY
  # The assistant's own first-run questions, answered here rather than in front
  # of a participant. A fresh config directory asks three things before it will
  # draw a prompt: pick a theme, pick a login, and do you trust this folder. The
  # first two are settings we already made; the third is the folder this script
  # just unpacked into the participant's own home, for a study they consented to,
  # and it is the same pre-approval the MCP server above gets and for the same
  # reason. Left unanswered, the trust dialog appears the moment the participant
  # takes over from the recording, which is the worst moment in the session for a
  # question nobody warned them about.
  WS="$d" STATE="$profile/.claude.json" python3 - <<'STATE_PY'
import json, os
path = os.environ["STATE"]
try:
    with open(path) as handle:
        state = json.load(handle)
except Exception:
    state = {}
state.setdefault("theme", "light")
state["hasCompletedOnboarding"] = True
projects = state.setdefault("projects", {})
here = projects.setdefault(os.path.realpath(os.environ["WS"]), {})
here["hasTrustDialogAccepted"] = True
here.setdefault("projectOnboardingSeenCount", 1)
with open(path, "w") as handle:
    json.dump(state, handle, indent=2)
    handle.write("\n")
STATE_PY
}

for name in $PROJECTS; do
  d="$WORK/$name"
  if write_profile "$d"; then
    ok "$name: its own assistant profile"
  else
    bad "could not write $d/.claude-study/settings.json"
    FAILED=1
  fi
  # A launcher, so nothing depends on the participant remembering to set an
  # environment variable. It also unsets any key of their own that happens to be
  # in their shell, which would otherwise be picked up and billed to them.
  # The pace settings below are IDENTICAL in both conditions and live here rather
  # than in either description, so they cannot contaminate the study material: the
  # baseline's description IS its CLAUDE.md, and anything written there would be
  # read by the participant as part of the project.
  #
  # Why bother: the session is 35 minutes and the thing being studied is how a
  # person works with a description, not how long an agent takes. A pilot spent
  # most of the task watching the assistant think, write tests nobody asked for,
  # and fan work out to sub-agents. None of that is what either condition is for.
  #   --effort low          less deliberation per turn; the task is small and the
  #                         model one-shots it either way
  #   --disallowedTools Task  no sub-agents: they add minutes and their work lands
  #                         with no trace in the transcript the analysis reads
  frames_dir="$WORK/replay/frames/$name/$(condition_for "$name")"
  cat > "$d/claude-study" <<LAUNCHER
#!/usr/bin/env bash
# Start the assistant for this study. Use this, not plain \`claude\`.
export CLAUDE_CONFIG_DIR="$d/.claude-study"
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL

# The first turn of the session is the change under review. It is recorded once
# and played back, so everybody reviews the same code and nobody waits forty
# minutes for an agent to type. `agent.py` takes the request, plays it, and
# leaves the session where the assistant picks it up, so every turn after the
# first is the real assistant with the change's own context.
# Only when it is started the way a session starts it, with no arguments. The
# setup check runs \`./claude-study -p ...\` to prove the key works, and without
# this that check would play the session's one recording into the workspace days
# before anybody sat down.
if [ \$# -eq 0 ] && [ ! -f "$d/.claude-study/handover.json" ] && [ -f "$frames_dir/manifest.json" ]; then
  python3 "$WORK/replay/agent.py" play "$d" "$frames_dir" || exit \$?
fi

# Unquoted on purpose: empty expands to no argument at all, and an empty array
# does not on the bash macOS ships.
RESUME=""
[ -f "$d/.claude-study/handover.json" ] && RESUME="--continue"
exec claude \$RESUME \\
  --effort low \\
  --disallowedTools Task \\
  --append-system-prompt "You are helping someone during a short timed session. Work quickly and directly. Make the change they asked for and stop. Do not write new tests, and do not run the test suite unless they ask. Do not explore the codebase beyond what the change needs. Keep replies to a few sentences: say what you changed and any decision you had to make, and skip summaries, plans, and next-step suggestions. If a choice is genuinely open, make a reasonable one and say which you made in one line rather than asking." \\
  "\$@"
LAUNCHER
  chmod +x "$d/claude-study"

  # Told to git when the workspace was unpacked, not here, because the verify
  # step is not the only thing that reads it and the filing step runs first.
  exclude_local "$d"
done

# ----------------------------------------------------------------------- codoc
# codoc, in the two workspaces that have it. Written down rather than inferred:
# left alone codoc reads the environment, and a key in the participant's own
# shell would silently move it onto their account.
# Rewritten rather than appended-to-if-absent. The old "is CODOC_PROVIDER already
# there?" guard meant that a run which fetched no key left OPENAI_API_KEY= in the
# file and every later run said "already configured" and walked past it, so the
# one thing a re-run exists to repair was the one thing it could not.
write_env() {
  ENVFILE="$1" OPENAI="$STUDY_OPENAI_KEY" MODEL="${STUDY_CODOC_MODEL:-gpt-5.6-luna}" python3 - <<'ENV_PY'
import os
path = os.environ['ENVFILE']
ours = ['CODOC_PROVIDER', 'OPENAI_API_KEY', 'CODOC_MODEL',
        'CODOC_TEMPERATURE', 'CODOC_REASONING_EFFORT', 'CODOC_VERBOSITY']
try:
    with open(path) as handle:
        kept = [line for line in handle.read().splitlines()
                if line.split('=', 1)[0].strip() not in ours]
except FileNotFoundError:
    kept = []                      # anything else is a real failure, so it raises
lines = kept + [
    'CODOC_PROVIDER=openai',
    'OPENAI_API_KEY=%s' % os.environ['OPENAI'],
    'CODOC_MODEL=%s' % os.environ['MODEL'],
    'CODOC_TEMPERATURE=',
    'CODOC_REASONING_EFFORT=medium',
    'CODOC_VERBOSITY=medium',
]
with open(path, 'w') as handle:
    handle.write('\n'.join(lines).strip('\n') + '\n')
ENV_PY
}

for name in $CODOC_ARM; do
  ENVFILE="$WORK/$name/.env"
  if [ -z "$STUDY_OPENAI_KEY" ]; then
    # Left alone on purpose. Writing an empty key here is what makes a workspace
    # look configured while codoc has no account.
    bad "$name: no OpenAI key to write, so codoc there has no account"
    FAILED=1
  elif write_env "$ENVFILE"; then
    chmod 600 "$ENVFILE"
    ok "$name: codoc runs on the study's account"
  else
    bad "could not write $ENVFILE"; FAILED=1
  fi
done

step "Checking that both keys work"
# Against the configuration just written, not against the keys as typed. A key
# that is fine but landed in the wrong file fails the session exactly as a bad
# key would, and only this can tell them apart.
if [ -z "$STUDY_ANTHROPIC_KEY" ]; then
  bad "no Anthropic key to check — the line above says why."
  FAILED=1
elif command -v claude >/dev/null 2>&1; then
  # Through the launcher, never plain `claude`. Run bare, it answers on the
  # participant's own claude.ai login and bills them for it, and then reports the
  # study's key as working when the study's key is not what answered.
  SAID="$(cd "$WORK/scribe" && with_deadline 120 ./claude-study -p 'Reply with the single word: ready' | tr -d '[:space:]')"
  case "$SAID" in
    *ready*|*Ready*) ok "Claude Code answers on the study's account" ;;
    *401*|*invalid*|*Invalid*)
      bad "the Anthropic key was refused. Ask the researcher for a new one."; FAILED=1 ;;
    *login*|*Login*)
      # What it says when the profile has no key at all, rather than a bad one.
      bad "the study's key did not reach this workspace, so the assistant has no"
      echo  "          account here. Tell the researcher: it is a fix on their side."
      FAILED=1 ;;
    # A key that is refused does not come back as an error: the CLI retries it
    # quietly, so a run that says nothing for two minutes is what a dead key
    # looks like from here.
    "") bad "Claude Code did not answer within two minutes, which usually means the"
        echo  "          study's key was refused. Tell the researcher before you start."
        FAILED=1 ;;
    *)  bad "Claude Code said: $SAID"; FAILED=1 ;;
  esac
fi

# One HTTPS call rather than a Python client, so this needs nothing installed.
# Asking for the model by name rather than just listing them also catches the
# case where the key is valid but this model is not enabled on the account,
# which would otherwise surface as codoc failing mid-session.
MODEL_WANTED="${STUDY_CODOC_MODEL:-gpt-5.6-luna}"
# Asked only when there is something to ask about. An empty key comes back 401,
# which reads as "the researcher's key is bad" and sends them looking in the one
# place where nothing is wrong.
if [ -z "$STUDY_OPENAI_KEY" ]; then
  bad "no OpenAI key to check — the line above says why."
  FAILED=1
  HTTP=skipped
else
  HTTP="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
    -H "authorization: Bearer $STUDY_OPENAI_KEY" \
    "https://api.openai.com/v1/models/$MODEL_WANTED" 2>/dev/null)"
fi
case "$HTTP" in
  skipped) ;;
  200) ok "the OpenAI key works and $MODEL_WANTED is available" ;;
  401|403) bad "the OpenAI key was refused. Ask the researcher for a new one."; FAILED=1 ;;
  404) bad "$MODEL_WANTED is not available on that OpenAI account. Tell the researcher."; FAILED=1 ;;
  000|"") warn "could not reach OpenAI to check the key. Try again on a better connection."; TODO=1 ;;
  *)   bad "OpenAI answered $HTTP when checking the key. Tell the researcher."; FAILED=1 ;;
esac

step "Keeping this machine's own opening screen"
# The session's first turn draws the assistant's welcome and takes the request
# there, so that welcome has to be this machine's rather than a copy of one.
# Recording it here, with the participant's own profile and their own key, means
# an assistant that changes its layout changes it here too and nobody has to
# notice. A failure is a warning and not a fault: the first turn falls back to a
# plain welcome, and every other part of the session is unaffected.
for name in $PROJECTS; do
  d="$WORK/$name"
  if python3 "$WORK/replay/agent.py" capture "$d" >/dev/null 2>&1 \
     && [ -s "$d/.claude-study/welcome.ansi" ]; then
    ok "$name: the assistant's opening screen"
  else
    warn "$name: could not record the opening screen. Tell the researcher; the"
    echo  "          session still runs, with a plain welcome instead of this"
    echo  "          machine's own."
  fi
done

# ------------------------------------------------------------------- extension
step "Installing the VS Code extension"
VSIX="$(ls "$HERE"/codoc-[0-9]*.vsix 2>/dev/null | head -1)"
LOGGER="$(ls "$HERE"/codoc-study-logger-*.vsix 2>/dev/null | head -1)"
if [ -z "$VSIX" ] || [ -z "$LOGGER" ]; then
  bad "a .vsix is missing from the bundle"
  FAILED=1
elif command -v code >/dev/null 2>&1; then
  code --install-extension "$VSIX" --force >/dev/null 2>&1 \
    && ok "installed $(basename "$VSIX")" || { bad "could not install the extension"; FAILED=1; }
  # The logger goes on for BOTH conditions. It records which files are opened and
  # for how long, never their contents. Read it: it is one short file.
  code --install-extension "$LOGGER" --force >/dev/null 2>&1 \
    && ok "installed $(basename "$LOGGER")" || { bad "could not install the study logger"; FAILED=1; }
else
  warn "install both .vsix files by hand: open VS Code, go to Extensions,"
  echo  "          click the ... menu at the top, and choose Install from VSIX"
  TODO=1
fi

# ---------------------------------------------------------------------- verify
step "Checking that both workspaces work"
# Each project is run once and its tests once, in both arms. The expected output
# is written out rather than "did it exit zero", because a project that quietly
# converted nothing also exits zero.
check_project() {
  local name="$1" command="$2" expect_run="$3" expect_tests="$4"
  local out n
  out="$(cd "$WORK/$name" && eval "$command" 2>&1 | tail -1)"
  case "$out" in
    *"$expect_run"*) ok "$name runs: $out" ;;
    *) bad "$name did not run as expected. It printed: $out"; FAILED=1 ;;
  esac
  n="$(cd "$WORK/$name" && ./.venv/bin/python -m pytest tests/ -q 2>&1 | tail -1)"
  case "$n" in
    *"$expect_tests passed"*) ok "$name passes its tests: $n" ;;
    *) bad "$name tests did not pass. It printed: $n"; FAILED=1 ;;
  esac
  # Put back anything the run generated. A workspace has to look untouched when
  # the participant opens it, or their first `git status` is somebody else's work.
  #
  # This only reaches what the run generated because of the two guards set
  # earlier: the study's own files are in .git/info/exclude so `clean` walks past
  # them, and the tracked files install-hooks rewrote are skip-worktree so
  # `checkout` does not put the build machine's paths back.
  ( cd "$WORK/$name" && git checkout -- . >/dev/null 2>&1
    git clean -fdq -e .venv >/dev/null 2>&1 )
}

check_project scribe './.venv/bin/scribe check fixtures/' "checked 3 documents" 54
check_project tally  './.venv/bin/tally check fixtures/'  "checked 3 statements" 43

# Only the codoc arm has a feature document. Asking the other one for it would
# report a failure on a workspace that is exactly right.
for name in $PROJECTS; do
  [ "$(condition_for "$name")" = codoc ] || continue
  feat="$("$CODOC" status --root "$WORK/$name" 2>/dev/null | head -1)"
  case "$feat" in
    *feature*) ok "$name's feature document is in place: $feat" ;;
    *) bad "$name's feature document is not what we expect. codoc status said: $feat"; FAILED=1 ;;
  esac
done

# ------------------------------------------------------- the instrumentation
step "Checking that the session will actually be recorded"
# Last, and after the workspaces have been put back, because everything above
# writes into these folders and one of those writes used to undo another.
# A machine that installs perfectly and records nothing is the one failure that
# costs a whole session, and it is invisible until the data does not arrive.
#
# Each of these is read the way the thing that depends on it reads it, not by
# asking whether a file exists. The code is checked against the one this run was
# given, so a machine set up twice under two codes is caught here rather than in
# the data.
for name in $PROJECTS; do
  instrument_check "$name" "$CODE"
  if [ "$(condition_for "$name")" = codoc ]; then
    wiring_check "$name" with-mcp
  else
    wiring_check "$name"
  fi
done

step "Result"
if [ "$FAILED" = 0 ] && [ "$TODO" = 0 ]; then
  cat <<EOF
  Everything is ready. Nothing else to do before the session.

  This machine is filed under $CODE ($ORDER, $LANG_CODE).

  Go back to your study page and carry on from where you left it:
    https://codoc-11b10.web.app/participant/?code=$CODE&order=$ORDER&lang=$LANG_CODE

  Your two workspaces are:
    $WORK/scribe
    $WORK/tally

  Please do not open them or look inside them before the session.

  For the experimenter: $CODOC_ARM is the codoc one this time. Nothing has to be
  started by hand there. The launcher starts the daemon itself, once the
  participant has sent their first request.
EOF
else
  echo "  Some things still need doing. See the lines marked fail or todo above,"
  echo "  then run: ./setup.sh --check"
fi
exit "$FAILED"
