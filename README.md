# codoc

codoc maintains a **human-intent-level feature tree** synchronized to a codebase. Each node is a *feature*: a named unit of intent that binds to code chunks across many files. The tree is first-class authored intent — not LLM-derived. Code attribution is a secondary index kept in sync by a reflective pipeline.

## Two interaction flows

**Flow 1 — Top-down (plan → code):**
`codoc plan "add dark mode support"` → planning agent reads the feature tree and emits proposals as col-0 diff hunks in `.codoc` files → user accepts → coding agents implement → `codoc reflect --file` marks changes as plan-aligned.

**Flow 2 — Bottom-up (code → tree):**
File saved (via git hook or `codoc reflect --file <path>`) → fingerprint comparison (< 200 ms for unchanged structure; 1–3 s with LLM for structural changes) → proposals emitted → user accepts or rejects.

## Requirements

- Python 3.11+
- SQLite WAL (bundled)
- FAISS for embeddings
- tree-sitter parsing (Python and TypeScript supported)
- OpenAI-compatible LLM API

## Quick start

```bash
pip install -e .

export CODOC_PROVIDER=openai
export CODOC_MODEL=gpt-4o-mini
export OPENAI_API_KEY=sk-...
export CODOC_EMBEDDER_PROVIDER=openai
export CODOC_EMBEDDER_MODEL=text-embedding-3-small

cd my-repo
codoc init            # creates .codoc/, installs git post-commit hook, runs bootstrap
codoc proposals       # review proposed feature tree
codoc accept --all    # accept all bootstrap proposals
codoc bootstrap finish
```

## Core commands

```bash
# Setup
codoc init                              # init .codoc/ + post-commit hook
codoc status                            # features, pending proposals, last change

# Bootstrap (first-time tree creation)
codoc bootstrap [--with-intent]         # cluster + propose feature tree
codoc bootstrap finish                  # switch to reflective mode

# Proposals
codoc proposals                         # list pending proposals
codoc accept <slug>                     # accept by feature slug
codoc accept --all                      # batch accept
codoc reject <slug>                     # reject by slug
codoc reject --all --yes                # batch reject

# Top-down planning (Flow 1)
codoc plan "<prompt>"                   # propose tree changes from description

# Code-driven updates (Flow 2)
codoc reflect --file <path>             # on-save reflect (no git required)
codoc reflect [--from-ref REF]          # post-commit reflect

# File-based editing
codoc projection render                 # DB -> .codoc/tree/ files
codoc projection sync                   # .codoc/tree/ edits -> DB -> re-render
codoc projection diff                   # dry-run

# Browse
codoc list                              # feature tree with states
codoc show <slug-path>                  # feature detail + bindings
codoc search <term>                     # search slug/intent

# Direct operations
codoc edit <slug-path> --intent "..."   # amend intent
codoc rename <slug-path> <new-slug>     # rename slug
codoc retire <slug-path>                # retire feature
```

## The `.codoc` file format

Feature trees live in `.codoc/tree/` as human-editable text files. Col-0 markers signal proposal hunks: `+` introduce, `~` amend, `-` retire. State badges: `(stub)`, `(strained)`, `(severed)`.

Index file (`.codoc/tree/_index.codoc`):
```
# codoc index — auto-generated. Edit subtree files, not this index.
# col-0 markers: + introduce  ~ amend  - retire  |  (stub) (strained) (severed) = needs attention

- Core API
- Theme System
```

Chapter file (one per root feature):
```
- Core API
    Manages chart schema introspection and Python class generation.
  - Schema Generation  (strained)
      Walks $ref refs and builds SchemaInfo objects.
  - Code Generator
      Emits typed Python dataclasses from resolved schema nodes.

+ - Theme System
+     Manages light/dark theme switching across the UI layer.
~ - Color Palette
~     Old: Manages brand colors.
~     New: Manages brand colors with dark-mode variants.
```

Title lines: indent + `- Title[ (strained|severed|stub)]`. No inline bindings, no headers.

## `.codoc/` layout

```
.codoc/
  codoc.db           — SQLite WAL (features, bindings, transactions)
  log.jsonl          — append-only audit log
  faiss/             — embedding index for bootstrap
  unattributed.json  — chunks not assigned to any feature (after bootstrap finish)
  tree/
    _index.codoc     — chapter listing (auto-generated)
    <slug>.codoc     — one file per root feature
    tree.meta.json   — sidecar: uuid<->title/slug-path mappings, diff-hunk->HLC
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CODOC_PROVIDER` | `openai` | LLM provider (`openai` or `ollama`) |
| `CODOC_MODEL` | `gpt-4o-mini` | LLM model name |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `CODOC_BASE_URL` | — | Custom OpenAI-compatible base URL |
| `CODOC_EMBEDDER_PROVIDER` | `sentence-transformers` | Embedder provider |
| `CODOC_EMBEDDER_MODEL` | `all-MiniLM-L6-v2` | Embedder model |
| `CODOC_ROOT_DIR` | cwd | Root directory for API server |
| `CODOC_LOG_PROMPTS` | `0` | Set to `1` to log LLM prompts to stderr |

## FastAPI server

```bash
codoc server --port 8001
```

Key endpoints: `POST /bootstrap`, `POST /bootstrap/finish`, `POST /reflect`, `POST /reflect/file`, `POST /plan`, `GET /tx/pending`, `POST /tx/accept-all`, `POST /tx/reject-all`, `GET /tree.codoc`, `POST /sync`.

## Tests

```bash
python3.11 -m pytest tests/
```

Fixtures: `test/draco/` (small Python), `test/requests/` (real-world Python), `test/mosaic/` (TypeScript), `test/altair/`, `test/gofish-python/`, `test/nanochat/`.

## Phase plan

- **Phase 1 (current):** bootstrap + reflect + intentional-minimal (AMEND/RENAME/RETIRE) + planning + CLI + FastAPI
- **Phase 1.5:** VSCode extension (`codoc-vscode` repo, pending gate passing)
- **Phase 2+:** SPLIT/MERGE/RESTRUCTURE/REWIND, branching, constraints

---

See [GETTING_STARTED.md](GETTING_STARTED.md) for the full workflow guide.
