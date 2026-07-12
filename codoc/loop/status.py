"""``.codoc/status.json`` — the pipeline state the IDE surfaces in its status bar.

Five states answer "where are code and intent relative to each other right now?":

  ``in_sync``       no pending proposals; tree and code agree.
  ``code_drift``    code changed and Loop A raised proposals awaiting review.
  ``tree_dirty``    tree.codoc was edited; the code change has not been realized yet.
  ``awaiting_impl`` tree edits were accepted and queued in ``.codoc/realize.md``
                    for the live Claude Code session to implement (``/codoc:sync``).
  ``realizing``     a coding agent is implementing tree edits right now.

The loops write this file at the end of each pass (``awaiting_impl`` when Loop B
queues directives for the session). The IDE watches the file and never has to poll.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from codoc.loop.filenames import REALIZE_FILENAME
from codoc.loop.fsio import atomic_write_json, read_json
from codoc.model.hlc import HLC

STATUS_FILENAME = "status.json"

IN_SYNC = "in_sync"
CODE_DRIFT = "code_drift"
TREE_DIRTY = "tree_dirty"
AWAITING_IMPL = "awaiting_impl"
REALIZING = "realizing"

# How long an on-disk `realizing` state is trusted without a fresh progress write
# before it's treated as dead. A live pass renews this lease on every directive
# (`codoc_realize_progress` / sdk_realize's per-directive write), so this is only
# ever reached by a pass that crashed or was cancelled mid-queue.
REALIZING_LEASE_SECONDS = 300


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
    atomic_write_json(dest, {
        "version": 1, "state": state, "pending": pending,
        "detail": detail, "at": HLC.now().to_str(),
    })
    return dest


def _realizing_is_fresh(codoc_dir: str | Path, *, now: float | None = None,
                        ttl: float = REALIZING_LEASE_SECONDS) -> bool:
    """True iff status.json currently says ``realizing`` AND was written within
    ``ttl`` seconds AND a queue is still present (``realize.md``).

    No caller ever passed ``realizing=True`` to :func:`refresh_status` — it was
    only ever set by a direct ``write_status(REALIZING, ...)`` from
    ``codoc_realize_progress`` / ``sdk_realize`` / ``autorealize``. That meant the
    very act of checking status (``codoc_status``, called by every ``/codoc:sync``
    as step 1) silently recomputed past it, which cuts both ways: a genuinely live
    pass could be clobbered back to ``awaiting_impl`` by an unrelated status check
    (letting two realize passes race the same queue), while a crashed pass with no
    such check in flight stayed stuck forever. This lease fixes both: fresh ⇒
    preserved (protects a live pass), stale ⇒ decays to the ground truth (un-wedges
    a fresh ``/codoc:sync`` after a cancelled run) — see WS1.5 in
    ``docs/plans/2026-07-11-001-loop-robustness-audit-and-plan.md``.
    """
    if not _realize_queue_size(codoc_dir):
        return False  # nothing queued → nothing can still be "in progress"
    data = read_json(status_path(codoc_dir), default={})
    if not isinstance(data, dict) or data.get("state") != REALIZING:
        return False
    try:
        mtime = status_path(codoc_dir).stat().st_mtime
    except OSError:
        return False
    if now is None:
        now = time.time()
    return (now - mtime) <= ttl


def refresh_status(
    codoc_dir: str | Path,
    store,
    *,
    realizing: bool | None = None,
    awaiting_impl: bool = False,
    pending: int | None = None,
    detail: str = "",
) -> Path:
    """Derive state from pending proposals and write it.

    ``awaiting_impl`` wins over ``realizing`` wins over the proposal-count
    default. ``pending`` overrides the displayed count (Loop B passes the
    directive count for ``awaiting_impl``); when ``None`` it is the number of
    pending proposals.

    ``realizing=None`` (the default) infers liveness from the on-disk lease
    (:func:`_realizing_is_fresh`) instead of always recomputing past it — pass an
    explicit ``True``/``False`` to override (e.g. a realize engine's own
    end-of-pass cleanup, which authoritatively knows the pass just ended and must
    force a recompute regardless of how fresh the last progress write looked).
    """
    if realizing is None:
        realizing = _realizing_is_fresh(codoc_dir)
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
        # /codoc:sync. The file self-clears when /codoc:sync finishes, so this
        # is transient; pending proposals still render inline in the tree regardless.
        state = AWAITING_IMPL
        if pending is None:
            count = queued
        if not detail:
            detail = f"{queued} change(s) ready to implement — run /codoc:sync"
    elif n_proposals:
        state = CODE_DRIFT
    else:
        state = IN_SYNC
    return write_status(codoc_dir, state, pending=count, detail=detail)
