---
description: Build the hand-offs queued in .codoc/realize.md, or reconcile the tree with what you changed.
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
| `reported` | you reported every queued hand-off this turn | nothing to do; they settle when your turn ends |
| `synced` | nothing is waiting | reconcile only what you changed this session (below) |

### `queued`: build the hand-offs in `.codoc/realize.md`

The file explains how to read a hand-off. For each one, in order:

1. `codoc_realize_progress(done=i-1, total=N, current="<title>")`.
2. Make the smallest code change that satisfies it.
3. Run the tests that cover the change. If a test command is refused for lack of permission, do not look for another way to run it; note which tests did not run.
4. `codoc_reflect(ops=[…], caused_by="<its d:… id>")`: `attach` symbols that existed before your turn and that the feature does not own yet; `amend` a block only where it says something the code does not do. Do not attach code you wrote this turn: it is not in the code index until your turn ends, and the pass that runs then binds it. The call reports the hand-off and you are done with it. When there is nothing to attach or amend, skip the call.
5. Read `.codoc/realize.md` again for hand-offs added while you work. One you reported stays listed until your turn ends; skip it.

After the last one call `codoc_realize_progress(done=N, total=N)`. If a hand-off
cannot be satisfied, leave it, say why, and stop.

### `synced`: reconcile your own changes

Call `codoc_context(files=[…])` for the files you touched.

- Existing code you changed that a feature should own and does not: `codoc_reflect` with `attach`. New code is bound when your turn ends.
- New intent: `codoc_propose` with `add_node`.
- Neither: report that the tree and the code agree.

## 3. Report

Say what you built, which hand-offs you reported, and which tests you ran. When a
test command was refused, say so plainly and name it: the person can allow it in
`.claude/settings.local.json`, for example `"Bash(pytest:*)"` under
`permissions.allow`. A reported hand-off settles when your turn ends
(`codoc_status` lists it under `reported_list` until then), so do not call
`codoc_status` again to wait for it.
