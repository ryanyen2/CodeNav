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


def test_status_awaiting_impl_when_realize_md_present(codoc_dir):
    """A queued realize.md is an active obligation: refresh_status must report
    awaiting_impl (not in_sync) even with zero pending proposals, so a later
    code-side pass cannot orphan the directive."""
    from pathlib import Path
    Path(codoc_dir, "realize.md").write_text(
        "preamble\n\n### 1. RETIRE FEATURE: \"x\"\n\n### 2. NEW FEATURE: \"y\"\n")
    store = open_store(codoc_dir)
    try:
        status.refresh_status(codoc_dir, store)  # no awaiting_impl flag passed
    finally:
        store.close()
    payload = json.loads(status.status_path(codoc_dir).read_text())
    assert payload["state"] == status.AWAITING_IMPL
    assert payload["pending"] == 2  # one per ### directive heading


def test_status_ignores_empty_realize_md(codoc_dir):
    from pathlib import Path
    Path(codoc_dir, "realize.md").write_text("   \n")
    store = open_store(codoc_dir)
    try:
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    assert _state(codoc_dir) == status.IN_SYNC


def test_code_side_pass_does_not_orphan_queued_realize_md(codoc_dir):
    """The regression the fix targets: a code-side reflection (which calls
    refresh_status with no awaiting_impl flag and zero pending proposals) must NOT
    clobber a queued realize.md back to in_sync — it stays awaiting_impl."""
    from pathlib import Path
    Path(codoc_dir, "realize.md").write_text("### 1. RETIRE FEATURE: \"x\"\n")
    store = open_store(codoc_dir)
    try:
        # simulate the tail of a Loop A / reconcile pass: no proposals, no flags
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    assert _state(codoc_dir) == status.AWAITING_IMPL


def test_realize_md_outranks_code_drift(codoc_dir):
    """A queued realize.md outranks pending proposals: status reports awaiting_impl
    (not code_drift), so the IDE keeps prompting /codoc:sync even when new
    proposals coexist. Proposals still render inline in the tree regardless."""
    from pathlib import Path
    Path(codoc_dir, "realize.md").write_text("### 1. NEW FEATURE: \"y\"\n")
    store = open_store(codoc_dir)
    try:
        store.append_event(Event(source="loop_a", applied=False,
                                 op=NodeOp(kind=NodeOpKind.ADD_NODE, title="x", description="y")))
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    assert _state(codoc_dir) == status.AWAITING_IMPL
