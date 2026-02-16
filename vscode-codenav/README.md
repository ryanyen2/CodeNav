# CodeNav VS Code Extension

Edit semantic trees in `.codoc` files with sync to the CodeNav backend (analyze, sync, apply).

## Prerequisites

- Build the CodeNav library from the repo root: `npm run build`
- Start the backend (from `server/`): `uv run python main.py` (default port 8001)

## How to run the extension (F5)

1. **Open the extension folder**: In Cursor/VS Code, use **File → Open Folder** and choose the `vscode-codenav` folder (not the whole CodeNav repo).
2. **Build**: Run `npm run build` in the terminal (or from repo root: `npm run build` then `cd vscode-codenav && npm run build`).
3. **Launch**: Press **F5** (or Run → Start Debugging). A new window opens (Extension Development Host).
4. **Commands**: In the new window, press **Ctrl+Shift+P** (Cmd+Shift+P on Mac), type **CodeNav**, and you should see:
   - CodeNav: Analyze Codebase
   - CodeNav: Sync (Code → Tree)
   - CodeNav: Apply to Code
   - CodeNav: Preview Apply
   - CodeNav: Toggle Status
5. **Status bar**: Bottom-left should show e.g. "CodeNav: Backend Offline" until the backend is running.

If commands don’t appear or you see errors, open **Help → Toggle Developer Tools** and check the Console for `[CodeNav]` messages.

## Commands

- **CodeNav: Analyze Codebase** — Create `.codoc` and `.codoc.meta.json` from the workspace
- **CodeNav: Sync** — Code → tree (refresh after editing Python)
- **CodeNav: Apply to Code** — Tree → code (apply edits)
- **CodeNav: Preview** — Dry-run apply (show planned changes)
- **CodeNav: Toggle Status** — Cycle status on the current line (context menu)

## Configuration

- `codenav.serverUrl` — Backend base URL (default `http://localhost:8001`)
- `codenav.serverPath` — Optional path to auto-start server

## Development

From repo root: build library then extension:

```bash
npm run build
cd vscode-codenav && npm install && npm run build
```

Open the `vscode-codenav` folder in VS Code, then press **F5** to launch the Extension Development Host.
