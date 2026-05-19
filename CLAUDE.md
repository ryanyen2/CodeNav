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
codoc bootstrap [--root-dir DIR]        # cluster codebase, propose feature cards
codoc bootstrap finish                  # mark bootstrap done
codoc reflect [--from-ref REF]          # run reflective pipeline on latest commits
codoc status                            # summary: features, pending proposals, last HLC
codoc proposals                         # list pending proposals (slug + kind + HLC prefix)
codoc accept <slug-or-hlc-prefix>       # accept a proposal by slug or HLC prefix
codoc accept --all-pending              # batch-accept all pending proposals
codoc reject <slug-or-hlc-prefix>       # reject a proposal
codoc reject --all-pending --yes        # batch-reject all (no confirm)
codoc list                              # browse feature tree (Rich table)
codoc show <slug-path>                  # show feature + state + bindings
codoc search <term>                     # fuzzy search slug/intent
codoc edit <slug-path> --intent "..."   # amend intent non-interactively
codoc rename <slug-path> <new-slug>     # rename slug
codoc retire <slug-path>                # retire feature

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

> **Deprecated aliases (still work, emit a notice):** `codoc tx list`, `codoc tx accept HLC`, `codoc tx reject HLC`, `codoc feature show UUID`, `codoc feature amend UUID`, `codoc feature rename UUID NEW`, `codoc feature retire UUID`.

## Architecture

### Core inversion vs. CodeNav

CodeNav (old): code → LLM → tree → diff → operations. Tree is derived.
codoc (new): tree is authored intent; code attribution is a secondary index. Features have stable UUIDs, prose, constraints (Phase 5), and many-to-many bindings to code chunks. The reflective pipeline observes commits and proposes attribution updates; the user curates.

### Package layout (`codoc/`)

```
codoc/
  model/          # Pydantic types: Feature, Anchor, Binding, Constraint, Transaction, Obligation, HLC, FeatureState
  core/           # Deterministic core: fingerprint, anchor_resolver, state_derivation, subtree_hash, binding_graph, log (TransactionLog)
  core/crdt/      # pycrdt-backed CRDT shapes: AWMap, LWWRegister, ORSet
  lang/           # Tree-sitter LanguageAdapter protocol + python.py + typescript.py; get_adapter(), detect_language()
  storage/        # SQLiteStore (WAL), JSONLLog (audit), FaissIndex
  pipelines/
    bootstrap/    # cluster.py → propose.py → runner.py (run_bootstrap, finish_bootstrap)
    reflective/   # commit_diff → fingerprint_compare → escalate → propose → runner (run_reflect)
    intentional/  # amend.py, rename.py, retire.py, runner.py (IntentionalRunner)
  agents/         # LLM dispatch: bootstrap_clustering, attribution; base utilities (load_prompt, parse_solution)
  prompts/        # LLM prompt templates: bootstrap_clustering.txt, attribution_judgment.txt
  api/            # FastAPI app (app.py + routes.py)
  cli/            # Typer CLI: main.py + init/bootstrap/reflect/tx/feature/gate_run/server
  config.py       # LLM + embedder config (env-driven: CODOC_PROVIDER, CODOC_MODEL, OPENAI_API_KEY, etc.)
```

### Data model key types

- **`Feature`**: `{uuid, slug, parent_uuid, intent, retired, created_at_hlc, updated_at_hlc}`. State computed on demand by `core.state_derivation`.
- **`Anchor`**: `{file, symbol_path?, ts_query?, occurrence_index}`. At least one of `symbol_path`/`ts_query` required. Symbol-path-first resolution; NO byte ranges stored.
- **`Binding`**: `{uuid, feature_uuid, anchor, fingerprint, fingerprint_at_hlc, parent_symbol?}`.
- **`Transaction`**: `{hlc, parent_hlcs, kind, payload, author, proposal, accepted_at, label}`. Append-only log; proposals pending user review have `proposal=True`.
- **`HLC`**: Hybrid Logical Clock (`logical_time, wall_clock, node_id`). `HLC.to_str()` is lexicographically sortable.
- **`FeatureState`**: `Stub | Drafting | Stable | Strained | Deprecated | Severed` — derived, never stored.

### Transaction kinds (Phase 1)

Reflective (proposed by pipeline): `INTRODUCE ABSORB EVICT RETIRE_REFLECTIVE REATTRIBUTE FRACTURE COALESCE RENAME_INFER`
Intentional v1 (user-originated, no cascade): `AMEND RENAME RETIRE`
Phase 2+: `SPLIT MERGE RESTRUCTURE REWIND BRANCH MERGE_BRANCH INSTATE_CONSTRAINT LIFT_CONSTRAINT`

### Storage schema

SQLite WAL at `.codoc/codoc.db`. Tables: `transactions features bindings constraints obligations chunk_fingerprints`. JSONL audit lane at `.codoc/log.jsonl` (rebuildable from SQLite).

### Reflective pipeline

Triggered by git post-commit hook (installed by `codoc init`). Flow: `git diff → tree-sitter chunk re-parse → fingerprint compare → cheap heuristics → LLM escalation (attribution agent) → proposal queue`. Scales with the change, not the codebase. Never re-indexes everything after bootstrap.

### Validation gate

Run `codoc gate-run` after labeling proposals from bootstrap on `test/draco` and reflective replay on `test/requests` (Python) + `test/mosaic` (TypeScript). Pass thresholds: accept-verbatim ≥ 60% AND (verbatim + light-edit) ≥ 80% AND median light-edit ≤ 80 chars. If gate fails, the CRDT/cascade/branching architecture must be collapsed to a simpler explicit log.

### Environment variables

| Var | Default | Description |
|---|---|---|
| `CODOC_PROVIDER` | `openai` | LLM provider (`openai` or `ollama`) |
| `CODOC_MODEL` | `gpt-5.4-mini` | LLM model name |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `CODOC_BASE_URL` | — | Custom OpenAI-compatible base URL |
| `CODOC_EMBEDDER_PROVIDER` | `sentence-transformers` | Embedder provider |
| `CODOC_EMBEDDER_MODEL` | `all-MiniLM-L6-v2` | Embedder model |
| `CODOC_ROOT_DIR` | cwd | Root directory for API server |
| `CODOC_LOG_PROMPTS` | — | Set to `1` to log LLM prompt+response to stderr |

### Phase plan

- **Phase 1 (current)**: data model + core + storage + tree-sitter adapters (Python+TS) + bootstrap + reflective + intentional-minimal (AMEND/RENAME/RETIRE) + CLI + FastAPI.
- **Phase 1.5**: VSCode webview in `codoc-vscode` repo (pending gate passing).
- **Phase 2**: SPLIT/MERGE/RESTRUCTURE/REWIND + cascade engine + agent reconciliation.
- **Phase 3**: BRANCH/MERGE_BRANCH + pycrdt merge + conflict resolution UI.
- **Phase 4**: operational polish.
- **Phase 5**: constraint subsystem (INSTATE_CONSTRAINT/LIFT_CONSTRAINT, inference).

## Test fixtures

`test/draco/` (small Python), `test/requests/` (real-world Python library), `test/mosaic/` (TypeScript), `test/small_python_repo/` (toy Python), `test/altair/`, `test/gofish-python/`, `test/nanochat/` (additional Python codebases).
