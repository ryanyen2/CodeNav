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

import time as _time
from datetime import datetime, timezone
from pathlib import Path

from codoc.loop.fsio import atomic_write_json, read_json

ACTIVITY_FILENAME = "activity.json"

# Per-feature reflection phases surfaced to the IDE doc view (skeleton → fill-in):
#   "editing"    — an agent is writing code bound to this feature right now
#   "reflecting" — the agent is binding the change into the tree
#   "done"       — reflection landed (content just updated); overrides the
#                  "editing" the watch/touched signal would otherwise imply
PHASE_EDITING = "editing"
PHASE_REFLECTING = "reflecting"
PHASE_DONE = "done"

# How long an `epoch.open=True` is trusted without a fresh activity.json write
# before liveness readers treat it as dead. An agent hard-killed (Esc, SIGKILL,
# closed window/terminal) never fires the `Stop` hook that would otherwise clear
# `epoch.open` — trusting the raw flag forever shows "agent working" long after
# the agent is gone. Every hook write touches this file, so file mtime doubles as
# the lease's `last_seen` with no schema change.
EPOCH_UI_TTL_SECONDS = 90

# Same failure mode, feature granularity: `features[fid].phase` is only cleared by
# the Stop hook (`handle_stop` resets `features = {}`); an interrupted session
# leaves the doc view's skeleton/"editing" animation stuck on that feature forever.
FEATURE_PHASE_TTL_SECONDS = 120

def _empty_activity() -> dict:
    """A fresh empty document (fresh nested dicts — callers mutate in place)."""
    return {
        "version": 1,
        "epoch": {"id": "", "origin": "interactive", "open": False,
                  "started_at": None, "ended_at": None},
        "touched": {},
        "recent": [],
    }


def activity_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / ACTIVITY_FILENAME


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
                ttl: float = EPOCH_UI_TTL_SECONDS) -> bool:
    """True iff activity.json currently says ``epoch.open`` AND the file was
    written within ``ttl`` seconds. A LEASE, not a flag (see the TTL constants'
    docstrings above) — every reader of "is an agent currently active" should go
    through this instead of trusting ``epoch.open`` directly, so a hard-killed
    session (no ``Stop`` hook) self-heals instead of showing "active" forever."""
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
    data = read_activity(codoc_dir)
    touched: dict = data.get("touched") or {}
    return list(touched.keys())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_activity(codoc_dir: str | Path, data: dict) -> None:
    """Write activity.json atomically under the cross-process lock.

    Concurrent writers (hook invocations within one CC session, the MCP server)
    serialize on the lock file so the JSON is never corrupted."""
    from filelock import FileLock

    codoc_dir = Path(codoc_dir)
    dest = activity_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(codoc_dir / (ACTIVITY_FILENAME + ".lock")), timeout=5):
        atomic_write_json(dest, data)


def mark_feature_phase(codoc_dir: str | Path, feature_ids: list[str], phase: str) -> None:
    """Set the reflection ``phase`` for ``feature_ids`` in ``activity.json``.

    Merges into the existing file under a lock so concurrent writers (the hook
    process marking ``editing`` and the MCP server marking ``done``) don't clobber
    each other. Best-effort: any failure is swallowed — this only drives an
    animation, never correctness.
    """
    if not feature_ids:
        return
    codoc_dir = Path(codoc_dir)
    try:
        from filelock import FileLock

        with FileLock(str(codoc_dir / (ACTIVITY_FILENAME + ".lock")), timeout=5):
            data = read_activity(codoc_dir)
            feats = data.get("features")
            if not isinstance(feats, dict):
                feats = {}
            now = _now_iso()
            for fid in feature_ids:
                feats[fid] = {"phase": phase, "at": now}
            data["features"] = feats
            dest = activity_path(codoc_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(dest, data)
    except Exception:  # noqa: BLE001 — never break a tool/hook over an animation hint
        pass


def close_epoch(codoc_dir: str | Path, expected_id: str, *,
                now: float | None = None, stale_after: float | None = None) -> bool:
    """Atomically mark a stale epoch closed in activity.json. Returns True iff it
    actually closed an epoch.

    Holds the activity :class:`FileLock` across the read + identity check + write
    (mirroring :func:`mark_feature_phase`) so a concurrent ``SessionStart`` — which
    replaces activity.json wholesale with a fresh epoch — can never be clobbered by
    a lagging stale-epoch heal:

    * ``expected_id`` — the epoch id the caller observed open (the daemon's own
      ``state.last_epoch_id``, captured *before* any in-memory overwrite). If the
      on-disk epoch no longer names it, a newer session has taken the single-slot
      epoch → no-op. An empty ``expected_id`` matches whatever open epoch is present
      (the caller could not identify one), preserving legacy best-effort behavior.
    * ``stale_after`` — when given, the file mtime is *re-checked under the lock*; a
      fresh write that landed after the caller's staleness stat but before the lock
      keeps the epoch alive → no-op. This is what makes the "never clobber a
      genuinely fresh epoch" guarantee real rather than merely narrated: the old
      inline heal compared two post-staleness-check reads with each other, so a
      SessionStart landing in that window was trivially "same epoch" and got
      overwritten.

    Best-effort: any failure (lock timeout, unwritable file) returns False rather
    than raising — a heal must never kill the daemon cycle that called it.
    """
    from filelock import FileLock

    codoc_dir = Path(codoc_dir)
    dest = activity_path(codoc_dir)
    try:
        with FileLock(str(codoc_dir / (ACTIVITY_FILENAME + ".lock")), timeout=5):
            data = read_activity(codoc_dir)
            ep = data.get("epoch") or {}
            if expected_id and ep.get("id") != expected_id:
                return False  # a newer session took the epoch slot — leave it alone
            if stale_after is not None:
                try:
                    mtime = dest.stat().st_mtime
                except OSError:
                    return False
                if (now if now is not None else _time.time()) - mtime <= stale_after:
                    return False  # a fresh write landed in the check→lock window
            if not (ep.get("open") or data.get("features")):
                return False  # already closed and cleared — nothing to heal
            data["epoch"] = {**ep, "open": False, "ended_at": _now_iso()}
            data["features"] = {}
            dest.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(dest, data)
            return True
    except Exception:  # noqa: BLE001 — heal is best-effort; never break the caller
        return False


def epoch_written_files(codoc_dir: str | Path) -> list[str]:
    """Files the last epoch actually WROTE (``mode == "write"``).

    Distinct from :func:`epoch_touched_files`, which also counts reads — reporting
    a read as "written" overstates what an agent did and mis-scopes the post-write
    reflection. Loop B uses this so "agent wrote N files" counts only writes.
    """
    data = read_activity(codoc_dir)
    touched: dict = data.get("touched") or {}
    return [f for f, meta in touched.items() if (meta or {}).get("mode") == "write"]
