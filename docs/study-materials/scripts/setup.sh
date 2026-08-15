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

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '  \033[33mtodo\033[0m  %s\n' "$1"; }
bad()  { printf '  \033[31mfail\033[0m  %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

FAILED=0
TODO=0

# ------------------------------------------------------------- who you are here
# Asked first, so a typo costs a second rather than a whole install. The code is
# not secret and identifies nobody. It is how your work is filed.
if [ "$CHECK_ONLY" = 0 ]; then
  while ! printf '%s' "$CODE" | grep -Eq '^p-[a-z0-9]+$'; do
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
else
  warn "Claude Code is not installed. Install it, then sign in:"
  echo  "          curl -fsSL https://claude.ai/install.sh | bash"
  echo  "          claude    (sign in when it asks, then type /exit)"
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
  for w in hearth hearth-baseline ember ember-baseline; do
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
except Exception: print('')" "$WORK/hearth/.vscode/settings.json" 2>/dev/null)"
  if [ -n "$SEEN" ]; then
    ok "this machine is filed under $SEEN"
  else
    bad "no participant code is set, so nothing would reach the researcher."
    echo  "          Run: ./setup.sh <your code> <your order>"
    FAILED=1
  fi
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
for arc in hearth-codoc hearth-baseline ember-codoc ember-baseline; do
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
for name in hearth hearth-baseline ember ember-baseline; do
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
for name in hearth hearth-baseline ember ember-baseline; do
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
  for name in hearth hearth-baseline ember ember-baseline; do
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
for name in hearth ember; do
  ( cd "$WORK/$name" && "$CODOC" install-hooks >/dev/null 2>&1 ) \
    && ok "$name: wrote .claude/settings.json and .mcp.json" \
    || { bad "codoc install-hooks failed in $WORK/$name. Run it there to see why."; FAILED=1; }
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
step "Checking that all four workspaces work"
for name in hearth hearth-baseline; do
  out="$(cd "$WORK/$name" && ./.venv/bin/hearth build 2>&1 | tail -1)"
  case "$out" in
    *"aggregates rebuilt"*) ok "$name builds: $out" ;;
    *) bad "$name did not build as expected. It printed: $out"; FAILED=1 ;;
  esac
  n="$(cd "$WORK/$name" && ./.venv/bin/python -m pytest tests/ -q 2>&1 | tail -1)"
  case "$n" in
    *"233 passed"*) ok "$name passes its tests: $n" ;;
    *) bad "$name tests did not pass. It printed: $n"; FAILED=1 ;;
  esac
  ( cd "$WORK/$name" && rm -rf _site .hearth )
done
for name in ember ember-baseline; do
  out="$(cd "$WORK/$name" && ./.venv/bin/python -m ember refresh 2>&1 | tail -1)"
  case "$out" in
    *"36 new"*) ok "$name reads its feeds: $out" ;;
    *) bad "$name did not read its feeds as expected. It printed: $out"; FAILED=1 ;;
  esac
  n="$(cd "$WORK/$name" && ./.venv/bin/python -m pytest tests/ -q 2>&1 | tail -1)"
  case "$n" in
    *"171 passed"*) ok "$name passes its tests: $n" ;;
    *) bad "$name tests did not pass. It printed: $n"; FAILED=1 ;;
  esac
  ( cd "$WORK/$name" && rm -rf _digest .ember )
done

for name in hearth ember; do
  feat="$("$CODOC" status --root "$WORK/$name" 2>/dev/null | head -1)"
  case "$feat" in
    *"25 features"*) ok "$name's feature document is in place: $feat" ;;
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
    $WORK/hearth            $WORK/hearth-baseline
    $WORK/ember             $WORK/ember-baseline

  Please do not open them or look inside them before the session.

  For the experimenter: start the daemon during a codoc condition with
    cd $WORK/hearth && $WORK/codoc watch      (or $WORK/ember)
EOF
else
  echo "  Some things still need doing. See the lines marked fail or todo above,"
  echo "  then run: ./setup.sh --check"
fi
exit "$FAILED"
