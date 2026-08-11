"""Phase 3 — the /codoc:plan realization lifecycle.

plan_add → accept → unrealized placeholder → code binds → realized; and work the
agent did that wasn't planned surfaces as a new proposal.
"""
from __future__ import annotations

import pytest

from codoc.loop.apply import apply_op
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.loop_a import apply_changeset
from codoc.mcp import tools
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    return str(cd)


def _propose(ops):
    def p(changes, subtree, all_titles, *, repo_name="codebase", config=None, **_kw):
        return list(ops)
    return p


def test_plan_node_unrealized_until_code_binds(codoc_dir):
    # 1. Propose a plan placeholder.
    res = tools.plan_add(codoc_dir, title="Dark mode", description="UI theme toggle.")
    assert res["applied"] is False
    s = open_store(codoc_dir)
    try:
        ev = s.pending_events()[0]
        assert ev.source == "plan" and ev.op.realized is False

        # 2. Accept (Loop B inbox path applies the op).
        apply_op(ev.op, s, source="user", applied=True)
        s.delete_event(ev.id)
        node = next(f for f in s.list_features() if f.title == "Dark mode")
        assert node.realized is False  # live, but a placeholder

        # 3. Code binds → realization transition.
        apply_op(NodeOp(kind=NodeOpKind.ATTACH, feature_id=node.id,
                        bindings=[("ui/theme.py", "ui/theme.py::toggle")]),
                 s, source="loop_a_agent", applied=True)
        assert s.get_feature(node.id).realized is True
    finally:
        s.close()


def test_unplanned_work_surfaces_while_planned_node_is_not_reproposed(codoc_dir):
    # A realized plan node already owns its planned chunk.
    s = open_store(codoc_dir)
    try:
        apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Dark mode",
                        description="theme", realized=False,
                        bindings=[("ui/theme.py", "ui/theme.py::toggle")]),
                 s, source="user", applied=True)
        node = next(f for f in s.list_features() if f.title == "Dark mode")

        # Reflect: the planned chunk (already bound) + an UNPLANNED helper chunk.
        cs = ChangeSet(added=[
            ChunkRef("ui/theme.py", "ui/theme.py::toggle", "h1", "def toggle(): ..."),
            ChunkRef("ui/theme.py", "ui/theme.py::_persist", "h2", "def _persist(): ..."),
        ])
        # LLM places nothing → the coverage net must still surface the unplanned one.
        res = apply_changeset(cs, s, propose=_propose([]))

        # The planned, already-bound chunk is NOT re-proposed.
        assert all("toggle" not in (op.title or "") for op in res.proposed)
        # The unplanned helper surfaces as a proposal.
        assert any(op.kind is NodeOpKind.ADD_NODE for op in res.proposed)
        assert s.binding_at("ui/theme.py", "ui/theme.py::toggle").feature_id == node.id
    finally:
        s.close()
