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
`.codoc/realize.md`. **Run the realize loop**: follow `/codoc:realize` — read the
queue, implement each directive one at a time (smallest change, only the named
files), `codoc_attach`/`codoc_reflect` to bind after each, call
`codoc_realize_progress(done, total, current)` as you go, then delete
`.codoc/realize.md`.

### `tree_dirty` — codoc → code (tree edited, not yet queued)
`tree.codoc` has unsaved intent edits the loop hasn't turned into directives yet.
Tell the user to let `codoc watch` (or `codoc sync` in the terminal) apply the tree
edits first — that drains them into `.codoc/realize.md` and flips the state to
`awaiting_impl`. Then proceed as in the `awaiting_impl` case. (Do not hand-edit
`.codoc/` yourself.)

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
