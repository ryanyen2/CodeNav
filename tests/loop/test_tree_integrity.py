"""#7 — cycle / orphan guards keep the feature tree reachable from the roots.

A feature orphaned by a dangling ``parent_id`` (its parent was retired, or the id
never existed) is promoted to a root by ``tree_order.preorder`` — the walk shared
by ``render_tree`` and the doc projection (``codoc/codoc_file/tree_order.py``) —
so it stays visible rather than silently vanishing from one or both surfaces. A
pure CYCLE among live features (crash / pre-guard debris, unreachable from any
root either way) is a narrower case: it stays invisible to ``render_tree`` until
healed. Either way the STORE ROW is still wrong (a stale ``parent_id``), so this
is a display-time fallback, not a fix — ``heal_tree_integrity`` is the durable
backstop that re-homes the row itself. These tests pin the three guards:

  * apply_op MOVE_NODE rejects a cycle-forming move (no-op, tree unchanged);
  * apply_op RETIRE_NODE re-parents live children to the grandparent;
  * heal_tree_integrity re-homes any orphaned/cyclic subtree (the recovery backstop).
"""
from __future__ import annotations

import pytest

from codoc.codoc_file.render import render_tree
from codoc.loop.apply import apply_op
from codoc.loop.loop_a import heal_tree_integrity
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _tree(store, *edges):
    """Seed features from (id, parent_id) pairs; titles derive from id."""
    for fid, parent in edges:
        store.upsert_feature(Feature(id=fid, title=fid.upper(), parent_id=parent))


# ── MOVE cycle rejection ─────────────────────────────────────────────────────

def test_move_under_own_descendant_is_rejected(store):
    _tree(store, ("a", None), ("b", "a"), ("c", "b"))
    # Moving A under C (its own grandchild) would form A→…→C→A. Must be a no-op.
    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="a", parent_id="c"),
             store, source="user", applied=True)
    assert store.get_feature("a").parent_id is None  # unchanged

    # Every live feature is still reachable from a root (render walks from None).
    txt = render_tree(store)
    assert "A" in txt and "B" in txt and "C" in txt


def test_move_under_self_is_rejected(store):
    _tree(store, ("a", None), ("b", "a"))
    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="a", parent_id="a"),
             store, source="user", applied=True)
    assert store.get_feature("a").parent_id is None


def test_legal_move_still_applies(store):
    _tree(store, ("a", None), ("b", None), ("c", "a"))
    # Moving C from A to B is not a cycle → applies.
    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="c", parent_id="b"),
             store, source="user", applied=True)
    assert store.get_feature("c").parent_id == "b"


# ── RETIRE re-parents children ───────────────────────────────────────────────

def test_retire_reparents_children_to_grandparent(store):
    _tree(store, ("a", None), ("b", "a"), ("c", "b"))
    # Retire B (mid-tree). Its child C must be promoted to A, not orphaned under B.
    apply_op(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id="b"),
             store, source="user", applied=True)
    assert store.get_feature("b").retired
    assert store.get_feature("c").parent_id == "a"   # promoted to grandparent
    # C is reachable from the root (would be invisible if left under retired B).
    assert "C" in render_tree(store)


def test_retire_root_reparents_children_to_root(store):
    _tree(store, ("a", None), ("b", "a"))
    apply_op(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id="a"),
             store, source="user", applied=True)
    assert store.get_feature("b").parent_id is None  # child becomes a root


# ── heal_tree_integrity backstop ─────────────────────────────────────────────

def test_heal_reattaches_orphan_pointing_at_missing_parent(store):
    # A live feature whose parent id names a feature that doesn't exist.
    store.upsert_feature(Feature(id="x", title="X", parent_id="ghost"))
    # Promoted to a root at render time (tree_order), so it's already visible...
    assert "X" in render_tree(store)
    assert store.get_feature("x").parent_id == "ghost"   # ...but the STORE ROW is still dangling
    healed = heal_tree_integrity(store)
    assert healed == 1
    assert store.get_feature("x").parent_id is None      # now durably fixed, not just displayed
    assert "X" in render_tree(store)


def test_heal_breaks_a_cycle(store):
    # A pre-existing 2-cycle among live features (crash / pre-guard debris).
    store.upsert_feature(Feature(id="a", title="A", parent_id="b"))
    store.upsert_feature(Feature(id="b", title="B", parent_id="a"))
    assert "A" not in render_tree(store)          # both invisible
    healed = heal_tree_integrity(store)
    assert healed >= 1
    txt = render_tree(store)
    assert "A" in txt and "B" in txt              # both reachable again


def test_heal_is_a_noop_on_a_sound_tree(store):
    _tree(store, ("a", None), ("b", "a"), ("c", "a"))
    assert heal_tree_integrity(store) == 0
