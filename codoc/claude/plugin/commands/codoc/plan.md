---
description: Plan a code change as codoc feature edits first, then implement once accepted.
argument-hint: <task description>
---

Plan this task as edits to the codoc feature tree. Do not edit code until the
person accepts the plan.

> $ARGUMENTS

## 1. Read

- `codoc_context(files=[…])` with the files the task will touch, or `codoc_tree` when the task is tree-wide.
- Both return each feature's description as `blocks` with ids, and `doc_language`. Write the plan in that language.
- For each thing the task implies, check whether a feature already covers it. Most tasks change what an existing feature does.

## 2. Propose

One call: `codoc_propose(ops=[…], note="<what the plan changes, in one plain line>")`.

| to | edit |
|---|---|
| change what a feature does | `{"kind": "amend", "feature_id", "blocks": [{"id", "text"}, {"id", "delete": true}, {"text"}]}`: list only the blocks you change |
| add intent nothing covers | `{"kind": "add_node", "id": "n1", "parent_id", "title", "description", "bindings"}`; `bindings` only for code that already exists |
| regroup | `{"kind": "move_node", "feature_id", "parent_id"}` |

- Write each block as it should read after the change, not as a note about the change.
- A description is an implementation sketch in Markdown: one plain sentence, then bullets for the rules, a numbered list where order matters, a table for parallel cases. The codoc skill has the full guide.
- Titles are 3 to 6 words.

## 3. Stop

Say: "Accept the plan in the codoc tree to approve it, or reject it to discard.
Accepting hands the work to me." Then end your turn.

When the person accepts, the feature is queued as a hand-off. `/codoc:realize`
builds it; cite the hand-off's `d:…` id as `caused_by` when you reflect.
