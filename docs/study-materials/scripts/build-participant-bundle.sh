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

# The extension is BUILT at the top of this script and PACKAGED a few lines later,
# so an edit made in between ships as the previous build under the new version
# number, and nothing says so. It happened once: a fix was verified by grepping the
# vsix for a string the older build also contained. Compare the bytes instead.
PACKED="$(mktemp -d)"
( cd "$PACKED" && unzip -qo "$VSIX" extension/dist/extension.js )
if ! cmp -s "$PACKED/extension/dist/extension.js" "$EXT/dist/extension.js"; then
  echo "the packaged extension is not the one just built."
  echo "Something changed under the build. Run this again."
  rm -rf "$PACKED"; exit 1
fi
rm -rf "$PACKED"

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
run node "$MAT/logger/test-snapshot.js"
run node --test "$MAT/logger/test-composition.js"
run python3 "$MAT/replay/test_replay.py"
run python3 -m pytest -q "$MAT/replay/test_agent.py"
# The extension reads every recorded frame the way the webview does. It skips
# when no recording is present, so it costs nothing until there is one.
( cd "$REPO/vscode-codoc" && run npx vitest run src/test/recorded-frames.test.ts )
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

# The recorded agent session, which is the change every participant reviews.
#
# Both frame sets have to be here and both have to replay into the state the
# recording ended in. A bundle that shipped one condition's frames would give one
# arm a change to review and the other nothing, and a bundle whose frames replay
# to a different end state would rate participants against a change they never
# saw. Missing frames are a warning rather than a failure only until the first
# recording exists; after that this is the gate that keeps them honest.
mkdir -p "$STAGE/replay"
cp "$MAT"/replay/play.py "$MAT"/replay/record.py "$MAT"/replay/agent.py "$STAGE/replay/"
chmod +x "$STAGE"/replay/play.py "$STAGE"/replay/agent.py
MISSING=0
for project in scribe tally; do
  for arm in codoc baseline; do
    frames="$MAT/replay/frames/$project/$arm"
    if [ -f "$frames/manifest.json" ]; then
      mkdir -p "$STAGE/replay/frames/$project"
      cp -R "$frames" "$STAGE/replay/frames/$project/$arm"
      scratch="$(mktemp -d)"
      if python3 "$MAT/replay/play.py" "$scratch" "$frames" --speed 1000 \
           --no-transcript >/dev/null 2>&1; then
        echo "  replay ok      $project/$arm"
      else
        echo "  replay BROKEN  $project/$arm"; rm -rf "$scratch"; exit 1
      fi
      rm -rf "$scratch"
    else
      echo "  no recording yet for $project/$arm"
      MISSING=1
    fi
  done
done
if [ "$MISSING" = 1 ]; then
  echo "  (record them with replay/record-session.sh before a real session)"
fi

# What this bundle is, in the bundle. setup.sh prints it before anything else, so
# a participant pasting their output says which download it came from without
# being asked, and a stale copy is one line rather than an afternoon.
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%MZ)" \
  "$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)" \
  > "$STAGE/bundle.stamp"

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
