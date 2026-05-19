# Getting Started with codoc

codoc maintains a **human-intent-level feature tree** synchronized to your code. You author the tree; the system keeps the code-attribution index up to date as commits land.

This guide walks through the full workflow using `test/draco/` as the example codebase.

---

## Setup

```bash
pip install -e .
```

**Required env vars** (add to `.env` in your project root, or export):

```bash
CODOC_PROVIDER=openai
CODOC_MODEL=gpt-5.4-mini
OPENAI_API_KEY=sk-...

# Use OpenAI embedder — the default sentence-transformers requires PyTorch >= 2.4
CODOC_EMBEDDER_PROVIDER=openai
CODOC_EMBEDDER_MODEL=text-embedding-3-small
```

---

## Step 1 — Initialize a repo

```bash
cd test/draco
codoc init
```

This creates `.codoc/` and installs a git post-commit hook.
`init` will also attempt to run `bootstrap` automatically if the tree is empty.

Check the current state of the repo at any time:

```bash
codoc status
```

Output:

```
codoc status
  Bootstrap  : done
  Features   : 7 active, 0 retired
  Proposals  : 3 pending
  Last commit: a3f1c2e  Add constraint weights
```

---

## Step 2 — Bootstrap the codebase

Bootstrap clusters your source files, proposes a feature tree, and stores proposals for your review.

```bash
codoc bootstrap
```

If bootstrap ran via `init`, check for pending proposals:

```bash
codoc proposals
```

Output (example):

```
 #   Ref                              Kind        Slug / Description
 ──  ───────────────────────────────  ──────────  ────────────────────────────────────────
 1   visualization                    INTRODUCE   visualization
 2   visualization/spec-parser        INTRODUCE   spec-parser (child of visualization)
 3   visualization/constraint-solver  INTRODUCE   constraint-solver (child of visualization)
 4   helper-utils                     INTRODUCE   helper-utils
 ...
```

Accept or reject proposals individually by slug/ref:

```bash
codoc accept visualization
codoc reject helper-utils
```

Or batch-accept everything at once:

```bash
codoc accept --all-pending
```

To batch-reject everything (useful to start over):

```bash
codoc reject --all-pending --yes
```

Once you have accepted the features you want, mark bootstrap done:

```bash
codoc bootstrap finish
```

---

## Step 3 — Render the tree to `.codoc` files

```bash
codoc projection render
```

This writes the current state to human-editable files:

```
.codoc/tree/
  _index.codoc          ← read-only top-level listing
  visualization.codoc   ← one file per root-level feature
  helper-utils.codoc
  tree.meta.json        ← auto-managed sidecar; never hand-edit
```

---

## Step 4 — Understand the `.codoc` file format

Open `.codoc/tree/visualization.codoc`:

```
# codoc subtree: visualization

# - active   ~ retired   ? proposal (delete=accept, !=reject)   [State] computed

- visualization  [Stub]
  Draco visualization recommendation — translates Vega-Lite specs to
  weighted soft-constraint ASP programs.

  - constraint-solver  [Stub]
    Run the ASP solver (clingo) to find best-scoring chart recommendations.

  - spec-parser  [Stub]
    Parse a Vega-Lite JSON spec into internal representation.
    bindings:
      [b1] js.py :: Draco
      [b2] helper.py :: topo_sort
```

### Line anatomy

```
- <slug>  [<State>]
  <intent prose>
  bindings:
    [b1] <file> :: <symbol>   ← read-only; edits here are ignored on sync
```

The feature's **slug-path** (e.g. `visualization/spec-parser`) is its stable, user-facing identifier. UUIDs are internal — they live in `tree.meta.json` and SQLite, not in the `.codoc` files.

| Sigil | Meaning |
|---|---|
| `-` | Active feature |
| `~` | Retired feature (monotonic — cannot un-retire in v1) |
| `?` | Pending proposal from the reflective pipeline |
| `!` | Explicit reject directive |

**State badges** are derived automatically (never stored):

| Badge | Meaning |
|---|---|
| `[Stub]` | Feature exists but has no bindings yet |
| `[Drafting]` | Has bindings, none fully resolved |
| `[Stable]` | All bindings resolved and fingerprints match |
| `[Strained]` | Some bindings have drifted from their last-seen fingerprint |
| `[Deprecated]` | Retired but bindings still exist |
| `[Severed]` | All bindings are unresolvable |

The `_index.codoc` file is **read-only** — editing it has no effect. Rename/retire operations happen in the subtree files or via CLI.

---

## Step 5 — Edit the tree

Make changes directly in `.codoc/tree/*.codoc`, then run `codoc projection sync`. Every saved edit is applied as a transaction — there is no preview gate. (Use `codoc tx rewind` to undo if needed.)

### Rename a feature

Change the slug text on the feature line. The UUID is not in the file — codoc resolves the identity from the slug-path via the `tree.meta.json` sidecar:

```diff
-   - spec-parser  [Stub]
+   - spec-ingestor  [Stub]
```

### Amend intent prose

Change the text block under the feature line:

```diff
    - spec-ingestor  [Stub]
-     Parse a Vega-Lite JSON spec into internal representation.
+     Parse and validate incoming Vega-Lite JSON specs, emitting typed AST nodes.
```

### Retire a feature

Change `-` to `~`:

```diff
- - helper-utils  [Stub]
+ ~ helper-utils  [Stub]
```

After sync the badge changes to `[Deprecated]`.

### Restructure (move) a feature

Change the indent level. To promote `spec-ingestor` from a child of `visualization` to a root-level feature, reduce its indentation by 2 spaces:

```diff
-   - spec-ingestor  [Stub]
-     Parse and validate incoming Vega-Lite JSON specs...
+ - spec-ingestor  [Stub]
+   Parse and validate incoming Vega-Lite JSON specs...
```

codoc detects the new parent from indentation and issues a `RESTRUCTURE` transaction. When a feature moves to root, it gets its own `<slug>.codoc` file on the next render.

### Apply all edits

```bash
codoc projection sync
```

Output:

```
status: ok
applied 2 transaction(s):
  - RENAME visualization/spec-parser → spec-ingestor
  - AMEND visualization/spec-ingestor: intent updated
```

Dry-run first:

```bash
codoc projection diff
```

---

## Step 6 — Review reflective proposals

After a git commit, the reflective pipeline runs automatically (via the post-commit hook) and emits `?` proposal lines into the affected `.codoc` files.

Example — after adding a function to `js.py`:

```
  - constraint-solver  [Stable]
    Run the ASP solver (clingo) to find best-scoring chart recommendations.
    bindings:
      [b1] js.py :: clingo_solve

? reattribute: constraint-solver  [proposal]  # ?0190ff...d4
    candidate-bindings: js.py:new_helper_fn
```

### Via file edit

**Accept** by deleting the `?` line (sync detects the deletion as acceptance):

```diff
- ? reattribute: constraint-solver  [proposal]  # ?0190ff...d4
-     candidate-bindings: js.py:new_helper_fn
```

**Reject** by prefixing with `!`:

```diff
- ? reattribute: constraint-solver  [proposal]  # ?0190ff...d4
+ ! reattribute: constraint-solver  [proposal]  # ?0190ff...d4
```

Then apply:

```bash
codoc projection sync
```

### Via CLI

List pending proposals:

```bash
codoc proposals
```

Accept or reject by the ref shown in the table (slug-path or short prefix):

```bash
codoc accept visualization/constraint-solver
codoc reject visualization/constraint-solver
```

Batch operations:

```bash
codoc accept --all-pending
codoc reject --all-pending --yes
```

---

## Step 7 — Browse and inspect features

### List all features

```bash
codoc list
```

Output (Rich table):

```
 Feature                              State     Bind  Intent (excerpt)
 ───────────────────────────────────  ────────  ────  ──────────────────────────────────────
 visualization                        Stub         0  Draco visualization recommendation
   visualization/constraint-solver    Stable       1  Run the ASP solver (clingo) to find...
   visualization/spec-parser          Stub         2  Parse a Vega-Lite JSON spec into...
 helper-utils                         Deprecated   0  Utility helpers (retired)
```

Filter by state:

```bash
codoc list --state strained
codoc list --state stub
codoc list --bindings js.py        # features that bind to js.py
codoc list --format json           # machine-readable output
```

### Show a feature

```bash
codoc show visualization/spec-parser
```

Output:

```
Slug    : visualization/spec-parser
State   : Stub
Retired : False
Parent  : visualization
Intent  :
  Parse a Vega-Lite JSON spec into internal representation.

Bindings (2):
  js.py :: Draco
  helper.py :: topo_sort
```

### Search by slug or intent

```bash
codoc search clingo
```

Output:

```
 visualization/constraint-solver   Stable   Run the ASP solver (clingo) to find best-scoring...
```

### Quick summary

```bash
codoc status
```

---

## Step 8 — Direct feature operations

These commands operate by slug-path without touching `.codoc` files.

### Edit intent non-interactively

```bash
codoc edit visualization/spec-parser --intent "Parse and validate incoming Vega-Lite JSON specs, emitting typed AST nodes."
```

### Rename a feature

```bash
codoc rename visualization/spec-parser spec-ingestor
```

The feature's slug-path becomes `visualization/spec-ingestor`.

### Retire a feature

```bash
codoc retire visualization/spec-parser
```

codoc asks for confirmation before retiring. The feature's badge changes to `[Deprecated]`.

---

## Step 9 — Phase 2 structural operations (CLI only)

These go beyond the basic rename/amend/retire operations. They use the `tx` sub-namespace, which will be replaced with top-level verbs (`codoc split`, `codoc merge`, `codoc move`, `codoc undo`) in a future release.

```bash
# Split one feature into two children
codoc tx split visualization/spec-parser \
  --a-slug parser-core --a-intent "Core ASP generation" \
  --b-slug parser-validation --b-intent "Input schema validation"

# Merge multiple features into one
codoc tx merge visualization/constraint-solver,visualization/spec-parser \
  --slug visualization-core --intent "Unified chart recommendation pipeline"

# Move a feature to a new parent (by slug-path)
codoc tx restructure visualization/spec-parser --new-parent helper-utils

# Rewind a feature to a prior state
codoc tx rewind visualization/spec-parser --to-hlc 0000000177...
```

---

## FastAPI server

```bash
codoc server --port 8001
```

Key endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/features` | List all features |
| `GET` | `/feature/{uuid}` | Feature detail + bindings + state |
| `POST` | `/sync` | Apply `.codoc/tree/` edits → DB → re-render |
| `GET` | `/tree.codoc` | Rendered file map + base HLC |
| `GET` | `/tx/pending` | List pending proposals |
| `POST` | `/tx/{hlc}/accept` | Accept a proposal |
| `POST` | `/tx/{hlc}/reject` | Reject a proposal |
| `POST` | `/tx/accept-all` | Accept all pending proposals |
| `POST` | `/tx/reject-all` | Reject all pending proposals |
| `POST` | `/bootstrap` | Trigger bootstrap |
| `POST` | `/reflect` | Trigger reflective pipeline |

---

## Validation gate

After labeling enough bootstrap proposals, run the gate to check quality thresholds:

```bash
codoc gate-run --report
```

Pass thresholds:
- `accept-verbatim` ≥ 60%
- `verbatim + light-edit` ≥ 80%
- Median light-edit Levenshtein ≤ 80 chars

Label proposals for gate measurement:

```bash
codoc accept <ref> --label accept-verbatim
codoc accept <ref> --label accept-light-edit
codoc accept <ref> --label accept-heavy-edit
codoc reject <ref>
```

---

## Known issues / current limitations

### 1. Bootstrap fails with default embedder

**Symptom:** `Error: bootstrap failed: name 'nn' is not defined`

**Cause:** The default `sentence-transformers` embedder requires PyTorch ≥ 2.4.

**Fix:** Switch to the OpenAI embedder:

```bash
export CODOC_EMBEDDER_PROVIDER=openai
export CODOC_EMBEDDER_MODEL=text-embedding-3-small
```

If you mix embedder providers (e.g., switch from sentence-transformers to OpenAI), dimension mismatch will cause FAISS errors. Delete `.codoc/` and re-bootstrap.

### 2. Stale buffer check not enforced

**Symptom:** Editing a `.codoc` file after the reflective pipeline has run still syncs without error.

**Context:** The plan specifies that `sync` should refuse if the file's header is older than `tree.meta.json`'s `base_hlc` (to protect against concurrent pipeline activity). This check is not yet enforced. Until it lands, always run `codoc projection render` to refresh your buffer before editing if you suspect the reflective pipeline has run since your last render.

### 3. `codoc init` in non-git directories

Shows `Warning: .git/hooks/ not found` and continues. The post-commit hook for automatic reflection won't be installed. Run `codoc reflect` manually after commits instead.

### 4. Introducing new features via file edit is not allowed (v1)

Adding a `- new-feature` line that doesn't match any existing feature causes a `new_feature_not_allowed` error and the sync is rejected. New features are introduced only through `bootstrap` proposals (accepted via `codoc accept`) or future `INTRODUCE` intentional ops. To rename an existing feature, change its slug text directly — the sidecar resolves identity from the prior slug-path.

---

## Quick reference

```bash
# Setup
codoc init                                         # init repo + install hook
codoc status                                       # quick summary

# Bootstrap
codoc bootstrap                                    # propose feature tree
codoc bootstrap finish                             # mark bootstrap done

# Proposals
codoc proposals                                    # list pending proposals
codoc accept <ref>                                 # accept by slug-path or prefix
codoc reject <ref>                                 # reject by slug-path or prefix
codoc accept --all-pending                         # batch accept
codoc reject --all-pending --yes                   # batch reject (no confirm)

# File-based workflow
codoc projection render                            # DB → .codoc/tree/
codoc projection sync                              # .codoc/tree/ edits → DB → re-render
codoc projection diff                              # dry-run: show ops without applying

# Browse
codoc list                                         # tree with states + binding counts
codoc list --state strained                        # filter by state
codoc list --bindings <file>                       # features bound to a source file
codoc show <slug-path>                             # feature detail + bindings
codoc search <term>                                # search slug/intent

# Direct operations
codoc edit <slug-path> --intent "..."              # amend intent
codoc rename <slug-path> <new-slug>                # rename slug
codoc retire <slug-path>                           # retire feature

# Reflective pipeline
codoc reflect [--from-ref REF]                     # run reflective pipeline

# Phase 2 structural ops (use `codoc tx` namespace for now)
codoc tx split <slug-path> --a-slug ... --b-slug ...
codoc tx merge <slug-path1>,<slug-path2> --slug ...
codoc tx restructure <slug-path> --new-parent <slug-path>
codoc tx rewind <slug-path> --to-hlc <id>

# Validation gate
codoc accept <ref> --label accept-verbatim         # label for gate measurement
codoc gate-run [--report]                          # check quality thresholds

# Server
codoc server [--port 8001]                         # FastAPI server
```

> **Deprecation note:** The old `codoc tx list`, `codoc tx accept <HLC>`, `codoc tx reject <HLC>`, `codoc feature show <UUID>`, `codoc feature amend <UUID>`, `codoc feature rename <UUID>`, and `codoc feature retire <UUID>` commands still work but print a deprecation notice. Prefer the new slug-path commands listed above.
