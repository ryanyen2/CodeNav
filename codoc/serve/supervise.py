"""Single-owner supervision: the hub owns the repo's daemon.

An atomic ``.codoc/serve.lock`` (``O_EXCL``) means two racing hubs never both
spawn a daemon; a lock naming a dead process is reclaimed. The daemon is spawned
only when none is running (the VS Code extension defers when it sees the lock).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from codoc.daemon.pidfile import daemon_running, pid_alive
from codoc.workspace.layout import SERVE_LOCK


class OwnershipError(RuntimeError):
    """Another live hub already owns the repo."""


def _lock(codoc_dir: str) -> Path:
    return Path(codoc_dir) / SERVE_LOCK


def _owner(codoc_dir: str) -> dict | None:
    try:
        return json.loads(_lock(codoc_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def acquire_owner(codoc_dir: str) -> bool:
    Path(codoc_dir).mkdir(parents=True, exist_ok=True)
    path = _lock(codoc_dir)
    payload = json.dumps({"pid": os.getpid(), "started_at": time.time()})
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pid = (_owner(codoc_dir) or {}).get("pid")
            if isinstance(pid, int) and pid != os.getpid() and pid_alive(pid):
                return False
            try:
                path.unlink()
            except OSError:
                return False
            continue
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        return True
    return False


def release_owner(codoc_dir: str) -> None:
    info = _owner(codoc_dir)
    if info is not None and info.get("pid") not in (None, os.getpid()):
        return
    try:
        _lock(codoc_dir).unlink()
    except OSError:
        pass


def _spawn(root: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "codoc.cli.main", "watch", "--root", root])


class DaemonSupervisor:
    def __init__(self, root: str, codoc_dir: str, *, spawn=None) -> None:
        self.root, self.codoc_dir, self._spawn = root, codoc_dir, spawn or _spawn
        self.owned = False
        self.child: subprocess.Popen | None = None

    def start(self) -> None:
        if not acquire_owner(self.codoc_dir):
            raise OwnershipError(f"another codoc hub already owns {self.codoc_dir} ({SERVE_LOCK})")
        self.owned = True
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
        if self.owned:
            release_owner(self.codoc_dir)
            self.owned = False

    def __enter__(self) -> "DaemonSupervisor":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()
