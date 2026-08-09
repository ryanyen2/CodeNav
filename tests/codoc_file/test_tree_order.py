"""tree_order.py — the shared parent→children walk behind render_tree (the tree
pane) and build_doc_from_store (the doc pane). Regression coverage for the bug
this module fixes: a feature orphaned by a dangling parent_id (retired/missing
parent) used to be dropped by render_tree but promoted to root by the doc
projection — the two panes disagreed about whether the feature existed at all."""
from __future__ import annotations

from codoc.codoc_file.doc_render import build_doc_from_store
from codoc.codoc_file.render import render_tree
from codoc.codoc_file.tree_order import children_map, preorder
from codoc.model.feature import Feature
from codoc.store.db import open_store


def _f(id: str, parent_id: str | None = None) -> Feature:
    return Feature(id=id, title=id.upper(), parent_id=parent_id)


def test_children_map_promotes_dangling_parent_to_root():
    features = [_f("a"), _f("b", parent_id="a"), _f("c", parent_id="ghost")]
    cm = children_map(features)
    assert [f.id for f in cm[None]] == ["a", "c"]
    assert [f.id for f in cm["a"]] == ["b"]


def test_preorder_visits_parent_before_children():
    features = [_f("a"), _f("b", parent_id="a"), _f("c")]
    assert [f.id for f in preorder(features)] == ["a", "b", "c"]


def test_preorder_promotes_orphan_and_keeps_it():
    features = [_f("a"), _f("orphan", parent_id="missing")]
    order = [f.id for f in preorder(features)]
    assert "orphan" in order
    assert set(order) == {"a", "orphan"}


def test_render_tree_and_doc_agree_on_an_orphaned_feature(tmp_path):
    """The exact bug: a feature whose parent was retired must appear — in the
    same position — in both the tree pane and the doc pane, not just one."""
    with open_store(tmp_path) as s:
        root = Feature(title="Root")
        s.upsert_feature(root)
        child = Feature(title="Child", parent_id=root.id)
        s.upsert_feature(child)
        s.retire_feature(root.id)  # child.parent_id now dangles

        import re
        tree_fids = re.findall(r"⟨(f-[0-9a-f]+)⟩", render_tree(s))
        doc = build_doc_from_store(s)
        doc_fids = [b["attrs"]["fid"] for b in doc["content"] if b.get("type") == "featureHeading"]

    assert tree_fids == doc_fids == [child.id]
