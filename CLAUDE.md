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
codoc watch --dry         # reflect + apply tree edits, but don't queue realization directives
codoc watch --no-realize  # sync the tree but never queue directives for the session

# Tests (run with Python 3.11)
python3.11 -m pytest tests/
```

The only human surface is `.codoc/tree.codoc`. You edit titles/descriptions
directly; structural proposals appear at the bottom as a git-style diff block and
are accepted/rejected with the IDE's inline **Accept / Reject** actions (which
write verdicts to `.codoc/inbox.json` — there is no accept/reject *syntax* to
type). Feature ids (`⟨f-id⟩`) stay on disk for stable identity but the IDE hides
them. Code is cited inline with markdown links: `[label](codoc:file.py#symbol)`.

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
  directive and **queue it for the live session** in `.codoc/realize.md` (status
  `awaiting_impl`). The session implements it via `/codoc:realize`; the existing
  Stop-hook reflection / watch-daemon Loop A then closes the loop. No headless
  `claude -p` is spawned.

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
                  #   inbox.py       — .codoc/inbox.json verdict channel (IDE Accept/Reject → loops)
                  #   status.py      — .codoc/status.json lifecycle (in_sync/code_drift/tree_dirty/awaiting_impl/realizing)
                  #   diff.py        — compute_changeset; ChunkRef carries tokens_hash + types_hash
                  #   apply.py       — derive_auto_ops, apply_op, AMEND_SAFE_RATIO (the one threshold)
                  #   subtree.py     — select_relevant_subtree (file-locality seeds)
                  #   loop_a.py      — run_loop_a / apply_changeset (code → codoc);
                  #                    _detect_relocations (move via tokens_hash, rename via types_hash);
                  #                    _cover_uncovered_adds (coverage net: neighbor_feature or ADD_NODE proposal)
                  #   loop_b.py      — run_loop_b (codoc → code; build directive, queue .codoc/realize.md for the live session)
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
                  # paths.py           — shared find_codoc_dir (hooks + MCP)
                  # hook.py / install_hooks.py — CC hooks + .claude/.mcp.json installer
                  # propose.py         — `codoc propose` CLI plumbing (human/test path)
  mcp/            # codoc MCP server (FastMCP, stdio) — agent-driven reflection:
                  #   tools.py  — plain functions (open store → apply_op → write_tree):
                  #               read_tree/read_status, propose_add/amend/move/retire,
                  #               attach, reflect (bulk), plan_add/plan_status
                  #   server.py — @mcp.tool wrappers (codoc_tree, codoc_reflect, codoc_plan_add, …);
                  #               main() = `codoc-mcp` console script. Resolves .codoc from cwd.
  codoc_file/     # render.py (store → tree.codoc + tree.bindings.json sidecar; hidden ⟨f-id⟩;
                  #            proposals as a diff block under "# ── pending changes"),
                  # parse.py (text → ParsedTree; multi-paragraph descriptions, extract_refs,
                  #           ignores everything past the pending sentinel), diff.py (→ user ops only)
  lang/           # Tree-sitter adapters: python.py + typescript.py; get_adapter(), detect_language()  [KEPT]
  core/           # tree_walk.py — tokens_hash/types_hash identity signals  [KEPT substrate]
  pipelines/
    indexing/     # cocoindex_app.py, runner.update_index(), reader.read_all_chunks()  [KEPT]
  prompts/        # tree_update.txt, realize.txt (Loop B directive template)
                  # bootstrap_file.txt (per-file LLM prompt), bootstrap_org.txt (org-pass LLM prompt)
  cli/            # main.py — Typer app with init/watch/status/sync
  config.py       # LLM config (CODOC_PROVIDER, CODOC_MODEL, OPENAI_API_KEY, …)
```

### Data model key types

- **`Feature`** (`model/feature.py`): `{id, title, description, parent_id, retired, realized, created_at, updated_at}`. `id` is a stable short id (`f-xxxxxxxx`) rendered into `tree.codoc` as `⟨f-id⟩`. ONE prose field, `description`. `retired` and `realized` are the only lifecycle bits (no status taxonomy): `realized=False` marks an accepted `/codoc:plan` placeholder with no code yet; the first binding (ATTACH/REFRESH in `loop/apply._mutate`) flips it True. Exposed in the sidecar's `features{}` but never written into `tree.codoc` text.
- **`Binding`** (`model/binding.py`): `{id, feature_id, file, symbol_path, fingerprint, updated_at}`. The anchor is inlined as `(file, symbol_path)` — the index join key. `fingerprint` is the chunk's `tokens_hash`.
- **`NodeOp`** (`model/event.py`): `{kind, feature_id?, parent_id?, title?, description?, bindings, rationale}`.
- **`Event`** (`model/event.py`): `{id, at, source, op, applied, accepted_at}`. A *proposal* is an Event with `applied=False`; accepting flips it and runs the op.
- **`HLC`** (`model/hlc.py`): Hybrid Logical Clock; `to_str()` is lexicographically sortable. Used as the monotonic `created_at`/`updated_at`/`at` clock. [KEPT]

### NodeOp kinds

Safe (auto-applied): `ATTACH DETACH REFRESH AMEND` (AMEND only when the edit is small — `AMEND_SAFE_RATIO`, the sole threshold).
Structural (accepted/rejected via `.codoc/inbox.json`): `ADD_NODE MOVE_NODE RETIRE_NODE`. **Rendering is an in-place overlay** (not a bottom diff block): ADD/MOVE emit a ghost hunk in `tree.codoc` text at the destination parent; RETIRE/AMEND emit NO text and instead ride in the sidecar `proposals` map so the IDE decorates the live node in place (strike for retire, inline title/desc diff for amend) — keeping the live node's text byte-identical to a clean render preserves the round-trip. `NodeOp` carries an optional `realized` (ADD_NODE realization; None ⇒ True).

### Storage schema

SQLite WAL at `.codoc/codoc.db`. **Three authoritative tables + one derived graph cache:**
- `features`, `bindings` (`UNIQUE(file, symbol_path)` — a chunk binds to at most one feature), `events` (append-only log; `applied=0` = pending proposal).
- `code_edges(src_file, src_symbol, dst_symbol, dst_name, dst_file, kind, internal)` — derived from `references_in_chunk`; safe to DROP and rebuild. PK `(src_symbol, dst_name, kind)`. `internal=1` edges are used for graph traversal; `internal=0` (external) are stored for display.

No transactions/constraints/obligations/binding_resolutions/citations tables, no JSONL audit lane.

The chunk index is owned by **cocoindex** and lives outside `codoc.db`: AST chunks + embeddings + identity hashes (tokens_hash / types_hash) are written to `.codoc/lancedb/code_chunks.lance` (LanceDB, embedded). Cocoindex's own memoization state lives in `.codoc/cocoindex.db/`. Together these provide durable, incremental, crash-resumable indexing — a killed `codoc init` resumes from the last completed file rather than re-embedding from scratch.

### Indexing layer (cocoindex + LanceDB)

`codoc/pipelines/indexing/` owns the chunk + embedding substrate. `update_index(root_dir, codoc_dir)` runs the cocoindex App once: walks the repo, parses each supported file via the existing tree-sitter adapters, embeds each AST chunk via sentence-transformers, and upserts to LanceDB. Memoized per-file: unchanged files cost nothing. Killed mid-run, the next call resumes from the last completed component.

Bootstrap and both loops call `update_index` first, then read from LanceDB via `read_all_chunks(codoc_dir)`. LanceDB rows carry `tokens_hash` (fingerprint) and `types_hash` (AST-shape identity), both actively used for move/rename detection in Loop A.

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

`run_loop_b` first drains `.codoc/inbox.json` (proposal verdicts written by the IDE's Accept/Reject actions: accept → `apply_op` + delete event; reject → delete event), then parses `tree.codoc` and diffs against the store (`codoc_file/diff.py`) into user ops (verdicts no longer come from the text), applies them (user edits are intentional → applied immediately), and builds a directive from each code-implying op's `description` + bound symbols (`prompts/realize.txt`). Instead of spawning a headless agent, it **hands the work to the live session**: it writes the assembled directives to `.codoc/realize.md` and sets `status.json` = `awaiting_impl`. The user's interactive Claude Code session (nudged by the `UserPromptSubmit` hook) runs `/codoc:realize` — read the file → implement each directive → `codoc_reflect` to bind → delete `.codoc/realize.md`. The loop is then closed by the existing Stop-hook reflection (`agent/hook._maybe_spawn_reflect`) or the watch daemon's epoch-close Loop A pass. `--dry`/`--no-realize` skip the queue write.

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

- **`.codoc/tree.codoc`** — human-authored feature tree. `- Title  ⟨f-id⟩` (id hidden by the IDE decoration; minted on save for hand-added nodes). Descriptions are free prose and may span multiple paragraphs — blank lines are *kept* (a node ends only at the next feature-marker line / the pending sentinel / EOF, never at a blank). Code is cited inline as `[label](codoc:file.py#symbol)` markdown links; `parse.extract_refs` pulls them out. **No `↪ refs:` line** — derived bindings are not printed into the text; they ride in the sidecar and the IDE renders them as inlay-hint chips. Pending proposals render as an **in-place overlay**: ADD/MOVE emit a ghost hunk (`+`/`~` op char in col 0, node at its tree depth, hidden `⟨e-id⟩`) at the destination parent; RETIRE/AMEND emit no text (they're carried in the sidecar). The parser skips any line matching both the proposal shape and a `⟨e-id⟩` marker, so render→parse→diff stays a no-op. (The legacy `# ── pending changes` sentinel is still honored on read.)
- **`.codoc/tree.bindings.json`** — machine-readable sidecar for the IDE (now **version 3**). Schema: `{version, by_feature{fid:[{file,symbol}]}, by_file{file:[{symbol,feature_id,feature_title}]}, features{fid:{title,parent_id,realized}}, feature_edges{}, proposals{by_feature{fid:{op,event_id,tag,…}}, by_event{eid:{op,…}}}}`. `proposals.by_feature` drives the in-place retire/amend overlays + Accept/Reject on the live node; `realized` drives the unrealized-placeholder decoration. Written atomically (tmp → rename).
- **`.codoc/status.json`** (written by the loops, not `write_tree`) — `{version, state, pending, detail, at}`; `state ∈ {in_sync, code_drift, tree_dirty, awaiting_impl, realizing}` drives the IDE status bar + the tree.codoc header CodeLens. `awaiting_impl` means Loop B queued code-implying tree edits in `.codoc/realize.md` for the live session (`pending` = directive count).
- **`.codoc/realize.md`** (written by Loop B) — the realization queue: the assembled directive prompt the live session implements via `/codoc:realize`, then deletes. Replaces the old headless `claude -p` spawn.
- **`.codoc/inbox.json`** (written by the IDE) — `{version, verdicts:[{event_id, accept}]}`; drained by Loop B / `codoc sync`, then cleared. The watch daemon watches it so an Accept/Reject wakes the loop.

### VSCode extension (`vscode-codoc/`)

File-based; no HTTP server, no port. `WorkspaceState` watches `**/.codoc/{tree.codoc, tree.bindings.json, status.json, inbox.json}`; reparses on change; fires `onDidChange`. Status bar follows `status.json`: `$(loading~spin) implementing…` (realizing) | `$(pencil) applying tree edits…` (tree_dirty) | `$(play) N to implement` (awaiting_impl) | `$(bell) N proposals` (code_drift) | `$(check) N` (in_sync) | `$(sync) not initialized`.

**Proposals are a single inline surface.** Two viewers, no separate proposal UI:
the **`tree.codoc` raw-text editor** keeps the `+`/`~` ghost hunks + decorations +
CodeLens + lightbulb; the **`Codoc Tree` webview** (`tree-editor.ts`, the default
editor for `tree.codoc`) renders **every** proposal type inline — ADD/MOVE as ghost
rows in the tree pane, RETIRE as a strike on the live row, AMEND as a word-level
inline diff *inside the description* — each with inline `✓`/`✗` Accept/Reject, plus
toolbar Accept-all / Reject-all. There is **no** Explorer "codoc Features" sidebar
(removed) and **no** detail-pane proposal panel (removed).

Key source files:
- `src/state/workspace-state.ts` — root detection, reload, status bar; `writeVerdict()` appends to `inbox.json`
- `src/state/tree-model.ts` — TypeScript port of `parse.py` (parity-tested); multi-paragraph descriptions, hidden ids, harvests proposal hunks + inline refs
- `src/state/bindings-model.ts` — sidecar types + `entriesForFile` / `bindingsForFeature`
- `src/providers/decoration.ts` — hides `⟨…⟩` ids (`display:none`), colours diff hunks, strikes retired nodes
- `src/providers/inlay.ts` — derived-binding chips at the end of each title line (from the sidecar)
- `src/providers/codoc-tree-lens.ts` — tree.codoc header status + per-proposal Accept/Reject (+ Accept/Reject all)
- `src/providers/code-actions.ts` — lightbulb Accept/Reject on a proposal hunk (recovers `⟨e-id⟩`)
- `src/providers/completion.ts` — `[`-triggered autocomplete inserting `[label](codoc:file#symbol)`
- `src/providers/doc-links.ts` — makes `[..](codoc:file#symbol)` clickable via the `codoc.openRef` command
- `src/providers/code-lens.ts` — source-file CodeLens (which feature owns a symbol), reads `sidecar.by_file`
- `src/providers/{folding,symbol,feature-lines}.ts` — outline / fold / nav helpers (the Explorer-sidebar `feature-tree-view.ts` was removed)
- `src/providers/tree-editor.ts` — the `Codoc Tree` webview (default editor for `tree.codoc`): outline + detail pane; renders all proposals inline (ghost rows / strike / inline desc diff) with inline + toolbar Accept/Reject
- `src/extension.ts` — activates `WorkspaceState`, registers providers + commands (`codoc.open/sync/openRef`, `codoc.{accept,reject}Proposal`, `codoc.{accept,reject}All`, fold/expand)

The pre-rewrite HTTP-era providers (`state/server.ts`, `live-activity.ts`, `sync-on-save.ts`, old `codelens.ts`/`hover.ts`/`definition.ts`, `api/client.ts`) were **deleted** in the format redesign.

### Status / next

Two-loop system fully implemented and tested (Python unit + BDD scenario suites pass; TypeScript `tsc --noEmit` + esbuild clean; the TS parser is parity-tested against `parse.py` on the real 28-feature `test/requests` tree). The cocoindex/real-LLM integration tests are gated to skip when no `OPENAI_API_KEY` is set / the embedding model can't load.

**Format redesign (2026-05-25):** `↪ refs:`/`›` removed (inline `[label](codoc:file#symbol)` markdown links + sidecar inlay chips instead); `⟨f-id⟩` hidden by an IDE decoration (still on disk for stable identity); `?`→`+`/`-` accept/reject syntax removed (proposals render as a diff block, verdicts flow through `.codoc/inbox.json` via IDE Accept/Reject); descriptions now support multi-paragraph prose (blank lines preserved); lifecycle surfaced via `.codoc/status.json`.

**Workflow overhaul (2026-05-26):** two cohesive loops + honest diff view + agent-driven reflection.
- **In-place overlay rendering** — RETIRE/AMEND no longer render as separate ghost lines (the "duplicate deletion node" confusion); they decorate the live node via the sidecar `proposals` map (strike / inline diff), with Accept/Reject on the node. Only ADD/MOVE remain as text ghosts. (`codoc_file/render.py::_proposals_map`; VS Code `decoration.ts` retireStrike/amendInline, `codoc-tree-lens.ts`, `code-actions.ts`.)
- **codoc MCP server** (`codoc/mcp/`, FastMCP stdio, registered in `.mcp.json` by `install_hooks`) — the code-first loop's primary reflection path: the agent calls `codoc_reflect`/`codoc_propose_*`/`codoc_attach` (carrying real intent) instead of relying on Loop A's blind index-diff. All tools route through `apply_op`. Loop A is now a **verification net**: `loop_a._pending_coverage` dedups so the agent's proposals + automatic Loop A never double-propose (and Loop A skips the LLM entirely when the agent covered everything).
- **`realized` lifecycle + `/codoc:plan`** — `/codoc:plan <task>` (command at `.claude/commands/codoc/plan.md`) proposes plan nodes via `codoc_plan_add` (source=plan, realized=False); accepted, they're unrealized placeholders that flip realized when code binds; unplanned work surfaces as new proposals. SKILL.md rewritten MCP-first. `codoc propose` CLI / `propose.py` kept for humans/tests (bind-string bug fixed: symbol_path keeps the full `file::symbol`).
- **Watch daemon**: a `tree.codoc` write during an open epoch (agent MCP reflection) is no longer routed to Loop B (`watch.process_batch` step 3 suppresses it; epoch-close Loop A reconciles).
- Tests: 211 Python pass; VS Code `tsc`/esbuild clean + a new `vitest` harness (`vscode-codoc/src/test/`, 6 tests) guarding Python↔TS overlay parity.

**Proposal-surface unification + live-session realize (2026-05-29):** collapsed the
multiple proposal surfaces onto one inline model and removed the headless coding agent.
- **One inline proposal surface in the webview** — `tree-editor.ts` now renders ADD/MOVE
  as ghost rows in the tree pane, RETIRE as a strike on the live row, and AMEND as a
  word-level inline diff *inside the description*; inline `✓`/`✗` on every row + toolbar
  Accept-all / Reject-all. The separate detail-pane "PROPOSED DESCRIPTION" block and the
  Accept/Reject panel were deleted (`buildPayload` injects `proposals.by_event` ghosts +
  `pendingEventIds`; verdict messages carry `eventIds[]`). Raw-text editor keeps the
  `+`/`~` ghosts + decorations + CodeLens + lightbulb unchanged.
- **No headless `claude -p`** — Loop B writes code-implying directives to `.codoc/realize.md`
  and sets status `awaiting_impl`; the live session implements them via the new
  `/codoc:realize` command (nudged by a `UserPromptSubmit` hook). `_spawn_claude` /
  `_files_modified_since` / the `spawn=`/`refine=` params and `LoopBResult.{spawned,
  files_written,refinement}` were removed; `run_loop_b(root, codoc_dir, *, dry_run)` now.
- **Removed** the Explorer "codoc Features" sidebar (`feature-tree-view.ts` deleted,
  `codoc.featureTree` view + `refreshFeatureTree` command/menus gone; `navigateToFeature`
  kept for source-file CodeLens) and the dead legacy `codoc-plugin/` directory (HTTP hooks
  to the deleted `localhost:8001` + stale `/codoc-accept|reject|status|proposals`).

**Loop-audit fixes landed + BDD/E2E round-trip harness (2026-05-30):** the five
audit workstreams (Loop-B imperative gate, `realized=False` plan default +
proposal GC, status-on-init/sync + honest summaries + `reconcile_drift` +
`codoc accept/reject`, state-based reconciliation as authority with `types_hash`
on bindings, ADD-proposal render parity) are all committed on `transactions`. Code
tidy: `store/db.py` audit stamp moved off the deprecated `datetime.utcnow()`.

A new **BDD scenario suite** (`tests/bdd/`) makes the code↔tree round-trip
assertable as Given/When/Then userflows:
- `world.py` — a dependency-free harness wrapping a real repo dir + `.codoc` store,
  driving Loop A through `apply_changeset` with an *injected* `propose` (the single
  LLM pass) so placement is deterministic, and Loop B through real `tree.codoc`
  edits + `inbox.json` verdicts. Every verb narrates a Given/When/Then transcript.
- `test_code_to_codoc_position.py` — added code attaches to the owning feature or
  is proposed under the right parent; modify→refresh; small vs. large description
  amend; delete→detach+retire; move/rename carry attribution to the new position
  with no duplicate node; same-title re-proposal binds into the existing node;
  placeholder adoption flips `realized`.
- `test_partial_verdicts.py` — accept-some/reject-some across a batch of proposals:
  only accepted nodes land (in position), rejected vanish, the store converges to
  `in_sync`, and only accepted *imperative* edits queue a realize directive.
- `test_dependencies.py` — the code graph drives placement (a new caller lands with
  the feature it calls; strongest dependency wins; import edges count) and impact
  (`LoopAResult.impacted` flags upstream dependents of a changed symbol).
- `e2e_report.py` + `test_e2e_userflows.py` — the **non-deterministic** real-LLM
  counterpart: bootstraps a tiny repo with the real index + LLM, walks add → modify
  → dependency-add → rename → delete, and prints a position report (which feature
  owns each change, under which parent) for **manual inspection**, asserting only
  LLM-agnostic invariants (nothing dropped, no duplicate titles, modify refreshes,
  delete detaches). Runs in a subprocess (cocoindex's index is a per-process
  singleton). Run it standalone with `python -m tests.bdd.e2e_report`.

Possible next steps: reconcile authored inline refs into authoritative bindings (currently navigable + round-trip-safe, but not yet fed back as `attach` ops); ego-graph context for Loop A subtree selection; may-impact propagation in the LLM prompt.

## Tests

- `tests/` — Python unit + integration suites (`tests/loop/`, `tests/store/`,
  `tests/graph/`, `tests/codoc_file/`, `tests/agent/`, `tests/mcp/`, `tests/cli/`).
- `tests/bdd/` — Given/When/Then userflows for the code↔tree round-trip (see the
  2026-05-30 status note): deterministic Loop A/B scenarios via an injected
  `propose`, plus a subprocess-isolated real-LLM E2E that prints a position report
  for manual inspection.
- `tests/loop/test_end_to_end.py` and `tests/bdd/test_e2e_userflows.py` are gated
  on `OPENAI_API_KEY` (real index + real LLM); everything else is deterministic.

Code fixtures: `test/draco/` (small Python), `test/requests/` (real-world Python library), `test/mosaic/` (TypeScript), `test/small_python_repo/` (toy Python), `test/altair/`, `test/gofish-python/`, `test/nanochat/` (additional Python codebases).
