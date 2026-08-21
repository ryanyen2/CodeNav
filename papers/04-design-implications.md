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

**Built:** `codoc/loop/prose.py` — a deterministic critic over a title or
description that reports named defects (opens on a mechanism, restates the title,
banned register, decorated punctuation, altitude mismatch against the node's
depth, no fact a reader could not get from the identifier names). Applied at the
one write boundary so every path that authors prose is checked, with a single
repair retry and, failing that, the sample kept with the defect recorded rather
than dropped.

## C. Altitude-aware description prompts

**From** §1 and §3 of the comprehension note: a top-level node feeds hypothesis
formation, a leaf feeds beacon search, and the same register cannot serve both.

**Built:** the tree-update and bootstrap prompts carry the node's depth and the
register expected at it; the prose gate checks the two agree.

## D. Answer the group-3 and group-4 questions

**From** [§2 of the comprehension note](01-codebase-understanding.md) — the Sillito
escalation.

Group 3 ("how are these related") is answered by a diagram, and the diagram
plugin already lifts one from the code graph. Group 4 ("what happens if I change
this") is answered by the `impacted` set, which Loop A computes and then uses only
advisorily.

**Status:** diagram lift audited against the question it is meant to answer; the
`impacted` set surfaced where a reader can see it.

## E. Warrant the why

**From** [the rationale note](03-rationale-and-why.md).

**Built:** each prose-writing op records which evidence licensed its why, so the
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
   proxy for readability that needs no human in the loop.
