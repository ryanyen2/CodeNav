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
# --no-dependencies: the extension is esbuild-bundled into dist/, so vsce's npm
# dependency traversal would only add ~11MB of inert node_modules — and in a
# clean export (git archive) that traversal returns an empty file list and the
# package step dies with "entrypoint missing". Skip it in both places.
( cd "$EXT" && run npx --yes @vscode/vsce package --allow-missing-repository --no-dependencies --out "$OUT/" )

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
run node "$MAT/logger/test-scope.js"
run node "$MAT/logger/test-transcript.js"
run node --test "$MAT/logger/test-composition.js"
( cd "$MAT/logger" && run npx --yes @vscode/vsce package \
    --allow-missing-repository --skip-license --no-dependencies --out "$OUT/" )
LOGGER="$(ls -t "$OUT"/codoc-study-logger-*.vsix | head -1)"
echo "Built $(basename "$LOGGER")"

rm -rf "$STAGE"; mkdir -p "$STAGE"
cp "$VSIX" "$LOGGER" "$STAGE/"
cp "$EXT"/bundled/codoc-*.whl "$STAGE/"
# Every language's archives. A session runs entirely in ONE of them: the page,
# the questions, the task cards and BOTH descriptions. Translating one arm and
# not the other would make language vary with condition, so the languages are
# whole sets and setup picks a set, never a file.
#
# '' is English, whose archives carry no suffix.
LANGS=('' '.zh-Hans')
for suffix in "${LANGS[@]}"; do
  cp "$MAT/workspaces/scribe$suffix.tar.gz" "$STAGE/"
  cp "$MAT/workspaces/tally$suffix.tar.gz" "$STAGE/"
done

# Repack each baseline with the skill text from this repo, so the copy under
# docs/study-materials/baseline/ is the only place the skill is ever edited.
#
# The skill is the instruction the baseline agent works from, so it is translated
# with everything else. An English skill inside a Chinese workspace would have the
# agent writing English back into a description the participant is reading in
# Chinese — the same confound, one layer down.
# The two arms must SAY THE SAME THING. If they drift apart the study stops
# comparing two ways of working and starts comparing two documents — and the
# drift is invisible, because each description reads fine on its own. This ran
# for months as a script nobody ran; it is a build gate now, so a bundle that
# would have shipped mismatched arms cannot be built at all.
echo "Checking both conditions carry the same description."
for pair in "scribe:scribe-baseline" "tally:tally-baseline"; do
  codoc_ws="${pair%%:*}"; base_ws="${pair#*:}"
  for suffix in "${LANGS[@]}"; do
    TMPC="$(mktemp -d)"; TMPB="$(mktemp -d)"
    tar xzf "$MAT/workspaces/$codoc_ws$suffix.tar.gz" -C "$TMPC"
    tar xzf "$MAT/workspaces/$base_ws$suffix.tar.gz" -C "$TMPB"
    if ! python3 "$MAT/scoring/check-descriptions-match.py" \
        "$TMPC/$codoc_ws" "$TMPB/$base_ws" >>"$LOG" 2>&1; then
      echo "  the two arms of $codoc_ws${suffix:-} do not say the same thing"
      echo "  see $LOG"
      rm -rf "$TMPC" "$TMPB"
      exit 1
    fi
    rm -rf "$TMPC" "$TMPB"
  done
  echo "  $codoc_ws: both arms match, in every language"
done

echo "Installing the doc-maintenance skill into the baseline workspaces."
for suffix in "${LANGS[@]}"; do
  skill="$MAT/baseline/doc-maintenance/SKILL${suffix}.md"
  [ -f "$skill" ] || { echo "  no doc-maintenance skill for '${suffix:-en}'"; exit 1; }
  for base in scribe-baseline tally-baseline; do
    TMP="$(mktemp -d)"
    tar xzf "$MAT/workspaces/$base$suffix.tar.gz" -C "$TMP"
    mkdir -p "$TMP/$base/.claude/skills/doc-maintenance"
    cp "$skill" "$TMP/$base/.claude/skills/doc-maintenance/SKILL.md"
    ( cd "$TMP" && COPYFILE_DISABLE=1 tar czf "$STAGE/$base$suffix.tar.gz" "$base" )
    rm -rf "$TMP"
  done
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

# Straight into the website's static files, so deploying the site and publishing
# the bundle are one action. It used to be emailed separately, which meant a
# rebuilt bundle reached nobody who had already been sent the old one, and there
# was nothing on either side to say so. The download button on the participant's
# setup page points at exactly this path.
SITE="$REPO/study-app/bundles"
mkdir -p "$SITE"
cp "$OUT/codoc-study-bundle.zip" "$SITE/codoc-study-bundle.zip"

echo
echo "Bundle: $OUT/codoc-study-bundle.zip"
echo "        $SITE/codoc-study-bundle.zip  (deployed with the site)"
echo "Contents:"
unzip -l "$OUT/codoc-study-bundle.zip" | sed -n '4,20p'
echo
echo "Publish it:  cd study-app && npm run build && npx firebase deploy --only hosting"
echo "Participants download it from their own study page. Send them the link"
echo "only — it is on their page in the dashboard, and it carries their code."
