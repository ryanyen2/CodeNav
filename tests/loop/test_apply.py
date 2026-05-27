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
