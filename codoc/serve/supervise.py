"""Single-owner supervision for the codoc serve hub.

The serve process is the canonical owner of the ``codoc watch`` daemon on a repo
— the peer of the VS Code extension's daemon manager. Two pieces:

- An **atomic** owner claim (``.codoc/serve.lock`` via ``O_EXCL``) so two racing
  serve processes can never both spawn a daemon: exactly one wins, the loser
  defers. A *stale* lock (its pid is dead) is reclaimed; a *live* foreign owner
  is respected. This is the hardened single-owner acquisition plan unit U1 calls
  for, sitting alongside the daemon's own ``watch.pid`` last-writer convention.
- Daemon supervision: spawn ``codoc watch`` as a child with ``CODOC_WATCH_OWNER``
  + ``CODOC_WATCH_PARENT_PID`` so it self-exits if the hub dies (see
  ``codoc.loop.watch.parent_alive``); skip spawning when a live daemon already
  owns the repo (``daemon_running``).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from codoc.loop.watch import daemon_running

OWNER_LOCK = "serve.lock"


class OwnershipError(RuntimeError):
    """Raised when another live hub already owns the repo."""


def _lock_path(codoc_dir: str) -> Path:
    return Path(codoc_dir) / OWNER_LOCK


def _pid_alive(pid: int) -> bool:
    """True if ``pid`` is a live process (signal-0 liveness probe).

    Mirrors ``codoc.loop.watch._pid_alive``: a bare ``OSError`` (no such process)
    is treated as dead. This is the same simplification the daemon uses for its
    own pidfile, so the two ownership checks stay consistent."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_owner(codoc_dir: str) -> dict | None:
    try:
        return json.loads(_lock_path(codoc_dir).read_text())
    except (OSError, ValueError):
        return None


def acquire_owner(codoc_dir: str) -> bool:
    """Atomically claim hub ownership of this repo. Returns True iff acquired.

    Uses ``O_EXCL`` so exactly one of N racing serve processes wins. An existing
    lock that names a dead process (or this process) is reclaimed and the claim
    retried once; a lock naming a live *foreign* process means we defer."""
    Path(codoc_dir).mkdir(parents=True, exist_ok=True)
    path = _lock_path(codoc_dir)
    payload = json.dumps({"pid": os.getpid(), "started_at": time.time()})
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            info = _read_owner(codoc_dir)
            pid = (info or {}).get("pid")
            if isinstance(pid, int) and pid != os.getpid() and _pid_alive(pid):
                return False  # a live hub already owns this repo
            try:
                path.unlink()  # stale (dead owner) or our own → reclaim
            except OSError:
                return False
            continue
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        return True
    return False


def release_owner(codoc_dir: str) -> None:
    """Remove the owner lock — but only when it still names this process."""
    info = _read_owner(codoc_dir)
    if info is not None and info.get("pid") not in (None, os.getpid()):
        return  # a foreign owner holds it — not ours to remove
    try:
        _lock_path(codoc_dir).unlink()
    except OSError:
        pass


def _default_spawn(root: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["CODOC_WATCH_OWNER"] = "serve"
    env["CODOC_WATCH_PARENT_PID"] = str(os.getpid())
    return subprocess.Popen(
        [sys.executable, "-m", "codoc.cli.main", "watch", "--root", root],
        env=env,
    )


class DaemonSupervisor:
    """Own the repo and keep a ``codoc watch`` daemon alive under the hub.

    Acquiring ownership is atomic; the daemon is spawned only when no live one
    already owns the repo. ``spawn`` is injectable for tests."""

    def __init__(self, root: str, codoc_dir: str, *, spawn=None):
        self.root = root
        self.codoc_dir = codoc_dir
        self._spawn = spawn or _default_spawn
        self.owned = False
        self.child: subprocess.Popen | None = None

    def start(self) -> None:
        if not acquire_owner(self.codoc_dir):
            raise OwnershipError(
                f"another codoc hub already owns {self.codoc_dir} ({OWNER_LOCK})"
            )
        self.owned = True
        if not daemon_running(self.codoc_dir):
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
