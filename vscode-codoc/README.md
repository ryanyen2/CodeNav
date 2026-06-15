# codoc VS Code Extension

Navigate and edit the codoc feature tree directly inside VS Code. The extension
reads `.codoc/tree.codoc` and the sidecar `.codoc/tree.bindings.json` from disk —
**no server, no port, no HTTP**. File watchers drive everything.

## Setup — one click, no terminal

Open your repo and run **“codoc: Set up codoc”** — from the first-run walkthrough,
the `$(rocket) Set up codoc` status-bar item, or the command palette. Setup:

1. Bootstraps **`uv`** and installs the codoc Python core into an isolated,
   version-pinned environment (no manual `pip`, no system-Python assumptions).
2. Points codoc's reflection at your existing **Claude Code** login — no separate
   API key in the common case (OpenAI is the fallback; you'll be prompted only
   then). *Heads-up:* headless Claude usage bills against your Claude subscription
   as of **2026-06-15**.
3. Runs `codoc init` (indexes the repo, proposes the tree, installs the **Claude
   Code plugin** into `.claude/` + `.mcp.json` — hooks, MCP server, `codoc-intent`
   skill, `/codoc:plan` + `/codoc:sync`).
4. Starts and supervises the `codoc watch` daemon **for you** — it stops cleanly
   when you close the window; you never run `codoc watch` by hand.

**Workspace Trust:** setup, provisioning, and the daemon require a trusted
workspace (they install and run code). In an untrusted/restricted workspace the
extension still parses and navigates `tree.codoc` read-only. Re-run anytime with
**“codoc: Repair / re-run setup”**.

Prefer the terminal? The CLI path (`uv tool install codoc` / `pip install -e .`,
then `codoc init`) still works — see the repo README.

## How to run the extension (development)

1. Open the `vscode-codoc` folder in VS Code.
2. Run `npm install && npm run build` in the terminal.
3. Press **F5** to launch the Extension Development Host.
4. Open a workspace that has a `.codoc/` directory.

---

## Workflow

### 1. Initialize

```bash
codoc init
```

Indexes your code, proposes an initial feature tree, and writes `.codoc/tree.codoc`
plus `.codoc/tree.bindings.json`.

### 2. Open the feature tree

`Cmd+K Cmd+C` — opens `tree.codoc`, or use the command palette: **codoc: Open**.

There are **two viewers**, both for the same file:

- The **Codoc Tree** webview (the default editor for `tree.codoc`) — an outline +
  detail pane that renders **every** proposal type inline (see §7).
- The **raw-text `tree.codoc` editor** — the file itself, with decorations,
  CodeLens, ghost hunks, and the lightbulb. Switch to it with *Open With → Text
  Editor*.

There is no Explorer "codoc Features" sidebar — the webview *is* the tree browser.

### 3. Browse features

In either viewer, indentation is the hierarchy. Jump to any feature with
**codoc: Navigate to feature** (by title or id), or click a CodeLens label in a
source file to land on the feature that owns that symbol. Inlay-hint chips at the
end of each title line show the derived bindings (from the sidecar `by_feature`
index) — no HTTP calls.

### 4. Dependency focus

Rest your cursor on any feature that has call/import edges to other features:

- That feature + all graph-neighbors stay full-opacity.
- Everything else dims to **0.6 opacity**.
- Dimming clears when your cursor moves away or when the node has no edges.
- Suppressed while typing (400 ms debounce so it doesn't flicker as you edit).
- Toggle with `codoc.focusDependencies` (default: on).

### 5. Feature attribution in source files

CodeLens labels on every `def`/`class`/`function` in Python and TypeScript files
show which feature owns each symbol. Click the label to navigate to that feature
in `tree.codoc`.

### 6. Edit `tree.codoc` directly

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

  - Token rotation  ⟨f-7b1d04⟩
      Refreshes session tokens before expiry.

- Password reset                       ← new node, no id needed
    Lets a user reset a forgotten password via emailed token.
```

- Add, rename, or reorganize features (indentation = hierarchy; the webview also
  supports drag-and-drop reparenting)
- Cite code with markdown links: `[label](codoc:file.py#symbol)` — clickable,
  opens the code **beside** the tree editor, focus stays in the tree
- Retire a node: change `-` → `~` (appears struck-through)
- `⟨f-…⟩` ids are hidden by the extension decoration — ignore them

Save. `codoc watch` picks up the change and runs Loop B.

### 7. Reviewing proposals

Proposals are a **single inline surface** — no separate panel. Both viewers show
them in place, at the tree position where the change would land:

- **Codoc Tree webview** — ADD/MOVE render as ghost rows in the tree pane, RETIRE
  as a strike on the live row, and AMEND as a word-level inline diff *inside the
  description*. Each row has inline `✓` / `✗`, plus toolbar **Accept all** /
  **Reject all**.
- **Raw-text editor** — ADD/MOVE render as `+` / `~` ghost hunks at column 0;
  RETIRE/AMEND decorate the live node (strike / inline diff). Accept/Reject via the
  CodeLens above each hunk, the lightbulb, or **Accept Change at Cursor** /
  **Reject Change at Cursor**.

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

+ - Rate limiting  ⟨e-9f01c2⟩
+     Caps API requests per user per minute.

  - Token rotation  ⟨f-7b1d04⟩
```

| Marker | Meaning |
|---|---|
| `+` (green) | add a new node here |
| `~` (blue) | move or amend this node |
| strike (red) | retire this node (decorated in place, not a separate line) |

Verdicts are written to `.codoc/inbox.json`; the loop applies them and removes the
blocks.

### 8. Agent activity

While a Claude Code session is active in the repo (driven by the codoc hooks):

- **Gutter markers** appear on `tree.codoc` feature lines the session is touching.
- **Explorer file badges** (`●`) appear on source files the session has written.
- Both clear when the activity epoch closes.

Toggle gutter markers with `codoc.agentGutter` (default: on).

### 9. Watch / sync

```bash
codoc watch   # continuous daemon: reacts to code edits + tree edits
codoc sync    # one-shot: apply tree edits, reflect code once, exit
```

---

## Commands (Cmd+Shift+P)

| Command | Description |
|---|---|
| `codoc: Set up codoc` | One-click setup: provision the core, init, start the daemon |
| `codoc: Repair / re-run setup` | Re-run setup to repair a partial/broken state |
| `codoc: Open` | Open `tree.codoc` |
| `codoc: Sync` | Run `codoc sync` in a new terminal |
| `codoc: Navigate to feature` | Jump to a feature line by title or id |
| `codoc: Accept / Reject proposed change` | Verdict on one proposal |
| `codoc: Accept / Reject all proposed changes` | Bulk verdict |
| `codoc: Accept / Reject Change at Cursor` | Verdict on the hunk under the cursor (raw editor) |
| `codoc: Open code reference` / `Open First Code Binding` / `Pick code binding to open` | Jump to bound code |
| `codoc: Collapse all (table of contents)` / `Expand all features` | Fold / unfold the whole tree |
| `codoc: Collapse / Expand feature subtree` | Fold / unfold under the cursor |

---

## Keybindings

| Key | Action | Context |
|---|---|---|
| `Cmd+K Cmd+C` | Open `tree.codoc` | Global |
| `Cmd+Shift+[` | Collapse feature subtree | `tree.codoc` editor |
| `Cmd+Shift+]` | Expand feature subtree | `tree.codoc` editor |
| `Cmd+K ↑` | Previous sibling | `tree.codoc` editor |
| `Cmd+K ↓` | Next sibling | `tree.codoc` editor |
| `Cmd+K ←` | Jump to parent | `tree.codoc` editor |
| `Cmd+K →` | Jump to first child | `tree.codoc` editor |

The `⌘K` nav chords move the cursor between feature title lines while leaving
normal arrow-key text editing intact.

---

## Status bar

The `codoc` status bar item (bottom-left) reflects the current loop state
(`status.json`):

| Status | State | Meaning |
|---|---|---|
| `$(rocket) codoc: Set up codoc` | — | No `.codoc/` yet — click to run one-click setup |
| `$(cloud-download) codoc: setting up…` | — | Provisioning the core / indexing the repo |
| `$(loading~spin) codoc: implementing…` | `realizing` | Your session is implementing tree edits |
| `$(pencil) codoc: applying tree edits…` | `tree_dirty` | Loop B is processing tree changes |
| `$(play) codoc: N to implement` | `awaiting_impl` | N directives queued in `realize.md` — run `/codoc:sync` |
| `$(bell) codoc: N proposals` | `code_drift` | N pending proposals to review |
| `$(check) codoc: N` | `in_sync` | All clean (N live features) |

Click the status bar item to open `tree.codoc`.

---

## Configuration

| Setting | Default | Description |
|---|---|---|
| `codoc.rootDir` | `` | Root of the codoc-initialized repo. Auto-detected from workspace if empty. |
| `codoc.focusDependencies` | `true` | Dim unrelated features when the cursor is on a node with dependency edges. |
| `codoc.agentGutter` | `true` | Show gutter markers on features the session is actively editing. |

---

## `tree.codoc` file format

```
- HTTP request convenience API  ⟨f-a1b2c3d4⟩
    Thin wrappers around Session for the eight HTTP verbs.
    Cites [session handling](codoc:requests/sessions.py#Session).

  - Session management  ⟨f-e5f6a7b8⟩
      Manages connection pooling and default headers/auth across requests.

  ~ Legacy urllib2 adapter  ⟨f-0d1e2f3a⟩
      Deprecated in favour of the Session adapter.

+ - Cookie jar helpers  ⟨e-0190ffaa⟩
+     Utilities for reading and writing HTTP cookies.
```

**Markers:**
- `-` — live feature
- `~` — retired feature (struck-through in the IDE)
- `+` / `~` at column 0 — in-situ ADD / MOVE proposal hunk (green / blue tint).
  RETIRE and AMEND proposals are not separate lines — they decorate the live node
  (strike / inline description diff).

**IDs:**
- `⟨f-…⟩` — stable feature id (hidden by the IDE, never type or edit)
- `⟨e-…⟩` — event id on a proposal hunk (identifies the pending change)

**Refs:** `[label](codoc:file.py#symbol)` — clickable; opens code Beside the
tree with focus preserved in the tree editor.

---

## File-based architecture

The extension reads files written by `codoc/codoc_file/render.py` and never
makes network requests:

| File | Purpose |
|---|---|
| `.codoc/tree.codoc` | Human-readable feature tree; parsed on every change |
| `.codoc/tree.bindings.json` | Machine-readable sidecar (see schema below) |
| `.codoc/status.json` | Loop lifecycle state — drives status bar + CodeLens header |
| `.codoc/inbox.json` | Verdict channel — Accept/Reject writes here, the loop drains it |
| `.codoc/edits.json` | Provenance/intent channel — settle authorship + live doc-ahead suggestions |
| `.codoc/activity.json` | Agent touch log — drives gutter markers + file badges |
| `.codoc/realize.md` | Realization queue — surfaced as "N to implement" in the status bar |

**Sidecar schema (v4):**

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

- `feature_edges` — aggregated call/import coupling between features; drives the
  dependency dimming (tolerated absent on older sidecars).
- `features[].realized` — `false` marks an accepted-but-unimplemented plan node;
  the extension decorates it as a placeholder.
- `proposals` — drives the in-place retire/amend overlays + Accept/Reject on the
  live node (`by_feature`) and the ADD/MOVE ghost hunks (`by_event`). The v4
  provenance fields (`actor`/`mode`/`caused_by`) drive the authorship ink and the
  "↳ from your edit" cascade cue.
- `changes` — the last ~50 applied events (newest first); drives the agent-pencil
  re-stamp in the doc view.
- `holds` — the doc-wins hold set (features with pending doc-ahead intent).

File watchers on all paths trigger an automatic reload whenever codoc writes new
output.

---

## Claude Code integration

codoc ships as a **Claude Code plugin** — hooks, an MCP server, a skill, and slash
commands — all installed by `codoc init`. (No MCP-free / `claude -p` path anymore.)

### How it works

**Hooks** (written to `.claude/settings.json`):

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

`PreToolUse` / `PostToolUse` write `.codoc/activity.json` as Claude reads and
modifies files (driving the gutter markers + file badges); `Stop` reflects the
session's changes back into the tree; `UserPromptSubmit` nudges `/codoc:sync`
when work is queued.

**MCP server** (`codoc`, registered in `.mcp.json`): the agent's reflection API —
`codoc_tree`, `codoc_status`, `codoc_reflect`, `codoc_propose_{add,amend,move,retire}`,
`codoc_attach`, `codoc_plan_add`.

**Skill + commands** (`.claude/skills/codoc-intent/`, `.claude/commands/codoc/`):
the skill teaches Claude the MCP-first propose-then-implement workflow; `/codoc:plan`
proposes plan nodes before coding, `/codoc:sync` implements the queued directives.

### The loop

```
You: "add rate limiting to the auth module"
  → Claude proposes via /codoc:plan (codoc_plan_add) — green + block in tree.codoc, no code touched
  → you Accept in VS Code (CodeLens / inline ✓)
  → Loop B queues a directive in .codoc/realize.md (status: awaiting_impl)
  → you run /codoc:sync in your session → it writes the code + binds it
  → Loop A re-reflects on the written files → may surface follow-up proposals
```

### Re-run after a fresh clone

```bash
codoc init   # idempotent: merges hooks + MCP + skill + commands, re-indexes only changed files
```

---

## Development

```bash
cd vscode-codoc
npm install
npm run build   # one-shot build
npm run watch   # watch mode
npx tsc --noEmit  # type-check without building
```

Press **F5** in VS Code to open the Extension Development Host. Check
**Help → Toggle Developer Tools → Console** for `[codoc]` log messages.
