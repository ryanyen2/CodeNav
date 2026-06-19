"""U1 — atomic single-owner acquisition + daemon supervision.

These tests are pure stdlib (no web deps): they verify the ownership lock that
prevents two hubs from both spawning a daemon, and the supervisor's spawn/skip
decision against a (faked) daemon-running check."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from codoc.serve import supervise


def _live_child() -> subprocess.Popen:
    """A real, signalable, guaranteed-live foreign process to stand in for a
    rival hub. Using a real pid (not pid 1) keeps the liveness probe honest."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


def _write_lock(cd: str, pid: int) -> None:
    (Path(cd) / supervise.OWNER_LOCK).write_text(json.dumps({"pid": pid, "started_at": time.time()}))


def test_acquire_owner_succeeds_on_clean_dir(tmp_path):
    cd = str(tmp_path)
    assert supervise.acquire_owner(cd) is True
    info = json.loads((Path(cd) / supervise.OWNER_LOCK).read_text())
    assert info["pid"] == os.getpid()


def test_acquire_owner_defers_to_live_foreign_owner(tmp_path):
    cd = str(tmp_path)
    child = _live_child()
    try:
        _write_lock(cd, child.pid)
        assert supervise.acquire_owner(cd) is False
        # the foreign lock is left intact
        assert json.loads((Path(cd) / supervise.OWNER_LOCK).read_text())["pid"] == child.pid
    finally:
        child.terminate()
        child.wait()


def test_acquire_owner_reclaims_stale_lock(tmp_path):
    cd = str(tmp_path)
    child = _live_child()
    dead_pid = child.pid
    child.terminate()
    child.wait()  # dead_pid is now gone
    _write_lock(cd, dead_pid)
    assert supervise.acquire_owner(cd) is True
    assert json.loads((Path(cd) / supervise.OWNER_LOCK).read_text())["pid"] == os.getpid()


def test_release_owner_removes_own_lock(tmp_path):
    cd = str(tmp_path)
    assert supervise.acquire_owner(cd) is True
    supervise.release_owner(cd)
    assert not (Path(cd) / supervise.OWNER_LOCK).exists()


def test_release_owner_leaves_foreign_lock(tmp_path):
    cd = str(tmp_path)
    child = _live_child()
    try:
        _write_lock(cd, child.pid)
        supervise.release_owner(cd)
        assert (Path(cd) / supervise.OWNER_LOCK).exists()  # not ours → untouched
    finally:
        child.terminate()
        child.wait()


class _FakeChild:
    def __init__(self):
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._alive = False


def test_supervisor_spawns_when_no_daemon(tmp_path, monkeypatch):
    root = str(tmp_path)
    cd = str(tmp_path / ".codoc")
    monkeypatch.setattr(supervise, "daemon_running", lambda _cd: False)
    rec: dict = {}

    def fake_spawn(r):
        rec["root"] = r
        return _FakeChild()

    sup = supervise.DaemonSupervisor(root, cd, spawn=fake_spawn)
    sup.start()
    assert rec["root"] == root
    assert sup.child is not None
    sup.stop()
    assert sup.child is None
    assert not (Path(cd) / supervise.OWNER_LOCK).exists()


def test_supervisor_skips_spawn_when_daemon_running(tmp_path, monkeypatch):
    root = str(tmp_path)
    cd = str(tmp_path / ".codoc")
    monkeypatch.setattr(supervise, "daemon_running", lambda _cd: True)

    def fake_spawn(_r):
        raise AssertionError("must not spawn when a daemon already runs")

    sup = supervise.DaemonSupervisor(root, cd, spawn=fake_spawn)
    sup.start()
    assert sup.child is None
    sup.stop()


def test_supervisor_raises_when_repo_owned(tmp_path, monkeypatch):
    root = str(tmp_path)
    cd = str(tmp_path / ".codoc")
    Path(cd).mkdir(parents=True, exist_ok=True)
    child = _live_child()
    try:
        _write_lock(cd, child.pid)
        monkeypatch.setattr(supervise, "daemon_running", lambda _cd: False)
        sup = supervise.DaemonSupervisor(root, cd, spawn=lambda _r: _FakeChild())
        try:
            sup.start()
            raised = False
        except supervise.OwnershipError:
            raised = True
        assert raised is True
    finally:
        child.terminate()
        child.wait()
