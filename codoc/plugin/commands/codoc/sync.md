---
description: Sync codoc and the codebase in whichever direction is currently out of date.
---

You are running the **codoc unified sync**. One command, the right direction:
codoc reads the current pipeline state and you act on it. The codoc MCP server is
available.

## 1. Read the state
- Call `codoc_status`. Note `state` (one of `in_sync`, `code_drift`, `tree_dirty`,
  `awaiting_impl`, `realizing`), `pending`, and `unrealized`.
- Note `doc_language` too. When its `code` is not `en`, every `title`,
  `description`, and `rationale` you write below goes in that language — the tree
  is one document and must read as one. The code you write is unaffected: match the
  surrounding files, and never translate an identifier, path, or `codoc:` target.

## 2. Dispatch on the state

### `awaiting_impl` — codoc → code (accepted tree edits are queued)
The user accepted code-implying tree edits; directives are waiting in
`.codoc/realize.md`. Implement the queue now.

**Read the queue.** Read `.codoc/realize.md`. It contains a numbered list of
directives, each one of `NEW FEATURE` / `UPDATE FEATURE` / `RETIRE FEATURE` /
`STEER FEATURE`, with an `Intent:` / `New intent:` / `Author note:` line, the
currently-`Bound code:`, and an `Edit only:` scope. Each `### N.` heading
carries a directive id like `⟨d-1a2b3c4d⟩` — note it; you pass it back as
`caused_by` when you reflect that directive's code. Three optional signals:
- `STEER FEATURE` = an inline `> …` comment the user addressed to you; the note
  wins over the feature's description where they conflict.
- `Focus:` = phrases the user **bolded** — the highest-priority part of the
  intent.
- `Consult:` = an external page; fetch it with WebFetch and read it before
  implementing that item.

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
- After reflecting each directive, **re-read `.codoc/realize.md`** — the queue
  can GROW while you work (the user edits the tree mid-flight). Implement any
  newly appended items too.
- After the last directive, call `codoc_realize_progress(done=N, total=N)`.

**Never delete `.codoc/realize.md` or `.codoc/realize.json`.** The queue closes
ITSELF: every `codoc_reflect` that cites a directive's `caused_by=<d-id>` marks
that item done, and codoc removes both files once every item has evidence.
(Deleting them by hand once destroyed a directive the user appended while an
agent was finishing — the ask vanished with no trace.) If a directive could not
be satisfied, leave it queued, explain why, and tell the user — an item left in
the queue is the honest state, not a failure to clean up.

### `tree_dirty` — codoc → code (tree edited, not yet queued)
`tree.codoc` has intent edits the loop hasn't turned into directives yet. Run
`codoc sync` in the terminal yourself — it applies the tree edits and drains
code-implying ones into `.codoc/realize.md`. Then call `codoc_status` again and
proceed per the new state (usually `awaiting_impl` above). Do not hand-edit
`.codoc/` yourself.

### `code_drift` — code → codoc (code changed, codoc is stale)
Code moved ahead of the tree; there are `pending` proposals and/or unattributed
code. **Reconcile the tree to the code you can see:**
- Call `codoc_context(files=[…])` with the files that changed to review the
  relevant features (use `codoc_tree` only if the drift is repo-wide); pending
  proposals ride along in either read.
- For code you wrote/changed that belongs to an existing feature, `codoc_attach` it.
- For genuinely new intent, `codoc_reflect` with `add_node` ops (they surface as
  proposals for the user to accept).
- When you author a `description`, you can use the same signals humans do:
  `**bold**` marks a phrase as the highest-priority intent (promoted to a
  `Focus:` line in future directives) and `[label](https://…)` cites a page to
  consult (promoted to `Consult:`). Use them sparingly, where they carry weight.
- Summarize the pending proposals so the user can Accept/Reject them in the IDE.

### `realizing` — already in progress
Another realize pass is genuinely running right now — this state is lease-verified
(codoc only reports it while the pass has written progress within the last few
minutes; a crashed or cancelled pass decays out of this state on its own). Report
progress (`pending` remaining) and stop; don't start a second pass. If you believe
this is wrong (e.g. you know the other session died), just wait a few minutes and
re-run — no manual cleanup needed.

### `in_sync` — usually nothing to do
Check `held_drafts` first. Non-zero means the author edited descriptions and the
edits are CAPTURED but not handed off — codoc holds them as drafts until the
author presses **Commit & send** (⌘S) in the tree editor. Do not implement a held
draft uninvited. Tell the author, in one sentence, that their edit is saved and
waiting, and that pressing Commit & send hands it to you — or that they can ask
you here directly and you will treat the draft's text as the instruction. If they
confirm in chat, read `.codoc/realize.json` for the draft's `text` and implement
it as if it were a queued directive, passing its `id` as `caused_by`.

When `held_drafts` is 0, report that codoc and the codebase are in sync and stop.

## 3. Confirm
- After acting, call `codoc_status` again and report the resulting state (ideally
  back to `in_sync`, or `code_drift` if your reflection raised fresh proposals).
