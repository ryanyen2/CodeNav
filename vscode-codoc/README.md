# codoc VS Code Extension

Navigate and edit the codoc feature tree directly inside VS Code. The extension reads `.codoc/tree.codoc` and the sidecar `.codoc/tree.bindings.json` from disk — no server, no port, no HTTP client.

## Prerequisites

- A codoc-initialized repo: run `codoc init` in your project root (Python package required)
- The extension activates automatically when VS Code finds a `.codoc/` directory in the workspace

## How to run the extension (F5)

1. Open the `vscode-codoc` folder in VS Code.
2. Run `npm install && npm run build` in the terminal.
3. Press **F5** to launch the Extension Development Host.
4. Open a workspace that has a `.codoc/` directory.

## Workflow

1. **Initialize** the repo:
   ```bash
   codoc init
   ```
   This indexes your code and writes `.codoc/tree.codoc` + `.codoc/tree.bindings.json`.

2. **Open** the feature tree with `Cmd+K Cmd+C` — opens `tree.codoc` in the editor.

3. **Browse features** in the Explorer panel under **codoc Features** (collapsible tree view).

4. **See feature attribution** in source files — CodeLens labels on every `def`/`class` show which feature owns each symbol. Unattributed symbols are labeled `codoc: unattributed`.

5. **Edit** `tree.codoc` directly:
   - Add, rename, or reorganize features (indentation = hierarchy)
   - Accept a proposal: change `?` → `+`
   - Reject a proposal: change `?` → `-` (or delete the line)
   - Retire a node: change `-` → `~`

6. **Run `codoc sync`** from the command palette (or terminal) to apply your edits and reflect code changes back to the tree.

7. **Watch mode** — run `codoc watch` in the terminal for continuous bidirectional sync as you edit code or `tree.codoc`.

## Commands (Cmd+Shift+P)

| Command | Description |
|---|---|
| `codoc: Open` | Open `tree.codoc` in the editor |
| `codoc: Sync` | Run `codoc sync` in a new terminal |
| `codoc: Navigate to feature` | Jump to a feature line by title or ID |
| `codoc: Refresh feature tree` | Force-reload the tree panel |
| `codoc: Collapse all (table of contents)` | Fold all features to title-only view |
| `codoc: Expand all features` | Unfold everything |
| `codoc: Collapse feature subtree` | Fold the subtree under the cursor |
| `codoc: Expand feature subtree` | Unfold the subtree under the cursor |

## Keybindings

| Key | Action | Context |
|---|---|---|
| `Cmd+K Cmd+C` | Open `tree.codoc` | Global |
| `Cmd+Shift+[` | Collapse feature subtree | `.codoc` file |
| `Cmd+Shift+]` | Expand feature subtree | `.codoc` file |

## Status bar

The `codoc` status bar item (bottom-left) shows the current state — never "offline":

- `$(sync) codoc: not initialized` — no `.codoc` directory found; run `codoc init`
- `$(bell) codoc: N proposals` — N pending proposals (warning colour); edit `tree.codoc` to accept/reject
- `$(check) codoc: N` — healthy, N live features

Click the status bar item to open `tree.codoc`.

## Configuration

| Setting | Default | Description |
|---|---|---|
| `codoc.rootDir` | `` | Root of the codoc-initialized repo. Auto-detected from workspace if empty. |
| `codoc.foldAttributesOnOpen` | `true` | Collapse features to title-only when first opening `tree.codoc`. |

## `tree.codoc` file format

```
- HTTP request convenience API  ⟨f-a1b2c3d4⟩
  Thin wrappers around Session for the eight HTTP verbs.

  ↪ refs: api.py › get, post, put, delete +4

  - Session management  ⟨f-e5f6a7b8⟩
    Manages connection pooling and default headers/auth across requests.

    ↪ refs: sessions.py › Session, request, send +8  ·  adapters.py › HTTPAdapter

? add "Cookie jar helpers"  ⟨e-0190ffaa⟩
  ? description: Utilities for reading and writing HTTP cookies.
```

**Markers:**
- `-` — live feature
- `~` — retired feature (still in DB, hidden from tree view)
- `?` — pending proposal; change to `+` to accept, `-` to reject

**Refs line** (`↪ refs:`) — auto-generated list of bound code symbols grouped by file. Read-only; the parser skips it during round-trip.

**IDs** (`⟨f-…⟩`) — stable feature IDs written by the backend. Do not edit.

## File-based architecture

The extension reads two files written by `codoc/codoc_file/render.py`:

- **`.codoc/tree.codoc`** — the human-readable feature tree. Parsed on every change; drives the tree view, proposal count, and status bar.
- **`.codoc/tree.bindings.json`** — machine-readable sidecar. Schema:
  ```json
  {
    "version": 1,
    "by_feature": { "f-id": [{"file": "path/to/file.py", "symbol": "file.py::ClassName"}] },
    "by_file":    { "path/to/file.py": [{"symbol": "file.py::fn", "feature_id": "f-id", "feature_title": "Title"}] },
    "features":   { "f-id": {"title": "Title", "parent_id": "f-parent"} }
  }
  ```
  `by_file` is the reverse index that CodeLens uses to annotate source files without any HTTP calls.

File watchers on both paths trigger an automatic reload whenever `codoc` writes new output.

## Development

```bash
cd vscode-codoc
npm install
npm run build   # or: npm run watch
```

Press **F5** in VS Code to open the Extension Development Host. Check **Help → Toggle Developer Tools → Console** for `[codoc]` log messages.
