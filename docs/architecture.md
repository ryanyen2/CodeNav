# codoc architecture (detailed reference)

The deep internals behind the summary in `CLAUDE.md`. Read `CLAUDE.md` first for
the overview, commands, the `tree.codoc` surface, and the package map; this doc
is the reference for the data model, the two loops in detail, storage, the
control files, environment variables, and the deployed hub.

> **History:** this repo was rewritten clean-slate (2026-05). The old
> reflective/planning/realize/health pipelines, the transaction/constraint model,
> the FastAPI server, and the 20-command CLI were deleted. The cocoindex+LanceDB
> index, the tree-sitter adapters, and `core/tree_walk.py` were kept as substrate.

## Data model

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

## Storage

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

## Loop A in detail (code → codoc)

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

## Loop B in detail (codoc → code)

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

## Bootstrap in detail

`run_bootstrap` → `bootstrap_hier_from_chunks`, two phases: (1) a per-file
`propose_file_features` LLM call (sees only that file's chunks → structurally can't
make a cross-file junk drawer; `_ensure_file_coverage` folds stragglers into the
file's largest node); (2) an org pass (`organize=True`) grouping file-features under
3–6 broad theme parents. Temp ids ("n1"/"t1") resolve to real ids before apply,
enabling within-call nesting.

## Render + sidecar (`codoc_file/render.py`) and the `.codoc` control files

`write_tree` writes `tree.codoc` + the sidecar; the sidecar is **pure derived
state** and is re-emitted on every pass even when the text render is held back
(so an accept/reject is never a dead click). All `.codoc` files use atomic writes
(tmp → rename) + tolerant reads (missing/corrupt → default) via `loop/fsio.py`.

- **`tree.codoc`** — the human-authored tree. `- Title  ⟨f-id⟩` (id hidden);
  free-prose multi-paragraph descriptions; inline `[label](codoc:file#symbol)`
  citations. Bindings are *not* printed (they ride in the sidecar). Pending ADD/MOVE
  render as ghost hunks; RETIRE/AMEND emit no text.
- **`tree.bindings.json`** (v5) — the IDE/browser sidecar: `by_feature`/`by_file`
  bindings, `features{}`, `proposals` (drives in-place overlays + Accept/Reject),
  `changes` (recent applied events), `holds`, and derived reading slices (`pitch`,
  `feature_kind`, `feature_see_also`, `feature_drift`). The TS reader (and the hub's
  `payload.py`) key on field presence, so older sidecars still parse.
- **`status.json`** — `{state ∈ in_sync | code_drift | tree_dirty | awaiting_impl |
  realizing, pending, at, …}`; drives the status bar + header CodeLens (and the
  hub's restart-safe payload version via its `at` HLC). A non-empty `realize.md` is
  a floor (reports `awaiting_impl`, never clobbered to `in_sync`).
- **`realize.md`** + **`realize.json`** — the realization queue (directive prompt
  for `/codoc:sync`) + its machine-readable manifest `{id, feature_id, kind,
  caused_by, text, handed_off}`. `text` lets a later pass rebuild the queue as
  old + new.
- **`tree.index.json`** — cross-reference registry (features/bindings/refs) for
  dead-ref flagging + hover; `refs[].resolved` is leaf-tolerant.
- **`drift.json`** — `{fid: "questioned" | "binding-lost"}`, re-emitted as the
  sidecar `feature_drift` slice (excludes held + unrealized).
- **`inbox.json`** — `{verdicts:[{event_id, accept}]}`, written by the IDE/hub,
  drained by Loop B, then cleared. Read-modify-write is `filelock`-guarded.
- **`edits.json`** — the host's provenance/intent channel: `edits` (authorship
  annotations), `intents`/`drafts` (live doc-ahead suggestions + held pending edits
  — the doc-wins hold set), `cancellations`/`steers`. Read-modify-write is
  `filelock`-guarded (the hub is a second writer).

## Environment variables

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

## VS Code extension internals (`vscode-codoc/`)

**Editing model.** Single-writer: the webview is authoritative (`tree.doc.json`),
`tree.codoc` is the byte-identical derived render. Human edits surface **in situ**
as a derived `changedRange` underline against a stable per-episode baseline;
agent→human changes surface via the vendored MIT track-changes engine's marks
(`webview/tiptap/track-changes/`). A **draft / hand-off** gate keeps code-implying
edits safe-by-default: the host marks pending edits as `drafts` in `edits.json`,
the daemon holds their directives, and "Hand to agent" clears the drafts → queues
`realize.md`. Inline comments and `> …` steering serialize to the same `> …`
channel Loop B drains.

Key source files: `state/workspace-state.ts` (root/reload/status, `writeVerdict`),
`state/tree-model.ts` (TS port of `parse.py`, parity-tested), `state/bindings-model.ts`,
`state/edits-channel.ts` (edits.json contract), `state/agent-proposals.ts`,
`providers/{decoration,inlay,completion,doc-links,code-lens,code-actions}.ts`,
`providers/tree-editor.ts` (+ inline-comment lifecycle), `webview/host-bridge.ts`
(the VS Code/network transport seam), `webview/tiptap/*`, `daemon/{lockfile,daemon-manager}.ts`,
`extension.ts` (activation + command registration).

## The deployed hub (`codoc serve`, `codoc/serve/`)

A Tier-1 web surface that serves the intent tree to GitHub-authorized remote users
**from the maintainer's own machine** (a separate process that supervises the
daemon; a file-channel client that reads `.codoc/*` and writes only the
verdict/draft channels). Remote contributors *suggest* edits; a maintainer hands
them off; the hub realizes them on a git worktree and opens a code PR. See
`docs/serve-deployment.md` for setup. Modules:

- `supervise.py` — atomic single-owner claim + daemon supervision (peer to the
  extension's `daemon-manager.ts`).
- `app.py` — the composition root (FastAPI): static SPA, `/api/payload`,
  `/api/events` (SSE), `/api/whoami`, the CSRF-guarded `POST /api/command`.
- `payload.py` / `push.py` — derive the browser `DocPayload` by re-shaping the
  sidecar (no re-derivation); SSE snapshot + version-guarded re-push with an
  idempotency (no-broadcast-storm) guard. Version = the daemon's HLC stamp on
  `status.json` (restart-safe, not a per-process counter).
- `auth.py` — GitHub collaborator permission → capability (read→suggest,
  write→hand-off); server-side sessions (the browser holds only an opaque cookie).
- `dispatch.py` — capability-gated routing of browser commands to the file channels;
  remote `commit`/settle is held (only an explicit hand-off crosses to execution).
- `ratelimit.py` / `tunnel.py` — per-identity token bucket; cloudflared launcher.
- `sandbox.py` — the enforced realize sandbox (allowed tools, path allow/denylist,
  secret-read exclusion, out-of-scope gate).
- `realize_trigger.py` / `realize_pr.py` — fire only on handed-off directives;
  worktree → sandboxed agent (no token) → `gh pr create` (never push to `main`).
- `budget.py` / `consult.py` — Denial-of-Wallet caps; SSRF-hardened Consult-URL
  allowlist.

The browser reuses the same webview bundle via `acquireHostApi()` (the VS Code
path is unchanged); the standalone shell is `vscode-codoc/web/index.html` (strict CSP).
