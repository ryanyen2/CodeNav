---
description: |
  Expert at reading and authoring the codoc feature tree (tree.codoc) via the
  codoc MCP tools. Use when:
  - you just changed code and should reflect that into the feature tree
  - the user asks to add, change, or remove a feature
  - proposing a plan for a code change before implementing (see /codoc:plan)
  - accepted tree edits are queued for you to implement (see /codoc:realize and
    .codoc/realize.md)
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
- `amend` a feature's description when you change what it does — including when
  you **add new capability** to it. If you add table/visualization helpers to a
  feature whose description only mentions "formatting", `amend` that description
  to reflect the richer behavior. Pair the `attach` and the `amend` in one
  `codoc_reflect` call. (Skip amends for trivial private helpers.)
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
   **placeholders** (`realized=false`). **Do NOT edit code yet.** Keep the
   `event_id` each call returns.
3. Ask the user to Accept the plan in the IDE, then call
   `codoc_await_verdicts(event_ids=[…])`. **This blocks your turn** until the user
   clicks Accept/Reject — it applies each verdict and returns
   `{accepted:[{event_id, feature_id, title}], rejected, pending}`. (It also marks
   accepted nodes "editing" so the IDE doc view shimmers them as in-progress.)
4. In the **same turn**, implement each accepted node one at a time: call
   `codoc_realize_progress(done, total, current)` as you start it, write the code,
   then `codoc_attach`/`codoc_reflect` to bind it (binding flips the placeholder to
   realized and resolves its skeleton in the doc view). Surface any unplanned work
   via `add_node` ops.
5. Call `codoc_plan_status` to confirm every plan node is realized.

## The realize loop (`/codoc:realize` — tree edit → code)

When the user **accepts a code-implying tree edit** in the IDE (an imperative
amend like "should validate…", an accepted plan node, or a retire of a feature
that owns code), codoc does **not** spawn a separate agent — it queues the work
for *you* by writing `.codoc/realize.md` and setting status `awaiting_impl`. On
your next prompt you'll see a reminder that changes are queued.

To implement them (or when the user runs `/codoc:realize`):
1. Read `.codoc/realize.md` — a numbered list of `NEW FEATURE` / `UPDATE FEATURE`
   / `RETIRE FEATURE` directives, each with its intent and an `Edit only:` scope.
2. Apply the **minimum** code change per directive. Edit **only** the files in its
   `Edit only:` line; **never** touch anything under `.codoc/`.
3. `codoc_reflect` to bind the code you wrote/changed to the features (flips
   accepted plan placeholders to realized).
4. Delete `.codoc/realize.md`, then `codoc_status` to confirm the pipeline
   returned to `in_sync` / `code_drift`.

## The unified sync (`/codoc:sync`)

One command that reads `codoc_status` and acts in whichever direction is stale —
use it when you're unsure which loop applies:
- `awaiting_impl` → run the realize loop above (codoc → code).
- `tree_dirty` → tree edits aren't queued yet; let `codoc watch` / terminal
  `codoc sync` drain them to `awaiting_impl`, then realize.
- `code_drift` → reconcile the tree to code: `codoc_attach` what belongs to existing
  features, `codoc_reflect` with `add_node` for genuinely new intent.
- `realizing` → a pass is already running; report progress and stop.
- `in_sync` → nothing to do.

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
