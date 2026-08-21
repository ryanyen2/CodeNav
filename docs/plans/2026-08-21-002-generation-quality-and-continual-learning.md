# Making the generated tree worth reading, and teaching it the author's hand

Written 2026-08-21. **Branch** `worktree-codoc-quality-continual-learning`, pushed to
`origin`; 25 commits over `a75003f`, 114 files, +17,040 / −370. **Suites green**: 2323
passed / 2 skipped (`pytest tests/`, minus the two real-LLM gates) and 1461 vitest tests
with `tsc --noEmit` clean.

This is the branch note: what was asked, what the literature said, what was built, what
was measured and thrown away, what is still missing, and what to do next. It is written
for whoever picks this up — including a later session of mine — so the *rejected* options
are here too. A rule without its measurement is a rule the next person will re-litigate.

## The goal, as given

> keep on improving the quality of the generated codoc by making the pipeline robust and
> also through the continuous learning; there are two lines of literature I need you to
> check, first is about codebase understanding … another line of research is from the NLP
> about continuous learning where we learn from how users prompts, describing the codebase
> and then gradually changing the tone, the style of writing and the feature name …
> given our codoc is built ground up from the codebase, and we need to be faithful to the
> codebase … at least the description of the feature, the label, and the structure should
> be as close as how user interpret the codebase … with these data, we might able to build
> the version control even further where we can link to the rationale behind certain
> changes, and knowing not just what should change but why it changes … work until we able
> to faithfully describe any type of python codebase and that when user edits, iterate
> through, AI edits in plan or diff mode, they stay robust, and that the text is very very
> human readable always … even utilizing diagram, sometimes other rich text editor block
> type … compatible with notion on the user experience and UI design and the robustness of
> editor, design minimalist with taste

Seven clauses, and they are tracked separately below because they are at different stages.

## Sources

`papers/` holds the reading notes and, in `papers/raw/`, the unedited `paper_search`
output for each of eleven queries (`q1`…`q11`). Read `papers/README.md` first: it carries
the one caution that matters. Entries marked **[verified]** were retrieved this session
and carry a URL and the citation count the API returned; entries marked **[canonical]**
are foundational works recalled *without* a live lookup, because the search APIs
rate-limited on the older literature. No numeric claim is attributed to a canonical
entry, and where a title or year was uncertain it was left unstated rather than guessed.

**Line 1 — how developers build a theory of a codebase**
(`papers/01-codebase-understanding.md`). Von Mayrhauser & Vans' integrated metamodel
[verified, 678 cites] is the load-bearing one: a maintainer works top-down *and*
bottom-up at once and needs both a program model and a situation model, which is the
argument for the tree having distinct **altitudes** rather than uniform prose. Sillito's
developer-questions taxonomy [verified, 306 cites] supplies the actual question groups a
description has to answer, and its groups 3 and 4 ("how are these related", "what would a
change to this affect") are why the diagram block and the impact count exist.

**Line 2 — learning a latent preference from user edits**
(`papers/02-continual-learning-from-user-edits.md`). PRELUDE / CIPHER (Gao et al.,
NeurIPS 2024) [verified, 96 cites] is the architecture codoc copies: infer a *named*
preference from the draft→revision gap, keep it in a store, retrieve by context, inject
into the prompt — no fine-tuning, and its normalized-edit-distance metric as the thing to
report. The EMNLP 2025 style-imitation result [verified, 11 cites] is why the author's own
paragraphs and the learned instructions are two separate prompt keys rather than one list.

**Line 3, which the goal implies rather than names** —
`papers/03-rationale-and-why.md`. The finding that shaped `warrant.py`: rationale usually
does not live in the artifact, it lives in commits, issues and review threads, and the
why-bearing minority of those is where the value is. So a *why* claim has to cite a source
that exists, and a claim that cites nothing must read as unwarranted rather than confident.

`papers/04-design-implications.md` is the bridge: sections A through F each turn a finding
into a named change under `codoc/`. All six are built.

## What was built

Grouped by the goal clause each answers. Hash, then the tests that pin it.

### Learning the author's hand (clause 2)

| | |
|---|---|
| `e69c6b1` | `loop/voice.py`, `agent/voice.py`, `model/voice.py`, `prompts/voice_infer.txt` — the style memory. Infer a named preference from each rewrite, keep it, retrieve on scope overlap, inject at most one lesson per axis. `tests/loop/test_voice.py` |
| `1df29de` | The same harvest reads **comments**, not only rewrites: a note stating a preference in words is the stronger signal and was previously invisible, because the amend that answers a note is written by an agent and so is not a human edit. Notes get half the batch reserved so a busy editor cannot starve them. |

Three properties are load-bearing and each answers a specific way this goes wrong: a
lesson is about the *writing* and never the content (a rewrite is classified
style/content/mixed/noise and only the style half survives); a lesson is provisional until
a second edit corroborates it; a lesson remembers where it was learned, so a changed mind
displaces the old preference at the point of use. `codoc voice forget` is the correction
channel, and it is the reason a learned preference is safe at all.

### Prose that reads like a person wrote it (clause "very very human readable")

| | |
|---|---|
| `d68cfec` | `loop/prose.py` — a **deterministic** critic (no model) naming sixteen defects, plus one repair pass that can only ever be an improvement: a rewrite is kept only if it covers the same nodes with the same bindings and scores strictly better. Altitude became part of the contract on both sides (`subtree.py` ships depth/children/spans_files; `style.txt` and `tree_update.txt` Rule 9 read them). `tests/loop/test_prose.py`, `tests/agent/test_altitude.py` |
| `971a806` | An agent gets the critique instead of a rerun, since an MCP write tool cannot repeat its own call. Advisory, so the write always lands. `tests/mcp/test_prose_advice.py` |
| `37c4249` | An amend that changes no words must not take the paragraph over — otherwise a mechanical loop write makes the loop the author of somebody's prose and relaxes the amend gate over their next rewrite. |
| `b678c80` | A description opening on a **class** is now caught, by asking the node's own symbol table rather than by shape. |

The check and the score deliberately sit in different places: the check runs at the three
passes that generate prose (a repair needs a rerun), the rate is recorded at `apply_op`
(the one boundary every writer crosses, so the number cannot measure whichever caller
remembered to call it). A person's prose is never checked and never scored.

### Faithfully describing any Python codebase (clause "any type of python codebase")

| | |
|---|---|
| `c123c5c`, `0c3f426` | Every definition in a namespace gets an address; one name gets exactly one (overloads and a property's accessors are joined, not last-wins); the guard a definition sits under is part of the definition, including when it *declares* rather than defines. Pinned over ~400 real files by `tests/test_address_conformance.py` with `ast` as the oracle. |
| `b7f3bfc`, `625bdb7`, `0b0a6e1` | A file that does not parse is not evidence of deletion, and legal Python is no longer called damaged: Python asks **two** readers (grammar, then `ast.parse`) because they fail on opposite halves of the language, and only a file both refuse is damage. `tests/loop/test_unparseable.py` |
| `6a6b305` | `pipelines/indexing/survey.py` — what the walk never saw, so the coverage figure cannot read 100% over code codoc never read. Reports codoc's own limits only, never intentional excludes. `tests/pipelines/test_survey.py` |
| `917db8d`, `0964385`, `a3fd899` | `loop/payload.py` — a per-pass prompt budget. A set that fits passes through unchanged; a set that does not is **split across calls by top-level owner** rather than spent down to 60 characters a method, so no pass is shown half a class. `tests/loop/test_payload.py` |
| `3bfe987` | A large file is judged by its parse, not its size: past 1.5 MB it gets a hearing instead of a silent drop. That is what made altair's 1.6 MB schema module visible while its 1.2 MB sibling was already indexed. `tests/pipelines/test_gate.py` |
| `aec70b7`, `bfb78f2`, `5cc87e6` | `settings_files.py` — a settings file as addressable chunks, indexed only where some indexed source file's text names it (a decision the code reads, not a glob), with the value in force named in the description that cites it. `tests/test_settings_files.py`, `tests/pipelines/test_settings_{scan,index}.py`, `settings-sections.test.ts` |
| `291faab`, `bec8435`, `73b2476` | `lang/notebook.py` — a `.ipynb` is Python with the cells still visible, so it is an adapter and not a second reader. The markdown headings are the author's own decomposition, the prose rides in as string literals so it reaches both the chunk and its identity, and outputs never do. `tests/test_notebook_lang.py`, `tests/agent/test_notebook_prompt.py`, `notebook-cells.test.ts` |

### Why, not only what (clause "rationale behind certain changes")

| | |
|---|---|
| `3ab3cbc` | `loop/warrant.py` — a prose-writing op records what its stated why **rests on**, and the provenance card quotes it. The describing pass cites ids and codoc resolves each id back to what the source actually said, so **the quote never comes from the model**; an id naming nothing is dropped, leaving the op unwarranted rather than falsely warranted. `tests/loop/test_warrant.py` |
| `800ba9e` | The two Sillito answers a description must not contain: how these relate (the diagram block, lifted from the dependency graph) and what a change would reach (`feature_impact`, a hover count). Both derived, deliberately never written into prose. `impact-decorations.test.ts` |

### Staying robust under edits (clause "user edits, AI edits in plan or diff mode")

| | |
|---|---|
| `6423c89` | A rename moves the **prose** a rename stales, not only the binding. Deterministic (the address is known from content and shape identity), no verdict (the question has one answer), and it does not claim the paragraph — `apply_op(claims_prose=False)` exists for exactly this one caller. Four guards, the load-bearing one being that only a citation which no longer *resolves* is touched. `tests/loop/test_repoint.py` |

## What was measured and rejected

This section is the point of the note. Each of these looked like a good rule until it was
swept against the repo's own prose or its own corpora.

- **Single-hump PascalCase in a description's opening** — 2.7% of docstring paragraphs
  even when gated on the node's bindings, and most of those are good imperative prose whose
  first word happens to be a class. This repo really does define `Merge`, `Phase`, `Usage`,
  `Resolution`, `Skipped`, `Divergence`. Left unchecked, and the reason recorded as measured
  rather than assumed.
- **Two-hump PascalCase ungated** — 0.9%, of which about half are somebody else's proper
  noun (`TypeScript`, `GitHub`, `OpenAI`, `LanceDB`, `FastAPI`, `ProseMirror`). Naming a
  technology the reader knows is orienting them, not showing them machinery. Gated on the
  node's symbol table it is 0.1%, all of it real. Adopted gated.
- **Three-caps acronyms** — 5.4% garbage (`MUST`, `NOT`, `JSON`, `LLM`, `TEMPLATE`).
  Rejected outright; gating would not save it, since an emphasized word can also name a
  constant.
- **`SCREAMING_SNAKE` ungated** — 0.3%, all genuine, and no English word carries an
  underscore, so it needs no evidence beyond its shape. Adopted ungated.
- **`altitude-too-high`** — deleted, at 19%. Good leaf prose often names no symbol and no
  number, because the *bindings* already tie the node to its code.
- **`decorated`'s share** is annotated in the module docstring as a corpus mismatch rather
  than a false-positive rate: `style.txt` exempts the notes-to-a-developer register these
  docstrings are written in.
- **Emitting notebook markdown as `#` comment lines** — the obvious choice, and wrong
  twice: comments are excluded from module-level glue runs so the prose would reach no
  chunk, and `_COMMENT_TYPES` excludes them from identity so a reworded step would not be
  a change. String literals instead.
- **A settings file as a `lang/` adapter** — rejected; a settings file has no functions and
  a tree-sitter adapter is the wrong shape. A notebook got the opposite answer for the
  opposite reason.

Two methodological rules came out of this and should be kept: **calibrate every new rule
against this repo's own prose and record the surviving false-positive share next to the
rule**, and **where a rule needs evidence, make it ask for evidence** rather than guess
from shape — with silence, not a guess, when the evidence is not offered.

## What is not done

### The limitation that matters most

**No stage on this branch has been validated on real generated output.**
`tests/loop/test_end_to_end.py::test_real_end_to_end` and `tests/bdd/test_e2e_userflows.py`
both fail on `429 … credit_balance_exhausted`, so no real-LLM bootstrap has run in this
session. Everything above is deterministic and unit-tested; the *prompt-side* changes —
`style.txt`, the altitude registers, the `{{doclang}}` directive, `notebook_note.txt`,
`bootstrap_settings.txt`, and the injected voice lessons — are therefore **unverified by
measurement**. The prose gate's recorded rate is the instrument for this and it has no
readings from a real run yet. First job for whoever has credit: bootstrap `test/requests/`
and `test/altair/`, read the tree, and record `codoc status`'s prose rate and
`codoc voice`'s `edit_cost_trend`.

### The open gap in what gets an address: re-exports

Measured today across `codoc/` and `test/` (215 files): **2407 public module-level names
bound by an import have no address**. Most of them must not have one — they are
dependencies the module uses, not surface it publishes:

| count | bucket |
|---|---|
| 1338 | plain absolute `from x import y` |
| 363 | plain `import x` |
| 233 | plain relative `from .x import y` (not in an `__init__.py`) |
| 203 | unused in the file (mostly `annotations`) |
| **238** | **in `__all__`** (199 also unused in the file, 25 also relative-in-`__init__`, 14 plain) |
| **56** | **relative import in an `__init__.py`** (28 also unused, 25 also in `__all__`, 3 plain) |
| 1 | explicit `as`-self re-export (PEP 484 convention) |

The concrete damage: `test/requests/__init__.py` gets addresses for
`check_compatibility`, `_check_cryptography`, two version probes and `ssl` — and for none
of `get`, `post`, `Session`, `Response`, `HTTPError`, `codes`. A feature describing *what
the library offers you* has nothing to bind to, and a citation of
`requests/__init__.py#get`, which is the path an author would naturally write because it
is the import path, resolves to nothing. Worse, `codoc/loop/__init__.py` and
`codoc/__init__.py` yield **zero** chunks — a pure re-export file contributes nothing at
all, not even `__module__`, so it is invisible to the walk.

`tests/test_address_conformance.py` could not have caught this: its `ast` oracle inspects
assignments and definitions and never imports.

A re-export is the single most *intentional* line in a Python package — the maintainer
choosing what is public — which is exactly the authored intent codoc claims to track.

### Everything else

- **Notion parity in the editor** — not reviewed this session. The baseline is healthy
  (1461 tests, clean typecheck) but nothing was done on drag-to-reorder, slash-menu block
  insertion, or keyboard-first editing.
- **Block types beyond the five that exist** (prose, diagram, screenshot, reference, plus
  the fetch guard) — no table, callout or toggle; and no diagram lifted from anything but
  the dependency graph.
- **Named residuals**, each left deliberately: a same-parent MOVE still bumps `updated_at`
  and records an event; no hover preview of a notebook cell behind a citation; a
  `%%bash`-only notebook section contributes no code chunk; a mid-edit cell reads as clean;
  a prose block using both quote styles loses that block's prose; a member the citation
  names but the cell no longer declares lands on the heading; `bridge.ts`'s `DECL_RE` does
  not mark a settings section as feature-owned; `settings_files.resolve_symbol_path` has no
  production caller; a class chunk spans its methods; `survey_repo` counts a small
  unparseable file as indexed; PEP 696 `def f[T = int]` is unreadable by both Python readers
  before 3.13; a module-level chained assignment would produce an extra address under the
  walk's first-target-only reading (no corpus case exists, left for the conformance test to
  surface).

## Next steps, in order

1. **Give a re-export an address.** The rule the measurement supports: a name is public
   surface when it is in `__all__`, or when a module-scope **relative** import binds it in
   an `__init__.py`. Reuse the existing "the guard is the definition" machinery for the
   `try: from x import y / except ImportError: y = None` compat shim so it stays one chunk.
   *Acceptance*: `test/requests/__init__.py` addresses `get`, `post`, `Session`,
   `Response`, `codes`; `codoc/loop/__init__.py` stops yielding nothing; the 269 names in
   the two public-surface buckets get an address and the other 2138 do not;
   `tests/test_address_conformance.py`'s oracle is extended to imports, keeping the `GLUE`
   convention of naming every deliberate omission with its reason and the
   `len(files) > 200` guard so it cannot pass by measuring nothing; and the new branch is
   verified to bite by disabling it and watching named tests fail.
2. **Run the real-LLM validation as soon as there is credit**, per the limitation above.
   This outranks new features: five prompt-side stages are currently unmeasured.
3. **Editor parity pass**, against `docs/edh-interaction-checklist-webview-ux.md`, one
   interaction at a time with a vitest per interaction.
4. **One new block type, chosen by what a description keeps failing to say.** The
   candidate with evidence behind it is a **table**, since Sillito's group-2 questions are
   comparisons across several entities and prose does that badly.
5. **Sweep the residuals list** for the ones that are now cheap.

## Where this stands

Six of the seven goal clauses have code behind them and deterministic tests over that
code. The seventh (Notion-parity UX) has a healthy baseline and no work this session. The
honest summary is that the *pipeline* is markedly more robust — it now reads notebooks,
settings files, guarded declarations, oversized generated modules and unparseable
mid-edit saves; it repairs the prose a rename stales; it checks its own writing and
learns from being corrected — and that the *generated text* has not been read since those
changes landed, because the model gate is closed. Those are different claims and this note
keeps them apart.
