"""render / parse / diff of tree.codoc (new format: hidden ids, multi-paragraph
descriptions, proposals as in-situ diff hunks)."""
from __future__ import annotations

import re

import pytest

from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import parse_text
from codoc.codoc_file.render import _compute_feature_edges, _proposals_map, render_tree
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
    }
    assert m["by_event"][add.id]["op"] == "add"
    assert m["by_event"][add.id]["parent_id"] == root.id


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
