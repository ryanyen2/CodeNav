# codoc

codoc maintains a **human-intent-level feature tree** synchronized to a codebase. Each node is a *feature*: a named unit of intent that binds to code chunks across many files. The tree is first-class authored intent — not LLM-derived. Code attribution is a secondary index kept in sync by a reflective pipeline.

## Two interaction flows

**Flow 1 — Top-down (plan → code):**
`codoc plan "add dark mode support"` → planning agent reads the feature tree and emits proposals as col-0 diff hunks in the `.codoc` file → user accepts → realize pipeline spawns `claude -p` to implement the code → post-realize reflect writes feedback proposals back to the same file.

**Flow 2 — Bottom-up (code → tree):**
File saved or committed → fingerprint comparison (< 200 ms for unchanged structure; 1–3 s with LLM for structural changes) → absorb/introduce proposals emitted → user accepts or rejects.

## Requirements

- Python 3.11+
- SQLite WAL (bundled)
- cocoindex + LanceDB for incremental embedded vector indexing (auto-installed)
- tree-sitter parsing (Python and TypeScript supported)
- OpenAI-compatible LLM API

## Quick start

```bash
pip install -e .

export CODOC_PROVIDER=openai
export CODOC_MODEL=gpt-4o-mini
export OPENAI_API_KEY=sk-...

cd my-repo
codoc init            # creates .codoc/, installs git post-commit hook
                      # also auto-runs bootstrap and prompts to review
codoc proposals       # list pending bootstrap proposals
codoc accept --all    # accept all at once (re-renders tree automatically)
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
codoc accept <slug>                     # accept by feature slug (auto-renders tree)
codoc accept --all                      # batch accept
codoc reject <slug>                     # reject by slug
codoc reject --all --yes                # batch reject

# Top-down planning (Flow 1)
codoc plan "<prompt>"                   # propose tree changes from description

# Code-driven updates (Flow 2)
codoc reflect --file <path>             # on-save reflect (no git required)
codoc reflect [--from-ref REF]          # post-commit reflect

# Continuous watching (combines both flows)
codoc watch                             # watch code files + .codoc edits
codoc watch --no-realize                # watch only; skip Claude realize pass
codoc watch --dry-realize               # build realize prompt without spawning claude

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
codoc retire <slug-path>               # retire feature
```

## The `.codoc` file format

Feature trees live in `.codoc/tree/_index.codoc` as a single human-editable document.

### Feature markers

| Marker | Meaning |
|---|---|
| `- Title` | Live feature (realized or stub) |
| `* Title` | Placeholder — no spec yet; triggers feedforward |
| `~ Title` | Retired feature |
| `? proposal-kind: slug` | Pending proposal diff hunk |

Col-0 diff markers on proposal lines: `+` add, `-` remove, `~` change.

### Structured fields

```
- Authentication flow
    purpose: handle user login, session creation, and token lifecycle
    rationale: centralises auth so no controller handles tokens directly @AuthManager
    scenario:
        given a valid username and password
        when  the user calls /login
        then  a signed JWT is returned [ref: src/auth.py::AuthManager.login]
    needs: token-lifecycle, rate-limiting
```

- **`purpose`** — one sentence: what the feature does (the WHAT).
- **`rationale`** — one sentence: why this design (the WHY). Use `@symbol` or `[ref: file::Symbol]` to anchor to code.
- **`scenario`** — three lines: `given … / when … / then …`. Use `[ref:]` for testable anchors.
- **`needs`** — comma-separated feature slugs (≤3) or arrow-list (>3).
- **`binds`** — hidden; moved to `_index.bindings.json` sidecar. Never appears in the human file.

### Inline code references

Two equivalent forms — use `@symbol` for brevity, `[ref:]` for explicit file paths:

```
@rotate_session                         # @symbol form
[ref: auth.py::rotate_session]          # [ref:] form (also accepted)
[ref: feature://token-lifecycle]        # cross-feature ref
```

Both forms are tracked in the citations table and rendered stale (`[⚠ @sym]`) when the target moves.

### Proposal hunks

Proposals appear inline. Col-0 prefix indicates the proposal type:

```
+ - Rate limiting                       # INTRODUCE proposal
+     purpose: cap requests per user per minute

? feedforward: rate-limiting            # FEEDFORWARD_FILL proposal (LLM filled missing spec)
+     purpose: cap requests per user per minute
+     rationale: token-bucket per user_id @check_rate_limit
+     plan: create rate_limit.py, modify @request_handler

? feedback: rate-limiting (unexpected files modified: cache.py)
~     rationale: token-bucket per user_id @check_rate_limit
+     rationale: token-bucket per user_id; cache layer @check_rate_limit @cache_get

~ ~ auth.py::rotate_session             # ABSORB proposal (code change detected)
```

### Placeholder → feedforward → realize loop

Write `* Title` (or `* Title` with partial prose) to stub a new feature:

```
* Rate limiting
* Invite quota
    Admins should not be able to flood users with invites.
```

On save, `codoc watch` detects placeholders and calls the feedforward LLM agent, which proposes a complete spec + coding plan as diff hunks. Accept to trigger the realize pipeline (Claude writes the code). After Claude exits, the feedback agent compares what was actually written against the plan and proposes rationale corrections.

## `.codoc/` layout

```
.codoc/
  codoc.db                  — SQLite WAL (features, bindings, transactions, citations)
  log.jsonl                 — append-only audit log
  lancedb/                  — cocoindex-managed LanceDB: AST chunks + embeddings + identity hashes
  cocoindex.db/             — cocoindex internal memoization state (resumes interrupted indexing)
  tree/
    _index.codoc            — single hierarchical document (auto-generated, human-editable)
    _index.bindings.json    — bindings sidecar: {feature_uuid: [{file, symbol, fingerprint}]}
    tree.meta.json          — sidecar: uuid↔slug mappings, diff-hunk→HLC line ranges
```

`_index.bindings.json` keeps bindings out of the human file while still making them available to the VSCode extension (CodeLens, hover, definition) without an API call.

## Feature status lifecycle

| Status | Meaning |
|---|---|
| `placeholder` | `* Title` authored; no purpose/rationale/scenario yet |
| `feedforward_pending` | Feedforward proposal emitted; awaiting user accept |
| `realized` | Full spec present (default for bootstrap-accepted features) |

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CODOC_PROVIDER` | `openai` | LLM provider (`openai` or `ollama`) |
| `CODOC_MODEL` | `gpt-4o-mini` | LLM model name |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `CODOC_BASE_URL` | — | Custom OpenAI-compatible base URL |
| `CODOC_EMBEDDER_PROVIDER` | `sentence-transformers` | Embedder provider (used for dedup / proposal similarity; chunk embeddings live in cocoindex) |
| `CODOC_EMBEDDER_MODEL` | `all-MiniLM-L6-v2` | Embedder model |
| `COCOINDEX_DB` | `.codoc/cocoindex.db` | Cocoindex internal memoization state path (auto-set) |
| `CODOC_LANCE_PATH` | `.codoc/lancedb` | LanceDB directory holding the `code_chunks` table |
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

- **Phases A–C (complete):** syntax cleanup (no binds in human file, bindings sidecar, `@symbol` refs, `needs:` CSV, `* placeholder` marker, `~ retire` in-file, no alignment padding), `Feature.status` field, citations fixed (stale-clearing, `@symbol` tracked)
- **Phase D (complete):** feedforward pipeline — placeholder → LLM fills spec + plan → FEEDFORWARD_FILL proposals render as in-file diff hunks → accept triggers realize
- **Phase E (complete):** feedback pipeline — after realize, compares modified files against feedforward plan → FEEDBACK_RECONCILE proposals for divergences
- **Phase F (complete):** bootstrap hierarchy — `cluster_into_parents` post-pass prevents wide-flat output (>6 top-level groups merged to ≤5 parent clusters)
- **Phase 1.5:** VSCode CodeLens for bindings sidecar + realize button on placeholders
- **Phase 2+:** SPLIT/MERGE/RESTRUCTURE/REWIND, branching, constraints

---

See [docs/getting-started-claude-code.md](docs/getting-started-claude-code.md) for the full workflow guide.
