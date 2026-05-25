# Getting Started — codoc × Claude Code

codoc keeps a **feature tree** — a small, navigable map of what your code is
*for* — in sync with the code itself, in both directions:

- **code → codoc:** when you (or an AI agent) change the code, codoc detects what
  changed and makes the minimal tree update — attach new code to a node, tweak a
  node's description, add a child, reparent, or retire. Safe updates apply
  automatically; structural ones appear as reviewable proposals.
- **codoc → code:** when you edit the tree, codoc has a coding agent (`claude -p`)
  make the matching code change, then re-reads the result to refine the tree if
  your intent was under-specified.

There is **one file** you ever look at — `.codoc/tree.codoc` — and **four
commands**.

---

## 1. Install

```bash
pip install -e .            # Python 3.11+
export OPENAI_API_KEY=sk-…  # codoc's own LLM calls; override model with CODOC_MODEL
codoc --help
```

(Claude Code is invoked by codoc only for the code→codoc direction; it uses your
own Claude Code credentials and codoc never sees them.)

## 2. Initialise

```bash
cd ~/code/my-project
codoc init
```

`codoc init` indexes the repo (incrementally — it never re-indexes from scratch
on later runs), proposes an initial feature tree in one pass, and writes
`.codoc/tree.codoc`. Open that file: each line is a feature.

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

  - Token rotation  ⟨f-7b1d04⟩
      Refreshes session tokens before expiry.

- Notification dispatch  ⟨f-1f88aa⟩
    Queues and flushes email + in-app notifications.
```

`⟨f-…⟩` is the node's stable id — codoc writes it; you never type it. Indentation
is the tree structure.

## 3. Watch

```bash
codoc watch
```

One daemon runs both loops. Leave it running while you work.

### Editing code (code → codoc)

Change a function, add a file, delete code. codoc reacts:

```
▸ code→codoc  (2 files) auto: 1 refresh, 1 attach · proposed: 1 add-node
```

- **auto** changes (refresh a binding, attach a new symbol to an existing
  feature, a small description tweak) are applied silently and logged.
- **structural** changes (a brand-new node, a reparent, a retire) appear in
  `tree.codoc` as a proposal you review.

### Editing the tree (codoc → code)

Edit a description, rename a node, or add one by hand:

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, token lifecycle, AND rate limiting.

- Password reset                       ← a brand-new node, no id needed
    Lets a user reset a forgotten password via emailed token.
```

Save. codoc builds a directive from each changed node and runs `claude -p` to
make the code change, then reflects on what was written and may propose a
refinement back into the tree.

## 4. Reviewing proposals

Structural proposals render as `?` blocks carrying an event id:

```
? add "Rate limiting"  ⟨e-9f01c2⟩
?     Caps API requests per user per minute.
?     parent: Authentication flow · no existing node covers this
```

To act, change the leading character of the block:

- `?` → `+`  **accept** (apply the change; for an add-code item, the coding agent runs)
- `?` → `-`  **reject**, or just **delete the block**
- leave it `?`  still pending — nothing happens

## 5. The four commands

| Command | What it does |
|---|---|
| `codoc init` | Index the repo, propose a tree, write `tree.codoc`. |
| `codoc watch` | The daemon — runs both loops as you edit. |
| `codoc status` | Feature count, pending proposals, recent activity. |
| `codoc sync` | One-shot (no daemon): apply tree edits, then reflect code. |

`codoc watch --dry` reflects and builds directives but doesn't spawn the coding
agent; `codoc watch --no-realize` syncs the tree but skips the agent entirely.

## 6. Where things live

```
.codoc/
  tree.codoc      # the one human surface (committed with your code)
  codoc.db        # features + bindings + an append-only event log (SQLite)
  lancedb/        # the incremental code-chunk index (cocoindex)
```

Commit `.codoc/tree.codoc` (and, if you like, `codoc.db`) alongside your code so
the intent map is versioned with it.

## 7. How it stays robust

- **Incremental:** the chunk index is memoised per file; an edit re-indexes only
  what changed.
- **No duplication:** a single LLM pass sees the whole change plus every existing
  node title at once, so it folds related code into one node instead of emitting
  duplicates; a `UNIQUE(file, symbol)` rule means a chunk binds to at most one
  feature.
- **No surprises:** safe updates apply automatically and are logged; anything
  structural waits for your `+`/`-` in `tree.codoc`.
