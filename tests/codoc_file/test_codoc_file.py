"""render / parse / diff of tree.codoc (new format: hidden ids, multi-paragraph
descriptions, proposals as in-situ diff hunks)."""
from __future__ import annotations

import re

import pytest

from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import ParsedNode, ParsedTree, parse_text
from codoc.codoc_file.render import (
    _compute_feature_edges, _proposals_map, _voted_event_ids, render_tree,
)
from codoc.model.binding import Binding
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _tree(store):
    root = Feature(title="Indexing layer", description="Owns the chunk + embedding substrate.")
    child = Feature(title="Index snapshot diff", parent_id=root.id,
                    description="Diffs the index before and after an update.\nSecond line of prose.")
    grand = Feature(title="Relevant subtree selection", parent_id=child.id,
                    description="Picks the minimal feature set, file-locality only.")
    sib = Feature(title="Chunk reader", parent_id=root.id, description="Reads rows from LanceDB.")
    for f in (root, child, grand, sib):
        store.upsert_feature(f)
    return root, child, grand, sib


# -- round-trip invariant -------------------------------------------------
def test_roundtrip_is_noop(store):
    _tree(store)
    store.append_event(Event(source="loop_a", applied=False,
                             op=NodeOp(kind=NodeOpKind.ADD_NODE, title="New node",
                                       description="desc", rationale="no fit")))

    text = render_tree(store)
    diff = diff_codoc(parse_text(text), store)
    assert diff.is_empty(), f"expected no-op round-trip, got {diff}"


def test_parse_recovers_structure(store):
    root, child, grand, sib = _tree(store)
    parsed = parse_text(render_tree(store))
    by_id = {n.id: n for n in parsed.nodes}

    assert by_id[child.id].parent_id == root.id
    assert by_id[grand.id].parent_id == child.id
    assert by_id[sib.id].parent_id == root.id
    assert by_id[child.id].description == "Diffs the index before and after an update.\nSecond line of prose."


def test_id_marker_present_on_disk(store):
    root, *_ = _tree(store)
    # The id stays in the bytes (the IDE hides it); authors never type it.
    assert f"⟨{root.id}⟩" in render_tree(store)


# -- multi-paragraph descriptions -----------------------------------------
def test_blank_line_does_not_end_node(store):
    f = Feature(title="Big idea",
                description="First paragraph of intent.\n\nSecond paragraph after a blank line.")
    store.upsert_feature(f)
    text = render_tree(store)
    parsed = parse_text(text)
    node = next(n for n in parsed.nodes if n.id == f.id)
    assert node.description == "First paragraph of intent.\n\nSecond paragraph after a blank line."
    # exact round-trip → no spurious AMEND
    assert diff_codoc(parsed, store).is_empty()


def test_paragraph_break_then_child(store):
    parent = Feature(title="Parent", description="Para one.\n\nPara two.")
    store.upsert_feature(parent)
    child = Feature(title="Child", parent_id=parent.id, description="kid")
    store.upsert_feature(child)
    parsed = parse_text(render_tree(store))
    by_id = {n.id: n for n in parsed.nodes}
    assert by_id[parent.id].description == "Para one.\n\nPara two."
    assert by_id[child.id].parent_id == parent.id


# -- edit detection -------------------------------------------------------
def test_amend_title_and_description(store):
    root, child, *_ = _tree(store)
    text = render_tree(store).replace("Index snapshot diff", "Snapshot diff engine")
    diff = diff_codoc(parse_text(text), store)
    amends = [o for o in diff.user_ops if o.kind is NodeOpKind.AMEND]
    assert any(o.feature_id == child.id and o.title == "Snapshot diff engine" for o in amends)


def test_retire_via_marker(store):
    root, child, *_ = _tree(store)
    text = render_tree(store).replace(f"- Index snapshot diff  ⟨{child.id}⟩",
                                      f"~ Index snapshot diff  ⟨{child.id}⟩")
    diff = diff_codoc(parse_text(text), store)
    assert any(o.kind is NodeOpKind.RETIRE_NODE and o.feature_id == child.id for o in diff.user_ops)


def test_hand_authored_node_becomes_add(store):
    _tree(store)
    text = render_tree(store) + "\n- Brand new top-level feature\n    a fresh idea\n"
    diff = diff_codoc(parse_text(text), store)
    adds = [o for o in diff.user_ops if o.kind is NodeOpKind.ADD_NODE]
    assert any(o.title == "Brand new top-level feature" for o in adds)
    assert adds[0].parent_id is None


def test_empty_title_node_is_not_emitted_as_add(store):
    """A featureHeading with blank title (mid-creation transient state, e.g. user
    typed `## ` but hasn't typed the title yet) must NOT produce an ADD_NODE.
    Without this guard, apply_op falls back to 'Untitled', permanently creating a
    spurious feature in the store."""
    _tree(store)
    # Append a node line with no title (simulates a freshly-created empty heading).
    text = render_tree(store) + "\n-  \n"
    diff = diff_codoc(parse_text(text), store)
    adds = [o for o in diff.user_ops if o.kind is NodeOpKind.ADD_NODE]
    assert adds == [], "empty-title node should not emit ADD_NODE"


def test_whitespace_only_title_node_is_not_emitted_as_add(store):
    """A node with only whitespace in its title also should not emit ADD_NODE."""
    _tree(store)
    text = render_tree(store) + "\n-     \n    some description\n"
    diff = diff_codoc(parse_text(text), store)
    adds = [o for o in diff.user_ops if o.kind is NodeOpKind.ADD_NODE]
    assert adds == [], "whitespace-only-title node should not emit ADD_NODE"


# ─── local_id-keyed identity (doc channel, has_local_ids=True) ───────────────

def test_local_id_keying_resolves_node_with_null_fid(store):
    """A node whose fid is null (TipTap undo reset it) but whose local_id maps to a
    live feature is recognized as that feature — AMEND, not a duplicate ADD."""
    f = Feature(title="Auth", description="Old prose.", local_id="lid-auth-1")
    store.upsert_feature(f)
    parsed = ParsedTree(nodes=[ParsedNode(
        id=None, title="Auth", description="New prose.", parent_id=None,
        retired=False, local_id="lid-auth-1")])
    diff = diff_codoc(parsed, store, has_local_ids=True)
    adds = [o for o in diff.user_ops if o.kind is NodeOpKind.ADD_NODE]
    amends = [o for o in diff.user_ops if o.kind is NodeOpKind.AMEND]
    assert adds == [], "null fid + known local_id must NOT be an ADD"
    assert len(amends) == 1 and amends[0].feature_id == f.id


def test_duplicate_local_id_second_is_add_not_clobber(store):
    """A cloned subtree (copy-paste / heading-split) can carry a duplicate local_id
    before the editor re-mints it. The FIRST node claims the feature; the SECOND with
    the same local_id is a genuine ADD — never a silent clobber of the original."""
    f = Feature(title="Original", description="Body.", local_id="lid-dup")
    store.upsert_feature(f)
    parsed = ParsedTree(nodes=[
        ParsedNode(id=None, title="Original", description="Body.", parent_id=None,
                   retired=False, local_id="lid-dup"),
        ParsedNode(id=None, title="Pasted clone", description="Body.", parent_id=None,
                   retired=False, local_id="lid-dup"),
    ])
    diff = diff_codoc(parsed, store, has_local_ids=True)
    adds = [o for o in diff.user_ops if o.kind is NodeOpKind.ADD_NODE]
    assert len(adds) == 1, "the duplicate-local_id clone must become an ADD"
    assert adds[0].title == "Pasted clone"


def test_local_id_mapping_to_retired_feature_is_not_add(store):
    """A node whose local_id maps to a RETIRED feature (reappearing via undo, or stale
    crash debris) must NOT be an ADD — the un-retire arrives as an explicit command
    (U3/U4); diff_codoc only guards the non-destructive write checks in reconcile.py."""
    f = Feature(title="Gone", description="Body.", local_id="lid-gone")
    store.upsert_feature(f)
    store.retire_feature(f.id)
    parsed = ParsedTree(nodes=[ParsedNode(
        id=None, title="Gone", description="Body.", parent_id=None,
        retired=False, local_id="lid-gone")])
    diff = diff_codoc(parsed, store, has_local_ids=True)
    adds = [o for o in diff.user_ops if o.kind is NodeOpKind.ADD_NODE]
    assert adds == [], "local_id → retired feature must not duplicate-ADD"


# ─── title clear (Step 4): doc channel clears, text channel is transient-safe ──

def test_doc_channel_blank_title_is_a_deliberate_clear(store):
    """On the doc channel a heading's title content is authoritative — emptying it is a
    deliberate clear → AMEND title='' (fixes the silent-revert data-loss). Resolves via
    local_id, so it is the AMEND branch, never a spurious ADD."""
    f = Feature(title="Login Handler", description="Body.", local_id="lid-login")
    store.upsert_feature(f)
    parsed = ParsedTree(nodes=[ParsedNode(
        id=f.id, title="", description="Body.", parent_id=None,
        retired=False, local_id="lid-login")])
    diff = diff_codoc(parsed, store, has_local_ids=True)
    amends = [o for o in diff.user_ops if o.kind is NodeOpKind.AMEND]
    assert len(amends) == 1 and amends[0].title == ""    # cleared, not reverted
    assert [o for o in diff.user_ops if o.kind is NodeOpKind.ADD_NODE] == []


def test_text_channel_blank_title_is_preserved(store):
    """On the text channel a blank `-  ⟨fid⟩` line has no structured signal (could be a
    transient mid-edit), so the stored title is preserved (R19) — no AMEND emitted."""
    f = Feature(title="Login Handler", description="Body.")
    store.upsert_feature(f)
    parsed = ParsedTree(nodes=[ParsedNode(
        id=f.id, title="", description="Body.", parent_id=None, retired=False)])
    diff = diff_codoc(parsed, store, has_local_ids=False)
    assert diff.user_ops == [], "text-channel blank title must not overwrite the stored title"


def test_cleared_title_round_trip_is_idempotent(store):
    """After a title is cleared to '' the render→parse→diff is a fixed point — no phantom
    AMEND, no re-apply loop."""
    f = Feature(title="", description="Holds brand colors.")
    store.upsert_feature(f)
    text = render_tree(store)
    assert diff_codoc(parse_text(text), store).is_empty()  # text channel
    # and the doc-channel projection of the same empty-title feature is also a no-op
    parsed = ParsedTree(nodes=[ParsedNode(
        id=f.id, title="", description="Holds brand colors.", parent_id=None,
        retired=False, local_id=f.local_id or "")])
    assert diff_codoc(parsed, store, has_local_ids=True).is_empty()


def test_reparent_detected(store):
    root, child, grand, sib = _tree(store)
    text = f"""# header
- Indexing layer  ⟨{root.id}⟩
    Owns the chunk + embedding substrate.

  - Index snapshot diff  ⟨{child.id}⟩
      Diffs the index before and after an update.
      Second line of prose.

    - Relevant subtree selection  ⟨{grand.id}⟩
        Picks the minimal feature set, file-locality only.

    - Chunk reader  ⟨{sib.id}⟩
        Reads rows from LanceDB.
"""
    diff = diff_codoc(parse_text(text), store)
    moves = [o for o in diff.user_ops if o.kind is NodeOpKind.MOVE_NODE]
    assert any(o.feature_id == sib.id and o.parent_id == child.id for o in moves)


# -- proposals render in situ as diff hunks -------------------------------
def _add_proposal(store):
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Proposed thing",
                        description="does a thing", rationale="no node fits"))
    store.append_event(e)
    return e


def test_proposal_renders_as_diff_hunk(store):
    _tree(store)
    e = _add_proposal(store)
    text = render_tree(store)
    # in-situ diff hunk: col-0 '+' op char, then the node at its tree depth
    # (a root add → no indent), hidden event id, then a '+'-prefixed body.
    assert f"+ - Proposed thing  ⟨{e.id}⟩" in text
    assert "+     does a thing" in text


def test_proposals_never_appear_as_live_nodes(store):
    _tree(store)
    _add_proposal(store)
    parsed = parse_text(render_tree(store))
    # in-situ proposal blocks are skipped → no node titled "Proposed thing"
    assert all(n.title != "Proposed thing" for n in parsed.nodes)
    # …and the proposal does not create a phantom user op
    assert diff_codoc(parsed, store).is_empty()


def test_retire_and_move_proposals_render(store):
    root, child, *_ = _tree(store)
    retire = Event(source="loop_a", applied=False,
                   op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=child.id, rationale="gone"))
    store.append_event(retire)
    store.append_event(Event(source="loop_a", applied=False,
                             op=NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=child.id, parent_id=root.id)))
    text = render_tree(store)
    # Retire decorates the live node in place (no text ghost): it rides in the
    # sidecar, and the live node text still reads "Index snapshot diff" once.
    assert "retire · code drift" not in text
    proposals = _proposals_map(store)
    assert proposals["by_feature"][child.id] == {
        "op": "retire", "event_id": retire.id, "tag": "code drift", "rationale": "gone",
        "actor": "loop", "mode": "suggest", "caused_by": "",
        # a default retire is detach-only — accepting it never touches the code
        "writes_code": None, "verdict_pending": False,
    }
    # Move still emits a destination ghost hunk in text.
    assert re.search(r"(?m)^~ \s*- Index snapshot diff", text)
    assert "move → Indexing layer" in text


def test_retire_amend_overlay_not_in_text_roundtrip_noop(store):
    """RETIRE/AMEND leave the live node's text untouched → render→parse→diff no-op."""
    root, child, *_ = _tree(store)
    store.append_event(Event(source="loop_a", applied=False,
                             op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=child.id)))
    store.append_event(Event(source="loop_a", applied=False,
                             op=NodeOp(kind=NodeOpKind.AMEND, feature_id=root.id,
                                       description="Reworded intent.")))
    text = render_tree(store)
    # No proposal text leaked for retire/amend.
    assert "retire" not in text and "amend" not in text
    parsed = parse_text(text)
    assert diff_codoc(parsed, store).is_empty()
    # The text is identical to a clean render with no pending events.
    for e in store.pending_events():
        store.delete_event(e.id)
    assert text == render_tree(store)


def test_sidecar_proposals_map_shape(store):
    root, child, *_ = _tree(store)
    retire = Event(source="loop_a_agent", applied=False,
                   op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=child.id, rationale="gone"))
    amend = Event(source="plan", applied=False,
                  op=NodeOp(kind=NodeOpKind.AMEND, feature_id=root.id,
                            title="New title", description="New prose."))
    add = Event(source="loop_a", applied=False,
                op=NodeOp(kind=NodeOpKind.ADD_NODE, parent_id=root.id, title="New child",
                          description="child prose"))
    for e in (retire, amend, add):
        store.append_event(e)

    m = _proposals_map(store)
    assert m["by_feature"][child.id]["op"] == "retire"
    assert m["by_feature"][child.id]["tag"] == "agent reflection"  # loop_a_agent
    assert m["by_feature"][root.id] == {
        "op": "amend", "event_id": amend.id, "tag": "agent plan", "rationale": "",
        "title": "New title", "description": "New prose.",
        "actor": "claude-code", "mode": "suggest", "caused_by": "",
        "writes_code": None, "verdict_pending": False,
    }
    assert m["by_event"][add.id]["op"] == "add"
    assert m["by_event"][add.id]["parent_id"] == root.id
    # by_parent anchors the ADD ghost under its destination parent so the IDE can
    # offer Accept/Reject at the parent node, not only on the ghost line.
    assert add.id in m["by_parent"][root.id]


# -- feature_edges sidecar helper -----------------------------------------
def test_compute_feature_edges_aggregates_symbol_edges(store):
    """_compute_feature_edges returns cross-feature coupling edges from code_edges."""
    caller = Feature(title="Caller feature")
    callee = Feature(title="Callee feature")
    store.upsert_feature(caller)
    store.upsert_feature(callee)

    store.upsert_binding(Binding(feature_id=caller.id, file="a.py", symbol_path="a.py::caller_fn", fingerprint="h1"))
    store.upsert_binding(Binding(feature_id=callee.id, file="b.py", symbol_path="b.py::callee_fn", fingerprint="h2"))

    store.insert_edges([{
        "src_file": "a.py",
        "src_symbol": "a.py::caller_fn",
        "dst_name": "callee_fn",
        "dst_symbol": "b.py::callee_fn",
        "dst_file": "b.py",
        "kind": "call",
        "internal": 1,
    }])

    result = _compute_feature_edges(store)

    assert caller.id in result
    edges = result[caller.id]
    assert len(edges) == 1
    assert edges[0]["to"] == callee.id
    assert edges[0]["weight"] == 1
    assert edges[0]["kinds"] == ["call"]
    # callee has no outgoing edges to other features
    assert callee.id not in result


# -- what ACCEPTING a proposal will actually do ---------------------------
# Every proposal looks alike on screen, but only two kinds hand work to the agent.
# The IDE cannot label the verdict honestly unless the sidecar says which is which.

def test_proposals_map_marks_the_two_kinds_that_write_code(store):
    root, child, *_ = _tree(store)
    plan = Event(source="plan", applied=False,
                 op=NodeOp(kind=NodeOpKind.ADD_NODE, parent_id=root.id, title="Not built yet",
                           description="intent", realized=False))
    plain_add = Event(source="loop_a", applied=False,
                      op=NodeOp(kind=NodeOpKind.ADD_NODE, parent_id=root.id,
                                title="Already in the code", description="found it"))
    kill = Event(source="loop_a_agent", applied=False,
                 op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=child.id, delete_code=True))
    for e in (plan, plain_add, kill):
        store.append_event(e)

    m = _proposals_map(store)
    # a plan placeholder describes code that does not exist → accepting is a build request
    assert m["by_event"][plan.id]["writes_code"] == "build"
    # an ADD that binds code already on disk is pure bookkeeping
    assert m["by_event"][plain_add.id]["writes_code"] is None
    # only an explicit delete-code retire removes code
    assert m["by_feature"][child.id]["writes_code"] == "remove"


def test_proposals_map_flags_a_verdict_already_waiting_in_the_inbox(store, tmp_path):
    """A click that has not landed yet must read as "recorded, waiting" — not as a
    fresh Accept button, which looks like the click failed. Covers every reason a
    verdict can be stuck: no daemon, pass not run, or a code-implying accept that
    Loop B deferred to a realize pass."""
    from codoc.loop import inbox

    root, child, *_ = _tree(store)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=child.id))
    store.append_event(e)

    codoc_dir = tmp_path / "cd"
    codoc_dir.mkdir()
    assert _proposals_map(store, _voted_event_ids(codoc_dir))["by_feature"][child.id][
        "verdict_pending"] is False

    inbox.append_verdict(codoc_dir, e.id, accept=True)
    assert _proposals_map(store, _voted_event_ids(codoc_dir))["by_feature"][child.id][
        "verdict_pending"] is True


def test_voted_event_ids_survives_a_missing_or_broken_inbox(tmp_path):
    """A derived legibility hint must never be able to break the render."""
    assert _voted_event_ids(tmp_path / "nope") == set()
    d = tmp_path / "cd"
    d.mkdir()
    (d / "inbox.json").write_text("{not json")
    assert _voted_event_ids(d) == set()


# -- the auto-edit slice: exactly one automatic op, deliberately ------------------

def _auto_slice(store):
    from codoc.codoc_file.render import _auto_edits
    live = {f.id for f in store.list_features()}
    return _auto_edits(store.recent_events(50), live)


def test_auto_edits_reports_a_loop_rewrite_with_the_prose_it_displaced(store):
    from codoc.loop.apply import apply_op
    from codoc.model.event import ACTOR_HUMAN

    root, *_ = _tree(store)
    store.set_feature_writer(root.id, "user", ACTOR_HUMAN)
    before = store.get_feature(root.id).description
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=root.id,
                    description="The loop's new wording.", rationale="code drifted"),
             store, source="loop_a", applied=True)

    slice_ = _auto_slice(store)
    assert slice_[root.id]["prev"] == before
    assert slice_[root.id]["written_by"] == ACTOR_HUMAN   # it overwrote a person's words
    assert slice_[root.id]["rationale"] == "code drifted"


def test_auto_edits_excludes_the_machinery_nobody_needs_to_know_about(store):
    """refresh/attach/detach fire constantly and no decision depends on them. Reporting
    them would set the noise floor and teach the reader to ignore the channel — the one
    failure mode this slice exists to avoid."""
    from codoc.loop.apply import apply_op

    root, child, *_ = _tree(store)
    for kind in (NodeOpKind.ATTACH, NodeOpKind.REFRESH):
        apply_op(NodeOp(kind=kind, feature_id=child.id,
                        bindings=[("a.py", "a.py::f")]), store, source="loop_a", applied=True)
    apply_op(NodeOp(kind=NodeOpKind.DETACH, feature_id=child.id,
                    bindings=[("a.py", "a.py::f")]), store, source="loop_a", applied=True)
    assert _auto_slice(store) == {}


def test_auto_edits_excludes_a_human_edit_and_an_unapplied_proposal(store):
    """Only changes the reader did not make and was not asked about. Their own edit is
    not news to them, and a proposal has not happened yet."""
    from codoc.loop.apply import apply_op
    from codoc.model.event import ACTOR_HUMAN, MODE_PEN

    root, child, *_ = _tree(store)
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=root.id, description="I wrote this."),
             store, source="user", applied=True, actor=ACTOR_HUMAN, mode=MODE_PEN)
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=child.id, description="Merely proposed."),
             store, source="loop_a", applied=False)
    assert _auto_slice(store) == {}


def test_auto_edits_keeps_only_the_newest_rewrite_per_feature(store):
    from codoc.loop.apply import apply_op

    root, *_ = _tree(store)
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=root.id, description="First rewrite."),
             store, source="loop_a", applied=True)
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=root.id, description="Second rewrite."),
             store, source="loop_a", applied=True)
    # newest wins: what the reader needs is the wording they last saw replaced
    assert _auto_slice(store)[root.id]["prev"] == "First rewrite."


def test_auto_edits_drops_a_retired_feature(store):
    """A rewrite of prose that is no longer in the document has nothing to point at."""
    from codoc.loop.apply import apply_op

    root, child, *_ = _tree(store)
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=child.id, description="Rewritten."),
             store, source="loop_a", applied=True)
    assert child.id in _auto_slice(store)
    store.retire_feature(child.id)
    assert child.id not in _auto_slice(store)
