"""Whether a daemon owns a checkout: the lock on ``.codoc/watch.pid``.

A daemon holds an exclusive ``flock`` on the file from its start until it
exits, and writes its pid into it for a person to read. A daemon is alive
exactly while the lock is held. The OS drops the lock however the process
ends, so a file left by a killed daemon, or naming a pid another program has
since been given, reads as no daemon. Every other writer asks
``daemon_running`` to decide between the daemon's inbox and applying for
itself.

The file is never removed. A daemon that removed it as it exited could leave
one starting at that moment holding the lock on a file no longer at the path,
where no writer would see it.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

from codoc.workspace.layout import PIDFILE, hold_lock


def pid_alive(pid: int) -> bool:
    """Whether a process with this pid exists (signal 0 probes without signalling)."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def hold_pidfile(codoc_dir: str | Path) -> bool:
    """Take the daemon's lock on watch.pid and write this process's pid into
    it. The lock is kept until the process exits. False, with nothing taken,
    when another daemon holds it. A writer asking ``daemon_running`` holds the
    lock for an instant, which the one-second wait in ``hold_lock`` outlasts."""
    return hold_lock(Path(codoc_dir) / PIDFILE) is not None   # the descriptor stays open: closing it would release the lock


def daemon_running(codoc_dir: str | Path) -> bool:
    """Whether a daemon holds the lock on watch.pid."""
    try:
        fd = os.open(Path(codoc_dir) / PIDFILE, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)                             # and with it the shared lock, if taken
