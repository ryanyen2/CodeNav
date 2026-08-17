#!/usr/bin/env bash
# setup.sh — sets up one participant machine for the codoc study.
#
# This script ships inside the participant bundle, next to the .vsix, the wheel,
# and the four workspace archives. It is safe to run more than once.
#
#   ./setup.sh p-abcdefghjkmn codoc-first   install everything
#   ./setup.sh --check                      only check what is already installed
#
# The researcher gives you the code and the order. Both are on the link they
# send you, and both are needed here: the code is what your session is filed
# against, and without it nothing you do reaches the researcher.
#
# It installs uv, installs the codoc command, unpacks the four workspaces, and
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

# ---------------------------------------------------------------- prerequisites
step "Checking what you already have"

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
  for w in scribe scribe-baseline tally tally-baseline; do
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
  # Checked last and reported loudest. Everything above can be fixed after the
  # session; a session that ran without a code recorded nothing and cannot.
  SEEN="$(python3 -c "
import json,sys
try: print(json.load(open(sys.argv[1])).get('codocStudyLogger.participant',''))
except Exception: print('')" "$WORK/scribe/.vscode/settings.json" 2>/dev/null)"
  if [ -n "$SEEN" ]; then
    ok "this machine is filed under $SEEN"
  else
    bad "no participant code is set, so nothing would reach the researcher."
    echo  "          Run: ./setup.sh <your code> <your order>"
    FAILED=1
  fi
  # The keys, by presence only. Whether they still work is a question for the
  # researcher's console, and checking would spend the study's money every time
  # somebody re-runs this.
  for w in scribe scribe-baseline tally tally-baseline; do
    python3 -c "
import json,sys
try: sys.exit(0 if json.load(open(sys.argv[1]))['env']['ANTHROPIC_API_KEY'] else 1)
except Exception: sys.exit(1)" "$WORK/$w/.claude/settings.json" 2>/dev/null \
      || { bad "$w has no Anthropic key, so the agent there has no account"; FAILED=1; }
  done
  for w in scribe tally; do
    grep -q '^OPENAI_API_KEY=..' "$WORK/$w/.env" 2>/dev/null \
      || { bad "$w has no OpenAI key, so codoc there has no account"; FAILED=1; }
  done
  [ "$FAILED" = 0 ] && ok "both keys are in place in all four workspaces"
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
step "Unpacking the four workspaces into $WORK"
mkdir -p "$WORK"
# A launcher with a fixed path, so nothing later depends on the PATH. `uv tool
# update-shell` only takes effect in a new shell, and the session starts in the
# shell the participant already has open.
ln -sf "$CODOC" "$WORK/codoc"
ok "made a launcher at $WORK/codoc"
for arc in scribe scribe-baseline tally tally-baseline; do
  src="$HERE/$arc.tar.gz"
  [ -f "$src" ] || { bad "$arc.tar.gz is missing from the bundle"; exit 1; }
  name="$(tar tzf "$src" | head -1 | cut -d/ -f1)"
  if [ -d "$WORK/$name" ]; then
    warn "$WORK/$name already exists, leaving it alone"
  else
    tar xzf "$src" -C "$WORK" && ok "unpacked $name"
  fi
done

step "Building a Python environment for each workspace"
for name in scribe scribe-baseline tally tally-baseline; do
  d="$WORK/$name"
  [ -d "$d" ] || { bad "$d is missing"; exit 1; }
  ( cd "$d" \
    && uv venv --python 3.11 --quiet .venv >/dev/null 2>&1 \
    && VIRTUAL_ENV="$d/.venv" uv pip install --quiet -e '.[dev]' >/dev/null 2>&1 ) \
    && ok "built $name" || { bad "could not build $name"; exit 1; }
done

# -------------------------------------------------------------- the study code
step "Filing this machine under $CODE"
# Written per workspace rather than into your own VS Code settings, so nothing
# outside these four folders is touched and you can delete them and be rid of it.
# The condition is set here rather than guessed from the folder name, because a
# folder someone renames would otherwise be counted as the wrong condition.
for name in scribe scribe-baseline tally tally-baseline; do
  d="$WORK/$name/.vscode"
  mkdir -p "$d"
  case "$name" in *-baseline) cond=baseline ;; *) cond=codoc ;; esac
  CODE="$CODE" ORDER="$ORDER" COND="$cond" python3 - "$d/settings.json" <<'PY'
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
})
with open(path, 'w') as f: json.dump(settings, f, indent=2); f.write('\n')
PY
  [ $? = 0 ] && ok "$name is filed under $CODE" \
    || { bad "could not write $d/settings.json"; FAILED=1; }
done

# ------------------------------------------------- wire codoc into the workspace
step "Recording prompts in all four workspaces"
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
  for name in scribe scribe-baseline tally tally-baseline; do
    python3 "$HOOK" "$WORK/$name" >/dev/null 2>&1 \
      && ok "$name: prompts will be recorded" \
      || { bad "could not install the prompt hook in $name"; FAILED=1; }
  done
else
  warn "no prompt hook in this bundle, so prompts will not be recorded"
  TODO=1
fi

step "Connecting codoc to the two codoc workspaces"
# This rewrites .claude/settings.json and .mcp.json with the paths on YOUR machine.
# The archive ships without them on purpose, because they hold absolute paths.
for name in scribe tally; do
  ( cd "$WORK/$name" && "$CODOC" install-hooks >/dev/null 2>&1 ) \
    && ok "$name: wrote .claude/settings.json and .mcp.json" \
    || { bad "codoc install-hooks failed in $WORK/$name. Run it there to see why."; FAILED=1; }
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

fetch_keys() {
  local token uid
  # An anonymous account, then claim this code's setup slot. The rules hand the
  # keys to a registered device and to nobody else, so a stranger who guessed
  # the URL gets nothing.
  token="$(curl -s -X POST \
    "https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=$FIREBASE_KEY" \
    -H 'content-type: application/json' -d '{"returnSecureToken":true}' \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('idToken',''))" 2>/dev/null)"
  [ -n "$token" ] || return 1
  uid="$(printf '%s' "$token" | python3 -c "
import sys, base64, json
part = sys.stdin.read().split('.')[1]
part += '=' * (-len(part) % 4)
print(json.loads(base64.urlsafe_b64decode(part)).get('user_id', ''))" 2>/dev/null)"
  [ -n "$uid" ] || return 1

  curl -s -o /dev/null -X PATCH "$FIRESTORE/participants/$CODE/devices/setup" \
    -H "authorization: Bearer $token" -H 'content-type: application/json' \
    -d "{\"fields\":{\"uid\":{\"stringValue\":\"$uid\"},\"kind\":{\"stringValue\":\"setup\"},\"registeredAt\":{\"integerValue\":\"$(date +%s)000\"}}}"

  curl -s -H "authorization: Bearer $token" \
    "$FIRESTORE/participants/$CODE/secrets/session" \
    | python3 -c "
import sys, json
try:
    fields = json.load(sys.stdin).get('fields', {})
except Exception:
    raise SystemExit(1)
get = lambda name: (fields.get(name) or {}).get('stringValue', '')
print(get('anthropicApiKey'))
print(get('openaiApiKey'))"
}

KEYS="$(fetch_keys 2>/dev/null)"
STUDY_ANTHROPIC_KEY="$(printf '%s' "$KEYS" | sed -n 1p)"
STUDY_OPENAI_KEY="$(printf '%s' "$KEYS" | sed -n 2p)"

if [ -z "$STUDY_ANTHROPIC_KEY" ]; then
  bad "no keys have been issued for $CODE yet. Tell the researcher before you start;"
  echo  "          everything else is set up, so this is a one-minute fix on their side."
  FAILED=1
else
  ok "fetched this session's keys"
fi

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
  printf '%s\n' "$STUDY_ANTHROPIC_KEY" > "$profile/api-key"
  chmod 600 "$profile/api-key"
  printf '#!/bin/sh\ncat "%s"\n' "$profile/api-key" > "$profile/api-key.sh"
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
    "effortLevel": "medium",
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
}

for name in scribe scribe-baseline tally tally-baseline; do
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
  cat > "$d/claude-study" <<LAUNCHER
#!/usr/bin/env bash
# Start the assistant for this study. Use this, not plain \`claude\`.
export CLAUDE_CONFIG_DIR="$d/.claude-study"
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL
exec claude "\$@"
LAUNCHER
  chmod +x "$d/claude-study"

  if [ -d "$d/.git" ]; then
    for pat in '.env' '.claude-study/' 'claude-study'; do
      grep -qxF "$pat" "$d/.git/info/exclude" 2>/dev/null \
        || printf '%s\n' "$pat" >> "$d/.git/info/exclude"
    done
  fi
done

# codoc, in the two workspaces that have it. Written down rather than inferred:
# left alone codoc reads the environment, and a key in the participant's own
# shell would silently move it onto their account.
for name in scribe tally; do
  ENVFILE="$WORK/$name/.env"
  if grep -q '^CODOC_PROVIDER=' "$ENVFILE" 2>/dev/null; then
    ok "$name: codoc is already configured"
  else
    {
      printf 'CODOC_PROVIDER=openai\n'
      printf 'OPENAI_API_KEY=%s\n' "$STUDY_OPENAI_KEY"
      printf 'CODOC_MODEL=%s\n' "${STUDY_CODOC_MODEL:-gpt-5.6-luna}"
      printf 'CODOC_TEMPERATURE=\n'
      printf 'CODOC_REASONING_EFFORT=medium\n'
      printf 'CODOC_VERBOSITY=medium\n'
    } >> "$ENVFILE" \
      && { chmod 600 "$ENVFILE"; ok "$name: codoc runs on the study's account"; } \
      || { bad "could not write $ENVFILE"; FAILED=1; }
  fi
done

step "Checking that both keys work"
# Against the configuration just written, not against the keys as typed. A key
# that is fine but landed in the wrong file fails the session exactly as a bad
# key would, and only this can tell them apart.
if command -v claude >/dev/null 2>&1; then
  SAID="$(cd "$WORK/scribe" && with_deadline 120 claude -p 'Reply with the single word: ready' | tr -d '[:space:]')"
  case "$SAID" in
    *ready*|*Ready*) ok "Claude Code answers on the study's account" ;;
    *401*|*invalid*|*Invalid*)
      bad "the Anthropic key was refused. Ask the researcher for a new one."; FAILED=1 ;;
    "") bad "Claude Code did not answer within two minutes. Tell the researcher."; FAILED=1 ;;
    *)  bad "Claude Code said: $SAID"; FAILED=1 ;;
  esac
fi

# One HTTPS call rather than a Python client, so this needs nothing installed.
# Asking for the model by name rather than just listing them also catches the
# case where the key is valid but this model is not enabled on the account,
# which would otherwise surface as codoc failing mid-session.
MODEL_WANTED="${STUDY_CODOC_MODEL:-gpt-5.6-luna}"
HTTP="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
  -H "authorization: Bearer $STUDY_OPENAI_KEY" \
  "https://api.openai.com/v1/models/$MODEL_WANTED" 2>/dev/null)"
case "$HTTP" in
  200) ok "the OpenAI key works and $MODEL_WANTED is available" ;;
  401|403) bad "the OpenAI key was refused. Ask the researcher for a new one."; FAILED=1 ;;
  404) bad "$MODEL_WANTED is not available on that OpenAI account. Tell the researcher."; FAILED=1 ;;
  000|"") warn "could not reach OpenAI to check the key. Try again on a better connection."; TODO=1 ;;
  *)   bad "OpenAI answered $HTTP when checking the key. Tell the researcher."; FAILED=1 ;;
esac

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
step "Checking that all four workspaces work"
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
  ( cd "$WORK/$name" && git checkout -- . >/dev/null 2>&1
    git clean -fdq -e .venv >/dev/null 2>&1 )
}

for name in scribe scribe-baseline; do
  check_project "$name" './.venv/bin/scribe check fixtures/' "checked 3 documents" 54
done
for name in tally tally-baseline; do
  check_project "$name" './.venv/bin/tally check fixtures/' "checked 3 statements" 43
done

for name in scribe tally; do
  feat="$("$CODOC" status --root "$WORK/$name" 2>/dev/null | head -1)"
  case "$feat" in
    *feature*) ok "$name's feature document is in place: $feat" ;;
    *) bad "$name's feature document is not what we expect. codoc status said: $feat"; FAILED=1 ;;
  esac
done

step "Result"
if [ "$FAILED" = 0 ] && [ "$TODO" = 0 ]; then
  cat <<EOF
  Everything is ready. Nothing else to do before the session.

  This machine is filed under $CODE ($ORDER).

  Go back to your study page and carry on from where you left it:
    https://codoc-11b10.web.app/participant/?code=$CODE&order=$ORDER

  Your four workspaces are:
    $WORK/scribe            $WORK/scribe-baseline
    $WORK/tally             $WORK/tally-baseline

  Please do not open them or look inside them before the session.

  For the experimenter: start the daemon during a codoc condition with
    cd $WORK/scribe && $WORK/codoc watch      (or $WORK/tally)
EOF
else
  echo "  Some things still need doing. See the lines marked fail or todo above,"
  echo "  then run: ./setup.sh --check"
fi
exit "$FAILED"
