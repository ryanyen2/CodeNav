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
this") is answered by the dependency graph's impact set, which Loop A computed and
then spent only on its own prompt.

**Built.**

| Piece | Where |
|---|---|
| The group-3 lift: both directions, per-file identity, grouped by owning feature, bounded | `codoc/blocks/diagram.py` |
| Its round trip, and where an unreadable edit becomes a draft instead of a guess | `codoc/blocks/diagram.py::lower` |
| The group-4 query, as a standing property of the tree | `codoc/graph/query.py::feature_impact` |
| The slice the document surface reads it from | `codoc/codoc_file/render.py::write_sidecar` (`feature_impact`) |
| The same answer handed to an agent that is about to edit | `codoc/mcp/tools.py::read_context` (`impact`, `impact_total`) |
| Drawn on the feature it is about | `vscode-codoc/src/webview/tiptap/impact-decorations.ts` |
| Both halves | `tests/blocks/test_diagram_plugin.py`, `tests/graph/test_query.py`, `vscode-codoc/src/test/impact-decorations.test.ts` |

**The picture was drawing half the relation.** The lift walked out-edges only, so a
feature saw what it called and never what called it — and "how are these related" is
symmetric, while the group-4 question is *entirely* the direction that was missing. It
now walks both, skipping in-edges that start inside the feature so a node's own
internals do not read as external pressure.

**Two same-named symbols in two files were ONE box, and the edge between them was
false.** Node ids collapsed a symbol path to its leaf, so `a.py::save` and
`b.py::save` merged, and any edge into either was drawn into the merged node — a
picture asserting a dependency that does not exist, which is worse than no picture.
Ids now carry the whole path; the LABEL carries the leaf, because that is what a
reader is there to read.

**Neighbours are grouped by the feature that owns them**, which is what turns a symbol
graph into a statement about the tree: a subgraph per owning feature, the reader's own
feature first, and a neighbour no feature covers labelled as outside the tree rather
than silently drawn as if it were in it.

**The cut is drawn as a node.** A hub symbol has more neighbours than a diagram can
say anything with, so the lift keeps the twelve most-connected — deterministically, by
degree and then symbol path, so a re-lift is idempotent and cannot refresh forever —
and puts `+N more related symbols` in the picture. A silently truncated diagram reads
as the whole story.

**The round trip carries no map, by construction.** Nothing in the mermaid content
records which box is which symbol; both `lift` and `lower` rebuild that from the
feature's bindings and their one-hop neighbours. An author editing the diagram
therefore cannot delete the mapping, which is the failure a stored legend invites.

**An edge delta always yields a directive; a change with no edge delta drafts.** A box
with no code behind it is a request to CREATE that code, and the author's own label is
the best name anyone has for it — so a hand-drawn edge is a directive, not a draft. What
is genuinely unmappable is a change that moved no edge: a lone new box, a relabel, or
content the codec cannot read. Those draft (KTD2 — ambiguous never guesses).

**Group 4 is a STANDING property, not a change-time one.** `loop_a._compute_impacted`
answers the neighbouring question — who was affected by what just happened — off a
changeset, and a reader asking "what happens if I change this?" has changed nothing
yet, so there is no changeset to compute from. `feature_impact` is the standing query,
and the two are kept deliberately separate.

**And it stays out of the prose.** A description listing its own callers is exactly the
inventory-of-machinery defect §C's altitude rule bans, and it goes stale the moment
somebody adds a caller — the class of fact a rebuildable index should hold instead of a
sentence. So the answer ships as a derived slice, an MCP field, and a chip that is
invisible until the heading is hovered: the fact is worth a great deal in the seconds
before an edit and nothing at all while reading. The count is the chip; clicking it
opens the dependents by name, each with the symbols that actually reach in, each a link
to go read. `via` is capped and `count` is not, because a truncated list read as
complete is worse than a number.

## E. Warrant the why

**From** [the rationale note](03-rationale-and-why.md).

§A gave descriptions a why by going and finding the prose a repo already wrote about
its own decisions. That closed one gap and opened another. The prompt now carries three
sources at once, the model writes one paragraph, and nothing recorded which source the
paragraph rested on — so a description grounded in a commit message and a description
that outran every source in the block read *identically on the page*. The second is
precisely the failure the evidence channel exists to prevent, and it had become
invisible.

**Built.**

| Piece | Where |
|---|---|
| Every evidence entry gets a citable id, and a commit keeps the sha the parser was already reading | `codoc/loop/why.py::stamp_ids`, `_parse_log` |
| The author's live prompt made citable too | `codoc/loop/loop_a.py` (`author_intent` → `{id, asked}`) |
| Resolving a citation to what the source actually said, and dropping one that names nothing | `codoc/loop/warrant.py` |
| The stored record | `codoc/model/event.py::Warrant`, `NodeOp.warrant` |
| Asking for it, with the rules that keep it honest | `codoc/prompts/tree_update.txt` (op schema, Rule 7) |
| Where the reply is turned into a record rather than trusted as one | `codoc/agent/tree_update.py::_coerce_op` |
| On the wire, both directions a reader asks from | `codoc/loop/revisions.py::_entry`, `codoc/codoc_file/render.py::_history_feed`, `codoc/cli/main.py::history` |
| The row on the card | `vscode-codoc/src/state/provenance.ts::warrantRows` |
| Both halves | `tests/loop/test_warrant.py`, `tests/loop/test_revisions.py`, `tests/codoc_file/test_history_feed.py`, `vscode-codoc/src/test/timeline.test.ts` |

**A chain is not a warrant.** The provenance card already assembled six links —
sentence → event → directive → prompt → session → commit → diff — and every one of them
answers *what happened before this claim*. None answers *why should I believe it*. Those
are different questions, and the second is the one a reader opens the card to ask. The
new row is `Rests on`, and it quotes the commit message, the request, or the earlier
recorded note that licensed the stated reason.

**The quote never comes from the model.** The reply carries ids — `warrant: ["c1", "a1"]`
— and codoc looks each one up in the block it sent. Asking the model to repeat the
evidence back would let a paraphrase drift toward the claim it is supposed to check, and
that is the one direction an error must not travel for free. What is stored is the
resolved quote, so the row on the card is a quotation rather than a summary of one.

**An invented citation leaves the op unwarranted, not falsely warranted.** An id that is
not in the index — a hallucinated commit, a stale id from a previous pass — resolves to
nothing and is dropped. There is no error and no retry: a missing warrant is a *normal*
state, so failing a pass over one would spend a user's tree update on a formality. The
failure mode is losing a real citation, never gaining a fake one.

**The author's own prompt is the strongest source, so it had to be citable.** §2 of the
rationale note is explicit that a person saying what they want, while they want it, beats
a commit message written afterwards. A warrant system that could cite only the three
recorded sources would warrant only the weaker evidence. `author_intent` is stamped with
`a1`… at the Loop A call site — not inside `relevant_intent`, because Loop B's directive
builder consumes that same function's output as plain strings and has no citation to
make. Rows are ordered by directness rather than by the order the model cited in: the
ask, then the request, then the commit, then the earlier note.

**Absence is the answer, and it is the common case.** Most descriptions report what code
achieves and make no claim about a decision — Rule 7 says so, and an observation needs no
warrant. So no row is drawn, and the prompt says outright to cite nothing when there is
no why claim. A `Rests on: —` row would turn the ordinary sentence into a defect, which
is how a reader learns to stop reading a field. Ops that write no prose carry no warrant
at all: a citation on an `attach` has no claim to support.

**The ground belongs to the reason it was offered for.** One save can produce several
ops in a single timeline moment. Pairing the card's `Why` row with a neighbouring op's
warrant would offer evidence for a claim it was never offered for, so the warrant comes
from the same entry that supplied the reason — and a warrant stands alone only when
nothing recorded a reason at all.

## F. The evals that matter

**From** RepoSummary's coverage metric and PRELUDE's edit-distance cost.

1. **Feature coverage** — against a repo whose maintainers wrote real
   architecture documentation, what fraction of the features they named appears
   in the generated tree.
2. **Edit-distance cost trend** — for descriptions a human later edited, the
   normalized distance between what codoc wrote and what the human left. Falling
   over time is the claim that the style memory works. Wired (A):
   `loop/voice.py::edit_cost_trend` computes it from the edit records the style
   memory already keeps, and `codoc voice` prints it through `render_trend`, so
   this one also accrues from ordinary use.
3. **Prose gate defect rate** — how often a fresh sample trips the critic. A
   proxy for readability that needs no human in the loop. Wired (B): recorded at
   `apply_op` and read back by `codoc status`, so the number accrues from ordinary
   use rather than from a benchmark run.

**Blocked on credit, not on code.** Only (1) needs real model passes — it has to
generate a tree for a repo it has never seen — and the OpenAI balance is exhausted,
so `tests/loop/test_end_to_end.py` and `tests/bdd/test_e2e_userflows.py` fail on a
429 today. (2) and (3) are wired and need no benchmark run: both read what ordinary
use already records. Everything deterministic is green.
