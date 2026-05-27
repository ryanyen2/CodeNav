---
description: Plan a code change as codoc feature nodes first, then implement once accepted.
argument-hint: <task description>
---

You are running the **codoc plan-first loop** for this task:

> $ARGUMENTS

codoc exposes the `codoc` MCP server. Follow these steps exactly. **Do not edit
any code until the user accepts the plan.**

## 1. Understand the current tree
- Call `codoc_tree` to see existing features (ids, titles, parents, bound code)
  and `codoc_status` for the current state.
- Decide what the task implies: which NEW features are needed, and which EXISTING
  features it touches. Prefer extending existing features over inventing new ones.

## 2. Propose the plan as placeholder nodes (no code yet)
- For each new feature the task needs, call `codoc_plan_add(title, description,
  parent_id?, binds?, rationale)`. These enter the tree as **unrealized
  placeholders** (shown highlighted in the IDE) once accepted.
  - Choose `parent_id` from `codoc_tree` so each node sits under the right parent.
  - Keep titles short (3–6 words) and give a 1–2 sentence description of intent.
  - You may pre-bind a node to the code you intend to write via `binds`
    (`"file.py::symbol"`); the binding will mark it realized once that code exists.
- For changes to existing features, use `codoc_propose_amend` / `codoc_propose_move`.

## 3. Hand the plan to the user
- Summarize the plan you proposed (the placeholder nodes + any amendments).
- Tell the user: **"Accept the plan in the codoc tree (Accept on each node, or
  Accept all) to approve it, or Reject to discard. I'll implement once accepted."**
- Stop here. Do not write code yet.

## 4. After acceptance — implement
Once the user accepts (the placeholder nodes are now live but unrealized):
- Implement the code for each planned feature.
- Then call `codoc_reflect(ops, rationale)` to bind the code you wrote to the plan
  nodes (`attach` with `binds`). Binding flips each placeholder to **realized**.
- If you implemented anything that wasn't in the plan, include `add_node` ops for
  it in the same `codoc_reflect` call so it surfaces as a new proposal.

## 5. Verify the plan was satisfied
- Call `codoc_plan_status`. If any nodes are still unrealized, either implement
  the missing code and re-bind, or tell the user which planned features remain
  unrealized and why.
