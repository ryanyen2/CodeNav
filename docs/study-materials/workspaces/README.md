# The four project copies

Two projects, two copies of each, packed as archives. Each unpacks into one folder
and brings its own git history. Unpack with `tar xzf <file>`, or let
`docs/study-materials/scripts/setup.sh` do it.

| Archive | Unpacks to | What it is |
| --- | --- | --- |
| `hearth-codoc.tar.gz` | `hearth/` | Hearth, with a codoc description already set up |
| `hearth-baseline.tar.gz` | `hearth-baseline/` | The same hearth, with a `CLAUDE.md` instead |
| `ember-codoc.tar.gz` | `ember/` | Ember, with a codoc description already set up |
| `ember-baseline.tar.gz` | `ember-baseline/` | The same ember, with a `CLAUDE.md` instead |

Hearth builds a website out of a folder of markdown files. It is about 2,050 lines
with 233 tests. Ember reads blog feeds and writes a daily digest page. It is about
2,275 lines with 171 tests. Each participant uses one project each way.

## What is the same and what is different

Within a project, the two copies have identical source, sample content, templates
and tests, and the same 12 commits of history. The copy without codoc has one extra
commit, the one that adds `CLAUDE.md`.

The codoc copy adds the codoc setup. The other copy adds `CLAUDE.md` and the
instructions that tell the agent to keep it current. Nothing else differs, and that
is the point.

Both descriptions hold exactly 25 features, every piece of code belongs to one of
them, and each keeps its whole test suite under a single feature rather than
scattering tests across the features they exercise.

## What the archives leave out on purpose

They ship without the two files that record where codoc is installed, because those
hold paths that only work on the machine that wrote them. The setup script runs
`codoc install-hooks` and writes them fresh for whatever machine you are on.

They also ship with no build output and no saved build state, so the first run does
all the work. An earlier hearth archive shipped saved state without the matching
output, which made the very first build skip every summary page. Section 6.3 of the
design doc records what went wrong.

## Checking a copy after unpacking

First, set up its Python:

```
uv venv --python 3.11 .venv
VIRTUAL_ENV=$PWD/.venv uv pip install -e '.[dev]'
```

Hearth should print exactly this:

```
./.venv/bin/hearth build            # 12 pages, 12 rebuilt, aggregates rebuilt
./.venv/bin/python -m pytest -q     # 233 passed
```

Ember should print this:

```
./.venv/bin/python -m ember refresh # 5 feeds, 36 items: 36 new
./.venv/bin/python -m pytest -q     # 171 passed
```

And either codoc copy:

```
codoc status                        # 25 features, 0 pending, state: in_sync
```

## What is written into the descriptions

Each description carries four recorded decisions that nobody could work out by
reading the code, because they say why something was built one way and what was
rejected instead.

In hearth: why the summary pages are only redone when a fingerprint changes, why
settings are read once at startup, why the markdown renderer is written by hand,
and why the preview server serves already-built files. In ember: why a day's page
is only rewritten when a fingerprint changes, why settings are read once at
startup, why the template engine is written by hand, and why the archive keeps a
fingerprint of its own.

Each also records the one rule the task is built around: anything that changes what
a summary page shows has to be visible where the list behind that page is
assembled. That sentence appears nowhere in the code. That is what makes it worth
studying.

These descriptions are the study's measuring instrument, so do not edit them
casually. After any change, export the other condition's `CLAUDE.md` again with
`codoc export-markdown`, repack both archives, and check the two still match:

```
python3 ../scoring/check-descriptions-match.py <codoc-copy> <other-copy>
```
