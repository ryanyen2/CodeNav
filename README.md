# CodeNav — Prescriptive Semantic Tree

Parser and tree-diff engine for **prescriptive semantic trees**: parse trees from markdown, compare before/after to infer operations, and dispatch to action stubs. The **TypeScript layer** does parsing, diffing, and dispatch; the **Python server** builds semantic trees from a codebase via an integrated RAG + LLM pipeline (analyze → tree markdown/JSON parseable by the frontend).

## Concepts (from plan + test_cases)

- **Semantic tree**: Markdown nested list with sigils (`/` dir, `%` file, `$`/`^` leaf, `~` abstract), grounding `[path]`, entity `(name)`, contract `{sig: ...}`, and `deps:` block.
- **Operations**: AddNode, DeleteNode, MoveNode, EditFeature, EditContract, ReorderChildren (plus ExtractAndGroup, SplitFunction, MergeNodes).
- **Tree diff**: Compare tree before vs after → infer which operation(s) occurred → convert to `Operation` for dispatch.
- **Dispatch**: Map each operation to an action result (stub plan only; no code generation yet).

## Setup

**TypeScript (parser, diff, dispatch):**

```bash
npm install
npm run build
```

**Server (semantic tree pipeline):** See [server/README.md](server/README.md). From `server/`: `uv sync`, set `.env` (e.g. `OPENAI_API_KEY`), then `uv run python main.py`. The API exposes `POST /semantic_tree/analyze` (extract + index + RAG + LLM → tree); returns 422 `intervention_required` when a step needs your fix.

## Usage

**Parse a tree from a test case file** (uses first real `--- TREE (BEFORE) ---` / `--- EXPECTED TREE (AFTER) ---` block):

```bash
npx tsx src/cli/parse-tree.ts test_cases.md
```

**Parse a test case, run diff, and dispatch**:

```bash
npx tsx src/cli/parse-test-case.ts test_cases.md add_patch_endpoint
```

**From code**:

```ts
import {
  parseTreeBlock,
  extractTreeBlockFromTestCase,
  parseOperationBlock,
  extractOperationBlock,
  diffTrees,
  diffResultToOperation,
  dispatch,
  parseCodebaseBlock,
  discoverCodebase,
} from 'codenav-semantic-tree';

// Tree from markdown
const treeBefore = parseTreeBlock(markdownTreeBlock);
const treeAfter = parseTreeBlock(markdownTreeAfter);
const deps = treeBefore.deps;

// Operation from OPERATION block
const opBlock = extractOperationBlock(testCaseContent);
const operation = parseOperationBlock(opBlock);

// Infer operation from diff
const diffs = diffTrees(treeBefore, treeAfter);
const op = diffResultToOperation(diffs[0], treeBefore, treeAfter);

// Dispatch (stub)
const result = dispatch(operation, treeAfter);
// result.kind === 'add_node' | 'delete_node' | ... ; result.plan = [...]
```

## Parser choice

The tree format is **custom** (sigils + inline annotations), so we use a **custom line-based parser** rather than `@textlint/markdown-to-ast`. The markdown list structure is parsed by indentation and `- ` + sigil; dependencies by the `deps:` block and `(a) --rel--> (b)` lines. You can later plug in a markdown AST (e.g. remark) to locate fenced blocks or list blocks, then pass the extracted text into `parseTreeBlock` unchanged.

## Layout

- `src/types.ts` — Node (f, m, c), edges, operations, codebase snapshot.
- `src/parser/tree-parser.ts` — Tree block + deps parsing; `findNodeByPath`.
- `src/parser/operation-parser.ts` — OPERATION block → `Operation`.
- `src/parser/codebase-parser.ts` — Codebase snapshot from test block or `discoverCodebase(rootDir)`.
- `src/diff/tree-diff.ts` — `diffTrees(before, after)` → `TreeDiffResult[]`; `diffResultToOperation`.
- `src/actions/dispatcher.ts` — `dispatch(operation, tree)` → `ActionResult` (stub plans only).
- `server/` — Python API: integrated analyze pipeline (extract → index → RAG → tree), search, status. See [server/README.md](server/README.md).

## Testing the pipeline

With the server running (`cd server && uv run python main.py`), from the repo root:

```bash
npm run test:semantic-tree-api
```

Uses `test/requests/` as the codebase, calls `POST /semantic_tree/analyze`, and verifies the response is parseable with `parseTreeBlock()`. On 422, the script prints `intervention_required` step and message.

## Next steps (when you add generation)

- Implement real handlers in the dispatcher (call LLM, write files, run AST).
- Run post-check (invariants) and surface drift.
- Resolve conflicts (name collision, broken deps, grounding) and prompt user when needed.
