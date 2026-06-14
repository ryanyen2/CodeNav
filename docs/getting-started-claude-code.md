# Getting Started — codoc × Claude Code

codoc keeps a **feature tree** — a small, navigable map of what your code is
*for* — in sync with the code itself, in both directions:

- **code → codoc:** when code changes, codoc detects what changed and makes the
  minimal tree update — attach new code to a node, tweak a description, add a
  child, reparent, retire, or carry attribution across a move/rename. Safe updates
  apply automatically; structural ones appear as reviewable proposals.
- **codoc → code:** when you (or Claude Code) edit the tree, codoc builds the
  matching coding directive and **queues it for your live Claude Code session** —
  you run `/codoc:sync` to implement it, then codoc re-reads the result to
  refine the tree if intent was under-specified. codoc never writes code itself
  and never runs a headless model.

There is **one file** you ever look at — `.codoc/tree.codoc`.

---

## 1. Install

**Recommended — the VS Code extension.** Install the **codoc** extension and run
**“codoc: Set up codoc”** (offered on first run, or via the `$(rocket) Set up codoc`
status-bar item). It provisions the Python core into an isolated `uv` environment,
points codoc's reflection at your existing **Claude Code** login (no API key
prompt), runs `codoc init`, and manages the `codoc watch` daemon for you. Skip
straight to §4 — the rest of §1–§3 describes what setup does under the hood (and
the manual CLI path for no-IDE / scripting use).

```bash
uv tool install codoc        # isolated, version-pinned; or: pip install -e .  (Python 3.11+)
export CODOC_PROVIDER=claude  # reuse your Claude Code login — no separate key
#   …or OpenAI:  export CODOC_PROVIDER=openai && export OPENAI_API_KEY=sk-…
codoc --help
```

## 2. Initialise

```bash
cd ~/code/my-project
codoc init
```

`codoc init` does three things:

1. **Indexes the repo** — AST chunks + embeddings via cocoindex/LanceDB.
   Incremental; a killed run resumes from the last completed file.
2. **Proposes a feature tree** and writes `.codoc/tree.codoc`.
3. **Installs the codoc Claude Code plugin** — hooks, the MCP server, the
   `codoc-intent` skill, and the `/codoc:plan` + `/codoc:sync` slash commands
   (see §3). No server, no port.

Open `tree.codoc` in VS Code. Each indented line is a feature:

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

  - Token rotation  ⟨f-7b1d04⟩
      Refreshes session tokens before expiry.

- Notification dispatch  ⟨f-1f88aa⟩
    Queues and flushes email + in-app notifications.
```

`⟨f-…⟩` is the node's stable id — codoc writes it; the VS Code extension hides
it. Indentation is the tree structure.

## 3. How Claude Code is wired in

codoc ships as a **Claude Code plugin** — hooks, an MCP server, a skill, and slash
commands, all installed into the repo by `codoc init`. There is no server process
and no port to configure; everything is file-based.

**Two roles for Claude:**

| Role | How invoked | What it does |
|---|---|---|
| Planning | You ask Claude Code a question; it loads the `codoc-intent` skill | Proposes changes to `tree.codoc` via the codoc MCP tools (`codoc_plan_add` / `codoc_propose_*`) or the `/codoc:plan` command. No code is touched yet. |
| Implementation | You run `/codoc:sync` in your session (nudged by a hook) | Reads the directive codoc queued, writes the code, calls `codoc_reflect` to bind it. Runs **in your interactive session, with your permissions** — never a headless `claude -p`. |

**The MCP server** (`codoc`, registered in `.mcp.json` as the `codoc-mcp` console
script, FastMCP over stdio) is the agent's primary reflection path. Instead of
relying on Loop A's blind index-diff, Claude calls tools that carry real intent
straight into the store:

- `codoc_tree` / `codoc_status` — read the current tree + lifecycle state.
- `codoc_propose_add` / `_amend` / `_move` / `_retire` — author a structural proposal.
- `codoc_attach` — bind code to an existing feature.
- `codoc_reflect` — bulk-reconcile after writing code.
- `codoc_plan_add` — author an unrealized **plan** node (realized only once code binds).

**The hooks** (auto-installed into `.claude/settings.json`):

```json
"hooks": {
  "SessionStart":     [{ "command": "python -m codoc.agent.hook session-start" }],
  "Stop":             [{ "command": "python -m codoc.agent.hook stop" }],
  "PreToolUse":       [{ "matcher": "Edit|Write|MultiEdit|Read",
                         "command": "python -m codoc.agent.hook pre-tool" }],
  "PostToolUse":      [{ "matcher": "Edit|Write|MultiEdit",
                         "command": "python -m codoc.agent.hook post-tool" }],
  "UserPromptSubmit": [{ "command": "python -m codoc.agent.hook user-prompt" }]
}
```

- `PreToolUse` / `PostToolUse` write `.codoc/activity.json` as Claude reads and
  modifies files — which files it touched, in which mode (read vs write), and which
  features those files belong to. The VS Code extension watches this file and shows
  live gutter markers in `tree.codoc` and file badges in Explorer.
- `Stop` runs a recovery-grade reflection (Loop A) on what the session changed, so
  the tree stays current even with no daemon running.
- `UserPromptSubmit` nudges you to run `/codoc:sync` when Loop B has queued work
  (`status = awaiting_impl`).

The hooks never block the agent; they're fire-and-forget with a short timeout.

**The skill** (auto-installed into `.claude/skills/codoc-intent/SKILL.md`):
Claude Code loads this automatically for every session in the repo. It instructs
Claude to (1) read `tree.codoc` + the sidecar to understand what exists, (2) propose
changes via the MCP tools / `/codoc:plan` before touching code, and (3) wait for
your Accept in the IDE.

## 4. The propose-then-implement loop

This is the full interactive flow:

### Step 1 — You ask Claude Code

```
You: Add rate limiting to the auth module — cap requests per user per minute.
```

### Step 2 — Claude proposes (no code touched)

Claude loads the skill, reads `tree.codoc`, and authors a plan proposal via the MCP
server (equivalently, the `/codoc:plan` command), e.g. `codoc_plan_add` with the
title, description, target parent, and intended binding. This creates an
`applied=False` event and re-renders `tree.codoc` with an in-situ proposal block at
the target position:

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

+ - Rate limiting  ⟨e-9f01c2⟩
+     Caps API requests per user per minute using a token-bucket per user_id.

  - Token rotation  ⟨f-7b1d04⟩
```

Claude then tells you: *"I've proposed Rate limiting as a codoc plan. Accept it in
VS Code to queue implementation."*

### Step 3 — You review and Accept

In VS Code, the green `+` block appears at the exact tree position. You can refine
the description before accepting — just edit the proposal text. When ready, click
**Accept** in the CodeLens.

### Step 4 — Loop B queues the work

Accepting writes a verdict to `.codoc/inbox.json`. Loop B (`codoc watch`, or the
next `codoc sync`) picks it up and:

1. Applies the op to the store (marks the event accepted).
2. Builds a coding directive from the accepted description + any bound symbols,
   scoped to the files the feature owns (`Edit only: …`).
3. Writes the directive to `.codoc/realize.md` and sets `status.json = awaiting_impl`.

Only edits that *request* code reach this step. A purely descriptive edit
("Holds brand colors and their dark-mode variants") just records intent and queues
nothing — documenting existing code never writes code.

### Step 5 — You run `/codoc:sync`

The `UserPromptSubmit` hook nudges you that work is queued. In your session:

```
/codoc:sync
```

This reads `.codoc/realize.md`, implements each directive **in your interactive
session** (with your normal permissions), calls `codoc_reflect` to bind the new
code to the accepted feature, and deletes the file.

### Step 6 — Loop A re-reflects

The `Stop`-hook reflection (or `codoc watch`'s Loop A) runs scoped to the files you
wrote:

- If the code matches the intent cleanly → bindings are updated, status returns to
  `in_sync`.
- If the implementation revealed something the description didn't capture (e.g. a
  helper that warrants its own node) → Loop A surfaces additional proposals as new
  in-situ hunks for your review.

This closes the loop: intent → plan → accept → queue → implement → reflect → refined tree.

## 5. Watch

```bash
codoc watch
```

One daemon runs both loops. Leave it running while you work. It reacts to both:
- **Code file changes** → Loop A re-checks affected bindings, surfaces proposals.
- **`tree.codoc` changes** and **`inbox.json` verdicts** → Loop B applies ops and,
  for code-implying edits, queues a directive in `.codoc/realize.md` for you to
  implement with `/codoc:sync`.

## 6. Reviewing proposals

Add/move proposals render in-situ at their target tree position; retire/amend
decorate the live node in place (strike / inline diff):

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

+ - Rate limiting  ⟨e-9f01c2⟩
+     Caps API requests per user per minute.

  - Token rotation  ⟨f-7b1d04⟩
```

| Op | Color | Meaning |
|---|---|---|
| `+` | green | add a new node here |
| `~` | blue | move or amend this node |
| strike | red | retire this node |

Use the **Accept** / **Reject** CodeLens buttons above each block, or **Accept
all** / **Reject all** from the header. Verdicts go to `.codoc/inbox.json`; the
loop applies them. (From the shell you can also `codoc accept <e-id>` /
`codoc reject <e-id>`.)

## 7. Editing the tree yourself

You don't have to go through Claude. Edit `tree.codoc` directly:

- **Rename a node** — just change the title text.
- **Add a node** — add a new `- Title` line at the right indentation. No id
  needed; codoc mints one on the next write.
- **Retire a node** — change `-` to `~`.
- **Adjust a description** — edit the prose below any title.

Save. `codoc watch` detects the change and runs Loop B. If the edit *requests* code
(an imperative description on a node with bound symbols — "should validate…",
"Add…"), Loop B queues a directive in `.codoc/realize.md` for you to implement with
`/codoc:sync`. A descriptive edit just updates the prose.

## 8. The commands

| Command | What it does |
|---|---|
| `codoc init` | Index repo, propose tree, install the CC plugin (hooks + MCP + skill + commands). |
| `codoc watch` | The daemon — runs both loops as you edit. |
| `codoc status` | Feature count, pending proposals, code↔binding coverage. |
| `codoc sync` | One-shot (no daemon): apply tree edits (Loop B), then reflect code (Loop A). |
| `codoc accept <e-id>` / `codoc reject <e-id>` | Shell verdict path — mirrors the IDE Accept/Reject. |

`codoc watch --dry` reflects and builds directives but doesn't queue them;
`codoc watch --no-realize` syncs the tree but never queues directives. (`codoc
reflect` and `codoc propose` exist for scripts/tests — the everyday path is the
agent reflecting via MCP and you Accepting in the IDE.)

## 9. Where things live

```
.codoc/
  tree.codoc          # the one human surface (commit with your code)
  tree.bindings.json  # IDE sidecar: feature↔symbol index + dependency edges + proposals + change feed + holds (v4)
  status.json         # loop lifecycle: in_sync / code_drift / tree_dirty / awaiting_impl / realizing
  inbox.json          # verdict channel: Accept/Reject writes here, the loop drains it
  realize.md          # realization queue: directives the live session implements via /codoc:sync
  activity.json       # agent touch log: hooks write here, VS Code extension reads it
  codoc.db            # features + bindings + event log (SQLite)
  lancedb/            # incremental code-chunk index (cocoindex)

.claude/
  settings.json       # CC hooks installed here by codoc init
  skills/
    codoc-intent/
      SKILL.md        # the propose-then-implement workflow for this repo
  commands/
    codoc/
      plan.md         # /codoc:plan — propose plan nodes before coding
      sync.md         # /codoc:sync — reconcile whichever side is behind (incl. queued directives)

.mcp.json             # registers the codoc MCP server (codoc-mcp, stdio)
```

Commit `.codoc/tree.codoc` (and optionally `codoc.db`) alongside your code so
the intent map is versioned with it. The `.claude/` directory and `.mcp.json` are
normally committed too, so the integration travels with the repo.

## 10. Re-installing after a fresh clone

```bash
codoc init
```

`codoc init` is idempotent — it re-indexes only changed files and deep-merges the
plugin (hooks into `.claude/settings.json`, the MCP entry into `.mcp.json`, the
skill + commands into `.claude/`) without clobbering existing entries. Run it after
cloning a repo that has a `.codoc/` directory to wire up the integration on the new
machine.
