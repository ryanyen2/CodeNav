"""The single cross-process lock that serializes a whole codoc loop pass.

Both loops mutate the SAME state — the SQLite store and the derived ``tree.codoc`` —
in a multi-step read → diff → mutate → re-render sequence. SQLite's WAL serializes
individual writes, but NOT that whole sequence: if a second pass mutates the store
between the first pass's diff and its re-render, the first pass writes a stale
``tree.codoc`` (a phantom revert) or computes its ops against a snapshot that has
already moved. The loops can run in genuinely separate processes — the watch daemon,
a CLI ``codoc sync`` / ``codoc reflect``, the ``codoc serve`` hub, and the Stop-hook
reflection — so an in-process mutex is not enough.

ONE reentrant cross-process ``FileLock`` held for the ENTIRE pass of EITHER loop
makes them mutually exclusive: Loop A never interleaves with Loop B, and neither
interleaves with another instance of itself. Reentrant (filelock counts per process),
so a pass that internally re-enters (e.g. a loop calling a helper that also takes the
lock) does not self-deadlock. The loops never NEST one inside the other within a
process — ``codoc sync`` runs Loop B then Loop A sequentially — so a single shared
lock cannot deadlock. Cached per repo, mirroring ``edits._edits_lock``.

This lives in its own leaf module (no codoc imports) so both ``loop_a`` and
``loop_b`` can use it without an import cycle.
"""
from __future__ import annotations

import os
from pathlib import Path

_loop_locks: dict[str, object] = {}


def loop_lock(codoc_dir: str | os.PathLike):
    """The shared, reentrant, cross-process lock for a whole codoc loop pass
    (Loop A and Loop B both acquire it). See the module docstring."""
    from filelock import FileLock

    key = str(Path(codoc_dir) / "loop.lock")
    lock = _loop_locks.get(key)
    if lock is None:
        # 120s: a generous CEILING, not a typical wait. Uncontended acquisition is
        # instant; the wait only materializes when another loop genuinely holds the lock
        # — and an authority reconcile (cocoindex update_index on a large repo) can take
        # tens of seconds, so a tight timeout would spuriously fail a waiting pass. The OS
        # releases the lock if the holder crashes, and the daemon wraps every cycle in
        # safe_process_batch, so a rare real timeout skips one cycle rather than wedging.
        lock = FileLock(key, timeout=120)
        _loop_locks[key] = lock
    return lock
