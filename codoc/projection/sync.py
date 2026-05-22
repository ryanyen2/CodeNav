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
    IntroduceOp,
    RejectOp,
    RenameOp,
    RestructureOp,
    RetireOp,
    diff_tree,
)
from codoc.projection.meta import read_meta
from codoc.projection.parser import parse_tree_dir
from codoc.projection.tree_codoc import write_tree, render_tree_with_meta


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
    if isinstance(op, IntroduceOp):
        return f"INTRODUCE {op.title!r} (proposal)"
    return repr(op)


def _current_store_head(codoc_dir: str) -> str:
    """Return the current head HLC string from the store, or '' if empty."""
    from codoc.storage.sqlite_store import SQLiteStore

    db_path = str(Path(codoc_dir) / "codoc.db")
    store = SQLiteStore(db_path)
    store.open()
    try:
        txs = store.list_transactions(proposal=False, limit=0)
        if not txs:
            return ""
        return max(txs, key=lambda t: t.hlc).hlc.to_str()
    finally:
        store.close()


def sync_from_dir(codoc_dir: str, author: str = "user") -> SyncResult:
    """Parse `.codoc/tree/`, diff against SQLite, apply transactions, re-render.

    Invariant 3 (race-detected sync): if the store head has advanced since the
    last render (base_hlc < current head), user-side ops that touch untouched
    features are applied cleanly.  Ops that conflict with server-side changes on
    the same feature emit inline <!-- conflict --> annotations in the buffer
    rather than aborting.
    """
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

    # Invariant 3: detect if the store has moved since last render.
    current_head = _current_store_head(codoc_dir)
    base_hlc = old_meta.base_hlc
    stale = current_head and base_hlc and current_head > base_hlc

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
        "parse_error",
    }
    fatal = [e for e in errors if e.kind in fatal_kinds]
    if fatal:
        return SyncResult(status="parse_error", errors=errors)

    if not ops:
        # No-op: re-render to refresh meta & state badges.
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
    conflict_uuids: set[str] = set()

    with IntentionalRunner(codoc_dir, author=author) as runner:
        for op in ops:
            try:
                if isinstance(op, AmendOp):
                    # Invariant 3: if store is stale and the server also amended this
                    # feature since base_hlc, record a conflict warning — but still
                    # apply the user's edit (explicit user intent takes precedence over
                    # a background server change).  The conflict is non-fatal: both
                    # edits land in the transaction log; the server's version is visible
                    # via `codoc show <slug>` history.
                    if stale and _server_amended_since(op.uuid, base_hlc, runner._open_store):
                        conflict_uuids.add(op.uuid)
                        runtime_errors.append(
                            DiffError(
                                kind="conflict",
                                message=(
                                    f"AMEND {op.uuid[:8]}: server also edited this feature "
                                    "since last render — your edit was applied (user wins)"
                                ),
                            )
                        )
                    runner.amend(op.uuid, op.new_intent)
                    applied.append(_describe_op(op))
                elif isinstance(op, RenameOp):
                    runner.rename(op.uuid, op.new_slug)
                    applied.append(_describe_op(op))
                elif isinstance(op, RetireOp):
                    runner.retire(op.uuid)
                    applied.append(_describe_op(op))
                elif isinstance(op, RestructureOp):
                    runner.restructure(op.uuid, op.new_parent_uuid)
                    applied.append(_describe_op(op))
                elif isinstance(op, IntroduceOp):
                    _apply_introduce(op, runner)
                    applied.append(_describe_op(op))
                elif isinstance(op, AcceptOp):
                    _apply_accept(op, runner)
                    applied.append(_describe_op(op))
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
        files, _ = render_tree_with_meta(store2, tx_log2)
        write_tree(codoc_dir, store2, tx_log2)
    finally:
        store2.close()

    status = "ok"
    if runtime_errors:
        conflict_only = all(e.kind == "conflict" for e in runtime_errors)
        if conflict_only:
            status = "ok"  # conflicts are non-fatal; user edit was applied
        else:
            status = "partial" if applied else "parse_error"
    return SyncResult(
        status=status, applied=applied, errors=runtime_errors, new_render=files
    )


def _server_amended_since(feature_uuid: str, base_hlc: str, store) -> bool:
    """Return True if any non-proposal transaction touched *feature_uuid* after *base_hlc*."""
    txs = store.list_transactions(proposal=False, feature_uuid=feature_uuid, limit=0)
    return any(tx.hlc.to_str() > base_hlc for tx in txs)


def _inject_conflict_annotations(
    codoc_dir: str,
    conflict_uuids: set[str],
    old_meta,
) -> None:
    """Write <!-- conflict --> annotations into _index.codoc for conflicting features."""
    index_path = Path(codoc_dir) / "tree" / "_index.codoc"
    if not index_path.exists():
        return

    lines = index_path.read_text(encoding="utf-8").splitlines()

    # Count how many conflict annotations we need to add.
    annotations_added = 0
    for uuid in conflict_uuids:
        loc = old_meta.uuid_to_location.get(uuid)
        if loc is None or loc.get("kind") != "feature":
            continue
        line_no = loc.get("line", -1)
        if line_no < 0 or line_no >= len(lines):
            continue
        # Insert annotation comment above the feature line.
        annotation = f"<!-- conflict: server and local both edited this feature — resolve manually -->"
        lines.insert(line_no + annotations_added, annotation)
        annotations_added += 1

    if annotations_added > 0:
        # Add a header summary.
        header = f"<!-- {annotations_added} unresolved conflict(s) — search for 'conflict:' to find them -->"
        lines.insert(0, header)
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _apply_introduce(op: IntroduceOp, runner: IntentionalRunner) -> None:
    """Create a pending INTRODUCE proposal for an unresolved (new) feature."""
    import re
    import uuid as _uuid_mod

    from codoc.model.hlc import HLC
    from codoc.model.transaction import Transaction, TransactionKind

    tx_log = runner._open_tx_log
    jsonl_log = runner._open_jsonl

    try:
        import uuid6
        new_uuid = str(uuid6.uuid7())
    except ImportError:
        new_uuid = str(_uuid_mod.uuid4())

    slug = re.sub(r"[^a-z0-9]+", "-", op.title.lower()).strip("-") or "unnamed"
    tx = Transaction(
        hlc=HLC(),
        parent_hlcs=[],
        kind=TransactionKind.INTRODUCE,
        payload={
            "title": op.title,
            "slug": slug,
            "intent": op.intent,
            "feature_uuid": new_uuid,
            "provisional_uuid": new_uuid,
            "parent_uuid": op.parent_uuid,
        },
        author="user",
        proposal=True,
    )
    stamped = tx_log.append_proposal(tx)
    jsonl_log.append(stamped)


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
