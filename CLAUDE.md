# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

**codoc** — a system that maintains a human-intent-level view of a codebase as a navigable feature tree, synchronized to the underlying code. Each node is a *feature*: a named unit of intent that binds to many code chunks across many files; a single file's chunks may belong to several features. The tree is first-class authored intent (not LLM-derived), and code attribution is a secondary index updated by the reflective pipeline.

This repo contains the Python core (`codoc/`). The VSCode extension lives in a separate `codoc-vscode` repo.

## Commands

```bash
# Install (Python 3.11+ required; use pip or uv)
pip install -e .

# CLI — preferred top-level verbs
codoc init                              # init .codoc/ and install git post-commit hook
codoc bootstrap [--with-intent]         # cluster codebase, propose feature tree
codoc bootstrap finish                  # mark bootstrap done

# Proposals
codoc proposals                         # list pending proposals
codoc accept <slug-or-hlc-prefix>       # accept a proposal by slug or HLC prefix
codoc accept --all                      # batch-accept all pending proposals
codoc reject <slug-or-hlc-prefix>       # reject a proposal
codoc reject --all --yes                # batch-reject all (no confirm)

# Top-down planning (Flow 1)
codoc plan "<prompt>"                   # planning agent → propose tree changes

# Code-driven updates (Flow 2)
codoc reflect --file <path>             # on-save reflect (no git refs; can be repeated)
codoc reflect [--from-ref REF]          # post-commit reflect via git refs

# Browse
codoc list                              # browse feature tree (Rich table)
codoc show <slug-path>                  # show feature + state + bindings
codoc search <term>                     # fuzzy search slug/intent
codoc edit <slug-path> --intent "..."   # amend intent non-interactively
codoc rename <slug-path> <new-slug>     # rename slug (title resets to slug)
codoc retire <slug-path>                # retire feature
codoc status                            # summary: features, pending proposals, last HLC

# Projection workflow
codoc projection render                 # DB → .codoc/tree/ files
codoc projection sync                   # .codoc/tree/ edits → DB → re-render
codoc projection diff                   # dry-run diff (shows ops, no apply)

# Gate and server
codoc gate-run [--report]               # compute validation gate metrics
codoc server [--port 8001]              # start FastAPI server

# Tests (run with Python 3.11)
python3.11 -m pytest tests/
```

> **Deprecated aliases (still work, print a notice):** `codoc tx list`, `codoc tx accept HLC`, `codoc tx reject HLC`, `codoc feature show UUID`, `codoc feature amend UUID`, `codoc feature rename UUID NEW`, `codoc feature retire UUID`. Use the slug-path commands above instead.

## Architecture

### Core inversion vs. CodeNav

CodeNav (old): code → LLM → tree → diff → operations. Tree is derived.
codoc (new): tree is authored intent; code attribution is a secondary index. Features have stable UUIDs, prose, constraints (Phase 5), and many-to-many bindings to code chunks. The reflective pipeline observes commits and proposes attribution updates; the user curates.

Two interaction flows: **Flow 1 (top-down)** — `codoc plan "<prompt>"` proposes semantic tree changes as diff hunks; accepted proposals carry a `coding_directive` for coding agents. **Flow 2 (bottom-up)** — `codoc reflect --file <path>` runs on save; fingerprint-only changes complete in < 200 ms; structural changes escalate to LLM (1–3 s).

### Package layout (`codoc/`)

```
codoc/
  model/          # Pydantic types: Feature, Anchor, Binding, Constraint, Transaction, Obligation, HLC, FeatureState
  core/           # Deterministic core: fingerprint, anchor_resolver, state_derivation, subtree_hash, binding_graph, log (TransactionLog)
                  #   reconciler.py    — central comparison engine: compare(binding, chunks_index) → Comparison
                  #   tree_walk.py     — one tree-sitter walker emitting (tokens_hash, types_hash, minhash)
                  #   lens.py          — get/put facade naming the projection layer as a bidirectional lens
                  #   feature_view.py  — resolve_feature(store, feature) → FeatureView with live binding_resolutions
  lang/           # Tree-sitter LanguageAdapter protocol + python.py + typescript.py; get_adapter(), detect_language()
  storage/        # SQLiteStore (WAL), JSONLLog (audit)
  agents/         # LLM dispatch: bootstrap_clustering, attribution, planning; base utilities (load_prompt, parse_solution)
  pipelines/
    indexing/     # cocoindex_app.py (walk + AST chunk + embed → LanceDB), runner.update_index(), reader.read_all_chunks()
    bootstrap/    # runner.run_bootstrap (update_index → cluster on LanceDB rows → LLM per cluster → INTRODUCE), semantic_cluster, propose
    reflective/   # runner.run_reflect (snapshot LanceDB before/after update_index → diff → reconcile + LLM) — no git diff, no chunk_fingerprints table
    intentional/  # amend.py, rename.py, retire.py, runner.py (IntentionalRunner)
    planning/     # runner.py (run_plan) — top-down planning from user prompt
    health/       # runner.py (reconcile_files, reconcile_all) — periodic binding-health sweep; no LLM calls
  prompts/        # LLM prompt templates: bootstrap_clustering.txt, attribution_judgment.txt, planning.txt
  api/            # FastAPI app (app.py + routes.py)
  cli/            # Typer CLI: main.py + init/bootstrap/reflect/tx/feature/gate_run/server
  config.py       # LLM + embedder config (env-driven: CODOC_PROVIDER, CODOC_MODEL, OPENAI_API_KEY, etc.)
```

### Data model key types

- **`Feature`**: `{uuid, slug, title, parent_uuid, intent, retired, created_at_hlc, updated_at_hlc}`. `title` is the 2–5 word prose display name (falls back to slug if empty). State computed on demand by `core.state_derivation`.
- **`Anchor`**: `{file, symbol_path?, ts_query?, occurrence_index}`. At least one of `symbol_path`/`ts_query` required. Symbol-path-first resolution; NO byte ranges stored.
- **`Binding`**: `{uuid, feature_uuid, anchor, fingerprint, fingerprint_at_hlc, parent_symbol?, types_hash?, minhash_sketch?}`. `types_hash` is a rename-invariant structural fingerprint (node-type sequence); `minhash_sketch` is a 16-byte MinHash for fast similarity queries. Both are computed by `core.tree_walk` and persisted to enable structural move detection.
- **`Transaction`**: `{hlc, parent_hlcs, kind, payload, author, proposal, accepted_at, label}`. Append-only log; proposals pending user review have `proposal=True`.
- **`HLC`**: Hybrid Logical Clock (`logical_time, wall_clock, node_id`). `HLC.to_str()` is lexicographically sortable.
- **`FeatureState`**: `Stub | Drafting | Stable | Strained | Deprecated | Severed` — derived, never stored.

### Transaction kinds (Phase 1)

Reflective (proposed by pipeline): `INTRODUCE ABSORB EVICT RETIRE_REFLECTIVE REATTRIBUTE FRACTURE COALESCE RENAME_INFER`
Intentional v1 (user-originated, no cascade): `AMEND RENAME RETIRE`
Phase 2+: `SPLIT MERGE RESTRUCTURE REWIND BRANCH MERGE_BRANCH INSTATE_CONSTRAINT LIFT_CONSTRAINT`

### Storage schema

SQLite WAL at `.codoc/codoc.db`. Tables: `transactions features bindings constraints obligations binding_resolutions`. JSONL audit lane at `.codoc/log.jsonl` (rebuildable from SQLite). `binding_resolutions` stores the latest comparison verdict per binding (still_aligned / moved / drifted / severed), populated by both the reflective pipeline and `codoc health`.

The chunk index is owned by **cocoindex** and lives outside `codoc.db`: AST chunks + embeddings + identity hashes (tokens_hash / types_hash / minhash) are written to `.codoc/lancedb/code_chunks.lance` (LanceDB, embedded). Cocoindex's own memoization state lives in `.codoc/cocoindex.db/`. Together these provide durable, incremental, crash-resumable indexing — a killed `codoc init` resumes from the last completed file rather than re-embedding from scratch.

### Indexing layer (cocoindex + LanceDB)

`codoc/pipelines/indexing/` owns the chunk + embedding substrate. `update_index(root_dir, codoc_dir)` runs the cocoindex App once: walks the repo, parses each supported file via the existing tree-sitter adapters, embeds each AST chunk via sentence-transformers, and upserts to LanceDB. Memoized per-file: unchanged files cost nothing. Killed mid-run, the next call resumes from the last completed component.

Bootstrap and reflective both call `update_index` first, then read from LanceDB via `read_all_chunks(codoc_dir)`. There is no parallel `chunk_fingerprints` SQLite table — LanceDB rows carry the `tokens_hash` (fingerprint), `types_hash` (rename-invariant skeleton), and `minhash` (Jaccard sketch) needed for reconciliation.

### Reflective pipeline

Triggered by git post-commit hook (installed by `codoc init`) or on save. Flow: **snapshot LanceDB → `update_index` → snapshot LanceDB → diff → reconcile against bindings → LLM escalation → proposal queue**. The before/after snapshot diff replaces the old git-diff + `chunk_fingerprints` mechanism; cocoindex's per-file memoization keeps the cost proportional to the change set. `from_ref`/`to_ref` parameters on `run_reflect` are retained for back-compat but unused — disk state via cocoindex is the source of truth.

The reconciler verdict domain (still_aligned / moved / drifted / severed / novel) is unchanged, as are move detection (RefDiff-2 / MinHash) and FRACTURE / COALESCE detection. Move detection now reads old chunk sources from the LanceDB pre-snapshot instead of `git show`. The `health` pipeline still runs its own sweep-based drift detection, writing results to `binding_resolutions`.

### Validation gate

Run `codoc gate-run` after labeling proposals from bootstrap on `test/draco` and reflective replay on `test/requests` (Python) + `test/mosaic` (TypeScript). Pass thresholds: accept-verbatim ≥ 60% AND (verbatim + light-edit) ≥ 80% AND median light-edit ≤ 80 chars. If gate fails, the cascade/branching architecture must be collapsed to a simpler explicit log.

### Environment variables

| Var | Default | Description |
|---|---|---|
| `CODOC_PROVIDER` | `openai` | LLM provider (`openai` or `ollama`) |
| `CODOC_MODEL` | `gpt-5.4-mini` | LLM model name |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `CODOC_BASE_URL` | — | Custom OpenAI-compatible base URL |
| `CODOC_EMBEDDER_PROVIDER` | `sentence-transformers` | Embedder provider (used by dedup / proposal similarity; chunk embeddings live in cocoindex) |
| `CODOC_EMBEDDER_MODEL` | `all-MiniLM-L6-v2` | Embedder model |
| `COCOINDEX_DB` | `.codoc/cocoindex.db` | Path to cocoindex's internal memoization state (auto-set by `update_index`) |
| `CODOC_LANCE_PATH` | `.codoc/lancedb` | Path to the LanceDB directory holding the `code_chunks` table |
| `CODOC_ROOT_DIR` | cwd | Root directory for API server |
| `CODOC_LOG_PROMPTS` | — | Set to `1` to log LLM prompt+response to stderr |

### Phase plan

- **Phase 1 (current)**: data model + core + storage + tree-sitter adapters (Python+TS) + cocoindex/LanceDB indexing substrate + bootstrap (semantic clustering over LanceDB embeddings) + reflective (LanceDB snapshot diff, via central reconciler) + binding-health sweep + intentional-minimal (AMEND/RENAME/RETIRE) + planning pipeline (codoc plan) + projection layer (render/sync/diff) + CLI + FastAPI.
- **Phase 1.5**: VSCode webview in `codoc-vscode` repo (pending gate passing).
- **Phase 2**: SPLIT/MERGE/RESTRUCTURE/REWIND + cascade engine + agent reconciliation.
- **Phase 3**: BRANCH/MERGE_BRANCH + prose CRDT merge (pycrdt, scoped to AMEND × AMEND conflicts) + conflict resolution UI.
- **Phase 4**: operational polish.
- **Phase 5**: constraint subsystem (INSTATE_CONSTRAINT/LIFT_CONSTRAINT, inference).

## Test fixtures

`test/draco/` (small Python), `test/requests/` (real-world Python library), `test/mosaic/` (TypeScript), `test/small_python_repo/` (toy Python), `test/altair/`, `test/gofish-python/`, `test/nanochat/` (additional Python codebases).
