---
description: |
  Expert at reading and authoring the codoc feature tree (tree.codoc) via the
  codoc MCP tools. Use when:
  - you just changed code and should reflect that into the feature tree
  - the user asks to add, change, or remove a feature
  - proposing a plan for a code change before implementing (see /codoc:plan)
  - understanding what features exist and which code they own
  - the user mentions "codoc", "feature tree", or "propose"
---

# codoc-intent — keep the feature tree in sync with your work

You are in a repo managed by **codoc**, which maintains a human-authored feature
tree at `.codoc/tree.codoc`: a navigable hierarchy of *intent*, where each node is
a named feature bound to the code chunks (`file.py::symbol`) that implement it.

codoc exposes an **MCP server** (`codoc`). Use its tools to read and update the
tree — you know *why* you changed the code, so your reflection is far better than
codoc's automatic index-diff can infer. **Do not** edit `.codoc/tree.codoc` by
hand or shell out to `codoc propose`; use the tools.

## Read before you write

- `codoc_tree` — the live tree: feature ids, titles, descriptions, parents,
  `realized` flag, and bound symbols. Read this to find the right parent / an
  existing feature to attach to, and to avoid creating duplicates.
- `codoc_status` — counts + pipeline state.

## The code-first loop (you edited code → reflect it)

After you finish a code change, reflect it in ONE call:

- `codoc_reflect(ops, rationale)` — submit every tree change your work implies.
  Each op is `{kind, feature_id?, parent_id?, title?, description?, binds?,
  rationale?}` where `kind ∈ attach | detach | refresh | amend | add_node |
  move_node | retire_node` and `binds` are `"file.py::symbol"` strings.

Guidance:
- **Strongly prefer `attach`** to an existing feature over `add_node`. Only add a
  node when no existing feature covers the new code (check `codoc_tree` first).
- Group related new chunks under ONE `add_node`, not one node per function.
- `amend` a description only when the code's *meaning* shifted.
- Safe ops (attach/refresh/detach, small amends) apply immediately; structural
  ops (add_node / move_node / retire_node, large amends) become proposals the
  user Accepts/Rejects in the IDE. Tell the user when you've left proposals.

For a single change you can also call the focused tools directly:
`codoc_attach`, `codoc_propose_add`, `codoc_propose_amend`, `codoc_propose_move`,
`codoc_propose_retire`.

## The plan-first loop (`/codoc:plan <task>`)

When asked to plan before implementing (or via the `/codoc:plan` command):
1. Read `codoc_tree` / `codoc_status`.
2. Decompose the task into features and call `codoc_plan_add` for each — these are
   **placeholders** (`realized=false`). **Do NOT edit code yet.**
3. Ask the user to Accept the plan in the IDE.
4. After acceptance, implement the code, then `codoc_reflect` to bind the code to
   the plan nodes (binding flips them from placeholder to realized) and to surface
   any work you did that wasn't in the plan as new proposals.
5. Call `codoc_plan_status` to confirm every plan node is realized.

## The tree format (for reading)

```
- Authentication  ⟨f-abc12345⟩
    Manages user login and session lifecycle.
  - OAuth login  ⟨f-def67890⟩
      Third-party OAuth flow (Google, GitHub).
- Data layer  ⟨f-ghi11111⟩
    All database access and ORM models.
```

- `⟨f-id⟩` is a stable hidden id — pass it as `feature_id` to the tools; never
  invent or change ids.
- Children are indented 2 spaces per level.
- Pending proposals are shown in place (added nodes as green ghosts; retire/amend
  decorate the live node) with Accept/Reject in the IDE.

## Don'ts

- ❌ Don't hand-edit `.codoc/tree.codoc` or any `⟨…⟩` marker.
- ❌ Don't edit code during the planning step of `/codoc:plan`.
- ❌ Don't create a node whose title duplicates an existing one — attach instead.
- ✅ Read `codoc_tree` first; reflect via the MCP tools; prefer attach over add.
