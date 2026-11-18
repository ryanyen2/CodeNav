# Code map

Where each part of codoc lives, how the coding agent and the hub reach the applier, and
the environment variables. The document model is in [model.md](model.md), processes and
files in [concurrency.md](concurrency.md), the loops and gestures in
[workflows.md](workflows.md).

## 1. Packages

A package imports only packages to its left in this order: `calculus`, `index`,
`workspace`, `doclang`, `llm`, `kinds`, `loops`, `daemon`, `claude`, `mcp`, `serve`,
`cli`. `calculus` and `index` import nothing else in codoc.

| module | holds |
|---|---|
| **`calculus/`** | In-memory structures only: nothing reads a file or a clock. |
| `ids.py` | `OpId`, `CharRef`, block, plan and batch ids; actor kinds. |
| `text.py` | `Text`: segments, marks, every text mutation by identity. |
| `doc.py` | `Block`, the kind table (Text fields, in flow, exported), the heading fold. |
| `rank.py` | Base-62 fractional order keys. |
| `ops.py` | The `Op` record, the op kinds, frontiers. |
| `store.py` | `Store`: blocks, ops, bindings, plans, watermarks and the indexes the views use. |
| `apply.py` | `apply(store, op)`: the handlers, inverses, verdicts. |
| `ingest.py` | `ingest` and `block_bodies`. |
| `inline_md.py`, `block_md.py` | The Markdown form, inline and block. |
| `derive.py` | The views: description, attribution, ink, ground, plan claims, phase, history, projection. |
| `window.py` | The timeline window in `history.json`. |
| `export.py` | `tree.codoc`. |
| **`workspace/`** | The `.codoc` directory. |
| `layout.py` | File names, atomic write, tolerant read, `flock`. |
| `config.py` | `config.json`. |
| `clock.py` | The hybrid logical clock. |
| `log.py` | Session files, their checks, replay order, the inbox and acks, inbox name to actor. |
| `roles.py` | The role table and the policy `apply` asks. |
| `workspace.py` | `Workspace`: rebuild, `submit`, `ingest` and the machine gate, `drain_inbox`, session rotation. |
| `snapshot.py` | `codoc snapshot`. |
| `validate.py` | `codoc validate-logs`. |
| **`index/`** | The code, read without the document. |
| `lang/` | Tree-sitter adapters for Python and TypeScript: chunks and references. |
| `tree_walk.py` | A chunk's two identity hashes (`fingerprint`, `types_hash`). |
| `app.py`, `runner.py`, `schema.py`, `reader.py` | The cocoindex app and the LanceDB chunk table. |
| `changes.py` | The change set of one index update. |
| `extract.py`, `graph.py`, `flows.py` | The dependency graph over chunks, cached as JSON. |
| `git.py` | `HEAD` and what recent commits said about a file. |
| **`doclang.py`** | The authoring language: tags, detection, character budgets. |
| **`llm/`** | `provider.py` (which model, `complete()`), `prompt.py` (templates in `codoc/prompts/`, JSON out), `passes.py` (one function per prompt). |
| **`kinds/`** | The kind registry, the shipped kinds, `write.py` (content to ops), `lift.py` (the lift pass), `fetch_guard.py`. See [kinds.md](kinds.md). |
| **`loops/`** | |
| `context.py` | What a pass or an agent read is shown: the ego graph over bindings, the title outline. |
| `edits.py` | Tree edits (`attach`, `detach`, `add_node`, `amend`, `move_node`, `retire_node`) compiled to op bodies and ingests. Writes nothing. |
| `bootstrap.py`, `reflect.py`, `realize.py`, `translate.py` | The loops in [workflows.md](workflows.md). |
| **`daemon/`** | |
| `daemon.py` | `codoc watch`: the per-batch order in [concurrency.md](concurrency.md#4-a-submissions-path). |
| `client.py` | How another process writes and reads: the inbox when a daemon is alive, else the applier under `applier.lock`. |
| `publish.py` | The derived files and the realizing lease in `status.json`. |
| `epoch.py` | `activity.json`, the agent epoch. |
| `pidfile.py` | `watch.pid`. |
| **`claude/`** | Section 2. |
| **`cli/main.py`** | The `codoc` command. |
| **`mcp/`** | `server.py` (FastMCP over stdio, `codoc-mcp`), `tools.py`, `walkthrough.py`. |
| **`serve/`** | Section 3. |

## 2. The coding agent

`codoc init` and `codoc install-hooks` write the files in `codoc/claude/plugin/` into the
repository: Claude Code hooks, the MCP registration in `.mcp.json`, the `codoc-intent`
skill, and the `/codoc:sync`, `/codoc:plan` and `/codoc:ask` commands.

**Hooks** (`claude/hook.py`, events `session-start`, `user-prompt`, `pre-tool`,
`post-tool`, `stop`, `session-end`):

- Open the agent epoch in `activity.json` while a turn runs and record the files it
  touched. The daemon holds those files and reflects them when the epoch closes, or after
  900 seconds of silence, citing every queued hand-off in `caused_by`. Nothing else reads
  the file: the editor and the hub learn that a feature is being built from its
  `realizing` phase in the projection.
- Tell the session when hand-offs are queued in `realize.md`.
- At `stop`, when the turn's `codoc_reflect` calls cite some queued items but not all (read
  from the agent's inbox file), block the stop once and name the rest, so an item built but
  not reported is reported or explained. A turn that never touched the queue just ends.
- The MCP tools take a hand-off as the queue shows it (`d:<op id>`) or as the op id.
- Never raise and never open the workspace; they read published files only.

**MCP tools.** Reads replay the logs into a read-only workspace. Writes go through
`daemon/client.py` with the inbox name `agent-<CODOC_AGENT>`, so the applier stamps
`agent:<CODOC_AGENT>`, `agent:claude-code` when the variable is unset. Another agent
(Cursor, Codex) sets `CODOC_AGENT` in the server's environment.

**How a hand-off settles.** When no agent epoch is open, the applier (the daemon at the
end of each batch and after each reflect pass, or a daemonless writer after it applies)
writes `feature.realized` for each queued feature once an applied binding op cites its
latest hand-off in `caused_by` (`loops/realize.py`, `settle`). It does not matter who
wrote the op: an agent's `codoc_reflect`, the pass after a Claude Code epoch closes, or
the pass over a commit whose `Codoc-Handoff` trailer names the hand-off. While an epoch
is open the build is in progress, so the feature stays queued until the epoch closes.
Code written by hand settles with `codoc done`.

| tool | does |
|---|---|
| `codoc_context`, `codoc_tree`, `codoc_status`, `codoc_history` | Read: the features around some files, the tree, the queue and phases, one feature's ops and attribution. |
| `codoc_reflect` | Tree edits. `attach`, `detach` and a rewording `amend` land on the trunk (an `amend` through the machine gate); anything else becomes one plan. |
| `codoc_propose` | Tree edits as one plan. |
| `codoc_write_block` | Content for a kind block ([kinds.md](kinds.md#4-how-content-becomes-ops)). |
| `codoc_realize_progress` | Renews the realizing lease with "implementing n of m". |
| `codoc_walkthrough`, `codoc_walkthrough_read`, `codoc_walkthrough_clear` | The `/codoc:ask` overlay in `ask.json`. |

Every edit is compiled against the current tree before anything is written; if one is
refused, nothing is written.

**Realize engines.** `codoc realize` runs `/codoc:sync` through the Claude Agent SDK
(`claude/sdk_realize.py`, the `sdk` extra) or `claude -p` (`--engine cli`).
`codoc watch --auto-realize` (`claude/autorealize.py`) starts a pass when work is queued
and no session is live.

## 3. The hub

`codoc serve` serves the editor bundle to people signed in with GitHub
([serve-deployment.md](serve-deployment.md)).

| module | role |
|---|---|
| `supervise.py` | Claims the repository with `.codoc/serve.lock` and supervises one daemon. |
| `app.py` | Routes: GitHub sign-in, `/api/whoami`, `/api/events` (SSE), `/api/message` (a `submit` appends to `inbox/hub-<login>.jsonl`), `/api/media/<name>`, the page. |
| `push.py` | The SSE channel: `projection`, `status`, this viewer's `acks`. |
| `auth.py`, `github_auth.py` | Sessions, and collaborator permission to role, written to `config.json`. Re-checked on a cadence. |
| `ratelimit.py` | Requests per login. |
| `worker.py` | Each hand-off with no realization and no pull request is built on its own worktree and branch, and the worker opens the pull request. The commit names the hand-off in a `Codoc-Handoff` trailer, so the daemon's pass over the merged commit cites it. |
| `realize_agent.py`, `sandbox.py`, `consult.py` | The sandboxed agent the worker runs: no shell, paths confined to the worktree, edits confined to the feature's bound files, secrets unreadable, tokens scrubbed, a scope check after the run, an allowlist for Consult URLs. |
| `static.py`, `tunnel.py` | Where the bundle is; a Cloudflare tunnel. |

## 4. Environment variables

| variable | read by | effect |
|---|---|---|
| `CODOC_PROVIDER` | `llm/provider.py` | `openai`, `anthropic`, `ollama` or `claude`. Unset: `openai` when `OPENAI_API_KEY` is set, else `anthropic` when `ANTHROPIC_API_KEY` is set, else `claude` (the Claude Code login). |
| `CODOC_MODEL`, `CODOC_MODEL_FAST`, `CODOC_BASE_URL`, `CODOC_TEMPERATURE`, `CODOC_MAX_TOKENS`, `CODOC_REASONING_EFFORT`, `CODOC_VERBOSITY`, `CODOC_LLM_TIMEOUT` | `llm/provider.py` | The model and its call settings. |
| `CODOC_LOG_PROMPTS` | `llm/provider.py` | `1` logs every prompt. |
| `CODOC_LANCE_PATH`, `CODOC_INDEX_RETENTION_S` | `index/` | The chunk table's location; how long old index versions are kept. |
| `CODOC_DOC_LANGUAGE` | `doclang.py` | Overrides `doc_language` for one process. |
| `CODOC_AGENT` | `mcp/tools.py` | The coding agent the MCP server writes as, `agent:<name>`; `claude-code` when unset. |
| `CODOC_REALIZE_OWNER` | `daemon/publish.py` | Marks the realize pass that holds the lease, so its own agent does not stand down. |
| `CODOC_GITHUB_CLIENT_ID`, `CODOC_GITHUB_CLIENT_SECRET`, `CODOC_GITHUB_TOKEN`, `CODOC_SERVE_REPO`, `CODOC_GITHUB_API`, `CODOC_GITHUB_WEB` | `serve/` | GitHub sign-in and permission checks. |
| `CODOC_SERVE_PR`, `CODOC_SERVE_BASE` | `cli/main.py` | `0` turns off the pull-request worker, as `codoc serve --no-prs` does; the base branch (`main`). |
| `CODOC_CONSULT_ALLOWLIST`, `CODOC_STATIC_DIR`, `CODOC_TUNNEL_CONFIG` | `serve/` | Consult URLs the hub's agent may fetch; the bundle; the tunnel config. |
