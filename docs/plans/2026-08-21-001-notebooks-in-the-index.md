# The reasoning was already written down, in the one file codoc did not read

Written 2026-08-21. **Built** — `codoc/lang/notebook.py`, registered in
`codoc.lang`, walked by the indexer, pinned by `tests/test_notebook_lang.py`. What the
extension does with a notebook citation is the part still open; see "Where this stands".

## What happened

The gap is the mirror image of [config files in the
index](2026-08-20-002-config-files-in-the-index.md), and it showed up the same way: a
description that is true and answers nothing. A repo whose analysis lives in
`work/churn.ipynb` gets a tree that describes the package the notebook calls and never
the notebook, so what a reader gets is the mechanism (`Model.fit` computes a
least-squares fit) and never the intent (*one fold, because the export is small*) —
even though the author WROTE that sentence, in that file, one cell above the code.

Two losses, and the second is quieter. A notebook is where the reasoning is: a markdown
heading is the author's own name for a step, at exactly the altitude a feature title
wants, and the paragraph under it is the description someone already took the trouble to
write. And a notebook that imports the package is a **caller** — unindexed, it is a call
edge `graph/extract` cannot see, so "what would this change reach" answers without it.

## Why it could not

`_INCLUDED_PATTERNS` did not name `**/*.ipynb`, so the file was never walked. But had it
been walked, nothing downstream could have read it: `detect_language` returns `None` for
the extension, and a `.ipynb` handed to `PythonAdapter` is a JSON document, which parses
as a Python *expression* and would therefore have been reported as a clean file with one
enormous chunk in it.

## The call: an adapter, not a second reader

The settings-file stage deliberately did NOT go into `codoc/lang/`, because a settings
file has no functions and a tree-sitter adapter is the wrong shape for one. A notebook is
the opposite case and gets the opposite answer: **it has symbols, because it is Python
with the cells still visible.** So it goes behind the `LanguageAdapter` Protocol, and
`detect_language(".ipynb") → "notebook"` plus `get_adapter("notebook")` is the entire
integration — diff, the hook's symbol scoping, the graph, `status` coverage, `payload`'s
per-owner budget split and bootstrap all work unchanged. The settings stage needed a
`_reads_cleanly` branch in three places; this one needed none.

The adapter answers every Protocol method in terms of one **synthetic Python document**
(the cells in order, one blank line between, with a per-line index back into the file),
and delegates the chunking of each section to `PythonAdapter().extract_chunks`. Only the
addresses differ; name-merging, decorator peeling and the transparent-wrapper rules are
not reimplemented.

### The prose goes in as string literals, not comments

This is the decision the stage turns on, and the obvious choice is the wrong one.
Emitting each markdown cell as `#` lines fails twice:

- **It reaches no chunk.** `python._extract_chunks_recursive` collects module-level glue
  in runs that exclude `comment` nodes, so the prose would be in the synthetic document
  and in no chunk's source — never in a prompt — and a section whose cells are all
  markdown would produce no chunk at all, so there would be nothing to bind.
- **It is not part of identity.** `core/tree_walk._COMMENT_TYPES` is hard-coded, so
  overriding an adapter's `comment_node_kinds` would not have helped: a rewritten
  paragraph would not have moved `tokens_hash` and Loop A would never have woken.

Both behaviours are *correct for code* — a reflowed comment is not a change. A notebook
inverts the case, because there the markdown IS the authored intent. As `r"""…"""`
statements the prose enters the chunk source and the identity, so a reworded step is
exactly the change a fresh description follows. Raw, and quoted with whichever
triple-quote the prose does not itself contain, so the paragraph is carried byte-for-byte
into what a person will later be shown as the reason for a description. A block using
both quote styles falls back to comments and loses that block's prose — per block, so it
never costs the cell or the notebook.

### A heading names the step, and sections are flat

`## Load the data` names the statement run under it (`nb.ipynb::load-the-data`); a `def`
under that heading is a member (`load-the-data.tenure`), the same owner/member relation a
class and its methods have, which is why the dotted-path readers downstream need no
changes. `### Load` under `## Data` is `load`, **not** `data.load`: headings are a
reading order, not a namespace, and nesting them would mint addresses that move when an
author demotes a heading. A repeated `## Train` is a second section (`train`,
`train[1]`) whose members are filed under the name that section actually got. Before the
first heading — and in a notebook with no headings at all — the addresses are a script's
own, identical to the equivalent `.py` file, so no code ever has two names.

### Identity is the cells' source and nothing else

Outputs and `execution_count` never enter the synthetic document. The alternative is a
tree that churns every time somebody presses Run All, which would train an author to
ignore it.

### IPython that is not Python

`!shell`, `%magic` and `?`-help lines are commented out; a `%%cell-magic` cell and any
cell that still fails to parse are commented out whole. The reason is not politeness: an
unparseable file is *damage*, and damage HOLDS Loop A's removals, so one `!pip install`
would freeze the notebook's features and leave the tree citing symbols that are gone. Two
costs, accepted and recorded in the module: a half-typed cell reads as clean rather than
mid-edit, and a section whose only cell is shell contributes no code chunk (its prose
still binds). Broken JSON is the case that must NOT be tolerated, and the fallback
document is `"(\n"` for that reason — raw JSON is a valid Python expression, so parsing
the file as Python would report a corrupt notebook as perfectly clean.

### The read ceiling had to become per-kind

A notebook's size is dominated by output — a base64 PNG per plot, a megabyte of captured
stdout — and none of it is parsed, hashed, or shown to a prompt; the synthetic document
for a 12 MB notebook of figures is a few tens of KB. `READ_CEILING_BYTES` is a **cost**
bound, so applying the code number here would turn a notebook away for bytes that exist
because somebody RAN it: whether codoc can see the code would depend on whether the
notebook was executed before it was committed. `read_ceiling(path)` gives `.ipynb`
`NOTEBOOK_READ_CEILING_BYTES` (20 MB). Five times and not unbounded, because the file is
still decoded whole before anything can tell where its bytes went.

## Where this stands

- **Built**: `codoc/lang/notebook.py`; `tree_is_clean` lifted into `codoc/lang/base.py`
  so `parses_cleanly` and the adapter's per-cell check ask one question; registration in
  `get_adapter` / `detect_language`; `**/*.ipynb` in `_INCLUDED_PATTERNS` and
  `**/.ipynb_checkpoints/**` excluded (the same notebook, stale); `read_ceiling` threaded
  through `cocoindex_app._process_file` and `survey.py`. 24 unit tests.
- **Open — clicking a notebook citation.** `codoc.openRef` cannot land on
  `codoc:work/churn.ipynb#load-the-data`: VS Code exposes no document symbols for a
  notebook, and `openRef`'s regex fallback would match inside the JSON text and reveal a
  line of base64. The analogue of `state/settings-sections.ts` is a `notebook-cells.ts`
  that finds the cell by heading and reveals it through the notebook API.
- **Open — bootstrap register.** A notebook section's description should be allowed to
  quote the author's own paragraph rather than paraphrase it; nothing yet tells the
  bootstrap prompt that this file's prose is the author's and not the model's.
