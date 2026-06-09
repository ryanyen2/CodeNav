# codoc — Collaborative Editing Model

The conceptual model for the doc surface: one rich-text document, shared between a
human and one or more AI agents (claude-code, codex, gemini, cursor, …), kept in
sync with the codebase. This is the spec the editor implementation must follow.

The model has **two orthogonal axes**. Conflating them was the original mistake.

---

## Axis 1 — Authorship (pen / pencil): *who owns settled text, and how committed*

A property of text that is **already settled** in the document.

| | meaning | AI may… | visual |
|---|---|---|---|
| **pen** | solid, committed intent | only **propose** a change (→ a diff) | full opacity |
| **pencil** | tentative — "not sure, take over" | **revise it directly** | faded opacity |

- Carried per-span as `{ role, mode }`. `role` ∈ {human, claude-code, codex, …} drives **tint**; `mode` ∈ {pen, pencil} drives **opacity**.
- A human writes in pen by default, or switches to pencil to invite AI to take over. AI-authored text is pencil by default.
- **Pen/pencil is the *gate*** that decides whether an incoming edit is applied directly or must become a diff (see Axis 2). It is *not* itself a pending state.

## Axis 2 — Agreement (diff / suggestion): *where doc and code disagree, and who must catch up*

A **diff** is a persistent annotation that the document and the codebase do **not
yet agree** at this span. It is rendered as a tracked change (inserted / deleted /
replaced) and **does not disappear when you finish typing** — it stays until the
side that is *behind* catches up. There are exactly two directions:

| direction | arises when | resolved by | resolution mechanism |
|---|---|---|---|
| **code-ahead** ▲ | an agent changed the code and reflected it back, or proposed a plan; or an agent wants to change a **pen** span | **human** accepts / rejects | `.codoc/inbox.json` verdict (Loop A) |
| **doc-ahead** ▼ | a human changed intent that the code must follow (a suggestion / an imperative edit) | **agent** implements it in code | `.codoc/realize.md` directive (Loop B) → reflect |

A diff carries `{ direction, kind, origin-role, status, event-id? }`. `kind` ∈
{insert, delete, replace} for prose, and {add, move, retire, amend} for structure.

> **This is the unifying idea:** a diff is *doc↔code disagreement at a span*,
> resolved by whichever side is behind. Today's structural "ghosts"
> (add/move/retire/amend proposals) are simply **code-ahead diffs**. There is one
> mechanism, not four special cases.

The two axes compose: pen/pencil decides *whether* an edit is direct or becomes a
diff; the diff then carries its own origin + resolver, regardless of the span's
pen/pencil.

---

## The document is the tree

One rich-text document **is** the feature tree:

- **Headings** = feature nodes; heading depth = parent/child. Editing a heading's
  text = AMEND title; indenting / outdenting = MOVE; a new heading = ADD; deleting
  / marking = RETIRE.
- **Body** under a heading = that feature's description (prose + inline `codeRef`
  chips). `@`-autocomplete inserts a ref from the AST-bound symbols.
- Marks: bold / italic / highlight, plus **comment** (a future "ask the LLM for a
  higher-level edit" channel), plus the two-axis state above.

## States a span can be in

```
SETTLED        normal text — carries { role, mode } authorship
SUGGEST-INSERT proposed new text, not yet in the canonical doc   (a diff)
SUGGEST-DELETE existing text marked for removal, not yet gone    (a diff)
SUGGEST-REPLACE delete + insert                                  (a diff)
```

Structural (heading) states: `SETTLED` · `SUGGEST-ADD` (ghost heading) ·
`SUGGEST-MOVE` (shown at destination) · `SUGGEST-RETIRE` (struck heading) ·
`SUGGEST-AMEND` (title/desc diff). Each suggestion is code-ahead **or** doc-ahead.

Lifecycle: `pending → accepted (folds into settled text) | rejected (removed)`.
A code-ahead diff is resolved by the **human**; a doc-ahead diff is resolved when
the **agent** lands the code (then it folds in, possibly spawning follow-up
code-ahead diffs for details the human under-specified — "code is more precise
than intent").

## Edit controls

Two independent selectors (kept separate on purpose):

1. **Instrument: pen ⇄ pencil** — the authorship mode stamped on text you settle.
2. **Mode: editing ⇄ suggesting** — like Google Docs. In *editing* mode your
   changes settle directly (and if they imply code, a doc-ahead diff is raised so
   the agent catches up). In *suggesting* mode every change is a tracked doc-ahead
   diff from the start.

AI never gets a toggle: AI edits to **pencil** spans settle directly; AI edits to
**pen** spans (or plan proposals, or post-code reflections) raise **code-ahead**
diffs for the human.

---

## User flows

1. **Human edits a description directly (editing, pen).** Text settles. If the
   edit implies code change, a doc-ahead diff is raised on it → agent implements →
   diff folds away when code agrees.
2. **Human restructures the tree** (rename heading / indent / new heading / delete).
   Same as (1) at the structural level (AMEND / MOVE / ADD / RETIRE).
3. **Human suggests (suggesting mode).** Edits become doc-ahead diffs immediately,
   nothing settles. On *apply*, directives queue → agent implements → agent resolves
   the diffs, and proposes follow-up **code-ahead** diffs for unspecified details.
4. **Agent changes code (in the IDE / via a directive), reflects back.** Loop A
   raises **code-ahead** diffs on affected descriptions ("this prose is now stale →
   here's the update"). Human accepts/rejects inline. Accepted text settles as
   pencil + agent-role; rejected reverts.
5. **Agent proposes a plan** (`/codoc:plan`). Code-ahead **add/amend** diffs (new
   nodes, refined prose). Human resolves; accepted add-nodes become unrealized
   placeholders that realize when code binds.
6. **Agent edits a human's pen span.** Cannot overwrite → raises a code-ahead diff.
   Editing a pencil span instead → settles directly (no diff).
7. **Comment.** Human highlights a span and comments a higher-level instruction;
   later this asks the LLM to produce a diff (Phase 2+).

---

## Persistence & data model

- **`.codoc/tree.doc.json`** (authoritative rich doc) holds settled text +
  authorship marks **+ pending diffs**. Diffs are first-class doc state, not UI.
- **`.codoc/tree.codoc`** (derived) is the *settled* projection the loops consume.
  Code-ahead diffs already live in the store as proposal events; doc-ahead diffs
  drive realize directives. The canonical text contains only settled content, so
  the existing parse→diff→apply round-trip is unchanged.
- A prose diff is a ProseMirror mark `suggestion { direction, kind, role, eventId,
  status }`; a structural diff is the existing proposal event surfaced inline.
- Resolution reuses existing machinery: **code-ahead → `inbox.json`** verdicts;
  **doc-ahead → `realize.md`** directives. No new transport.

## What changes from the current implementation

- **Per-section editor → one whole-doc editor** with headings as the tree. The
  read-view decorations (binding rail, cross-refs, activity, TOC/scroll-sync)
  rehome as ProseMirror widgets / a side panel / heading-anchored decorations.
- **"Done" no longer clears the diff.** In editing mode it settles text (raising a
  doc-ahead diff if code must follow); in suggesting mode it leaves a persistent
  doc-ahead diff. Diffs only vanish on resolution by the correct party.
- **Structural ghosts unify** with prose diffs under one "code-ahead vs doc-ahead"
  renderer + inline accept/reject (human) / await-implementation (agent).
- Pen/pencil keeps the U6 meaning (authorship of settled text) and additionally
  acts as the gate that turns AI-on-pen edits into code-ahead diffs.
