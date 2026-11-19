"""The agent epoch: ``.codoc/activity.json``, written by the coding agent's
hooks, its MCP server and a realize pass, read by the daemon and the Stop hook.

An epoch is one agent session's hold on the code. While it is open the daemon
defers reflecting code changes, since the agent is mid-edit; when it closes,
the daemon reflects everything the session touched in one pass.

Schema (version 1)::

    {
      "version": 1,
      "epoch": {"id": "ep-<session_id>", "open": true, "started_at": "<iso>", "ended_at": null},
      "touched": ["src/theme.py"],
      "cited": ["s-3f9a:12"],
      "realize_seen": 2
    }

``touched`` is the repo-relative files the turn's tools read or wrote.
``cited`` is the hand-offs the turn's MCP calls cited in ``caused_by``. Both
reset when the epoch opens. ``realize_seen`` is the size of the realize queue
the hooks last announced to the session. ``open`` is a lease, not a flag: a
session that is hard-killed never runs the hook that would clear it, so a
reader trusts it only while the file keeps being written (``epoch_alive``,
``EPOCH_STALE_SECONDS``).

Every write is a read-modify-write under one lock, so two hooks firing
together, or a hook and the MCP server, never drop each other's change. The
file is not intent and is safe to delete; the next session start rewrites it.
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from pathlib import Path

from codoc.workspace.layout import ACTIVITY, locked, read_json, write_json

# How long an open epoch is trusted without a fresh write to activity.json.
# Hooks renew the file on tool calls that name a file, so a live session in a
# long Bash or inference stretch is silent for minutes; this has to outlast that.
EPOCH_STALE_SECONDS = 900


def activity_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / ACTIVITY


def _lock(codoc_dir: str | Path):
    return locked(Path(codoc_dir) / (ACTIVITY + ".lock"), timeout=5)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fresh(session_id: str | None) -> dict:
    """A new epoch, open, with nothing touched or cited."""
    return {"version": 1,
            "epoch": {"id": f"ep-{session_id or int(_time.time())}", "open": True, "started_at": _now_iso(),
                      "ended_at": None},
            "touched": [], "cited": []}


# ── reading ──────────────────────────────────────────────────────────────────

def read_activity(codoc_dir: str | Path) -> dict:
    """The parsed activity.json, or an empty document when absent or corrupt."""
    data = read_json(activity_path(codoc_dir))
    if isinstance(data, dict):
        return data
    return {"version": 1, "epoch": {"id": "", "open": False, "started_at": None, "ended_at": None}, "touched": []}


def read_epoch(codoc_dir: str | Path) -> dict | None:
    """The ``epoch`` block, or None when there is none."""
    ep = read_activity(codoc_dir).get("epoch")
    return ep if ep and ep.get("id") else None


def epoch_age(codoc_dir: str | Path, now: float | None = None) -> float:
    """Seconds since activity.json was last written; infinite when it is absent."""
    try:
        mtime = activity_path(codoc_dir).stat().st_mtime
    except OSError:
        return float("inf")
    return (_time.time() if now is None else now) - mtime


def epoch_alive(codoc_dir: str | Path, *, now: float | None = None, ttl: float = EPOCH_STALE_SECONDS) -> bool:
    """Whether activity.json says ``epoch.open`` and was written within
    ``ttl`` seconds, so a hard-killed session stops reading as active."""
    return bool((read_activity(codoc_dir).get("epoch") or {}).get("open")) and epoch_age(codoc_dir, now) <= ttl


def epoch_touched_files(codoc_dir: str | Path) -> list[str]:
    """The repo-relative files the last epoch touched."""
    return list(read_activity(codoc_dir).get("touched") or [])


def repo_relative(path: str, codoc_dir: str | Path) -> str | None:
    """``path`` relative to the repository ``codoc_dir`` belongs to, as
    ``touched`` records it; None outside the repository."""
    try:
        return str(Path(path).resolve().relative_to(Path(codoc_dir).resolve().parent))
    except ValueError:
        return None


# ── writing ──────────────────────────────────────────────────────────────────

def write_activity(codoc_dir: str | Path, data: dict) -> None:
    """Replace activity.json, under the lock."""
    with _lock(codoc_dir):
        write_json(activity_path(codoc_dir), data)


def open_epoch(codoc_dir: str | Path, session_id: str | None) -> None:
    """A session starts: a new open epoch, replacing whatever was there."""
    write_activity(codoc_dir, _fresh(session_id))


def reopen_epoch(codoc_dir: str | Path, session_id: str | None) -> None:
    """A turn starts (the person's prompt, or a tool call before it writes):
    reopen this session's closed epoch with nothing touched or cited. A closed
    epoch of another session, one that missed its start, is replaced by a new
    one. An open epoch, this session's or a concurrent one's, is left alone."""
    with _lock(codoc_dir):
        data = read_activity(codoc_dir)
        ep = data.get("epoch") or {}
        if ep.get("open"):
            return
        mine = f"ep-{session_id}" if session_id else ""
        if ep.get("id") and mine and ep["id"] != mine:
            data = _fresh(session_id)
        else:
            data["epoch"] = {**ep, "id": ep.get("id") or mine, "open": True, "ended_at": None,
                             "started_at": ep.get("started_at") or _now_iso()}
            data["touched"], data["cited"] = [], []
        write_json(activity_path(codoc_dir), data)


def touch_epoch(codoc_dir: str | Path, rel: str) -> None:
    """Add a repo-relative file to what this turn touched. The write renews
    the epoch's lease even when the file is already listed."""
    with _lock(codoc_dir):
        data = read_activity(codoc_dir)
        touched = list(data.get("touched") or [])
        if rel not in touched:
            touched.append(rel)
        write_json(activity_path(codoc_dir), {**data, "touched": touched})


def note_cited(codoc_dir: str | Path, handoff: str) -> None:
    """Record on the open epoch that this turn cited ``handoff``, for the
    Stop hook."""
    with _lock(codoc_dir):
        data = read_activity(codoc_dir)
        cited = list(data.get("cited") or [])
        if (data.get("epoch") or {}).get("open") and handoff not in cited:
            write_json(activity_path(codoc_dir), {**data, "cited": [*cited, handoff]})


def note_queue_size(codoc_dir: str | Path, n: int) -> bool:
    """Record that the session was told the realize queue holds ``n`` items.
    True when that is more than it was last told: the queue grew. An empty
    queue resets the count, so the next queue is announced again."""
    with _lock(codoc_dir):
        data = read_activity(codoc_dir)
        seen = int(data.get("realize_seen") or 0)
        if n > seen or (n == 0 and seen):
            write_json(activity_path(codoc_dir), {**data, "realize_seen": n})
        return n > seen


def close_epoch(codoc_dir: str | Path, expected_id: str, *,
                now: float | None = None, stale_after: float | None = None) -> bool:
    """Close the open epoch. Returns True iff it closed one.

    * ``expected_id``: the epoch the caller means. If the file names another,
      a newer session has taken the single epoch slot and nothing changes. An
      empty ``expected_id`` matches whatever epoch is open.
    * ``stale_after``: when given, the epoch closes only if the file has not
      been written for that long, checked under the lock, so a write that
      landed after the caller's own check keeps the epoch alive.

    The read, the checks and the write happen under the lock, so a session
    start that replaces the file is never overwritten by a lagging close. Any
    failure (a lock timeout, an unwritable file) returns False rather than
    raising: a hook or a daemon batch that closes never fails for it."""
    try:
        with _lock(codoc_dir):
            data = read_activity(codoc_dir)
            ep = data.get("epoch") or {}
            if expected_id and ep.get("id") != expected_id:
                return False
            if stale_after is not None and epoch_age(codoc_dir, now) <= stale_after:
                return False
            if not ep.get("open"):
                return False
            data["epoch"] = {**ep, "open": False, "ended_at": _now_iso()}
            write_json(activity_path(codoc_dir), data)
            return True
    except Exception:  # noqa: BLE001 (closing is best-effort; never break the caller)
        return False
