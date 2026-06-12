# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

**codoc** — a system that maintains a human-intent-level view of a codebase as a navigable feature tree, synchronized to the underlying code. Each node is a *feature*: a named unit of intent that binds to many code chunks across many files; a single file's chunks may belong to several features. The tree is first-class authored intent (not LLM-derived), and code attribution is a secondary index updated by the reflective pipeline.

This repo contains the Python core (`codoc/`) and the VS Code extension (`vscode-codoc/`).

## Commands

```bash
# Install (Python 3.11+ required; use pip or uv)
pip install -e .

# CLI — five core commands
codoc init                # index repo, propose initial tree, write .codoc/tree.codoc
codoc watch               # the daemon: run both loops as you edit code / tree.codoc
codoc status              # feature count, pending proposals, recent activity
codoc sync                # one-shot escape hatch: apply tree edits (Loop B), then reflect code (Loop A)

# plumbing commands (agents / no-IDE workflows)
codoc accept <e-id>       # CLI verdict path — mirrors the IDE Accept (then runs Loop B)
codoc reject <e-id>       # CLI verdict path — mirrors the IDE Reject
codoc reflect             # recovery-grade state reconciliation (used by the Stop hook)
codoc propose <kind>      # author a plan proposal from the shell (humans/tests)
codoc install-hooks       # (re)install the CC hooks + MCP registration

# watch flags
codoc watch --dry           # reflect + apply tree edits, but don't queue realization directives
codoc watch --no-realize    # sync the tree but never queue directives for the session
codoc watch --auto-realize  # unattended fallback: implement queued tree edits when no interactive
                            #   session is open — prefers the Claude Agent SDK engine
                            #   (loop/sdk_realize.py) when `codoc[sdk]` is installed, else a
                            #   headless `claude -p /codoc:sync`

codoc realize               # implement the queue NOW, foreground: SDK engine streams one compact
                            #   line per agent action (edit/read/reflect/fetch) and mirrors each
                            #   into .codoc/activity.json so the IDE shows live signals
                            #   (--engine auto|sdk|cli, --permission-mode)

# Tests (run with Python 3.11+; the project venv is .venv)
python3.11 -m pytest tests/
```

The only human surface is `.codoc/tree.codoc`. You edit titles/descriptions
directly; structural proposals render as an in-place overlay (ADD/MOVE as ghost
hunks at their destination, RETIRE/AMEND as decorations on the live node) and
are accepted/rejected with the IDE's inline **Accept / Reject** actions (which
write verdicts to `.codoc/inbox.json` — there is no accept/reject *syntax* to
type). Feature ids (`⟨f-id⟩`) stay on disk for stable identity but the IDE hides
them. Code is cited inline with markdown links: `[label](codoc:file.py#symbol)`.

Three further markdown-native signals in descriptions (2026-06-12):
- `> …` blockquote lines are **steering comments** — notes addressed to the
  agent, not prose. Loop B drains each into a `STEER FEATURE` realize directive
  (always imperative; appended to an in-flight queue, so you can steer
  mid-generation) and the next render consumes it from the text. The raw editor
  ghost-inks them with a `→ for agent` cue.
- `**bold**` is **focus**: newly-bolded spans ride into directives as a `Focus:`
  line, and a newly-bolded span that itself reads imperative queues a directive
  even when the description as a whole is descriptive.
- `[label](https://…)` external links become `Consult:` lines — the realizing
  agent WebFetches them before implementing.

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
  `awaiting_impl`). The session implements it via `/codoc:sync`; the existing
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
                  #   classify.py    — THE decision table (see docs/codoc-change-ledger.md): every change,
                  #                    either side, any actor → one explicit reaction. is_imperative/implies_code
                  #                    (rows 7/8) + suppressed_by_hold (row 13, doc-wins)
                  #   edits.py       — .codoc/edits.json provenance channel (host annotations drained by Loop B +
                  #                    host-owned intents) and .codoc/realize.json directive manifest; hold_set()
                  #   inbox.py       — .codoc/inbox.json verdict channel (IDE Accept/Reject → loops)
                  #   fsio.py        — shared control-file IO: atomic writes (tmp→rename) + tolerant
                  #                    JSON reads (missing/corrupt → default, logged) for every .codoc file
                  #   status.py      — .codoc/status.json lifecycle (in_sync/code_drift/tree_dirty/awaiting_impl/realizing)
                  #   diff.py        — compute_changeset; ChunkRef carries tokens_hash + types_hash; scoped LanceDB
                  #                    reads (files= pushdown, embeddings never read in the loops)
                  #   apply.py       — derive_auto_ops, apply_op (stamps actor/mode/caused_by), AMEND_SAFE_RATIO
                  #   subtree.py     — select_relevant_subtree (file-locality seeds; accepts the pass's feature list)
                  #   loop_a.py      — run_loop_a / apply_changeset (code → codoc);
                  #                    _detect_relocations (move via tokens_hash, rename via types_hash);
                  #                    _cover_uncovered_adds (coverage net: neighbor_feature or ADD_NODE proposal);
                  #                    held features (doc-wins) + caused_by stamping from the realize manifest
                  #   loop_b.py      — run_loop_b (codoc → code; stamp user ops from edits.json annotations, mint
                  #                    ⟨d-…⟩ directive ids, queue .codoc/realize.md + realize.json for the session;
                  #                    drain `> …` steering comments → STEER directives, bold-amplified imperative
                  #                    gate, Focus:/Consult: signal lines, append-to-queue via manifest texts)
                  #   sdk_realize.py — Claude Agent SDK realize engine: run /codoc:sync via query(),
                  #                    RealizeMonitor maps streamed tool events → compact terminal lines +
                  #                    activity.json signals (editing/reflecting); `python -m` runnable
                  #   autorealize.py — unattended spawn: should_spawn + spawn_realize(engine=auto|sdk|cli)
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
- **`Event`** (`model/event.py`): `{id, at, source, op, applied, accepted_at, actor, mode, caused_by}`. A *proposal* is an Event with `applied=False`; accepting flips it and runs the op. The last three fields are the **change ledger** (see `docs/codoc-change-ledger.md`): `actor` ∈ human | agent id | loop; `mode` ∈ pen | suggest | auto; `caused_by` = the directive (`d-…`) / event / suggestion id this change implements. A model validator infers actor/mode from `source` when absent, so legacy rows and direct constructions are always stamped.
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

Bootstrap and both loops call `update_index` first, then read from LanceDB via `read_all_chunks(codoc_dir, *, files=None, with_embeddings=True, with_source=True)`. LanceDB rows carry `tokens_hash` (fingerprint) and `types_hash` (AST-shape identity), both actively used for move/rename detection in Loop A. **The loops never read embeddings** (`with_embeddings=False`) and push their file scope down as a LanceDB `file IN (…)` predicate; a scoped `compute_changeset` reads only the touched files' rows plus one source-less/embedding-less full projection for the graph's symbol table. Only bootstrap reads embeddings.

### Loop A in detail (code → codoc)

`compute_changeset` (`loop/diff.py`) reads the index, runs `update_index`, reads again, and keys both snapshots by `(file, symbol_path)` comparing `tokens_hash` → `ChangeSet{added, removed, modified}`. `ChunkRef` carries both `fingerprint` (= `tokens_hash`, move-invariant) and `types_hash` (AST-shape identity, rename-invariant).

`apply_changeset` has five phases:

1. **Auto-ops**: `derive_auto_ops` resolves trivially-safe changes (modified-bound → REFRESH, removed-bound → DETACH) with no LLM.
2. **Correspondence**: `_detect_relocations` pairs removed↔added chunks that are the same code relocated — a *move* (identical `tokens_hash`) or a *rename* (same-file unique `types_hash`, 1:1 only). Each match emits a deterministic ATTACH to the removed chunk's feature — **no LLM, no risk of dropped attribution**.
3. **LLM pass**: runs if there are still-unbound additions, a feature lost its last binding, OR (when `amend_on_change=True`) an in-place `modified` chunk belongs to a realized feature with prose whose description may now be stale — ONE `propose_tree_update` call with the change set, the file-locality seed subtree, **every node title** (de-dup context), and optional graph context. Safe ops apply immediately; structural ops become `applied=False` proposal Events. **`apply_changeset` takes two authority flags set by the caller, not the op:** `allow_retire` (drop LLM-proposed RETIRE ops when False) and `amend_on_change` (broaden the trigger to in-place modifications). `run_loop_a` (the twitchy temporal index diff) passes both `False`; `reconcile_drift` (the authoritative full-state pass — the production daemon path) passes both `True`. So a RETIRE is only ever surfaced from the authoritative state view, never a mid-edit diff.
4. **May-impact** (observability): `_compute_impacted` surfaces upstream dependent features of changed symbols for the LLM prompt.
5. **Coverage net**: `_cover_uncovered_adds` ensures no added chunk is silently dropped — it attaches to the `neighbor_feature` (graph-neighbor feature owning the most call/import edges to the new symbol) or, failing that, surfaces a pending ADD_NODE proposal.

**Stale-proposal GC** (`_gc_superseded_proposals`, run first each pass): drops a pending `ADD_NODE` whose chunks are all bound elsewhere, AND a pending `RETIRE_NODE` whose feature still owns code *not* being removed by the current change set (`removed_keys`). A retire is only ever raised when a feature lost its last binding; once code rebinds (a mid-implementation lull, or the agent reflected it in) the proposal is a false positive and is cleared — so a no-op `codoc sync` converges to `in_sync` and a transiently-empty feature is never wrongly retired. The `emptied` retire-candidate set also excludes unrealized (`realized=False`) plan placeholders.

**Doc-wins holds + causality (classify rows 13/6).** Both entrypoints read `_doc_intent(codoc_dir)` → (`held`, `caused_by_map`, `default_caused_by`) from `edits.py`. `held` (live doc-ahead intents ∪ queued realize directives) suppresses code-side AMEND/RETIRE/MOVE on those features — a held feature is excluded from `emptied` and the amend-on-change trigger, and LLM ops on it are dropped (`classify.suppressed_by_hold`, counted in `LoopAResult.held_back`). Binding maintenance (ATTACH/DETACH/REFRESH) is never suppressed — bindings are attribution, not intent. While `realize.json` exists, every op the pass applies is stamped `caused_by=⟨directive id⟩` (per-feature from the manifest, sole-directive fallback) so the IDE can group the surfaced-back changes under the doc edit that triggered them.

**One feature read per pass:** `apply_changeset` loads `store.list_features()` once and threads it through subtree selection, title dedup, and placeholder adoption; `store.bound_feature_ids()` (one `SELECT DISTINCT`) replaces per-feature binding lookups.

### Bootstrap in detail

`run_bootstrap` (thin shim in `bootstrap.py`) delegates to `bootstrap_hier_from_chunks` in `bootstrap_hier.py`. Two phases:

1. **Per-file pass**: one `propose_file_features` LLM call per file. Sees only that file's chunks + per-symbol call/contain edges → structurally impossible to create a cross-file junk drawer. Temp node ids ("n1", "t1") in `NodeOp.feature_id` are resolved by `_apply_ops_with_local_ids` to real ids before apply — enabling within-call nesting. `_ensure_file_coverage` folds any uncovered chunks into the file's largest node (same-file, never a junk drawer); mints one node only if the model returned nothing.
2. **Org pass** (`organize=True` by default): one `propose_organization` LLM call grouping existing file-features under 3–6 broad theme parents via ADD_NODE + MOVE_NODE. `_feature_coupling` computes feature→feature call/import coupling lines as context for this call.

`run_bootstrap` signature: `run_bootstrap(root_dir, codoc_dir, *, repo_name=None, config=None, do_index=True, organize=True)`. The old `max_per_call` parameter is gone.

### Loop B in detail (codoc → code)

`run_loop_b` first drains the `edits.json` **annotations** and snapshots the text diff (`codoc_file/diff.py`) **against the pre-mutation store** (step 0 — diffing after verdicts would read the stale text as a human edit and revert the accepted change), then drains `.codoc/inbox.json` (proposal verdicts written by the IDE's Accept/Reject actions: accept → `apply_op` + delete event; reject → delete event), applies the snapshotted user ops (user edits are intentional → applied immediately) **stamped with the annotating actor/mode (default human/pen)**, then drains live **payload intents** (doc-ahead suggestions — applied as user ops `mode=suggest`/`caused_by=` suggestion id; the agent-side "apply"), **re-renders `tree.codoc`** when the pass mutated the store, and builds a directive from each code-implying op's `description` + bound symbols (`prompts/realize.txt`). Each queued directive gets a minted `d-…` id, embedded as `⟨d-id⟩` in its `### N.` heading and recorded in the **`.codoc/realize.json` manifest** (`{id, feature_id, kind, caused_by, text}` — `caused_by` = the applying settle's suggestion id, else the user-op event id; `kind` may also be `"steer"`; `text` = the rendered directive body for append-not-clobber rebuilds); the implementing agent passes the id back via `codoc_reflect(caused_by=…)`. **RETIRE is path-asymmetric:** accepting an auto-raised RETIRE *from the inbox* is **detach-only** — it marks the feature retired and detaches its bindings (so the code isn't orphaned under a now-hidden feature) but NEVER queues a code-removal directive (a Loop-A retire off transient drift could be a false positive; deleting code on accept is the most destructive failure mode). Only a human `~` retire *in the text* (a `user_op`) keeps its bindings and queues a `RETIRE FEATURE` removal directive. Instead of spawning a headless agent, it **hands the work to the live session**: it writes the assembled directives to `.codoc/realize.md` and sets `status.json` = `awaiting_impl`. The user's interactive Claude Code session (nudged by the `UserPromptSubmit` hook) runs `/codoc:sync` — read the file → implement each directive → `codoc_reflect` to bind → delete `.codoc/realize.md`. The loop is then closed by the existing Stop-hook reflection (`agent/hook._maybe_spawn_reflect`) or the watch daemon's epoch-close Loop A pass. `--dry`/`--no-realize` skip the queue write.

**Steering / emphasis / links (2026-06-12).** Step 2.7 drains inline `> …` **steering comments** (parsed per-node into `ParsedNode.comments`, excluded from the prose, surfaced by `diff.py` as `CodocDiff.comments`): each becomes a `STEER FEATURE` directive (`build_steer_directive` — the note wins over the description where they conflict) and the end-of-pass re-render consumes it from the text (a comment-only pass re-renders too). The imperative gate is **bold-amplified**: `CodocDiff.emphasis` carries each AMEND's newly-bolded spans (new bold minus old bold), and an imperative bolded span queues a directive even when the whole description reads descriptive. `build_directive` appends `Focus:` (bolded spans) and `Consult:` (external `https://` links) lines; `prompts/realize.txt` + `/codoc:sync` tell the agent to prioritize Focus phrases and WebFetch Consult links. The queue **appends instead of clobbering**: `realize.json` directives now carry their rendered `text`, and step 3 rebuilds `realize.md` from existing-manifest + new directives — so a steering comment lands while a realization is in flight (`/codoc:sync` re-reads the queue after each directive).

**Realization trigger + fallbacks.** The primary path is *in-session*: `/codoc:plan` proposes nodes then calls the **blocking** `codoc_await_verdicts(event_ids)` MCP tool (modeled on plannotator's blocking review hook) — it polls `inbox.json`, applies each verdict as it lands (recovering an ADD's freshly-minted feature id by diffing the feature set), marks accepted nodes `phase=editing`, and returns so the *same turn* implements + binds. No daemon, no idle gap. Two fallbacks reuse `realize.md` for when no session is waiting on that tool: (1) the `UserPromptSubmit` hook (`agent/hook._drain_inbox_fallback`) drains the inbox via Loop B when **no `codoc watch` daemon** owns the repo, so a plan accepted with no daemon still queues `realize.md` on the next prompt; (2) `codoc watch --auto-realize` (`loop/autorealize.py`: `should_spawn`/`spawn_realize(engine=…)`, driven by `watch.maybe_auto_realize`) spawns an unattended pass when a queue exists and no interactive epoch is open — the only path that lands code with nobody at the keyboard. Two engines: **`loop/sdk_realize.py`** (preferred when `codoc[sdk]`/claude-agent-sdk is installed; also runnable foreground via `codoc realize`) runs `/codoc:sync` through `claude_agent_sdk.query()` with `setting_sources=["user","project","local"]` (so the repo's hooks/MCP/commands load) and reacts to each streamed tool event synchronously — one compact terminal line per action (`● edit`/`◦ read`/`⊙ reflect`/`⇣ fetch`, dim-ANSI on tty only, summary line at the end) and codoc-side signals via the SAME `agent/hook._handle_tool` path the interactive hooks use (`activity.json` `touched` + phase `editing`), plus marking `reflecting` on `mcp__codoc__*` calls — the writer the doc-pane hollow-dot decoration was waiting for (`RealizeMonitor` is duck-typed/SDK-free for tests); or the original blind **`claude -p /codoc:sync`**. The unified `/codoc:sync` command reads `status.json` and dispatches direction (awaiting_impl/tree_dirty → realize, code_drift → reflect).

### Environment variables

| Var | Default | Description |
|---|---|---|
| `CODOC_PROVIDER` | `openai` | LLM provider (`openai` or `ollama`) |
| `CODOC_MODEL` | `gpt-5.4-mini` | LLM model name |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `CODOC_BASE_URL` | — | Custom OpenAI-compatible base URL |
| `CODOC_TEMPERATURE` | `0.2` | LLM sampling temperature |
| `CODOC_MAX_TOKENS` | `16000` | LLM completion budget (reasoning models spend it on hidden reasoning too) |
| `CODOC_EMBEDDER_PROVIDER` | `sentence-transformers` | Embedder provider (used by dedup / proposal similarity; chunk embeddings live in cocoindex) |
| `CODOC_EMBEDDER_MODEL` | `all-MiniLM-L6-v2` | Embedder model |
| `COCOINDEX_DB` | `.codoc/cocoindex.db` | Path to cocoindex's internal memoization state (auto-set by `update_index`) |
| `CODOC_LANCE_PATH` | `.codoc/lancedb` | Path to the LanceDB directory holding the `code_chunks` table |
| `CODOC_LOG_PROMPTS` | — | Set to `1` to log LLM prompt+response to stderr |
| `CODOC_EPOCH_ORIGIN` | `interactive` | Set to `loop_b` to mark an agent-owned epoch (hooks) |
| `CODOC_NO_STOP_REFLECT` | — | Set to disable the Stop-hook recovery reflection |

### Render + sidecar

`codoc_file/render.py` writes two files on every `write_tree` call (the sidecar via the public `write_sidecar`). The sidecar is **pure derived state** and is also written *independently* by `reconcile.safe_write_tree` on every pass — even when the `tree.codoc` *text* render is held back to preserve an un-absorbed human edit — so applied verdicts / new bindings / proposal changes surface in the IDE immediately (an accept/reject is never a dead click):

- **`.codoc/tree.codoc`** — human-authored feature tree. `- Title  ⟨f-id⟩` (id hidden by the IDE decoration; minted on save for hand-added nodes). Descriptions are free prose and may span multiple paragraphs — blank lines are *kept* (a node ends only at the next feature-marker line / the pending sentinel / EOF, never at a blank). Code is cited inline as `[label](codoc:file.py#symbol)` markdown links; `parse.extract_refs` pulls them out. **No `↪ refs:` line** — derived bindings are not printed into the text; they ride in the sidecar and the IDE renders them as inlay-hint chips. Pending proposals render as an **in-place overlay**: ADD/MOVE emit a ghost hunk (`+`/`~` op char in col 0, node at its tree depth, hidden `⟨e-id⟩`) at the destination parent; RETIRE/AMEND emit no text (they're carried in the sidecar). The parser skips any line matching both the proposal shape and a `⟨e-id⟩` marker, so render→parse→diff stays a no-op. (The legacy `# ── pending changes` sentinel is still honored on read.)
- **`.codoc/tree.bindings.json`** — machine-readable sidecar for the IDE (now **version 4**). Schema: `{version, by_feature{fid:[{file,symbol}]}, by_file{file:[{symbol,feature_id,feature_title}]}, features{fid:{title,parent_id,realized}}, feature_edges{}, proposals{by_feature{fid:{op,event_id,tag,actor,mode,caused_by,…}}, by_event{eid:{op,…}}}, changes:[{event_id,at,kind,feature_id,actor,mode,caused_by}], holds:[fid]}`. `proposals.by_feature` drives the in-place retire/amend overlays + Accept/Reject on the live node; `realized` drives the unrealized-placeholder decoration. v4's `changes` (last ~50 applied events, newest first) drives the agent-pencil re-stamp in the webview, `holds` is the doc-wins hold set, and proposal `caused_by` drives the `↳ from your edit` cascade cue. Written atomically (tmp → rename).
- **`.codoc/status.json`** (written by the loops, not `write_tree`) — `{version, state, pending, detail, at}`; `state ∈ {in_sync, code_drift, tree_dirty, awaiting_impl, realizing}` drives the IDE status bar + the tree.codoc header CodeLens. `awaiting_impl` means Loop B queued code-implying tree edits in `.codoc/realize.md` for the live session (`pending` = directive count). `refresh_status` treats a non-empty `realize.md` as a floor: if no proposals are pending it reports `awaiting_impl` rather than `in_sync`, so a later code-side pass can't clobber the state and orphan the queued directive (the file self-clears when `/codoc:sync` completes).
- **`.codoc/realize.md`** (written by Loop B) — the realization queue: the assembled directive prompt the live session implements via `/codoc:sync`, then deletes (together with `realize.json`). Each `### N.` heading carries its `⟨d-id⟩`. Replaces the old headless `claude -p` spawn.
- **`.codoc/realize.json`** (written by Loop B next to realize.md) — the machine-readable directive manifest `{version, directives:[{id, feature_id, kind, caused_by, text}]}`: feature ids feed the doc-wins hold set, ids feed `caused_by` tagging, and `text` (the rendered directive body) lets a later pass rebuild `realize.md` as old + new — append, never clobber. `kind` may also be `"steer"`. Stale (no realize.md beside it) ⇒ ignored + cleaned.
- **`.codoc/inbox.json`** (written by the IDE) — `{version, verdicts:[{event_id, accept}]}`; drained by Loop B / `codoc sync`, then cleared. The watch daemon watches it so an Accept/Reject wakes the loop.
- **`.codoc/edits.json`** (written by the IDE host; `loop/edits.py` + `src/state/edits-channel.ts`) — the provenance/intent channel: `edits` = per-feature authorship annotations for settles (written BEFORE the tree.codoc save; drained by Loop B, default human/pen), `intents` = the live doc-ahead suggestions (host-owned list; >7-day-old intents ignored; the other half of the hold set). An intent carries the suggested title/description as **payload**, and Loop B's **intent drain** applies it — the agent-side "apply" (classify row 9 → 7/8, `mode=suggest`, `caused_by=` suggestion id); satisfied intents (payload == store) are skipped so the read-only drain is idempotent. Watched by the daemon (a suggestion alone must wake Loop B).

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
  `/codoc:sync` command (nudged by a `UserPromptSubmit` hook). `_spawn_claude` /
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

**Unified change ledger (2026-06-10):** provenance + doc-wins conflicts + causality +
incrementality, in six landed phases (see `docs/codoc-change-ledger.md` — the citable
algorithm description):
- **Ledger schema** — `events` gains `actor`/`mode`/`caused_by` (additive migration);
  `Event` model validator infers actor/mode from `source` for legacy rows;
  `apply_op(actor=, mode=, caused_by=)`. New `loop/classify.py` holds THE 13-row
  decision table (absorbed `_is_imperative`/`_implies_code` from loop_b; row 13 =
  `suppressed_by_hold`).
- **Annotation channel** — `.codoc/edits.json` (host → loops: settle authorship +
  live doc-ahead intents) + `.codoc/realize.json` (directive manifest). Loop B stamps
  user ops from annotations and mints `⟨d-id⟩` directive ids; MCP tools accept
  `caused_by`/`actor`; `/codoc:sync` + `prompts/realize.txt` instruct the agent to
  echo the id and delete both queue files.
- **Doc always wins** — pending doc-ahead intent holds code-side AMEND/RETIRE/MOVE on
  that feature (bindings still maintained); holds release when the suggestion clears
  or the realize queue completes; 7-day staleness backstop. BDD: `tests/bdd/test_doc_wins.py`.
- **Incrementality** — LanceDB file-scope pushdown + no embeddings in the loops
  (`read_all_chunks(files=, with_embeddings=, with_source=)`); one feature-table read
  per Loop A pass (`bound_feature_ids()`); bootstrap title list maintained
  incrementally. Guard: `tests/test_reader.py`.
- **Sidecar v4 + minimal frontend** — `changes` feed + `holds` + proposal provenance;
  webview pencil-stamps agent-amended prose (`reconcileDoc(agentAmends)`,
  `agentAmendsByFeature`), `↳ from your edit` cascade cue on diff cards
  (`Suggestion.causedBy`), host writes `edits.json` annotations before each settle and
  mirrors intents from doc-ahead suggestions (`src/state/edits-channel.ts`). No new UI
  surfaces — color=direction, ink=authorship, text-only cascade cue.
- Tests: 310 Python (+ classify/edits/doc-wins/reader suites) + 127 vitest, tsc/esbuild clean.

**Verdict grammar + agent-applied suggestions (2026-06-10):** who resolves what is
now structural, with one rule — *Accept/Reject is exclusively a human verb on
code-ahead items; a doc-ahead suggestion is applied by the AI side and offers its
author only Withdraw.*
- **Loop B intent drain** — `edits.json` intents carry the suggested title/desc as
  payload (`loop/edits.py Intent.title/description`, mirrored in
  `edits-channel.ts intentsFromSuggestions`); Loop B step 2.5 applies live payload
  intents as user ops (`mode=suggest`, `caused_by=` suggestion id → row 7/8: a
  descriptive suggestion persists, an imperative one queues a directive). Satisfied
  intents are skipped (stateless idempotence); the daemon watches `edits.json`. The
  webview's Apply button, `suggest-apply` message, host `applySuggestion` +
  `editTitle`/`editDescription` were deleted; the doc-ahead card shows
  `→ for agent · Withdraw`. Withdraw works until the drain picks the intent up.
- **Loop B text↔store coherence fix** — accepted AMENDs were being silently
  reverted: the pass diffed the (necessarily stale) text against the already-
  mutated store and applied the old text back as a "user edit". Fixed by
  snapshotting the text diff BEFORE verdicts/intents (step 0) and re-rendering
  `tree.codoc` at end of any store-mutating pass. Regression test:
  `test_accepted_amend_survives_the_stale_text`.
- **Two quiet encodings on diff cards** (colour stays direction, shape stays kind):
  authorship = ink opacity (`by-human` full / `by-agent` pencil-faded until
  accepted); realization = texture (a plan proposal — `tag` contains "plan", code
  not yet real — gets a hollow `△` marker + dashed left border, rhyming with the
  unrealized dashed ring; landed-code reflections stay solid `▲`).
- **Agent activity in the doc pane** — `sync.phase` (hooks → `activity.json`
  features.phase) was computed but never rendered; a new
  `tiptap/activity-decorations.ts` extension + `setPhases` decorate the heading
  with a pulsing dot (`editing`) / hollow dot (`reflecting`), reduced-motion-gated
  explicitly (the `*` blanket doesn't match pseudo-elements). `reflecting` has no
  Python writer yet — supported, awaiting one.

**Command-surface consolidation (2026-06-10):** `/codoc:realize` was folded into
`/codoc:sync` (whose `awaiting_impl` branch now carries the full realize loop) —
the plugin ships exactly two commands, `/codoc:plan` and `/codoc:sync`. All nudges
(UserPromptSubmit hook, status detail strings, IDE tooltips, `--auto-realize`'s
headless `claude -p`) point at `/codoc:sync`; `install_hooks` deletes
previously-installed codoc commands the plugin no longer ships. The `codoc-intent`
SKILL.md was slimmed to tree-format + code-first reflection guidance (it no longer
duplicates the per-command loop walkthroughs). The `.codoc/realize.md` *queue file*
and the realize machinery are unchanged.

**Steering / emphasis / links + SDK realize engine (2026-06-12):** four
author-side signals + a second unattended engine, all markdown-native (no new
syntax to learn):
- **Inline steering comments** — `> …` blockquote lines in a description are
  notes TO THE AGENT: parsed per-node (`ParsedNode.comments`, excluded from
  prose; TS parser parity in `tree-model.ts`), drained by Loop B step 2.7 into
  `STEER FEATURE` directives (note wins over description), consumed from the
  text by the end-of-pass re-render. Raw editor ghost-inks them with a
  `→ for agent` cue (`decoration.ts steeringNote`). The realize queue now
  **appends** (manifest directives carry their rendered `text`) so steering
  works mid-realization; `/codoc:sync` re-reads the queue after each directive.
- **Bold = focus** — `CodocDiff.emphasis` (newly-bolded spans per AMEND) rides
  into directives as `Focus:` lines, and an imperative bolded span queues a
  directive even when the prose reads descriptive (bold-amplified gate).
- **External links** — `[label](https://…)` in descriptions become `Consult:`
  directive lines; realize.txt/sync.md instruct WebFetch-before-implement.
- **SDK realize engine** — `loop/sdk_realize.py` runs `/codoc:sync` via
  `claude_agent_sdk.query()` (optional dep `codoc[sdk]`): compact per-action
  terminal readout + every action mirrored into `activity.json` through the
  same `hook._handle_tool` path (and `reflecting` finally has its Python
  writer). `codoc realize` runs it foreground; `--auto-realize` prefers it via
  `spawn_realize(engine=auto)`. Both engines + the interactive path coexist.
- Tests: 327 Python + 26 BDD + 133 vitest; tsc/esbuild clean.

Possible next steps: reconcile authored inline refs into authoritative bindings (currently navigable + round-trip-safe, but not yet fed back as `attach` ops); may-impact propagation in the LLM prompt; an LLM imperative classifier behind `classify.is_imperative` if heuristic precision becomes limiting.

## Tests

- `tests/` — Python unit + integration suites (`tests/loop/`, `tests/store/`,
  `tests/graph/`, `tests/codoc_file/`, `tests/agent/`, `tests/mcp/`, `tests/cli/`).
- `tests/bdd/` — Given/When/Then userflows for the code↔tree round-trip (see the
  2026-05-30 status note): deterministic Loop A/B scenarios via an injected
  `propose`, plus a subprocess-isolated real-LLM E2E that prints a position report
  for manual inspection. `test_doc_wins.py` covers the holds/causality rows.
- Ledger suites: `tests/loop/test_classify.py` (the decision table, row by row),
  `tests/loop/test_edits.py` (annotation channel + manifest + hold_set),
  `tests/test_reader.py` (scoped LanceDB reads + the compute_changeset read contract).
- `tests/loop/test_end_to_end.py` and `tests/bdd/test_e2e_userflows.py` are gated
  on `OPENAI_API_KEY` (real index + real LLM); everything else is deterministic.
- vscode-codoc: `npx vitest run` from `vscode-codoc/` (incl. `edits-channel.test.ts`
  pinning the edits.json contract + pencil re-stamp + cascade cue).

Code fixtures: `tests/fixtures/` (self-contained Python + TypeScript samples used by the adapter tests — committed, so the suite passes on a fresh clone) and `test/` (real-world corpora for bootstrap/E2E runs: `requests/`, `altair/`, `nanochat/`, `small_python_repo/`).
