"""The timeline transport (W8) — what an applied op displaces, and the file that ships it.

Two things are pinned here, and they are the load-bearing ones:

1. Every applied op records what it destroyed (`loop.apply._record_displaced`), so the
   editor can walk the ledger BACKWARDS from the live tree. Each of these tests is a
   different way for that walk to lose text.
2. `revisions.json` carries only what was really recorded — never a guess — and joins
   each change to the directive, prompt, and session behind it.
"""
from __future__ import annotations

import json

import pytest

from codoc.loop.apply import apply_op
from codoc.loop.revisions import (
    REVISION_LIMIT, _same_window, build_revisions, revisions_path, write_revisions,
)
from codoc.model.event import ACTOR_HUMAN, MODE_PEN, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _add(store, title, description="", parent_id=None):
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title=title, description=description,
                parent_id=parent_id)
    apply_op(op, store, source="user", applied=True)
    return op.feature_id


# ── what an applied op records about what it displaced ───────────────────────

def test_amend_records_the_title_it_replaced(store):
    fid = _add(store, "Sesions", "Handles login.")
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, title="Sessions")
    apply_op(op, store, source="user", applied=True)

    assert op.prev_title == "Sesions"
    assert store.get_feature(fid).title == "Sessions"


def test_amend_records_the_description_it_replaced(store):
    fid = _add(store, "Sessions", "Handles login.")
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                description="Handles login and refresh.")
    apply_op(op, store, source="loop_a", applied=True)

    assert op.prev_description == "Handles login."
    assert op.prev_title is None  # the op never touched the title — say nothing about it


def test_amend_records_nothing_for_a_field_that_did_not_change(store):
    """A no-op rewrite must not claim to have displaced anything.

    Otherwise a save that re-sends identical text mints a revision whose diff is empty,
    and the timeline fills with ticks that show the reader nothing.
    """
    fid = _add(store, "Sessions", "Handles login.")
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, title="Sessions",
                description="Handles login.")
    apply_op(op, store, source="user", applied=True)

    assert op.prev_title is None
    assert op.prev_description is None


def test_a_pending_proposal_displaces_nothing(store):
    fid = _add(store, "Sessions", "Handles login.")
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description="Rewritten.")
    apply_op(op, store, source="loop_a", applied=False)

    assert op.prev_description is None
    assert store.get_feature(fid).description == "Handles login."


def test_a_caller_supplied_prev_is_never_overwritten(store):
    """An accepted proposal carries the base it was computed against.

    Re-deriving it from the store at apply time would silently re-anchor the change to
    whatever landed in between, and the diff the reader is shown would no longer be the
    diff anyone authored.
    """
    fid = _add(store, "Sessions", "Handles login.")
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description="Interim."),
             store, source="loop_a", applied=True)

    op = NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description="Final.",
                prev_description="Handles login.")
    apply_op(op, store, source="user", applied=True)
    assert op.prev_description == "Handles login."


def test_retire_records_the_text_it_takes_out_of_the_tree(store):
    """A retired feature leaves the projection entirely.

    Without its text the timeline can show that a node used to be there but not what it
    said — which is the one thing worth seeing about a node that no longer exists.
    """
    fid = _add(store, "Legacy export", "Writes the old CSV format.")
    op = NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=fid)
    apply_op(op, store, source="user", applied=True)

    assert op.prev_title == "Legacy export"
    assert op.prev_description == "Writes the old CSV format."
    assert op.prev_parent_id == ""  # it sat at the root — and now nothing else can say so


def test_move_records_the_parent_it_left(store):
    root_a = _add(store, "Storage")
    root_b = _add(store, "Indexing")
    child = _add(store, "Chunk cache", parent_id=root_a)

    op = NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=child, parent_id=root_b)
    apply_op(op, store, source="user", applied=True)

    assert op.prev_parent_id == root_a
    assert store.get_feature(child).parent_id == root_b


def test_move_off_a_root_records_empty_string_not_none(store):
    """`None` means "not recorded" and `""` means "was a root" — collapsing the two
    would silently re-root every node whose move predates the field."""
    parent = _add(store, "Storage")
    orphan = _add(store, "Chunk cache")

    op = NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=orphan, parent_id=parent)
    apply_op(op, store, source="user", applied=True)
    assert op.prev_parent_id == ""


def test_retiring_a_parent_logs_its_children_promotion(store):
    """Retiring a node lifts its children to the grandparent — a real tree mutation the
    ledger used to make in silence, so `codoc history` showed nothing and a backwards
    replay lost the subtree."""
    grandparent = _add(store, "Storage")
    parent = _add(store, "Index", parent_id=grandparent)
    child = _add(store, "Chunk cache", parent_id=parent)

    retire = NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=parent)
    apply_op(retire, store, source="user", applied=True, actor=ACTOR_HUMAN, mode=MODE_PEN)

    assert store.get_feature(child).parent_id == grandparent
    moves = [e for e in store.events_for_feature(child)
             if e.op.kind is NodeOpKind.MOVE_NODE]
    assert len(moves) == 1
    assert moves[0].op.prev_parent_id == parent
    assert moves[0].op.parent_id == grandparent
    # Provenance is INHERITED: whoever retired the parent authored its consequences,
    # and the move is grouped under the retire rather than floating unexplained.
    assert moves[0].actor == ACTOR_HUMAN
    assert moves[0].caused_by


# ── revisions.json ───────────────────────────────────────────────────────────

def test_revisions_carry_both_sides_of_a_text_change(store, tmp_path):
    fid = _add(store, "Sessions", "Handles login.")
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                    description="Handles login and refresh."),
             store, source="loop_a", applied=True)

    doc = build_revisions(store.recent_events(50), tmp_path)
    amend = next(r for r in doc["revisions"] if r["kind"] == "amend")
    assert amend["description"] == "Handles login and refresh."
    assert amend["prev_description"] == "Handles login."
    assert amend["feature_id"] == fid


def test_revisions_omit_a_field_the_op_did_not_touch(store, tmp_path):
    """Presence-keyed: absent means "this op left that alone", which a `null` could not
    express — the reconstructor would read it as "cleared it"."""
    fid = _add(store, "Sessions", "Handles login.")
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description="Rewritten."),
             store, source="loop_a", applied=True)

    amend = next(r for r in build_revisions(store.recent_events(50), tmp_path)["revisions"]
                 if r["kind"] == "amend")
    assert "title" not in amend
    assert "prev_title" not in amend


def test_refresh_never_reaches_the_timeline(store, tmp_path):
    """The noisiest event kind, and it changes nothing a reader can see."""
    fid = _add(store, "Sessions", "Handles login.")
    apply_op(NodeOp(kind=NodeOpKind.REFRESH, feature_id=fid,
                    bindings=[("auth.py", "auth.py::login")]),
             store, source="loop_a", applied=True)

    kinds = {r["kind"] for r in build_revisions(store.recent_events(50), tmp_path)["revisions"]}
    assert "refresh" not in kinds
    assert "add_node" in kinds


def test_pending_proposals_never_reach_the_timeline(store, tmp_path):
    fid = _add(store, "Sessions", "Handles login.")
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description="Proposed."),
             store, source="loop_a", applied=False)

    revs = build_revisions(store.recent_events(50), tmp_path)["revisions"]
    assert all(r["kind"] != "amend" for r in revs)


def test_revisions_join_the_directive_prompt_and_session(store, tmp_path):
    """The whole point of the provenance chain: a changed sentence → the directive that
    asked for it → the prompt the human typed → the session they typed it in."""
    from codoc.loop import edits as edits_channel

    fid = _add(store, "Uploads", "Accepts files.")
    directive = edits_channel.Directive(
        id="d-abc123", feature_id=fid, kind="amend", text='UPDATE FEATURE: "Uploads"',
        asked="add rate limiting to the upload endpoint",
        session_id="0568a9e3-9988-4a00", base_sha="a" * 40)
    edits_channel.write_manifest(tmp_path, [directive])

    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                    description="Accepts files, at most 5 per minute."),
             store, source="loop_b", applied=True, caused_by="d-abc123")

    doc = build_revisions(store.recent_events(50), tmp_path)
    amend = next(r for r in doc["revisions"] if r["kind"] == "amend")
    assert amend["caused_by"] == "d-abc123"

    cited = doc["directives"]["d-abc123"]
    assert cited["asked"] == "add rate limiting to the upload endpoint"
    assert cited["session_id"] == "0568a9e3-9988-4a00"
    assert cited["base_sha"] == "a" * 40
    assert cited["done"] is False  # still queued


def test_only_cited_directives_are_shipped(store, tmp_path):
    from codoc.loop import edits as edits_channel

    fid = _add(store, "Uploads", "Accepts files.")
    edits_channel.write_manifest(tmp_path, [
        edits_channel.Directive(id="d-cited", feature_id=fid, kind="amend", text="x"),
        edits_channel.Directive(id="d-unrelated", feature_id=fid, kind="amend", text="y"),
    ])
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description="Rewritten."),
             store, source="loop_b", applied=True, caused_by="d-cited")

    doc = build_revisions(store.recent_events(50), tmp_path)
    assert set(doc["directives"]) == {"d-cited"}


def test_write_revisions_skips_an_unchanged_window(store, tmp_path):
    _add(store, "Sessions", "Handles login.")
    write_revisions(store.recent_events(50), tmp_path)
    path = revisions_path(tmp_path)
    first = path.stat().st_mtime_ns

    write_revisions(store.recent_events(50), tmp_path)
    assert path.stat().st_mtime_ns == first, \
        "a byte-identical rewrite wakes every file-watcher for nothing"

    _add(store, "Uploads", "Accepts files.")
    write_revisions(store.recent_events(50), tmp_path)
    assert json.loads(path.read_text())["revisions"][0]["title"] == "Uploads"


def test_truncated_says_there_is_older_history(store, tmp_path):
    for i in range(REVISION_LIMIT + 5):
        _add(store, f"Feature {i}")

    doc = build_revisions(store.recent_events(REVISION_LIMIT + 50), tmp_path)
    assert len(doc["revisions"]) == REVISION_LIMIT
    assert doc["truncated"] is True


def test_write_revisions_never_raises_on_a_bad_directory(store):
    """A timeline that fails to write costs the reader a view; it must never cost them
    the render pass it rides on."""
    write_revisions(store.recent_events(10), "/nonexistent/\x00/path")


# ── the git anchor ───────────────────────────────────────────────────────────

def test_git_helper_survives_an_unusable_subprocess_layer(monkeypatch, tmp_path):
    """A provenance lookup must never be able to stop the realize queue.

    `head_sha` runs on the hand-off path, immediately before Loop B writes the trigger
    file. A wrapped or instrumented `Popen` (a test double, a sandbox, a tracer) makes
    `subprocess.run` raise `AttributeError` rather than `OSError` — which once meant an
    absent anchor silently cost the author their queued code change.
    """
    import subprocess

    from codoc.loop.gitref import changed_files, head_sha

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: None)
    assert head_sha(tmp_path) == ""
    assert changed_files(tmp_path, "a" * 40) == []


def test_head_sha_is_empty_outside_a_repo(tmp_path):
    from codoc.loop.gitref import head_sha

    assert head_sha(tmp_path) == ""


def test_changed_files_needs_an_anchor(tmp_path):
    from codoc.loop.gitref import changed_files

    assert changed_files(tmp_path, "") == []


def test_a_clock_skewed_event_is_not_skipped(store, tmp_path):
    """HLCs are minted by several processes, so a new event can sort BELOW the current
    head. At the window cap it lands mid-list and pushes the oldest out — head and length
    both unchanged. A head-only guard skipped that write and the event never reached the
    file."""
    from codoc.model.hlc import HLC

    _add(store, "First")
    _add(store, "Second")
    write_revisions(store.recent_events(50), tmp_path)
    before = json.loads(revisions_path(tmp_path).read_text())["revisions"]

    # Same window length, same newest id — one entry swapped in the middle.
    skewed = [dict(before[0]), {**before[1], "event_id": "e-skewed"}]
    doc = {"version": 1, "revisions": skewed, "directives": {}, "truncated": False}
    assert not _same_window(json.loads(revisions_path(tmp_path).read_text()), doc)


def test_an_unchanged_window_is_still_recognised(store, tmp_path):
    _add(store, "First")
    write_revisions(store.recent_events(50), tmp_path)
    prior = json.loads(revisions_path(tmp_path).read_text())
    assert _same_window(prior, build_revisions(store.recent_events(50), tmp_path))


def test_a_revision_carries_what_its_prose_rests_on(store, tmp_path):
    """The chain says what happened before a claim; the warrant says what the claim
    stands on. Both ride the same entry, because a reader dragging the scrubber to the
    moment a sentence appeared is asking whether to believe it."""
    from codoc.model.event import Warrant

    fid = _add(store, "Retry policy", "Tries once.")
    apply_op(
        NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
               description="Retries only on a timeout, because the server can "
                           "duplicate a non-idempotent post.",
               rationale="the client retries now",
               warrant=[Warrant(kind="commit", ref="1a2b3c4d",
                                quote="Retry only on timeout — the server can duplicate."),
                        Warrant(kind="intent", quote="add a retry guard to fan-out")]),
        store, source="loop_a_agent", applied=True, actor="claude-code", mode="auto")

    amend = next(r for r in build_revisions(store.recent_events(50), tmp_path)["revisions"]
                 if r["kind"] == "amend")
    assert [w["kind"] for w in amend["warrant"]] == ["commit", "intent"]
    assert amend["warrant"][0]["ref"] == "1a2b3c4d"
    # An intent has no address of its own, so the key is absent rather than empty —
    # the reader can then treat presence as "there is somewhere to go look".
    assert "ref" not in amend["warrant"][1]


def test_a_revision_with_no_warrant_omits_the_key(store, tmp_path):
    fid = _add(store, "Totals", "Adds numbers.")
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                    description="Computes the total on write."),
             store, source="user", applied=True, actor=ACTOR_HUMAN, mode=MODE_PEN)
    amend = next(r for r in build_revisions(store.recent_events(50), tmp_path)["revisions"]
                 if r["kind"] == "amend")
    assert "warrant" not in amend
