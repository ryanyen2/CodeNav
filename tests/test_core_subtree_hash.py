"""Tests for the per-feature Merkle-style subtree hash."""

from __future__ import annotations

from codoc.core.subtree_hash import feature_canonical_hash, subtree_hash


def test_canonical_hash_is_deterministic(make_feature) -> None:
    feat = make_feature(slug="alpha", intent="An alpha feature.")
    assert feature_canonical_hash(feat) == feature_canonical_hash(feat)


def test_canonical_hash_changes_with_slug(make_feature) -> None:
    f1 = make_feature(slug="alpha", intent="X", uuid="u1")
    f2 = make_feature(slug="beta", intent="X", uuid="u1")
    assert feature_canonical_hash(f1) != feature_canonical_hash(f2)


def test_canonical_hash_changes_with_intent(make_feature) -> None:
    f1 = make_feature(slug="alpha", intent="One", uuid="u1")
    f2 = make_feature(slug="alpha", intent="Two", uuid="u1")
    assert feature_canonical_hash(f1) != feature_canonical_hash(f2)


def test_subtree_hash_changes_when_child_changes(make_feature) -> None:
    parent = make_feature(slug="parent", intent="P", uuid="parent-uuid")
    child_a = make_feature(slug="child", intent="X", uuid="child-uuid")
    child_b = make_feature(slug="child", intent="Y", uuid="child-uuid")  # intent differs

    h_a = subtree_hash(parent, [feature_canonical_hash(child_a)])
    h_b = subtree_hash(parent, [feature_canonical_hash(child_b)])
    assert h_a != h_b


def test_subtree_hash_independent_of_child_order(make_feature) -> None:
    parent = make_feature(slug="parent", intent="P", uuid="parent-uuid")
    c1 = feature_canonical_hash(make_feature(slug="a", intent="A", uuid="a"))
    c2 = feature_canonical_hash(make_feature(slug="b", intent="B", uuid="b"))

    h_in_order = subtree_hash(parent, [c1, c2])
    h_reversed = subtree_hash(parent, [c2, c1])
    assert h_in_order == h_reversed


def test_subtree_hash_identical_trees_match(make_feature) -> None:
    parent_a = make_feature(slug="parent", intent="P", uuid="parent-uuid")
    parent_b = make_feature(slug="parent", intent="P", uuid="parent-uuid")
    child_a = feature_canonical_hash(make_feature(slug="c", intent="C", uuid="c"))
    child_b = feature_canonical_hash(make_feature(slug="c", intent="C", uuid="c"))
    assert subtree_hash(parent_a, [child_a]) == subtree_hash(parent_b, [child_b])


def test_subtree_hash_empty_children(make_feature) -> None:
    feat = make_feature(slug="leaf", intent="L", uuid="leaf-u")
    h_empty = subtree_hash(feat, [])
    h_again = subtree_hash(feat, [])
    assert h_empty == h_again
    # Length is 64 (sha256 hex)
    assert len(h_empty) == 64


def test_retired_flag_changes_hash(make_feature) -> None:
    f1 = make_feature(slug="x", intent="X", uuid="u", retired=False)
    f2 = make_feature(slug="x", intent="X", uuid="u", retired=True)
    assert feature_canonical_hash(f1) != feature_canonical_hash(f2)
