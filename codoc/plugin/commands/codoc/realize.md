---
description: Implement the tree edits that were accepted in the codoc tree (.codoc/realize.md).
---

You are running the **codoc realize loop**. When the user accepts a code-implying
tree edit (an imperative amend, an accepted plan node, or a retire that owns code),
codoc does **not** spawn a separate agent — it queues the work for you, the live
session, in `.codoc/realize.md`. Your job is to implement that queue now.

codoc exposes the `codoc` MCP server. Follow these steps exactly.

## 1. Read the queue
- Read `.codoc/realize.md`. It contains a numbered list of directives, each one of
  `NEW FEATURE` / `UPDATE FEATURE` / `RETIRE FEATURE`, with an `Intent:` /
  `New intent:` line, the currently-`Bound code:`, and an `Edit only:` scope.
- If the file does not exist, there is nothing to realize — tell the user and stop.

## 2. Implement each directive (minimum surgical change)
- Apply the **smallest** code change that satisfies each item's intent.
- **Edit ONLY the files named in that item's `Edit only:` line.** If an item names
  no files, create the smallest new file/symbol that fits — do not refactor
  unrelated code to host it.
- **NEVER edit anything under `.codoc/`** — that is codoc's own state. In particular
  do not touch `.codoc/tree.codoc`; codoc maintains feature descriptions itself.
- Do not rename or reword features/symbols no directive asked you to change.

## 3. Bind the code back to the tree
- Call `codoc_reflect(ops, rationale)` to attach the code you wrote/changed to the
  features it realizes (`attach` with `binds` of `"file.py::symbol"`). Binding flips
  an accepted plan placeholder to **realized**.
- If you implemented anything beyond the queued directives, include `add_node` ops in
  the same `codoc_reflect` call so it surfaces as a new proposal for the user.

## 4. Clear the queue
- Delete `.codoc/realize.md` (the work is done).
- Call `codoc_status` to confirm the pipeline returned to `in_sync` (or `code_drift`
  if your reflection raised fresh proposals for review).

## 5. Report
- Briefly summarize what you implemented and which features are now realized. If any
  directive could not be satisfied, leave its entry in `.codoc/realize.md`, explain
  why, and tell the user.
