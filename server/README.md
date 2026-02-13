# CodeNav API (server)

Backend for **CodeNav**: semantic tree extraction, indexing, analysis, and search. Uses **adalflow** for embeddings and LLM completion.

## Features

- **Extract**: Discover files and extract Python entities + imports from a local codebase.
- **Index**: Build a FAISS vector index over entities (embeddings via OpenAI or Ollama).
- **Analyze**: Full pipeline (domain discovery → semantic parsing → hierarchy → tree assembly) with configurable LLM (OpenAI or Ollama).
- **Search**: Semantic search over an existing index.

Output trees are markdown/JSON compatible with the TypeScript `parseTreeBlock()` grammar.

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
- **Semantic tree**: `POST /semantic_tree/extract`, `/semantic_tree/index`, `/semantic_tree/analyze`, `POST /semantic_tree/search`, `GET /semantic_tree/status`

## Layout

| Path           | Purpose                                   |
|----------------|-------------------------------------------|
| `server/`      | Project root; `main.py`, `.env`, `uv`    |
| `server/api/`  | Package: app, config, embedder, routes    |
| `server/api/semantic_tree/` | Extract → index → analyze → search pipeline |

## Summary

| Item            | Purpose                                      |
|-----------------|----------------------------------------------|
| `uv sync`       | Install deps and editable `codenav-api`     |
| `.env` in server | Loaded on startup (OPENAI_API_KEY, etc.)    |
| `adalflow`      | Embeddings and LLM client abstraction       |
| `CODENAV_*`     | CodeNav-specific env (embedder type, config) |
