"""Tests for POST /claude-code/event and GET /claude-code/activity."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from codoc.api.app import create_app
from codoc.listener import ledger as _ledger_mod
from codoc.listener.ledger import LiveActivity
from codoc.listener.event_bus import EventBus, bus as _global_bus


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset module-level singletons between tests to avoid cross-test pollution."""
    # Reset ledger singleton.
    import codoc.listener.ledger as lm
    lm.ledger = LiveActivity()

    # Reset event bus subscribers.
    import codoc.listener.event_bus as em
    em.bus = EventBus()

    yield

    # Cleanup after test.
    lm.ledger = LiveActivity()
    em.bus = EventBus()


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Canned hook payloads
# ---------------------------------------------------------------------------

def _edit_payload(cwd="/tmp/project", file_path="/tmp/project/src/auth.py"):
    return {
        "session_id": "sess-abc",
        "transcript_path": "",
        "cwd": cwd,
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": file_path, "old_string": "x", "new_string": "y"},
        "tool_response": {"type": "text", "text": "ok"},
        "tool_use_id": "tu-1",
    }


def _write_payload(cwd="/tmp/project", file_path="/tmp/project/src/utils.py"):
    return {
        "session_id": "sess-abc",
        "transcript_path": "",
        "cwd": cwd,
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": file_path, "content": "# new file"},
        "tool_response": {"type": "text", "text": "ok"},
        "tool_use_id": "tu-2",
    }


def _multi_edit_payload(cwd="/tmp/project"):
    return {
        "session_id": "sess-abc",
        "transcript_path": "",
        "cwd": cwd,
        "hook_event_name": "PostToolUse",
        "tool_name": "MultiEdit",
        "tool_input": {
            "edits": [
                {"file_path": "/tmp/project/src/a.py", "old_string": "a", "new_string": "b"},
                {"file_path": "/tmp/project/src/b.py", "old_string": "c", "new_string": "d"},
            ]
        },
        "tool_response": {"type": "text", "text": "ok"},
        "tool_use_id": "tu-3",
    }


def _read_payload(cwd="/tmp/project", file_path="/tmp/project/src/auth.py"):
    return {
        "session_id": "sess-abc",
        "transcript_path": "",
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
        "tool_use_id": "tu-4",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_edit_event_returns_empty_dict(client):
    resp = client.post("/claude-code/event", json=_edit_payload())
    assert resp.status_code == 200
    assert resp.json() == {}


def test_write_event_returns_empty_dict(client):
    resp = client.post("/claude-code/event", json=_write_payload())
    assert resp.status_code == 200
    assert resp.json() == {}


def test_read_event_returns_empty_dict(client):
    resp = client.post("/claude-code/event", json=_read_payload())
    assert resp.status_code == 200
    assert resp.json() == {}


def test_multi_edit_returns_empty_dict(client):
    resp = client.post("/claude-code/event", json=_multi_edit_payload())
    assert resp.status_code == 200
    assert resp.json() == {}


def test_edit_event_updates_ledger(client):
    import codoc.listener.ledger as lm
    client.post("/claude-code/event", json=_edit_payload())
    active = lm.ledger.get_active()
    assert len(active) >= 1
    rel_paths = {e.rel_path for e in active}
    assert "src/auth.py" in rel_paths


def test_multi_edit_updates_ledger_for_each_file(client):
    import codoc.listener.ledger as lm
    client.post("/claude-code/event", json=_multi_edit_payload())
    active = lm.ledger.get_active()
    rel_paths = {e.rel_path for e in active}
    assert "src/a.py" in rel_paths
    assert "src/b.py" in rel_paths


def test_event_without_file_path_is_graceful(client):
    """Events with no file_path (e.g., Bash) should return {} without error."""
    payload = {
        "session_id": "s",
        "cwd": "/tmp/project",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_use_id": "tu-bash",
    }
    resp = client.post("/claude-code/event", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {}


def test_activity_endpoint_reflects_ledger(client):
    client.post("/claude-code/event", json=_edit_payload())
    resp = client.get("/claude-code/activity?root_dir=/tmp/project")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    rel_paths = {e["rel_path"] for e in data}
    assert "src/auth.py" in rel_paths


def test_activity_endpoint_empty_when_no_events(client):
    resp = client.get("/claude-code/activity?root_dir=/tmp/project")
    assert resp.status_code == 200
    assert resp.json() == []
