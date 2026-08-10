"""apply_op realization transition: a plan placeholder becomes realized on bind."""
from __future__ import annotations

import pytest

from codoc.loop.apply import apply_op
from codoc.model.event import ACTOR_HUMAN, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def test_add_node_unrealized_then_attach_realizes(store):
    # A plan placeholder: ADD_NODE with realized=False, no bindings.
    add = NodeOp(kind=NodeOpKind.ADD_NODE, title="Dark mode", description="UI theme.",
                 realized=False)
    apply_op(add, store, source="plan", applied=True)
    feature = next(f for f in store.list_features() if f.title == "Dark mode")
    assert feature.realized is False

    # First code binds → flips realized.
    attach = NodeOp(kind=NodeOpKind.ATTACH, feature_id=feature.id,
                    bindings=[("ui/theme.py", "ui/theme.py::apply_dark")])
    apply_op(attach, store, source="loop_a_agent", applied=True)
    assert store.get_feature(feature.id).realized is True


def test_add_node_defaults_realized_true(store):
    add = NodeOp(kind=NodeOpKind.ADD_NODE, title="Regular feature", description="x")
    apply_op(add, store, source="loop_a", applied=True)
    feature = next(f for f in store.list_features() if f.title == "Regular feature")
    assert feature.realized is True


def test_amend_updated_at_is_strictly_monotonic(store):
    """P2 — two AMENDs in the same wall-clock ms must yield STRICTLY increasing
    updated_at. HLC.now() always returns logical_time=0, so same-ms edits tied and the
    webview's "strictly newer" doc-gate could miss a real change; advance() bumps the
    feature's own logical counter to keep each edit ordered."""
    add = NodeOp(kind=NodeOpKind.ADD_NODE, title="F", description="v0")
    apply_op(add, store, source="user", applied=True)
    fid = next(f for f in store.list_features() if f.title == "F").id

    stamps = []
    for i in range(3):
        apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description=f"v{i + 1}"),
                 store, source="user", applied=True)
        stamps.append(store.get_feature(fid).updated_at)
    # Even within one millisecond, each stamp is strictly greater than the last.
    assert stamps[0] < stamps[1] < stamps[2]


def _render_reachable_ids(store) -> set[str]:
    """Ids reachable from the roots via live parent links — i.e. actually visible in
    the tree (render/projection/sidecar all walk from the roots)."""
    kids: dict = {}
    for f in store.list_features():
        kids.setdefault(f.parent_id, []).append(f.id)
    seen, stack = set(), list(kids.get(None, []))
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        stack.extend(kids.get(nid, []))
    return seen


def test_move_under_retired_parent_lands_on_a_live_ancestor(store):
    """Regression: accepting a MOVE whose destination was retired in the meantime must
    NOT strand the node under an invisible ancestor (live+bound but unreachable from any
    root — the same orphan hazard the cycle guard covers)."""
    apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Parent"), store, source="u", applied=True)
    parent = next(f for f in store.list_features() if f.title == "Parent")
    apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Child"), store, source="u", applied=True)
    child = next(f for f in store.list_features() if f.title == "Child")

    # Retire the destination, THEN move Child under it.
    apply_op(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=parent.id), store, source="u", applied=True)
    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=child.id, parent_id=parent.id),
             store, source="u", applied=True)

    moved = store.get_feature(child.id)
    assert moved.parent_id != parent.id            # not buried under the retired node
    assert child.id in _render_reachable_ids(store)  # still visible in the tree


def test_add_under_retired_parent_is_rehomed_visible(store):
    """A stale ADD (proposal accepted / MCP plan_add after its parent was retired) is
    re-homed to a live ancestor instead of vanishing under the retired one."""
    apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Theme"), store, source="u", applied=True)
    theme = next(f for f in store.list_features() if f.title == "Theme")
    apply_op(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=theme.id), store, source="u", applied=True)

    apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="New leaf", parent_id=theme.id),
             store, source="u", applied=True)
    leaf = next(f for f in store.list_features() if f.title == "New leaf")
    assert leaf.parent_id != theme.id
    assert leaf.id in _render_reachable_ids(store)


# -- what an auto-applied amend displaced (v6) -----------------------------------
# Loop A rewrites descriptions without asking. The old wording is unrecoverable one
# instruction later, so apply_op records it at the write boundary — otherwise the IDE
# can only say "this paragraph is different from the one you remember".

def test_applied_amend_records_the_prose_it_displaced(store):
    f = Feature(title="Session lifecycle", description="Original human prose.")
    store.upsert_feature(f)
    store.set_feature_writer(f.id, "user", ACTOR_HUMAN)

    e = apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id,
                        description="Rewritten by the loop."),
                 store, source="loop_a", applied=True)

    assert e.op.prev_description == "Original human prose."
    # …and WHOSE words were displaced, read before the write reassigns authorship —
    # the IDE weights the cue by whether a person's own sentences were overwritten.
    assert e.op.prev_written_by == ACTOR_HUMAN
    assert store.get_feature(f.id).description == "Rewritten by the loop."
    # the write did reassign it, which is exactly why it had to be read first
    assert store.feature_writer_info(f.id)[1] == "loop"


def test_a_pending_amend_proposal_displaces_nothing_yet(store):
    f = Feature(title="Session lifecycle", description="Original prose.")
    store.upsert_feature(f)
    e = apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id, description="Proposed."),
                 store, source="loop_a", applied=False)
    assert e.op.prev_description is None
    assert store.get_feature(f.id).description == "Original prose."


def test_a_no_op_amend_records_nothing(store):
    """Re-asserting the same text is not a rewrite, and must not raise a cue."""
    f = Feature(title="T", description="Same prose.")
    store.upsert_feature(f)
    e = apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id, description="Same prose."),
                 store, source="loop_a", applied=True)
    assert e.op.prev_description is None
