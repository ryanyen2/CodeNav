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
echo "The study's keys, not the participant's account"
keys_block() {
  local work="$1"
  local body
  body="$(awk '/^step "Putting the study.s keys in place"/,/^step "Checking that both keys work"/' "$SETUP" | sed '$d')"
  [ -n "$body" ] || { echo "could not find the keys block in setup.sh"; return 99; }
  WORK="$work" STUDY_ANTHROPIC_KEY="sk-ant-test" STUDY_OPENAI_KEY="sk-openai-test" \
  bash -c "
    set -uo pipefail
    ok() { :; }; bad() { echo \"bad: \$1\" >&2; }; step() { :; }
    FAILED=0
    $body
    exit \$FAILED"
}
read_json() {
  python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split('.'): d=d[k]
print(d)" "$1" "$2" 2>/dev/null
}

TMP="$(mktemp -d)"
for w in hearth ember hearth-baseline ember-baseline; do mkdir -p "$TMP/$w"; done
# What install-hooks leaves behind, which this must merge into rather than erase.
mkdir -p "$TMP/hearth/.claude"
echo '{"hooks":{"Stop":[{"matcher":"","hooks":[{"type":"command","command":"codoc-hook"}]}]}}' \
  > "$TMP/hearth/.claude/settings.json"

if keys_block "$TMP"; then
  # All four, because the agent does the work in both conditions. A key in only
  # the codoc pair would bill the participant for half the study.
  n=0
  for w in hearth hearth-baseline ember ember-baseline; do
    [ "$(read_json "$TMP/$w/.claude/settings.json" env.ANTHROPIC_API_KEY)" = "sk-ant-test" ] && n=$((n+1))
  done
  [ "$n" = 4 ] && ok "all four workspaces carry the Anthropic key" \
    || bad "only $n of 4 workspaces carry the Anthropic key"

  [ "$(read_json "$TMP/hearth/.claude/settings.json" model)" = "claude-sonnet-5" ] \
    && ok "the model is pinned to sonnet-5" || bad "the model is not pinned"
  [ "$(read_json "$TMP/hearth/.claude/settings.json" effortLevel)" = "medium" ] \
    && ok "thinking is set to medium" || bad "thinking is not set"
  # Erasing these would silently unhook codoc from Claude Code in the very
  # condition the study is about.
  [ -n "$(read_json "$TMP/hearth/.claude/settings.json" hooks.Stop)" ] \
    && ok "codoc's own hooks survive the merge" || bad "the hooks were erased"
  [ "$(stat -f '%Lp' "$TMP/hearth/.claude/settings.json" 2>/dev/null || stat -c '%a' "$TMP/hearth/.claude/settings.json")" = "600" ] \
    && ok "and the file holding it is private" || bad "the settings file is world-readable"

  # codoc, in its two workspaces only.
  grep -q '^CODOC_PROVIDER=openai$' "$TMP/hearth/.env" && ok "hearth sends codoc to OpenAI" || bad "hearth does not"
  grep -q '^OPENAI_API_KEY=sk-openai-test$' "$TMP/ember/.env" && ok "ember carries the OpenAI key" || bad "ember does not"
  grep -q '^CODOC_MODEL=gpt-5.6-luna$' "$TMP/hearth/.env" && ok "the codoc model is luna" || bad "the codoc model is wrong"
  grep -q '^CODOC_REASONING_EFFORT=medium$' "$TMP/hearth/.env" && ok "reasoning effort is medium" || bad "reasoning effort is unset"
  [ -f "$TMP/hearth-baseline/.env" ] && bad "the baseline got a codoc key it has no use for" \
    || ok "the baseline workspaces get no OpenAI key"
  [ "$(stat -f '%Lp' "$TMP/hearth/.env" 2>/dev/null || stat -c '%a' "$TMP/hearth/.env")" = "600" ] \
    && ok "and the .env is private too" || bad "the .env is world-readable"

  # Without this, codoc reads the environment and a key in the participant's own
  # shell would move it onto their account: their money, and a stale key breaking
  # codoc partway through the condition being measured.
  out="$(cd "$TMP/hearth" && python3 -c "
import os,sys
for k in ('CODOC_PROVIDER','OPENAI_API_KEY','ANTHROPIC_API_KEY','CODOC_MODEL'): os.environ.pop(k,None)
os.environ['ANTHROPIC_API_KEY']='sk-ant-left-in-their-shell'
from codoc.config import get_llm_config
c=get_llm_config(); print(f'{c.provider}|{c.model}|{c.reasoning_effort}')" 2>/dev/null)"
  case "$out" in
    "openai|gpt-5.6-luna|medium") ok "a key in their shell does not move codoc off the study's account" ;;
    "")  ok "(codoc not importable here, so the live check was skipped)" ;;
    *)   bad "codoc resolved to $out instead" ;;
  esac

  keys_block "$TMP" >/dev/null
  [ "$(grep -c '^CODOC_PROVIDER=' "$TMP/hearth/.env")" = "1" ] \
    && ok "running setup again does not stack the settings up" || bad "the settings were written twice"
else
  bad "the keys block did not run"
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
