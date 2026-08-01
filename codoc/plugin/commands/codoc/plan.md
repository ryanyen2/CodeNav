---
description: Plan a code change as codoc feature nodes first, then implement once accepted.
argument-hint: <task description>
---

You are running the **codoc plan-first loop** for this task:

> $ARGUMENTS

codoc exposes the `codoc` MCP server. Follow these steps exactly. **Do not edit
any code until the user accepts the plan.**

## 1. Understand the current tree
- Call `codoc_context(files=[…])` with the files the task will touch (when you
  know them) for the relevant features + a whole-tree title outline; fall back
  to `codoc_tree` (optionally `root_id`/`depth`-scoped) when the task is
  tree-wide. Check `codoc_status` for the current state.
- Decide what the task implies: which NEW features are needed, and which EXISTING
  features it touches. Prefer extending existing features over inventing new ones.

## 2. Propose the plan as placeholder nodes (no code yet)
- For each new feature the task needs, call `codoc_plan_add(title, description,
  parent_id?, binds?, rationale)`. These enter the tree as **unrealized
  placeholders** (shown highlighted in the IDE) once accepted.
  - Choose `parent_id` from the titles outline / subtree you read so each node
    sits under the right parent.
  - Keep titles short (3–6 words) and give a 1–2 sentence description of intent.
  - You may pre-bind a node to the code you intend to write via `binds`
    (`"file.py::symbol"`); the binding will mark it realized once that code exists.
- For changes to existing features, use `codoc_propose_amend` / `codoc_propose_move`.

## 3. Hand the plan to the user and wait for their verdict
- Summarize the plan you proposed (the placeholder nodes + any amendments).
- Tell the user: **"Accept the plan in the codoc tree (Accept on each node, or
  Accept all) to approve it, or Reject to discard. I'll implement what you accept."**
- Then call `codoc_await_verdicts(event_ids=[…])` with the `event_id`s returned by
  your `codoc_plan_add` / `codoc_propose_*` calls. **This blocks your turn** until
  the user clicks Accept/Reject in the IDE — do not write any code while it waits.
  It returns `{accepted:[{event_id, feature_id, title}], rejected, pending}`.
  - If everything was rejected (or it timed out with nothing accepted), report that
    and stop — there is nothing to implement.

## 4. Implement the accepted nodes (same turn, one at a time)
For each entry in `accepted` (now live but **unrealized** placeholders), implement
it and reflect it **before** starting the next — this makes the IDE doc view fill
in each feature as you finish, one skeleton resolving at a time, rather than all at
once:
- Call `codoc_realize_progress(done=i-1, total=N, current="<title>")` as you start
  feature *i*, so the IDE shows "implementing i of N".
- Write the smallest code change that satisfies the node's intent.
- Immediately call `codoc_attach(feature_id, binds=["file.py::symbol", …])` (or
  `codoc_reflect`) to bind that code to the accepted node. **Binding flips the
  placeholder to realized** and resolves its skeleton in the doc view.
- If you implemented anything that wasn't in the plan, include `add_node` ops via
  `codoc_reflect` so it surfaces as a new proposal for the user.
- After the last one, call `codoc_realize_progress(done=N, total=N)`.

## 5. Verify the plan was satisfied
- Call `codoc_plan_status`. If any nodes are still unrealized, either implement
  the missing code and re-bind, or tell the user which planned features remain
  unrealized and why.
