"""Starting a realize pass: ``codoc realize`` and ``codoc watch --auto-realize``.

A hand-off waits in ``.codoc/realize.md`` until an agent builds it. A person's
own Claude Code session does that with ``/codoc:realize``. ``codoc realize``
starts a pass in the foreground, and with ``--auto-realize`` the daemon starts
one whenever work is queued and no session is live, so a plan accepted with
nobody at the keyboard still lands code.

Both go through :func:`start_realize`. Two engines: the Claude Agent SDK
runner (:mod:`codoc.claude.sdk_realize`, chosen by ``auto`` when it is
installed: it prints one line per action and records the files it touches in
``activity.json``) and ``claude -p "/codoc:realize"``. Either way the agent
uses the MCP server and commands the repository already has installed.
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


class EngineMissing(RuntimeError):
    """The engine asked for is not installed. The message says what to install."""


def find_claude() -> str | None:
    """The ``claude`` CLI on PATH, or None when it is not installed."""
    return shutil.which("claude")


def start_realize(root_dir: str, codoc_dir: str, *, engine: str = "auto", permission_mode: str = "acceptEdits",
                  detail: str) -> subprocess.Popen:
    """Start a realize pass in ``root_dir`` and return its process.

    ``sdk`` runs ``python -m codoc.claude.sdk_realize``; ``cli`` runs ``claude -p
    "/codoc:realize"``; ``auto`` takes the SDK when it is importable. The pass
    takes the realizing lease with ``detail`` and is named its owner in the
    child's environment, so its own agent does not read the lease as another
    pass's and stand down. Raises :class:`EngineMissing` when the engine is not
    installed."""
    from codoc.claude.sdk_realize import resolve_engine, sdk_available

    if resolve_engine(engine) == "sdk":
        if not sdk_available():
            raise EngineMissing("claude-agent-sdk is not installed. Run pip install 'codoc[sdk]', or use --engine cli")
        argv = [sys.executable, "-m", "codoc.claude.sdk_realize", root_dir, permission_mode]
    else:
        claude = find_claude()
        if claude is None:
            raise EngineMissing("the `claude` CLI is not on PATH")
        argv = [claude, "-p", "/codoc:realize", "--permission-mode", permission_mode]
    owner = uuid.uuid4().hex
    try:
        write_realizing(codoc_dir, detail=detail, owner=owner)
    except OSError:  # status is advisory
        pass
    return subprocess.Popen(argv, cwd=root_dir, env={**os.environ, REALIZE_OWNER_ENV: owner}, stdin=subprocess.DEVNULL)


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


def auto_realizer(root_dir: str, codoc_dir: str):
    """A callable for ``Daemon(after_batch=…)``: after each batch, start a pass
    if one should run and the last one this started has finished."""
    proc: subprocess.Popen | None = None

    def run(daemon) -> None:
        nonlocal proc
        in_flight = proc is not None and proc.poll() is None
        if daemon.state.epoch_open or not should_spawn(codoc_dir, in_flight=in_flight):
            return
        try:
            proc = start_realize(root_dir, codoc_dir, detail="building: codoc watch --auto-realize")
        except EngineMissing:
            proc = None

    return run
