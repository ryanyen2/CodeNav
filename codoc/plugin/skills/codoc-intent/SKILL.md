---
description: |
  Expert at reading and authoring the codoc feature tree (tree.codoc) via the
  codoc MCP tools. Use when:
  - you just changed code and should reflect that into the feature tree
  - the user asks to add, change, or remove a feature
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

- `codoc_context(files=[…])` — **the preferred read**: pass the repo-relative
  file(s) you are working on (and/or a `feature_id`) and get the relevant slice
  of the tree — the features bound to those files with descriptions and bound
  symbols, one hop of related features along call/import edges, plus a compact
  outline of every title for orientation and duplicate-avoidance. Bounded by
  your edit, not the repo.
- `codoc_tree` — the whole tree (titles, descriptions, `binding_count`, files;
  pass `include_bindings=true` for exact symbols, `root_id`/`depth` to scope).
  Use it only for genuinely tree-wide work (restructuring, auditing).
- `codoc_status` — counts + pipeline state.

## Reflect your code changes (the code-first loop)

After you finish a code change, reflect it in ONE call:

- `codoc_reflect(ops, rationale)` — submit every tree change your work implies.
  Each op is `{kind, feature_id?, parent_id?, title?, description?, binds?,
  rationale?}` where `kind ∈ attach | detach | refresh | amend | add_node |
  move_node | retire_node` and `binds` are `"file.py::symbol"` strings.

Guidance:
- **Strongly prefer `attach`** to an existing feature over `add_node`. Only add a
  node when no existing feature covers the new code (check `codoc_context` for
  the files you touched first).
- Group related new chunks under ONE `add_node`, not one node per function.
- `amend` a feature's description when you change what it does — including when
  you **add new capability** to it. If you add table/visualization helpers to a
  feature whose description only mentions "formatting", `amend` that description
  to reflect the richer behavior. Pair the `attach` and the `amend` in one
  `codoc_reflect` call. (Skip amends for trivial private helpers.)
- Safe ops (attach/refresh/detach, small amends) apply immediately; structural
  ops (add_node / move_node / retire_node, large amends) become proposals the
  user Accepts/Rejects in the IDE. Tell the user when you've left proposals.
- When the work came from a queued `.codoc/realize.md` directive, pass its
  `⟨d-…⟩` id as `caused_by` so the IDE groups your changes under the user's
  originating edit.

For a single change you can also call the focused tools directly:
`codoc_attach`, `codoc_propose_add`, `codoc_propose_amend`, `codoc_propose_move`,
`codoc_propose_retire`.

## Everything else → two commands

- **`/codoc:sync`** — reads `codoc_status` and reconciles whichever side is
  behind: implements the queued tree edits in `.codoc/realize.md`
  (`awaiting_impl`), drains un-queued tree edits (`tree_dirty`), or reconciles
  the tree to drifted code (`code_drift`). Run it when a prompt reminder says
  changes are queued, when the user asks to sync, or whenever you're unsure
  which loop applies.
- **`/codoc:plan <task>`** — plan a change doc-first: say what each affected
  feature WILL do (`codoc_propose_amend` — the default, since most tasks change
  existing intent) and add placeholder nodes only for intent nothing covers yet
  (`codoc_plan_add`, `realized=false`); block on the user's Accept/Reject via
  `codoc_await_verdicts`, then implement what was accepted in the same turn.

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
- ✅ Read `codoc_context` (scoped to your files) first; reflect via the MCP
  tools; prefer attach over add.
