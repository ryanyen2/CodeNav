# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

**codoc** — a system that maintains a human-intent-level view of a codebase as a navigable feature tree, synchronized to the underlying code. Each node is a *feature*: a named unit of intent that binds to many code chunks across many files; a single file's chunks may belong to several features. The tree is first-class authored intent (not LLM-derived), and code attribution is a secondary index updated by the reflective pipeline.

This repo contains the Python core (`codoc/`) and the VS Code extension (`vscode-codoc/`).

## Commands

```bash
# Install (Python 3.11+ required; use pip or uv)
pip install -e .

# CLI — four commands, nothing more
codoc init                # index repo, propose initial tree, write .codoc/tree.codoc
codoc watch               # the daemon: run both loops as you edit code / tree.codoc
codoc status              # feature count, pending proposals, recent activity
codoc sync                # one-shot escape hatch: apply tree edits (Loop B), then reflect code (Loop A)

# watch flags
codoc watch --dry         # reflect + build coding directives, but don't spawn the agent
codoc watch --no-realize  # sync the tree but never spawn the coding agent

# Tests (run with Python 3.11)
python3.11 -m pytest tests/
```

The only human surface is `.codoc/tree.codoc`. You edit it directly; structural
proposals appear inline as `?` blocks (change `?`→`+` to accept, `?`→`-` or delete
to reject).

## Architecture

> **NOTE (2026-05): this repo was rewritten clean-slate.** The old
> reflective/intentional/planning/feedforward/realize/health pipelines, the
> transaction/constraint/obligation model, the projection layer, the FastAPI
> server, and the 20-command CLI were **deleted**. The cocoindex+LanceDB index,
> the tree-sitter adapters, and `core/tree_walk.py` were kept as the substrate.
> What follows describes the current system.

### Core idea

The tree is first-class authored intent; code attribution (bindings) is a
secondary index. The system is two loops:

- **Loop A — code → codoc** (`codoc/loop/loop_a.py`): snapshot-diff the index →
  auto-apply safe ops (refresh/attach/detach/small-amend) → if anything needs
  judgment, ONE LLM pass (`agent/tree_update.py`) returns the minimal node ops;
  structural ops (add/move/retire) are logged as pending proposals.
- **Loop B — codoc → code** (`codoc/loop/loop_b.py`): parse `tree.codoc` edits →
  apply user edits + proposal verdicts → for edits that imply code change, build a
  directive and spawn `claude -p` once → re-run Loop A on what was written.

A single LLM pass with full change + whole-tree-title context (plus a
`UNIQUE(file, symbol_path)` binding constraint) is what prevents duplicate nodes —
there are no move/fracture/coalesce detectors and no post-hoc dedup gates.

### Package layout (`codoc/`)

```
codoc/
  model/          # Pydantic: Feature, Binding, Event/NodeOp/NodeOpKind, HLC; ids.py
  store/          # db.py — Store over 3 SQLite tables (features, bindings, events) + 1 derived graph cache, WAL
  graph/          # Code dependency graph layer (derived cache, rebuildable):
                  #   extract.py   — references_in_chunk → edges; contain edges from symbol_path structure
                  #   query.py     — build_graph, update_graph (incremental), neighbors, ego_graph,
                  #                  topological_order, entry_points, neighbor_feature(store, symbol)
  loop/           # the two loops + their pieces:
                  #   diff.py        — compute_changeset; ChunkRef carries tokens_hash + types_hash
                  #   apply.py       — derive_auto_ops, apply_op, AMEND_SAFE_RATIO (the one threshold)
                  #   subtree.py     — select_relevant_subtree (file-locality seeds)
                  #   loop_a.py      — run_loop_a / apply_changeset (code → codoc);
                  #                    _detect_relocations (move via tokens_hash, rename via types_hash);
                  #                    _cover_uncovered_adds (coverage net: neighbor_feature or ADD_NODE proposal)
                  #   loop_b.py      — run_loop_b (codoc → code; build directive, spawn claude -p, reflect back)
                  #   bootstrap.py   — run_bootstrap / run_init (thin shim; organize=True by default)
                  #   bootstrap_hier.py — two-phase bootstrap:
                  #                       per-file pass (propose_file_features, one LLM call per file) +
                  #                       org pass (propose_organization, groups file-features under broad themes);
                  #                       _apply_ops_with_local_ids (resolves temp ids "n1"/"t1" → real ids,
                  #                       enables within-call nesting); _ensure_file_coverage (folds uncovered
                  #                       chunks into the file's largest node)
                  #   watch.py       — run_watch / process_batch (debounced router + self-write guard)
  agent/          # base.py (load_prompt/format_prompt/parse_solution/run_agent)
                  # tree_update.py     — the single incremental LLM call
                  # bootstrap_agent.py — propose_file_features, propose_organization (bootstrap-only LLM calls)
  codoc_file/     # render.py (store → tree.codoc + tree.bindings.json sidecar; refs grouped by file),
                  # parse.py (text → ParsedTree; skips ↪ refs: lines), diff.py (→ user ops + verdicts)
  lang/           # Tree-sitter adapters: python.py + typescript.py; get_adapter(), detect_language()  [KEPT]
  core/           # tree_walk.py (tokens_hash/types_hash/minhash) + chunk_matching/minhash.py  [KEPT substrate]
  pipelines/
    indexing/     # cocoindex_app.py, runner.update_index(), reader.read_all_chunks()  [KEPT]
  prompts/        # tree_update.txt, realize.txt (Loop B directive template)
                  # bootstrap_file.txt (per-file LLM prompt), bootstrap_org.txt (org-pass LLM prompt)
  cli/            # main.py — Typer app with init/watch/status/sync
  config.py       # LLM config (CODOC_PROVIDER, CODOC_MODEL, OPENAI_API_KEY, …)
```

### Data model key types

- **`Feature`** (`model/feature.py`): `{id, title, description, parent_id, retired, created_at, updated_at}`. `id` is a stable short id (`f-xxxxxxxx`) rendered into `tree.codoc` as `⟨f-id⟩`. ONE prose field, `description` — no slug, no intent/purpose/rationale/scenario, no status, no derived FeatureState.
- **`Binding`** (`model/binding.py`): `{id, feature_id, file, symbol_path, fingerprint, updated_at}`. The anchor is inlined as `(file, symbol_path)` — the index join key. `fingerprint` is the chunk's `tokens_hash`.
- **`NodeOp`** (`model/event.py`): `{kind, feature_id?, parent_id?, title?, description?, bindings, rationale}`.
- **`Event`** (`model/event.py`): `{id, at, source, op, applied, accepted_at}`. A *proposal* is an Event with `applied=False`; accepting flips it and runs the op.
- **`HLC`** (`model/hlc.py`): Hybrid Logical Clock; `to_str()` is lexicographically sortable. Used as the monotonic `created_at`/`updated_at`/`at` clock. [KEPT]

### NodeOp kinds

Safe (auto-applied): `ATTACH DETACH REFRESH AMEND` (AMEND only when the edit is small — `AMEND_SAFE_RATIO`, the sole threshold).
Structural (surfaced as `?` proposals in `tree.codoc`): `ADD_NODE MOVE_NODE RETIRE_NODE`.

### Storage schema

SQLite WAL at `.codoc/codoc.db`. **Three authoritative tables + one derived graph cache:**
- `features`, `bindings` (`UNIQUE(file, symbol_path)` — a chunk binds to at most one feature), `events` (append-only log; `applied=0` = pending proposal).
- `code_edges(src_file, src_symbol, dst_symbol, dst_name, dst_file, kind, internal)` — derived from `references_in_chunk`; safe to DROP and rebuild. PK `(src_symbol, dst_name, kind)`. `internal=1` edges are used for graph traversal; `internal=0` (external) are stored for display.

No transactions/constraints/obligations/binding_resolutions/citations tables, no JSONL audit lane.

The chunk index is owned by **cocoindex** and lives outside `codoc.db`: AST chunks + embeddings + identity hashes (tokens_hash / types_hash / minhash) are written to `.codoc/lancedb/code_chunks.lance` (LanceDB, embedded). Cocoindex's own memoization state lives in `.codoc/cocoindex.db/`. Together these provide durable, incremental, crash-resumable indexing — a killed `codoc init` resumes from the last completed file rather than re-embedding from scratch.

### Indexing layer (cocoindex + LanceDB)

`codoc/pipelines/indexing/` owns the chunk + embedding substrate. `update_index(root_dir, codoc_dir)` runs the cocoindex App once: walks the repo, parses each supported file via the existing tree-sitter adapters, embeds each AST chunk via sentence-transformers, and upserts to LanceDB. Memoized per-file: unchanged files cost nothing. Killed mid-run, the next call resumes from the last completed component.

Bootstrap and both loops call `update_index` first, then read from LanceDB via `read_all_chunks(codoc_dir)`. LanceDB rows carry `tokens_hash` (fingerprint), `types_hash` (AST-shape identity), and `minhash`. `tokens_hash` and `types_hash` are now both actively used (move/rename detection in Loop A); `minhash` remains computed-but-unread.

### Loop A in detail (code → codoc)

`compute_changeset` (`loop/diff.py`) reads the index, runs `update_index`, reads again, and keys both snapshots by `(file, symbol_path)` comparing `tokens_hash` → `ChangeSet{added, removed, modified}`. `ChunkRef` carries both `fingerprint` (= `tokens_hash`, move-invariant) and `types_hash` (AST-shape identity, rename-invariant).

`apply_changeset` has five phases:

1. **Auto-ops**: `derive_auto_ops` resolves trivially-safe changes (modified-bound → REFRESH, removed-bound → DETACH) with no LLM.
2. **Correspondence**: `_detect_relocations` pairs removed↔added chunks that are the same code relocated — a *move* (identical `tokens_hash`) or a *rename* (same-file unique `types_hash`, 1:1 only). Each match emits a deterministic ATTACH to the removed chunk's feature — **no LLM, no risk of dropped attribution**.
3. **LLM pass**: only if there are still-unbound additions or a feature lost its last binding — ONE `propose_tree_update` call with the change set, the file-locality seed subtree, **every node title** (de-dup context), and optional graph context. Safe ops apply immediately; structural ops become `applied=False` proposal Events.
4. **May-impact** (observability): `_compute_impacted` surfaces upstream dependent features of changed symbols for the LLM prompt.
5. **Coverage net**: `_cover_uncovered_adds` ensures no added chunk is silently dropped — it attaches to the `neighbor_feature` (graph-neighbor feature owning the most call/import edges to the new symbol) or, failing that, surfaces a pending ADD_NODE proposal.

### Bootstrap in detail

`run_bootstrap` (thin shim in `bootstrap.py`) delegates to `bootstrap_hier_from_chunks` in `bootstrap_hier.py`. Two phases:

1. **Per-file pass**: one `propose_file_features` LLM call per file. Sees only that file's chunks + per-symbol call/contain edges → structurally impossible to create a cross-file junk drawer. Temp node ids ("n1", "t1") in `NodeOp.feature_id` are resolved by `_apply_ops_with_local_ids` to real ids before apply — enabling within-call nesting. `_ensure_file_coverage` folds any uncovered chunks into the file's largest node (same-file, never a junk drawer); mints one node only if the model returned nothing.
2. **Org pass** (`organize=True` by default): one `propose_organization` LLM call grouping existing file-features under 3–6 broad theme parents via ADD_NODE + MOVE_NODE. `_feature_coupling` computes feature→feature call/import coupling lines as context for this call.

`run_bootstrap` signature: `run_bootstrap(root_dir, codoc_dir, *, repo_name=None, config=None, do_index=True, organize=True)`. The old `max_per_call` parameter is gone.

### Loop B in detail (codoc → code)

`run_loop_b` parses `tree.codoc`, diffs against the store (`codoc_file/diff.py`) into user ops + proposal verdicts, applies them (user edits are intentional → applied immediately), builds a directive from each code-implying op's `description` + bound symbols (`prompts/realize.txt`), spawns `claude -p … --dangerously-skip-permissions` once, then re-runs Loop A scoped to the files the agent wrote — surfacing any refinement the under-specified intent missed.

### Environment variables

| Var | Default | Description |
|---|---|---|
| `CODOC_PROVIDER` | `openai` | LLM provider (`openai` or `ollama`) |
| `CODOC_MODEL` | `gpt-4o` | LLM model name |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `CODOC_BASE_URL` | — | Custom OpenAI-compatible base URL |
| `CODOC_EMBEDDER_PROVIDER` | `sentence-transformers` | Embedder provider (used by dedup / proposal similarity; chunk embeddings live in cocoindex) |
| `CODOC_EMBEDDER_MODEL` | `all-MiniLM-L6-v2` | Embedder model |
| `COCOINDEX_DB` | `.codoc/cocoindex.db` | Path to cocoindex's internal memoization state (auto-set by `update_index`) |
| `CODOC_LANCE_PATH` | `.codoc/lancedb` | Path to the LanceDB directory holding the `code_chunks` table |
| `CODOC_ROOT_DIR` | cwd | Root directory for API server |
| `CODOC_LOG_PROMPTS` | — | Set to `1` to log LLM prompt+response to stderr |

### Render + sidecar

`codoc_file/render.py` writes two files on every `write_tree` call:

- **`.codoc/tree.codoc`** — human-readable feature tree. Refs line format: `↪ refs: api.py › get, post, put +4  ·  models.py › Response` (grouped by file, `‹module›` for `__module__` chunks; capped at `_REFS_MAX_FILES=4` files and `_REFS_MAX_PER_FILE=4` symbols each).
- **`.codoc/tree.bindings.json`** — machine-readable sidecar for the IDE. Schema: `{version, by_feature{fid:[{file,symbol}]}, by_file{file:[{symbol,feature_id,feature_title}]}, features{fid:{title,parent_id}}}`. Written atomically (tmp → rename). `parse.py` explicitly skips `↪ refs:` lines so render→parse→diff is always a no-op.

### VSCode extension (`vscode-codoc/`)

File-based; no HTTP server, no port. `WorkspaceState` watches `**/.codoc/tree.codoc` and `**/.codoc/tree.bindings.json`; parses them on any change; fires `onDidChange` to refresh providers. Status bar is never "offline": `$(sync) codoc: not initialized` | `$(bell) codoc: N proposals` | `$(check) codoc: N`.

Key source files:
- `src/state/workspace-state.ts` — detects root dir, reloads on file change, drives status bar
- `src/state/tree-model.ts` — TypeScript port of `parse.py`; skips `↪ refs:` lines
- `src/state/bindings-model.ts` — sidecar types + `entriesForFile` / `bindingsForFeature` helpers
- `src/providers/feature-tree-view.ts` — Explorer panel reading `WorkspaceState.features`
- `src/providers/code-lens.ts` — CodeLens on `def`/`class` lines, reads `sidecar.by_file`
- `src/extension.ts` — activates `WorkspaceState`, registers commands (`codoc.open`, `codoc.sync`, `codoc.navigateToFeature`, fold/expand commands)

### Status / next

Two-loop system fully implemented and tested (157 Python unit tests pass; TypeScript compiles clean). The cocoindex integration test for `compute_changeset` is gated to skip when the embedding model can't load. Real bootstrap on `test/requests` (320 chunks) yields 28 features, depth 3, 23/28 nested, zero empty descriptions, zero duplicate titles.

Possible next steps: ego-graph context for Loop A subtree selection (Phase 3 of the plan); may-impact propagation in the LLM prompt (Phase 4); trim `minhash` from the index schema.

## Test fixtures

`test/draco/` (small Python), `test/requests/` (real-world Python library), `test/mosaic/` (TypeScript), `test/small_python_repo/` (toy Python), `test/altair/`, `test/gofish-python/`, `test/nanochat/` (additional Python codebases).
