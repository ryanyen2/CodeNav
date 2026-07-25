"""Opt-in headless realize — the unattended fallback for ``codoc watch``.

The default loop hands code-implying tree edits to the *live* Claude Code session
(`.codoc/realize.md` + the ``/codoc:sync`` command). That requires a human to
be (or soon be) at the keyboard. With ``codoc watch --auto-realize`` the daemon
instead spawns an unattended pass to implement the queue when no interactive
session is around — so accepting a plan with nobody watching still lands code.
Two engines (``spawn_realize(engine=…)``): the Claude Agent SDK runner
(:mod:`codoc.loop.sdk_realize` — preferred when installed: live per-action
readout + IDE activity signals) and the original blind ``claude -p "/codoc:sync"``.

This deliberately re-introduces the headless spawn the 2026-05-29 rewrite removed,
but isolated here and gated behind the explicit flag, so the in-session model stays
the default. The spawned agent uses the same MCP server + commands the repo already
has installed; it reads ``realize.md``, implements each directive, reflects, and
deletes the file — at which point ``should_spawn`` goes quiet again.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from codoc.loop.filenames import REALIZE_FILENAME
from codoc.loop import status
from codoc.loop.activity import epoch_alive


def find_claude() -> str | None:
    """Locate the ``claude`` CLI on PATH, or None if it isn't installed."""
    return shutil.which("claude")


def _epoch_open(codoc_dir: str) -> bool:
    """True if a live agent session owns this repo (don't headless-spawn over it).

    Lease-based (`epoch_alive`), not a raw flag read: a session hard-killed
    without firing `Stop` would otherwise leave `epoch.open=true` forever and
    permanently starve `--auto-realize` of any queue it could pick up.

    Uses the DAEMON-grade TTL (`EPOCH_STALE_SECONDS`, ~15 min), not the 90 s UI
    TTL: hooks only renew activity.json on Edit/Write/Read tool calls, so a
    LIVE session in a long Bash/inference stretch goes activity-silent well
    past 90 s — spawning a headless pass over it would race two agents on the
    same queue. The plan's TTL tiering (WS1.1) assigns seconds-scale to UI
    display and 900 s to daemon decisions; spawning is a daemon decision."""
    from codoc.loop.watch import EPOCH_STALE_SECONDS

    return epoch_alive(codoc_dir, ttl=EPOCH_STALE_SECONDS)


def should_spawn(codoc_dir: str, *, in_flight: bool) -> bool:
    """Decide whether to launch a headless realize pass right now.

    Spawn only when there is a queued ``realize.md`` to implement, nothing is
    already in flight (we launched one that hasn't finished), no interactive
    session is open to do it instead, and no OTHER realize pass holds a fresh
    ``realizing`` lease (an interactive ``/codoc:sync`` renews status.json per
    directive even when its epoch looks activity-silent; a crashed pass's lease
    decays on its own, after which spawning resumes)."""
    if in_flight:
        return False
    if not (Path(codoc_dir) / REALIZE_FILENAME).exists():
        return False
    if status.realizing_is_fresh(codoc_dir):
        return False
    return not _epoch_open(codoc_dir)


def spawn_realize(root_dir: str, codoc_dir: str, *, engine: str = "auto") -> subprocess.Popen | None:
    """Launch an unattended realize pass detached in ``root_dir``.

    Two engines: ``sdk`` runs ``python -m codoc.loop.sdk_realize`` (Claude Agent
    SDK — streams a compact per-action readout to the daemon's terminal and
    feeds ``activity.json`` so the IDE shows live signals) and ``cli`` is the
    original blind ``claude -p "/codoc:sync"``. ``auto`` prefers the SDK when
    the package is importable. Returns the Popen handle (so the daemon can
    track liveness), or None if the chosen engine isn't available. Sets status
    to ``realizing`` so the IDE reflects that an implementation pass is underway."""
    import sys

    from codoc.loop.sdk_realize import resolve_engine, sdk_available

    engine = resolve_engine(engine)

    if engine == "sdk":
        if not sdk_available():
            return None
        # stdout/stderr inherit: the runner's per-action lines land in the
        # daemon's terminal — the user sees what the agent does without a UI.
        proc = subprocess.Popen(
            [sys.executable, "-m", "codoc.loop.sdk_realize", root_dir],
            cwd=root_dir,
            stdin=subprocess.DEVNULL,
        )
        detail = "implementing (sdk) — codoc watch --auto-realize"
    else:
        claude = find_claude()
        if claude is None:
            return None
        proc = subprocess.Popen(
            [claude, "-p", "/codoc:sync"],
            cwd=root_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        detail = "implementing (headless) — codoc watch --auto-realize"
    try:
        status.write_status(codoc_dir, status.REALIZING, detail=detail)
    except Exception:  # noqa: BLE001 — status is advisory
        pass
    return proc
