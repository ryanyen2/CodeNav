---
description: Sync codoc and the codebase in whichever direction is currently out of date.
---

You are running the **codoc unified sync**. One command, the right direction:
codoc reads the current pipeline state and you act on it. The codoc MCP server is
available.

## 1. Read the state
- Call `codoc_status`. Note `state` (one of `in_sync`, `code_drift`, `tree_dirty`,
  `awaiting_impl`, `realizing`), `pending`, and `unrealized`.

## 2. Dispatch on the state

### `awaiting_impl` — codoc → code (accepted tree edits are queued)
The user accepted code-implying tree edits; directives are waiting in
`.codoc/realize.md`. Implement the queue now.

**Read the queue.** Read `.codoc/realize.md`. It contains a numbered list of
directives, each one of `NEW FEATURE` / `UPDATE FEATURE` / `RETIRE FEATURE`, with
an `Intent:` / `New intent:` line, the currently-`Bound code:`, and an
`Edit only:` scope. Each `### N.` heading carries a directive id like
`⟨d-1a2b3c4d⟩` — note it; you pass it back as `caused_by` when you reflect that
directive's code.

**Implement each directive (minimum surgical change), sequentially.** Reflect
each one before starting the next — this lets the IDE's documentation view fill
in each feature as you finish it, rather than all at once at the end. For
directive *i* of *N*:
- Call `codoc_realize_progress(done=i-1, total=N, current="<feature title>")` as
  you **start** it, so the IDE shows "implementing i of N".
- Apply the **smallest** code change that satisfies its intent.
- **Edit ONLY the files named in that item's `Edit only:` line.** If an item
  names no files, create the smallest new file/symbol that fits — do not
  refactor unrelated code to host it.
- **NEVER edit anything under `.codoc/`** — that is codoc's own state. In
  particular do not touch `.codoc/tree.codoc`; codoc maintains feature
  descriptions itself.
- Do not rename or reword features/symbols no directive asked you to change.
- Immediately call `codoc_reflect(ops, rationale, caused_by="<that directive's
  d-id>")` to attach the code you wrote/changed to the feature it realizes
  (`attach` with `binds` of `"file.py::symbol"`). Binding flips an accepted plan
  placeholder to **realized** and resolves its skeleton in the IDE. The
  `caused_by` id is how the IDE groups your changes under the user's originating
  edit ("↳ from your edit") — always pass it. If you implemented anything beyond
  the queued directives, include `add_node` ops in the same `codoc_reflect` call
  (same `caused_by`) so it surfaces as a proposal grouped under that edit.
- After the last directive, call `codoc_realize_progress(done=N, total=N)`.

**Clear the queue.** Delete `.codoc/realize.md` AND `.codoc/realize.json` (the
directive manifest — leaving it behind keeps the implemented features on
doc-wins hold). If a directive could not be satisfied, leave its entry in
`.codoc/realize.md`, explain why, and tell the user.

### `tree_dirty` — codoc → code (tree edited, not yet queued)
`tree.codoc` has intent edits the loop hasn't turned into directives yet. Run
`codoc sync` in the terminal yourself — it applies the tree edits and drains
code-implying ones into `.codoc/realize.md`. Then call `codoc_status` again and
proceed per the new state (usually `awaiting_impl` above). Do not hand-edit
`.codoc/` yourself.

### `code_drift` — code → codoc (code changed, codoc is stale)
Code moved ahead of the tree; there are `pending` proposals and/or unattributed
code. **Reconcile the tree to the code you can see:**
- Call `codoc_tree` to review existing features and the pending proposals.
- For code you wrote/changed that belongs to an existing feature, `codoc_attach` it.
- For genuinely new intent, `codoc_reflect` with `add_node` ops (they surface as
  proposals for the user to accept).
- Summarize the pending proposals so the user can Accept/Reject them in the IDE.

### `realizing` — already in progress
Another realize pass is running. Report progress (`pending` remaining) and stop;
don't start a second pass.

### `in_sync` — nothing to do
Report that codoc and the codebase are in sync and stop.

## 3. Confirm
- After acting, call `codoc_status` again and report the resulting state (ideally
  back to `in_sync`, or `code_drift` if your reflection raised fresh proposals).
