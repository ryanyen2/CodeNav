"""The verdict inbox (.codoc/inbox.json) and pipeline status (.codoc/status.json)."""
from __future__ import annotations

import json

import pytest

from codoc.loop import inbox, status
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    return str(d)


# -- inbox ----------------------------------------------------------------
def test_inbox_empty_when_missing(codoc_dir):
    assert inbox.read_verdicts(codoc_dir) == []


def test_inbox_append_and_read(codoc_dir):
    inbox.append_verdict(codoc_dir, "e-1", accept=True)
    inbox.append_verdict(codoc_dir, "e-2", accept=False)
    verdicts = inbox.read_verdicts(codoc_dir)
    assert [(v.event_id, v.accept) for v in verdicts] == [("e-1", True), ("e-2", False)]


def test_inbox_clear(codoc_dir):
    inbox.append_verdict(codoc_dir, "e-1", accept=True)
    inbox.clear(codoc_dir)
    assert inbox.read_verdicts(codoc_dir) == []
    inbox.clear(codoc_dir)  # idempotent


def test_inbox_tolerates_garbage(codoc_dir):
    inbox.inbox_path(codoc_dir).write_text("{ not json")
    assert inbox.read_verdicts(codoc_dir) == []


# -- status ---------------------------------------------------------------
def _state(codoc_dir):
    return json.loads(status.status_path(codoc_dir).read_text())["state"]


def test_status_in_sync_when_no_pending(codoc_dir):
    store = open_store(codoc_dir)
    try:
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    assert _state(codoc_dir) == status.IN_SYNC


def test_status_code_drift_with_pending(codoc_dir):
    store = open_store(codoc_dir)
    try:
        store.append_event(Event(source="loop_a", applied=False,
                                 op=NodeOp(kind=NodeOpKind.ADD_NODE, title="x", description="y")))
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    payload = json.loads(status.status_path(codoc_dir).read_text())
    assert payload["state"] == status.CODE_DRIFT and payload["pending"] == 1


def test_status_realizing_override(codoc_dir):
    store = open_store(codoc_dir)
    try:
        status.refresh_status(codoc_dir, store, realizing=True, detail="implementing")
    finally:
        store.close()
    payload = json.loads(status.status_path(codoc_dir).read_text())
    assert payload["state"] == status.REALIZING and payload["detail"] == "implementing"
