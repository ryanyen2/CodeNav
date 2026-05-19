"""Test multi-file specifics: top-level rename, cross-file restructure."""

from __future__ import annotations

import uuid as _uuid
from pathlib import Path

import pytest

from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.pipelines.intentional.runner import open_stores
from codoc.projection.differ import RenameOp, RestructureOp, diff_tree
from codoc.projection.parser import parse_tree_dir
from codoc.projection.sync import sync_from_dir
from codoc.projection.tree_codoc import write_tree


def test_top_level_rename_renames_file(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()

    u = str(_uuid.uuid4())
    store, _, _ = open_stores(str(codoc_dir))
    try:
        hlc = HLC.now(node_id="test")
        store.upsert_feature(
            Feature(
                uuid=u, slug="auth-flow", parent_uuid=None,
                intent="Authentication.", retired=False,
                created_at_hlc=hlc, updated_at_hlc=hlc,
            )
        )
    finally:
        store.close()

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    assert (codoc_dir / "tree" / "auth-flow.codoc").exists()

    # User renames root feature.
    f = codoc_dir / "tree" / "auth-flow.codoc"
    text = f.read_text().replace("- auth-flow", "- auth-system")
    f.write_text(text)

    result = sync_from_dir(str(codoc_dir))
    assert result.status == "ok", result.errors
    assert any("auth-system" in line for line in result.applied)

    # File should be renamed (old gone, new present).
    assert not (codoc_dir / "tree" / "auth-flow.codoc").exists()
    assert (codoc_dir / "tree" / "auth-system.codoc").exists()

    # _index.codoc should reference the new slug.
    index_text = (codoc_dir / "tree" / "_index.codoc").read_text()
    assert "auth-system" in index_text
    assert "auth-flow" not in index_text


def test_cross_file_restructure(tmp_path: Path) -> None:
    """Move a top-level feature into another top-level subtree."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()

    a, b = str(_uuid.uuid4()), str(_uuid.uuid4())
    store, _, _ = open_stores(str(codoc_dir))
    try:
        hlc = HLC.now(node_id="test")
        store.upsert_feature(
            Feature(
                uuid=a, slug="alpha", parent_uuid=None, intent="Alpha.",
                retired=False, created_at_hlc=hlc, updated_at_hlc=hlc,
            )
        )
        store.upsert_feature(
            Feature(
                uuid=b, slug="bravo", parent_uuid=None, intent="Bravo.",
                retired=False, created_at_hlc=hlc, updated_at_hlc=hlc,
            )
        )
    finally:
        store.close()

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # Move bravo into alpha's subtree by:
    # 1) Append bravo's content (indented) under alpha
    # 2) Delete bravo.codoc
    f_alpha = codoc_dir / "tree" / "alpha.codoc"
    f_bravo = codoc_dir / "tree" / "bravo.codoc"
    bravo_text = f_bravo.read_text()
    indented = []
    for line in bravo_text.splitlines():
        if line.startswith("# codoc subtree") or not line.strip():
            continue
        indented.append("  " + line)
    f_alpha.write_text(f_alpha.read_text() + "\n" + "\n".join(indented) + "\n")
    f_bravo.unlink()

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    restructure_ops = [o for o in ops if isinstance(o, RestructureOp)]
    assert len(restructure_ops) == 1
    assert restructure_ops[0].uuid == b
    assert restructure_ops[0].new_parent_uuid == a
