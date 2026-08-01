# Writing plan — the "System" section (CoDoc)

A blueprint for the System section, revised against the UIST poster and your three
directions. The section sits after the Introduction and the higher-level Related Work,
and before the detailed mechanism. Its job is to present the design from inside the
Visual Studio Code extension, through the one running example the poster already uses,
and to justify each design choice in the same breath by contrasting it with existing
approaches at the point where the choice is made.

This is a planning document, so its own headings use ordinary punctuation. The style
rules apply to the paper prose. The fully drafted samples in Section 8 obey them and are
meant to fix the register.

---

## 0. What changed from the first draft of this plan

Three changes, matching your points.

1. **Register.** The earlier samples were short and abstract. The new target is the
   poster's own register, which is plain, long, and concrete. Section 2 recalibrates the
   voice and shows before-and-after rewrites.
2. **Related work is woven in, never a block.** There is no related-work paragraph inside
   the System section. Each walkthrough step names the prior approach at the moment its
   design choice is justified, and says plainly why CoDoc goes a different way.
3. **Walk the interface, not an abstract tree.** The scene is the CoDoc Tree editor in VS
   Code, with the document on the left and the code on the right, and it follows Alicia
   making edits in the document and in the code and watching each side move the other.
4. **Example aligned to the poster.** The spine is now Alicia making the small coding
   agent's model calls survive a flaky local Ollama server, the same scenario the poster
   develops. See Section 9 to confirm.

---

## 1. Where this section sits and what it does

The Related Work above has already argued the intellectual frame, that intent and code
have long been treated as alternatives and that CoDoc keeps them together and derived
from each other. This section makes that argument concrete by showing it happen in the
interface. It opens with one paragraph that establishes the interface and the core
concepts, then walks a single task through the two directions of editing, and closes on
how the two stay consistent. Full mechanism, the phase order and the storage, belongs to
the section below and appears here only as much as a step needs.

The section carries two whys at once. The first why is the purpose of a concept, which is
usually easy. The second why is the form of the concept, which takes the design choice,
the existing approach it departs from, and the tradeoff, and states them plainly. The
CoDoc history is strong on the second why because it records real reversals, and telling
them plainly reads as candor.

---

## 2. Register and the style rules

**The target register is the poster's.** Sentences are longer than a tweet and shorter
than a paragraph, they carry concrete nouns from the scene, and they explain as they go.
The poster is the model for flow and plainness. Note that the poster itself uses colons,
em-dashes, and terse contrasts, so it is a register reference and not a punctuation
reference. The paper prose still obeys the four rules.

**Hard rules for paper prose.**

1. No colon anywhere in a sentence.
2. No parentheses. Fold the aside into the sentence.
3. No em-dash. Use a full stop, or a comma with a conjunction.
4. No terse "X, not Y" fragment. Write the contrast as full clauses, one saying what is so
   and one saying what is not, joined with a plain word such as "rather than," "instead
   of," or "whereas," or split across two sentences.
5. Every technical claim traces to a source in Section 5.
6. Plain and long beats clipped and abstract. Prefer a sentence that a maintainer could
   have said out loud while doing the task.

**Calibration, before and after.**

- Too short and abstract, the thing to avoid: "A codebase carries intent that lives
  nowhere the code can hold it, and that intent drifts silently. The tree is a navigable
  home for it."
- In register: "When Alicia takes over the coding agent, the code shows her what each
  function does but leaves out why the model client was written to give up after a single
  failed request, and that reasoning is exactly what she needs before she can change it
  safely, so CoDoc keeps it in a document that sits beside the code and stays bound to it."

---

## 3. The running example, walked through the interface

**Spine.** Alicia has taken over a small coding agent of a few thousand lines that runs a
read-think-act tool-use loop against a locally hosted model. Users report that the local
model server is flaky and that one dropped request aborts an entire session, so her task
is to make the model calls resilient by adding retry with exponential backoff and a test
for the retry path. This is the poster's scenario, and it is a good spine because the task
is honest, its steps exercise both editing directions in order, and the code it touches is
small enough that a handful of features carry the whole scene.

**The interface the scene lives in.** The CoDoc Tree editor is a two-pane view in VS Code.
The left pane is the document CoDoc maintains for the repository, an indented outline of
features where each feature is a named unit of intent with a short description and an
inline link to the code that implements it. The right pane is the source file the feature
governs, and clicking a citation jumps there. As the agent works, the feature it is
touching shows a live attribution such as "editing mini_coding_agent.py," and a
requirement the person wrote is rendered inline as tracked, diff-style prose. A terminal
below shows the plugin talking to the live coding session. The walkthrough should refer to
these surfaces by what Alicia sees, so the reader is always located in the interface.

**Grounding to-do.** Reconcile the feature names with the real bootstrapped tree for this
repository so the citations and titles in the prose are the ones a reader would see, the
way the poster names "Ollama model backend client" and the `OllamaModelClient` class. Do
not invent titles or code symbols.

---

## 4. Structure, one concept paragraph then a walkthrough

### Paragraph A — the interface and the concepts

- **What it does.** Put Alicia in the CoDoc Tree editor, describe the two panes, and
  introduce the primitives she will use, which are the feature, the binding, the inline
  citation, and the live correspondence between a section of the document and its slice of
  the code. Say how the outline is built, by parsing the repository into an abstract
  syntax tree, grouping chunks into features, and nesting them by how the code is
  organized, so the outline follows the shape of the codebase.
- **Why this form, with related work woven in.** This is where the paper earns the claim
  that the document is worth authoring. Contrast the companion files developers keep now,
  a CLAUDE.md file, a set of Cursor rules, or a memory bank, which are the agent's
  summaries of code the developer never condensed, so they turn into change logs a person
  cannot trust or cleanly edit. Contrast RPG-Encoder, which keeps a live capability graph
  but builds it for an agent to traverse instead of for a person to read and edit.
  Contrast SpecLang, whose natural-language spec is written for the model and drifts from
  the code. Close on the CoDoc stance, a live correspondence grounded in a document a
  person authors and edits, which follows Heer's argument that automation should be
  arranged around a representation the person still holds.

### The walkthrough, each step is operation then how it works then why this way

Each step names the operation Alicia performs in the interface, explains the mechanism
plainly, and justifies the design against the approach it departs from.

- **Step 1 — Editing intent in the document.** Operation, Alicia reads the client, finds
  that the generate call sets a timeout and never retries, and writes the fix as note
  lines under the feature that describes the client, one asking for retry with backoff and
  one asking for a test. How it works, CoDoc reads these lines as intent aimed at that
  feature, marks them as a pending edit, holds them as a draft that changes no code and
  spends no budget, and resolves and fetches the runbook she linked so the instruction and
  its material travel together, while each line keeps a stable place and a version. Why
  this way, holding the edit as a draft keeps the person in control, whereas earlier
  specification tools consumed the description on their own the moment it was written and
  let the spec run ahead of the code, and autonomous agents act before the person has
  decided the change is right.
- **Step 2 — Handing off, and the code follows.** Operation, Alicia hands the draft to the
  agent and reviews the result. How it works, the hand-off starts the document-to-code
  direction, CoDoc classifies the edit as one that implies code, assembles a directive from
  the feature's description, its bound symbols, and its consulted links, and queues it for
  the live coding session, which reads the runbook, wraps the call in retry with backoff,
  adds the test, and returns an ordinary diff whose changes appear inline as tracked edits
  Alicia accepts or rejects, with the feature-to-code links updated alongside. Why this
  way, CoDoc never runs a headless model and instead does the work in the person's own live
  session so the same turn that writes the code also binds it, and the explicit hand-off is
  what lets the person decide what becomes code. A second contrast lands here, that
  intentional software and projectional editors made intent primary but forced people to
  edit a structured tree where ordinary diffing and version control broke, whereas Alicia
  keeps typing freely in a document and her diffs keep working.
- **Step 3 — A code change surfacing back into the document.** Operation, the retry logic
  lands in a new helper that no feature owns, and later Alicia also edits code by hand, so
  the code and the outline no longer line up and CoDoc raises a proposal for her to review
  as a ghost row with inline accept and reject. How it works, the code-to-document
  direction diffs the fresh index against the current bindings and resolves most changes
  mechanically, refreshing a modified binding, detaching a removed chunk, and re-attaching
  a chunk that reappears elsewhere so attribution follows moves and renames, while only
  genuinely new code or a feature that lost its last binding goes to a single model call
  that sees the change, the surrounding tree, and every existing feature title, which is
  what keeps it from minting duplicates. Wording and binding fixes apply on their own,
  while add, move, and retire become proposals a person accepts. Why this way, everyday
  code is regular enough for a model to judge which code serves which feature, so CoDoc
  never needs the exact reversible grammar that made single-source literate-programming
  tools and round-trip editors brittle, and because both sides move incrementally Alicia
  reviews a small diff rather than a regenerated document she must read from scratch.
- **Step 4 — Keeping the two consistent.** Operation and result, Alicia finishes with a
  correct outline and a working fix, and the reason for the fix, down to the runbook link
  and the bolded requirement, stays recorded on the feature. How it works and why, a
  feature with pending document-side intent enters a hold set, and while it is held CoDoc
  suppresses code-side edits to its wording and structure so an in-flight human edit is
  never overwritten, while binding fixes still run because they only record where the code
  lives. The document expresses intent and the reflector only observes it, so the document
  side wins whenever the two disagree. Keep this step short, and if space is tight fold its
  point into the close of Step 3.

---

## 5. Claim to source traceability

Every claim the prose can make, grounded in the poster, a design doc, or the code. Prefer
the code citation when a doc disagrees, and see Section 7 for stale prose to avoid.

| Claim in prose | Source |
|---|---|
| The scenario, a small coding agent, a flaky local model server, retry with backoff and a test | poster Section 3; scenario is reproducible on the repo's own corpus |
| Two-pane CoDoc Tree editor, document left and code right, click a citation to jump | poster Figure 2; `vscode-codoc/` `providers/tree-editor.ts`, `doc-links.ts` |
| Feature is a named unit of intent with a description and inline code citations | `CONCEPTS.md`; `README.md`; poster Section 3 |
| A binding attaches each chunk to at most one feature; the map is the correspondence | `UNIQUE(file, symbol_path)` in `codoc/store/db.py`; poster Section 4 |
| Outline built by parsing to an AST, grouping chunks into features, nesting by code organization | poster Sections 1 and 3; `codoc/loop/bootstrap_hier.py` |
| Companion files today are agent summaries the developer did not condense and cannot cleanly edit | poster Section 1 |
| RPG-Encoder keeps a live capability graph built for an agent to traverse | poster Section 2, ref [4] |
| SpecLang keeps a spec authored for the model that drifts from the code | poster Section 2 |
| Automation should center a representation the person still holds | poster ref [2], Heer |
| A document edit is held as a draft that changes no code and spends no budget until hand-off | `docs/brainstorms/2026-06-18-...`; `docs/codoc-change-ledger.md` rows 7 and 8; `codoc/loop/loop_b.py` `_EXPLICIT_REALIZE_KINDS` and `handoffs`; poster Section 3 |
| Note lines are steering notes, a bolded span is a requirement, a linked runbook is consulted before implementation | `README.md`; `codoc/codoc_file/parse.py`; poster Figure 1 and Section 3 |
| A pending edit keeps a stable place and a version and stays attached to its feature | poster Section 3; per-feature HLC in `codoc/model/hlc.py` |
| Hand-off starts the document-to-code direction, builds a directive from description, bound symbols, and consulted links, and queues it for the live session | poster Section 4; `docs/architecture.md`; `codoc/loop/loop_b.py` |
| CoDoc never runs a headless model; the work happens in the person's live session and the same turn binds | `CLAUDE.md`; `docs/architecture.md` realization trigger |
| The agent's edits appear inline as tracked changes accepted or rejected, links updated alongside | poster Section 3; `vscode-codoc/src/webview/tiptap/track-changes/` |
| Intentional software and projectional editors made intent primary but broke free typing and diffing | poster Section 2, refs [6] and [7] |
| Code-to-document diffs the index against bindings, refreshes, detaches, and re-attaches across moves and renames mechanically | poster Section 4; `codoc/loop/loop_a.py` `derive_auto_ops`, `_detect_relocations` |
| Only new code or a lost-last-binding goes to a single model call that sees the change, the tree, and every feature title, which prevents duplicates | poster Section 4; `codoc/prompts/tree_update.txt` rules 2 and 3; `codoc/agent/tree_update.py` |
| Wording and binding fixes apply on their own; add, move, and retire are proposals a person accepts | poster Section 4; `codoc/model/event.py` `STRUCTURAL_OPS` |
| A proposal renders in place as a ghost row with inline accept and reject | poster Section 3; `codoc/codoc_file/render.py`; `README.md` |
| CoDoc needs no exact reversible grammar because everyday code is regular enough for a model to judge attribution | poster Section 4 |
| A held feature suppresses code-side wording and structure edits while binding fixes still run, so the document wins on conflict | poster Section 4; `codoc/loop/classify.py` `suppressed_by_hold`; `codoc/loop/phase.py` `is_held`; `docs/codoc-change-ledger.md` row 13 |
| The reason for a change, the runbook link and the bolded requirement, stays recorded on the feature | poster Section 3 |

---

## 6. Where each prior approach attaches, and how CoDoc departs

Placement map so related work lands at the right step and never clumps.

- **Paragraph A.** Companion summary files such as CLAUDE.md and memory banks, RPG-Encoder,
  SpecLang, and Heer. Borrow the live correspondence, depart by grounding it in an authored
  document a person reads and edits.
- **Step 1.** Specification tooling that consumes the description on its own, and autonomous
  agents that act before review. Depart by holding the edit as a draft under the person's
  control.
- **Step 2.** Intentional software and projectional editors, refs [6] and [7]. Borrow intent
  as a primary artifact, depart by keeping free typing and working diffs and by realizing in
  the person's live session.
- **Step 3.** Literate programming's single-source coupling, ref [3], and brittle round-trip
  tools. Depart by using a model judgment over regular code instead of an exact reversible
  grammar, and by surfacing incremental diffs instead of a regenerated document.
- **Optional.** Dijkstra's caution against natural-language programming, ref [1], and Sammet,
  ref [5]. If the section wants one sentence of intellectual framing, place it in Paragraph A
  and keep it to a single sentence, since the deeper treatment belongs to Related Work above.

---

## 7. Accuracy guardrails, stale prose to avoid

The code verification pass found repo prose that the implementation has outrun. Do not carry
these into the paper. Note that the poster's line "Imperative prose ... becomes a directive"
is one of them.

- Do not say CoDoc detects imperative or "should" phrasing in prose. That classifier was
  removed. What realizes is decided structurally and gated by an explicit hand-off, so the
  poster's phrasing should be tightened to "an explicit hand-off" rather than "imperative
  prose."
- Do not describe a code-implying edit as immediately queued. The dominant behavior is a
  held draft that reaches the queue only on hand-off. Steering notes, retires, and accepted
  plan nodes are the cases that hand off on creation.
- Do not say edits are inferred from a text or document diff. That inference is retired, and
  every authored edit arrives as an explicit identity-keyed command, with deletion as an
  explicit retire.
- Do not call the feature state a pair of booleans. It is one lifecycle value, and the
  booleans survive only as derived read-only views.
- Prefer "most changes resolve mechanically and only new or orphaned code goes to one model
  call" over reciting a fixed number of loop phases, since the phase count in older docs does
  not map one to one onto the code.

---

## 8. Fully drafted samples, in the target register

These obey all four rules and set the voice. Names and citations are placeholders to
reconcile with the real tree.

**Paragraph A, the interface and the concepts.**

> Alicia has taken over a small coding agent of a few thousand lines that runs a
> read-think-act tool-use loop against a locally hosted model, and her task is to make its
> model calls survive a flaky server, since users report that one dropped request now aborts
> an entire session. She opens the repository in Visual Studio Code and works in the CoDoc
> Tree editor, a two-pane view that places the document CoDoc maintains for the repository on
> the left and the source file it governs on the right. The document is an indented outline of
> features, where each feature is a named unit of intent with a short description of what that
> part of the system is responsible for, and each feature cites the exact code that implements
> it as an inline link she can click to jump to the definition on the right. CoDoc builds this
> outline by parsing the repository into an abstract syntax tree, grouping the resulting code
> chunks into features, and nesting the features by how the code is organized, so the shape of
> the outline follows the shape of the codebase and Alicia can read it the way she would read
> the system itself. This is the property that the companion files developers keep today do
> not have, because a CLAUDE.md file, a set of Cursor rules, or a memory bank is a running
> summary the agent wrote about code the developer never condensed, so it grows into a change
> log a person cannot fully trust and has no clean way to edit, whereas the CoDoc document is
> authored on the same footing by the person and the agent and stays bound to the code it
> describes, so reading it is reading the current system. Recent agent tooling keeps a live
> description too, yet it aims elsewhere, since RPG-Encoder lifts a repository into a
> capability graph built for an agent to traverse rather than for a person to read and edit,
> and SpecLang keeps a natural-language spec written for the model that drifts from the code
> the way older documentation did. CoDoc keeps that same live correspondence and grounds it in
> a document a person authors, reads, and edits directly, which follows Heer's argument that
> automation works best when it is arranged around a representation the person still holds.

**Step 1, editing intent in the document.**

> Reading the client on the right, Alicia confirms what is going wrong, because the call to
> the model's generate endpoint sets a timeout and never retries, so one transient failure
> propagates up and ends the run. She writes the fix where the behavior is described rather
> than in the code itself, adding two note lines under the feature for the model client, one
> asking the agent to retry on a transient error or timeout with exponential backoff up to
> three attempts instead of aborting, and one asking it to add a test for the retry path.
> CoDoc reads these lines as intent aimed at that feature, marks them as a pending edit, and
> holds them as a draft that changes nothing in the code and spends no model budget until
> Alicia decides to act on them, and it resolves the runbook she linked and fetches it so the
> instruction and its supporting material travel together. Each line stays attached to the
> feature it concerns and keeps a stable position and a version in the document, so Alicia can
> leave, come back, and still find the change she asked for exactly where she left it. Holding
> the edit as a draft is a deliberate choice about who stays in control, because earlier
> specification tools treated the description as an input the system consumes on its own the
> moment it is written, which is why their specs quietly ran ahead of the code, and autonomous
> coding agents go further and act before the person has decided the change is right. CoDoc
> instead lets Alicia write freely, see the consequence staged in place, and keep authorship
> of the decision, so nothing reaches the code until she hands it over.

Steps 2, 3, and 4 to be drafted in the same register, following the operation then how then
why order in Section 4.

---

## 9. Open decisions for the author

1. **Confirm the example switch.** The plan now uses the poster's mini coding agent and Ollama
   scenario for continuity with the poster, in place of the `requests` example chosen earlier.
   Both are the same retry-with-backoff task, so this is the codebase, not the task. Confirm or
   revert.
2. **Depth of Step 4.** Keep the consistency step as its own short paragraph, or fold its
   doc-wins point into the close of Step 3.
3. **One line of deep framing.** Decide whether Paragraph A carries a single Dijkstra-or-Sammet
   framing sentence, or leaves all such framing to Related Work above.
4. **Figure use.** The section can lean on the poster's two figures, the tree.codoc fragment and
   the two-pane editor. Decide whether the paper reuses them or draws fresh ones at higher
   fidelity.
