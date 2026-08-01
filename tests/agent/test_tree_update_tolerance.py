"""P2 — one malformed LLM op must not sink the whole tree-update response.

Before this fix, `_coerce_op` raised on a bad/absent kind or a missing key inside a
list comprehension, so a single malformed op errored the entire Loop A pass; the
state-based reconcile then re-issued every subsequent save — an unbounded retry/cost
loop. Now a bad op is dropped (logged) and the well-formed ops still apply.
"""
from __future__ import annotations

from codoc.agent import tree_update
from codoc.model.event import NodeOpKind


def test_malformed_op_dropped_good_ops_survive(monkeypatch):
    def fake_run_agent(prompt, config, *, prefix_parts=None):
        return {"ops": [
            {"kind": "amend", "feature_id": "f-1", "description": "ok"},   # good
            {"kind": "not_a_real_kind", "feature_id": "f-2"},              # bad kind
            {"feature_id": "f-3"},                                          # missing kind
            {"kind": "retire_node", "feature_id": "f-4"},                   # good
        ]}

    monkeypatch.setattr(tree_update, "run_agent", fake_run_agent)
    ops = tree_update.propose_tree_update({}, [], [], repo_name="x")

    kinds = [(o.kind, o.feature_id) for o in ops]
    assert kinds == [(NodeOpKind.AMEND, "f-1"), (NodeOpKind.RETIRE_NODE, "f-4")]


def test_all_malformed_yields_empty_not_raise(monkeypatch):
    monkeypatch.setattr(tree_update, "run_agent",
                        lambda p, c, *, prefix_parts=None: {"ops": [{"kind": "bogus"}, {}]})
    assert tree_update.propose_tree_update({}, [], [], repo_name="x") == []
