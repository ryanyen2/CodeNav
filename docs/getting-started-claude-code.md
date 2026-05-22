# Getting Started — codoc × Claude Code

This guide walks you from a fresh repo to a live integration where:

- **Claude Code** edits files in your session using its own credentials.
- **codoc** observes every `Edit` / `Write` / `MultiEdit` / `Read` in real time.
- The VSCode status bar shows which proposals are staged and what the next action is.
- `git commit` records a semantic snapshot so codoc state is versioned 1:1 with git history.

You will run two processes: the codoc HTTP server and your `claude` CLI session. The VSCode extension is optional. All three talk over localhost — no credentials proxy, no cloud round-trip.

---

## 1. Prerequisites

- **Python 3.11+**
- **git** (codoc installs post-commit + pre-commit hooks automatically)
- **Claude Code CLI** — install per https://docs.claude.com/en/docs/claude-code (codoc never sees your API key)
- An `OPENAI_API_KEY` (or compatible base URL) for codoc-side LLM calls. codoc uses `gpt-5.4-mini` by default; override with `CODOC_MODEL`.

```bash
export OPENAI_API_KEY=sk-...
```

## 2. Install codoc

```bash
git clone <your-fork-or-this-repo>
cd CodeNav
uv pip install -e .
```

Verify:

```bash
codoc --help
codoc server --port 8001    # leave this running in a separate shell
```

## 3. Initialise your project — one command

```bash
cd ~/code/my-project
codoc sync
```

`codoc sync` is state-aware: it reads the current `.codoc/` state and performs the minimum work needed. On a fresh repo it does everything automatically:

1. Creates `.codoc/` and installs git hooks (pre-commit + post-commit).
2. Clusters your codebase and proposes a feature tree (bootstrap).
3. Renders `.codoc/tree/_index.codoc` — the human-readable projection.
4. Prints a summary and exits with the next action.

```
  → initialized .codoc/
  → installed git hooks
  → bootstrap: 312 chunks → 47 proposals
  → rendered .codoc/tree/_index.codoc

codoc: 47 proposals pending — run `codoc proposals` to review or re-run `codoc sync --yes` to accept all
```

Re-running `codoc sync` on an already-initialised repo is safe — it detects the stage and only runs what's needed (reflect for new commits, re-render if the tree is stale, nothing at all if everything is in sync).

### Stage detection

`codoc sync` always knows where you are:

| Stage | Meaning |
|---|---|
| `uninit` | No `.codoc/` yet |
| `needs-bootstrap` | `.codoc/` exists but no features yet |
| `bootstrap-review` | Bootstrap proposals awaiting your review |
| `proposals-pending` | Reflective or planning proposals in the queue |
| `stale-render` | DB moved ahead of the last rendered tree |
| `clean` | Everything in sync |

### Review proposals

```bash
codoc proposals          # list pending
codoc show auth/login    # inspect a specific feature
codoc accept auth/login  # accept one proposal
codoc accept --all       # accept everything at once (or: codoc sync --yes)
codoc reject auth/login  # discard a proposal
```

After accepting, `.codoc/codoc.db` holds the canonical feature tree and `.codoc/tree/_index.codoc` is re-rendered automatically. Commit the `.codoc/` directory to version-control it.

## 4. Git-like mental model

codoc tracks your semantic intent the way git tracks your code. The analogy is exact:

| git | codoc |
|---|---|
| Working tree | source code + `.codoc/tree/_index.codoc` |
| Staging area | pending proposals (`proposal=1` in the DB) |
| `git commit` | `codoc accept` — flips proposal to canonical |
| HEAD | latest accepted transaction HLC |
| History | append-only `transactions` table + `log.jsonl` |
| `git diff <ref>` | `codoc diff HEAD~1` |
| `git log` SHA | `SNAPSHOT` transaction written by the post-commit hook |

Every `git commit` automatically writes a `SNAPSHOT` transaction containing the git SHA + the codoc HEAD HLC at that moment, so you can always ask *"what did the feature tree look like at commit abc1234?"*:

```bash
codoc diff abc1234    # or: codoc diff HEAD~3
```

## 5. Install the Claude Code plugin

The plugin ships hooks (`PreToolUse` + `PostToolUse`) that POST to `http://localhost:8001/claude-code/event`, plus an MCP stdio server so Claude can call codoc tools mid-session.

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

The MCP server also registers seven tools Claude can invoke autonomously (`list_pending_proposals`, `accept_proposal`, `reject_proposal`, `show_feature`, `find_feature_owning_symbol`, `get_feature_tree`, `reflect_now`).

## 6. Optional — VSCode extension

The `vscode-codoc/` extension subscribes to the codoc SSE stream, decorates editors when Claude is active, and lets you accept/reject proposals directly in the rendered tree file.

```bash
cd $CODOC_REPO/vscode-codoc
npm install
npm run build
# Then F5 in VSCode, or "Install from VSIX" against the build output.
```

Open your project in this extension host. You should see:

- **Status bar** (left): `$(check) codoc: 66` (feature count when clean) or `$(bell) codoc: 3` (pending proposals). Click it to run **codoc: Sync**.
- **Stage-aware tooltip**: hover the status bar to see the current stage and next-action hint.
- **`_index.codoc` IS the UI**: open it with `Cmd+K Cmd+C` or `codoc: Open`. Proposals appear inline with `? ` prefix. CodeLens above each proposal line: **Accept / Edit & Accept / Reject**.
- **Live gutter pulse**: when Claude edits a file → blue gutter dot appears.
- **FileSystemWatcher**: whenever the server re-renders the tree (e.g. after a reflect), the open `_index.codoc` buffer updates automatically.

### Key commands

| Command | Shortcut / trigger |
|---|---|
| `codoc: Sync` | Status bar click |
| `codoc: Open` | `Cmd+K Cmd+C` |
| `codoc: Accept proposal` | `Cmd+Enter` on a proposal line in `.codoc` file |
| `codoc: Reject proposal` | `Cmd+Shift+Backspace` on a proposal line |
| `codoc: Accept all proposals` | Command Palette |
| `codoc: Render (hard refresh)` | Command Palette |

## 7. End-to-end smoke test

Start the server and open a Claude Code session inside your project:

```bash
codoc server --port 8001   # shell 1
claude --plugin-dir "$CODOC_REPO/codoc-plugin"  # shell 2
```

In Claude:
```
> rename function `login` to `authenticate` in src/auth.py
```

You should observe, in roughly this order:

1. **Within ~50ms**: `GET /claude-code/activity` shows the file + the feature(s) bound to it.
2. **VSCode** (if installed): gutter pulse appears; status bar shows the active file.
3. **Within ~1s** of Claude finishing the edit: `_index.codoc` gains new `~` diff hunks (the staged proposal). The FileSystemWatcher refreshes the buffer automatically.
4. `codoc proposals` lists a fresh `RENAME_INFER` proposal authored `claude-code`.

You can accept it now:

```bash
codoc accept <hlc-prefix>    # accept by HLC prefix shown in `codoc proposals`
# or in VSCode: Cmd+Enter on the proposal line in _index.codoc
```

Or wait for the pre-commit gate:

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

After the commit succeeds, the post-commit hook automatically:
- Runs `reflect` for the new commit (detects changed chunks, emits proposals).
- Writes a `SNAPSHOT` transaction referencing the git SHA.

In CI or any non-TTY context, the gate degrades to a soft warning and exits 0.

## 8. Three-tier mental model

| Tier | Where | Lifetime | What it represents |
|---|---|---|---|
| **0 — Live activity** | In-memory ledger | 30s TTL | "Claude is currently editing `src/auth.py`" — drives gutter + status bar |
| **1 — Pending proposal** | SQLite, `proposal=True` | Until accept/reject | Semantic delta from the reflective/planning pipeline |
| **2 — Accepted** | SQLite, `proposal=False` | Permanent (append-only) | Canonical state — what `codoc list` shows |

Tier 0 → Tier 1 happens automatically via the debouncer (750ms after the last edit). Tier 1 → Tier 2 is always a deliberate user action (or a `/codoc-accept` from Claude inside a session).

## 9. Minimal CLI surface

```
codoc sync [--yes] [--from-ref REF]   # one-verb entry point
codoc proposals                        # list pending
codoc accept <slug-or-prefix>          # accept one
codoc reject <slug-or-prefix>          # reject one
codoc show <slug-path>                 # inspect a feature
codoc list                             # browse the tree
codoc search <term>                    # fuzzy search slug/intent
codoc edit <slug-path> --intent "..."  # amend intent prose
codoc rename <slug-path> <new-slug>    # rename a feature
codoc retire <slug-path>               # mark a feature inactive
codoc status                           # feature count, pending, last HLC
codoc diff <ref>                       # semantic diff vs. a git SHA or HLC
codoc plan "<prompt>"                  # planning agent → propose tree changes
codoc server [--port 8001]             # start the FastAPI server
codoc gate-run [--report]              # validation gate metrics
```

## 10. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `codoc: offline` in status bar | `codoc server --port 8001` isn't running. Start it; the bar auto-reconnects. |
| Plugin slash commands missing | `claude --plugin-dir` path is wrong, or you didn't restart `claude`. |
| Gutter pulse never appears | VSCode extension can't reach `/events/stream`. Check the status bar tooltip. |
| Proposals appear minutes late | Debouncer is working (750ms window) but the LLM call took long. Tail the server logs. |
| Status bar stuck at `stale` | Re-render manually: Command Palette → `codoc: Render (hard refresh)`, or run `codoc sync`. |
| Every commit blocks on the gate | Use `c` to proceed without acting, or `codoc accept --all` upfront. |

To skip the gate for a single commit:

```bash
git commit --no-verify -m "..."
```

## 11. What's next

- Open `.codoc/tree/_index.codoc` and browse your feature tree.
- Run `codoc diff HEAD~1` after a coding session to see what changed semantically.
- Run `codoc gate-run --report` to inspect proposal quality (accept-verbatim %, light-edit median).
- Edit a feature's intent: `codoc edit auth/login --intent "the canonical login flow"`.

When in doubt, `codoc status` and the server logs are the two best signals.
