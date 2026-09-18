---
description: Plan a code change as codoc feature edits first, then implement once accepted.
argument-hint: <task description>
---

You are running the **codoc plan-first loop** for this task:

> $ARGUMENTS

codoc exposes the `codoc` MCP server. **Do not edit any code until the person
accepts the plan.**

## 1. Understand the current tree
- Call `codoc_context(files=[…])` with the files the task will touch, or
  `codoc_tree` when the task is tree-wide. Both return each feature's
  paragraphs by block id and `doc_language`; write the plan in that language.
- For each thing the task implies, ask: does a feature for this intent already
  exist? Most tasks change what an existing feature does.

## 2. Propose the plan — amend first, add only when you must
One call: `codoc_propose(ops=[…], note="<why, in one line>")`.
- To change what an existing feature does: `{"kind": "amend", "feature_id",
  "blocks": [{"id": "<block id>", "text": "<the paragraph as it should read once the
  work lands>"}, {"id": "<block id>", "delete": true}, {"text": "<a new paragraph>"}]}`.
  Name only the paragraphs you change; one you do not mention is unchanged. Write
  descriptions as they should read AFTER the change, never as notes about it.
- To add intent no feature covers: `{"kind": "add_node", "id": "n1", "parent_id"?,
  "title", "description", "binds"?: ["file.py::symbol"]}`. Titles are 3–6 words.
- To move: `{"kind": "move_node", "feature_id", "parent_id"}`.

## 3. Hand the plan to the person and stop
Say: **"Accept the plan in the codoc tree to approve it, or Reject to discard.
Accepting hands the work to me."** Then end your turn. When they accept, the
feature is handed off and `/codoc:sync` (or the nudge you receive) implements it
under the `queued` branch; cite the item's `d:…` id as `caused_by` when you reflect.
