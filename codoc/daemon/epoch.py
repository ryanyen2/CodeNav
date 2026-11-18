"""The agent epoch: ``.codoc/activity.json``, written by the coding agent's
hooks and read by the daemon.

An epoch is one agent session's hold on the code. While it is open the daemon
does not reflect code changes, since the agent is mid-edit; when it closes, the
daemon reflects everything the session touched in one pass.

Schema (version 1)::

    {
      "version": 1,
      "epoch": {"id": "ep-<session_id>", "open": true, "started_at": "<iso>", "ended_at": null},
      "touched": ["src/theme.py"]
    }

``touched`` is the repo-relative files the turn's tools read or wrote. ``open``
is a lease, not a flag: a session that is hard-killed never runs the hook that
would clear it, so a reader trusts it only while the file keeps being written
(``epoch_alive``, ``EPOCH_STALE_SECONDS``).

The file is not intent and is safe to delete; the next session start rewrites it.
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from pathlib import Path

from codoc.workspace.layout import ACTIVITY, locked, read_json, write_json

# How long an open epoch is trusted without a fresh write to activity.json.
# Hooks renew the file on Edit/Write/Read tool calls only, so a live session in
# a long Bash or inference stretch is silent for minutes; this has to outlast that.
EPOCH_STALE_SECONDS = 900


def _empty_activity() -> dict:
    """A fresh empty document (fresh nested dicts, because callers mutate in place)."""
    return {
        "version": 1,
        "epoch": {"id": "", "open": False, "started_at": None, "ended_at": None},
        "touched": [],
    }


def activity_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / ACTIVITY


def _lock(codoc_dir: str | Path):
    return locked(Path(codoc_dir) / (ACTIVITY + ".lock"), timeout=5)


def read_activity(codoc_dir: str | Path) -> dict:
    """Return the parsed activity.json or an empty document if absent / corrupt."""
    data = read_json(activity_path(codoc_dir))
    return data if isinstance(data, dict) else _empty_activity()


def read_epoch(codoc_dir: str | Path) -> dict | None:
    """Return the ``epoch`` block, or None if the file is absent / corrupt."""
    data = read_activity(codoc_dir)
    ep = data.get("epoch")
    if not ep or not ep.get("id"):
        return None
    return ep


def epoch_alive(codoc_dir: str | Path, *, now: float | None = None,
                ttl: float = EPOCH_STALE_SECONDS) -> bool:
    """True iff activity.json says ``epoch.open`` AND the file was written
    within ``ttl`` seconds, so a hard-killed session stops reading as active."""
    path = activity_path(codoc_dir)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    if not bool((read_activity(codoc_dir).get("epoch") or {}).get("open")):
        return False
    if now is None:
        now = _time.time()
    return (now - mtime) <= ttl


def epoch_touched_files(codoc_dir: str | Path) -> list[str]:
    """Return the list of files (repo-relative paths) touched in the last epoch."""
    return list(read_activity(codoc_dir).get("touched") or [])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_activity(codoc_dir: str | Path, data: dict) -> None:
    """Write activity.json atomically, under the lock concurrent hook
    invocations and the realize runner share."""
    with _lock(codoc_dir):
        write_json(activity_path(codoc_dir), data)


def close_epoch(codoc_dir: str | Path, expected_id: str, *,
                now: float | None = None, stale_after: float | None = None) -> bool:
    """Mark a stale epoch closed in activity.json. Returns True iff it closed one.

    The read, the identity check and the write happen under the activity lock,
    so a ``SessionStart`` that replaces the file with a fresh epoch is never
    overwritten by a lagging heal:

    * ``expected_id``: the epoch id the caller saw open. If the file does
      not name it, a newer session has taken the single epoch slot: no-op. An
      empty ``expected_id`` matches whatever open epoch is present.
    * ``stale_after``: when given, the file mtime is re-checked under the
      lock; a write that landed after the caller's own staleness check keeps
      the epoch alive: no-op.

    Best-effort: any failure (lock timeout, unwritable file) returns False
    rather than raising, so a heal never kills the daemon batch that called it.
    """
    dest = activity_path(codoc_dir)
    try:
        with _lock(codoc_dir):
            data = read_activity(codoc_dir)
            ep = data.get("epoch") or {}
            if expected_id and ep.get("id") != expected_id:
                return False  # a newer session took the epoch slot; leave it alone
            if stale_after is not None:
                try:
                    mtime = dest.stat().st_mtime
                except OSError:
                    return False
                if (now if now is not None else _time.time()) - mtime <= stale_after:
                    return False  # a fresh write landed in the check→lock window
            if not ep.get("open"):
                return False  # already closed: nothing to heal
            data["epoch"] = {**ep, "open": False, "ended_at": _now_iso()}
            write_json(dest, data)
            return True
    except Exception:  # noqa: BLE001 (heal is best-effort; never break the caller)
        return False
