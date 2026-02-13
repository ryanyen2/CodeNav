# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodeNav is a parser and tree-diff engine for prescriptive semantic trees. It parses semantic trees from markdown notation, compares before/after states to infer operations, and dispatches to action stubs. Currently v0.1.0 — parsing and diffing only, no code generation.

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
```

## Architecture

**Module system:** ES Modules (`"type": "module"` in package.json, NodeNext resolution). All internal imports use `.js` extensions.

**Zero runtime dependencies.** `@babel/parser` is a devDependency, lazy-loaded in `codebase-parser.ts` for JS/TS AST extraction with regex fallback.

### Core Pipeline

```
Markdown tree notation → parseTreeBlock() → SemanticTree
                                                ↓
                         diffTrees(before, after) → TreeDiffResult[]
                                                        ↓
                              diffResultToOperation() → Operation
                                                           ↓
                                         dispatch() → ActionResult (stub plan)
```

### Key Modules

- **`src/types.ts`** — All type definitions. Semantic nodes have three components: `f` (feature/behavior), `m` (metadata/grounding), `c` (contract/interface). Sigils: `/` dir, `%` file, `$`/`^` leaf, `~` abstract.
- **`src/parser/tree-parser.ts`** — Custom line-based parser for markdown nested list → SemanticTree. Also parses `deps:` blocks with `(a) --rel--> (b)` notation.
- **`src/parser/codebase-parser.ts`** — Builds codebase snapshots from markdown blocks, source files, or directory discovery. Uses Babel for JS/TS deep signature extraction, regex for Python.
- **`src/parser/operation-parser.ts`** — Parses `--- OPERATION ---` blocks into Operation objects. Supports 9 operation types (AddNode, DeleteNode, MoveNode, etc.).
- **`src/diff/tree-diff.ts`** — Compares two SemanticTrees, matches nodes by stable ID (grounded: `fpath::entity`, else: feature path), infers operations.
- **`src/actions/dispatcher.ts`** — Maps Operations to ActionResult stubs with plan arrays describing intended steps.
- **`src/index.ts`** — Public API barrel file.

### Test Structure

Tests use **tape** (TAP-compliant). Test fixtures in `test/fixtures/cases/` are markdown test cases with BEFORE/AFTER trees and OPERATION blocks. Real codebases for snapshot testing live in `test/requests/` (Python) and `test/mosaic/` (TypeScript).

### Design Documents

- **`prescriptive-semantic-tree-plan.md`** — Detailed algorithmic design: node schema, invariants, operation taxonomy, algorithms for each operation type.
- **`test_cases.md`** — Comprehensive test specification with tree notation, operation syntax, and codebase snapshot format.
- **`prompts/`** — LLM instruction prompts for semantic parsing, hierarchical construction, and domain discovery.
