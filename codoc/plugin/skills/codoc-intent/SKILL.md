---
description: |
  Expert at reading and authoring the codoc feature tree (tree.codoc). Use when:
  - the user asks to add, change, or remove a feature in the codebase
  - proposing a plan for a code change before implementing
  - understanding what features exist and which code they own
  - the user mentions "codoc", "feature tree", or "propose"
---

# codoc-intent — Intent-First Code Planning

You are working in a repo managed by **codoc**, which maintains a human-authored
feature tree at `.codoc/tree.codoc`. The tree is the *single source of truth*
for what the codebase does. Code changes should flow from tree intent, not the
other way around.

## Your two-step workflow for ANY code change request

### Step 1 — Propose the plan (no code edits yet)

When the user asks you to change the codebase, **express the change as codoc
proposals first** using the `codoc propose` CLI:

```bash
# Add a new feature
codoc propose add_node \
  --root . \
  --title "Date formatting" \
  --description "Converts datetimes to ISO-8601 strings throughout the app." \
  --rationale "standardise date handling" \
  --bind "utils/dates.py::format_date"

# Amend an existing feature's intent (use the feature id from tree.codoc)
codoc propose amend \
  --root . \
  --feature f-1a2b3c4d \
  --title "Updated title" \
  --description "New description of what this feature does."

# Retire (remove) a feature
codoc propose retire_node \
  --root . \
  --feature f-1a2b3c4d \
  --rationale "replaced by X"
```

**Do not edit any code files during the planning step.** The proposal renders in
`.codoc/tree.codoc` under `# ── pending changes`, tagged **agent plan**, so the
user can review it before anything changes.

After proposing, tell the user:
> "I've proposed [X] as a codoc plan. Please **Accept** it in the VS Code IDE
> (inline Accept action on the diff block) to trigger implementation. You can
> also Reject it to discard."

### Step 2 — Implementation (happens automatically)

When the user accepts a proposal, codoc's Loop B automatically:
1. Converts the accepted intent into a coding directive.
2. Invokes a fresh coding session (you'll be re-invoked) with the directive.
3. Reflects the resulting code changes back into the feature tree.

You don't need to do anything — **just propose, then wait for the accept signal.**

## The codoc tree format

`.codoc/tree.codoc` example:
```
# codoc feature tree
- Authentication  ⟨f-abc12345⟩
    Manages user login and session lifecycle.
    See [login handler](codoc:auth/views.py#login_view).

  - OAuth login  ⟨f-def67890⟩
      Third-party OAuth flow (Google, GitHub).

- Data layer  ⟨f-ghi11111⟩
    All database access and ORM models.
```

**Rules:**
- Each `- Title  ⟨f-id⟩` line is a feature. The `⟨f-id⟩` is a stable hidden id — **never invent or change ids**.
- Descriptions are free prose (multiple paragraphs OK; blank lines preserved).
- Children are indented 2 spaces per level.
- Code is cited inline with `[label](codoc:file.py#symbol)` — derived bindings ride in the sidecar.
- The `# ── pending changes` block is read-only from your perspective — manage it via `codoc propose`.

## Reading the current tree

```bash
cat .codoc/tree.codoc        # human-readable feature tree
cat .codoc/tree.bindings.json | python3 -m json.tool  # file→feature index
codoc status --root .        # feature count + pending proposals
```

## Common mistakes to avoid

- ❌ Do NOT edit code files before the user accepts a proposal.
- ❌ Do NOT edit `⟨f-id⟩` or `⟨e-id⟩` markers — they are managed by codoc.
- ❌ Do NOT write directly into the `# ── pending changes` block.
- ✅ Use `codoc propose` for ALL tree mutations during planning.
- ✅ Keep proposals small and focused — one intent per proposal.
