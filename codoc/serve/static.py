"""Resolve the built standalone-SPA directory for ``codoc serve`` (plan U2).

The editor bundle is the SAME esbuild output the VS Code webview uses
(``vscode-codoc/dist/webview/`` — ``doc-view.js`` + ``doc-view.css`` + ``fonts/`` +
``index.html``). It is transport-agnostic (``acquireHostApi()`` selects the network
bridge when ``acquireVsCodeApi`` is absent), so the hub just serves that directory.

When ``--static-dir`` is not passed, the hub auto-discovers the bundle so the
placeholder ("…not built yet…") only ever shows when the bundle genuinely is not
built. Resolution order (first hit wins):

1. an explicit path (``--static-dir``) — honored verbatim by the caller;
2. ``$CODOC_STATIC_DIR`` — an override for packaged / non-standard layouts;
3. ``<repo>/vscode-codoc/dist/webview`` — the dev build location, relative to the
   repo root the hub was started in;
4. a packaged copy shipped beside this module (``codoc/serve/_spa/``) — populated
   by the wheel build for an installed codoc (absent in a source checkout).

A directory only counts as a built SPA when it contains ``index.html``; a half-built
or empty ``dist/webview`` resolves to ``None`` so the caller falls back to the
placeholder rather than serving a blank page.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Marker file that must exist for a directory to count as a built SPA.
_SPA_MARKER = "index.html"


def _is_built_spa(path: Path | None) -> bool:
    return path is not None and (path / _SPA_MARKER).is_file()


def resolve_static_dir(repo_root: str | os.PathLike[str], explicit: str | None = None) -> str | None:
    """Return the directory to serve the SPA from, or ``None`` if no build exists.

    ``explicit`` (the ``--static-dir`` flag) wins when given — even if it does not
    yet contain ``index.html`` — so an operator pointing at a known location is
    never second-guessed. Otherwise the candidate chain is tried in order.
    """
    if explicit:
        return explicit

    env = os.environ.get("CODOC_STATIC_DIR")
    if env and _is_built_spa(Path(env)):
        return env

    repo = Path(repo_root).resolve()
    dev_build = repo / "vscode-codoc" / "dist" / "webview"
    if _is_built_spa(dev_build):
        return str(dev_build)

    # Editable / source install: the bundle lives in the codoc SOURCE TREE
    # (``<repo>/vscode-codoc/dist/webview``), not the workspace being served — so
    # serving any other repo still finds it. ``static.py`` is at
    # ``<repo>/codoc/serve/static.py`` → parents[2] is the codoc repo root.
    pkg_repo = Path(__file__).resolve().parents[2] / "vscode-codoc" / "dist" / "webview"
    if _is_built_spa(pkg_repo):
        return str(pkg_repo)

    packaged = Path(__file__).resolve().parent / "_spa"
    if _is_built_spa(packaged):
        return str(packaged)

    return None
