"""Test single-document layout: top-level rename and cross-feature restructure."""

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


_INDEX = "_index.codoc"


def _index_path(codoc_dir: Path) -> Path:
    return codoc_dir / "tree" / _INDEX


def test_top_level_rename_updates_index(tmp_path: Path) -> None:
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

    assert _index_path(codoc_dir).exists()
    assert "auth-flow" in _index_path(codoc_dir).read_text()

    # User renames root feature in _index.codoc.
    # "auth-flaw" is similar enough to "auth-flow" (1 edit, sim=0.89) for
    # structural UUID resolution to work without inline @uuid comments.
    f = _index_path(codoc_dir)
    f.write_text(f.read_text().replace("- auth-flow", "- auth-flaw"))

    result = sync_from_dir(str(codoc_dir))
    assert result.status == "ok", result.errors
    assert any("auth-flaw" in line for line in result.applied)

    # _index.codoc should reference the new slug only.
    index_text = _index_path(codoc_dir).read_text()
    assert "auth-flaw" in index_text
    assert "auth-flow" not in index_text


def test_cross_feature_restructure(tmp_path: Path) -> None:
    """Move bravo under alpha by re-indenting its block in _index.codoc."""
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

    # Re-indent bravo's block under alpha by adding 2 spaces to its lines.
    f = _index_path(codoc_dir)
    lines = f.read_text().splitlines()
    new_lines = []
    in_bravo = False
    for line in lines:
        if "- bravo" in line:
            in_bravo = True
        if in_bravo and line.strip():
            new_lines.append("  " + line)
        else:
            if in_bravo and not line.strip():
                new_lines.append(line)
            else:
                new_lines.append(line)
    f.write_text("\n".join(new_lines) + "\n")

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
