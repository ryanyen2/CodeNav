---
description: Bring the codoc tree and the code back in step, in whichever direction is behind.
---

Read the state, then act on it. The `codoc` MCP server is available.

## 1. Read the state

Call `codoc_status`. Note `state` and `queued_list`. Note `doc_language`: when
its `code` is not `en`, everything you write into the tree goes in that
language. Code is unaffected.

## 2. Act

| `state` | meaning | do |
|---|---|---|
| `queued` | the person handed edits to you | build them (below) |
| `edited` | the person has edits they have not handed off | say so in one sentence. Build them only if they ask you here. |
| `proposed` | plans wait for the person's verdict | summarize the open plans (`codoc_tree` lists them). Nothing else. |
| `realizing` | another pass is building | report the remaining count and stop |
| `reported` | you reported every queued item this turn | nothing to do; they settle when your turn ends |
| `synced` | nothing is waiting | reconcile only what you changed this session (below) |

### `queued`: build the items in `.codoc/realize.md`

The file explains how to read an item. For each item, in order:

1. `codoc_realize_progress(done=i-1, total=N, current="<title>")`.
2. Make the smallest code change that satisfies the item.
3. `codoc_reflect(ops=[…], caused_by="<the item's d:… id>")`: `attach` the symbols you wrote or changed; `amend` a block only where it says something the code does not do. This reports the item and you are done with it.
4. Read `.codoc/realize.md` again for items added while you work. An item you reported stays listed until your turn ends; skip it.

After the last item call `codoc_realize_progress(done=N, total=N)`. If an item
cannot be satisfied, leave it, say why, and stop.

### `synced`: reconcile your own changes

Call `codoc_context(files=[…])` for the files you touched.

- Code that belongs to an existing feature: `codoc_reflect` with `attach`.
- New intent: `codoc_propose` with `add_node`.
- Neither: report that the tree and the code agree.

## 3. Report

Say what you built and which items you reported. A reported item settles when
your turn ends (`codoc_status` lists it under `reported_list` until then), so
do not call `codoc_status` again to wait for it.
