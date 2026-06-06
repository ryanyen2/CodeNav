"""Opt-in headless realize — the unattended fallback for ``codoc watch``.

The default loop hands code-implying tree edits to the *live* Claude Code session
(`.codoc/realize.md` + the ``/codoc:realize`` command). That requires a human to
be (or soon be) at the keyboard. With ``codoc watch --auto-realize`` the daemon
instead spawns a **headless** ``claude -p "/codoc:realize"`` to implement the queue
when no interactive session is around — so accepting a plan with nobody watching
still lands code.

This deliberately re-introduces the headless spawn the 2026-05-29 rewrite removed,
but isolated here and gated behind the explicit flag, so the in-session model stays
the default. The spawned agent uses the same MCP server + commands the repo already
has installed; it reads ``realize.md``, implements each directive, reflects, and
deletes the file — at which point ``should_spawn`` goes quiet again.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from codoc.agent.hook import REALIZE_FILENAME
from codoc.loop import status
from codoc.loop.activity import activity_path


def find_claude() -> str | None:
    """Locate the ``claude`` CLI on PATH, or None if it isn't installed."""
    return shutil.which("claude")


def _epoch_open(codoc_dir: str) -> bool:
    """True if a live agent session owns this repo (don't headless-spawn over it)."""
    try:
        data = json.loads(activity_path(codoc_dir).read_text())
    except (OSError, json.JSONDecodeError):
        return False
    ep = data.get("epoch") or {}
    return bool(ep.get("open"))


def should_spawn(codoc_dir: str, *, in_flight: bool) -> bool:
    """Decide whether to launch a headless realize pass right now.

    Spawn only when there is a queued ``realize.md`` to implement, nothing is
    already in flight (we launched one that hasn't finished), and no interactive
    session is open to do it instead."""
    if in_flight:
        return False
    if not (Path(codoc_dir) / REALIZE_FILENAME).exists():
        return False
    return not _epoch_open(codoc_dir)


def spawn_realize(root_dir: str, codoc_dir: str) -> subprocess.Popen | None:
    """Launch ``claude -p "/codoc:realize"`` detached in ``root_dir``.

    Returns the Popen handle (so the daemon can track liveness), or None if the
    ``claude`` CLI isn't available. Sets status to ``realizing`` so the IDE reflects
    that an (unattended) implementation pass is underway."""
    claude = find_claude()
    if claude is None:
        return None
    proc = subprocess.Popen(
        [claude, "-p", "/codoc:realize"],
        cwd=root_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    try:
        status.write_status(codoc_dir, status.REALIZING, detail="implementing (headless) — codoc watch --auto-realize")
    except Exception:  # noqa: BLE001 — status is advisory
        pass
    return proc
