"""The pipeline state must see authored edits nobody has drained.

The failure this file exists for reached a pilot: the daemon was not running
(the study workspaces start it by hand), their description edit sat in
``edits.host.jsonl``, and every recompute of the state said ``in_sync`` — so the
editor raised no hand-off affordance and ``/codoc:sync``'s dispatch found
nothing to do. An edit that exists only in the channel files is exactly as real
as a pending proposal, and a state that cannot see it reports a lie.
"""
from __future__ import annotations

import json

import pytest

from codoc.loop import edits, status
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    return str(d)


def _read_state(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _host_command(codoc_dir, cid="c-1"):
    edits.append_host_op(codoc_dir, "appendCommand", {
        "id": cid, "kind": "set_description", "feature_id": "f-1",
        "base_text": "old", "session": "s",
        "payload": {"description": "old, and remove this feature."},
    })


def test_quiet_workspace_is_in_sync(codoc_dir):
    with open_store(codoc_dir) as store:
        st = status.refresh_status(codoc_dir, store)
    assert _read_state(st)["state"] == "in_sync"


def test_a_command_in_edits_json_is_tree_dirty(codoc_dir):
    edits.append_command(codoc_dir, edits.Command(
        id="c-1", kind="set_description", feature_id="f-1",
        payload={"description": "x"},
    ))
    with open_store(codoc_dir) as store:
        st = status.refresh_status(codoc_dir, store)
    data = _read_state(st)
    assert data["state"] == "tree_dirty"
    # And the detail says what to run, because the person reading it is a
    # participant whose daemon is not running, not us.
    assert "codoc watch" in data["detail"] or "codoc sync" in data["detail"]


def test_an_unmerged_host_op_is_tree_dirty(codoc_dir):
    # The exact pilot state: the IDE appended, nobody merged.
    _host_command(codoc_dir)
    with open_store(codoc_dir) as store:
        st = status.refresh_status(codoc_dir, store)
    assert _read_state(st)["state"] == "tree_dirty"


def test_mcp_status_merges_the_host_log_first(codoc_dir):
    # /codoc:sync's first call is codoc_status. When the daemon is dead this is
    # the only consumer left, so it folds the append log in before computing —
    # otherwise the state it dispatches on cannot see the author's edit.
    from codoc.mcp import tools

    _host_command(codoc_dir)
    out = tools.read_status(codoc_dir)
    assert out["state"] == "tree_dirty"
    # The log was consumed into edits.json (idempotently), not left behind.
    assert not edits.host_ops_path(codoc_dir).exists()
    assert [c.id for c in edits.read_commands(codoc_dir)] == ["c-1"]


def test_mcp_status_reports_held_drafts(codoc_dir):
    # After `codoc sync` drains, the directive is born HELD and the state returns
    # to in_sync — which is true, and also exactly when an author who just typed
    # an edit asks why nothing happened. The sync dispatch needs to see the
    # draft to tell them about Commit & send instead of "nothing to do".
    from codoc.loop.edits import Directive, write_manifest
    from codoc.mcp import tools

    write_manifest(codoc_dir, [Directive(
        id="d-1", feature_id="f-1", kind="amend", caused_by="c-1",
        text="UPDATE FEATURE", baseline="old", handed_off=False, ts=1,
    )])
    out = tools.read_status(codoc_dir)
    assert out["held_drafts"] == 1
    assert out["held_draft_list"][0]["feature_id"] == "f-1"
