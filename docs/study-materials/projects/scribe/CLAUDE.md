# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

The repo ships no `.venv`; the README's `.venv/bin/...` invocations assume you made one:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"     # installs pytest and the `scribe` console script
```

Day to day (from the repo root — `scribe/` is importable from cwd, so the editable install is only needed for the console script):

```bash
python -m pytest tests/ -q                                  # full suite, ~0.3s
python -m pytest tests/test_rules.py -q                      # one file
python -m pytest tests/test_rules.py::test_a_decimal_is_not_a_footnote_marker -q   # one test
python -m pytest -k furniture -q                             # one policy area

python -m scribe.cli convert fixtures/report.txt             # writes report.md beside it
python -m scribe.cli convert fixtures/report.txt -           # writes to stdout
python -m scribe.cli check fixtures/                         # converts every .txt, writes nothing
```

`check` is the fast way to see the blast radius of a rule change across the corpus: it prints a
one-line summary per document (`report.txt: 3 pages, 8 headings, 12 paragraphs, ...`) and touches no
files. There is no linter or formatter configured, and no runtime dependencies.

## Architecture

Input is the output of `pdftotext` or similar: one string, form feeds between pages. Output is
Markdown. Everything happens in-process; `convert.convert(raw) -> Converted` is pure, and `cli.py` is
a thin wrapper that does file I/O around it.

### One structural module, six policy modules

`lines.py` is the only module that describes the document as it *is*. It parses the form-feed-
delimited string into `Document -> Page -> Line` and strips trailing whitespace; it makes no other
judgement. Each `Line` carries `page`, `index`, `total_on_page`, and derived `from_top` /
`from_bottom` — positional metadata that the furniture and footnote rules depend on and that cannot
be recovered later.

Every other module is a *policy*: a guess about what the text means, made from the text alone. Each
one owns its guesses and states, in its module docstring, what alternative was rejected and what it
costs. `furniture.py` (running headers, page numbers), `paragraphs.py` (dehyphenation, paragraph
breaks, reflow), `blocks.py` (headings, bullets, blank space), `notes.py` (footnotes), `text.py`
(ligatures, smart punctuation).

### The pipeline order is load-bearing

`convert.convert` runs the rules in a fixed order, and this is the thing most likely to surprise you:

1. `lines.read` — string into `Document`.
2. `furniture.strip` — **before** anything looks for headings. A running header is usually the
   section title, so it looks exactly like a heading; stripping first means the heading rule never
   sees it. `tests/test_rules.py::test_furniture_runs_before_headings_and_that_is_load_bearing`
   pins this. Note `strip` mutates the `Document` in place; `convert` measures the drop by
   comparing `len(doc.lines)` before and after.
3. `_collect_notes` — **before** reflow, or a note at the foot of a page gets glued to the last
   sentence above it. This is also the last step that can see positional data: it consumes the
   `Document` and returns `list[str]`. Anything needing `from_bottom` must run at or before this
   point. It appends a blank line at each page boundary so reflow gets the chance to decide whether
   the sentence runs on.
4. `blocks.collapse_blanks` — runs of blanks to one.
5. The main loop — headings and bullets are decided line by line (both are properties of a single
   line); everything between them accumulates into a prose `run` that is flushed through
   `paragraphs.reflow` whenever a heading or bullet interrupts, so a paragraph broken across a
   heading is never silently joined.
6. Footnote definitions appended, `_join` inserts blank lines between blocks (skipping between
   consecutive bullets, which would make a loose list).
7. `text.normalise` — **last**, so every rule above sees the text as it came out of the PDF rather
   than a partly rewritten version.

Two non-obvious control-flow details: `blocks.heading_level` takes the *following* line as a lookahead
(a heading has space under it; a wrapped numbered list item runs straight on), and
`paragraphs.reflow` rebuilds its own `lines` list mid-loop to push the remainder of a dehyphenated
line back onto the queue so the rest of it still passes through every rule.

### Tuning constants

The behaviour of this program is mostly these numbers. Each is commented at its definition with the
failure that motivated it:

- `furniture.EDGE` (2), `MIN_PAGE` (6), `REPEAT_SHARE` (0.6), plus a hard floor of 3 pages before
  furniture detection runs at all.
- `blocks.MAX_HEADING_WORDS` (12) — the only thing separating "3. Findings" from a wrapped list item.
- `paragraphs.KEEP_HYPHEN` — prefixes whose hyphen survives a line break; and the 60-character
  threshold in `is_break`.
- `notes.looks_like_note`'s `from_bottom <= 6`.
- `notes.MARKER` deliberately excludes a preceding digit, so decimals and years are not footnote
  references.

## Tests

Two files, two jobs. `tests/test_rules.py` is one test per policy, with a final section for the
places two policies meet — that section is where a change to one rule shows up as a break in another.
`tests/test_documents.py` runs the three fixtures end to end.

The fixtures are the spec, and each covers something the others cannot: `report.txt` has furniture,
footnotes and numbered headings; `memo.txt` has none of those and is what makes "keep the running
header" a live alternative; `handbook.txt` has deep numbering plus a numbered list that must not
become headings. A new rule generally needs a fixture change or a new fixture, not just a unit test.

## Conventions

Docstrings here carry the argument, not the mechanics — what the rule does, what the rejected
alternative was, and what the current choice costs. Match that when adding or changing a policy; a
rule with no stated cost reads as if it had no downside. Comments in tests record the bug that the
test was written for.
