# codoc

codoc maintains a **human-intent-level feature tree** synchronized to a codebase. Each node is a *feature*: a named unit of intent that binds to code chunks across many files. The tree is first-class authored intent — not LLM-derived. Code attribution is a secondary index kept in sync by a two-loop reflective pipeline.

## Two interaction flows

**Flow 1 — Bottom-up (code → tree):**
You (or an agent) change source files → codoc detects what moved → auto-applies safe updates (refresh bindings, small description tweaks, carry attribution across a move/rename) → surfaces structural proposals (add/move/retire nodes) in-situ in `tree.codoc` → you Accept/Reject via the VS Code CodeLens.

**Flow 2 — Top-down (tree → code):**
You (or Claude Code) edit or add features in `tree.codoc` → codoc builds a coding directive and **queues it for your live Claude Code session** in `.codoc/realize.md` (status `awaiting_impl`) → you run `/codoc:sync`, which implements the change and reflects it back into the tree. codoc never spawns a headless agent and never writes code itself; only edits that *request* code ("should…", "Add…", an accepted plan node) queue work — documenting existing code never triggers a build.

## How codoc and Claude Code work together

codoc ships as a **Claude Code plugin**. There is no server process and no port —
`codoc init` wires four file-based integration surfaces into your repo:

| Surface | Installed to | Role |
|---|---|---|
| **MCP server** (`codoc`) | `.mcp.json` → `codoc-mcp` (FastMCP, stdio) | The agent's reflection API: `codoc_tree`, `codoc_reflect`, `codoc_propose_{add,amend,move,retire}`, `codoc_attach`, `codoc_plan_add`. The agent carries real intent straight into the store instead of leaving it to a blind index-diff. |
| **Slash commands** | `.claude/commands/codoc/` | `/codoc:plan <task>` — propose plan nodes *before* writing code; `/codoc:sync` — reconcile whichever side is behind (implements queued directives, drains tree edits, or reflects code drift). |
| **Skill** `codoc-intent` | `.claude/skills/codoc-intent/SKILL.md` | Auto-loaded every session; teaches Claude the MCP-first propose-then-implement workflow for this repo. |
| **Hooks** | `.claude/settings.json` | `SessionStart` · `Stop` · `PreToolUse` · `PostToolUse` · `UserPromptSubmit` — maintain `.codoc/activity.json` (the live agent touch-log → VS Code gutter decorations), run recovery reflection when the session stops, and nudge `/codoc:sync` when work is queued. Fire-and-forget; they never block the agent. |

Claude is the **planner and the implementer**; codoc is the bookkeeper that keeps
intent and code aligned. It never runs a headless model — the work always happens
in your interactive session, with your permissions.

**The propose-then-implement loop:**
1. You ask Claude Code to add or change a feature.
2. Claude plans it — `/codoc:plan` (or a `codoc_plan_add` / `codoc_propose_*` MCP
   call) — and a proposal appears in-situ in `tree.codoc`. No code is touched yet.
3. You review the description and **Accept** in VS Code → verdict → `.codoc/inbox.json`.
4. **Loop B** applies the op, builds a directive from the accepted intent + any
   bound symbols, writes it to `.codoc/realize.md`, and sets status `awaiting_impl`.
5. Your session runs **`/codoc:sync`** — reads the directive, writes the code,
   calls `codoc_reflect` to bind it, and deletes the file.
6. The `Stop`-hook reflection (or `codoc watch`'s Loop A) reflects the written code
   back into the tree, refining it if the implementation revealed more than the
   description captured.

## Flow 3 — Remote suggestions via the deployed hub (`codoc serve`)

The first two flows are local — you, in your editor, on your machine. The
**deployed hub** opens the tree to *remote* collaborators **without giving up the
local, file-based model**: it serves the same editor as a web app **from your own
always-on machine** (reached over a tunnel — no cloud holds your repo, keys, or
agent).

How it works, end to end:

1. You run `codoc serve` on your machine and share the GitHub-authorized link. The
   hub is a *separate process* that supervises your daemon and is a file-channel
   client — it reads `.codoc/*` to render the browser UI and writes only the
   verdict/draft channels, **never** `tree.codoc`.
2. A collaborator opens the link and signs in with GitHub. Their repo-collaborator
   permission sets what they can do: **read → suggest** (suggest edits, comment),
   **write → hand-off** (also accept and hand work to the agent). Non-collaborators
   are denied.
3. They edit intent in the browser. Code-implying edits are **held by default** —
   nothing touches your repo or spends budget. They watch suggestion status live
   over an SSE channel (an offline edit is queued and syncs on reconnect).
4. You (or any write-collaborator) **hand off** an accepted suggestion. Only then
   does the hub realize it: on an isolated **git worktree**, with the agent in an
   enforced sandbox (no token, can't read secrets or touch CI/settings), opening a
   **code PR** — never a push to `main`.
5. After the PR merges, the daemon re-indexes and everyone's tree catches up.

This is **Tier 1** (async suggest → hand-off → PR). Real-time co-editing is a
planned fast-follow. Setup, the GitHub App, and the Cloudflare/Tailscale tunnel
are in [`docs/serve-deployment.md`](docs/serve-deployment.md); the module map is in
[`docs/architecture.md`](docs/architecture.md).

## Requirements

- The **codoc VS Code extension** (it provisions everything else for you).
- **Claude Code** — used both for the interactive plan/implement workflow *and*,
  by default, as the engine for codoc's own reflection calls (so no separate LLM
  key is needed). An OpenAI key is the fallback when Claude Code isn't present.

Everything below is auto-provisioned by the extension's one-click setup and does
not need manual installation: Python 3.11+ (via a `uv`-managed isolated env),
cocoindex + LanceDB (incremental vector index), tree-sitter (Python + TypeScript),
and SQLite WAL.

## Quick start (recommended: the VS Code extension)

1. Install the **codoc** VS Code extension.
2. Open your repo and run **“codoc: Set up codoc”** (the walkthrough offers it on
   first run, or via the `$(rocket) Set up codoc` status-bar item / command palette).

Setup is fully automatic and needs no terminal: it bootstraps `uv`, installs the
codoc core into an isolated environment, wires the Claude Code plugin (hooks + MCP
+ skill + slash commands), points codoc's reflection at your existing Claude
credentials (no API key prompt in the common case), runs `codoc init` to index the
repo and propose the initial tree, and starts the `codoc watch` daemon **for you**
— you never run or stop it by hand. (Setup requires a trusted workspace.)

### Advanced: the CLI (no IDE, or scripting)

```bash
uv tool install codoc            # or: pip install -e .

export CODOC_PROVIDER=claude     # reuse your Claude Code login (no separate key)
# …or the OpenAI path:
#   export CODOC_PROVIDER=openai
#   export CODOC_MODEL=gpt-5.4-mini
#   export OPENAI_API_KEY=sk-...

cd my-repo
codoc init        # index repo, propose initial tree, install the CC plugin
codoc watch       # run both loops as you edit code / tree.codoc
```

`codoc init` installs the Claude Code plugin (hooks + MCP server + skill + slash
commands) into the repo's `.claude/` and `.mcp.json`. It is idempotent — re-run it
(or **“codoc: Repair / re-run setup”** in the IDE) after a fresh clone to wire up
the integration on a new machine.

## Core commands

```bash
codoc init                # index repo + propose initial tree + install the CC plugin
codoc watch               # daemon: bidirectional sync as you work
codoc watch --dry         # reflect + apply tree edits, but don't queue realize directives
codoc watch --no-realize  # sync the tree but never queue directives for the session
codoc status              # feature count, pending proposals, code↔binding coverage
codoc sync                # one-shot (no daemon): apply tree edits (Loop B), then reflect code (Loop A)
codoc accept <e-id>       # CLI verdict path — mirrors the IDE Accept (then runs Loop B)
codoc reject <e-id>       # CLI verdict path — mirrors the IDE Reject
```

(`codoc reflect` runs a recovery-grade state reconciliation by hand, and
`codoc propose` authors a proposal from the shell — both exist mainly for scripts
and tests; in normal use the agent reflects via MCP and you Accept in the IDE.)

## The `tree.codoc` file

The only human surface. Located at `.codoc/tree.codoc`:

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

    Cites [session creation](codoc:auth.py#AuthManager.create_session).

  - Token rotation  ⟨f-7b1d04⟩
      Refreshes session tokens before expiry.

  ~ Legacy password auth  ⟨f-2c8b01⟩
      Deprecated in favour of OAuth.
```

**Markers:**
- `-` — live feature
- `~` — retired feature (struck-through in the IDE)

**IDs** (`⟨f-…⟩`) — stable feature identifiers written by the backend; hidden by
the VS Code extension decoration. Never edit them.

**Inline refs** — `[label](codoc:file.py#symbol)` markdown links cite code.
The parser extracts them; the IDE makes them clickable.

**Indentation** — 2 spaces per level; determines parent/child relationships.

**Proposals** render in-situ, at the tree position where the change would land:

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

+ - Rate limiting  ⟨e-9f01c2⟩
+     Caps API requests per user per minute.

  - Token rotation  ⟨f-7b1d04⟩
```

`+` add / `~` move·amend — these render as in-situ text hunks; RETIRE/AMEND on a
*live* node decorate it in place (strike / inline diff) rather than adding a line.
Accept or Reject using the VS Code CodeLens buttons — no text syntax to type.
Verdicts flow through `.codoc/inbox.json`; the loop applies them.

## `.codoc/` layout

```
.codoc/
  tree.codoc          — human-authored feature tree (commit with your code)
  tree.bindings.json  — IDE sidecar: feature↔symbol index + dependency edges + proposals + change feed (v4)
  status.json         — loop lifecycle: in_sync / code_drift / tree_dirty / awaiting_impl / realizing
  inbox.json          — verdict channel: Accept/Reject writes here, the loop drains it
  edits.json          — provenance/intent channel: settle authorship + live doc-ahead suggestions
  realize.md          — the realization queue: directives the live session implements via /codoc:sync
  realize.json        — machine-readable directive manifest (ids → features → causes)
  activity.json       — agent touch-log: the CC hooks write here, the VS Code extension reads it
  codoc.db            — features + bindings + event log (SQLite WAL)
  lancedb/            — cocoindex-managed chunk index: AST + embeddings + identity hashes
  cocoindex.db/       — cocoindex internal memoization (resumes interrupted indexing)
```

Commit `tree.codoc` (and optionally `codoc.db`) alongside source so the intent
map is versioned with the code.

## Architecture — two loops

**Loop A (code → codoc):** diff the chunk index → auto-apply safe ops (REFRESH,
ATTACH, DETACH, small AMEND) and carry attribution across moves/renames
deterministically → one LLM pass for anything that needs judgement → structural
ops (ADD/MOVE/RETIRE) become pending Events (proposals). A graph-neighbor coverage
net guarantees no added chunk is ever silently dropped.

**Loop B (codoc → code):** drain `inbox.json` verdicts → parse `tree.codoc`, diff
against the store → apply user edits immediately → for each edit that *requests*
code, build a directive and **queue it in `.codoc/realize.md` for the live session**
(status `awaiting_impl`). The session implements via `/codoc:sync`; the
`Stop`-hook reflection or `codoc watch`'s Loop A then closes the loop. Loop B never
writes code and never spawns a headless agent.

A single LLM pass with the full change set plus every existing node title prevents
duplicates. `UNIQUE(file, symbol_path)` in the store ensures a chunk binds to at
most one feature.

## Sidecar schema (v4)

```json
{
  "version": 4,
  "by_feature": { "f-id": [{"file": "path.py", "symbol": "path.py::Class.method"}] },
  "by_file":    { "path.py": [{"symbol": "...", "feature_id": "f-id", "feature_title": "Title"}] },
  "features":   { "f-id": {"title": "Title", "parent_id": null, "realized": true} },
  "feature_edges": { "f-id": [{"to": "f-other", "weight": 4, "kinds": ["call"]}] },
  "proposals":  { "by_feature": {"f-id": {"op": "amend", "event_id": "e-id", "actor": "…", "mode": "…", "caused_by": "…"}},
                  "by_event": {"e-id": {"op": "add_node", "title": "…"}} },
  "changes":    [{"event_id": "e-id", "at": "…", "kind": "amend", "feature_id": "f-id",
                  "actor": "agent", "mode": "auto", "caused_by": "d-…"}],
  "holds":      ["f-id"]
}
```

`feature_edges` aggregates `code_edges` (call/import) into feature-level coupling
(the IDE dims unrelated features when the cursor rests on a coupled node).
`features[].realized` drives the unrealized-placeholder decoration; `proposals`
drives the in-place retire/amend overlays + Accept/Reject on the live node.
v4's `changes` (the last ~50 applied events, newest first) drives the agent-pencil
re-stamp in the doc view; `holds` is the doc-wins hold set; proposal `caused_by`
drives the "↳ from your edit" cascade cue.

## Environment variables

| Var | Default | Description |
|---|---|---|
| `CODOC_PROVIDER` | `openai` | LLM provider for reflection: `claude` (reuse Claude Code's login via headless `claude -p`, no key), `openai`, or `ollama`. The extension sets `claude` by default. |
| `CODOC_MODEL` | `gpt-5.4-mini` | LLM model name (defaults to `sonnet` when `CODOC_PROVIDER=claude`) |
| `OPENAI_API_KEY` | — | OpenAI API key (only for `CODOC_PROVIDER=openai`) |
| `CODOC_BASE_URL` | — | Custom OpenAI-compatible base URL |
| `CODOC_TEMPERATURE` | `0.2` | LLM sampling temperature |
| `CODOC_MAX_TOKENS` | `16000` | LLM completion budget (reasoning models spend it on hidden reasoning too) |
| `CODOC_EMBEDDER_PROVIDER` | `sentence-transformers` | Embedder provider (`sentence-transformers` or `openai`) |
| `CODOC_EMBEDDER_MODEL` | `all-MiniLM-L6-v2` | Embedding model |
| `CODOC_LANCE_PATH` | `.codoc/lancedb` | LanceDB directory holding the `code_chunks` table |
| `COCOINDEX_DB` | `.codoc/cocoindex.db` | cocoindex memoization state (auto-set by the indexer) |
| `CODOC_LOG_PROMPTS` | — | Set to `1` to log LLM prompt+response to stderr |

## Tests

```bash
python3.11 -m pytest tests/
```

- `tests/` — deterministic unit + integration suites (store, loops, graph, parse/render, MCP, CLI).
- `tests/bdd/` — Given/When/Then userflows for the code↔tree round-trip: deterministic Loop A/B
  scenarios (injected LLM pass) plus a real-LLM end-to-end that prints a position report for manual
  inspection (`python -m tests.bdd.e2e_report`).
- The real-LLM / real-index tests (`tests/loop/test_end_to_end.py`, `tests/bdd/test_e2e_userflows.py`)
  skip automatically when no `OPENAI_API_KEY` is set.

Code fixtures: `tests/fixtures/` (self-contained Python + TypeScript samples for the
adapter tests) and `test/` (real-world corpora for bootstrap/E2E runs: `requests/`,
`altair/`, `nanochat/`, `small_python_repo/`).

---

See [docs/getting-started-claude-code.md](docs/getting-started-claude-code.md) for the full workflow guide and
[docs/how-codoc-works.html](docs/how-codoc-works.html) for the architectural deep-dive.
