"""``.codoc/activity.json`` — ephemeral runtime activity + agent epoch state.

This file is **not** an intent channel (that's ``tree.codoc``); it carries
transient "what's being touched right now" and epoch lifecycle state so the
watch daemon can suppress redundant Loop A passes during an active agent session
and the VS Code extension can render live gutter decorations.

Schema (version 1)::

    {
      "version": 1,
      "epoch": {
        "id": "ep-<session_id>",
        "origin": "interactive | loop_b",
        "open": true,
        "started_at": "<iso>",
        "ended_at": null
      },
      "touched": {
        "src/theme.py": {
          "symbols": ["theme.py::apply_theme"],
          "feature_ids": ["f-1a2b"],
          "last": "<iso>",
          "mode": "write"
        }
      },
      "recent": [
        {"tool": "Edit", "file": "src/theme.py", "feature_ids": ["f-1a2b"], "at": "<iso>"}
      ]
    }

``open=true``  → an agent session is active; the watch daemon suppresses
                 independent Loop A passes.
``open=false`` → the session just ended; the daemon reconciles (interactive
                 origin only — ``loop_b`` origin is owned by Loop B's reflect).

This file is safe to delete at any time; the loops regenerate it on the next
SessionStart hook. The daemon never starts a loop solely because this file
changed — it only reads the ``epoch.open`` transition.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ACTIVITY_FILENAME = "activity.json"

_EMPTY: dict = {
    "version": 1,
    "epoch": {"id": "", "origin": "interactive", "open": False, "started_at": None, "ended_at": None},
    "touched": {},
    "recent": [],
}


def activity_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / ACTIVITY_FILENAME


def read_activity(codoc_dir: str | Path) -> dict:
    """Return the parsed activity.json or the empty sentinel if absent / corrupt."""
    path = activity_path(codoc_dir)
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return dict(_EMPTY)


def read_epoch(codoc_dir: str | Path) -> dict | None:
    """Return the ``epoch`` block, or None if the file is absent / corrupt."""
    data = read_activity(codoc_dir)
    ep = data.get("epoch")
    if not ep or not ep.get("id"):
        return None
    return ep


def epoch_touched_files(codoc_dir: str | Path) -> list[str]:
    """Return the list of files (repo-relative paths) touched in the last epoch."""
    data = read_activity(codoc_dir)
    touched: dict = data.get("touched") or {}
    return list(touched.keys())


def epoch_written_files(codoc_dir: str | Path) -> list[str]:
    """Files the last epoch actually WROTE (``mode == "write"``).

    Distinct from :func:`epoch_touched_files`, which also counts reads — reporting
    a read as "written" overstates what an agent did and mis-scopes the post-write
    reflection. Loop B uses this so "agent wrote N files" counts only writes.
    """
    data = read_activity(codoc_dir)
    touched: dict = data.get("touched") or {}
    return [f for f, meta in touched.items() if (meta or {}).get("mode") == "write"]
