#!/usr/bin/env bash
# build-workspaces.sh — the four participant workspaces, from the source in
# docs/study-materials/projects/.
#
#   ./docs/study-materials/scripts/build-workspaces.sh          both projects
#   ./docs/study-materials/scripts/build-workspaces.sh scribe   just one
#
# Four workspaces come out of two projects: scribe, scribe-baseline, tally,
# tally-baseline. The two arms hold identical code, identical tests and identical
# git history. The ONLY difference is where the description lives — the codoc arm
# has a feature tree, the baseline arm has CLAUDE.md — and both are written from
# the same source file, so neither arm can be told more than the other.
#
# The codoc arm's tree is seeded by `codoc init`, which costs an LLM call per
# file. That step needs a key and is skipped with --no-seed if you only want to
# check the shape.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="$REPO/docs/study-materials/projects"
OUT="$REPO/docs/study-materials/workspaces"
SEED=1
PROJECTS=(scribe tally)

for arg in "$@"; do
  case "$arg" in
    --no-seed) SEED=0 ;;
    scribe|tally) PROJECTS=("$arg") ;;
    *) echo "unknown argument: $arg"; exit 2 ;;
  esac
done

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mfail\033[0m  %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

command -v uv >/dev/null 2>&1 || { bad "uv is needed to build the environments"; exit 1; }
mkdir -p "$OUT"

# The history both arms get. Twelve commits, because a participant reading `git
# log` is one of the ways they can learn why something is the way it is, and an
# arm with a squashed history has a channel the other one does not.
commit_history() {
  local dir="$1" project="$2"
  ( cd "$dir"
    git init -q
    git config user.name "The $project authors"
    git config user.email "$project@example.invalid"
    # Committed in the order the program grew, so the log reads as a story.
    local order=(lines.py text.py blocks.py paragraphs.py furniture.py notes.py convert.py cli.py)
    [ "$project" = "tally" ] && order=(rows.py money.py categories.py months.py dedupe.py recurring.py summary.py cli.py)
    git add "$project/__init__.py" pyproject.toml README.md >/dev/null 2>&1
    git commit -qm "Start $project" >/dev/null
    for f in "${order[@]}"; do
      [ -f "$project/$f" ] || continue
      git add "$project/$f" >/dev/null 2>&1
      git commit -qm "Add ${f%.py}" >/dev/null
    done
    git add fixtures >/dev/null 2>&1 && git commit -qm "Add the sample documents" >/dev/null 2>&1
    git add tests >/dev/null 2>&1 && git commit -qm "Add the tests" >/dev/null 2>&1
    # The description, last and always, in BOTH arms. Skipping it when there is
    # nothing to add would leave one arm a commit shorter than the other, and a
    # participant reading `git log` would have a slightly different amount of
    # evidence depending on which condition they were in. --allow-empty is what
    # keeps the two histories the same length whatever the arm holds.
    git add -A >/dev/null 2>&1
    git commit -q --allow-empty -m "Write down what it does and why" >/dev/null 2>&1
  )
}

build_one() {
  local project="$1" arm="$2"      # arm is "codoc" or "baseline"
  # Seeding happens inside here, before the history is written, so the tree lands
  # in the same commit the baseline's CLAUDE.md lands in.
  local name="$project"
  [ "$arm" = "baseline" ] && name="$project-baseline"
  local dir="$OUT/.stage/$name"

  rm -rf "$dir"; mkdir -p "$dir"
  # Everything except the study's own files. STUDY.md holds the answer key.
  ( cd "$SRC/$project" && tar cf - \
      --exclude STUDY.md --exclude ABOUT.md --exclude CLAUDE.md \
      --exclude .venv --exclude __pycache__ --exclude '*.egg-info' \
      --exclude .pytest_cache --exclude '*.md.orig' . ) | ( cd "$dir" && tar xf - )

  if [ "$arm" = "baseline" ]; then
    cp "$SRC/$project/CLAUDE.md" "$dir/CLAUDE.md"
    mkdir -p "$dir/.claude/skills/doc-maintenance"
    cp "$REPO/docs/study-materials/baseline/doc-maintenance/SKILL.md" \
       "$dir/.claude/skills/doc-maintenance/SKILL.md" 2>/dev/null || true
  fi

  if [ "$arm" = "codoc" ] && [ "$SEED" = 1 ]; then
    seed_codoc "$project" "$dir" || return 1
  fi

  commit_history "$dir" "$project"

  ( cd "$dir" \
    && uv venv --python 3.11 --quiet .venv >/dev/null 2>&1 \
    && VIRTUAL_ENV="$dir/.venv" uv pip install --quiet -e '.[dev]' >/dev/null 2>&1 ) \
    || { bad "$name: could not build its environment"; return 1; }

  local passed
  passed="$( cd "$dir" && ./.venv/bin/python -m pytest tests/ -q 2>&1 | tail -1 )"
  case "$passed" in
    *passed*) ok "$name: $passed" ;;
    *) bad "$name: tests did not pass — $passed"; return 1 ;;
  esac

  # The environment and anything generated do not travel; setup.sh rebuilds them
  # on the participant's machine, and a venv holding absolute paths would not
  # work there anyway.
  rm -rf "$dir/.venv" "$dir/__pycache__" "$dir/.pytest_cache" "$dir"/*.egg-info
  find "$dir" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
  find "$dir" -name "*.md" -path "*/fixtures/*" -delete 2>/dev/null || true
  return 0
}

seed_codoc() {
  local project="$1" dir="$2"
  local codoc log
  codoc="$(command -v codoc || echo "$REPO/.venv/bin/codoc")"
  [ -x "$codoc" ] || { bad "codoc not found; run with --no-seed or install it"; return 1; }

  # Kept, not discarded. This used to be `>/dev/null 2>&1`, and init's own
  # warning that some files could not be described went into it — so a workspace
  # whose five policy modules were bare filenames with no prose was packed,
  # shipped, and reported as "ok · 21 features".
  log="$dir/.seed.log"
  ( cd "$dir" && "$codoc" init >"$log" 2>&1 ) \
    || { bad "$project: codoc init failed"; tail -20 "$log"; return 1; }
  if grep -q "could not be described" "$log"; then
    bad "$project: init could not describe every file"
    grep -A 6 "could not be described" "$log" | sed 's/^/      /'
    return 1
  fi

  # And checked against the store rather than against the log, because the two
  # can disagree: a file can be described and still yield a node with nothing in
  # it. A description is the whole reason the codoc arm exists — a blank one is
  # the arm having LESS than the baseline's CLAUDE.md on exactly the point the
  # study asks about.
  local blanks
  blanks="$( cd "$dir" && python3 -c "
import sqlite3, sys
rows = sqlite3.connect('.codoc/codoc.db').execute(
    'select title, description from features').fetchall()
print('|'.join(t for t, d in rows if not (d or '').strip()))" 2>/dev/null )"
  if [ -n "$blanks" ]; then
    bad "$project: these features have no description: ${blanks//|/, }"
    return 1
  fi

  local features
  features="$( cd "$dir" && "$codoc" status 2>/dev/null | head -1 )"
  ok "$project: $features, every feature described"
  # The realize queue is a side effect of bootstrap and would hand the first
  # participant a pending job nobody asked for.
  rm -f "$dir/.codoc/realize.md" "$dir/.codoc/realize.json" "$log"
}

for project in "${PROJECTS[@]}"; do
  step "Building $project"
  build_one "$project" codoc || exit 1
  build_one "$project" baseline || exit 1

  step "Packing"
  for name in "$project" "$project-baseline"; do
    ( cd "$OUT/.stage" && COPYFILE_DISABLE=1 tar czf "$OUT/$name.tar.gz" "$name" ) \
      && ok "$name.tar.gz" || { bad "could not pack $name"; exit 1; }
  done
done

rm -rf "$OUT/.stage"

if [ "$SEED" = 0 ]; then
  echo
  echo "  NOTE: built with --no-seed, so the codoc arm has no feature tree."
  echo "        These are not usable for a session. Rebuild without the flag."
fi
echo
echo "Workspaces in $OUT"
ls -la "$OUT"/*.tar.gz 2>/dev/null | awk '{print "  " $9, $5 " bytes"}'
