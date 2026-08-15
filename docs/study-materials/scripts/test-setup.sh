#!/usr/bin/env bash
# test-setup.sh — checks the part of setup.sh that files a machine under a code.
#
# Only that part. Installing uv, building four Python environments and running
# two test suites takes minutes and needs the network, so what is tested here is
# the step that was missing and would have cost a whole session: writing the
# participant code where the logger reads it.
#
#   ./docs/study-materials/scripts/test-setup.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP="$HERE/setup.sh"
PASS=0; FAIL=0
ok()  { printf '  \033[32mok\033[0m    %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  \033[31mfail\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }

# Run just the settings-writing block, with the surrounding install stubbed out.
# Extracting it keeps the test honest about which lines it covers: if the block
# moves or its markers change, this stops finding it and says so.
write_settings() {
  local work="$1" code="$2" order="$3"
  local body
  body="$(awk '/^step "Filing this machine under/,/^# ------.* wire codoc/' "$SETUP" | sed '$d')"
  [ -n "$body" ] || { echo "could not find the block in setup.sh"; return 99; }
  WORK="$work" CODE="$code" ORDER="$order" bash -c "
    set -uo pipefail
    ok() { :; }; bad() { echo \"bad: \$1\" >&2; }; step() { :; }
    FAILED=0
    $body
    exit \$FAILED"
}

read_key() {
  python3 -c "
import json,sys
try: print(json.load(open(sys.argv[1])).get(sys.argv[2],''))
except Exception: print('')" "$1" "$2"
}

echo
echo "The code reaches every workspace"
TMP="$(mktemp -d)"
for w in hearth hearth-baseline ember ember-baseline; do mkdir -p "$TMP/$w"; done
if write_settings "$TMP" "p-abcdefghjkmn" "codoc-first"; then
  for w in hearth hearth-baseline ember ember-baseline; do
    f="$TMP/$w/.vscode/settings.json"
    [ "$(read_key "$f" codocStudyLogger.participant)" = "p-abcdefghjkmn" ] \
      && ok "$w carries the code" || bad "$w does not carry the code"
  done
  # A baseline folder logged as codoc would move a participant's whole session
  # into the wrong arm of the comparison, silently.
  [ "$(read_key "$TMP/hearth/.vscode/settings.json" codocStudyLogger.condition)" = "codoc" ] \
    && ok "hearth is the codoc condition" || bad "hearth has the wrong condition"
  [ "$(read_key "$TMP/hearth-baseline/.vscode/settings.json" codocStudyLogger.condition)" = "baseline" ] \
    && ok "hearth-baseline is the baseline condition" || bad "hearth-baseline has the wrong condition"
  [ "$(read_key "$TMP/ember-baseline/.vscode/settings.json" codocStudyLogger.condition)" = "baseline" ] \
    && ok "ember-baseline is the baseline condition" || bad "ember-baseline has the wrong condition"
  [ "$(read_key "$TMP/ember/.vscode/settings.json" codocStudyLogger.order)" = "codoc-first" ] \
    && ok "the order is written too" || bad "the order is missing"
else
  bad "the block did not run"
fi
rm -rf "$TMP"

echo
echo "Settings that were already there survive"
TMP="$(mktemp -d)"
for w in hearth hearth-baseline ember ember-baseline; do mkdir -p "$TMP/$w/.vscode"; done
echo '{"editor.fontSize": 15}' > "$TMP/hearth/.vscode/settings.json"
echo 'not json at all'        > "$TMP/ember/.vscode/settings.json"
if write_settings "$TMP" "p-zzzzzzzzzzzz" "baseline-first"; then
  [ "$(read_key "$TMP/hearth/.vscode/settings.json" editor.fontSize)" = "15" ] \
    && ok "an existing setting is kept" || bad "an existing setting was lost"
  [ "$(read_key "$TMP/hearth/.vscode/settings.json" codocStudyLogger.participant)" = "p-zzzzzzzzzzzz" ] \
    && ok "and the code is added beside it" || bad "the code was not added"
  # Unreadable is not a reason to refuse: the participant is on a call, and a
  # setup that stops here would cost more than the file it overwrites.
  [ "$(read_key "$TMP/ember/.vscode/settings.json" codocStudyLogger.participant)" = "p-zzzzzzzzzzzz" ] \
    && ok "a file that is not JSON is replaced rather than fatal" || bad "unreadable JSON stopped it"
else
  bad "the block did not run"
fi
rm -rf "$TMP"

echo
echo "codoc is pinned to the participant's own Claude login"
pin_block() {
  local work="$1"
  local body
  body="$(awk '/^step "Pinning codoc to your own Claude login"/,/^# ------.* wire codoc/' "$SETUP" | sed '$d')"
  [ -n "$body" ] || { echo "could not find the pinning block in setup.sh"; return 99; }
  WORK="$work" bash -c "
    set -uo pipefail
    ok() { :; }; bad() { echo \"bad: \$1\" >&2; }; step() { :; }
    FAILED=0
    $body
    exit \$FAILED"
}
TMP="$(mktemp -d)"
for w in hearth ember hearth-baseline ember-baseline; do mkdir -p "$TMP/$w"; done
if pin_block "$TMP"; then
  grep -q '^CODOC_PROVIDER=claude$' "$TMP/hearth/.env" && ok "hearth is pinned" || bad "hearth is not pinned"
  grep -q '^CODOC_PROVIDER=claude$' "$TMP/ember/.env"  && ok "ember is pinned"  || bad "ember is not pinned"
  # Without this, an OPENAI_API_KEY left in a participant's shell profile moves
  # codoc onto their key: it spends their money unasked, and a stale one breaks
  # codoc partway through the very condition the study is measuring.
  out="$(cd "$TMP/hearth" && python3 -c "
import os,sys
for k in ('CODOC_PROVIDER','OPENAI_API_KEY','ANTHROPIC_API_KEY'): os.environ.pop(k,None)
os.environ['OPENAI_API_KEY']='sk-left-in-their-shell'
from codoc.config import get_llm_config
print(get_llm_config().provider)" 2>/dev/null)"
  case "$out" in
    claude) ok "a stray OPENAI_API_KEY no longer moves it off Claude" ;;
    "")     ok "(codoc not importable here, so the live check was skipped)" ;;
    *)      bad "a stray OPENAI_API_KEY still moved it to $out" ;;
  esac
  # Running setup twice must not stack the line up.
  pin_block "$TMP" >/dev/null
  [ "$(grep -c '^CODOC_PROVIDER=' "$TMP/hearth/.env")" = "1" ] \
    && ok "running setup again does not repeat the line" || bad "the line was added twice"
else
  bad "the pinning block did not run"
fi
rm -rf "$TMP"

echo
echo "A bad code is refused before anything is installed"
# The whole point of asking first. </dev/null makes the prompt fail immediately,
# which is what a non-interactive run does.
out="$(echo "" | bash "$SETUP" "not-a-code" "codoc-first" 2>&1)"; rc=$?
case "$out" in
  *"does not look like a code"*) ok "it says so and asks again" ;;
  *) bad "a bad code was not caught. It printed: $(echo "$out" | head -3)" ;;
esac
[ "$rc" != 0 ] && ok "and it stops" || bad "it carried on with a bad code"

out="$(echo "" | bash "$SETUP" "p-abcdefghjkmn" "sideways" 2>&1)"
case "$out" in
  *"codoc-first or baseline-first"*) ok "a bad order is caught too" ;;
  *) bad "a bad order was not caught" ;;
esac

echo
echo "--check reports whether the code is set"
out="$(HOME="$(mktemp -d)" bash "$SETUP" --check 2>&1)"
case "$out" in
  *"no participant code is set"*) ok "an unfiled machine is named as a failure" ;;
  *) bad "--check did not mention the code" ;;
esac

echo
if [ "$FAIL" = 0 ]; then printf '\033[32m%s passed\033[0m\n' "$PASS"; else printf '\033[31m%s failed, %s passed\033[0m\n' "$FAIL" "$PASS"; fi
exit "$FAIL"
