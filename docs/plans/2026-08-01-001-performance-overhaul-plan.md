# Performance overhaul — indexing, loops, LLM cost, agent surface

**Date:** 2026-08-01 · **Status:** ✅ implemented (P0+P1 + parts of P2; residuals listed at the end)

## Measured results (after)

| Metric | Before | After | Factor |
|---|---|---|---|
| From-scratch index, test/requests (347 chunks) | 12.7s | **2.2s** | 5.9× |
| From-scratch index, whole CodeNav repo (3,541 chunks) | ~2+ min (extrapolated 27/s) | **16.8s** | ~8× |
| `.codoc` index disk (this repo) | 283MB (4,253 Lance versions) | **23MB** (bounded by 30-min retention) | 12× |
| Keyless `claude` completion (the default provider) | 4.5s, 37,171 tok ingest, $0.075 | **1.7s, ~1.2K tok, $0.0016** (+ cross-spawn cache hits) | ~47× cost |
| Bootstrap 24 files @0.5s-per-call stand-in | 12.15s serial | **1.55s** (waves of 8, identical output) | 7.8× |
| `codoc_tree` payload (60 feats × 8 binds synthetic) | 40.7KB | **16KB** default; **2.7KB** via the new scoped `codoc_context` | 2.5× / 15× |
| Light full-index scan (3.5K chunks) | 0.105s | **0.020s** (post-compaction) | 5× |
| No-op reconcile graph work | full O(C) tree-sitter re-extraction | **skipped** (scoped; full build only when the graph is empty) | — |
| Per-tick sidecar compute | 2× O(F+B+E) + per-feature query storm | **1× + bulk grouped reads** | ≥2× |
| Verification | — | 1,026 pytest + 646 vitest + `tsc --noEmit` all green | |
**Goal:** end-to-end speed "a few times faster, fully optimized": `codoc init`
multi-x faster, per-edit tick cost proportional to the *edit* (not the repo),
LLM API spend cut by an order of magnitude via cache-aligned prompting +
parallelism, and agent-facing query results sized to what an LLM can actually
reason over.

Evidence base: 4 subsystem explorations + measured baseline on this repo
(249 py/ts files → 3,523 chunks, 41,741 graph edges) + external research
(cocoindex batching/live-mode docs, LanceDB maintenance APIs, Anthropic/OpenAI
prompt-caching + Batch API specifics). Baseline numbers in
`scratchpad/BASELINE.md` (session) — headline rows reproduced inline below.

---

## Measured baseline (the "before")

| Metric | Value |
|---|---|
| From-scratch index, test/requests (347 chunks) | 12.7s (~27 chunks/s) |
| Embedding throughput as-shipped (per-chunk `.embed`) | ~9/s; batched b=64: **136/s** |
| LanceDB state for this repo | **256MB, 4,253 versions**; after `optimize(cleanup_older_than=0)`: **22MB, 1 version**, scans 5× faster |
| `import sentence_transformers` + model load | 5.8s + 1.8s, per cold process |
| Keyless `claude -p` completion (the default provider) | **4.5s wall + 37,171 cache-write tokens per call** (~$0.07 even on Haiku) — new session per call, nothing reused |
| Bootstrap | **N+1 strictly serial LLM calls** for N files |
| Warm no-op re-index / scoped 1-file changeset | 0.03s / 0.05s (cocoindex memoization works) |
| Per-tick loop work for a 1-file edit | Θ(N+C+F+B+E) — repo-proportional (see WS2) |
| `codoc_tree` MCP dump (test/requests, 24 features) | ~20KB / ~5-6K tokens, unbounded scaling; binding symbol_paths ≈ 2/3 of it |

Structural facts driving the plan:
1. **Chunk embeddings are computed + stored but never read.** All 6
   `read_all_chunks` callers pass `with_embeddings=False`; no vector/KNN search
   exists over `code_chunks`. Only opt-in title-dedup embeds (fresh title
   strings, not stored vectors). The most expensive part of indexing produces
   dead data.
2. **LanceDB is never maintained** (cocoindex never calls `optimize()` — known
   gap, cocoindex#2002): version-per-write accumulates forever.
3. **The keyless provider spawns a fresh `claude -p` per completion** — 37K
   tokens of Claude Code system context ingested per call, never read back.
4. **Prompt order is cache-hostile**: volatile `{changes}` precedes the stable
   rules/titles in `tree_update.txt`; caching is strict-prefix, so hits ≈ 0.
   No `cache_control` anywhere; no system/user split.
5. **Per-tick repo-proportional work**: unscoped `read_all_chunks`
   (loop_a.py:1015), `_build_indices` over all rows each pass (extract.py:199),
   sidecar recomputed **twice** per tick with ~13 `list_features` scans
   (reconcile.py:74 + render.py:656), heal + types-backfill every tick,
   empty-changeset fallback re-extracts the whole graph (loop_a.py:1029).
6. **`codoc_tree` is all-or-nothing** while the relevance selector
   (`subtree.select_relevant_subtree`) already exists, unexposed.

---

## WS1 — Indexing pipeline

**1.1 Chunk embedding becomes opt-in (default OFF).** New env
`CODOC_EMBED_CHUNKS=1` (+ config passthrough). Default pipeline schema has no
embedding column / no embedder in the lifespan → `codoc init` indexing becomes
parse+hash+write only; the 5.8s+1.8s sentence-transformers cold cost disappears
from every process. When ON, embed via cocoindex's **batched** path
(`batching=True` list→list fn / built-in `SentenceTransformerEmbed` op, ~5×
measured by upstream, 15× in our microbenchmark vs per-chunk `.embed`).
Capability preserved for future semantic search; cocoindex `detect_change`
invalidation makes flipping the flag a clean one-time re/de-embed.

**1.2 LanceDB maintenance.** After each `update_index` write pass (cheap to
call; skip when no rows changed): `table.optimize(cleanup_older_than=
timedelta(hours=1))`. NOT zero retention (lancedb#3086 concurrent-writer
issue); 1h bounds disk to a session's churn. One-time effect here: 256→22MB.

**1.3 Kill the double parse.** `_process_chunk` re-parses every chunk with
tree-sitter (`tree_walk`) after `extract_chunks` already parsed the file.
Compute `tokens_hash`/`types_hash` during extraction from the already-parsed
tree (adapter returns them per chunk); `_process_chunk` becomes pure row
assembly. ~2× the parse cost of every changed file, every pass.

**1.4 Reader connection reuse.** `read_all_chunks` opens a new event loop +
LanceDB connection per call (and `compute_changeset` calls it up to 3×).
Cache the connection per codoc_dir (invalidate on path change); single loop.

**1.5 (deferred) cocoindex live mode** in the daemon to eliminate the O(N)
walk per tick. The walk is ~0.2s at 250 files — matters at 5k+ files; noted,
not implemented now.

## WS2 — Loop hot path: Θ(repo) → Θ(edit)

**2.1 Render once, skip unchanged.** Dedupe the double sidecar computation
(safe_write_tree + write_tree both call write_sidecar); thread ONE preloaded
feature list through render; add a content-hash skip so an unchanged projection
writes nothing. Kills the ~13 `list_features`/tick and the 2× O(F+B+E).

**2.2 Scope the reconcile reads.** `reconcile_drift` reads all C chunks
unscoped every tick; the watch batch knows the touched files. Scoped read for
the changeset; the full symbol table only for the graph — see 2.3.

**2.3 Cache the graph symbol index.** `_build_indices` rebuilds by_symbol/
by_leaf/file_to_module over ALL rows per pass. Keep a process-lifetime index in
the daemon, invalidated per touched file. Fix the empty-changeset fallback
(`cs.touched_files() or {r.file for r in rows}`) that re-extracts the whole
graph on clean startup/no-op reconciles.

**2.4 Gate the every-tick sweeps.** `heal_tree_integrity` +
`_backfill_types_hashes` move to startup + post-structural-change only.

**2.5 Store micro-fixes.** Missing indices (`features.retired`,
`features.created_at`); `open_store` fast-path (PRAGMA user_version — skip
executescript+migrate on already-current DBs; it currently reruns on EVERY
open, several times per tick); bulk bindings load in `_state_changeset`
(one query per scope, not per chunk); batched backfill commits.

**2.6 Lock hold.** Where safe, run `update_index` outside `loop_lock` (index
is cocoindex-transactional; only store mutation needs the loop lock).

## WS3 — LLM cost & latency

**3.1 Cache-aligned prompt order** (all prompts): [static instructions+rules]
→ [all_titles, deterministically serialized, id-sorted] → [subtree] →
[changes/volatile] last. Compact serialization: indented outline instead of
`json.dumps(indent=2)` for titles (~3-5× fewer tokens).

**3.2 Provider layer upgrades** (`config.py`): system/user split;
Anthropic `cache_control` breakpoints (static system block + titles block;
5m TTL default); OpenAI `prompt_cache_key`; retries with backoff + explicit
timeouts (the CLI path can currently hang forever); usage logging
(cache_read/cache_creation) behind `CODOC_LOG_USAGE=1` to verify hits.

**3.3 Persistent SDK session for the keyless path.** Replace per-call
`claude -p` subprocess with a resident `claude_agent_sdk` client in the daemon
(minimal `system_prompt`, no tools/MCP/CLAUDE.md) — kills the 37K/call ingest
and the ~2s spawn; consecutive calls share the SDK's automatic prompt cache.
CLI remains the fallback when the SDK import fails.

**3.4 Bootstrap parallelism.** Wave-based: split files into waves; within a
wave, calls run concurrently (ThreadPool, default 8, `CODOC_BOOTSTRAP_CONCURRENCY`)
against a shared titles snapshot; titles extend between waves (dedup context
preserved wave-to-wave; org pass + `(title,parent)` guard absorb intra-wave
overlap). Same-snapshot waves = identical prompt prefix = cache hits across
the wave. Optional `CODOC_BOOTSTRAP_BATCH=1`: Anthropic Message Batches /
OpenAI Batch (50% off, caching stacks) for unattended big-repo init.

**3.5 Model tiering.** Tree-update + per-file bootstrap are structured
extraction → default to the fast tier (`haiku` alias / `claude-haiku-4-5` /
`gpt-5.4-mini` stays); org pass keeps the mid tier. `CODOC_MODEL` still
overrides everything; new `CODOC_MODEL_FAST` for the extraction calls.

**3.6 Response memoization.** Local `hash(prompt)→parsed ops` cache (bounded,
in `.codoc/`) so identical re-issued passes (state reconcile after a failed
apply, crash-replay) don't re-bill.

## WS4 — Agent surface: what an LLM needs from codoc

**4.1 Scope `codoc_tree`.** Params: `root_id`, `depth`, `include_bindings`
(default **false** → per-feature `{binding_count, files}`), `fields`.
Full dump stays available explicitly (`include_bindings=true, depth=0`).

**4.2 New `codoc_context` MCP tool** — the primary agent read: takes `files`
(and/or `feature_id`), runs `select_relevant_subtree` (ego-graph), returns the
relevant subtree + compact titles outline + graph edge sketch. This is "what
does codoc know that's relevant to what I'm editing" in one bounded call.

**4.3 Trim realize directives.** `Bound code:` line → files + count (full
symbol list only under a size threshold).

**4.4 Update the plugin guidance** (SKILL.md, sync.md, plan.md): agents call
`codoc_context` scoped-first; `codoc_tree` only for whole-tree operations.

**4.5 Cap `dead_ref_list`** in `codoc_status` (count + first 20).

## WS5 — Stress test + verification

Re-run the baseline harness (init on test/requests + this repo, warm/cold
tick, MCP payload bytes, LanceDB disk) and require: init indexing ≥5× faster
default-path, tick render+read work no longer repo-proportional (measured via
counters), `codoc_tree` default payload ≥5× smaller on test corpora, all
pytest + vitest suites green, `tsc --noEmit` clean.

## Sequencing

P0 (this session, huge/cheap): 1.1, 1.2, 3.1, 3.2, 2.5-indices, 4.1, 4.2, 4.5
P1: 3.3, 3.4, 2.1, 2.2, 2.3-fallback-fix, 4.3, 4.4, 1.4
P2: 1.3, 2.3-cache, 2.4, 2.6, 3.5, 3.6, 3.4-batch-mode, 1.5

Non-goals now: swapping the embedding model (embeddings default off; revisit
jina-v2-base-code/potion when a semantic feature ships), cocoindex live mode,
Postgres-backed anything.

## What shipped (2026-08-01)

- **1.1** `CODOC_EMBED_CHUNKS` opt-in (default OFF): `CodeChunkLite` schema,
  lazy sentence-transformers import, flag recorded in `index.meta.json` with
  wipe-and-rebuild on mismatch (also self-heals pre-existing bloated indexes).
  When ON, hashes are hoisted out of the embed tasks so the embedder's adaptive
  batching can form real batches.
- **1.2** `optimize(cleanup_older_than=30min)` after every `update_index`
  (~40ms steady-state; 30-min retention per lancedb#3086).
- **1.4** reader: process-lifetime background event loop + cached LanceDB
  connection per index path (was: fresh loop + connection per call, ≤4×/pass);
  embeddings default to excluded; missing column tolerated.
- **2.1** sidecar computed ONCE per tick (`write_tree(sidecar=False)` from
  `safe_write_tree`) + bulk grouped bindings read (was per-feature queries).
- **2.2** `reconcile_drift` reads the light identity projection first and
  fetches SOURCE only for files that actually diverged (an in-sync pass never
  materializes a chunk body).
- **2.3** empty-changeset full-graph-rebuild fallback removed; full extraction
  only when `code_edges` is empty (`store.has_edges()`).
- **2.4** `heal_tree_integrity` + `_backfill_types_hashes` gated to unscoped
  recovery passes (startup / Stop hook / sync), skipped on per-edit ticks.
- **2.5** `(retired, created_at)` composite index + `PRAGMA user_version`
  fast-path in `Store.open` (skips schema replay + 4 table_info scans);
  `_state_changeset` uses one bulk bindings read (was per-chunk `binding_at`).
- **3.1** cache-aligned prompts: `tree_update.txt` + `bootstrap_file.txt`
  restructured [frozen instructions] → [titles] → [volatile change] with
  `<<<CACHE_BREAK>>>` markers (`agent/base.py split_prompt`); titles as a
  compact deterministic outline (`titles_outline`), not indented JSON.
- **3.2** provider layer: `prefix_parts` → Anthropic system blocks with
  `cache_control` breakpoints (≤2), OpenAI `prompt_cache_key` + stable prefix,
  timeouts (`CODOC_LLM_TIMEOUT`, default 300s) + SDK retries everywhere (the
  CLI path could previously hang forever).
- **3.3** keyless path minimal context: `--system-prompt-file` (codoc preamble
  + stable prefix parts; file, never argv) + `--disallowedTools "*"` — 37K→
  ~1.2K tokens/call, cross-spawn cache hits verified. `--bare` re-verified
  still broken for OAuth (guard kept).
- **3.4** bootstrap waves: concurrent per-file calls in waves of
  `CODOC_BOOTSTRAP_CONCURRENCY` (default 8) against a shared titles snapshot;
  store reads before dispatch, writes after, deterministic order preserved.
- **3.5** fast model tier (`fast_llm_config`): tree-update + per-file bootstrap
  default to haiku-class; `CODOC_MODEL_FAST` overrides; explicit `CODOC_MODEL`
  pins everything.
- **4.1** `codoc_tree(root_id, depth, include_bindings=False)` — default
  returns `binding_count`+`files` per feature, not every symbol_path.
- **4.2** `codoc_context(files, feature_id)` MCP tool over the (extracted)
  `subtree.select_context` ego-graph selector — the new primary agent read.
- **4.3** realize `Bound code:` line summarizes per-file above 12 bindings.
- **4.4** SKILL.md / sync.md / plan.md rewritten scoped-first.
- **4.5** `dead_ref_list` capped at 20 (count stays exact).

## Review pass (2026-08-01, two independent reviewers) — all verified findings fixed

- **P0 removed-symbol graph wipe** (both reviewers): a file whose only
  divergence was a DELETED symbol never entered the sourced-fetch candidates,
  yet landed in graph_scope — update_graph re-extracted its surviving rows from
  `source=''` and silently wiped the file's call/import edges. Fixed
  (removed-bound files join `candidates`); regression test
  `tests/loop/test_reconcile_scoped_reads.py` proven to catch it (fails with
  the fix disabled).
- **P0 empty-index mass-detach**: a torn/absent index read ([]) beside real
  bindings would auto-detach everything. Three guards: reconcile_drift aborts
  when `rows_light` is empty but bindings exist; bootstrap's `update_index`
  (the one unlocked caller — it can rmtree the index) now runs under
  `loop_lock`; `codoc init` refuses to run beside a live daemon.
- **Marker injection**: templates are now split on `<<<CACHE_BREAK>>>` BEFORE
  substitution — a literal marker in repo content (codoc's own base.py has one)
  could previously move the split and promote untrusted text into the system
  prompt.
- **Embed-flag ping-pong**: an explicit `CODOC_EMBED_CHUNKS` wins; with the var
  unset the recorded `index.meta.json` state is authoritative, so processes
  with different environments can't alternately wipe each other's index. Wipes
  and meta-write failures now log loudly; the resolved value is pinned into the
  env for the app run.
- **Gating premise fix**: the Stop hook's reflect is SCOPED, so heal/backfill
  gated to unscoped passes would never run in hook-only workspaces.
  `heal_tree_integrity` now runs every pass (cheap); the types-hash backfill
  sweeps the pass's scope, full on unscoped passes.
- **Nothing-vanishes reads**: `read_tree` walk is cycle-safe (seen-set) and
  appends root-unreachable features flat; `titles_outline` emits every title
  (orphan descendants/cycle members were dropped — duplicate-mint risk).
- **Reliability hardening**: reader ops bounded at 120s (a wedged native read
  no longer holds `loop_lock` forever), dead-loop-thread recovery, connections
  closed on invalidate; optimize failures log instead of vanishing; bootstrap
  wave failure explains its bounded wait instead of hanging silently at exit;
  `prompt_cache_key` only against the stock OpenAI endpoint; claude temp
  system-file can't leak on a failed write.
- Accepted without change: external MCP clients written against the old
  `codoc_tree` shape see `bindings` → `binding_count`+`files` (documented);
  8-way bootstrap spawn memory/rate-limit amplification (bounded by
  concurrency env); zero-edge-repo `graph_full_build` refire (pathological —
  any class/contain edge prevents it).

## Residuals (deliberately deferred)

- Embeddings-ON path still ~35 chunks/s (opt-in only; route through a
  `batching=True` list→list op or the built-in `SentenceTransformerEmbed` op
  when a semantic feature actually ships — and reconsider the model:
  all-MiniLM-L6-v2 is weak for code; jina-embeddings-v2-base-code (quality) or
  potion-base-8M (speed) are the candidates).
- 1.3 double parse: `tree_walk` still re-parses each chunk's source for hashes
  after `extract_chunks` parsed the file — the fix belongs in the lang
  adapters (compute hashes during extraction).
- 2.3-cache: `_build_indices` symbol index still rebuilt per `update_graph`
  call (O(C) dict build; ~100ms at 3.5K chunks, matters at 50K).
- 3.6 response memoization (hash(prompt)→ops) not implemented.
- 3.4-batch-mode: Anthropic/OpenAI Batch API path for unattended big-repo
  bootstrap (50% off) not implemented.
- 1.5 cocoindex live mode (removes the O(N) per-tick source scan) not adopted.
- claude-agent-sdk persistent-session completions: not pursued — the measured
  CLI floor (~1.2K tok, cross-spawn cache hits) removed most of the win; also
  the SDK is not installed in this venv.
