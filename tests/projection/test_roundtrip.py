"""Round-trip: render → parse → diff produces zero ops."""

from __future__ import annotations

from pathlib import Path

import pytest

from codoc.pipelines.intentional.runner import IntentionalRunner, open_stores
from codoc.projection.differ import diff_tree
from codoc.projection.parser import parse_tree_dir
from codoc.projection.tree_codoc import write_tree


def _seed_features(codoc_dir: Path) -> dict[str, str]:
    """Seed a small tree directly through the SQLiteStore (bypasses runner since
    we need both root and child UUIDs known up-front)."""
    import uuid as _uuid

    from codoc.model.feature import Feature
    from codoc.model.hlc import HLC

    store, jsonl_log, tx_log = open_stores(str(codoc_dir))
    try:
        hlc = HLC.now(node_id="test")
        root_uuid = str(_uuid.uuid4())
        child_uuid = str(_uuid.uuid4())
        store.upsert_feature(
            Feature(
                uuid=root_uuid,
                slug="auth-flow",
                parent_uuid=None,
                intent="Login, logout, session refresh.",
                retired=False,
                created_at_hlc=hlc,
                updated_at_hlc=hlc,
            )
        )
        store.upsert_feature(
            Feature(
                uuid=child_uuid,
                slug="token-rotation",
                parent_uuid=root_uuid,
                intent="Refreshes JWTs every 15 minutes.",
                retired=False,
                created_at_hlc=hlc,
                updated_at_hlc=hlc,
            )
        )
    finally:
        store.close()
    return {"root": root_uuid, "child": child_uuid}


def test_render_parse_diff_roundtrip(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _seed_features(codoc_dir)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    assert (codoc_dir / "tree" / "_index.codoc").exists()

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    assert len(parsed.features) == 2
    assert {pf.slug for pf in parsed.features} == {"auth-flow", "token-rotation"}

    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()
    assert ops == [], f"Expected zero ops, got: {ops}"
    assert errors == [], f"Expected zero errors, got: {errors}"


def test_render_empty_tree(tmp_path: Path) -> None:
    """An empty store should still produce a valid _index.codoc."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    index_path = codoc_dir / "tree" / "_index.codoc"
    assert index_path.exists()
    text = index_path.read_text()
    assert text.strip()  # non-empty file produced


def test_render_parse_diff_with_no_intent(tmp_path: Path) -> None:
    """Feature with empty intent should round-trip."""
    import uuid as _uuid

    from codoc.model.feature import Feature
    from codoc.model.hlc import HLC

    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        hlc = HLC.now(node_id="test")
        store.upsert_feature(
            Feature(
                uuid=str(_uuid.uuid4()),
                slug="empty-feature",
                parent_uuid=None,
                intent="",
                retired=False,
                created_at_hlc=hlc,
                updated_at_hlc=hlc,
            )
        )
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()
    assert ops == []
    assert errors == []
