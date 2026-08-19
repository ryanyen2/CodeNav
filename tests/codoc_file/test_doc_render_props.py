"""Property tests for the store → tree.doc.json projection.

The example tests in ``test_doc_render.py`` pin hand-picked trees. These assert
INVARIANTS over many randomly generated trees, so the two guarantees the webview
relies on hold "for all trees", not just the fixtures:

  P-preorder    — ``build_doc_from_store`` emits features in the SAME depth-first
                  pre-order the left-nav walks (``render_tree`` → ``store.children``),
                  so the doc body and the nav line up 1:1 (the scroll-spy-jump fix).
  P-parent      — every parent heading appears before all of its descendants.
  P-roundtrip   — ``parse_doc(build_doc_from_store(store))`` recovers each feature's
                  title, description, and parent_id — so the projection the daemon
                  writes parses back to the same tree (no drift across the round-trip).

Determinism: a seeded ``random.Random`` drives generation, so a failing tree is
reproducible from its seed.
"""
from __future__ import annotations

import random

import pytest

from codoc.codoc_file.doc_parse import parse_doc
from codoc.codoc_file.doc_render import build_doc_from_store
from codoc.codoc_file.parse import normalize_description
from codoc.model.feature import Feature
from codoc.store.db import open_store

# `**…**` is in the pool because bold is not decoration: it projects to a `bold` mark
# and has to serialize back to the same asterisks, or every projection of a description
# carrying a focus span reads as an edit nobody made.
_WORDS = ["auth", "theme", "sync loop", "parses the AST", "renders output", "stores rows",
          "**get this exactly right**"]


def _random_tree(store, rng: random.Random) -> list[Feature]:
    """Create a random acyclic feature tree. A node's parent is chosen from features
    created BEFORE it (or None) — so no cycle is possible, and creation order is
    deliberately NOT tree order (the whole point: a child is often created after a
    later root, the exact shape that used to desync the doc from the nav)."""
    created: list[Feature] = []
    n = rng.randint(0, 12)
    for i in range(n):
        # ~40% roots, else parented under a random earlier node.
        parent_id = None
        if created and rng.random() < 0.6:
            parent_id = rng.choice(created).id
        # Titles/descriptions are pre-normalized (no leading/trailing space, single
        # spaces) so the round-trip compares against a stable normal form.
        title = f"{rng.choice(_WORDS)} {i}"
        desc = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(0, 3))).strip()
        f = Feature(title=title, description=desc, parent_id=parent_id)
        store.upsert_feature(f)
        created.append(f)
    return created


def _reference_preorder(store) -> list[str]:
    """The nav's order: depth-first pre-order over ``store.children`` — the same walk
    ``render_tree`` uses. Returns feature ids in visitation order."""
    order: list[str] = []

    def walk(parent_id):
        for f in store.children(parent_id):
            order.append(f.id)
            walk(f.id)

    walk(None)
    return order


def _doc_fids(doc: dict) -> list[str]:
    return [b["attrs"]["fid"] for b in doc["content"] if b.get("type") == "featureHeading"]


@pytest.mark.parametrize("seed", range(60))
def test_prop_projection_matches_nav_preorder(tmp_path, seed):
    rng = random.Random(seed)
    with open_store(tmp_path) as s:
        _random_tree(s, rng)
        doc = build_doc_from_store(s)
        reference = _reference_preorder(s)
    assert _doc_fids(doc) == reference


@pytest.mark.parametrize("seed", range(60))
def test_prop_parent_precedes_descendants(tmp_path, seed):
    rng = random.Random(seed)
    with open_store(tmp_path) as s:
        features = _random_tree(s, rng)
        doc = build_doc_from_store(s)
    order = _doc_fids(doc)
    pos = {fid: i for i, fid in enumerate(order)}
    by_id = {f.id: f for f in features}
    for f in features:
        if f.parent_id and f.parent_id in by_id:
            assert pos[f.parent_id] < pos[f.id], f"parent {f.parent_id} must precede child {f.id}"


@pytest.mark.parametrize("seed", range(60))
def test_prop_roundtrip_recovers_title_description_parent(tmp_path, seed):
    rng = random.Random(seed)
    with open_store(tmp_path) as s:
        features = _random_tree(s, rng)
        doc = build_doc_from_store(s)
    parsed = parse_doc(doc)
    by_id = {f.id: f for f in features}
    parsed_by_id = {n.id: n for n in parsed.nodes}
    # Same set of features survives the round-trip.
    assert set(parsed_by_id) == set(by_id)
    for fid, node in parsed_by_id.items():
        original = by_id[fid]
        assert node.title == original.title.strip()
        assert node.description == normalize_description(original.description)
        # A live parent round-trips exactly; a parent that is absent from the live set
        # (never happens for these acyclic trees) would surface as None.
        assert node.parent_id == original.parent_id
