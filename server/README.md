# CodeNav API (server)

Backend for **CodeNav**: semantic tree extraction, indexing, analysis, and search. Uses **adalflow** for embeddings and LLM completion.

## Features

- **Analyze**: Single integrated pipeline — extract → build FAISS index (RAG) → domain discovery (1 LLM call) → semantic parsing via RAG (one call per functional area + one for remaining) → hierarchy (1 call) → tree assembly. Uses both embedder and LLM; index is built automatically and saved to `path/.codenav/index`.
- **Search**: Semantic search over the index produced by analyze (pass `index_path`, e.g. `path/.codenav/index`).
- **Status**: Index size for a given `index_path`.

Output trees are markdown/JSON compatible with the TypeScript `parseTreeBlock()` grammar.

### Intervention (422)

When a pipeline step fails in a way that needs your fix (e.g. no `<solution>` block, invalid JSON, no functional areas), the API **stops** and returns **422** with:

```json
{"status": "intervention_required", "step": "<step_name>", "message": "<reason>"}
```

Steps: `extract`, `index`, `domain_discovery`, `semantic_parsing`, `hierarchical_construction`. Fix the cause (prompt, LLM, or input) and retry.

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
# Required for OpenAI embedder and LLM (default)
OPENAI_API_KEY=sk-...

# Embedder: "openai" (default) or "ollama"
# CODENAV_EMBEDDER_TYPE=openai

# Optional: for Ollama (local) embedder and LLM
# OLLAMA_HOST=http://localhost:11434

# Optional: config directory (default: server/api/config)
# CODENAV_CONFIG_DIR=/path/to/config

# Server port (default 8001)
# PORT=8001
```

- **OpenAI**: Set `OPENAI_API_KEY` in `.env`. Use `provider=openai` in `/semantic_tree/analyze`.
- **Ollama (local)**: If Ollama/LLaMA is installed locally, set `CODENAV_EMBEDDER_TYPE=ollama` and optionally `OLLAMA_HOST`. Pull an embed model (e.g. `nomic-embed-text`) and an LLM (e.g. `llama3.2:3b`). Use `provider=ollama` in analyze.

### 4. Config files (optional)

Under `server/api/config/`:

- **`generator.json`** — LLM providers and models (openai, ollama).
- **`embedder.json`** — Embedder models (OpenAI, Ollama). Override with `CODENAV_CONFIG_DIR` if needed.

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
- **Semantic tree**: `POST /semantic_tree/analyze`, `POST /semantic_tree/search`, `GET /semantic_tree/status`

## Layout

| Path           | Purpose                                   |
|----------------|-------------------------------------------|
| `server/`      | Project root; `main.py`, `.env`, `uv`    |
| `server/api/`  | Package: app, config, embedder, routes    |
| `server/api/semantic_tree/` | Analyze (extract + index + RAG pipeline), search, status |

## Summary

| Item            | Purpose                                      |
|-----------------|----------------------------------------------|
| `uv sync`       | Install deps and editable `codenav-api`     |
| `.env` in server | Loaded on startup (OPENAI_API_KEY, etc.)    |
| `adalflow`      | Embeddings and LLM client abstraction       |
| `CODENAV_*`     | CodeNav-specific env (embedder type, config) |
