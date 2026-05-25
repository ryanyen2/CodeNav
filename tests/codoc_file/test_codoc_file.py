"""Phase 3 — render / parse / diff of tree.codoc."""
from __future__ import annotations

import pytest

from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import parse_text
from codoc.codoc_file.render import render_tree
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
    parsed = parse_text(text)
    diff = diff_codoc(parsed, store)

    assert diff.is_empty(), f"expected no-op round-trip, got {diff}"


def test_parse_recovers_structure(store):
    root, child, grand, sib = _tree(store)
    parsed = parse_text(render_tree(store))
    by_id = {n.id: n for n in parsed.nodes}

    assert by_id[child.id].parent_id == root.id
    assert by_id[grand.id].parent_id == child.id
    assert by_id[sib.id].parent_id == root.id
    assert by_id[child.id].description == "Diffs the index before and after an update.\nSecond line of prose."


# -- edit detection -------------------------------------------------------
def test_amend_title_and_description(store):
    root, child, *_ = _tree(store)
    text = render_tree(store).replace("Index snapshot diff", "Snapshot diff engine")
    diff = diff_codoc(parse_text(text), store)
    amends = [o for o in diff.user_ops if o.kind is NodeOpKind.AMEND]
    assert any(o.feature_id == child.id and o.title == "Snapshot diff engine" for o in amends)


def test_retire_via_marker(store):
    root, child, *_ = _tree(store)
    # flip the child's '-' marker to '~'
    text = render_tree(store).replace(f"- Index snapshot diff  ⟨{child.id}⟩",
                                      f"~ Index snapshot diff  ⟨{child.id}⟩")
    diff = diff_codoc(parse_text(text), store)
    assert any(o.kind is NodeOpKind.RETIRE_NODE and o.feature_id == child.id for o in diff.user_ops)


def test_hand_authored_node_becomes_add(store):
    root, *_ = _tree(store)
    text = render_tree(store) + "\n- Brand new top-level feature\n    a fresh idea\n"
    diff = diff_codoc(parse_text(text), store)
    adds = [o for o in diff.user_ops if o.kind is NodeOpKind.ADD_NODE]
    assert any(o.title == "Brand new top-level feature" for o in adds)
    assert adds[0].parent_id is None


def test_reparent_detected(store):
    root, child, grand, sib = _tree(store)
    # move 'Chunk reader' (sib) to be a child of 'Index snapshot diff' (child)
    # by re-authoring the text with deeper indentation under child.
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


# -- proposal verdicts ----------------------------------------------------
def _flip_action(text: str, event_id: str, new: str) -> str:
    out = []
    for line in text.splitlines():
        if event_id in line and line.lstrip().startswith("?"):
            line = line.replace("?", new, 1)
        out.append(line)
    return "\n".join(out)


@pytest.fixture
def proposal(store):
    _tree(store)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Proposed thing",
                        description="does a thing", rationale="no node fits"))
    store.append_event(e)
    return e


def test_proposal_pending_no_verdict(store, proposal):
    diff = diff_codoc(parse_text(render_tree(store)), store)
    assert diff.verdicts == []


def test_proposal_accept(store, proposal):
    text = _flip_action(render_tree(store), proposal.id, "+")
    diff = diff_codoc(parse_text(text), store)
    assert [(v.event_id, v.accept) for v in diff.verdicts] == [(proposal.id, True)]


def test_proposal_reject_via_minus(store, proposal):
    text = _flip_action(render_tree(store), proposal.id, "-")
    diff = diff_codoc(parse_text(text), store)
    assert [(v.event_id, v.accept) for v in diff.verdicts] == [(proposal.id, False)]


def test_proposal_reject_via_deletion(store, proposal):
    # drop every line mentioning the event id
    text = "\n".join(l for l in render_tree(store).splitlines() if proposal.id not in l)
    diff = diff_codoc(parse_text(text), store)
    assert [(v.event_id, v.accept) for v in diff.verdicts] == [(proposal.id, False)]
