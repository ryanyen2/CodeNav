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
| `apply.py` | `apply(store, op)`: the handlers and their inverses. |
| `verdict.py` | `plan.accept`, `plan.reject` and `plan.reopen`: the steps a verdict takes, computed from the plan's ops. |
| `ingest.py` | `ingest` and `block_bodies`. |
| `inline_md.py`, `block_md.py` | The Markdown form, inline and block. |
| `scripts.py` | Which characters belong to which writing script, and whether it spaces its words. |
| `derive.py` | The views: description, attribution, ink, ground, plan claims, phase, history, projection, and `tree.codoc`. |
| `window.py` | The timeline window in `history.json`. |
| **`workspace/`** | The `.codoc` directory. |
| `layout.py` | File names, `.codoc/.gitignore`, atomic write, tolerant read, `flock`. |
| `config.py` | `config.json`. |
| `clock.py` | The hybrid logical clock. |
| `log.py` | Session files: reading and checking them, replay order, the writer. |
| `inbox.py` | The client inbox and acks, drain offsets, inbox name to actor, what a submission may assert (`client_payload`, `resolve_provisional`). |
| `epoch.py` | `activity.json`, the agent epoch: every write a locked read-modify-write. |
| `roles.py` | The role table and the policy `apply` asks. |
| `workspace.py` | `Workspace`: rebuild, `follow_disk` (a new session when `HEAD` moves or git takes the session file; a rebuild when any other log file changed), `submit`, `ingest` and the machine gate, `drain_inbox`. |
| `snapshot.py` | `codoc snapshot`. |
| `validate.py` | `codoc validate-logs`. |
| **`index/`** | The code, read without the document. |
| `lang/` | Tree-sitter adapters for Python and TypeScript: chunks and references. |
| `tree_walk.py` | A chunk's two identity hashes (`fingerprint`, `types_hash`). |
| `app.py`, `runner.py`, `schema.py`, `reader.py` | The cocoindex app and the LanceDB chunk table. |
| `changes.py` | The change set of one index update. |
| `extract.py`, `graph.py`, `flows.py` | The dependency graph over chunks, cached as JSON. |
| `git.py` | `HEAD` (commit and branch), the hand-offs commit trailers name, and what recent commits said about a file. |
| **`doclang.py`** | The authoring language: its profiles (tags, the prompt directive) and the script-aware text helpers (detection, title keys, character budgets). |
| **`llm/`** | `provider.py` (which model, `complete()`, the `.env` fallback, `spent()`: what the calls reported they cost), `prompt.py` (templates in `codoc/prompts/`, JSON out), `passes.py` (one function per prompt). |
| **`kinds/`** | The kind registry, the shipped kinds, `write.py` (content to ops), `lift.py` (the lift pass), `fetch_guard.py`. See [kinds.md](kinds.md). |
| **`loops/`** | |
| `context.py` | What a pass or an agent read is shown: the ego graph over bindings, the title outline. |
| `edits.py` | Tree edits (`attach`, `detach`, `add_node`, `amend`, `move_node`, `delete_node`) compiled to op bodies and ingests. Writes nothing. |
| `bootstrap.py`, `reflect.py`, `realize.py`, `translate.py` | The loops in [workflows.md](workflows.md). |
| **`daemon/`** | |
| `daemon.py` | `codoc watch`: the per-batch order in [concurrency.md](concurrency.md#4-a-submissions-path). |
| `client.py` | How another process writes: the inbox when a daemon is alive, else the applier under `applier.lock`. |
| `reads.py` | What the CLI and the MCP tools read: `queue_status`, `read_history`, `doc_language_block`. |
| `publish.py` | The derived files and the realizing lease in `status.json`. |
| `pidfile.py` | `watch.pid`. |
| **`claude/`** | Section 2. |
| **`cli/main.py`** | The `codoc` command. Without `--root` it finds `.codoc` in the current directory or the nearest one above it, as the hooks and the MCP server do; an explicit `--root` is taken as given. `codoc init` makes its workspace at `--root` (the current directory by default) and never walks up: a package in a monorepo may hold its own. |
| **`mcp/`** | `server.py` (FastMCP over stdio, `codoc-mcp`), `tools.py`. |
| **`serve/`** | Section 3. |

## 2. The coding agent

`codoc init` and `codoc install-hooks` install the files in `codoc/claude/plugin/`. The
hooks name this machine's interpreter, so they go in the untracked
`.claude/settings.local.json`, with the permission rule `mcp__codoc__*` (without it a
headless `claude -p` is denied every codoc tool). The MCP server is registered at Claude
Code's local scope with `claude mcp add --scope local` as `<interpreter> -m
codoc.mcp.server`, the interpreter the hooks run; when `claude` is not on PATH the install
prints that command to run later. When that interpreter, started in the project root
without `PYTHONPATH`, would import a different codoc than the one installing (codoc run
from a checkout beside another codoc in the same environment), the hook commands and the
server's registration set `PYTHONPATH` to the directory holding the installing one. The `codoc-intent` skill and the `/codoc:realize`,
`/codoc:plan` and `/codoc:ask` commands are copied into `.claude/`, and an installed
`/codoc:*` command the plugin no longer ships is removed. Codoc's own entries in a
tracked `.claude/settings.json` or `.mcp.json`, where an older install put them, are
removed, the server only once the local registration succeeds. A settings file that does
not parse, or holds the wrong type where codoc writes, stops the install before it writes
anything; a null counts as absent. Each person runs `codoc install-hooks` once after
cloning.

**Hooks** (`claude/hook.py`, events `session-start`, `user-prompt`, `pre-tool`,
`post-tool`, `stop`, `session-end`):

- Open the agent epoch in `activity.json` while a turn runs and record the files it
  touched. The daemon defers those files and reflects them when the epoch closes, or after
  900 seconds of silence (its quiet tick notices that with no file event), citing every
  queued hand-off in `caused_by`. The editor and the
  hub do not read the file: they learn that a feature is being built from its
  `realizing` phase in the projection.
- Tell the session when hand-offs are queued in `realize.md`.
- At `stop`, when the turn's MCP calls cite some queued hand-offs but not all, block the stop
  once and name the rest, so a hand-off built but not reported is reported or explained. A
  turn that never touched the queue just ends. The MCP server records each hand-off a
  call cites in the epoch's `cited` list (`epoch.note_cited`), daemon or not, once an op
  of the call has landed (applied or planned), so a call that errored cites nothing. The
  epoch's open resets it. Every writer of `activity.json` (the hooks, the MCP server, an
  SDK realize pass) goes through the locked functions in `workspace/epoch.py`, so none drops
  another's write.
- At `stop` with no daemon alive and something queued, start a detached
  `python -m codoc.daemon.client` that settles what could not settle while the epoch was open, and exits
  ([concurrency.md](concurrency.md#1-processes)).
- The MCP tools take a hand-off as the queue shows it (`d:<op id>`) or as the op id.
- Never raise and never open the workspace; they read published files only.

**MCP tools.** Reads replay the logs into a read-only workspace. Writes go through
`daemon/client.py` with the inbox name `agent-<CODOC_AGENT>`, so the applier stamps
`agent:<CODOC_AGENT>`, `agent:claude-code` when the variable is unset. Another agent
(Cursor, Codex) sets `CODOC_AGENT` in the server's environment.

**How a hand-off settles.** When no agent epoch is open, the applier (the daemon at the
end of each batch and after each reflect pass, a daemonless writer after it applies, or
the process the Stop hook starts when no daemon runs) writes `feature.realized` for each
queued feature once an applied binding op cites its latest hand-off in `caused_by`
(`loops/realize.py`, `settle`), whichever feature the op binds. An agent's `attach` is
compiled against the code on disk (`index/app.py`, `code_on_disk`): a new symbol is
attached, one the feature owns is refreshed when the build changed its code, and a
symbol the files do not define is refused
([concurrency.md](concurrency.md#10-hand-off-and-realization)). It does not matter who
wrote the op: an agent's `codoc_reflect`, the pass after a Claude Code epoch closes, or
the pass over a commit whose `Codoc-Handoff` trailer names the hand-off, read when
`HEAD` moves over it while on the branch its `Codoc-Base` trailer names. While an epoch
is open the build is in progress, so the feature stays
queued until the epoch closes; `codoc_status` lists a hand-off the epoch's `cited` names
under `reported_list`, and its state is `reported` when every queued hand-off is.
Code written by hand settles with `codoc done`.

| tool | does |
|---|---|
| `codoc_context` | Read: with `files`, the features bound in them, one hop along the code graph, their parents and children; with `feature_id`, that feature, the features under it and the features owning a symbol one call, import or inheritance away from their code, in either direction. |
| `codoc_tree`, `codoc_status`, `codoc_history` | Read: the tree and its open plans; the queue of hand-offs, phases and the number of open plans; one feature's changes in log order, one row per change (a gesture, a reflect pass, an amend, a plan's opening, a verdict; its ops with `ops`) with a summary, and attribution. A writer gives each change one batch: a reflect pass writes its trunk ops under one, `Workspace.amend` writes all of an amend under one, and a later amend that joins an open plan writes under its own, not the plan's. |
| `codoc_reflect` | Tree edits. `attach`, `detach` and a rewording `amend` land on the trunk (an `amend` through the machine gate); anything else becomes one plan. An `attach` or `add_node` binds only symbols the files on disk define, hashed as the index hashes them (`index.app.code_on_disk`), so code written during the turn binds at once, and refuses any other. An `attach` of a symbol the feature already owns writes a `bind.refresh` citing the hand-off when that code changed since the hand-off, and nothing otherwise. |
| `codoc_propose` | Tree edits as one plan. |
| `codoc_write_block` | Content for a kind block ([kinds.md](kinds.md#4-how-content-becomes-ops)). |
| `codoc_realize_progress` | Renews the realizing lease with "building n of m", as the MCP server's own lease (`CODOC_REALIZE_OWNER`), so the agent's status does not read it as another pass. |

Every edit is compiled against the current tree before anything is written; if one is
refused, nothing is written.

**Realize engines.** `codoc realize` and `codoc watch --auto-realize` start a pass through
`claude/autorealize.py`'s `start_realize`: `/codoc:realize` through the Claude Agent SDK
(`claude/sdk_realize.py`, the `sdk` extra) or `claude -p` (`--engine cli`), with
`--permission-mode` (`acceptEdits` by default) for either. It starts the child, takes the
realizing lease with the child's pid, which keeps the lease while the child lives, and names
the child its owner in `CODOC_REALIZE_OWNER`. The cli engine's child is `python -m
codoc.claude.autorealize`, which runs `claude -p --output-format json` and prints the
agent's last message. Either engine ends with one line: how the pass ended, its time and
its cost. `--auto-realize` (`auto_realizer`) starts a pass when no session is live and the
queue holds a hand-off no finished pass was started for, and backs off after a pass that
failed (`FAILED_BACKOFF_S`, doubled per failure in a row, at most an hour), counted from
when the pass ended: each engine's child records its pid, exit code and wall time in
`.codoc/cache/realize-ended.json` as it exits (`record_end`). The daemon's quiet tick
(`TICK_MS`, an empty batch after 30 seconds with no file event) runs `after_batch`, so
the retry needs no file event.

**Commands.** `/codoc:realize` builds the queue (`state` decides what it does, from
`codoc_status`); `/codoc:plan <task>` proposes the change with `codoc_propose` and stops;
`/codoc:ask <question>` answers in chat as a numbered reading path over existing features,
read with `codoc_tree` and `codoc_context`, and writes nothing.

## 3. The hub

`codoc serve` serves the editor bundle to people signed in with GitHub
([serve-deployment.md](serve-deployment.md)).

| module | role |
|---|---|
| `supervise.py` | Claims the repository with `.codoc/serve.lock` and supervises one daemon. |
| `app.py` | Routes: GitHub sign-in, `/api/whoami`, `/api/events` (SSE), `/api/message` (a `submit` appends to `inbox/hub-<login>.jsonl`; `set-language` writes the normalized tag for a maintainer; `realize` and `open-cite` are refused with a reason), `/api/media/<name>`, the page. |
| `push.py` | The SSE channel: `projection`, `status` (with `terminal: false`, so the page offers no Build now), this viewer's `acks`. |
| `auth.py`, `github_auth.py` | Sessions, and collaborator permission to role, written to `config.json`. Re-checked on a cadence. |
| `ratelimit.py` | Requests per login. |
| `worker.py` | Each hand-off with no realization and no pull request is built on its own worktree and branch, and the worker opens the pull request. The commit names the hand-off in a `Codoc-Handoff` trailer and the branch the pull request targets in a `Codoc-Base` trailer, so the pass of a daemon on that branch over the merged commit cites it. |
| `realize_agent.py`, `sandbox.py`, `consult.py` | The sandboxed agent the worker runs: no shell, paths confined to the worktree, edits confined to the feature's bound files, secrets unreadable, tokens scrubbed, a scope check after the run, an allowlist for Consult URLs. |
| `static.py`, `tunnel.py` | Where the bundle is; a Cloudflare tunnel. |

## 4. Environment variables

When `llm/provider.py` first resolves a provider in a process, it sets each variable the
environment leaves unset from the `.env` nearest the working directory (the editor's
setup writes the chosen provider there). It never overrides the shell, and never takes
`CODOC_BASE_URL`, `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL` or `CODOC_LOG_PROMPTS` from
the file.

| variable | read by | effect |
|---|---|---|
| `CODOC_PROVIDER` | `llm/provider.py` | `openai`, `anthropic`, `ollama` or `claude`. Unset: `openai` when `OPENAI_API_KEY` is set, else `anthropic` when `ANTHROPIC_API_KEY` is set, else `claude` (the Claude Code login). |
| `CODOC_MODEL`, `CODOC_MODEL_FAST`, `CODOC_BASE_URL`, `CODOC_TEMPERATURE`, `CODOC_MAX_TOKENS`, `CODOC_REASONING_EFFORT`, `CODOC_VERBOSITY`, `CODOC_LLM_TIMEOUT` | `llm/provider.py` | The model and its call settings. |
| `CODOC_LOG_PROMPTS` | `llm/provider.py` | `1` logs every prompt. |
| `CODOC_INDEX_RETENTION_S` | `index/runner.py` | How long a superseded version of the chunk table is kept (60 seconds). |
| `CODOC_DOC_LANGUAGE` | `doclang.py` | Overrides `doc_language` for one process. |
| `CODOC_AGENT` | `mcp/tools.py` | The coding agent the MCP server writes as, `agent:<name>`; `claude-code` when unset. |
| `CODOC_REALIZE_OWNER` | `daemon/publish.py` | Marks the realize pass that holds the lease, so its own agent does not stand down. |
| `CODOC_GITHUB_CLIENT_ID`, `CODOC_GITHUB_CLIENT_SECRET`, `CODOC_GITHUB_TOKEN`, `CODOC_SERVE_REPO`, `CODOC_GITHUB_API`, `CODOC_GITHUB_WEB` | `serve/` | GitHub sign-in and permission checks. |
| `CODOC_SERVE_PR`, `CODOC_SERVE_BASE` | `cli/main.py` | `0` turns off the pull-request worker, as `codoc serve --no-prs` does; the branch its pull requests target (default: the branch origin's `HEAD` names, else the branch checked out, else `main`). |
| `CODOC_CONSULT_ALLOWLIST`, `CODOC_STATIC_DIR`, `CODOC_TUNNEL_CONFIG` | `serve/` | Consult URLs the hub's agent may fetch; the bundle; the tunnel config. |
