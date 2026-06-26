"""U6 — proposal callouts + comment-command verdicts."""
from __future__ import annotations

import pytest

from codoc.loop import inbox
from codoc.loop.loop_b import run_loop_b
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.notion.dispatch import (
    handle_comment_verdict, parse_verdict_command, submit_verdict,
)
from codoc.notion.render import (
    proposal_callout_block, proposal_callouts, recover_event_id,
)
from codoc.store.db import open_store


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    cd = tmp_path / ".codoc"; cd.mkdir()
    return str(root), str(cd)


# ── command parsing ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("/accept", True),
    ("/reject", False),
    ("looks good, /accept", True),
    ("no — /reject please", False),
    ("just a normal comment", None),
    ("", None),
    ("/accepted-ish", None),  # word-boundary: not a bare /accept
])
def test_parse_verdict_command(text, expected):
    assert parse_verdict_command(text) is expected


def test_both_commands_first_wins():
    assert parse_verdict_command("/reject no wait /accept") is False
    assert parse_verdict_command("/accept actually /reject") is True


# ── callout render + event-id recovery ───────────────────────────────────────

def test_callout_carries_recoverable_event_id():
    block = proposal_callout_block("e-abc123", "add", "Theme system")
    assert block["type"] == "callout"
    assert recover_event_id(block) == "e-abc123"


def test_recover_event_id_none_for_plain_block():
    assert recover_event_id({"type": "paragraph"}) is None


def test_proposal_callouts_from_sidecar():
    sidecar = {"proposals": {
        "by_feature": {"f-1": {"op": "amend", "event_id": "e-1", "title": "Auth"}},
        "by_event": {"e-2": {"op": "add", "parent_id": "f-1", "title": "Tokens"}},
        "by_parent": {"f-1": ["e-2"]},
    }}
    callouts = proposal_callouts(sidecar)
    by_event = {c.event_id: c for c in callouts}
    assert by_event["e-1"].op == "amend" and by_event["e-1"].anchor_feature_id == "f-1"
    assert by_event["e-2"].op == "add" and by_event["e-2"].anchor_parent_id == "f-1"
    # every callout round-trips its event id
    for c in callouts:
        assert recover_event_id(c.block) == c.event_id


# ── verdict → inbox ──────────────────────────────────────────────────────────

def test_handle_comment_verdict_writes_inbox(dirs):
    _root, cd = dirs
    assert handle_comment_verdict(cd, "e-xyz", "/accept") is True
    verdicts = inbox.read_verdicts(cd)
    assert len(verdicts) == 1
    assert verdicts[0].event_id == "e-xyz" and verdicts[0].accept is True


def test_handle_comment_verdict_ignores_non_command(dirs):
    _root, cd = dirs
    assert handle_comment_verdict(cd, "e-xyz", "nice work") is None
    assert inbox.read_verdicts(cd) == []


def test_handle_comment_verdict_ignores_empty_event_id(dirs):
    _root, cd = dirs
    assert handle_comment_verdict(cd, "", "/accept") is None
    assert inbox.read_verdicts(cd) == []


# ── verdict → Loop B applies the proposal ────────────────────────────────────

def test_accept_proposal_applies_via_loop_b(dirs):
    root, cd = dirs
    s = open_store(cd)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Theme system", realized=False,
                        description="Light/dark switcher.", rationale="planned"))
    s.append_event(e)
    s.close()

    # a Notion comment "/accept" on the proposal callout → inbox verdict
    submit_verdict(cd, e.id, True)
    run_loop_b(root, cd, dry_run=True)

    s2 = open_store(cd)
    assert any(f.title == "Theme system" for f in s2.list_features())
    assert s2.pending_events() == []
    s2.close()


def test_reject_proposal_drops_it(dirs):
    root, cd = dirs
    s = open_store(cd)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Nope", realized=False,
                        description="x", rationale="planned"))
    s.append_event(e)
    s.close()

    submit_verdict(cd, e.id, False)
    run_loop_b(root, cd, dry_run=True)

    s2 = open_store(cd)
    assert not any(f.title == "Nope" for f in s2.list_features())
    assert s2.pending_events() == []
    s2.close()
