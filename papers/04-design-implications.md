# From the literature to the pipeline

Each finding, turned into a concrete change in `codoc/`. Status is updated as the
work lands.

## A. Style memory — learn the author's voice from their edits

**From** [PRELUDE / CIPHER](02-continual-learning-from-user-edits.md).

Codoc already records everything needed and uses none of it. Every AMEND event
carries `prev_description`, `prev_written_by`, and `actor`, so the ledger already
holds the (draft, revision, who) triples PRELUDE needs. What exists today is
`Store.human_written_descriptions(limit=2)` — two raw paragraphs shown as
`author_voice`, which the EMNLP 2025 imitation result says is the weak form of
this.

**Built.**

| Piece | Where |
|---|---|
| `StyleLesson` model — a named preference, its scope, its evidence count | `codoc/model/voice.py` |
| `style_lessons` table + queries | `codoc/store/db.py` |
| Harvest (find the pairs), infer (one LLM pass), retrieve (context-scoped), render | `codoc/loop/voice.py` |
| The inference prompt | `codoc/prompts/voice_infer.txt` |
| Injection into the prose prompts as `author_voice.lessons` | `codoc/prompts/tree_update.txt`, `bootstrap_*.txt` |
| `codoc voice` — read, correct, and forget what was learned | `codoc/cli/main.py` |
| Edit-distance cost trend, the PRELUDE metric | `codoc/loop/voice.py::edit_cost_trend` |

**Guards, from §3 of the note.** A lesson inferred once is `provisional` and is
not injected until a second edit corroborates it. Every lesson records the
subtree it was learned in and is retrieved for that region first. The prompt
block says explicitly that a lesson governs *how* to say something and never
*what* is true, because faithfulness to the code outranks voice.

**Content edits teach nothing about style**, so the inference pass classifies each
revision first and discards the ones that only fixed a fact. This is the failure
mode that would otherwise poison the memory.

## B. A prose gate — check what we generated before it lands

**From** [§4 of the comprehension note](01-codebase-understanding.md): readers want
purpose, generated text supplies mechanism, and the defect is checkable.

`prompts/style.txt` states the rules and nothing enforces them, so a bad sample
reaches the tree and the author has to fix it by hand — which then costs an edit
that the style memory reads as a preference. Enforcement upstream is therefore
also what keeps the memory clean.

**Built.**

| Piece | Where |
|---|---|
| The critic: named defects over a title/description, with severity | `codoc/loop/prose.py` |
| The critique text a repair pass is given | `prose.critique` |
| The gate: check, repair once, keep the better draft | `prose.gate` |
| Repair wired into the three passes that write prose | `codoc/agent/tree_update.py`, `codoc/agent/bootstrap_agent.py` |
| The scorecard, recorded where prose lands | `codoc/loop/apply.py::_record_prose` |
| The rate, read back | `prose.defect_rate` / `prose.render_rate`, shown by `codoc status` |
| Rule-by-rule tests, each with the good sample it must stay quiet on | `tests/loop/test_prose.py` |

**Check and score live in different places, and that is the design.** A repair is
only possible where a rerun is possible, so the gate runs at the three generation
sites. But a per-caller counter measures whichever caller remembered to call it, so
the *rate* is recorded at `apply_op` — the one boundary every writer passes. It
counts what a reader will meet (post-repair, pending proposals included, since the
overlay materializes those into the document) and never counts a person's own
words: an author who writes a dash has not introduced a defect, and averaging their
prose in would improve codoc's score every time somebody typed one.

**The repair costs one cache miss and no new prompt.** It is the same call with the
critique appended to the volatile tail, so the wave's frozen prefix stays
byte-identical — and appending also makes the second request differ, which
`run_agent`'s response memo requires. A clean answer never triggers it, so the cost
is bounded by the defect rate itself.

**A repair can only ever be an improvement.** The rewrite is accepted only if it
covers the same nodes with the same bindings and scores strictly better; a dropped
node, a re-attributed binding, a worse draft, or a rerun that raises all keep the
first draft with the defect recorded. The severity function weights a missing fact
above a punctuation slip, so a rewrite cannot win by deleting the content it was
asked to fix.

**Every rule was calibrated by measurement, not assertion.** Each was swept over
412 of this repo's own docstring paragraphs, and the surviving false-positive shares
are recorded in the module docstring — a rule firing on a fifth of good prose has
stopped describing a defect. Two results came out of that sweep. `altitude-too-low`
survived and its mirror `altitude-too-high` was **removed**: the only mechanical
test for it is "does this prose name a symbol or a number", and good leaf prose
often names neither, because the bindings already tie the node to its code. And
`decorated`'s 109% is reported as a corpus mismatch rather than a false-positive
rate, because `style.txt` exempts the notes-to-a-developer register that these
docstrings are written in.

## C. Altitude-aware description prompts

**From** §1 and §3 of the comprehension note: a top-level node feeds hypothesis
formation, a leaf feeds beacon search, and the same register cannot serve both.

**Built.**

| Piece | Where |
|---|---|
| The rule, in the shared guide every prose prompt pulls in | `codoc/prompts/style.txt` |
| The per-node signal: `depth`, `children`, `spans_files` on each subtree row | `codoc/loop/subtree.py` |
| The prompt reading it, as Rule 9 | `codoc/prompts/tree_update.txt` |
| The register each bootstrap pass writes at (it has no per-node altitude yet) | `codoc/prompts/bootstrap_file.txt`, `bootstrap_org.txt` |
| The same three signals handed to the gate | `codoc/agent/tree_update.py::_node_context` |
| Both halves, pinned against each other | `tests/agent/test_altitude.py` |

**The prompt and the gate share one threshold**, and a test states it in words so
changing `prose._BROAD_FILES` fails there rather than quietly leaving the prompt
asking for a register nobody is enforcing. Broad means: has children, or spans three
files or more, or sits at depth 0.

**The depth is computed over the whole tree, not the payload.** A tree-update call
sends a WINDOW of the tree, so a parent chain routinely reaches above what was sent
and a depth counted inside the window is short by whatever was cut. `subtree.py`
computes it over the full feature list and puts it on the row; `_node_context` prefers
the stated value and only falls back to walking the window when a node has none.

**Neither bootstrap pass is given a per-node altitude, because neither knows one.**
The file pass proposes a file's nodes before the organization pass exists to put them
under anything. What is knowable is which pass you are in — the file pass writes
leaves, the organization pass writes the themes above them — so the register is fixed
per prompt, and the only per-node signal that survives is whether a node got a child
of its own in the same answer.

## D. Answer the group-3 and group-4 questions

**From** [§2 of the comprehension note](01-codebase-understanding.md) — the Sillito
escalation.

Group 3 ("how are these related") is answered by a diagram, and the diagram
plugin already lifts one from the code graph. Group 4 ("what happens if I change
this") is answered by the `impacted` set, which Loop A computes and then uses only
advisorily.

**To build:** diagram lift audited against the question it is meant to answer; the
`impacted` set surfaced where a reader can see it.

## E. Warrant the why

**From** [the rationale note](03-rationale-and-why.md).

**To build:** each prose-writing op records which evidence licensed its why, so the
provenance card can show the warrant rather than only the chain.

## F. The evals that matter

**From** RepoSummary's coverage metric and PRELUDE's edit-distance cost.

1. **Feature coverage** — against a repo whose maintainers wrote real
   architecture documentation, what fraction of the features they named appears
   in the generated tree.
2. **Edit-distance cost trend** — for descriptions a human later edited, the
   normalized distance between what codoc wrote and what the human left. Falling
   over time is the claim that the style memory works.
3. **Prose gate defect rate** — how often a fresh sample trips the critic. A
   proxy for readability that needs no human in the loop. Wired (B): recorded at
   `apply_op` and read back by `codoc status`, so the number accrues from ordinary
   use rather than from a benchmark run.

**Blocked on credit, not on code.** (1) and (2) need real model passes and the
OpenAI balance is exhausted, so `tests/loop/test_end_to_end.py` and
`tests/bdd/test_e2e_userflows.py` fail on a 429 today. Everything deterministic is
green, and (3) needs no model at all.
