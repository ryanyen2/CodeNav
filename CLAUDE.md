# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodeNav is a parser and tree-diff engine for prescriptive semantic trees. It parses semantic trees from markdown notation, compares before/after states to infer operations, and dispatches to action stubs. The **TypeScript layer** (`src/`) handles parsing, diffing, and dispatch; the **Python backend** (`server/api/`) provides an integrated semantic tree pipeline: **analyze** (extract → FAISS index/RAG → domain discovery → semantic parsing via RAG → hierarchy → tree assembly). The API returns 422 `intervention_required` when a step needs user fix. Currently v0.1.0 — parsing and diffing in TS; tree construction in Python, no code generation.

## Commands

```bash
npm install              # Install dependencies
npm run build            # Compile TypeScript (tsc → dist/)
npm test                 # Run all tests (tape + tsx)

# Run a single test file
npx tsx node_modules/tape/bin/tape test/parser/tree-parser.test.ts

# CLI tools
npx tsx src/cli/parse-tree.ts test_cases.md
npx tsx src/cli/parse-test-case.ts test_cases.md <test-name>
npx tsx src/cli/parse-codebase.ts <directory>

# Extract test fixtures from test_cases.md
npm run test:extract-fixtures

# Integration test: call analyze API, parse tree_md with parseTreeBlock (requires server running)
npm run test:semantic-tree-api
```

## Architecture

**Module system:** ES Modules (`"type": "module"` in package.json, NodeNext resolution). All internal TS imports use `.js` extensions.

**Zero runtime dependencies** (TS). `@babel/parser` is a devDependency, lazy-loaded in `codebase-parser.ts` for JS/TS AST extraction with regex fallback.

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
- **`src/parser/codebase-parser.ts`** — Codebase snapshots from markdown blocks, source files, or directory; Babel for JS/TS, regex for Python.
- **`src/parser/operation-parser.ts`** — Parses `--- OPERATION ---` blocks into Operation objects (AddNode, DeleteNode, MoveNode, etc.).
- **`src/diff/tree-diff.ts`** — Compares two SemanticTrees; matches nodes by stable ID; infers operations.
- **`src/actions/dispatcher.ts`** — Maps Operations to ActionResult stubs (plan arrays).
- **`src/index.ts`** — Public API barrel.

### Backend (Python — `server/api/`)

The backend builds semantic trees from a live codebase. It uses `api.config`, `api.openai_client`, and `api.tools.embedder` (adalflow).

- **`server/api/semantic_tree/`** — Integrated pipeline and routes:
  - **`models.py`** — Pydantic models aligned with `src/types.ts`.
  - **`extraction/`** — Discovery (directory walk), Python AST extraction, import edges.
  - **`indexing/`** — Entity-level chunking and FAISS vector store (embedder from `api.tools.embedder`); used as RAG inside analyze.
  - **`llm/`** — Prompt loader (`prompts/`), `<solution>` parsing, completion via `api.config.get_model_config()`.
  - **`pipeline/`** — Domain discovery, semantic parsing (RAG: one call per area + remaining), hierarchical construction, tree assembly.
  - **`output/tree_serializer.py`** — Tree → markdown (parseable by `parseTreeBlock()`) or JSON.
  - **`routes.py`** — FastAPI router: `POST /semantic_tree/analyze` (extract + index + RAG pipeline), `POST /semantic_tree/search`, `GET /semantic_tree/status`. On step failure (e.g. invalid LLM output), returns **422** with `intervention_required` (step + message); agent should stop for user fix.

Output markdown from the backend is designed to be consumed by TS `parseTreeBlock()` (same grammar as test fixtures).

### Test Structure

Tests use **tape** (TAP). Fixtures in `test/fixtures/cases/` are markdown test cases with BEFORE/AFTER trees and OPERATION blocks. Codebase snapshot tests use `test/requests/` (Python) and `test/mosaic/` (TypeScript).

### Design Documents

- **`prescriptive-semantic-tree-plan.md`** — Algorithm design: node schema, invariants, operation taxonomy.
- **`test_cases.md`** — Test spec: tree notation, operation syntax, codebase snapshot format.
- **`prompts/`** — LLM prompts for domain discovery, semantic parsing, hierarchical construction (used by `server/api/semantic_tree`).
