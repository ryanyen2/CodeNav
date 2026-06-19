# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project Overview

**codoc** maintains a human-intent-level view of a codebase as a navigable
*feature tree*, synchronized to the underlying code. Each node is a **feature**: a
named unit of intent that binds to many code chunks across many files (and one
file's chunks may belong to several features). The tree is **first-class authored
intent** (not LLM-derived); code attribution (bindings) is a secondary index kept
fresh by the reflective pipeline.

The repo has the Python core (`codoc/`) and the VS Code extension (`vscode-codoc/`).

> **History:** this repo was rewritten clean-slate (2026-05). The old
> reflective/planning/realize/health pipelines, the transaction/constraint model,
> the FastAPI server, and the 20-command CLI were deleted. The cocoindex+LanceDB
> index, the tree-sitter adapters, and `core/tree_walk.py` were kept as substrate.

## Commands

```bash
pip install -e .            # Python 3.11+ (pip or uv); project venv is .venv

# Five core CLI commands
codoc init                  # index repo, propose initial tree, write .codoc/tree.codoc
codoc watch                 # the daemon: run both loops as you edit code / tree.codoc
codoc status                # feature count, pending proposals, recent activity
codoc sync                  # one-shot: apply tree edits (Loop B), then reflect code (Loop A)

# Plumbing (agents / no-IDE workflows)
codoc accept <e-id>         # CLI verdict path — mirrors the IDE Accept (then runs Loop B)
codoc reject <e-id>         # CLI verdict path — mirrors the IDE Reject
codoc reflect               # recovery-grade state reconciliation (used by the Stop hook)
codoc propose <kind>        # author a plan proposal from the shell (humans/tests)
codoc install-hooks         # (re)install the CC hooks + MCP registration
codoc realize               # implement the realize queue NOW, foreground (SDK or CLI engine)

# watch flags: --dry (apply tree edits, don't queue realization), --no-realize
# (never queue this session), --auto-realize (unattended fallback when no session)

python3.11 -m pytest tests/   # Python tests
```

## The `tree.codoc` surface

The only human surface is `.codoc/tree.codoc`. You edit titles/descriptions
directly. Structural proposals render as an **in-place overlay** — ADD/MOVE as
ghost hunks at their destination, RETIRE/AMEND as decorations on the live node —
accepted/rejected with the IDE's inline **Accept / Reject** (which write verdicts
to `.codoc/inbox.json`; there is no accept/reject *syntax*). Feature ids
(`⟨f-id⟩`) stay on disk for stable identity but the IDE hides them. Code is cited
inline with markdown links: `[label](codoc:file.py#symbol)`.

Three markdown-native signals in descriptions feed Loop B directives:
- `> …` blockquote lines are **steering comments** — imperative notes to the
  agent (not prose), drained into `STEER FEATURE` directives and consumed on the
  next render. The webview authors them via select → comment bubble → composer.
- `**bold**` is **focus** — newly-bolded spans ride in as a `Focus:` line; an
  imperative bolded span queues a directive even when the prose reads descriptive.
- `[label](https://…)` external links become `Consult:` lines — the realizing
  agent WebFetches them before implementing.

## Architecture

### Core idea — two loops

- **Loop A — code → codoc** (`loop/loop_a.py`): snapshot-diff the index →
  auto-apply safe ops (refresh/attach/detach/small-amend) → if anything needs
  judgment, ONE LLM pass (`agent/tree_update.py`) returns minimal node ops;
  structural ops (add/move/retire) are logged as pending proposals.
- **Loop B — codoc → code** (`loop/loop_b.py`): parse `tree.codoc` edits → apply
  user edits + proposal verdicts → for edits implying code change, build a
  directive and **queue it for the live session** in `.codoc/realize.md` (status
  `awaiting_impl`). The session implements via `/codoc:sync`; the Stop-hook
  reflection / watch-daemon Loop A then closes the loop. No headless `claude -p`.

A single LLM pass with full change + whole-tree-title context (plus the
`UNIQUE(file, symbol_path)` binding constraint) is what prevents duplicate nodes —
no move/fracture/coalesce detectors, no post-hoc dedup gates.

### Package layout (`codoc/`)

```
model/       # Pydantic: Feature, Binding, Event/NodeOp/NodeOpKind, HLC; ids.py
store/       # db.py — Store over 3 SQLite tables + 1 derived graph cache (WAL)
graph/       # code dependency graph (derived, rebuildable): extract.py, query.py
loop/        # the two loops + pieces:
             #   classify.py — the decision table (docs/codoc-change-ledger.md):
             #     every change → one reaction; is_imperative/implies_code, holds
             #   diff.py — compute_changeset (ChunkRef: tokens_hash + types_hash)
             #   apply.py — derive_auto_ops, apply_op (stamps actor/mode/caused_by)
             #   loop_a.py / loop_b.py — the loops (see "in detail" below)
             #   edits.py — edits.json provenance/intent channel + realize.json manifest
             #   inbox.py — inbox.json verdict channel; status.py — status.json lifecycle
             #   fsio.py — atomic writes + tolerant JSON reads for every .codoc file
             #   subtree.py — select_relevant_subtree; bootstrap_hier.py — 2-phase bootstrap
             #   sdk_realize.py / autorealize.py — SDK realize engine + unattended spawn
             #   watch.py — run_watch / process_batch (debounced router + self-write guard)
agent/       # base.py, tree_update.py (the incremental LLM call), bootstrap_agent.py,
             # paths.py, hook.py / install_hooks.py, propose.py
mcp/         # codoc MCP server (FastMCP, stdio): tools.py + server.py (codoc-mcp script)
codoc_file/  # render.py (store → tree.codoc + sidecar), parse.py, diff.py (→ user ops)
lang/        # tree-sitter adapters: python.py + typescript.py  [KEPT]
core/        # tree_walk.py — tokens_hash/types_hash identity signals  [KEPT substrate]
pipelines/indexing/  # cocoindex_app.py, update_index(), read_all_chunks()  [KEPT]
prompts/     # tree_update.txt, realize.txt, bootstrap_file.txt, bootstrap_org.txt
cli/main.py  # Typer app; config.py — LLM config
```

### Data model

- **`Feature`** (`model/feature.py`): `{id, title, description, parent_id, retired,
  realized, created_at, updated_at}`. `id` = stable `f-xxxxxxxx`, rendered as
  `⟨f-id⟩`. ONE prose field. `retired`/`realized` are the only lifecycle bits:
  `realized=False` marks an accepted `/codoc:plan` placeholder with no code yet;
  the first binding flips it True.
- **`Binding`** (`model/binding.py`): `{id, feature_id, file, symbol_path,
  fingerprint, updated_at}`. Anchor `(file, symbol_path)` is the index join key;
  `fingerprint` = the chunk's `tokens_hash`.
- **`NodeOp`** (`model/event.py`): `{kind, feature_id?, parent_id?, title?,
  description?, bindings, rationale, realized?}`.
- **`Event`** (`model/event.py`): `{id, at, source, op, applied, accepted_at,
  actor, mode, caused_by}`. A *proposal* is an Event with `applied=False`. The last
  three are the **change ledger** (`docs/codoc-change-ledger.md`): `actor` ∈
  human | agent | loop; `mode` ∈ pen | suggest | auto; `caused_by` = the directive
  / event / suggestion id this change implements. A validator infers actor/mode
  from `source` when absent.
- **`HLC`** (`model/hlc.py`): Hybrid Logical Clock; monotonic, lexicographically
  sortable; the `created_at`/`updated_at`/`at` clock.

### NodeOp kinds

- Safe, auto-applied: `ATTACH DETACH REFRESH AMEND` (AMEND only when the edit is
  small — `AMEND_SAFE_RATIO`, the sole threshold).
- Structural, accepted/rejected via `inbox.json`: `ADD_NODE MOVE_NODE RETIRE_NODE`.
  **Rendering is an in-place overlay**: ADD/MOVE emit a ghost hunk at the
  destination parent; RETIRE/AMEND emit no text and ride in the sidecar
  `proposals` map (the IDE decorates the live node in place), keeping the live
  node byte-identical to a clean render so the round-trip stays a no-op.

### Storage

SQLite WAL at `.codoc/codoc.db` — **3 authoritative tables + 1 derived graph cache**:
`features`, `bindings` (`UNIQUE(file, symbol_path)`), `events` (append-only;
`applied=0` = pending), plus `code_edges` (derived from `references_in_chunk`,
safe to drop and rebuild). No transactions/constraints/obligations tables.

The chunk index is owned by **cocoindex**, outside `codoc.db`: AST chunks +
embeddings + identity hashes (tokens_hash / types_hash) in
`.codoc/lancedb/code_chunks.lance`; cocoindex memoization in `.codoc/cocoindex.db/`.
Indexing is durable, incremental, crash-resumable. `update_index(root, codoc_dir)`
runs the pipeline once (memoized per-file); the loops then read via
`read_all_chunks(...)`. **The loops never read embeddings** and push file scope
down as a LanceDB predicate; only bootstrap reads embeddings.

### Loop A in detail (code → codoc)

`compute_changeset` (`loop/diff.py`) diffs two index snapshots keyed by
`(file, symbol_path)` on `tokens_hash` → `ChangeSet{added, removed, modified}`.
`apply_changeset` has five phases: (1) **auto-ops** — modified-bound → REFRESH,
removed-bound → DETACH; (2) **correspondence** — `_detect_relocations` pairs
removed↔added that are the same code relocated (move via `tokens_hash`, rename via
same-file unique `types_hash`) → deterministic ATTACH, no dropped attribution;
(3) **LLM pass** — for unbound additions / a feature that lost its last binding /
a stale-prose modification, safe ops apply and structural ops become proposals;
(4) **may-impact** — surface upstream dependents for the prompt; (5) **coverage
net** — `_cover_uncovered_adds` attaches to the best `neighbor_feature` or surfaces
a pending ADD, so nothing is silently dropped.

Two caller-set authority flags gate the LLM pass: `allow_retire` and
`amend_on_change`. `run_loop_a` (mid-edit diff) passes both `False`;
`reconcile_drift` (authoritative full-state, the daemon path) both `True` — so a
RETIRE only ever surfaces from the authoritative view. **Stale-proposal GC** runs
first each pass (drops ADDs already bound elsewhere, RETIREs whose feature still
owns code), so a no-op `codoc sync` converges to `in_sync`. **Doc-wins holds:**
`held` features (live doc-ahead intents ∪ queued directives) suppress code-side
AMEND/RETIRE/MOVE — doc wins; binding maintenance is never suppressed. While
`realize.json` exists, applied ops are stamped `caused_by=⟨directive⟩`.

### Loop B in detail (codoc → code)

`run_loop_b` (1) snapshots the text diff **against the pre-mutation store** (diffing
after verdicts would misread the text and revert the accepted change), (2) drains
`inbox.json` verdicts, (3) applies user ops stamped with the annotating actor/mode
(default human/pen), (4) drains live **payload intents** (doc-ahead suggestions,
`mode=suggest`), (5) re-renders `tree.codoc`, (6) builds a directive from each
code-implying op. Each gets a `d-…` id (in its `### N.` heading + the
`realize.json` manifest) and is written to `realize.md` with status
`awaiting_impl` for the live session — the queue **appends, never clobbers**.
`> …` steering, `**bold**` focus, and external links drain here into
STEER / `Focus:` / `Consult:` lines.

**RETIRE is path-asymmetric:** accepting an auto-raised RETIRE from the inbox is
**detach-only** (never queue code removal — a Loop-A retire off transient drift
could be a false positive); only a human `~` retire in the text queues a
`RETIRE FEATURE` removal directive.

**Realization trigger.** Primary path is in-session: `/codoc:plan` proposes nodes
then calls the blocking `codoc_await_verdicts` MCP tool, so the same turn
implements + binds. Fallbacks reuse `realize.md`: the `UserPromptSubmit` hook (no
daemon) and `codoc watch --auto-realize` (no session), via `loop/sdk_realize.py`
(preferred) or a blind `claude -p /codoc:sync`. The unified `/codoc:sync` reads
`status.json` and dispatches direction.

### Bootstrap in detail

`run_bootstrap` → `bootstrap_hier_from_chunks`, two phases: (1) a per-file
`propose_file_features` LLM call (sees only that file's chunks → structurally can't
make a cross-file junk drawer; `_ensure_file_coverage` folds stragglers into the
file's largest node); (2) an org pass (`organize=True`) grouping file-features under
3–6 broad theme parents. Temp ids ("n1"/"t1") resolve to real ids before apply,
enabling within-call nesting.

### Environment variables

| Var | Default | Description |
|---|---|---|
| `CODOC_PROVIDER` | inferred | `claude` / `openai` / `anthropic` / `ollama`. Unset → `openai` if `OPENAI_API_KEY`, else `anthropic` if `ANTHROPIC_API_KEY`, else **keyless `claude`** (Claude Code login) |
| `CODOC_MODEL` | per-provider | default `gpt-5.4-mini` / `claude-sonnet-4-6` / `sonnet`; cross-family value ignored |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | API keys (also select the provider) |
| `CODOC_BASE_URL` | — | custom OpenAI-compatible base URL |
| `CODOC_TEMPERATURE` | `0.2` | sampling temperature |
| `CODOC_MAX_TOKENS` | `16000` | completion budget |
| `CODOC_EMBEDDER_PROVIDER` / `CODOC_EMBEDDER_MODEL` | `sentence-transformers` / `all-MiniLM-L6-v2` | embedder (dedup/similarity) |
| `COCOINDEX_DB` / `CODOC_LANCE_PATH` | `.codoc/cocoindex.db` / `.codoc/lancedb` | index state paths (auto-set) |
| `CODOC_LOG_PROMPTS` | — | `1` → log LLM prompt+response to stderr |
| `CODOC_EPOCH_ORIGIN` | `interactive` | `loop_b` marks an agent-owned epoch |
| `CODOC_NO_STOP_REFLECT` | — | disable the Stop-hook recovery reflection |

### Render + sidecar (`codoc_file/render.py`)

`write_tree` writes `tree.codoc` + the sidecar; the sidecar is **pure derived
state** and is re-emitted on every pass even when the text render is held back
(so an accept/reject is never a dead click). The `.codoc/` control files:

- **`tree.codoc`** — the human-authored tree. `- Title  ⟨f-id⟩` (id hidden);
  free-prose multi-paragraph descriptions; inline `[label](codoc:file#symbol)`
  citations. Bindings are *not* printed (they ride in the sidecar). Pending ADD/MOVE
  render as ghost hunks; RETIRE/AMEND emit no text.
- **`tree.bindings.json`** (v5) — the IDE sidecar: `by_feature`/`by_file` bindings,
  `features{}`, `proposals` (drives in-place overlays + Accept/Reject), `changes`
  (recent applied events — agent-pencil re-stamp), `holds`, and derived reading
  slices (`pitch`, `feature_kind`, `feature_see_also`, `feature_drift`). The TS
  reader keys on field presence, so older sidecars still parse.
- **`status.json`** — `{state ∈ in_sync | code_drift | tree_dirty | awaiting_impl |
  realizing, pending, …}`; drives the status bar + header CodeLens. A non-empty
  `realize.md` is a floor (reports `awaiting_impl`, never clobbered to `in_sync`).
- **`realize.md`** + **`realize.json`** — the realization queue (directive prompt
  for `/codoc:sync`) + its machine-readable manifest `{id, feature_id, kind,
  caused_by, text}`. `text` lets a later pass rebuild the queue as old + new.
- **`tree.index.json`** — cross-reference registry (features/bindings/refs) for
  dead-ref flagging + hover; `refs[].resolved` is leaf-tolerant.
- **`drift.json`** — `{fid: "questioned" | "binding-lost"}`, re-emitted as the
  sidecar `feature_drift` slice (excludes held + unrealized).
- **`inbox.json`** — `{verdicts:[{event_id, accept}]}`, written by the IDE, drained
  by Loop B, then cleared. Watched by the daemon.
- **`edits.json`** — the host's provenance/intent channel: `edits` (per-feature
  authorship annotations, drained by Loop B), `intents`/`drafts` (live doc-ahead
  suggestions + held pending edits — the doc-wins hold set). Watched by the daemon.

All `.codoc` files: atomic writes (tmp → rename), tolerant reads (missing/corrupt →
default), via `loop/fsio.py`.

### VSCode extension (`vscode-codoc/`)

File-based; no HTTP server, no port. `WorkspaceState` watches the `.codoc/*`
control files, reparses on change, and drives the status bar off `status.json`.

The **`Codoc Tree` webview** (`providers/tree-editor.ts`) is the default editor for
`tree.codoc`; the raw-text editor is the secondary surface (ghost hunks + decorations
+ CodeLens + lightbulb). Both render **every** proposal type inline — ADD/MOVE as
ghost rows, RETIRE as a strike, AMEND as a word-level inline diff inside the
description — each with inline `✓`/`✗` Accept/Reject plus toolbar Accept-all /
Reject-all. There is no Explorer sidebar and no separate proposal panel.

**Editing model.** Single-writer: the webview is authoritative
(`tree.doc.json`), `tree.codoc` is the byte-identical derived render. Human edits
surface **in situ** as a derived `changedRange` underline against a stable
per-episode baseline; agent→human changes surface via the vendored MIT
track-changes engine's marks (`webview/tiptap/track-changes/`). A **draft / hand-off**
gate keeps code-implying edits safe-by-default: the host marks pending edits as
`drafts` in `edits.json`, the daemon holds their directives, and "Hand to agent"
clears the drafts → queues `realize.md`. Inline comments and `> …` steering
serialize to the same `> …` channel Loop B drains.

Key source files: `state/workspace-state.ts` (root/reload/status, `writeVerdict`),
`state/tree-model.ts` (TS port of `parse.py`, parity-tested), `state/bindings-model.ts`,
`state/edits-channel.ts` (edits.json contract), `state/agent-proposals.ts`,
`providers/{decoration,inlay,completion,doc-links,code-lens,code-actions}.ts`,
`providers/tree-editor.ts` (+ inline-comment lifecycle), `webview/tiptap/*`,
`extension.ts` (activation + command registration).

## Tests

- `tests/` — Python unit + integration (`loop/`, `store/`, `graph/`, `codoc_file/`,
  `agent/`, `mcp/`, `cli/`). Ledger suites: `test_classify.py` (the decision table),
  `test_edits.py` (annotations + manifest + hold_set), `test_reader.py` (scoped reads).
- `tests/bdd/` — Given/When/Then code↔tree round-trip (deterministic via an injected
  `propose`) + a subprocess-isolated real-LLM E2E position report.
  `tests/loop/test_end_to_end.py` and `tests/bdd/test_e2e_userflows.py` are gated on
  `OPENAI_API_KEY` (real index + real LLM); everything else is deterministic.
- vscode-codoc: `npx vitest run` from `vscode-codoc/`; `npx tsc --noEmit` + esbuild
  must stay clean. The TS parser is parity-tested against `parse.py`.
- Fixtures: `tests/fixtures/` (self-contained Python + TypeScript samples) and `test/`
  (real-world corpora for bootstrap/E2E: `requests/`, `altair/`, `nanochat/`, …).

Accepted review residuals (deferred follow-ups, e.g. a `codoc_steer` MCP tool) live
in `docs/residual-review-findings/`.
