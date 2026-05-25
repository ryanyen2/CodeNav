"""Phase 6 — bootstrap (mocked LLM, no index)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from codoc.loop.bootstrap import bootstrap_from_chunks
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import open_store


@dataclass
class Row:
    file: str
    symbol_path: str
    source: str = ""
    tokens_hash: str = "h"


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def test_bootstrap_builds_tree(store):
    rows = [
        Row("a.py", "a.py::foo", "def foo(): ...", "h1"),
        Row("a.py", "a.py::bar", "def bar(): ...", "h2"),
        Row("b.py", "b.py::baz", "def baz(): ...", "h3"),
    ]

    def propose(changes, subtree, all_titles, *, repo_name="codebase", config=None):
        # one node owning all the added chunks in the batch
        return [NodeOp(kind=NodeOpKind.ADD_NODE, title="Utilities",
                       description="misc helpers",
                       bindings=[(c["file"], c["symbol_path"]) for c in changes["added"]])]

    res = bootstrap_from_chunks(rows, store, propose=propose, max_per_call=40)

    assert res.chunks == 3 and res.batches == 1 and res.features == 1
    feat = store.list_features()[0]
    assert feat.title == "Utilities"
    assert len(store.bindings_for_feature(feat.id)) == 3
    assert store.binding_at("a.py", "a.py::foo").fingerprint == "h1"


def test_bootstrap_dedups_across_batches(store):
    # force three single-chunk batches; dedup context must prevent 3 separate nodes
    rows = [Row(f"f{i}.py", f"f{i}.py::s{i}", tokens_hash=f"h{i}") for i in range(3)]

    def propose(changes, subtree, all_titles, *, repo_name="codebase", config=None):
        c = changes["added"][0]
        existing = next((s for s in subtree if s["title"] == "Shared"), None)
        if existing is None:
            return [NodeOp(kind=NodeOpKind.ADD_NODE, title="Shared", description="one node",
                           bindings=[(c["file"], c["symbol_path"])])]
        # subsequent batches attach to the existing node instead of duplicating it
        return [NodeOp(kind=NodeOpKind.ATTACH, feature_id=existing["id"],
                       bindings=[(c["file"], c["symbol_path"])])]

    res = bootstrap_from_chunks(rows, store, propose=propose, max_per_call=1)

    assert res.batches == 3
    assert res.features == 1  # de-duplicated via all_titles/subtree context
    feat = store.list_features()[0]
    assert len(store.bindings_for_feature(feat.id)) == 3


def test_bootstrap_empty_repo(store):
    res = bootstrap_from_chunks([], store, propose=lambda *a, **k: [], max_per_call=40)
    assert res.chunks == 0 and res.features == 0 and res.batches == 0
