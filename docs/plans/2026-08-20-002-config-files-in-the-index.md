# The decision moved into a config file, and codoc stopped being able to say it

Written 2026-08-20. **Not built** — found while preparing the CHI27 sessions, scoped
here because the day before a study is the wrong day to change what `bindings` means.

## What happened

The tally recording moves the merchant rules and the period policy out of module
constants and into `tally/rules.toml`. That file decides which date each summary is
lined up on (`month = "made"`, `week = "posted"`) and what an unmatched merchant does
(`unmatched = "stop"`). Those are the two decisions the session exists to have
reviewed.

Loop A amended the descriptions and could not name either one. What it wrote was:

> The month threshold is read from rules.toml.

and

> Anything unmatched goes to the configured uncategorised bucket, or stops the run
> with the full list when the rules require it.

Both are true. Neither answers the question a reader opened the description to ask.
The second is worse than vague: it names both outcomes and not the one in force, so a
reader who consults the record about a run that stopped learns nothing from it.

## Why it could not

`_INCLUDED_PATTERNS` in `codoc/pipelines/indexing/cocoindex_app.py` is

```python
["**/*.py", "**/*.ts", "**/*.tsx", "**/*.mts", "**/*.cts"]
```

so `rules.toml` is never walked, never chunked, never bound, and never in the prompt.
The pass that wrote those sentences had the code that READS the setting and not the
file that SETS it. It described the mechanism because the mechanism is all it was
shown, and no part of the pipeline could report the absence — a file that is not
indexed is indistinguishable from a file that does not exist.

`prompts/tree_update.txt` gained a rule the same day (*name the answer, not just the
question*). It covers every decision that lives in code and cannot reach this one.

## Why this is not a niche gap

The pattern it misses is the ordinary one. A codebase moves a constant into a settings
file precisely BECAUSE the value matters to somebody who is not reading the source —
which is the same reason a feature tree exists. So the moment a decision becomes worth
configuring is the moment codoc goes quiet about it, and the description drifts from
"three months rather than two, so a coincidence does not become a commitment" to "the
month threshold is read from rules.toml". The prose gets worse exactly where the
project got more careful.

## What it would take

A config file has no symbols, so the work is not "add `.toml` to the list". Every
consumer of `codoc.lang` assumes a tree-sitter adapter:

| consumer | what it asks of the adapter |
| --- | --- |
| `pipelines/indexing/cocoindex_app.py` | `extract_chunks` |
| `graph/extract.py` | `references_in_chunk` |
| `agent/hook.py` + `core/tree_walk.py` | `parse`, `token_stream`, `comment_node_kinds` |
| binding repair | `resolve_symbol_path` |

A sketch that fits the existing seams:

- **A chunk is a top-level table.** `[periods]` becomes
  `tally/rules.toml::periods`, comments included — the comments in `rules.toml` carry
  the reasoning, and they are the part a description most wants to quote.
- **`references_in_chunk` returns nothing.** A config file cites no symbols; the
  dependency graph should not grow edges it cannot justify. This is the honest answer,
  not a stub.
- **`token_stream` is the parsed key/value pairs, not the raw text**, so reordering
  two settings or reflowing a comment does not read as drift. Fingerprinting is what
  decides whether Loop A wakes at all, and a config file rewritten by a formatter must
  not look like a policy change.
- **`resolve_symbol_path` finds the table header.** Cheap, and it is what keeps a
  binding alive across an edit.

Formats worth having, in order: TOML, YAML, JSON, INI. All four are read by the
standard library or a dependency already present except YAML.

## What has to be decided first

1. **Does a config chunk get BOUND to a feature, or only quoted into the prompt?**
   Binding is the honest model — the feature really is partly implemented by that file
   — but it changes coverage arithmetic (`codoc status`), the `UNIQUE(file, symbol_path)`
   constraint's reach, and what `attach`/`detach` mean for a file with no code in it.
   Quoting-only is smaller and leaves the record unable to say WHERE the value lives.
2. **Which files count.** Every `.toml` in a repo includes `pyproject.toml` and lock
   files, which are packaging and not intent. Blanket inclusion would add noise to
   every tree in exchange for one useful node. Likely answer: config files that a
   binding's code already reads, discovered from the code rather than from the glob.
3. **What a drifted config does.** A changed value is a changed decision, so it should
   wake Loop A — but a formatter pass should not, which is what the `token_stream`
   choice above is for.

## Until then

`docs/study-materials/projects/tally/STUDY.md` records this under D1 and D3 so a rater
does not read a vague description as a null result about codoc. The tally plan carries
the period decision in the agent's own words instead, which is where it belongs anyway:
the plan is the moment the choice is made, and the amend is only the record of it.
