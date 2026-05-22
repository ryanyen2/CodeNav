# codoc VS Code Extension

Edit `.codoc` feature-tree files with auto-sync to the codoc backend. Proposals from the reflective pipeline appear inline — accept or reject them without leaving your editor.

## Prerequisites

- Python backend running: `codoc server` (default port 8001)
- A codoc-initialized repo: `codoc init` in your project root

## How to run the extension (F5)

1. Open the `vscode-codoc` folder in VS Code.
2. Run `npm install && npm run build` in the terminal.
3. Press **F5** to launch the Extension Development Host.
4. Open a workspace that has a `.codoc/` directory.

## Workflow

1. **Render** the feature tree to editable files:
   ```bash
   codoc projection render
   ```
   This writes `.codoc/tree/*.codoc` files.

2. **Open** the index with `Cmd+K Cmd+L` — opens `_index.codoc` listing all root features.

3. **Edit** a `.codoc` file — change slugs, amend intent prose, change `-` to `~` to retire. Changes are applied automatically on save.

4. **Review proposals** — after a git commit, the reflective pipeline emits `?` lines into the affected files:
   ```
   ? reattribute: constraint-solver  [proposal]  # ?0190ff...
       candidate-bindings: js.py:new_helper_fn
   ```
   - `Cmd+Enter` on a `?` line — accept the proposal
   - `Cmd+Shift+Backspace` on a `?` line — reject
   - `$(edit) Edit & Accept` codelens — accept with a slug rename

## Commands (Cmd+Shift+P)

| Command | Description |
|---|---|
| `codoc: Open feature tree (_index.codoc)` | Open `_index.codoc` — top-level feature listing |
| `codoc: Render tree` | Re-render `.codoc/tree/` from SQLite |
| `codoc: Sync current file` | Apply edits in the current `.codoc` file to SQLite |
| `codoc: Bootstrap codebase` | Trigger bootstrap (propose initial feature tree) |
| `codoc: Reflect (process new commits)` | Run the reflective pipeline on recent commits |
| `codoc: Accept proposal` | Accept the proposal at the cursor |
| `codoc: Reject proposal` | Reject the proposal at the cursor |
| `codoc: Accept all proposals` | Accept every pending proposal |
| `codoc: Reject all proposals` | Reject every pending proposal |
| `codoc: Accept proposal with edits` | Accept and rename the proposed slug |

## Keybindings

| Key | Action | Context |
|---|---|---|
| `Cmd+Enter` | Accept proposal at cursor | `.codoc` file |
| `Cmd+Shift+Backspace` | Reject proposal at cursor | `.codoc` file |
| `Cmd+K Cmd+L` | Open `_index.codoc` | Global |

## Status bar

The `codoc` status bar item (bottom-right) shows:
- `$(warning) codoc: offline` — server not reachable
- `$(bell) codoc: N` — N pending proposals
- `$(check) codoc` — connected, no pending proposals

Click the status bar item to open `_index.codoc`.

## Configuration

| Setting | Default | Description |
|---|---|---|
| `codoc.serverUrl` | `http://localhost:8001` | codoc FastAPI server URL |
| `codoc.rootDir` | `` | Repo root with `.codoc/`. Auto-detected from workspace if empty. |

## `.codoc` file format

```
# codoc subtree: visualization

# - active   ~ retired   ? proposal (delete=accept, !=reject)   [State] computed

- visualization  [Stub]
  Draco visualization recommendation — translates Vega-Lite specs to
  weighted soft-constraint ASP programs.

  - spec-parser  [Stable]
    Parse a Vega-Lite JSON spec into internal representation.
    bindings:
      [b1] js.py :: Draco
      [b2] helper.py :: topo_sort

? reattribute: spec-parser  [proposal]  # ?0190ff...
    candidate-bindings: js.py:new_helper_fn
```

Edit slugs, indent, and prose directly. The extension syncs on save. Bindings lines are read-only — edits there are ignored.

## Development

```bash
cd vscode-codoc
npm install
npm run build   # or: npm run watch
```

Press **F5** in VS Code to open the Extension Development Host. Check **Help → Toggle Developer Tools → Console** for `[codoc]` log messages.
