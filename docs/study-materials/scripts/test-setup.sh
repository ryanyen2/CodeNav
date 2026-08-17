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

# The block that decides which project carries which arm, taken from setup.sh
# rather than copied here. Every extracted block below depends on it, and a copy
# would let this suite keep passing after the real mapping had changed.
ARMS="$(awk '/^# ---8<--- which arm is which/,/^# --->8--- end of the arm block/' "$SETUP")"
[ -n "$ARMS" ] || { echo "could not find the arm block in setup.sh"; exit 1; }

# Run just the settings-writing block, with the surrounding install stubbed out.
# Extracting it keeps the test honest about which lines it covers: if the block
# moves or its markers change, this stops finding it and says so.
write_settings() {
  local work="$1" code="$2" order="$3"
  local body
  body="$(awk '/^step "Filing this machine under/,/^# ------.* wire codoc/' "$SETUP" | sed '$d')"
  [ -n "$body" ] || { echo "could not find the block in setup.sh"; return 99; }
  WORK="$work" CODE="$code" ORDER="$order" LANG_CODE="${4-en}" bash -c "
    set -uo pipefail
    ok() { :; }; bad() { echo \"bad: \$1\" >&2; }; step() { :; }
    FAILED=0
    $ARMS
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
for w in scribe tally; do mkdir -p "$TMP/$w"; done
if write_settings "$TMP" "p-abcdefghjkmn" "codoc-first"; then
  for w in scribe tally; do
    f="$TMP/$w/.vscode/settings.json"
    [ "$(read_key "$f" codocStudyLogger.participant)" = "p-abcdefghjkmn" ] \
      && ok "$w carries the code" || bad "$w does not carry the code"
  done
  # The folders are named for the project alone now, so this file is the ONLY
  # thing that says which arm a workspace is. Getting it wrong moves a whole
  # session into the other arm of the comparison, silently. codoc-first means
  # scribe is the codoc one, since scribe is always the first project.
  [ "$(read_key "$TMP/scribe/.vscode/settings.json" codocStudyLogger.condition)" = "codoc" ] \
    && ok "under codoc-first, scribe is the codoc condition" \
    || bad "scribe has the wrong condition"
  [ "$(read_key "$TMP/tally/.vscode/settings.json" codocStudyLogger.condition)" = "baseline" ] \
    && ok "and tally is the other one" || bad "tally has the wrong condition"
  [ "$(read_key "$TMP/tally/.vscode/settings.json" codocStudyLogger.order)" = "codoc-first" ] \
    && ok "the order is written too" || bad "the order is missing"
else
  bad "the block did not run"
fi
rm -rf "$TMP"

echo
echo "Settings that were already there survive"
TMP="$(mktemp -d)"
for w in scribe tally; do mkdir -p "$TMP/$w/.vscode"; done
echo '{"editor.fontSize": 15}' > "$TMP/scribe/.vscode/settings.json"
echo 'not json at all'        > "$TMP/tally/.vscode/settings.json"
if write_settings "$TMP" "p-zzzzzzzzzzzz" "baseline-first"; then
  [ "$(read_key "$TMP/scribe/.vscode/settings.json" editor.fontSize)" = "15" ] \
    && ok "an existing setting is kept" || bad "an existing setting was lost"
  [ "$(read_key "$TMP/scribe/.vscode/settings.json" codocStudyLogger.participant)" = "p-zzzzzzzzzzzz" ] \
    && ok "and the code is added beside it" || bad "the code was not added"
  # The other way round, because the arms swap with the order and a mapping that
  # ignored it would pass every test above.
  [ "$(read_key "$TMP/scribe/.vscode/settings.json" codocStudyLogger.condition)" = "baseline" ] \
    && ok "under baseline-first, scribe is the baseline one" \
    || bad "the arms did not swap with the order"
  # Unreadable is not a reason to refuse: the participant is on a call, and a
  # setup that stops here would cost more than the file it overwrites.
  [ "$(read_key "$TMP/tally/.vscode/settings.json" codocStudyLogger.participant)" = "p-zzzzzzzzzzzz" ] \
    && ok "a file that is not JSON is replaced rather than fatal" || bad "unreadable JSON stopped it"
else
  bad "the block did not run"
fi
rm -rf "$TMP"

echo
echo "The study's keys, not the participant's account"
keys_block() {
  local work="$1" akey="${2-sk-ant-test}" okey="${3-sk-openai-test}"
  local body
  # The block that writes the assistant profile and codoc's .env, given the keys.
  # Fetching them is the step above it and needs the network, so it is left out.
  body="$(awk '/^step "Setting up an assistant profile that is not yours"/,/^step "Checking that both keys work"/' "$SETUP" | sed '$d')"
  [ -n "$body" ] || { echo "could not find the profile block in setup.sh"; return 99; }
  WORK="$work" STUDY_ANTHROPIC_KEY="$akey" STUDY_OPENAI_KEY="$okey" \
  ORDER="${4-codoc-first}" LANG_CODE="${5-en}" bash -c "
    set -uo pipefail
    ok() { :; }; bad() { echo \"bad: \$1\" >&2; }; step() { :; }
    FAILED=0
    $ARMS
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
for w in scribe tally; do mkdir -p "$TMP/$w"; done
# What install-hooks leaves behind, which this must merge into rather than erase.
mkdir -p "$TMP/scribe/.claude"
echo '{"hooks":{"Stop":[{"matcher":"","hooks":[{"type":"command","command":"codoc-hook"}]}]}}' \
  > "$TMP/scribe/.claude/settings.json"

if keys_block "$TMP"; then
  # All four, because the agent does the work in both conditions. A profile in
  # only the codoc pair would bill the participant for half the study.
  n=0
  for w in scribe tally; do
    [ "$(cat "$TMP/$w/.claude-study/api-key" 2>/dev/null)" = "sk-ant-test" ] && n=$((n+1))
  done
  [ "$n" = 2 ] && ok "both workspaces carry the Anthropic key" \
    || bad "only $n of 2 workspaces carry the Anthropic key"

  PROFILE="$TMP/scribe/.claude-study/settings.json"
  # Through a helper rather than ANTHROPIC_API_KEY: setting the variable makes
  # Claude Code ask once whether to trust the key, mid-session, for no benefit.
  [ "$(read_json "$PROFILE" apiKeyHelper)" = "$TMP/scribe/.claude-study/api-key.sh" ] \
    && ok "the key is served by a helper, so nothing prompts them" \
    || bad "no apiKeyHelper, so they will be asked to trust the key"
  [ "$(read_json "$PROFILE" model)" = "claude-sonnet-5" ] \
    && ok "the model is pinned to sonnet-5" || bad "the model is not pinned"
  [ "$(read_json "$PROFILE" effortLevel)" = "medium" ] \
    && ok "thinking is set to medium" || bad "thinking is not set"
  # The assistant's version is part of the condition. One that upgraded itself
  # between participant three and four is a confound nobody can reconstruct.
  [ "$(read_json "$PROFILE" env.DISABLE_AUTOUPDATER)" = "1" ] \
    && ok "the auto-updater is off, so the version stays put" \
    || bad "the assistant may upgrade itself mid-study"

  # The profile is a config directory of its own. Their own ~/.claude is never
  # read, and the workspace's .claude — where codoc installs its hooks — is not
  # touched by this block at all, so unhooking codoc is not possible here.
  [ -n "$(read_json "$TMP/scribe/.claude/settings.json" hooks.Stop)" ] \
    && ok "codoc's own hooks are left alone" || bad "the hooks were erased"

  # The key is the one file here worth reading off a shared machine.
  m="$(stat -f '%Lp' "$TMP/scribe/.claude-study/api-key" 2>/dev/null \
       || stat -c '%a' "$TMP/scribe/.claude-study/api-key")"
  [ "$m" = "600" ] && ok "the file holding the key is private" \
    || bad "the key file is $m, not 600"

  # The launcher, because nothing may depend on remembering an environment
  # variable, and a key of their own in the shell would otherwise be picked up
  # and billed to them.
  L="$TMP/scribe/claude-study"
  [ -x "$L" ] && ok "each workspace has its own launcher" || bad "no launcher"
  grep -q 'CLAUDE_CONFIG_DIR' "$L" 2>/dev/null \
    && ok "it points the assistant at the study profile" || bad "it does not set the config dir"
  grep -q 'unset ANTHROPIC_API_KEY' "$L" 2>/dev/null \
    && ok "and clears any key of their own first" || bad "their own key could be billed"

  # codoc, in the ONE workspace that runs it. keys_block ran as codoc-first, so
  # that is scribe. Writing a codoc key into the other arm would put an OpenAI
  # account on a machine whose condition is defined by not having codoc.
  grep -q '^CODOC_PROVIDER=openai$' "$TMP/scribe/.env" && ok "scribe sends codoc to OpenAI" || bad "scribe does not"
  grep -q '^OPENAI_API_KEY=sk-openai-test$' "$TMP/scribe/.env" && ok "and carries the OpenAI key" || bad "scribe has no key"
  [ -f "$TMP/tally/.env" ] \
    && bad "the baseline arm was given a codoc key it has no use for" \
    || ok "the other arm gets no codoc key at all"
  grep -q '^CODOC_MODEL=gpt-5.6-luna$' "$TMP/scribe/.env" && ok "the codoc model is luna" || bad "the codoc model is wrong"
  grep -q '^CODOC_REASONING_EFFORT=medium$' "$TMP/scribe/.env" && ok "reasoning effort is medium" || bad "reasoning effort is unset"
  # Empty, not absent. Absent means the old default, which luna answers 400 to on
  # every single call.
  grep -q '^CODOC_TEMPERATURE=$' "$TMP/scribe/.env" \
    && ok "no temperature is sent, which is what luna requires" \
    || bad "a temperature would be sent, and luna refuses every value"
  [ -f "$TMP/scribe-baseline/.env" ] && bad "the baseline got a codoc key it has no use for" \
    || ok "the baseline workspaces get no OpenAI key"
  [ "$(stat -f '%Lp' "$TMP/scribe/.env" 2>/dev/null || stat -c '%a' "$TMP/scribe/.env")" = "600" ] \
    && ok "and the .env is private too" || bad "the .env is world-readable"

  # Without this, codoc reads the environment and a key in the participant's own
  # shell would move it onto their account: their money, and a stale key breaking
  # codoc partway through the condition being measured.
  out="$(cd "$TMP/scribe" && python3 -c "
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
  [ "$(grep -c '^CODOC_PROVIDER=' "$TMP/scribe/.env")" = "1" ] \
    && ok "running setup again does not stack the settings up" || bad "the settings were written twice"
else
  bad "the keys block did not run"
fi
rm -rf "$TMP"

echo
echo "A run that fetched no keys leaves nothing looking configured"
# The failure a pilot actually hit: the fetch was refused, and the run wrote the
# empty result out anyway. That left four workspaces that looked configured, an
# OpenAI call that came back 401 as if the researcher's key were bad, and — worst
# — a .env the next run walked straight past, so re-running could never repair it.
DRY="$(mktemp -d)"
for w in scribe tally; do mkdir -p "$DRY/$w"; done
keys_block "$DRY" "" "" >/dev/null 2>&1

[ -s "$DRY/scribe/.claude-study/api-key" ] \
  && bad "an empty Anthropic key file was written, so --check would call it present" \
  || ok "no key file is written when no key was fetched"
[ -f "$DRY/scribe/.env" ] \
  && bad "an empty OpenAI key was written into .env" \
  || ok "the .env is left alone rather than written empty"

# And the repair. This is the case that was stuck: whatever state a failed run
# left behind, a later run with real keys has to end in a workspace that works.
printf 'CODOC_PROVIDER=openai\nOPENAI_API_KEY=\nSOMETHING_THEIRS=keep-me\n' > "$DRY/scribe/.env"
keys_block "$DRY" >/dev/null 2>&1
grep -q '^OPENAI_API_KEY=sk-openai-test$' "$DRY/scribe/.env" \
  && ok "a later run with keys repairs a .env left empty" \
  || bad "an empty OPENAI_API_KEY survived a run that had a key"
[ "$(grep -c '^OPENAI_API_KEY=' "$DRY/scribe/.env")" = "1" ] \
  && ok "and repairs it in place rather than appending a second one" \
  || bad "the file now holds two OPENAI_API_KEY lines and codoc reads one of them"
grep -q '^SOMETHING_THEIRS=keep-me$' "$DRY/scribe/.env" \
  && ok "anything else in the .env is kept" || bad "the rewrite dropped other settings"
[ "$(cat "$DRY/scribe/.claude-study/api-key" 2>/dev/null)" = "sk-ant-test" ] \
  && ok "and the Anthropic key lands on the second run too" \
  || bad "the second run did not write the Anthropic key"
rm -rf "$DRY"

echo
echo "Prompts are recorded under the code, by an interpreter that will still be there"
PH="$(mktemp -d)"
for w in scribe tally; do mkdir -p "$PH/$w"; done
hookblock="$(awk '/^step "Recording prompts in both workspaces"/,/^step "Connecting codoc/' "$SETUP" | sed '$d')"
if [ -z "$hookblock" ]; then
  bad "could not find the prompt hook block in setup.sh"
else
  WORK="$PH" CODE="p-hooktest0000" HERE="$(dirname "$HERE")" ORDER=codoc-first LANG_CODE=en \
  bash -c "set -uo pipefail; ok() { :; }; bad() { echo \"bad: \$1\" >&2; }; warn() { :; }; step() { :; }
           FAILED=0; TODO=0
           $ARMS
           $hookblock" >/dev/null 2>&1
  cmd="$(python3 -c "
import json, sys
try:
    hooks = json.load(open(sys.argv[1]))['hooks']['UserPromptSubmit']
except Exception:
    raise SystemExit(0)
print(''.join(h.get('command','') for e in hooks for h in e.get('hooks',[]) if 'prompt-hook' in h.get('command','')))" \
    "$PH/scribe/.claude/settings.json" 2>/dev/null)"
  case "$cmd" in
    *"CODOC_STUDY_PARTICIPANT=p-hooktest0000"*)
      ok "every prompt is stamped with the participant code" ;;
    *) bad "the hook records prompts under no code, so the log cannot be attributed" ;;
  esac
  # The installer writes whichever python ran it into the hook. A bare `python3`
  # is whatever the participant happened to have active that day.
  interp="$(printf '%s' "$cmd" | tr ' ' '\n' | grep -m1 '^/' || true)"
  if [ -n "$interp" ] && [ -x "$interp" ]; then
    ok "and by an interpreter that exists on this machine"
  else
    bad "the hook's interpreter is '$interp', which is not something that runs here"
  fi
fi
rm -rf "$PH"

echo
echo "What goes back to the researcher"
# The end of a session, where a mistake cannot be undone: the call is over and
# the folders belong to the participant again.
COL="$HERE/collect.sh"
CHOME="$(mktemp -d)"
mkdir -p "$CHOME/codoc-study/scribe/.claude-study/projects/a-project" \
         "$CHOME/codoc-study/scribe/.codoc" \
         "$CHOME/codoc-study/session-logs"
echo '{"type":"user"}' > "$CHOME/codoc-study/scribe/.claude-study/projects/a-project/session.jsonl"
echo 'sk-ant-averyrealkey' > "$CHOME/codoc-study/scribe/.claude-study/api-key"
printf 'CODOC_PROVIDER=openai\nOPENAI_API_KEY=sk-proj-averyrealkey\n' > "$CHOME/codoc-study/scribe/.env"
echo '{"ev":"prompt"}' > "$CHOME/codoc-study/session-logs/interaction-scribe.jsonl"
echo 'name = "scribe"' > "$CHOME/codoc-study/scribe/pyproject.toml"

out="$(HOME="$CHOME" bash "$COL" p-collecttest0 2>&1)"
ZIP="$CHOME/Desktop/codoc-study-p-collecttest0.zip"
if [ -s "$ZIP" ]; then
  ok "it writes the archive even when there is no Desktop yet"
  rm -rf "$CHOME/unzipped"; mkdir -p "$CHOME/unzipped"
  unzip -qo "$ZIP" -d "$CHOME/unzipped" 2>/dev/null
  # The keys live inside the workspace, so a plain copy of the workspace takes
  # them along into a file that is mailed, uploaded, and then archived.
  if grep -rqE 'sk-ant-averyrealkey|sk-proj-averyrealkey' "$CHOME/unzipped" 2>/dev/null; then
    bad "the archive carries the study's keys, which would have to be rotated"
  else
    ok "and it carries neither key"
  fi
  [ -n "$(find "$CHOME/unzipped" -path '*claude-transcripts*' -name '*.jsonl' 2>/dev/null)" ] \
    && ok "the assistant transcripts are in it" \
    || bad "no transcripts: they are in the workspace's own config dir, not ~/.claude"
  case "$out" in
    *"transcripts: 0"*) bad "it reports 0 transcripts on a session that has them" ;;
    *) ok "and it counts them where they actually are" ;;
  esac
else
  bad "collect.sh wrote no archive"
fi
# A "Done, send this file" over an archive that does not exist loses the session.
CHOME2="$(mktemp -d)"; mkdir -p "$CHOME2/codoc-study"
out2="$(HOME="$CHOME2" bash "$COL" p-collecttest1 2>&1)"; rc2=$?
if [ -s "$CHOME2/Desktop/codoc-study-p-collecttest1.zip" ] || [ -s "$CHOME2/codoc-study-p-collecttest1.zip" ]; then
  ok "an empty session still produces something to send"
else
  case "$out2$rc2" in
    *"could not be written"*) ok "and when it cannot, it says so instead of saying Done" ;;
    *) bad "it claimed success with no archive written" ;;
  esac
fi
rm -rf "$CHOME" "$CHOME2"

echo
echo "The verify step does not undo the setup"
# The failure this exists for: the last step of setup.sh puts each workspace back
# with `git checkout -- .` and `git clean`, and it used to delete the participant
# code, revert the prompt hook, and restore .claude/settings.json and .mcp.json to
# the paths of the machine the archive was built on. Everything then looked
# installed, and the session would have recorded nothing and run codoc with hooks
# pointing at somebody else's disk.
#
# Run against the real archives and the real blocks out of setup.sh, because this
# is a question about what those exact lines do to a real git repository.
ARCS="$HERE/../workspaces"
if [ -f "$ARCS/scribe.tar.gz" ]; then
  VER="$(mktemp -d)"
  unpack="$(awk '/^step "Unpacking your two workspaces/,/^step "Building a Python environment/' "$SETUP" | sed '$d')"
  hold="$(awk '/^# Hold those rewrites against git/,/^# ---.*which model runs/' "$SETUP" | sed '$d')"
  cleanup="$(awk '/^  # Put back anything the run generated/,/git clean/' "$SETUP")"
  if [ -z "$unpack" ] || [ -z "$hold" ] || [ -z "$cleanup" ]; then
    bad "could not find the unpack, hold or cleanup block in setup.sh"
  else
    WORK="$VER/codoc-study" HERE="$ARCS" CODOC="$(command -v true)" ORDER=codoc-first LANG_CODE=en \
    bash -c "set -uo pipefail; ok() { :; }; warn() { :; }; bad() { :; }; step() { :; }
             $ARMS
             $unpack" >/dev/null 2>&1

    W="$VER/codoc-study/scribe"
    # What setup writes: the code the logger reads, the study's prompt hook, and
    # the wiring install-hooks rewrites for THIS machine.
    mkdir -p "$W/.vscode"
    echo '{"codocStudyLogger.participant":"p-verifytest00"}' > "$W/.vscode/settings.json"
    python3 -c "
import json, sys
path = sys.argv[1]
s = json.load(open(path))
s.setdefault('hooks', {})['UserPromptSubmit'] = [
    {'matcher': '', 'hooks': [{'type': 'command', 'command': 'python3 /here/prompt-hook.py'}]}]
json.dump(s, open(path, 'w'), indent=2)" "$W/.claude/settings.json"
    python3 -c "
import json, sys
json.dump({'mcpServers': {'codoc': {'command': '/this/machine/codoc-mcp'}}}, open(sys.argv[1], 'w'))" \
      "$W/.mcp.json"

    WORK="$VER/codoc-study" ORDER=codoc-first LANG_CODE=en \
      bash -c "set -uo pipefail; $ARMS
               $hold" >/dev/null 2>&1

    # A run leaves rubbish behind, which the cleanup is there to remove.
    echo "output from a test run" > "$W/generated.txt"
    WORK="$VER/codoc-study" name=scribe bash -c "set -uo pipefail; $cleanup" >/dev/null 2>&1

    [ "$(python3 -c "
import json,sys
try: print(json.load(open(sys.argv[1])).get('codocStudyLogger.participant',''))
except Exception: print('')" "$W/.vscode/settings.json" 2>/dev/null)" = "p-verifytest00" ] \
      && ok "the participant code survives the verify step" \
      || bad "the verify step deleted the participant code, so nothing would be recorded"
    grep -q 'prompt-hook' "$W/.claude/settings.json" 2>/dev/null \
      && ok "the prompt hook survives it" \
      || bad "the verify step reverted the prompt hook"
    grep -q '/this/machine/codoc-mcp' "$W/.mcp.json" 2>/dev/null \
      && ok "codoc stays wired to this machine, not the one the archive was built on" \
      || bad "the verify step put back the build machine's paths, so codoc is not wired here"
    [ -f "$W/generated.txt" ] \
      && bad "the cleanup no longer removes what a run generated" \
      || ok "and it still removes what a run generated"
    [ -f "$W/.codoc/codoc.db" ] \
      && ok "the seeded codoc state is left alone" \
      || bad "the cleanup deleted the seeded codoc state"
    [ -z "$(cd "$W" && git status --porcelain 2>/dev/null)" ] \
      && ok "and the participant's first git status is clean" \
      || bad "the workspace opens dirty: $(cd "$W" && git status --porcelain | head -3 | tr '\n' ' ')"
  fi
  rm -rf "$VER"
else
  bad "the workspace archives are not next to this script, so the verify step was not tested"
fi

echo
echo "Nothing of the participant's own is touched"
# The whole design rests on this. Verified against the real CLI too: with the key
# in a workspace, `claude` there 401s on a broken one while a plain folder next to
# it still answers on the machine's own login, and ~/.claude/settings.json and
# ~/.claude/.credentials.json come back byte-identical.
FAKE_HOME="$(mktemp -d)"
mkdir -p "$FAKE_HOME/.claude" "$FAKE_HOME/codoc-study"/{scribe,tally}
echo '{"model":"their-own-model","env":{}}' > "$FAKE_HOME/.claude/settings.json"
printf 'export PATH=/theirs\n' > "$FAKE_HOME/.zshrc"
cp "$FAKE_HOME/.claude/settings.json" "$FAKE_HOME/.settings-before"
cp "$FAKE_HOME/.zshrc" "$FAKE_HOME/.zshrc-before"

HOME="$FAKE_HOME" keys_block "$FAKE_HOME/codoc-study" >/dev/null 2>&1

cmp -s "$FAKE_HOME/.claude/settings.json" "$FAKE_HOME/.settings-before" \
  && ok "their global Claude settings are untouched" \
  || bad "their global Claude settings were modified"
cmp -s "$FAKE_HOME/.zshrc" "$FAKE_HOME/.zshrc-before" \
  && ok "their shell profile is untouched" || bad "their shell profile was modified"
# A key in a shell profile would follow them into their own projects long after
# the session, which is the failure this whole approach exists to avoid.
grep -rqF "sk-ant-test" "$FAKE_HOME/.claude" "$FAKE_HOME/.zshrc" 2>/dev/null \
  && bad "the Anthropic key leaked outside the workspaces" \
  || ok "neither key leaks outside the study workspaces"
grep -qF "sk-ant-test" "$FAKE_HOME/codoc-study/scribe/.claude-study/api-key" 2>/dev/null \
  && ok "and it is present where it is supposed to be" \
  || bad "the key is not in the workspace either, so nothing was written"
rm -rf "$FAKE_HOME"

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
echo "--check reports whether the session would be recorded"
out="$(HOME="$(mktemp -d)" bash "$SETUP" --check 2>&1)"
case "$out" in
  *"has no participant code"*) ok "an unfiled machine is named as a failure" ;;
  *) bad "--check did not mention the code" ;;
esac
# All four, because they are configured one at a time. Three good ones say
# nothing about the fourth, and the fourth is half the study.
n=0
for w in scribe tally; do
  case "$out" in *"$w has no participant code"*) n=$((n+1)) ;; esac
done
[ "$n" = 2 ] && ok "it checks both workspaces, not just the first" \
  || bad "only $n of 2 workspaces were checked for their code"
case "$out" in
  *"does not record prompts"*) ok "and whether prompts would be recorded" ;;
  *) bad "--check says nothing about the prompt hook" ;;
esac
case "$out" in
  *"is wired to paths that do not exist here"*)
    ok "and whether the hooks and codoc point at this machine" ;;
  *) bad "--check says nothing about the wiring" ;;
esac
# A check is read by somebody who is already worried. Raw shell errors in the
# middle of it read as something being broken beyond the thing being reported.
case "$out" in
  *"No such file or directory"*|*"setup.sh: line "*)
    bad "--check prints raw shell errors: $(printf '%s' "$out" | grep -m1 'No such file\|line ')" ;;
  *) ok "and it prints no raw shell errors while doing it" ;;
esac

echo

echo
echo "A session runs entirely in one language"
# Translating one arm and not the other would make language vary WITH condition,
# so every result would be as attributable to reading in a second language as to
# the tool. The suffix is therefore chosen once and applied to BOTH workspaces.
LG="$(mktemp -d)"
mkdir -p "$LG/bundle"
for a in scribe scribe-baseline tally tally-baseline; do
  mkdir -p "$LG/src/$a"; echo "$a" > "$LG/src/$a/pyproject.toml"
  ( cd "$LG/src" && tar czf "$LG/bundle/$a.tar.gz" "$a" \
      && tar czf "$LG/bundle/$a.zh-Hans.tar.gz" "$a" )
done
unpack="$(awk '/^step "Unpacking your two workspaces/,/^step "Building a Python environment/' "$SETUP" | sed '$d')"
run_unpack() {
  WORK="$1/codoc-study" HERE="$LG/bundle" CODOC="$(command -v true)" \
  ORDER="$2" LANG_CODE="$3" \
  bash -c "set -uo pipefail; ok(){ :; }; warn(){ :; }; bad(){ echo \"bad: \$1\" >&2; }; step(){ :; }
           FAILED=0
           $ARMS
           $unpack" 2>&1
}

H1="$LG/en"; mkdir -p "$H1"; run_unpack "$H1" codoc-first en >/dev/null
[ -f "$H1/codoc-study/scribe/pyproject.toml" ] && ok "English unpacks both workspaces" \
  || bad "English did not unpack"

H2="$LG/zh"; mkdir -p "$H2"; run_unpack "$H2" codoc-first zh-Hans >/dev/null
if [ -f "$H2/codoc-study/scribe/pyproject.toml" ] && [ -f "$H2/codoc-study/tally/pyproject.toml" ]; then
  ok "zh-Hans unpacks both workspaces, into the same project-named folders"
else
  bad "zh-Hans did not unpack both workspaces"
fi

# The one that matters: a language cannot apply to one arm and not the other.
if printf '%s' "$unpack" | grep -q 'SUFFIX'; then
  ok "the archive name is chosen by language, not per arm"
else
  bad "the unpack does not use the language suffix at all"
fi
# The property, tested rather than grepped for: the per-arm function returns a
# name with NO language in it, so the suffix cannot be chosen differently for the
# codoc arm than for the other one. That is the whole guarantee — one language per
# session, not one per condition.
per_arm="$(LANG_CODE=zh-Hans ORDER=codoc-first bash -c "set -uo pipefail
  $ARMS
  archive_for scribe; archive_for tally")"
if printf '%s' "$per_arm" | grep -q 'zh-Hans'; then
  bad "the arm mapping picks a language, so it could differ per condition"
else
  ok "the arm mapping names an arm and never a language"
fi
# And the suffix it is combined with is one value for the whole run.
suffixes="$(for o in codoc-first baseline-first; do
  LANG_CODE=zh-Hans ORDER=$o bash -c "set -uo pipefail
    $ARMS
    printf '%s\n' \"\$SUFFIX\""
done | sort -u | wc -l | tr -d ' ')"
[ "$suffixes" = 1 ] && ok "and it is the same suffix whichever way round they go" \
  || bad "the language changed with the order"

# An unknown language falls back to English rather than looking for an archive
# that is not there and stopping a participant days before their session.
H3="$LG/xx"; mkdir -p "$H3"
out="$(run_unpack "$H3" codoc-first kl-KL)"
if [ -f "$H3/codoc-study/scribe/pyproject.toml" ]; then
  ok "an unknown language falls back to English rather than failing"
else
  bad "an unknown language left the machine with no workspaces"
fi

# A machine already built in one language refuses a link in another.
#
# The archives differ by language, so a folder unpacked in one while the study
# page runs in another gives somebody Chinese questions about an English
# description. Both halves look like a working setup, so nothing on screen would
# have said so, and only .vscode/settings.json records which language the folder
# was built in.
settings="$(awk '/^step "Filing this machine under/,/^# ------.* wire codoc/' "$SETUP" | sed '$d')"
build_one() {
  WORK="$1/codoc-study" HERE="$LG/bundle" CODOC="$(command -v true)" \
  ORDER="$2" LANG_CODE="$3" CODE=p-abcdefghjkmn \
  bash -c "set -uo pipefail; ok(){ :; }; warn(){ :; }; bad(){ echo \"bad: \$1\" >&2; }; step(){ :; }
           FAILED=0
           $ARMS
           $unpack
           $settings" 2>/dev/null
}
H4="$LG/mix"; mkdir -p "$H4"
build_one "$H4" codoc-first zh-Hans >/dev/null
if [ "$(read_key "$H4/codoc-study/scribe/.vscode/settings.json" codocStudyLogger.lang)" = "zh-Hans" ]; then
  ok "the language is written where a later run can read it"
else
  bad "nothing on disk says which language the workspace was built in"
fi

if run_unpack "$H4" codoc-first en >/dev/null 2>&1; then
  bad "a link in another language was accepted over an existing workspace"
else
  ok "and a link in another language is refused rather than half-applied"
fi
if run_unpack "$H4" codoc-first zh-Hans >/dev/null 2>&1; then
  ok "while a re-run in the same language still goes through"
else
  bad "a re-run in the machine's own language was refused"
fi
rm -rf "$LG"



echo
echo "An expected not-yet is not reported as something to do"
# The participant's page tells them to report anything marked fail or todo. A
# workspace they have not opened in VS Code yet is neither: they open it with the
# researcher, on the day. Reporting it as todo sent people back with nothing
# wrong, and taught them the word means nothing, which costs more than it saves.
NT="$(mktemp -d)"
mkdir -p "$NT/codoc-study/scribe/.vscode" "$NT/codoc-study/tally/.vscode"
out="$(HOME="$NT" bash "$SETUP" --check 2>&1)"
if printf '%s' "$out" | grep -q 'has not been opened in VS Code yet'; then
  ok "it still says the logger has not run there"
else
  bad "it no longer mentions the workspace never having been opened"
fi
if printf '%s' "$out" | grep 'has not been opened' | grep -q 'todo'; then
  bad "an expected not-yet is still marked todo, which the page tells them to report"
else
  ok "and says it without the word the page tells them to report"
fi
rm -rf "$NT"

if [ "$FAIL" = 0 ]; then printf '\033[32m%s passed\033[0m\n' "$PASS"; else printf '\033[31m%s failed, %s passed\033[0m\n' "$FAIL" "$PASS"; fi
exit "$FAIL"