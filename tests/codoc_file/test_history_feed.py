"""W2 — the sidecar `feature_history` slice (blame): per-feature edit history
(who/when/why), bounded, newest-first, live-features only."""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.render import (
    BINDINGS_FILENAME,
    _HISTORY_PER_FEATURE,
    write_sidecar,
)
from codoc.loop.apply import apply_op
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _history(tmp_path):
    return json.loads((tmp_path / BINDINGS_FILENAME).read_text())["feature_history"]


def test_history_carries_who_when_why_newest_first(store, tmp_path):
    ev = apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Auth", description="v0",
                         rationale="created for login"),
                  store, source="user", applied=True, actor="human", mode="pen")
    fid = ev.op.feature_id
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description="v1",
                    rationale="clarified sessions"),
             store, source="loop_a_agent", applied=True,
             actor="claude-code", mode="auto", caused_by="d-9")

    write_sidecar(store, tmp_path)
    hist = _history(tmp_path)[fid]

    assert [h["kind"] for h in hist] == ["amend", "add_node"]  # newest first
    assert hist[0]["actor"] == "claude-code"
    assert hist[0]["rationale"] == "clarified sessions"
    assert hist[0]["caused_by"] == "d-9"
    assert hist[1]["actor"] == "human"


def test_history_bounded_per_feature(store, tmp_path):
    ev = apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Busy"),
                  store, source="user", applied=True)
    fid = ev.op.feature_id
    for i in range(_HISTORY_PER_FEATURE + 5):
        apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description=f"v{i}"),
                 store, source="user", applied=True)

    write_sidecar(store, tmp_path)
    assert len(_history(tmp_path)[fid]) == _HISTORY_PER_FEATURE


def test_retired_feature_absent_from_history(store, tmp_path):
    ev = apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Gone"),
                  store, source="user", applied=True)
    fid = ev.op.feature_id
    apply_op(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=fid),
             store, source="user", applied=True)

    write_sidecar(store, tmp_path)
    assert fid not in _history(tmp_path)  # retired → not a live feature


def test_empty_when_no_events(store, tmp_path):
    store.upsert_feature(Feature(title="Fresh"))
    write_sidecar(store, tmp_path)
    assert _history(tmp_path) == {}  # feature exists but no events touched it


def test_history_carries_the_warrant_a_description_rests_on(store, tmp_path):
    """`rationale` says why the change happened; `warrant` says why its stated reason
    should be believed. A reader asks the second question at the paragraph at least as
    often as at a moment on the timeline, so it rides here too."""
    from codoc.model.event import Warrant

    ev = apply_op(
        NodeOp(kind=NodeOpKind.ADD_NODE, title="Retry policy",
               description="Retries only on a timeout, because the server can "
                           "duplicate a non-idempotent post.",
               rationale="the client retries now",
               warrant=[Warrant(kind="commit", ref="1a2b3c4d",
                                quote="Retry only on timeout — the server can "
                                      "duplicate a non-idempotent post.")]),
        store, source="loop_a_agent", applied=True, actor="claude-code", mode="auto")

    write_sidecar(store, tmp_path)
    (row,) = _history(tmp_path)[ev.op.feature_id][0]["warrant"]
    assert row["kind"] == "commit"
    assert row["ref"] == "1a2b3c4d"
    assert "duplicate a non-idempotent post" in row["quote"]


def test_a_change_with_no_warrant_omits_the_key(store, tmp_path):
    """Absence is the answer and the common case: a description reporting what code
    achieves makes no claim needing evidence, so an empty list here would read as a
    missing warrant rather than an unneeded one."""
    ev = apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Totals",
                         description="Computes the total on write.",
                         rationale="new node"),
                  store, source="user", applied=True, actor="human", mode="pen")
    write_sidecar(store, tmp_path)
    assert "warrant" not in _history(tmp_path)[ev.op.feature_id][0]
