"""Shared filesystem helpers for the agent-facing surfaces (hooks + MCP).

Both the CC hook handler and the MCP server need to locate the ``.codoc``
directory from a working directory, so the discovery lives here to avoid drift.
"""
from __future__ import annotations

from pathlib import Path


def find_codoc_dir(cwd: str) -> str | None:
    """Walk up from *cwd* to the first ancestor that contains ``.codoc``."""
    p = Path(cwd).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / ".codoc").is_dir():
            return str(candidate / ".codoc")
    return None
