# codoc VS Code Extension

Navigate and edit the codoc feature tree directly inside VS Code. The extension
reads `.codoc/tree.codoc` and the sidecar `.codoc/tree.bindings.json` from disk —
**no server, no port, no HTTP**. File watchers drive everything.

## Prerequisites

- A codoc-initialized repo: run `codoc init` in your project root (Python package required)
- The extension activates automatically when VS Code finds a `.codoc/` directory in the workspace

`codoc init` also installs the **Claude Code hooks and skill** into `.claude/` so that Claude Code sessions in this repo follow the propose-then-implement workflow (see §Claude Code below).

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

`Cmd+K Cmd+C` — opens `tree.codoc` in the editor, or use the command palette:
**codoc: Open**.

### 3. Browse features

The **codoc Features** panel in the Explorer sidebar shows the full feature
hierarchy. Click any feature to jump to its exact line in `tree.codoc`. The panel
shows the binding count (e.g. `3 refs`) as each item's description, and uses
state-aware icons:

- `$(symbol-module)` — live feature
- `$(circle-slash)` — retired feature
- `$(bell)` — feature with a pending proposal

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

Inlay-hint chips at the end of each feature title line show the derived bindings
(from the sidecar `by_feature` index) without any HTTP calls.

### 6. Edit `tree.codoc` directly

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

  - Token rotation  ⟨f-7b1d04⟩
      Refreshes session tokens before expiry.

- Password reset                       ← new node, no id needed
    Lets a user reset a forgotten password via emailed token.
```

- Add, rename, or reorganize features (indentation = hierarchy)
- Cite code with markdown links: `[label](codoc:file.py#symbol)` — clickable,
  opens the code **beside** the tree editor, focus stays in the tree
- Retire a node: change `-` → `~` (appears struck-through)
- `⟨f-…⟩` ids are hidden by the extension decoration — ignore them

Save. `codoc watch` picks up the change and runs Loop B.

### 7. Reviewing proposals

When codoc proposes a structural change it renders a **diff block in-situ** at
the target position:

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

Each block is blank-line terminated. Use the **Accept** / **Reject** CodeLens
buttons that appear above each block. For bulk decisions, use the **Accept all**
/ **Reject all** buttons in the row-0 header CodeLens. Verdicts are written to
`.codoc/inbox.json`; the daemon applies them and removes the blocks.

### 8. Agent activity

While `codoc watch` is running and the coding agent is active:

- **Gutter markers** appear on `tree.codoc` feature lines the agent is touching.
- **Explorer file badges** (`●`) appear on source files the agent has written.
- Both clear when the agent epoch closes.

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
| `codoc: Open` | Open `tree.codoc` in the editor |
| `codoc: Sync` | Run `codoc sync` in a new terminal |
| `codoc: Navigate to feature` | Jump to a feature line by title or ID |
| `codoc: Collapse all (table of contents)` | Fold all features to title-only view |
| `codoc: Expand all features` | Unfold everything |
| `codoc: Collapse feature subtree` | Fold the subtree under the cursor |
| `codoc: Expand feature subtree` | Unfold the subtree under the cursor |

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

The `codoc` status bar item (bottom-left) reflects the current loop state:

| Status | Meaning |
|---|---|
| `$(sync) codoc: not initialized` | No `.codoc/` directory — run `codoc init` |
| `$(loading~spin) codoc: implementing…` | Loop B is running the coding agent |
| `$(pencil) codoc: applying tree edits…` | Loop B is processing tree changes |
| `$(bell) codoc: N proposed changes` | N pending proposals in the tree |
| `$(check) codoc: in sync` | All clean |

Click the status bar item to open `tree.codoc`.

---

## Configuration

| Setting | Default | Description |
|---|---|---|
| `codoc.rootDir` | `` | Root of the codoc-initialized repo. Auto-detected from workspace if empty. |
| `codoc.focusDependencies` | `true` | Dim unrelated features when the cursor is on a node with dependency edges. |
| `codoc.agentGutter` | `true` | Show gutter markers on features the agent is actively editing. |

---

## `tree.codoc` file format

```
- HTTP request convenience API  ⟨f-a1b2c3d4⟩
    Thin wrappers around Session for the eight HTTP verbs.
    Cites [session handling](codoc:requests/sessions.py#Session).

  - Session management  ⟨f-e5f6a7b8⟩
      Manages connection pooling and default headers/auth across requests.

  ~ Legacy urllib2 adapter  ⟨f-0d1e2f3a⟩
      Removed in v2.0; see migration guide.

+ - Cookie jar helpers  ⟨e-0190ffaa⟩
+     Utilities for reading and writing HTTP cookies.
```

**Markers:**
- `-` — live feature
- `~` — retired feature (struck-through in the IDE)
- `+`/`-`/`~` at column 0 — in-situ proposal block (green/red/blue tint)

**IDs:**
- `⟨f-…⟩` — stable feature id (hidden by the IDE, never type or edit)
- `⟨e-…⟩` — event id on a proposal block (identifies the pending change)

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
| `.codoc/inbox.json` | Verdict channel — Accept/Reject writes here, daemon drains it |

**Sidecar schema (v2):**

```json
{
  "version": 2,
  "by_feature": { "f-id": [{"file": "path.py", "symbol": "path.py::Class.method"}] },
  "by_file":    { "path.py": [{"symbol": "...", "feature_id": "f-id", "feature_title": "Title"}] },
  "features":   { "f-id": {"title": "Title", "parent_id": null} },
  "feature_edges": { "f-id": [{"to": "f-other", "weight": 4, "kinds": ["call"]}] }
}
```

`feature_edges` is new in v2 — aggregated call/import coupling between features,
used to determine which nodes to keep opaque during dependency dimming. The
extension tolerates its absence (v1 sidecars have no dependency dimming).

File watchers on all four paths trigger an automatic reload whenever codoc writes
new output.

---

---

## Claude Code integration

codoc integrates with Claude Code through **hooks and a skill file** — not MCP,
not a plugin. `codoc init` installs both automatically.

### How it works

**Hooks** (written to `.claude/settings.json`):

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

These write `.codoc/activity.json` as Claude reads and modifies files — which
files it touched, which features they belong to (resolved from the sidecar). The
extension watches this file to drive:
- **Gutter markers** on feature lines Claude is currently editing in `tree.codoc`
- **Explorer file badges** on source files Claude has written

**Skill** (written to `.claude/skills/codoc-intent/SKILL.md`):

Claude Code auto-loads this file for every session in the repo. It instructs
Claude to:
1. Read `tree.codoc` to understand existing features.
2. **Propose** changes via `codoc propose` CLI — no code files are touched.
3. Wait and tell you to Accept in the VS Code IDE.

### The loop

```
You: "add rate limiting to the auth module"
  → Claude proposes via codoc propose add_node …
  → green + block appears in tree.codoc (no code touched)
  → you Accept in VS Code (CodeLens button)
  → Loop B builds directive + spawns claude -p (headless, for implementation)
  → Loop A re-reflects on written files → may surface follow-up proposals
```

### Re-run after a fresh clone

```bash
codoc init   # idempotent: merges hooks, re-installs skill, re-indexes only changed files
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
