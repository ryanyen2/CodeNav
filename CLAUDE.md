# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

**codoc** — a system that maintains a human-intent-level view of a codebase as a navigable feature tree, synchronized to the underlying code. Each node is a *feature*: a named unit of intent that binds to many code chunks across many files; a single file's chunks may belong to several features. The tree is first-class authored intent (not LLM-derived), and code attribution is a secondary index updated by the reflective pipeline.

This repo contains the Python core (`codoc/`). The VSCode extension lives in a separate `codoc-vscode` repo.

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
  store/          # db.py — Store over 3 SQLite tables (features, bindings, events), WAL
  loop/           # the two loops + their pieces:
                  #   diff.py      — compute_changeset (index snapshot before/after → added/removed/modified)
                  #   apply.py     — derive_auto_ops, apply_op, AMEND_SAFE_RATIO (the one threshold)
                  #   subtree.py   — select_relevant_subtree (embedding-free, file-locality seeds)
                  #   loop_a.py    — run_loop_a / apply_changeset (code → codoc)
                  #   loop_b.py    — run_loop_b (codoc → code; build directive, spawn claude -p, reflect back)
                  #   bootstrap.py — run_bootstrap / run_init (Loop A against an empty tree)
                  #   watch.py     — run_watch / process_batch (debounced router + self-write guard)
  agent/          # base.py (load_prompt/format_prompt/parse_solution/run_agent), tree_update.py (the single LLM call)
  codoc_file/     # render.py (store → tree.codoc), parse.py (text → ParsedTree), diff.py (→ user ops + verdicts)
  lang/           # Tree-sitter adapters: python.py + typescript.py; get_adapter(), detect_language()  [KEPT]
  core/           # tree_walk.py (tokens_hash/types_hash/minhash) + chunk_matching/minhash.py  [KEPT substrate]
  pipelines/
    indexing/     # cocoindex_app.py, runner.update_index(), reader.read_all_chunks()  [KEPT]
  prompts/        # tree_update.txt (the one tree-update prompt), realize.txt (Loop B directive template)
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

SQLite WAL at `.codoc/codoc.db`. Three tables: `features`, `bindings`
(`UNIQUE(file, symbol_path)` — a chunk binds to at most one feature), `events`
(append-only log; `applied=0` = pending proposal). No transactions/constraints/
obligations/binding_resolutions/citations tables, no JSONL audit lane.

The chunk index is owned by **cocoindex** and lives outside `codoc.db`: AST chunks + embeddings + identity hashes (tokens_hash / types_hash / minhash) are written to `.codoc/lancedb/code_chunks.lance` (LanceDB, embedded). Cocoindex's own memoization state lives in `.codoc/cocoindex.db/`. Together these provide durable, incremental, crash-resumable indexing — a killed `codoc init` resumes from the last completed file rather than re-embedding from scratch.

### Indexing layer (cocoindex + LanceDB)

`codoc/pipelines/indexing/` owns the chunk + embedding substrate. `update_index(root_dir, codoc_dir)` runs the cocoindex App once: walks the repo, parses each supported file via the existing tree-sitter adapters, embeds each AST chunk via sentence-transformers, and upserts to LanceDB. Memoized per-file: unchanged files cost nothing. Killed mid-run, the next call resumes from the last completed component.

Bootstrap and both loops call `update_index` first, then read from LanceDB via `read_all_chunks(codoc_dir)`. LanceDB rows carry the `tokens_hash` (fingerprint), `types_hash`, and `minhash`; the rewritten system reads only `tokens_hash` (the others remain computed-but-unread).

### Loop A in detail (code → codoc)

`compute_changeset` (`loop/diff.py`) reads the index, runs `update_index`, reads again, and keys both snapshots by `(file, symbol_path)` comparing `tokens_hash` → `ChangeSet{added, removed, modified}`. `derive_auto_ops` resolves the trivial parts (modified-bound → REFRESH, removed-bound → DETACH) with no LLM. Only if there are unbound additions or a feature lost its last binding does `apply_changeset` make the single `propose_tree_update` LLM call, passing the change set, the file-locality seed subtree (full descriptions + bindings), and **every node title** (the de-dup context). Safe ops apply immediately; structural ops become `applied=False` proposal Events.

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

### Status / next

The clean-slate two-loop system is implemented and unit-tested (model+store,
Loop A routing/apply, .codoc render/parse/diff round-trip, Loop B dry-run + spawn
wiring, watch routing + self-write guard, bootstrap dedup, CLI). The cocoindex
integration test for `compute_changeset` is gated to skip when the embedding
model can't load (a broken `transformers` install in some environments) — the
logic itself is covered by unit tests with a mocked LLM.

Possible next steps: trim the now-unread `types_hash`/`minhash` from the index
schema; a richer `status` view; a VSCode surface that reads `codoc.db` directly.

## Test fixtures

`test/draco/` (small Python), `test/requests/` (real-world Python library), `test/mosaic/` (TypeScript), `test/small_python_repo/` (toy Python), `test/altair/`, `test/gofish-python/`, `test/nanochat/` (additional Python codebases).
