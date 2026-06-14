"""Parent-death self-exit + watch.pid owner metadata (U6).

The VS Code extension owns the daemon lifecycle: it spawns ``codoc watch`` with
``CODOC_WATCH_PARENT_PID`` (its own pid) and ``CODOC_WATCH_OWNER`` (a window id).
These tests pin the two pure pieces the extension relies on without spinning up
the real ``watchfiles`` loop:

  • :func:`parent_alive` — the keep-running decision: False when the spawning
    parent is gone (orphan defense), True when it's alive, True when unset (the
    plain CLI path must be unchanged).
  • :func:`write_pidfile` / :func:`read_pid` — the pid file now carries owner
    metadata as JSON yet stays backward-compatible with a legacy bare-int file.
"""
from __future__ import annotations

import json
import os

import pytest

from codoc.loop import watch


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    return str(d)


def _dead_pid() -> int:
    """A pid that is (almost certainly) not a live process.

    Spawn-and-reap a trivial child, then use its now-dead pid — guaranteed unused
    for the moment after reaping."""
    import subprocess

    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


# ── parent_alive: the keep-running decision ──────────────────────────────────


def test_parent_alive_false_when_parent_dead(monkeypatch):
    monkeypatch.setenv("CODOC_WATCH_PARENT_PID", str(_dead_pid()))
    assert watch.parent_alive() is False  # orphaned → self-exit


def test_parent_alive_true_when_parent_live(monkeypatch):
    monkeypatch.setenv("CODOC_WATCH_PARENT_PID", str(os.getpid()))
    assert watch.parent_alive() is True


def test_parent_alive_true_when_unset(monkeypatch):
    monkeypatch.delenv("CODOC_WATCH_PARENT_PID", raising=False)
    assert watch.parent_alive() is True  # plain CLI path unchanged


def test_parent_alive_true_on_malformed_env(monkeypatch):
    monkeypatch.setenv("CODOC_WATCH_PARENT_PID", "not-a-pid")
    assert watch.parent_alive() is True  # never self-exit on a bad value


# ── watch.pid owner metadata ─────────────────────────────────────────────────


def test_write_pidfile_includes_owner_metadata(codoc_dir, monkeypatch):
    monkeypatch.setenv("CODOC_WATCH_OWNER", "window-abc123")
    watch.write_pidfile(codoc_dir)

    raw = (watch._pidfile(codoc_dir)).read_text()
    payload = json.loads(raw)
    assert payload["pid"] == os.getpid()
    assert payload["owner"] == "window-abc123"
    assert isinstance(payload["started_at"], (int, float))


def test_write_pidfile_owner_empty_without_env(codoc_dir, monkeypatch):
    monkeypatch.delenv("CODOC_WATCH_OWNER", raising=False)
    watch.write_pidfile(codoc_dir)
    payload = json.loads((watch._pidfile(codoc_dir)).read_text())
    assert payload["owner"] == ""  # plain CLI invocation: no owner


def test_read_pid_parses_json_metadata(codoc_dir):
    watch.write_pidfile(codoc_dir)
    assert watch.read_pid(codoc_dir) == os.getpid()


def test_read_pid_parses_legacy_bare_int(codoc_dir):
    # The Stop-hook test and any pre-U6 daemon write a bare pid — still parse it.
    watch._pidfile(codoc_dir).write_text(str(os.getpid()))
    assert watch.read_pid(codoc_dir) == os.getpid()


def test_read_pid_none_when_missing(codoc_dir):
    assert watch.read_pid(codoc_dir) is None


def test_read_pid_none_when_garbage(codoc_dir):
    watch._pidfile(codoc_dir).write_text("{ not json and not int")
    assert watch.read_pid(codoc_dir) is None


# ── daemon_running still works across both pidfile formats ────────────────────


def test_daemon_running_true_for_live_json_pid(codoc_dir):
    watch.write_pidfile(codoc_dir)  # our own (live) pid
    assert watch.daemon_running(codoc_dir) is True


def test_daemon_running_true_for_live_legacy_pid(codoc_dir):
    watch._pidfile(codoc_dir).write_text(str(os.getpid()))
    assert watch.daemon_running(codoc_dir) is True


def test_daemon_running_false_for_dead_pid(codoc_dir):
    watch._pidfile(codoc_dir).write_text(json.dumps({"pid": _dead_pid(), "owner": "", "started_at": 0}))
    assert watch.daemon_running(codoc_dir) is False
