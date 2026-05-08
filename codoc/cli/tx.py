"""codoc tx — transaction proposal management commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from codoc.cli._utils import require_codoc_dir as _require_codoc_dir

tx_app = typer.Typer(
    help="Manage transaction proposals (list, show, accept, reject, label).",
    no_args_is_help=True,
)

_VALID_LABELS = frozenset(
    {"accept-verbatim", "accept-light-edit", "accept-heavy-edit", "reject"}
)


def _open_stores(codoc_dir: Path):
    """Open SQLiteStore, JSONLLog, and TransactionLog. Caller must close store."""
    from codoc.storage.sqlite_store import SQLiteStore
    from codoc.storage.jsonl_log import JSONLLog
    from codoc.core.log import TransactionLog

    db_path = str(codoc_dir / "codoc.db")
    log_path = str(codoc_dir / "log.jsonl")

    store = SQLiteStore(db_path)
    store.open()
    jsonl_log = JSONLLog(log_path)
    tx_log = TransactionLog(store)

    return store, jsonl_log, tx_log




def _apply_accepted_transaction(tx, store) -> None:
    """Apply the side-effects of an accepted transaction to the store."""
    from codoc.model.transaction import TransactionKind
    from codoc.model.feature import Feature
    from codoc.model.binding import Binding
    from codoc.model.anchor import Anchor
    from codoc.model.hlc import HLC

    try:
        import uuid_utils  # type: ignore[import]

        def _new_uuid() -> str:
            return str(uuid_utils.uuid7())
    except ImportError:
        import uuid

        def _new_uuid() -> str:  # type: ignore[misc]
            return str(uuid.uuid4())

    now_hlc = HLC.now()
    kind = tx.kind
    payload = tx.payload

    if kind == TransactionKind.INTRODUCE:
        slug = payload.get("slug", "feature")
        intent = payload.get("intent", "")
        parent_uuid = payload.get("parent_uuid", None)
        feature_uuid = payload.get("feature_uuid") or _new_uuid()

        feature = Feature(
            uuid=feature_uuid,
            slug=slug,
            parent_uuid=parent_uuid,
            intent=intent,
            retired=False,
            created_at_hlc=now_hlc,
            updated_at_hlc=now_hlc,
        )
        store.upsert_feature(feature)

        for cb in payload.get("candidate_bindings", []):
            anchor_data = cb.get("anchor", {})
            # Ensure at least symbol_path or ts_query is present.
            symbol_path = anchor_data.get("symbol_path")
            ts_query = anchor_data.get("ts_query")
            if symbol_path is None and ts_query is None:
                # Skip invalid anchors silently.
                continue
            anchor = Anchor(
                file=anchor_data.get("file", ""),
                symbol_path=symbol_path,
                ts_query=ts_query,
                occurrence_index=anchor_data.get("occurrence_index", 0),
            )
            binding = Binding(
                uuid=_new_uuid(),
                feature_uuid=feature_uuid,
                anchor=anchor,
                fingerprint=cb.get("fingerprint", ""),
                fingerprint_at_hlc=now_hlc,
                parent_symbol=cb.get("parent_symbol"),
            )
            store.upsert_binding(binding)

    elif kind == TransactionKind.ABSORB:
        feature_uuid = payload.get("feature_uuid")
        if not feature_uuid:
            return
        anchor_data = payload.get("anchor", {})
        symbol_path = anchor_data.get("symbol_path") if anchor_data else payload.get("symbol_path")
        ts_query = anchor_data.get("ts_query") if anchor_data else None
        file_path = anchor_data.get("file") if anchor_data else payload.get("file", "")

        if symbol_path is None and ts_query is None:
            # Nothing to bind.
            return

        anchor = Anchor(
            file=file_path or "",
            symbol_path=symbol_path,
            ts_query=ts_query,
        )
        binding = Binding(
            uuid=_new_uuid(),
            feature_uuid=feature_uuid,
            anchor=anchor,
            fingerprint=payload.get("current_fingerprint", ""),
            fingerprint_at_hlc=now_hlc,
        )
        store.upsert_binding(binding)

    elif kind == TransactionKind.EVICT:
        binding_uuid = payload.get("binding_uuid")
        if binding_uuid:
            store.delete_binding(binding_uuid)

    elif kind == TransactionKind.RETIRE_REFLECTIVE:
        feature_uuid = payload.get("feature_uuid") or payload.get("affected_feature_uuid")
        if not feature_uuid:
            return
        feature = store.get_feature(feature_uuid)
        if feature is not None and not feature.retired:
            updated = feature.model_copy(update={"retired": True, "updated_at_hlc": now_hlc})
            store.upsert_feature(updated)

    elif kind == TransactionKind.RENAME_INFER:
        feature_uuid = payload.get("feature_uuid") or payload.get("affected_feature_uuid")
        new_slug = payload.get("new_slug") or payload.get("slug")
        if not feature_uuid or not new_slug:
            return
        feature = store.get_feature(feature_uuid)
        if feature is not None:
            updated = feature.model_copy(
                update={"slug": new_slug, "updated_at_hlc": now_hlc}
            )
            store.upsert_feature(updated)

    elif kind == TransactionKind.REATTRIBUTE:
        binding_uuid = payload.get("binding_uuid")
        new_feature_uuid = payload.get("new_feature_uuid")
        if binding_uuid and new_feature_uuid:
            binding = store.get_binding(binding_uuid)
            if binding is not None:
                updated = binding.model_copy(
                    update={
                        "feature_uuid": new_feature_uuid,
                        "fingerprint_at_hlc": now_hlc,
                    }
                )
                store.upsert_binding(updated)


def _try_rich_table(headers: list[str], rows: list[list[str]]) -> bool:
    """Attempt to render a rich table. Returns True on success, False if rich unavailable."""
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(*headers, show_header=True, header_style="bold")
        for row in rows:
            table.add_row(*row)
        console.print(table)
        return True
    except ImportError:
        return False


def _plain_table(headers: list[str], rows: list[list[str]]) -> None:
    """Print a plain-text aligned table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    sep = "  "
    header_line = sep.join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    divider = sep.join("-" * col_widths[i] for i in range(len(headers)))
    typer.echo(header_line)
    typer.echo(divider)
    for row in rows:
        typer.echo(sep.join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row)))


@tx_app.command("list")
def list_proposals(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    limit: int = typer.Option(50, "--limit", "-n", help="Maximum number of proposals to show"),
) -> None:
    """List pending (unaccepted) transaction proposals."""
    codoc_dir = _require_codoc_dir(root_dir)
    store, _, _ = _open_stores(codoc_dir)

    try:
        txs = store.list_transactions(proposal=True, limit=limit)
    finally:
        store.close()

    if not txs:
        typer.echo("No pending proposals.")
        return

    headers = ["HLC", "Kind", "Preview", "Label"]
    rows: list[list[str]] = []
    for tx in txs:
        hlc_short = tx.hlc.to_str()[:30]
        kind = tx.kind.value
        payload = tx.payload
        # Build a useful preview from the payload.
        slug = payload.get("slug") or payload.get("feature_uuid", "")
        preview = slug[:60] if slug else ""
        label = tx.label or ""
        rows.append([hlc_short, kind, preview, label])

    if not _try_rich_table(headers, rows):
        _plain_table(headers, rows)


@tx_app.command("show")
def show_proposal(
    hlc: str = typer.Argument(..., help="HLC string identifier of the proposal"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
) -> None:
    """Show a single transaction proposal in detail."""
    codoc_dir = _require_codoc_dir(root_dir)
    store, _, _ = _open_stores(codoc_dir)

    try:
        tx = store.get_transaction(hlc)
    finally:
        store.close()

    if tx is None:
        typer.echo(f"Error: No transaction found for HLC {hlc!r}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"HLC     : {tx.hlc.to_str()}")
    typer.echo(f"Kind    : {tx.kind.value}")
    typer.echo(f"Author  : {tx.author}")
    typer.echo(f"Proposal: {tx.proposal}")
    if tx.accepted_at:
        typer.echo(f"Accepted: {tx.accepted_at.isoformat()}")
    if tx.label:
        typer.echo(f"Label   : {tx.label}")
    typer.echo("Payload :")
    typer.echo(json.dumps(tx.payload, indent=2, ensure_ascii=False))


@tx_app.command("accept")
def accept_proposal(
    hlc: str = typer.Argument(..., help="HLC string identifier of the proposal to accept"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
) -> None:
    """Accept a transaction proposal, applying its side-effects to the store."""
    codoc_dir = _require_codoc_dir(root_dir)
    store, jsonl_log, tx_log = _open_stores(codoc_dir)

    try:
        tx = store.get_transaction(hlc)
        if tx is None:
            typer.echo(f"Error: No transaction found for HLC {hlc!r}", err=True)
            raise typer.Exit(code=1)
        if not tx.proposal:
            typer.echo(f"Error: Transaction {hlc!r} is already accepted.", err=True)
            raise typer.Exit(code=1)

        # Apply side-effects before flipping proposal→accepted.
        _apply_accepted_transaction(tx, store)

        # Flip proposal to accepted in the log.
        accepted_tx = tx_log.accept_proposal(hlc)

        # Append to the JSONL audit log.
        jsonl_log.append(accepted_tx)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error: accept failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(f"Accepted: [{hlc[:30]}]  {tx.kind.value}")


@tx_app.command("reject")
def reject_proposal(
    hlc: str = typer.Argument(..., help="HLC string identifier of the proposal to reject"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
) -> None:
    """Reject (hard-delete) a transaction proposal."""
    codoc_dir = _require_codoc_dir(root_dir)
    store, _, tx_log = _open_stores(codoc_dir)

    try:
        tx = store.get_transaction(hlc)
        if tx is None:
            typer.echo(f"Error: No transaction found for HLC {hlc!r}", err=True)
            raise typer.Exit(code=1)
        if not tx.proposal:
            typer.echo(f"Error: Transaction {hlc!r} is already accepted and cannot be rejected.", err=True)
            raise typer.Exit(code=1)

        tx_log.reject_proposal(hlc)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error: reject failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(f"Rejected: [{hlc[:30]}]  {tx.kind.value}")


@tx_app.command("label")
def label_proposal(
    hlc: str = typer.Argument(..., help="HLC string identifier of the proposal"),
    label: str = typer.Argument(..., help="Validation label to apply"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
) -> None:
    """Label a proposal for the validation gate.

    Valid labels: accept-verbatim, accept-light-edit, accept-heavy-edit, reject.
    """
    if label not in _VALID_LABELS:
        typer.echo(
            f"Error: Invalid label {label!r}. Must be one of: {', '.join(sorted(_VALID_LABELS))}",
            err=True,
        )
        raise typer.Exit(code=1)

    codoc_dir = _require_codoc_dir(root_dir)
    store, _, _ = _open_stores(codoc_dir)

    try:
        tx = store.get_transaction(hlc)
        if tx is None:
            typer.echo(f"Error: No transaction found for HLC {hlc!r}", err=True)
            raise typer.Exit(code=1)

        store.update_transaction(hlc, {"label": label})
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error: label failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(f"Labeled [{hlc[:30]}] as '{label}'")
