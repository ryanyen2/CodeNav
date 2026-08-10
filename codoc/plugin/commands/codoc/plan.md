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
- Decide what the task implies, feature by feature. For each one ask **"does a
  feature for this intent already exist?"** — because most tasks change what an
  existing feature does rather than introducing a new unit of intent.

## 2. Propose the plan (no code yet) — amend first, add only when you must

**Default to `codoc_propose_amend`.** If the task changes, extends, or narrows what
an existing feature already does, amend that feature's description to say what it
will do once the work lands. The user reviews that as a tracked-change diff on the
prose they wrote, which is far easier to judge than a new node appearing beside it.
Adding a node instead splits one intent across two places and leaves the original
description silently stale.

Add a node ONLY when the task introduces intent no existing feature covers — a
genuinely separate thing a reader would look for under its own name. "It's a new
function/file/class" is not the test; a new helper inside an existing feature's job
is an amend, not an add.

- To amend: `codoc_propose_amend(feature_id, description=…, rationale=…)` — write
  the description as it should read AFTER the change, not as a note about the change.
  Reuse `codoc_propose_move` when the task also relocates a feature.
- To add: `codoc_plan_add(title, description, parent_id?, binds?, rationale)`. These
  enter the tree as **unrealized placeholders** — the IDE draws them dimmed, in the
  position they will occupy — once accepted.
  - Choose `parent_id` from the titles outline / subtree you read so each node
    sits under the right parent.
  - Keep titles short (3–6 words) and give a 1–2 sentence description of intent.
  - You may pre-bind a node to the code you intend to write via `binds`
    (`"file.py::symbol"`); the binding will mark it realized once that code exists.

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
