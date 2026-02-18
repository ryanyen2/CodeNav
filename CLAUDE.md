# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodeNav is a parser and tree-diff engine for prescriptive semantic trees. It parses semantic trees from markdown notation, compares before/after states to infer operations, and dispatches to action stubs. The **TypeScript layer** (`src/`) handles parsing, diffing, and dispatch; the **Python backend** (`server/api/`) provides an integrated semantic tree pipeline: **analyze** (extract → FAISS index/RAG → domain discovery → semantic parsing via RAG → hierarchy → tree assembly). The API returns 422 `intervention_required` when a step needs user fix.

## Current Status & Limitations

- **v0.1.0**: Parsing and diffing in TypeScript; tree construction pipeline in Python. Code generation (AddNode, EditFeature, DeleteNode) runs in Python; tree→ops still uses TS.
- **`src/` is required**: The server **cannot** run without the TypeScript `src/` tree. It invokes `src/cli/tree-edit-targets.ts` (base vs edited tree → operations) and `src/cli/merge-trees.ts` (forward merge). Do not remove `src/` unless tree-diff and merge are reimplemented in Python.
- **Python backend** only extracts `.py` files — JS/TS are not supported in the analyze pipeline.
- **`src/parser/codebase-parser.ts`** is a standalone tool for CLI inspection; it is **not** connected to the Python analyze pipeline. Use it to parse codebase blocks from markdown or to snapshot a directory from the CLI.

## Commands

```bash
npm install              # Install dependencies
npm run build            # Compile TypeScript (tsc → dist/)

# CLI tools (npm run or npx tsx)
npm run parse:tree       # or: npx tsx src/cli/parse-tree.ts [path-to-test_cases.md]
npm run parse:test      # or: npx tsx src/cli/parse-test-case.ts test_cases.md [test-name]
npm run parse:codebase  # or: npx tsx src/cli/parse-codebase.ts <directory>
npx tsx src/cli/tree-edit-targets.ts <base.md> <edited.md>   # Tree edit → operations + code targets (JSON)
```

**Server (from `server/`):**

```bash
uv run python main.py    # Start API; port from PORT (default 8001)
```

No automated test suite; validate via CLI and API.

## Architecture

**Module system:** ES Modules (`"type": "module"` in package.json, NodeNext resolution). All internal TS imports use `.js` extensions.

**Zero runtime dependencies** (TS). `@babel/parser` is a devDependency, lazy-loaded in `codebase-parser.ts` for JS/TS AST extraction with regex fallback.

### End-to-End Workflow

Two phases:

- **Phase A — Codebase → Semantic Tree (Python):** Extract entities from the codebase (directory path or in-memory files) → build FAISS index → domain discovery (LLM) → semantic parsing with RAG (LLM) → hierarchical construction (LLM) → assemble tree with path/entity grounding and deps → serialize to markdown or JSON. Output is parseable by TS `parseTreeBlock()` (sigils, `[path]`, `(entity)`, `deps:`).
- **Phase B — Tree Edits → Code Changes (TypeScript):** Markdown tree notation → `parseTreeBlock()` → `SemanticTree` → `diffTrees(before, after)` → operations → `dispatch()` → `ActionResult` (stub plans only; no code generation yet).

### Core Pipeline (TypeScript)

```
Markdown tree notation → parseTreeBlock() → SemanticTree
                                                ↓
                         diffTrees(before, after) → TreeDiffResult[]
                                                        ↓
                              diffResultToOperation() → Operation
                                                           ↓
                                         dispatch() → ActionResult (stub plan)
```

### Key Modules (TypeScript — `src/`)

- **`src/types.ts`** — All type definitions. Semantic nodes: (f, m, c) = feature, metadata, contract. Sigils: `/` dir, `%` file, `$`/`^` leaf, `~` abstract.
- **`src/parser/tree-parser.ts`** — Line-based parser for markdown nested list → SemanticTree; parses `deps:` blocks with `(a) --rel--> (b)` notation.
- **`src/parser/codebase-parser.ts`** — Codebase snapshots from markdown blocks, source files, or directory; Babel for JS/TS, regex for Python. CLI only; not part of the Python analyze pipeline.
- **`src/parser/operation-parser.ts`** — Parses `--- OPERATION ---` blocks into Operation objects (AddNode, DeleteNode, MoveNode, etc.).
- **`src/diff/tree-diff.ts`** — Compares two SemanticTrees; matches nodes by stable ID; infers operations.
- **`src/actions/dispatcher.ts`** — Maps Operations to ActionResult stubs (plan arrays).
- **`src/sync/tree-edit-targets.ts`** — Tree edit (base + edited markdown) → operations and code targets (fpath, entity_name, line_range).
- **`src/index.ts`** — Public API barrel.

### Backend (Python — `server/api/`)

The backend builds semantic trees from a live codebase. **Embeddings**: CocoIndex (SentenceTransformer `all-MiniLM-L6-v2`) + PostgreSQL/pgvector; no external embedder. **LLM**: `api.config.get_model_config(provider, model)` for completion.

- **`server/api/semantic_tree/`** — Integrated pipeline and routes:
  - **`models.py`** — Domain models aligned with `src/types.ts` (CodeEntity, FileInfo, CodebaseSnapshot, tree nodes, etc.).
  - **`schemas.py`** — Request/response Pydantic models (AnalyzeRequest, SyncRequest, TreeEditRequest, ApplyRequest/ApplyResponse, InterventionResponse, etc.).
  - **`extraction/`** — Discovery (directory walk), Python AST extraction, import edges.
  - **`indexing/`** — Entity-level chunking; **CocoIndex** (cocoindex_store) for embeddings and RAG; scope_id = index_path (e.g. path/.codenav/index).
  - **`llm/`** — Prompt loader (`server/prompts/*.txt`), `<solution>` parsing, completion via api.config.
  - **`pipeline/`** — **Forward**: domain_discovery → semantic_parsing (RAG) → hierarchical_construction → tree_assembly; **incremental_forward** reuses cache/delta; **inverse**: code_dispatch (AddNode/EditFeature/DeleteNode) → code_applicator → post_check; **diff_format** for unified diff / search-replace.
  - **`state/`** — Sync state (fingerprints, semantic cache, last_tree_md, direction); **sync_guard** prevents forward after inverse when code unchanged (loop prevention); delta, persistence, fingerprint.
  - **`output/tree_serializer.py`** — Tree → markdown (parseable by `parseTreeBlock()`) or JSON.
  - **`routes.py`** — FastAPI router (prefix `/semantic_tree`): `POST /sync`, `POST /apply_tree_edit`, `POST /apply`, `POST /analyze`, `GET /tree?path=`, `POST /tree_edit`, `POST /search`, `GET /status`. Tree edit and merge invoke TS `src/cli/tree-edit-targets.ts` and `merge-trees.ts`. On step failure, returns **422** with `intervention_required`.
  - **`logging.py`** — Pipeline stage logger and one-line `[CODENAV]` logs.
  - **`observation_report.py`** — Build/log apply and merge observations (over_generation, surfaced_added).

**Forward pipeline (code → tree):** Extract → index (CocoIndex+pgvector) → domain_discovery (1 LLM, `domain_discovery.txt`) → semantic_parsing RAG (per-area search + `semantic_parsing.txt`) → hierarchical_construction (1 LLM, `hierarchical_construction.txt`) → tree_assembly → markdown/JSON. Incremental: entity delta; only added/modified re-embedded and re-parsed.

**Inverse pipeline (tree → code):** Tree edit (TS) → operations → code_dispatch (LLM for AddNode/EditFeature; inline prompts) → code_applicator → post_check → state update + re-fingerprint.

**Loop prevention:** After inverse, forward is allowed only if entity delta is non-empty (`can_run_forward`); otherwise 409. Inverse always allowed when state has tree.

**Codoc / tree schema:** `.codoc` = semantic tree markdown. Format: nested list, sigils (`/`, `%`, `$`, `^`, `~`), `[path]`, `(entity)`, optional `{contract}`, `deps:` with `(a) --rel--> (b)`. Matches `test_cases.md` and TS `parseTreeBlock()`.

**Prompts (server/prompts/):** `domain_discovery.txt` (functional areas), `semantic_parsing.txt` (per-entity features), `hierarchical_construction.txt` (path → entity groups). Code gen uses inline prompts in `code_dispatch.py`.

### Fixtures and Sample Data

- **`test/fixtures/`** — Markdown tree and codebase snapshot examples (including `cases/`); used by CLI and as format reference.
- **`test/draco/`** — Small Python codebase (Draco) for trying sync/tree_edit/apply_tree_edit manually.
- **`test/requests/`**, **`test/small_python_repo/`** — Sample Python codebases for extraction/indexing.
- **`test/mosaic/`** — Sample codebase (TypeScript); not used by Python analyze pipeline (Python-only).

### Design Documents

- **`prescriptive-semantic-tree-plan.md`** — Algorithm design: node schema, invariants, operation taxonomy.
- **`test_cases.md`** — Test spec: tree notation, operation syntax, codebase snapshot format.
- **`server/prompts/`** — LLM prompts: `domain_discovery.txt`, `semantic_parsing.txt`, `hierarchical_construction.txt`; loaded by `server/api/semantic_tree/llm/prompt_loader.py` (uses `CODENAV_PROMPTS_DIR` or walks up to find `prompts/`).


## PITFALLS