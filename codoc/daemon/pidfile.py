"""Whether a daemon owns a checkout: ``.codoc/watch.pid``.

The daemon writes its pid when it starts and removes the file when it exits.
Every other writer asks ``daemon_running`` to decide between the daemon's inbox
and applying for itself.
"""
from __future__ import annotations

import os
from pathlib import Path

from codoc.workspace.layout import PIDFILE


def pid_alive(pid: int) -> bool:
    """Whether a process with this pid exists (signal 0 probes without signalling)."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def write_pidfile(codoc_dir: str | Path) -> None:
    (Path(codoc_dir) / PIDFILE).write_text(str(os.getpid()))


def clear_pidfile(codoc_dir: str | Path) -> None:
    """Remove the pidfile if it is this process's."""
    p = Path(codoc_dir) / PIDFILE
    try:
        if p.read_text().strip() == str(os.getpid()):
            p.unlink()
    except OSError:
        pass


def daemon_running(codoc_dir: str | Path) -> bool:
    """Whether a daemon other than this process is alive. A pidfile naming a
    dead process is removed."""
    p = Path(codoc_dir) / PIDFILE
    try:
        pid = int(p.read_text().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return False
    if pid_alive(pid):
        return True
    p.unlink(missing_ok=True)
    return False
