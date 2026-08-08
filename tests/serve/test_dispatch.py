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
    assert allowed("block-edit", Capability.SUGGEST)
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


# U3 / KTD10 — identity-keyed command kinds are capability-gated.
def test_suggest_can_set_title_and_description(tmp_path):
    cd = str(tmp_path)
    # set_title / set_description are description-level → SUGGEST-eligible.
    res = dispatch({"kind": "set_title", "id": "c-1", "featureId": "f-1",
                    "payload": {"title": "Renamed"}}, Capability.SUGGEST, cd)
    assert res["ok"] is True
    dispatch({"kind": "set_description", "id": "c-2", "featureId": "f-1",
              "payload": {"description": "New prose."}}, Capability.SUGGEST, cd)
    cmds = edits.read_commands(cd)
    assert {c.id for c in cmds} == {"c-1", "c-2"}
    assert {c.kind for c in cmds} == {"set_title", "set_description"}


def test_suggest_cannot_structural_command(tmp_path):
    cd = str(tmp_path)
    for kind in ("add", "move", "retire"):
        with pytest.raises(CommandError) as ei:
            dispatch({"kind": kind, "id": f"c-{kind}", "featureId": "f-1"},
                     Capability.SUGGEST, cd)
        assert ei.value.status == 403
    # …and nothing was written to the channel (rejected before the handler).
    assert edits.read_commands(cd) == []


def test_handoff_can_structural_command(tmp_path):
    cd = str(tmp_path)
    res = dispatch({"kind": "add", "id": "c-add", "localId": "L1",
                    "payload": {"title": "New feature"}}, Capability.HANDOFF, cd)
    assert res["ok"] is True
    dispatch({"kind": "retire", "id": "c-ret", "featureId": "f-9"}, Capability.HANDOFF, cd)
    assert {c.kind for c in edits.read_commands(cd)} == {"add", "retire"}


# Blocks (v6) — a remote suggester may propose a block edit; it's held/staged
# exactly like every other hub suggestion (SUGGEST-eligible, content-level).
def test_suggest_can_block_edit(tmp_path):
    cd = str(tmp_path)
    res = dispatch(
        {"kind": "block-edit",
         "block": {"block_id": "blk-1", "feature_id": "f-1", "kind": "diagram",
                    "action": "edit", "content": "flowchart TB\n  a --> b",
                    "prev_content": "flowchart TB\n  a"}},
        Capability.SUGGEST, cd)
    assert res["ok"] is True
    pending = edits.read_block_edits(cd)
    assert len(pending) == 1
    assert pending[0].block_id == "blk-1" and pending[0].kind == "diagram"
    assert pending[0].content == "flowchart TB\n  a --> b"


def test_block_edit_requires_identity(tmp_path):
    with pytest.raises(CommandError):
        dispatch({"kind": "block-edit", "block": {"feature_id": "f-1", "kind": "url"}},
                 Capability.SUGGEST, str(tmp_path))
    assert edits.read_block_edits(str(tmp_path)) == []


def test_suggest_block_edit_writes_a_file_attachment(tmp_path):
    import base64

    cd = str(tmp_path)
    data = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    res = dispatch(
        {"kind": "block-edit",
         "block": {"block_id": "blk-2", "feature_id": "f-1", "kind": "image",
                    "action": "add", "mediaData": data, "mediaMime": "image/png"}},
        Capability.SUGGEST, cd)
    assert res["ok"] is True
    pending = edits.read_block_edits(cd)
    assert pending[0].content == ".codoc/media/blk-2.png"
    assert (tmp_path / "media" / "blk-2.png").read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_none_cannot_block_edit(tmp_path):
    with pytest.raises(CommandError) as ei:
        dispatch({"kind": "block-edit",
                  "block": {"block_id": "blk-1", "feature_id": "f-1", "kind": "url"}},
                 Capability.NONE, str(tmp_path))
    assert ei.value.status == 403


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


# The merge-claim fields survive the hub ingestion path (the same drop that
# disabled merge3 on the IDE's host-op path existed here too).
def test_command_preserves_base_text_and_session(tmp_path):
    cd = str(tmp_path)
    res = dispatch(
        {"kind": "set_description", "id": "c-hub-1", "featureId": "f-1",
         "baseText": "before", "session": "hub-3",
         "payload": {"description": "after"}},
        Capability.SUGGEST, cd)
    assert res.get("ok")
    (cmd,) = edits.read_commands(cd)
    assert cmd.base_text == "before"
    assert cmd.session == "hub-3"
