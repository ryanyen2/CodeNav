"""``.codoc/status.json`` — the pipeline state the IDE surfaces in its status bar.

Four states answer "where are code and intent relative to each other right now?":

  ``in_sync``    no pending proposals; tree and code agree.
  ``code_drift`` code changed and Loop A raised proposals awaiting review.
  ``tree_dirty`` tree.codoc was edited; the code change has not been realized yet.
  ``realizing``  the coding agent is implementing tree edits right now.

The loops write this file at the end of each pass (and ``realizing`` around the
agent spawn). The IDE watches the file and never has to poll.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from codoc.model.hlc import HLC

STATUS_FILENAME = "status.json"

IN_SYNC = "in_sync"
CODE_DRIFT = "code_drift"
TREE_DIRTY = "tree_dirty"
REALIZING = "realizing"


def status_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / STATUS_FILENAME


def write_status(codoc_dir: str | Path, state: str, *, pending: int = 0, detail: str = "") -> Path:
    dest = status_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "state": state, "pending": pending, "detail": detail, "at": HLC.now().to_str()}
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, dest)
    return dest


def refresh_status(codoc_dir: str | Path, store, *, realizing: bool = False, detail: str = "") -> Path:
    """Derive state from pending proposals and write it. ``realizing`` overrides."""
    pending = len(store.pending_events())
    if realizing:
        state = REALIZING
    elif pending:
        state = CODE_DRIFT
    else:
        state = IN_SYNC
    return write_status(codoc_dir, state, pending=pending, detail=detail)
