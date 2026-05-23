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
    IntroduceOp,
    RejectOp,
    RenameOp,
    RestructureOp,
    RetireOp,
    diff_tree,
)
from codoc.projection.parser import parse_tree_dir
from codoc.projection.tree_codoc import write_tree


_INDEX = "_index.codoc"


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


def _index_path(codoc_dir: Path) -> Path:
    return codoc_dir / "tree" / _INDEX


def test_slug_change_emits_rename_op(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    [u] = _seed(codoc_dir, n_features=1)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # User edits the feature title in _index.codoc.
    # Use a name with Levenshtein similarity > 0.6 to "feature-0" so structural
    # alignment can resolve the UUID without an inline @uuid comment.
    f = _index_path(codoc_dir)
    text = f.read_text().replace("- feature-0", "- feature-0x")
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
    assert ops[0].new_slug == "feature-0x"


def test_intent_change_emits_amend_op(tmp_path: Path) -> None:
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    [u] = _seed(codoc_dir, n_features=1)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    f = _index_path(codoc_dir)
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

    # Delete feature-1 block from _index.codoc.
    f = _index_path(codoc_dir)
    lines = f.read_text().splitlines()
    filtered = [l for l in lines if "feature-1" not in l and "Intent for feature 1" not in l]
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


def test_parent_change_emits_restructure(tmp_path: Path) -> None:
    """Move feature-1 under feature-0 by re-indenting in _index.codoc."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    uuids = _seed(codoc_dir, n_features=2)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    f = _index_path(codoc_dir)
    lines = f.read_text().splitlines()

    # Re-indent feature-1 lines by 2 extra spaces so the parser sees it as a
    # child of feature-0.
    new_lines = []
    in_f1 = False
    for line in lines:
        if "- feature-1" in line:
            in_f1 = True
        if in_f1:
            if line.strip():
                new_lines.append("  " + line)
            else:
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
    assert restructure_ops[0].uuid == uuids[1]
    assert restructure_ops[0].new_parent_uuid == uuids[0]


def test_proposal_deletion_emits_accept(tmp_path: Path) -> None:
    """Insert a proposal, render, delete its diff-hunk lines, expect AcceptOp."""
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

    # Proposals render as col-0 diff hunks. Deleting those lines signals acceptance.
    f = _index_path(codoc_dir)
    text = f.read_text()
    lines = text.splitlines()
    truncated = []
    for line in lines:
        if line and line[0] in "+-~" and len(line) > 1 and line[1] in " -~+":
            break
        truncated.append(line)
    while truncated and not truncated[-1].strip():
        truncated.pop()
    f.write_text("\n".join(truncated) + "\n")

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

    f = _index_path(codoc_dir)
    text = f.read_text()
    lines = text.splitlines()
    clean_lines = []
    for line in lines:
        if line and line[0] in "+-~" and len(line) > 1 and line[1] in " -~+":
            break
        clean_lines.append(line)
    while clean_lines and not clean_lines[-1].strip():
        clean_lines.pop()
    hlc_display = proposal_hlc.to_str()
    clean_lines.append(f"! reattribute: x  [proposal]  # ?{hlc_display}")
    f.write_text("\n".join(clean_lines) + "\n")

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    reject_ops = [o for o in ops if isinstance(o, RejectOp)]
    assert len(reject_ops) == 1
    assert reject_ops[0].hlc == proposal_hlc.to_str()


def test_new_feature_without_uuid_becomes_introduce_op(tmp_path: Path) -> None:
    """Unresolvable feature lines are now turned into IntroduceOp (proposals), not errors."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    _seed(codoc_dir, n_features=1)

    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()

    # Append a new feature line that cannot be matched to any known feature.
    f = _index_path(codoc_dir)
    f.write_text(f.read_text() + "\n  - new-handcrafted-feature  [Drafting]\n")

    parsed = parse_tree_dir(str(codoc_dir), old_meta=meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    # No fatal error — instead we get an IntroduceOp.
    assert not any(e.kind == "new_feature_not_allowed" for e in errors)
    introduce_ops = [o for o in ops if isinstance(o, IntroduceOp)]
    assert len(introduce_ops) == 1
    assert introduce_ops[0].title == "new-handcrafted-feature"


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
    f = _index_path(codoc_dir)
    fake_uuid = "00000000-0000-0000-0000-deadbeefdead"
    f.write_text(
        f.read_text() + f"\n- ghost  [Drafting]  # @{fake_uuid}\n    Ghost intent.\n"
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

    f = _index_path(codoc_dir)
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
