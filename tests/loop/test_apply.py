"""apply_op realization transition: a plan placeholder becomes realized on bind."""
from __future__ import annotations

import pytest

from codoc.loop.apply import apply_op
from codoc.model.event import NodeOp, NodeOpKind
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
