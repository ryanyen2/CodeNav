"""Single-owner supervision: the hub owns the repo's daemon.

A hub owns the checkout while it holds an exclusive ``flock`` on
``.codoc/serve.lock``, from ``start`` to ``stop``. The OS drops the lock
however the process ends, so a file left by a crashed hub, or naming a pid
another program has since been given, claims nothing; the file is never
removed, for the reason ``daemon/pidfile.py`` gives for watch.pid. Two racing
hubs never both spawn a daemon. The daemon is spawned only when none is running.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from codoc.daemon.pidfile import daemon_running
from codoc.workspace.layout import SERVE_LOCK, hold_lock


class OwnershipError(RuntimeError):
    """Another live hub already owns the repo."""


def _spawn(root: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "codoc.cli.main", "watch", "--root", root])


class DaemonSupervisor:
    def __init__(self, root: str, codoc_dir: str, *, spawn=None) -> None:
        self.root, self.codoc_dir, self._spawn = root, codoc_dir, spawn or _spawn
        self.lock: int | None = None              # the descriptor holding serve.lock
        self.child: subprocess.Popen | None = None

    def start(self) -> None:
        self.lock = hold_lock(Path(self.codoc_dir) / SERVE_LOCK)
        if self.lock is None:
            raise OwnershipError(f"another codoc hub already owns {self.codoc_dir} ({SERVE_LOCK})")
        if not daemon_running(Path(self.codoc_dir)):
            self.child = self._spawn(self.root)

    def stop(self) -> None:
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
        self.child = None
        if self.lock is not None:
            os.close(self.lock)                    # and with it the lock
            self.lock = None

    def __enter__(self) -> "DaemonSupervisor":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()
