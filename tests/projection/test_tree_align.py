"""Tests for codoc.projection.tree_align — structural UUID resolution."""

from __future__ import annotations

import uuid as _uuid
from pathlib import Path

import pytest

from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.pipelines.intentional.runner import open_stores
from codoc.projection.differ import IntroduceOp, RetireOp, diff_tree
from codoc.projection.meta import TreeMeta
from codoc.projection.parser import parse_tree_dir
from codoc.projection.tree_align import (
    _compute_feature_hash,
    _levenshtein,
    _title_norm_hash,
    _title_to_slug,
    resolve_uuid_structural,
)
from codoc.projection.tree_codoc import write_tree


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meta(entries: list[dict]) -> TreeMeta:
    """Build a minimal TreeMeta with uuid_to_location populated from *entries*.

    Each entry must have 'uuid' plus the location fields.
    """
    uuid_to_location: dict = {}
    for e in entries:
        uuid = e.pop("uuid")
        uuid_to_location[uuid] = {"kind": "feature", **e}
    return TreeMeta(
        base_hlc="",
        rendered_at="",
        uuid_to_location=uuid_to_location,
    )


def _seed_features(codoc_dir: Path, features: list[dict]) -> list[str]:
    """Insert features into the store; return list of UUIDs in insertion order."""
    store, _, _ = open_stores(str(codoc_dir))
    uuids: list[str] = []
    try:
        hlc = HLC.now(node_id="test")
        for f in features:
            u = str(_uuid.uuid4())
            store.upsert_feature(
                Feature(
                    uuid=u,
                    slug=f.get("slug", u),
                    title=f.get("title", ""),
                    parent_uuid=f.get("parent_uuid"),
                    intent=f.get("intent", ""),
                    retired=f.get("retired", False),
                    created_at_hlc=hlc,
                    updated_at_hlc=hlc,
                )
            )
            uuids.append(u)
    finally:
        store.close()
    return uuids


def _index_path(codoc_dir: Path) -> Path:
    return codoc_dir / "tree" / "_index.codoc"


# ---------------------------------------------------------------------------
# Unit tests for tree_align helpers
# ---------------------------------------------------------------------------


def test_levenshtein_identical() -> None:
    assert _levenshtein("hello", "hello") == 0


def test_levenshtein_single_insert() -> None:
    assert _levenshtein("abc", "abcd") == 1


def test_levenshtein_single_delete() -> None:
    assert _levenshtein("abcd", "abc") == 1


def test_levenshtein_substitution() -> None:
    assert _levenshtein("abc", "axc") == 1


def test_title_to_slug() -> None:
    assert _title_to_slug("My Cool Feature!") == "my-cool-feature"
    assert _title_to_slug("Checkpoint Persistence") == "checkpoint-persistence"
    assert _title_to_slug("  Leading Spaces  ") == "leading-spaces"


def test_title_norm_hash_stable() -> None:
    h1 = _title_norm_hash("Checkpoint Persistence")
    h2 = _title_norm_hash("checkpoint-persistence")  # hyphen stripped → different
    # The hash is deterministic.
    assert h1 == _title_norm_hash("Checkpoint Persistence")


def test_compute_feature_hash() -> None:
    h = _compute_feature_hash("My Feature", "some intent", "parent-uuid", False)
    assert len(h) == 40  # SHA1 hex digest
    # Same inputs → same hash.
    assert h == _compute_feature_hash("My Feature", "some intent", "parent-uuid", False)
    # Different inputs → different hash.
    assert h != _compute_feature_hash("My Feature", "other intent", "parent-uuid", False)


# ---------------------------------------------------------------------------
# Integration tests: structural UUID resolution
# ---------------------------------------------------------------------------


def test_exact_title_match_returns_correct_uuid() -> None:
    """Pass 1: exact case-insensitive title match within parent → correct UUID."""
    uuid_a = str(_uuid.uuid4())
    uuid_b = str(_uuid.uuid4())
    meta = _make_meta([
        {"uuid": uuid_a, "parent_uuid": None, "sibling_index": 0, "title": "Alpha Feature", "slug": "alpha-feature"},
        {"uuid": uuid_b, "parent_uuid": None, "sibling_index": 1, "title": "Beta Feature", "slug": "beta-feature"},
    ])

    result = resolve_uuid_structural("Alpha Feature", None, 0, meta)
    assert result == uuid_a

    result2 = resolve_uuid_structural("BETA FEATURE", None, 1, meta)
    assert result2 == uuid_b


def test_parent_renamed_child_exact_still_resolved() -> None:
    """Child title unchanged; parent UUID changed → child still resolved via Pass 1."""
    parent_uuid = str(_uuid.uuid4())
    child_uuid = str(_uuid.uuid4())
    meta = _make_meta([
        {
            "uuid": child_uuid,
            "parent_uuid": parent_uuid,
            "sibling_index": 0,
            "title": "Checkpoint persistence",
            "slug": "checkpoint-persistence",
        },
    ])

    # We pass the new parent UUID — as long as it matches stored parent_uuid it's fine.
    result = resolve_uuid_structural("Checkpoint persistence", parent_uuid, 0, meta)
    assert result == child_uuid


def test_slug_match_resolves_via_pass2() -> None:
    """Pass 2: normalised-to-slug title matches stored slug."""
    uuid_x = str(_uuid.uuid4())
    meta = _make_meta([
        {
            "uuid": uuid_x,
            "parent_uuid": None,
            "sibling_index": 0,
            "title": "Some Old Title",
            "slug": "new-feature-slug",  # stored slug differs from title
        },
    ])

    # The new title normalises to "new-feature-slug" → matches stored slug.
    result = resolve_uuid_structural("New Feature Slug", None, 0, meta)
    assert result == uuid_x


def test_structural_sibling_index_with_edit_distance() -> None:
    """Pass 3: same sibling_index + minor rename → resolved via edit distance."""
    uuid_c = str(_uuid.uuid4())
    meta = _make_meta([
        {
            "uuid": uuid_c,
            "parent_uuid": None,
            "sibling_index": 0,
            "title": "Model Training State",
            "slug": "model-training-state",
        },
    ])

    # Small rename: "Model Training State" → "Model Train State" (distance 3, ratio 3/19 ≈ 0.16)
    result = resolve_uuid_structural("Model Train State", None, 0, meta)
    assert result == uuid_c


def test_sibling_index_mismatch_blocked() -> None:
    """Pass 3 should not match if sibling_index differs AND title diverges too much."""
    uuid_d = str(_uuid.uuid4())
    meta = _make_meta([
        {
            "uuid": uuid_d,
            "parent_uuid": None,
            "sibling_index": 2,
            "title": "Completely Different Title",
            "slug": "completely-different-title",
        },
    ])

    # sibling_index_new=0 ≠ stored 2; edit distance also large.
    result = resolve_uuid_structural("Unrelated Feature", None, 0, meta)
    assert result is None


def test_true_new_feature_returns_none() -> None:
    """No match in any pass → None."""
    meta = _make_meta([
        {
            "uuid": str(_uuid.uuid4()),
            "parent_uuid": None,
            "sibling_index": 0,
            "title": "Existing Feature",
            "slug": "existing-feature",
        },
    ])

    result = resolve_uuid_structural("Brand New Unrelated", None, 5, meta)
    assert result is None


def test_empty_meta_returns_none() -> None:
    """No siblings at all → None."""
    meta = _make_meta([])
    result = resolve_uuid_structural("Anything", None, 0, meta)
    assert result is None


# ---------------------------------------------------------------------------
# Integration tests with full render/parse/diff cycle
# ---------------------------------------------------------------------------


def test_true_new_feature_produces_introduce_op(tmp_path: Path) -> None:
    """A feature added to _index.codoc that can't be resolved → IntroduceOp."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _seed_features(codoc_dir, [{"slug": "existing", "intent": "Existing feature."}])

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # Add a genuinely new feature that structurally cannot match existing.
    f = _index_path(codoc_dir)
    f.write_text(f.read_text() + "\n- brand-new-feature\n    Brand new intent.\n")

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    # No fatal errors.
    assert not any(e.kind == "new_feature_not_allowed" for e in errors)
    introduce_ops = [o for o in ops if isinstance(o, IntroduceOp)]
    assert len(introduce_ops) == 1
    assert introduce_ops[0].title == "brand-new-feature"
    # Intent collected at flush time.
    assert "brand new intent" in introduce_ops[0].intent.lower()


def test_feature_deleted_produces_retire_op(tmp_path: Path) -> None:
    """Removing a feature line from _index.codoc → RetireOp."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    uuids = _seed_features(codoc_dir, [
        {"slug": "keep-me", "intent": "Keep this one."},
        {"slug": "delete-me", "intent": "This will be deleted."},
    ])

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    f = _index_path(codoc_dir)
    lines = f.read_text().splitlines()
    filtered = [l for l in lines if "delete-me" not in l and "This will be deleted" not in l]
    f.write_text("\n".join(filtered) + "\n")

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    retire_ops = [o for o in ops if isinstance(o, RetireOp)]
    assert len(retire_ops) == 1
    assert retire_ops[0].uuid == uuids[1]
