"""Where the editor bundle is: the same esbuild output the extension's webview
loads (``vscode-codoc/dist/webview``: ``app.js``, ``app.css``, ``hub.js``). An
explicit ``--static-dir`` wins; else ``$CODOC_STATIC_DIR``; else the dev build
beside this source tree; else a copy packaged as ``codoc/serve/_spa``."""
from __future__ import annotations

import os
from pathlib import Path

MARKER = "app.js"


def resolve_static_dir(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    env = os.environ.get("CODOC_STATIC_DIR")
    cands = [Path(env)] if env else []
    cands.append(Path(__file__).resolve().parents[2] / "vscode-codoc" / "dist" / "webview")
    cands.append(Path(__file__).resolve().parent / "_spa")
    for c in cands:
        if (c / MARKER).is_file():
            return str(c)
    return None
