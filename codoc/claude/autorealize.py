"""Unattended realize: ``codoc watch --auto-realize``.

By default a hand-off waits in ``.codoc/realize.md`` for the person's own
Claude Code session to run ``/codoc:sync``. With ``--auto-realize`` the daemon
starts a pass itself whenever work is queued and no session is live, so
accepting a plan with nobody at the keyboard still lands code.

Two engines (``spawn_realize(engine=…)``): the Claude Agent SDK runner
(:mod:`codoc.claude.sdk_realize`, preferred when installed: it prints one line
per action and feeds ``activity.json``) and a plain ``claude -p "/codoc:sync"``.
Either way the agent uses the MCP server and commands the repository already
has installed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from codoc.daemon.epoch import epoch_alive
from codoc.daemon.publish import REALIZE_OWNER_ENV, realizing_is_fresh, write_realizing
from codoc.workspace.layout import REALIZE


def find_claude() -> str | None:
    """Locate the ``claude`` CLI on PATH, or None if it isn't installed."""
    return shutil.which("claude")


def should_spawn(codoc_dir: str, *, in_flight: bool) -> bool:
    """Whether to start an unattended realize pass right now.

    Only when ``realize.md`` holds queued work, the pass this caller started
    has finished, no other pass holds a fresh ``realizing`` lease (a crashed
    pass's lease lapses on its own), and no agent session is live to do the
    work instead."""
    if in_flight:
        return False
    if not (Path(codoc_dir) / REALIZE).exists():
        return False
    if realizing_is_fresh(codoc_dir):
        return False
    return not epoch_alive(codoc_dir)


def spawn_realize(root_dir: str, codoc_dir: str, *, engine: str = "auto") -> subprocess.Popen | None:
    """Start an unattended realize pass in ``root_dir``.

    ``sdk`` runs ``python -m codoc.claude.sdk_realize``; ``cli`` runs
    ``claude -p "/codoc:sync"``; ``auto`` prefers the SDK when it is
    importable. Returns the Popen handle (so the caller can tell when it has
    finished), or None if the chosen engine isn't available. Takes the
    realizing lease, naming the pass as its owner in the child's environment."""
    from codoc.claude.sdk_realize import resolve_engine, sdk_available

    engine = resolve_engine(engine)
    owner = uuid.uuid4().hex
    env = {**os.environ, REALIZE_OWNER_ENV: owner}

    if engine == "sdk":
        if not sdk_available():
            return None
        # stdout/stderr inherit: the runner's per-action lines land in the
        # daemon's terminal.
        proc = subprocess.Popen(
            [sys.executable, "-m", "codoc.claude.sdk_realize", root_dir],
            cwd=root_dir, env=env,
            stdin=subprocess.DEVNULL,
        )
        detail = "implementing (sdk) — codoc watch --auto-realize"
    else:
        claude = find_claude()
        if claude is None:
            return None
        proc = subprocess.Popen(
            [claude, "-p", "/codoc:sync"],
            cwd=root_dir, env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        detail = "implementing (headless) — codoc watch --auto-realize"
    try:
        write_realizing(codoc_dir, detail=detail, owner=owner)
    except Exception:  # noqa: BLE001 (status is advisory)
        pass
    return proc


def auto_realizer(root_dir: str, codoc_dir: str):
    """A callable for ``Daemon(after_batch=…)``: after each batch, start a pass
    if one should run and the last one this started has finished."""
    proc: subprocess.Popen | None = None

    def run(daemon) -> None:
        nonlocal proc
        in_flight = proc is not None and proc.poll() is None
        if daemon.state.epoch_open or not should_spawn(codoc_dir, in_flight=in_flight):
            return
        proc = spawn_realize(root_dir, codoc_dir)

    return run
