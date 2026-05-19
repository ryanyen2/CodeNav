"""Orchestrate save → parse → diff → apply → re-render for `.codoc/tree/` edits."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from codoc.core.log import TransactionLog
from codoc.pipelines.intentional.runner import IntentionalRunner, open_stores
from codoc.projection.differ import (
    AcceptOp,
    AmendOp,
    DiffError,
    IntentOp,
    RejectOp,
    RenameOp,
    RestructureOp,
    RetireOp,
    diff_tree,
)
from codoc.projection.meta import read_meta
from codoc.projection.parser import parse_tree_dir
from codoc.projection.tree_codoc import write_tree


@dataclass
class SyncResult:
    applied: list[str] = field(default_factory=list)
    errors: list[DiffError] = field(default_factory=list)
    status: str = "ok"  # "ok" | "stale_buffer" | "parse_error" | "partial" | "noop"
    new_render: dict[str, str] | None = None


def _describe_op(op: IntentOp) -> str:
    if isinstance(op, AmendOp):
        return f"AMEND {op.uuid[:8]}: intent updated"
    if isinstance(op, RenameOp):
        return f"RENAME {op.uuid[:8]} → {op.new_slug}"
    if isinstance(op, RetireOp):
        return f"RETIRE {op.uuid[:8]}"
    if isinstance(op, RestructureOp):
        parent_str = op.new_parent_uuid[:8] if op.new_parent_uuid else "<root>"
        return f"RESTRUCTURE {op.uuid[:8]} → parent={parent_str}"
    if isinstance(op, AcceptOp):
        return f"ACCEPT proposal {op.hlc[:24]}"
    if isinstance(op, RejectOp):
        return f"REJECT proposal {op.hlc[:24]}"
    return repr(op)


def sync_from_dir(codoc_dir: str, author: str = "user") -> SyncResult:
    """Parse `.codoc/tree/`, diff against SQLite, apply transactions, re-render."""
    codoc_path = Path(codoc_dir)
    if not codoc_path.exists():
        return SyncResult(
            status="stale_buffer",
            errors=[DiffError(kind="missing_codoc", message=f"{codoc_dir!r} does not exist")],
        )

    old_meta = read_meta(codoc_dir)
    if old_meta is None:
        return SyncResult(
            status="stale_buffer",
            errors=[
                DiffError(
                    kind="stale_buffer",
                    message=(
                        "No tree.meta.json found. Run `codoc projection render` to "
                        "create the .codoc/tree/ buffer first."
                    ),
                )
            ],
        )

    parsed = parse_tree_dir(codoc_dir, old_meta=old_meta)

    # Open the store to compute the diff.
    store, _, _ = open_stores(codoc_dir)
    try:
        ops, errors = diff_tree(parsed, store)
    finally:
        store.close()

    fatal_kinds = {
        "unknown_uuid",
        "duplicate_uuid",
        "cycle_detected",
        "slug_collision",
        "new_feature_not_allowed",
        "parse_error",
    }
    fatal = [e for e in errors if e.kind in fatal_kinds]
    if fatal:
        return SyncResult(status="parse_error", errors=errors)

    if not ops:
        # No-op: re-render to refresh meta & state badges.
        from codoc.projection.tree_codoc import render_tree_with_meta

        store2, _, tx_log2 = open_stores(codoc_dir)
        try:
            files, _ = render_tree_with_meta(store2, tx_log2)
            write_tree(codoc_dir, store2, tx_log2)
        finally:
            store2.close()
        return SyncResult(status="ok", applied=[], errors=errors, new_render=files)

    # Re-open via the runner context manager to apply ops.
    applied: list[str] = []
    runtime_errors: list[DiffError] = list(errors)

    with IntentionalRunner(codoc_dir, author=author) as runner:
        for op in ops:
            try:
                if isinstance(op, AmendOp):
                    runner.amend(op.uuid, op.new_intent)
                elif isinstance(op, RenameOp):
                    runner.rename(op.uuid, op.new_slug)
                elif isinstance(op, RetireOp):
                    runner.retire(op.uuid)
                elif isinstance(op, RestructureOp):
                    runner.restructure(op.uuid, op.new_parent_uuid)
                elif isinstance(op, AcceptOp):
                    _apply_accept(op, runner)
                elif isinstance(op, RejectOp):
                    _apply_reject(op, runner)
                applied.append(_describe_op(op))
            except (ValueError, KeyError) as exc:
                runtime_errors.append(
                    DiffError(
                        kind="apply_failed",
                        message=f"{_describe_op(op)} failed: {exc}",
                    )
                )

    # Re-render after applying.
    store2, _, tx_log2 = open_stores(codoc_dir)
    try:
        from codoc.projection.tree_codoc import render_tree_with_meta

        files, _ = render_tree_with_meta(store2, tx_log2)
        write_tree(codoc_dir, store2, tx_log2)
    finally:
        store2.close()

    status = "ok"
    if runtime_errors:
        status = "partial" if applied else "parse_error"
    return SyncResult(
        status=status, applied=applied, errors=runtime_errors, new_render=files
    )


def _apply_accept(op: AcceptOp, runner: IntentionalRunner) -> None:
    """Accept a proposal and apply its side effects to the store.

    Mirrors the logic in ``codoc.cli.tx._apply_accepted_transaction`` so that
    accepting via the projection sync path has the same effect as ``codoc tx accept``.
    """
    from codoc.cli.tx import _apply_accepted_transaction

    store = runner._open_store
    tx_log = runner._open_tx_log
    jsonl_log = runner._open_jsonl

    tx = store.get_transaction(op.hlc)
    if tx is None:
        raise KeyError(f"Proposal {op.hlc!r} not found")
    if not tx.proposal:
        raise ValueError(f"Transaction {op.hlc!r} is already accepted")

    # Apply side-effects first so the store is consistent before flipping the flag.
    _apply_accepted_transaction(tx, store)
    accepted = tx_log.accept_proposal(op.hlc, edits=op.edits)
    jsonl_log.append(accepted)


def _apply_reject(op: RejectOp, runner: IntentionalRunner) -> None:
    """Reject (delete) a proposal."""
    tx_log = runner._open_tx_log
    tx_log.reject_proposal(op.hlc)
