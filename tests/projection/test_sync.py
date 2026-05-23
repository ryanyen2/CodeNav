"""Test the full sync cycle: parse → diff → apply → re-render."""

from __future__ import annotations

import uuid as _uuid
from pathlib import Path

import pytest

from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.pipelines.intentional.runner import open_stores
from codoc.projection.sync import sync_from_dir
from codoc.projection.tree_codoc import write_tree


def _seed_two(codoc_dir: Path) -> tuple[str, str]:
    store, _, _ = open_stores(str(codoc_dir))
    try:
        hlc = HLC.now(node_id="test")
        a = str(_uuid.uuid4())
        b = str(_uuid.uuid4())
        store.upsert_feature(
            Feature(
                uuid=a, slug="alpha", parent_uuid=None,
                intent="Alpha intent.", retired=False,
                created_at_hlc=hlc, updated_at_hlc=hlc,
            )
        )
        store.upsert_feature(
            Feature(
                uuid=b, slug="bravo", parent_uuid=None,
                intent="Bravo intent.", retired=False,
                created_at_hlc=hlc, updated_at_hlc=hlc,
            )
        )
    finally:
        store.close()
    return a, b


def test_sync_stale_buffer_when_no_meta(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    (codoc_dir / "tree").mkdir()

    result = sync_from_dir(str(codoc_dir))
    assert result.status == "stale_buffer"
    assert result.errors


def test_sync_applies_amend_and_rename(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    a, b = _seed_two(codoc_dir)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # Edit alpha: change slug AND intent in _index.codoc.
    # "alpha-v2" is similar enough to "alpha" (3 edits / max 8 = 0.625 > 0.6) for
    # structural UUID resolution to work without inline @uuid comments.
    f = codoc_dir / "tree" / "_index.codoc"
    text = f.read_text()
    text = text.replace("- alpha", "- alpha-v2")
    text = text.replace("Alpha intent.", "Alpha is now updated.")
    f.write_text(text)

    result = sync_from_dir(str(codoc_dir))
    assert result.status == "ok", f"errors: {result.errors}"
    assert len(result.applied) == 2
    assert any("RENAME" in line for line in result.applied)
    assert any("AMEND" in line for line in result.applied)

    # Verify in store.
    store, _, _ = open_stores(str(codoc_dir))
    try:
        feat = store.get_feature(a)
    finally:
        store.close()
    assert feat.slug == "alpha-v2"
    assert "updated" in feat.intent.lower()


def test_sync_new_feature_becomes_introduce_proposal(tmp_path: Path) -> None:
    """Adding a new unresolvable feature line creates an INTRODUCE proposal, not a parse error."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    a, b = _seed_two(codoc_dir)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # Inject a new feature line that cannot be matched.
    f = codoc_dir / "tree" / "_index.codoc"
    f.write_text(f.read_text() + "\n- handcrafted-feature-no-uuid\n    A brand new feature.\n")

    result = sync_from_dir(str(codoc_dir))
    assert result.status == "ok"
    assert any("INTRODUCE" in line for line in result.applied)

    # The existing features should be unchanged.
    store, _, _ = open_stores(str(codoc_dir))
    try:
        feat = store.get_feature(a)
        proposals = store.list_transactions(proposal=True, limit=0)
    finally:
        store.close()
    assert feat.slug == "alpha"
    assert feat.intent == "Alpha intent."
    # There should be a pending INTRODUCE proposal.
    assert any(p.kind.value == "introduce" for p in proposals)


def test_sync_no_changes_is_ok_noop(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _seed_two(codoc_dir)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    result = sync_from_dir(str(codoc_dir))
    assert result.status == "ok"
    assert result.applied == []
