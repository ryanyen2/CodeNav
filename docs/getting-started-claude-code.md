# Getting Started — codoc × Claude Code

This guide walks you from a fresh repo to a live integration where:

- **Claude Code** edits files in your session using its own credentials.
- **codoc** observes every `Edit` / `Write` / `MultiEdit` / `Read` in real time.
- The VSCode tree shows which features Claude is touching (live gutter pulse).
- `git commit` becomes the moment you also commit to the staged semantic deltas.

You will run three processes: the codoc HTTP server, your `claude` CLI session, and (optionally) the VSCode extension. They talk to each other over localhost — no credentials proxy, no cloud round-trip.

---

## 1. Prerequisites

- **Python 3.11+**
- **git** (codoc uses post-commit + pre-commit hooks)
- **Claude Code CLI** — install per https://docs.claude.com/en/docs/claude-code (codoc never sees your API key)
- An `OPENAI_API_KEY` (or compatible base URL) for codoc-side LLM calls. codoc-side uses `gpt-5.4-mini` by default; you can change with `CODOC_MODEL`.

```bash
export OPENAI_API_KEY=sk-...
```

## 2. Install codoc

```bash
git clone <your-fork-or-this-repo>
cd CodeNav
pip install -e .
```

Verify:

```bash
codoc --help
codoc server --port 8001    # leave this running
```

The server logs every Claude Code hook event it receives. Tail it if anything looks off.

## 3. Initialise your project

In the repo you want codoc to track:

```bash
cd ~/code/my-project
codoc init                  # creates .codoc/ + installs git hooks
codoc bootstrap             # clusters the codebase, proposes a feature tree
codoc proposals             # review what bootstrap suggested
codoc accept --all          # accept the bootstrap proposals
```

After this, `.codoc/codoc.db` holds the feature tree and `.codoc/tree/` contains the human-readable projection. `git status` should show the `.codoc/` directory as new — commit it.

## 4. Install the Claude Code plugin

The plugin ships hooks (`PreToolUse` + `PostToolUse`) that POST to `http://localhost:8001/claude-code/event`, plus an MCP stdio server so Claude can call codoc tools mid-session.

For local development:

```bash
# Path to this repo, not to your project
export CODOC_REPO=/path/to/CodeNav

claude --plugin-dir "$CODOC_REPO/codoc-plugin"
```

To confirm the plugin loaded, type `/help` inside Claude — you should see four slash commands:

| Command | What it does |
|---|---|
| `/codoc-status` | Show last HLC, pending proposal count |
| `/codoc-proposals` | List pending proposals |
| `/codoc-accept <slug>` | Accept a proposal |
| `/codoc-reject <slug>` | Reject a proposal |

And the MCP server registers seven tools that Claude can invoke autonomously (`list_pending_proposals`, `accept_proposal`, `reject_proposal`, `show_feature`, `find_feature_owning_symbol`, `get_feature_tree`, `reflect_now`).

## 5. Optional — VSCode extension

The `vscode-codoc/` extension subscribes to the codoc SSE stream and decorates editors when Claude is active.

```bash
cd $CODOC_REPO/vscode-codoc
npm install
npm run build
# Then F5 in VSCode, or "Install from VSIX" against the build output.
```

Open your project in this extension host. You should see:

- **Status bar**: `codoc: server ✓` (means SSE stream is connected).
- **Tree view**: the live feature tree.
- When Claude edits a file → blue gutter dot + status bar `⟳ Claude: Edit src/auth.py [auth-login]`.

## 6. End-to-end smoke test

In a Claude Code session inside your project:

```
> rename function `login` to `authenticate` in src/auth.py
```

You should observe, in roughly this order:

1. **Within ~50ms**: `GET /claude-code/activity` shows the file + the feature(s) bound to it.
2. **VSCode** (if installed): gutter pulse + status-bar entry appear.
3. **Within ~1s** of Claude finishing the edit: `_index.codoc` gains new `~` diff hunks (the staged proposal).
4. `codoc proposals` lists a fresh `RENAME_INFER` (or similar) proposal authored `claude-code`.

You can accept it now, or wait for the pre-commit gate:

```bash
git add . && git commit -m "rename login → authenticate"
```

The pre-commit hook prints the pending proposals touching staged files and prompts:

```
  [a] accept all   [r] reject all   [c] continue anyway   [q] quit (abort commit)
```

- `a` → POST `/tx/accept-all`, commit proceeds with proposals promoted to canonical state.
- `r` → POST `/tx/reject-all`, commit proceeds with proposals discarded.
- `c` → commit proceeds, proposals stay pending.
- `q` → commit aborts (exit 1).

In CI or any non-TTY context, the gate degrades to a soft warning and exits 0.

## 7. Three-tier mental model

codoc keeps three layers of state at all times. Understanding which tier you're looking at clears up most confusion:

| Tier | Where | Lifetime | What it represents |
|---|---|---|---|
| **0 — Live activity** | In-memory ledger | 30s TTL | "Claude is currently editing `src/auth.py`" — drives gutter + status bar |
| **1 — Pending proposal** | SQLite, `proposal=True` | Until accept/reject | Semantic delta proposed by the reflective pipeline (rename, absorb, evict, …) |
| **2 — Accepted** | SQLite, `proposal=False` | Permanent | Canonical state — what `codoc list` shows |

Tier 0 → Tier 1 happens automatically via the debouncer (750ms after the last edit). Tier 1 → Tier 2 is always a deliberate user action (or a `/codoc-accept` from Claude).

## 8. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `codoc: server not reachable` during commit | `codoc server --port 8001` isn't running. Start it; commit again. |
| Plugin slash commands missing | `claude --plugin-dir` path is wrong, or you didn't restart `claude`. |
| Gutter pulse never appears | VSCode extension can't reach `/events/stream`. Check the status bar — if it shows `codoc: server ✗`, hit the server URL in a browser to verify. |
| Proposals show up minutes late | Debouncer is working as intended (750ms window) but the OpenAI call took long. Tail the codoc server logs. |
| Every commit blocks on the gate | You're not accepting/rejecting before committing. Use `c` to proceed without action, or `codoc accept --all` upfront. |

To skip the gate entirely for a single commit:

```bash
git commit --no-verify -m "..."
```

## 9. What's next

- Read **`docs/how-codoc-works.html`** for the deep dive on the data model and reflective pipeline.
- Run `codoc gate-run --report` after a typical session to inspect the validation gate (accept-verbatim %, light-edit median).
- Edit features by slug: `codoc edit auth/login --intent "the canonical login flow"`.
- Browse the live tree: `codoc list`.

When in doubt, the server logs and `codoc status` are the two best signals.
