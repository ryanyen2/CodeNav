"""Tests for the codoc CC hook handler (codoc/agent/hook.py)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from codoc.agent.hook import (
    _find_codoc_dir,
    _rel,
    _resolve_features,
    handle_pre_tool,
    handle_session_start,
    handle_stop,
    main,
)
from codoc.loop.activity import ACTIVITY_FILENAME, activity_path, read_activity


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path):
    """Minimal repo with a .codoc dir and a pre-written sidecar."""
    root = tmp_path / "repo"
    root.mkdir()
    codoc_dir = root / ".codoc"
    codoc_dir.mkdir()

    # Write a sidecar with one file→feature mapping.
    sidecar = {
        "version": 1,
        "by_feature": {"f-abc": [{"file": "src/app.py", "symbol": "app.run"}]},
        "by_file": {"src/app.py": [{"symbol": "app.run", "feature_id": "f-abc", "feature_title": "App runner"}]},
        "features": {"f-abc": {"title": "App runner", "parent_id": None}},
    }
    (codoc_dir / "tree.bindings.json").write_text(json.dumps(sidecar))
    return root, codoc_dir


def _payload(cwd: str, **extra) -> dict:
    return {"session_id": "sess-1", "cwd": cwd, **extra}


@pytest.fixture(autouse=True)
def no_real_spawn(monkeypatch):
    """Capture (and never actually launch) the Stop hook's detached reflect."""
    import subprocess
    calls: list[list[str]] = []

    def fake_popen(cmd, *a, **k):
        calls.append(cmd)
        return None

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


# ── Unit helpers ──────────────────────────────────────────────────────────────

def test_find_codoc_dir_finds_parent(tmp_path):
    (tmp_path / ".codoc").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert _find_codoc_dir(str(sub)) == str(tmp_path / ".codoc")


def test_find_codoc_dir_returns_none_when_absent(tmp_path):
    assert _find_codoc_dir(str(tmp_path)) is None


def test_rel_inside_root(tmp_path):
    f = tmp_path / "src" / "main.py"
    assert _rel(str(f), str(tmp_path)) == "src/main.py"


def test_rel_outside_root_returns_none(tmp_path):
    assert _rel("/etc/passwd", str(tmp_path)) is None


def test_resolve_features_from_sidecar(repo):
    root, codoc_dir = repo
    fids = _resolve_features("src/app.py", str(codoc_dir))
    assert fids == ["f-abc"]


def test_resolve_features_missing_file(repo):
    root, codoc_dir = repo
    assert _resolve_features("nonexistent.py", str(codoc_dir)) == []


def test_resolve_features_corrupt_sidecar(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    (codoc_dir / "tree.bindings.json").write_text("{corrupt")
    assert _resolve_features("src/app.py", str(codoc_dir)) == []


# ── session-start ─────────────────────────────────────────────────────────────

def test_session_start_writes_open_epoch(repo):
    root, codoc_dir = repo
    payload = _payload(str(root))
    handle_session_start(payload, str(codoc_dir))

    data = read_activity(str(codoc_dir))
    assert data["epoch"]["open"] is True
    assert data["epoch"]["origin"] == "interactive"
    assert data["epoch"]["started_at"] is not None
    assert data["touched"] == {}


def test_session_start_records_loop_b_origin(repo, monkeypatch):
    root, codoc_dir = repo
    monkeypatch.setenv("CODOC_EPOCH_ORIGIN", "loop_b")
    handle_session_start(_payload(str(root)), str(codoc_dir))
    data = read_activity(str(codoc_dir))
    assert data["epoch"]["origin"] == "loop_b"


def test_session_start_resets_touched(repo):
    root, codoc_dir = repo
    # Write stale touched data from a previous epoch.
    stale = {"version": 1, "epoch": {"id": "old", "origin": "interactive", "open": False,
                                      "started_at": None, "ended_at": None},
             "touched": {"old_file.py": {}}, "recent": [{"old": True}]}
    (codoc_dir / ACTIVITY_FILENAME).write_text(json.dumps(stale))

    handle_session_start(_payload(str(root)), str(codoc_dir))
    data = read_activity(str(codoc_dir))
    assert data["touched"] == {}
    assert data["recent"] == []


# ── stop ──────────────────────────────────────────────────────────────────────

def test_stop_closes_epoch(repo):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_stop(_payload(str(root)), str(codoc_dir))

    data = read_activity(str(codoc_dir))
    assert data["epoch"]["open"] is False
    assert data["epoch"]["ended_at"] is not None


def test_stop_preserves_touched(repo):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))

    # Simulate a pre-tool having been recorded.
    handle_pre_tool(
        _payload(str(root), tool_name="Edit",
                 tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_stop(_payload(str(root)), str(codoc_dir))

    data = read_activity(str(codoc_dir))
    assert "src/app.py" in data["touched"]  # kept after close


def test_stop_spawns_reflect_when_no_daemon(repo, no_real_spawn):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Edit", tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_stop(_payload(str(root)), str(codoc_dir))

    assert len(no_real_spawn) == 1
    cmd = no_real_spawn[0]
    assert "reflect" in cmd and "--scope" in cmd
    assert "src/app.py" in cmd[cmd.index("--scope") + 1]


def test_stop_skips_reflect_when_daemon_running(repo, no_real_spawn):
    import os
    root, codoc_dir = repo
    (codoc_dir / "watch.pid").write_text(str(os.getpid()))  # a "live" daemon
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Edit", tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_stop(_payload(str(root)), str(codoc_dir))

    assert no_real_spawn == []  # daemon owns the epoch-close reconcile


def test_stop_skips_reflect_for_loop_b_origin(repo, no_real_spawn, monkeypatch):
    root, codoc_dir = repo
    monkeypatch.setenv("CODOC_EPOCH_ORIGIN", "loop_b")
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Edit", tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_stop(_payload(str(root)), str(codoc_dir))

    assert no_real_spawn == []  # Loop B reflects its own epoch


def test_stop_skips_reflect_with_no_writes(repo, no_real_spawn):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(  # a Read, not a write
        _payload(str(root), tool_name="Read", tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    handle_stop(_payload(str(root)), str(codoc_dir))

    assert no_real_spawn == []


# ── pre-tool / post-tool ──────────────────────────────────────────────────────

def test_pre_tool_records_read(repo):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Read",
                 tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )

    data = read_activity(str(codoc_dir))
    assert "src/app.py" in data["touched"]
    entry = data["touched"]["src/app.py"]
    assert "f-abc" in entry["feature_ids"]
    assert entry["mode"] == "read"
    assert len(data["recent"]) == 1
    assert data["recent"][0]["phase"] == "pre"


def test_pre_tool_write_upgrades_mode(repo):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    # First a read…
    handle_pre_tool(
        _payload(str(root), tool_name="Read",
                 tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    # …then a write — mode should become "write".
    handle_pre_tool(
        _payload(str(root), tool_name="Edit",
                 tool_input={"file_path": str(root / "src/app.py")}),
        str(codoc_dir),
    )
    data = read_activity(str(codoc_dir))
    assert data["touched"]["src/app.py"]["mode"] == "write"


def test_pre_tool_outside_repo_is_ignored(repo):
    root, codoc_dir = repo
    handle_session_start(_payload(str(root)), str(codoc_dir))
    handle_pre_tool(
        _payload(str(root), tool_name="Edit",
                 tool_input={"file_path": "/etc/passwd"}),
        str(codoc_dir),
    )
    data = read_activity(str(codoc_dir))
    assert data["touched"] == {}


# ── main dispatch + safety ────────────────────────────────────────────────────

def test_main_no_codoc_dir_exits_zero(tmp_path, monkeypatch):
    """No .codoc → hook exits 0 without writing anything."""
    payload = json.dumps({"session_id": "x", "cwd": str(tmp_path)})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    assert main(["session-start"]) == 0


def test_main_corrupt_stdin_exits_zero(repo):
    root, codoc_dir = repo
    with patch("sys.stdin", __import__("io").StringIO("{bad json")):
        assert main(["session-start"]) == 0


def test_main_unknown_event_exits_zero(repo):
    root, codoc_dir = repo
    payload = json.dumps({"cwd": str(root)})
    with patch("sys.stdin", __import__("io").StringIO(payload)):
        assert main(["unknown-event"]) == 0


def test_atomic_write_no_tmp_left(repo):
    root, codoc_dir = repo
    payload = _payload(str(root))
    handle_session_start(payload, str(codoc_dir))

    tmp = codoc_dir / (ACTIVITY_FILENAME + ".tmp")
    assert not tmp.exists(), "tmp file should have been renamed to final dest"
