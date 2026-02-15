# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodeNav is a parser and tree-diff engine for prescriptive semantic trees. It parses semantic trees from markdown notation, compares before/after states to infer operations, and dispatches to action stubs. The **TypeScript layer** (`src/`) handles parsing, diffing, and dispatch; the **Python backend** (`server/api/`) provides an integrated semantic tree pipeline: **analyze** (extract → FAISS index/RAG → domain discovery → semantic parsing via RAG → hierarchy → tree assembly). The API returns 422 `intervention_required` when a step needs user fix.

## Current Status & Limitations

- **v0.1.0**: Parsing and diffing in TypeScript; tree construction pipeline in Python. No code generation yet.
- **Python backend** only extracts `.py` files — JS/TS are not supported in the analyze pipeline.
- **Dispatcher** is stubs only — no actual code generation; operations map to plan arrays only.
- **`src/parser/codebase-parser.ts`** is a standalone tool for CLI inspection; it is **not** connected to the Python analyze pipeline. Use it to parse codebase blocks from markdown or to snapshot a directory from the CLI.

## Commands

```bash
npm install              # Install dependencies
npm run build            # Compile TypeScript (tsc → dist/)

# CLI tools
npx tsx src/cli/parse-tree.ts [path-to-test_cases.md]
npx tsx src/cli/parse-test-case.ts test_cases.md [test-name]
npx tsx src/cli/parse-codebase.ts <directory>
```

**Server (from `server/`):**

```bash
uv run python main.py    # Start API (e.g. port 8001)
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

The backend builds semantic trees from a live codebase. It uses `api.config`, `api.openai_client`, and `api.tools.embedder` (adalflow).

- **`server/api/semantic_tree/`** — Integrated pipeline and routes:
  - **`models.py`** — Pydantic models aligned with `src/types.ts`.
  - **`extraction/`** — Discovery (directory walk), Python AST extraction, import edges.
  - **`indexing/`** — Entity-level chunking and FAISS vector store (embedder from `api.tools.embedder`); used as RAG inside analyze.
  - **`llm/`** — Prompt loader (`prompts/`), `<solution>` parsing, completion via `api.config.get_model_config()`.
  - **`pipeline/`** — Domain discovery, semantic parsing (RAG), hierarchical construction, tree assembly; **incremental_forward** for code→tree with delta and cached semantic/index.
  - **`output/tree_serializer.py`** — Tree → markdown (parseable by `parseTreeBlock()`) or JSON.
  - **`routes.py`** — FastAPI router: `POST /semantic_tree/sync` (forward code → tree; incremental when state exists; empty delta reuses `last_tree_md`), `POST /semantic_tree/apply_tree_edit` (inverse: persist edited tree, bump `tree_version`), `POST /semantic_tree/analyze` (legacy full pipeline), `GET /semantic_tree/tree?path=`, `POST /semantic_tree/tree_edit` (base + edited tree → operations and code targets), `POST /semantic_tree/search`, `GET /semantic_tree/status`. On step failure, returns **422** with `intervention_required`.
  - **`logging.py`** — Pipeline stage logger and one-line `[CODENAV]` logs (SYNC mode/delta/index/semantic, TREE_EDIT ops/targets, APPLY_TREE_EDIT).

Output markdown from the backend is designed to be consumed by TS `parseTreeBlock()` and matches the format in `test_cases.md` (sigils, path grounding, entity names, `deps:` block).

### Fixtures and Sample Data

- **`test/fixtures/`** — Markdown tree and codebase snapshot examples; used by CLI and as format reference.
- **`test/draco/`** — Small Python codebase (Draco) for trying sync/tree_edit/apply_tree_edit manually.
- **`test/requests/`**, **`test/small_python_repo/`** — Sample Python codebases for extraction/indexing.

### Design Documents

- **`prescriptive-semantic-tree-plan.md`** — Algorithm design: node schema, invariants, operation taxonomy.
- **`test_cases.md`** — Test spec: tree notation, operation syntax, codebase snapshot format.
- **`prompts/`** — LLM prompts for domain discovery, semantic parsing, hierarchical construction (used by `server/api/semantic_tree`).
