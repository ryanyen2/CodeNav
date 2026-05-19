"""Test diff classification: slug/intent/parent/retire/proposal/error cases."""

from __future__ import annotations

import uuid as _uuid
from pathlib import Path

import pytest

from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.model.transaction import Transaction, TransactionKind
from codoc.pipelines.intentional.runner import open_stores
from codoc.projection.differ import (
    AcceptOp,
    AmendOp,
    DiffError,
    RejectOp,
    RenameOp,
    RestructureOp,
    RetireOp,
    diff_tree,
)
from codoc.projection.parser import parse_tree_dir
from codoc.projection.tree_codoc import write_tree


def _seed(codoc_dir: Path, *, n_features: int = 1) -> list[str]:
    store, _, _ = open_stores(str(codoc_dir))
    uuids: list[str] = []
    try:
        hlc = HLC.now(node_id="test")
        for i in range(n_features):
            u = str(_uuid.uuid4())
            store.upsert_feature(
                Feature(
                    uuid=u,
                    slug=f"feature-{i}",
                    parent_uuid=None,
                    intent=f"Intent for feature {i}.",
                    retired=False,
                    created_at_hlc=hlc,
                    updated_at_hlc=hlc,
                )
            )
            uuids.append(u)
    finally:
        store.close()
    return uuids


def test_slug_change_emits_rename_op(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    [u] = _seed(codoc_dir, n_features=1)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # User edits the slug in feature-0.codoc
    f = codoc_dir / "tree" / "feature-0.codoc"
    text = f.read_text().replace("- feature-0", "- renamed-feature")
    # Header reference also references feature-0; only edit the feature line.
    f.write_text(text)

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    assert errors == []
    assert len(ops) == 1
    assert isinstance(ops[0], RenameOp)
    assert ops[0].uuid == u
    assert ops[0].new_slug == "renamed-feature"


def test_intent_change_emits_amend_op(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    [u] = _seed(codoc_dir, n_features=1)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    f = codoc_dir / "tree" / "feature-0.codoc"
    text = f.read_text().replace(
        "Intent for feature 0.",
        "Updated intent prose, much more descriptive.",
    )
    f.write_text(text)

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    assert errors == []
    assert len(ops) == 1
    assert isinstance(ops[0], AmendOp)
    assert ops[0].uuid == u
    assert "Updated intent" in ops[0].new_intent


def test_line_deleted_emits_retire(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    uuids = _seed(codoc_dir, n_features=2)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # Delete feature-1.codoc entirely (UUID disappears from the buffer).
    (codoc_dir / "tree" / "feature-1.codoc").unlink()

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    retire_ops = [o for o in ops if isinstance(o, RetireOp)]
    assert len(retire_ops) == 1
    assert retire_ops[0].uuid == uuids[1]


def test_parent_change_emits_restructure(tmp_path: Path) -> None:
    """Move feature-1 under feature-0."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    uuids = _seed(codoc_dir, n_features=2)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # Move feature-1 into feature-0's subtree by editing both files.
    f0 = codoc_dir / "tree" / "feature-0.codoc"
    f1 = codoc_dir / "tree" / "feature-1.codoc"
    f1_text = f1.read_text()
    # Find feature-1 line content (with its UUID) and its intent
    f1_uuid = uuids[1]

    # Construct the indented child block in f0.
    indented = []
    for line in f1_text.splitlines():
        if line.startswith("# codoc subtree"):
            continue
        if not line.strip():
            continue
        indented.append("  " + line)

    new_f0 = f0.read_text() + "\n" + "\n".join(indented) + "\n"
    f0.write_text(new_f0)
    f1.unlink()

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()
    restructure_ops = [o for o in ops if isinstance(o, RestructureOp)]
    assert len(restructure_ops) == 1
    assert restructure_ops[0].uuid == uuids[1]
    assert restructure_ops[0].new_parent_uuid == uuids[0]


def test_proposal_deletion_emits_accept(tmp_path: Path) -> None:
    """Insert a proposal, render, delete its line, expect AcceptOp."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    [u] = _seed(codoc_dir, n_features=1)

    # Insert a pending REATTRIBUTE proposal targeting our feature.
    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        proposal_hlc = HLC.now(node_id="test")
        tx = Transaction(
            hlc=proposal_hlc,
            parent_hlcs=[],
            kind=TransactionKind.REATTRIBUTE,
            payload={
                "feature_uuid": u,
                "binding_uuid": "fake-binding",
                "new_feature_uuid": u,
                "symbol_path": "src/foo.py::Foo",
                "rationale": "Sample reattribution proposal.",
            },
            author="reflective",
            proposal=True,
        )
        store.write_transaction(tx)
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # Delete the proposal line.
    f = codoc_dir / "tree" / "feature-0.codoc"
    new_lines = [
        line for line in f.read_text().splitlines()
        if "?reattribute" not in line.lower() and proposal_hlc.to_str() not in line
    ]
    # Also strip the proposal body line(s) — keep things simple by removing
    # every non-feature line indented under a deleted proposal. For this
    # test, since the proposal block is at the bottom, just truncate at
    # the first "? reattribute".
    text = f.read_text()
    idx = text.find("? reattribute")
    if idx != -1:
        text = text[:idx].rstrip() + "\n"
    f.write_text(text)

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    accept_ops = [o for o in ops if isinstance(o, AcceptOp)]
    assert len(accept_ops) == 1
    assert accept_ops[0].hlc == proposal_hlc.to_str()


def test_proposal_reject_marker_emits_reject(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    [u] = _seed(codoc_dir, n_features=1)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        proposal_hlc = HLC.now(node_id="test")
        tx = Transaction(
            hlc=proposal_hlc,
            parent_hlcs=[],
            kind=TransactionKind.REATTRIBUTE,
            payload={"feature_uuid": u, "binding_uuid": "x"},
            author="reflective",
            proposal=True,
        )
        store.write_transaction(tx)
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # Replace `? reattribute:` with `! reattribute:` to signal reject.
    f = codoc_dir / "tree" / "feature-0.codoc"
    f.write_text(f.read_text().replace("? reattribute:", "! reattribute:"))

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()
    reject_ops = [o for o in ops if isinstance(o, RejectOp)]
    assert len(reject_ops) == 1
    assert reject_ops[0].hlc == proposal_hlc.to_str()


def test_new_feature_without_uuid_is_error(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _seed(codoc_dir, n_features=1)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # Append a new feature line with NO UUID comment — illegal.
    f = codoc_dir / "tree" / "feature-0.codoc"
    f.write_text(f.read_text() + "\n  - new-handcrafted-feature  [Drafting]\n")

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    assert any(e.kind == "new_feature_not_allowed" for e in errors)


def test_unknown_uuid_is_error(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _seed(codoc_dir, n_features=1)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # Insert a feature line with a fabricated UUID.
    f = codoc_dir / "tree" / "feature-0.codoc"
    fake_uuid = "00000000-0000-0000-0000-deadbeefdead"
    f.write_text(
        f.read_text() + f"\n- ghost  [Drafting]  # @{fake_uuid}\n  Ghost intent.\n"
    )

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    assert any(e.kind == "unknown_uuid" for e in errors)


def test_rename_ops_ordered_deepest_first(tmp_path: Path) -> None:
    """When renaming both parent and child, child should come first."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()

    parent_u = str(_uuid.uuid4())
    child_u = str(_uuid.uuid4())
    store, _, _ = open_stores(str(codoc_dir))
    try:
        hlc = HLC.now(node_id="test")
        store.upsert_feature(
            Feature(
                uuid=parent_u,
                slug="parent-slug",
                parent_uuid=None,
                intent="Parent.",
                retired=False,
                created_at_hlc=hlc,
                updated_at_hlc=hlc,
            )
        )
        store.upsert_feature(
            Feature(
                uuid=child_u,
                slug="child-slug",
                parent_uuid=parent_u,
                intent="Child.",
                retired=False,
                created_at_hlc=hlc,
                updated_at_hlc=hlc,
            )
        )
    finally:
        store.close()

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    f = codoc_dir / "tree" / "parent-slug.codoc"
    text = f.read_text()
    text = text.replace("- parent-slug", "- new-parent-slug")
    text = text.replace("- child-slug", "- new-child-slug")
    f.write_text(text)

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    rename_ops = [o for o in ops if isinstance(o, RenameOp)]
    assert len(rename_ops) == 2
    # Deepest first: child rename before parent rename.
    assert rename_ops[0].uuid == child_u
    assert rename_ops[1].uuid == parent_u
