---
description: Sync codoc and the codebase in whichever direction is currently out of date.
---

You are running the **codoc sync**. One command, the right direction: read the
state, act on it. The codoc MCP server is available.

## 1. Read the state
- Call `codoc_status`. Note `state` (one of `synced`, `edited`, `queued`,
  `realizing`, `proposed`) and `queued_list`.
- Note `doc_language`. When its `code` is not `en`, every title, paragraph and
  note you write goes in that language. Code is unaffected: match the files you
  edit, and never translate an identifier, a path or a `codoc:` target.

## 2. Dispatch on the state

### `queued` — the person handed edits to you; build them
Read `.codoc/realize.md`. Each `### ⟨d:…⟩ Title` item is one feature: its
description as the person wants it to read, what changed since the last time
it was built (`added:` / `removed:` lines), and up to four signals:
- `Focus:` — phrases the person **bolded**: the highest-priority part.
- `Consult:` — an external page; WebFetch it before implementing that item.
- `Edit only:` — the code the person cited inside a comment: stay inside it.
- `Author asked:` — the person's own words; where prose and ask differ, the ask wins.
A `REMOVE FEATURE` item says whether to delete the code or just stop tracking it.

Implement each item with the smallest change that satisfies it, one at a time:
- Call `codoc_realize_progress(done=i-1, total=N, current="<title>")` as you start it.
- Never edit anything under `.codoc/`.
- Do not rename or reword anything no item asked you to change.
- After the code for an item is written, call `codoc_reflect(ops=[…],
  caused_by="<that item's d:… id>")` with `attach` ops binding the symbols you
  wrote or changed (`"file.py::symbol"`), and an `amend` only if a paragraph
  now states something the code no longer does (name the paragraph by its block
  id from `codoc_context`). The `caused_by` is what marks the item realized;
  the queue closes itself.
- Re-read `.codoc/realize.md` after each item: it can grow while you work.
- After the last item, call `codoc_realize_progress(done=N, total=N)`.

If an item cannot be satisfied, leave it queued, say why, and stop.

### `edited` — the person has unsent edits
Their edit is saved and waiting for them to hand it off (⌘S in the editor, or
`codoc handoff`). Do not implement it uninvited. Tell them in one sentence, or,
if they ask you here directly, implement it and reflect with the feature's id.

### `proposed` — plans await the person's verdict
Summarize the open plans (`codoc_tree` lists them) so the person can Accept or
Reject in the editor. Do nothing else.

### `realizing` — another pass is running
Report the remaining count and stop.

### `synced` — reconcile only if you changed code this session
Call `codoc_context(files=[…])` for the files you touched. For code that belongs
to an existing feature, `codoc_reflect` with `attach`. For genuinely new intent,
`codoc_propose` with `add_node` ops (they become plans). Otherwise report that
codoc and the code are in sync.

## 3. Confirm
Call `codoc_status` again and report the resulting state.
