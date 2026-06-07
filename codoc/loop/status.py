"""``.codoc/status.json`` — the pipeline state the IDE surfaces in its status bar.

Five states answer "where are code and intent relative to each other right now?":

  ``in_sync``       no pending proposals; tree and code agree.
  ``code_drift``    code changed and Loop A raised proposals awaiting review.
  ``tree_dirty``    tree.codoc was edited; the code change has not been realized yet.
  ``awaiting_impl`` tree edits were accepted and queued in ``.codoc/realize.md``
                    for the live Claude Code session to implement (``/codoc:realize``).
  ``realizing``     a coding agent is implementing tree edits right now.

The loops write this file at the end of each pass (``awaiting_impl`` when Loop B
queues directives for the session). The IDE watches the file and never has to poll.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from codoc.loop.filenames import REALIZE_FILENAME
from codoc.model.hlc import HLC

STATUS_FILENAME = "status.json"

IN_SYNC = "in_sync"
CODE_DRIFT = "code_drift"
TREE_DIRTY = "tree_dirty"
AWAITING_IMPL = "awaiting_impl"
REALIZING = "realizing"


def status_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / STATUS_FILENAME


def _realize_queue_size(codoc_dir: str | Path) -> int:
    """Directive count in a queued ``realize.md`` (0 if absent/empty).

    Loop B writes one ``### N.`` heading per directive; we count them so the
    status can report how many changes are awaiting implementation.
    """
    try:
        text = (Path(codoc_dir) / REALIZE_FILENAME).read_text()
    except OSError:
        return 0
    if not text.strip():
        return 0
    return len(re.findall(r"(?m)^### ", text)) or 1


def write_status(codoc_dir: str | Path, state: str, *, pending: int = 0, detail: str = "") -> Path:
    dest = status_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "state": state, "pending": pending, "detail": detail, "at": HLC.now().to_str()}
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, dest)
    return dest


def refresh_status(
    codoc_dir: str | Path,
    store,
    *,
    realizing: bool = False,
    awaiting_impl: bool = False,
    pending: int | None = None,
    detail: str = "",
) -> Path:
    """Derive state from pending proposals and write it.

    ``awaiting_impl`` wins over ``realizing`` wins over the proposal-count
    default. ``pending`` overrides the displayed count (Loop B passes the
    directive count for ``awaiting_impl``); when ``None`` it is the number of
    pending proposals.
    """
    n_proposals = len(store.pending_events())
    count = n_proposals if pending is None else pending
    if awaiting_impl:
        state = AWAITING_IMPL
    elif realizing:
        state = REALIZING
    elif (queued := _realize_queue_size(codoc_dir)):
        # A realize.md queued by Loop B is an active obligation and outranks
        # code_drift (matching the documented awaiting_impl > code_drift order): a
        # later code-side pass that passes no awaiting_impl flag must not report
        # in_sync/code_drift and orphan the directive — the IDE would stop prompting
        # /codoc:realize. The file self-clears when /codoc:realize finishes, so this
        # is transient; pending proposals still render inline in the tree regardless.
        state = AWAITING_IMPL
        if pending is None:
            count = queued
        if not detail:
            detail = f"{queued} change(s) ready to implement — run /codoc:realize"
    elif n_proposals:
        state = CODE_DRIFT
    else:
        state = IN_SYNC
    return write_status(codoc_dir, state, pending=count, detail=detail)
