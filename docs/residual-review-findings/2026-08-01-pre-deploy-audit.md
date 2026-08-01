# Pre-deployment audit — deferred findings (2026-08-01)

A six-dimension pre-deploy audit (onboarding, control-file/concurrency, wrong-action,
security, performance, cleanup) ran before opening codoc to bring-your-own-codebase
users. Most confirmed findings were fixed in the same pass. The items below were
**deliberately deferred** — either they need a design change too large/risky for the
deploy window, or their blast radius is narrow. Each records the exact fix so it can be
picked up later.

## RESOLVED (2026-08-01) — hub (Flow 3, `codoc serve`) auth + sandbox now wired

The hub's tested-but-unwired layers were wired into the running process:

- **Auth wired end-to-end** — `codoc/serve/github_auth.py` (new) is the live GitHub
  edge: OAuth authorization-code exchange + a collaborator-permission resolver (HTTP
  injected for tests). `serve()` builds a real `AuthContext` from env
  (`CODOC_GITHUB_CLIENT_ID`/`SECRET`/`CODOC_GITHUB_TOKEN`/`CODOC_SERVE_REPO`) and passes
  it to `build_app`. `app.py` now GATES every `/api/*` route — reads included
  (`/api/payload`, `/api/media`, `/api/events`) — on a valid collaborator session, and
  adds `/auth/login`, `/auth/callback`, `/api/logout`. Exposure (`--tunnel`/non-local
  `--host`) now REQUIRES configured auth (else refused, unless
  `--i-understand-unauthenticated`).
- **Sandbox + consult enforced** — `codoc/serve/realize_agent.py` (new) binds
  `sandbox.tool_policy(scope)` + `consult_url_allowed` onto a Claude-Agent-SDK run
  (Read/Edit/Write/Glob/Grep + consult-gated WebFetch, NO Bash, scoped edits, secret
  denylist, token-scrubbed env), FAIL-SAFE (refuses rather than running unsandboxed).
  `codoc/serve/realize_hub.py` (new) is the server-owned trigger worker: handed-off +
  undone directives → worktree → sandboxed agent → PR (`realize_pr.realize_directive`)
  → `mark_done`. Wired into `serve()` (only when auth is configured). The local
  `sdk_realize` engine stays unsandboxed by design — it only runs on the user's OWN
  directives; the supervisor spawns `codoc watch` WITHOUT `--auto-realize`, so remote
  directives never reach it.
- Tests: `tests/serve/test_github_auth.py`, `test_app_auth_gate.py`,
  `test_realize_agent.py`, `test_realize_hub.py` (+ existing serve suite green).

**Residual for a real deployment** (needs live GitHub App creds / SDK / git-gh to
exercise, not unit-testable here): confirm the installed `claude-agent-sdk`'s
`ClaudeAgentOptions(can_use_tool=…)` hook signature matches (the agent is fail-safe if
not — it refuses rather than running unsandboxed, so a mismatch degrades to "hub
realize does nothing," never to an unsafe run); and the `fetch_guard.safe_get`
DNS-rebinding TOCTOU on the block-edit `url` path (latent — that write path is behind
the now-gated `/api/command`).

The local flow (`init`/`watch`/`sync` + the VS Code extension) remains the primary,
fully-hardened deployment.

## Deferred — correctness / robustness (narrow blast radius)

- **Handed-off realize directive lost in a two-write crash window** (`codoc/loop/loop_b.py`
  ~1050): writing `realize.md` before the manifest (shipped) closes the common single-crash
  case, but the manifest-loss window still needs a `triggered` flag on `Directive` so
  `read_manifest` can tell "agent finished" from "crashed before trigger" instead of using
  realize.md-absence as the completion signal. (control-file P1-3)
- **Verdict channel lock-less RMW in the extension** (`vscode-codoc/src/state/workspace-state.ts:194-204`):
  `writeVerdict` does read-modify-write on `inbox.json` with no lock, so a second window (or
  a daemon drain racing the write) can resurrect or lose a verdict. Fix: route Accept/Reject
  through the `edits.host.jsonl` append log the command channel already uses (add an
  `appendVerdict` host-op + daemon dispatch). (control-file P1-1)
- **base_rev version gate is client-only** (`codoc/loop/edits.py:173-175`): the server applies
  `set_title`/`set_description` commands in drain order with last-writer-wins; concurrent edits
  to one feature from two windows silently drop the earlier one. Fix: compare `cmd.base_rev`
  to the feature's `updated_at` in loop_b step 0.5 and route a mismatch to the diff surface.
  (wrong-action P1)
- **Fresh clone / deleted `codoc.db` can't restore the store from committed `tree.codoc`**:
  the store is gitignored and authoritative, but there is no `tree.codoc`→store importer, so a
  teammate who clones has the tree file and an empty store. `init` now refuses to clobber an
  existing tree (shipped guard), but a real importer (seed the store from a committed
  `tree.codoc`/`tree.doc.json` on first `watch`) is the complete fix. (wrong-action P0-2)
- **HLC uses raw wall clock** (`codoc/model/hlc.py:46-48`): non-monotonic within a ms and
  inverts on a backward clock jump; bounded impact (a projection/gate glitch that self-heals),
  so deferred. Fix: seed `now()` from `max(store-max-HLC, wall)` or a process monotonic floor.
  (control-file P2-5)
- **`events` / `applied_commands` grow unbounded**: an `idx_events_at` index shipped (keeps
  `recent_events` flat), but a periodic prune of applied events older than N days + a ledger cap
  is still wanted for a long-lived daemon. (control-file P2-8 / perf P2-5)
- **Steer double-fire on `.merging` crash-recovery** (`codoc/loop/edits.py:701-708`): a crash
  between draining a host-op batch and unlinking `.merging` re-appends the same steer (no
  `comment_id` dedup). Narrow; fix by deduping steers on drain. (control-file P2-4)

## Deferred — performance (correctness-entangled)

- **Daemon reads the whole index WITH source on every save** (`codoc/loop/loop_a.py:1015`):
  the fix (read the symbol table `with_source=False`, hydrate source only for `file_scope`)
  entangles with the `update_graph(..., cs.touched_files() or {all files})` fallback, which
  re-extracts edges from `row.source` for ALL files when the changeset is empty — so scoping
  the source read would drop edges on an empty-changeset pass. Needs the fallback reworked to
  only rebuild the graph when it's actually empty, verified against the LLM-gated e2e. The 26×
  dead-loop fix (shipped) already removed the dominant cost from this path. (perf P1-2)
- **Full render N+1** (`write_sidecar`/`build_doc_from_store`): per-feature `bindings_for_feature`/
  `marks_for_feature`/`comments_for_feature` in a loop over all features (~240ms/pass at 1k
  features). Fix: batch with `IN (…)` queries like `blocks_for_features` already does. (perf P1-3)

## Deferred — first-run polish

- **Default OpenAI model `gpt-5.4-mini`** (`codoc/config.py:74`, README, `.env`): flagged as a
  possible placeholder that would 404 the documented OpenAI path. Left unchanged — a model id is
  not something to guess; verify against the live OpenAI model list and correct README + default
  together if it's wrong. The keyless-`claude` default path (the recommended one) is unaffected.
- **`.env` holds a live-looking key**: gitignored and never committed, but rotate it before
  archiving/sharing the working folder.
