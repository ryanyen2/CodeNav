"""Tests for sync conflict detection and fresh-sidecar sync."""

from __future__ import annotations

import uuid as _uuid
from pathlib import Path

import pytest

from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.pipelines.intentional.runner import open_stores
from codoc.pipelines.intentional.amend import amend_feature
from codoc.projection.sync import sync_from_dir
from codoc.projection.tree_codoc import write_tree


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_one(codoc_dir: Path, slug: str = "alpha", intent: str = "Alpha intent.") -> str:
    store, _, _ = open_stores(str(codoc_dir))
    try:
        hlc = HLC.now(node_id="test")
        u = str(_uuid.uuid4())
        store.upsert_feature(
            Feature(
                uuid=u, slug=slug, parent_uuid=None,
                intent=intent, retired=False,
                created_at_hlc=hlc, updated_at_hlc=hlc,
            )
        )
    finally:
        store.close()
    return u


# ---------------------------------------------------------------------------
# Test 1: sync on fresh sidecar (no stale) → ok
# ---------------------------------------------------------------------------


def test_sync_fresh_sidecar_ok(tmp_path: Path) -> None:
    """Sync on a freshly rendered sidecar with no edits → status ok, nothing applied."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _seed_one(codoc_dir)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    result = sync_from_dir(str(codoc_dir))
    assert result.status == "ok"
    assert result.applied == []
    assert result.errors == []


def test_sync_fresh_sidecar_with_amend(tmp_path: Path) -> None:
    """Sync after editing intent in buffer → AmendOp applied cleanly, status ok."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    u = _seed_one(codoc_dir)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # User edits intent in buffer.
    f = codoc_dir / "tree" / "_index.codoc"
    text = f.read_text()
    text = text.replace("Alpha intent.", "New alpha intent, updated by user.")
    f.write_text(text)

    result = sync_from_dir(str(codoc_dir))
    assert result.status == "ok", f"errors: {result.errors}"
    assert any("AMEND" in line for line in result.applied)
    assert result.errors == []

    # Verify in store.
    store, _, _ = open_stores(str(codoc_dir))
    try:
        feat = store.get_feature(u)
    finally:
        store.close()
    assert "updated by user" in feat.intent


# ---------------------------------------------------------------------------
# Test 2: server amended feature AFTER last render base_hlc → ConflictOp in errors
# ---------------------------------------------------------------------------


def test_conflict_when_server_amends_after_render(tmp_path: Path) -> None:
    """If server amends a feature after the last render, and user also amends
    the same feature in the buffer, sync detects the conflict and annotates it.

    Precondition: the store must have at least one committed (non-proposal) transaction
    before the render so that base_hlc is non-empty and the stale check can fire.
    """
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    u = _seed_one(codoc_dir)

    # Perform an initial amend to seed a committed transaction in the log
    # so base_hlc is non-empty after the first render.
    store, jsonl0, tx_log0 = open_stores(str(codoc_dir))
    try:
        amend_feature(
            feature_uuid=u,
            new_intent="Alpha intent.",  # same as original — just establishes a tx
            store=store,
            tx_log=tx_log0,
            jsonl_log=jsonl0,
            author="user",
        )
    finally:
        store.close()

    # Render now; base_hlc will reflect this committed transaction.
    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # User edits the intent in their buffer.
    f = codoc_dir / "tree" / "_index.codoc"
    text = f.read_text()
    text = text.replace("Alpha intent.", "User-side edit of intent.")
    f.write_text(text)

    # Server-side amend happens AFTER the render (base_hlc < current head after amend).
    store, jsonl, tx_log2 = open_stores(str(codoc_dir))
    try:
        amend_feature(
            feature_uuid=u,
            new_intent="Server-side amendment.",
            store=store,
            tx_log=tx_log2,
            jsonl_log=jsonl,
            author="server",
        )
    finally:
        store.close()

    # Now sync: store head has advanced past base_hlc; same feature was amended on both sides.
    result = sync_from_dir(str(codoc_dir))

    # The conflict should be detected and reported in errors.
    conflict_errors = [e for e in result.errors if e.kind == "conflict"]
    assert len(conflict_errors) == 1, (
        f"Expected 1 conflict error, got {len(conflict_errors)}. "
        f"All errors: {result.errors}, applied: {result.applied}"
    )

    # User's edit should still be applied (user wins policy — no silent discard).
    assert any("AMEND" in a for a in result.applied), (
        f"Expected user amend to be applied; applied={result.applied}"
    )

    # The buffer should contain the user's edited intent, not the server's.
    index_content = (codoc_dir / "tree" / "_index.codoc").read_text()
    assert "user-side edit" in index_content.lower(), (
        f"Expected user's edit in buffer; content={index_content!r}"
    )
