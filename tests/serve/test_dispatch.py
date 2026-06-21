"""U5 — capability-gated command dispatch to the file channels.

Five flows: a read collaborator may suggest/comment, may NOT hand off or write
verdicts, may NOT auto-send (commit is held), a write collaborator may, and a
malformed/unknown command is rejected. The capability matrix is the security
heart: outsiders can only suggest."""
from __future__ import annotations

import pytest

from codoc.loop import edits, inbox
from codoc.serve.auth import Capability
from codoc.serve.dispatch import CommandError, allowed, dispatch


def test_capability_matrix():
    assert allowed("comment-create", Capability.SUGGEST)
    assert allowed("commit", Capability.SUGGEST)
    assert not allowed("hand-off", Capability.SUGGEST)
    assert not allowed("verdict", Capability.SUGGEST)
    assert allowed("hand-off", Capability.HANDOFF)
    assert allowed("verdict", Capability.HANDOFF)
    assert allowed("comment-create", Capability.HANDOFF)
    assert not allowed("comment-create", Capability.NONE)
    assert not allowed("hand-off", Capability.NONE)


# Flow 1 — a read collaborator comments → steer written.
def test_suggest_can_comment(tmp_path):
    cd = str(tmp_path)
    res = dispatch(
        {"kind": "comment-create",
         "thread": {"featureId": "f-1", "body": "this is getting tangled", "id": "c-1"}},
        Capability.SUGGEST, cd)
    assert res["ok"] is True
    steers = edits.read_steers(cd)
    assert len(steers) == 1 and steers[0].feature_id == "f-1"


# Flow 2 — a read collaborator may NOT hand off.
def test_suggest_cannot_hand_off(tmp_path):
    with pytest.raises(CommandError) as ei:
        dispatch({"kind": "hand-off"}, Capability.SUGGEST, str(tmp_path))
    assert ei.value.status == 403


# Flow 3 — a read collaborator may NOT write verdicts.
def test_suggest_cannot_verdict(tmp_path):
    with pytest.raises(CommandError) as ei:
        dispatch({"kind": "verdict", "eventIds": ["e-1"], "accept": True},
                 Capability.SUGGEST, str(tmp_path))
    assert ei.value.status == 403
    assert inbox.read_verdicts(str(tmp_path)) == []


# Flow 3b — a read collaborator's commit is HELD, not auto-sent.
def test_suggest_commit_is_held_not_handed_off(tmp_path):
    cd = str(tmp_path)
    # pre-seed a draft so we can prove hand-off didn't clear it
    edits.set_drafts(cd, ["f-1"])
    res = dispatch({"kind": "commit", "doc": {"type": "doc", "content": []}},
                   Capability.SUGGEST, cd)
    assert res.get("held") is True
    assert edits.read_drafts(cd) == {"f-1"}  # NOT cleared → not handed off
    assert (tmp_path / "tree.doc.json").exists()  # doc persisted


# Flow 4 — a write collaborator hands off → drafts cleared (the realize trigger acts in U7).
def test_handoff_clears_drafts(tmp_path):
    cd = str(tmp_path)
    edits.set_drafts(cd, ["f-1", "f-2"])
    res = dispatch({"kind": "hand-off"}, Capability.HANDOFF, cd)
    assert res["ok"] is True
    assert edits.read_drafts(cd) == set()


def test_handoff_writes_verdict(tmp_path):
    cd = str(tmp_path)
    res = dispatch({"kind": "verdict", "eventIds": ["e-1", "e-2"], "accept": True},
                   Capability.HANDOFF, cd)
    assert res["verdicts"] == 2
    assert {v.event_id for v in inbox.read_verdicts(cd)} == {"e-1", "e-2"}


def test_withdraw_appends_cancellation(tmp_path):
    cd = str(tmp_path)
    dispatch({"kind": "withdraw-realization", "featureId": "f-9"}, Capability.SUGGEST, cd)
    assert "f-9" in edits.read_cancellations(cd)


# Flow 5 — malformed / unknown commands rejected.
def test_unknown_and_malformed_rejected(tmp_path):
    cd = str(tmp_path)
    with pytest.raises(CommandError):
        dispatch({}, Capability.HANDOFF, cd)  # missing kind
    with pytest.raises(CommandError):
        dispatch({"kind": "open-binding", "file": "a.py", "symbol": "x"},
                 Capability.HANDOFF, cd)  # not a remote-surface command
    with pytest.raises(CommandError):
        dispatch({"kind": "withdraw-realization"}, Capability.SUGGEST, cd)  # missing featureId
