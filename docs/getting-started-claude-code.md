# Getting Started — codoc × Claude Code

codoc keeps a **feature tree** — a small, navigable map of what your code is
*for* — in sync with the code itself, in both directions:

- **code → codoc:** when code changes, codoc detects what changed and makes the
  minimal tree update — attach new code to a node, tweak a description, add a
  child, reparent, or retire. Safe updates apply automatically; structural ones
  appear as reviewable proposals.
- **codoc → code:** when you (or Claude Code) edit the tree, codoc has a coding
  agent make the matching code change, then re-reads the result to refine the tree
  if intent was under-specified.

There is **one file** you ever look at — `.codoc/tree.codoc` — and **four commands**.

---

## 1. Install

```bash
pip install -e .            # Python 3.11+
export OPENAI_API_KEY=sk-…  # codoc's own LLM calls; override model with CODOC_MODEL
codoc --help
```

## 2. Initialise

```bash
cd ~/code/my-project
codoc init
```

`codoc init` does four things:

1. **Indexes the repo** — AST chunks + embeddings via cocoindex/LanceDB.
   Incremental; a killed run resumes from the last completed file.
2. **Proposes a feature tree** in one LLM pass and writes `.codoc/tree.codoc`.
3. **Installs Claude Code hooks** into `.claude/settings.json` —
   `SessionStart`, `Stop`, `PreToolUse`, `PostToolUse` hooks that maintain
   `.codoc/activity.json` (the live agent-touch log used by the VS Code extension
   for gutter decorations and file badges).
4. **Installs the `codoc-intent` skill** into `.claude/skills/codoc-intent/SKILL.md` —
   a context file Claude Code auto-loads that teaches it the propose-then-implement
   workflow for this repo.

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

codoc integrates with Claude Code through **hooks and a skill file** — not MCP,
not a VS Code plugin, not an SDK. There is no server process; no port to configure.

**Two roles for Claude:**

| Role | How invoked | What it does |
|---|---|---|
| Interactive planning | You open Claude Code and ask a question | Reads `codoc-intent` skill → proposes changes to `tree.codoc` via `codoc propose` CLI; no code is touched yet |
| Headless implementation | Loop B spawns `claude -p` after you Accept | Implements the code change described by the accepted intent |

**The hooks** (auto-installed into `.claude/settings.json`):

```json
"hooks": {
  "SessionStart": [{ "command": "python -m codoc.agent.hook session-start" }],
  "Stop":         [{ "command": "python -m codoc.agent.hook stop" }],
  "PreToolUse":   [{ "matcher": "Edit|Write|MultiEdit|Read",
                     "command": "python -m codoc.agent.hook pre-tool" }],
  "PostToolUse":  [{ "matcher": "Edit|Write|MultiEdit",
                     "command": "python -m codoc.agent.hook post-tool" }]
}
```

These hooks write `.codoc/activity.json` as Claude reads and modifies files —
which files it touched, in which mode (read vs write), and which features those
files belong to (resolved from the sidecar). The VS Code extension watches this
file and shows live gutter markers in `tree.codoc` and file badges in Explorer.
The hooks never block the agent; they're fire-and-forget with a 10 s timeout.

**The skill** (auto-installed into `.claude/skills/codoc-intent/SKILL.md`):
Claude Code loads this automatically for every session in the repo. It instructs
Claude to:

1. **Read** `tree.codoc` and `tree.bindings.json` to understand what exists.
2. **Propose** changes via `codoc propose` CLI before touching any code.
3. **Wait** — tell the user to Accept in the VS Code IDE, then stop.

## 4. The propose-then-implement loop

This is the full interactive flow:

### Step 1 — You ask Claude Code

```
You: Add rate limiting to the auth module — cap requests per user per minute.
```

### Step 2 — Claude proposes (no code touched)

Claude Code reads the skill, scans `tree.codoc`, and runs:

```bash
codoc propose add_node \
  --title "Rate limiting" \
  --description "Caps API requests per user per minute using a token-bucket per user_id." \
  --parent f-3a9c2e \
  --bind "auth/rate_limit.py::check_rate_limit"
```

This creates an `applied=False` event in the store and re-renders `tree.codoc`
with an in-situ proposal block at the target position:

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

+ - Rate limiting  ⟨e-9f01c2⟩
+     Caps API requests per user per minute using a token-bucket per user_id.

  - Token rotation  ⟨f-7b1d04⟩
```

Claude then tells you: *"I've proposed Rate limiting as a codoc plan. Accept it in
VS Code to trigger implementation."*

### Step 3 — You review and Accept

In VS Code, the green `+` block appears at the exact tree position. You can
refine the description before accepting — just edit the proposal text (it's in
the file). When ready, click **Accept** in the CodeLens.

### Step 4 — Loop B implements

Accepting writes a verdict to `.codoc/inbox.json`. Loop B (`codoc watch`) picks
it up and:

1. Applies the op to the store (marks the event as accepted).
2. Builds a coding directive from the accepted description + any bound symbols.
3. Writes `status.json = realizing`, then spawns `claude -p --dangerously-skip-permissions` once with the directive.
4. `claude -p` creates/modifies the code files.

### Step 5 — Loop A re-reflects

After the coding agent exits, Loop A runs scoped to the files it wrote:

- If the code matches the intent cleanly → bindings are updated, status goes
  back to `in_sync`.
- If the implementation revealed something the description didn't capture fully
  → Loop A may surface additional proposals (e.g., a helper function that warrants
  its own node). These appear in `tree.codoc` as new in-situ hunks for your
  review.

This closes the loop: intent → plan → accept → code → reflect → refined tree.

## 5. Watch

```bash
codoc watch
```

One daemon runs both loops. Leave it running while you work. It reacts to both:
- **Code file changes** → Loop A re-checks affected bindings, surfaces proposals.
- **`tree.codoc` changes** (your edits, Claude's proposals, your Accepts) →
  Loop B applies ops and, if needed, invokes the coding agent.

## 6. Reviewing proposals

Proposals render in-situ at their target tree position:

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
| `-` | red | retire this node |
| `~` | blue | move or amend this node |

Use the **Accept** / **Reject** CodeLens buttons above each block, or **Accept
all** / **Reject all** from the header. Verdicts go to `.codoc/inbox.json`; the
daemon applies them.

## 7. Editing the tree yourself

You don't have to go through Claude. Edit `tree.codoc` directly:

- **Rename a node** — just change the title text.
- **Add a node** — add a new `- Title` line at the right indentation. No id
  needed; codoc mints one on the next write.
- **Retire a node** — change `-` to `~`.
- **Adjust a description** — edit the prose below any title.

Save. `codoc watch` detects the change and runs Loop B. If the description
change implies code work (the node has bound symbols), Loop B builds a directive
and invokes the coding agent.

## 8. The four commands

| Command | What it does |
|---|---|
| `codoc init` | Index repo, propose tree, install CC hooks + skill. |
| `codoc watch` | The daemon — runs both loops as you edit. |
| `codoc status` | Feature count, pending proposals, recent activity. |
| `codoc sync` | One-shot (no daemon): apply tree edits, then reflect code. |

`codoc watch --dry` reflects and builds directives but doesn't spawn the coding
agent; `codoc watch --no-realize` syncs the tree but skips the agent entirely.

## 9. Where things live

```
.codoc/
  tree.codoc          # the one human surface (commit with your code)
  tree.bindings.json  # IDE sidecar: feature↔symbol index + dependency edges
  status.json         # loop lifecycle: in_sync / code_drift / tree_dirty / realizing
  inbox.json          # verdict channel: Accept/Reject writes here, daemon reads it
  activity.json       # agent touch log: hooks write here, VS Code extension reads it
  codoc.db            # features + bindings + event log (SQLite)
  lancedb/            # incremental code-chunk index (cocoindex)

.claude/
  settings.json       # CC hooks installed here by codoc init
  skills/
    codoc-intent/
      SKILL.md        # the propose-then-implement workflow for this repo
```

Commit `.codoc/tree.codoc` (and optionally `codoc.db`) alongside your code so
the intent map is versioned with it. The `.claude/` directory is already
committed in most Claude Code repos.

## 10. Re-installing after a fresh clone

```bash
codoc init
```

`codoc init` is idempotent — it re-indexes only changed files and deep-merges
the hook block into `.claude/settings.json` without clobbering existing hooks.
Run it after cloning a repo that has a `.codoc/` directory to wire up the CC
integration for the new machine.
