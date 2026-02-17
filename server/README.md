# CodeNav API (server)

Backend for **CodeNav**: semantic tree extraction, indexing, analysis, and search. Uses **CocoIndex** (SentenceTransformer + PostgreSQL/pgvector) for fast, incremental code indexing and **adalflow** for LLM completion.

## Features

- **Analyze**: Single integrated pipeline — extract → index (CocoIndex + Postgres) → domain discovery (1 LLM call) → semantic parsing via RAG → hierarchy (1 call) → tree assembly. Index is stored in PostgreSQL; `index_path` is the scope ID (e.g. `path/.codenav/index` or root_dir).
- **Search**: Semantic search over the index (pass `index_path` as scope_id, e.g. root_dir or `path/.codenav/index`).
- **Status**: Index size for a given scope_id (`index_path`).

Output trees are markdown/JSON compatible with the TypeScript `parseTreeBlock()` grammar.

**Dependency on `src/`:** The server invokes TypeScript CLIs under the repo root: `src/cli/tree-edit-targets.ts` (tree edit → operations) and `src/cli/merge-trees.ts` (forward merge). Do not remove the repo’s `src/` directory; run the server from the CodeNav repo root so these scripts are available.

### Intervention (422)

When a pipeline step fails in a way that needs your fix (e.g. no `<solution>` block, invalid JSON, no functional areas), the API **stops** and returns **422** with:

```json
{"status": "intervention_required", "step": "<step_name>", "message": "<reason>"}
```

Steps: `extract`, `index`, `domain_discovery`, `semantic_parsing`, `hierarchical_construction`. Fix the cause (prompt, LLM, or input) and retry.

### Debug: prompt and generation logging

Set **`CODENAV_LOG_PROMPTS=1`** when starting the server to log (at INFO):

- Full prompt sent to the LLM for AddNode / EditFeature (first 2000 chars)
- Raw LLM response (first 1500 chars)
- Context for each op: `fpath`, `entity_name`, line range, `use_search_replace` (EditFeature)

Use this to verify context preparation and generation for any codebase.

## Environment setup

### 1. Python and uv

- Python **3.11+**.
- Install [uv](https://docs.astral.sh/uv/) (recommended for package management).

### 2. Install dependencies

From the `server` directory:

```bash
cd server
uv sync
```

This creates/uses a venv, installs the `codenav-api` package in editable mode, and pins deps in `uv.lock`.

### 3. Environment variables

Create a `.env` file in the **server** directory. Variables are loaded on startup via `load_dotenv(server_dir / ".env")`.

```bash
# Code index: PostgreSQL with pgvector (required for analyze/sync/search)
COCOINDEX_DATABASE_URL=postgresql://localhost/codoc

# Required for OpenAI LLM (default)
OPENAI_API_KEY=sk-...

# Optional: for Ollama (local) LLM
# OLLAMA_HOST=http://localhost:11434

# Optional: config directory (default: server/api/config)
# CODENAV_CONFIG_DIR=/path/to/config

# Server port (default 8001)
# PORT=8001
```

- **PostgreSQL**: Create a database and enable pgvector: `CREATE EXTENSION vector;`. With Homebrew: `brew install postgresql pgvector`, create DB, set `COCOINDEX_DATABASE_URL`.
- **OpenAI**: Set `OPENAI_API_KEY` for LLM steps. Use `provider=openai` in `/semantic_tree/analyze`.
- **Ollama (local)**: Pull an LLM (e.g. `ollama pull llama3.2:3b`). Use `provider=ollama` in the analyze request.

### 4. Config files (optional)

Under `server/api/config/`:

- **`generator.json`** — LLM providers and models (openai, ollama).

## Run the server

From the **server** directory:

```bash
uv run python main.py
```

Or with the venv activated:

```bash
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python main.py
```

API base: `http://localhost:8001` (or `PORT`).

- **Health**: `GET /health`
- **Routes**: `GET /`
- **Semantic tree**: `POST /semantic_tree/sync` (forward: code → tree; incremental when state exists), `POST /semantic_tree/analyze` (legacy, full run), `GET /semantic_tree/tree?path=` (last tree), `POST /semantic_tree/tree_edit` (edited tree → operations + code targets), `POST /semantic_tree/apply` (tree edit → code gen → file writes), `POST /semantic_tree/search`, `GET /semantic_tree/status`

**Apply and base_tree_md:** For `POST /semantic_tree/apply`, send **`base_tree_md`** (the tree content *before* this edit) so the diff is "your edit" only. If you omit it, the server uses `state.last_tree_md` (backend phrasing). When that differs from the user's doc, every node can be reported as changed and the server will return **400** when there are more than 2 EditFeature ops, asking you to send `base_tree_md`. The VS Code extension stores base on open/save of the `.codoc` and sends it automatically.

### Quick test (see the semantic tree)

1. Start the server in one terminal: `uv run python main.py`
2. In another terminal, from **server**: `uv run python scripts/call_analyze_and_show.py`

This calls sync (force_full) with the small test codebase (`test/small_python_repo`) and prints the tree. Set `CODENAV_ANALYZE_PATH` (e.g. `../test/requests`) for a larger codebase.

### API tests (server running)

From **server** (with API running on 8001):

- **Smoke tests**: `uv run python scripts/test_api.py`  
  All endpoints (health, tree_edit, analyze, sync, apply, …). Set **`CODENAV_FAST_TESTS=1`** to skip analyze/sync (faster; no index/LLM needed).
- **Integration (tree edit + apply, with logging)**: `uv run python scripts/test_api_integration.py [path]`  
  Sync → simulate tree edit → tree_edit → apply dry_run. Prints operations, planned_changes, and **unified_diff** so you can verify context and diff. Use **`CODENAV_LOG_PROMPTS=1`** in the server process to see prompts and generation in server logs.
- **Bidirectional sync**: `uv run python scripts/test_bidirectional_sync.py`  
  Sync → tree edit → apply_tree_edit (state) → sync without force (expect 409) → sync with force_full.

### Bidirectional flow test (test/draco)

From **server**: `uv run python ../test/draco/test_semantic_tree_flow.py`. Syncs `test/draco`, saves `semantic_tree.md`, edits the tree and checks `tree_edit` returns operations and targets (fpath, entity_name), then edits code and re-syncs to verify incremental update. Ensures sync state and target identification align with the formalized loop.

## Troubleshooting

- **Index/search errors** — Ensure PostgreSQL is running and `COCOINDEX_DATABASE_URL` is set. Run `CREATE EXTENSION vector;` in your database if needed.

## Layout

| Path           | Purpose                                   |
|----------------|-------------------------------------------|
| `server/`      | Project root; `main.py`, `.env`, `uv`    |
| `server/api/`  | Package: app, config, routes, semantic_tree (indexing via CocoIndex) |
| `server/api/semantic_tree/` | Sync/analyze (extract + index + RAG pipeline), tree, tree_edit, search, status |

## Summary

| Item            | Purpose                                      |
|-----------------|----------------------------------------------|
| `uv sync`       | Install deps and editable `codenav-api`     |
| `.env` in server | Loaded on startup (OPENAI_API_KEY, etc.)    |
| `adalflow`      | Embeddings and LLM client abstraction       |
| `CODENAV_*`     | CodeNav-specific env (config dir, etc.) |
| `COCOINDEX_DATABASE_URL` | Postgres URL for code index (pgvector) |

Incremental sync is implemented: state at `path/.codenav/sync_state.json` stores fingerprints and semantic cache; re-sync only re-embeds and re-parses added/modified entities.
