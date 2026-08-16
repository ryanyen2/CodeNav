#!/usr/bin/env bash
# build-participant-bundle.sh — makes the zip you send to a participant.
#
# Run this from anywhere in the codoc repo. It builds a fresh VS Code extension,
# pulls the matching codoc wheel out of it, and puts both next to the workspace
# archives, the setup script, and the instructions.
#
#   ./docs/study-materials/scripts/build-participant-bundle.sh
#   → dist/codoc-study-bundle.zip
#
# You need node, npm, and uv on this machine. Participants do not.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MAT="$REPO/docs/study-materials"
EXT="$REPO/vscode-codoc"
OUT="$REPO/dist"
STAGE="$OUT/codoc-study-bundle"

for cmd in node npm uv zip; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "missing: $cmd"; exit 1; }
done

mkdir -p "$OUT"
LOG="$OUT/build.log"

echo "Building the VS Code extension."
run() { "$@" >>"$LOG" 2>&1 || { echo "failed: $*"; echo "see $LOG"; tail -20 "$LOG"; exit 1; }; }
: > "$LOG"
( cd "$EXT" && run npm run build && run npm run bundle-wheel )
( cd "$EXT" && run npx --yes @vscode/vsce package --allow-missing-repository --out "$OUT/" )

VSIX="$(ls -t "$OUT"/codoc-*.vsix | head -1)"
echo "Built $(basename "$VSIX")"

# The study logger. A separate extension on purpose: it installs in BOTH
# conditions, so navigation is measured the same way in each. Its tests run here
# because a file sorted into the wrong surface changes a reported number.
echo "Checking that setup.sh files a machine under its code."
# The one step whose failure is invisible: a session that ran without a code
# looks normal on the participant's screen and arrives nowhere.
run bash "$MAT/scripts/test-setup.sh"

echo "Building the study logger."
run node "$MAT/logger/test-classify.js"
run node "$MAT/logger/test-extension.js"
run node --test "$MAT/logger/test-composition.js"
( cd "$MAT/logger" && run npx --yes @vscode/vsce package \
    --allow-missing-repository --skip-license --out "$OUT/" )
LOGGER="$(ls -t "$OUT"/codoc-study-logger-*.vsix | head -1)"
echo "Built $(basename "$LOGGER")"

rm -rf "$STAGE"; mkdir -p "$STAGE"
cp "$VSIX" "$LOGGER" "$STAGE/"
cp "$EXT"/bundled/codoc-*.whl "$STAGE/"
cp "$MAT"/workspaces/hearth-codoc.tar.gz "$STAGE/"
cp "$MAT"/workspaces/ember-codoc.tar.gz "$STAGE/"

# Repack the baseline with the skill text from this repo, so the copy under
# docs/study-materials/baseline/ is the only place the skill is ever edited.
echo "Installing the doc-maintenance skill into the baseline workspace."
for base in hearth-baseline ember-baseline; do
  TMP="$(mktemp -d)"
  tar xzf "$MAT/workspaces/$base.tar.gz" -C "$TMP"
  mkdir -p "$TMP/$base/.claude/skills/doc-maintenance"
  cp "$MAT/baseline/doc-maintenance/SKILL.md" \
     "$TMP/$base/.claude/skills/doc-maintenance/SKILL.md"
  ( cd "$TMP" && COPYFILE_DISABLE=1 tar czf "$STAGE/$base.tar.gz" "$base" )
  rm -rf "$TMP"
done

mkdir -p "$STAGE/logger"
cp "$MAT"/logger/install-prompt-hook.py "$MAT"/logger/prompt-hook.py "$STAGE/logger/"
cp "$MAT"/scripts/setup.sh "$STAGE/"
cp "$MAT"/scripts/session-log.sh "$STAGE/"
cp "$MAT"/scripts/collect.sh "$STAGE/"
cp "$MAT"/participant-before-the-session.md "$STAGE/README.md"
chmod +x "$STAGE"/setup.sh "$STAGE"/session-log.sh "$STAGE"/collect.sh

( cd "$OUT" && rm -f codoc-study-bundle.zip && zip -qr codoc-study-bundle.zip codoc-study-bundle )
rm -rf "$STAGE"

echo
echo "Bundle: $OUT/codoc-study-bundle.zip"
echo "Contents:"
unzip -l "$OUT/codoc-study-bundle.zip" | sed -n '4,20p'
echo
echo "Send this zip with the participant's link, which is on their page in the"
echo "dashboard. The consent form and the questionnaires are on that link."
