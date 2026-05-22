"""codoc tx — transaction proposal management commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from codoc.cli._utils import require_codoc_dir as _require_codoc_dir

_DEPRECATION_NOTICE = (
    "[deprecated] `codoc tx` has been replaced by top-level commands.\n"
    "  codoc tx list      →  codoc proposals\n"
    "  codoc tx accept    →  codoc accept <slug-path-or-prefix>\n"
    "  codoc tx reject    →  codoc reject <slug-path-or-prefix>\n"
    "  codoc tx label     →  codoc label <ref> <label>\n"
)


def _deprecation_callback(ctx: typer.Context):
    import sys
    if ctx.invoked_subcommand:
        print(_DEPRECATION_NOTICE, file=sys.stderr)


tx_app = typer.Typer(
    help="[deprecated] Use `codoc proposals`, `codoc accept`, `codoc reject` instead.",
    no_args_is_help=True,
    callback=_deprecation_callback,
    invoke_without_command=True,
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




def _resolve_provisional_uuid(provisional_uuid: str, store) -> str | None:
    """Look up a feature by its provisional_uuid (stored in payload on accept).

    When bootstrap emits INTRODUCE proposals, each carries a ``provisional_uuid``
    in its payload.  On accept, that UUID becomes the feature's real UUID.  So to
    resolve a ``parent_uuid`` that was set to a provisional UUID at proposal time,
    we just check whether a feature with that exact UUID already exists.
    """
    feature = store.get_feature(provisional_uuid)
    return feature.uuid if feature is not None else None


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
        title = payload.get("title", "") or slug
        intent = payload.get("intent", "")
        description = payload.get("description", "")
        parent_uuid = payload.get("parent_uuid", None)

        # Resolve provisional parent UUID: if parent_uuid looks like a provisional
        # UUID stored in a sibling INTRODUCE payload, look it up in the features table.
        if parent_uuid and "-" in parent_uuid:
            # Check if a feature with this provisional_uuid exists.
            resolved = _resolve_provisional_uuid(parent_uuid, store)
            if resolved:
                parent_uuid = resolved

        feature_uuid = payload.get("provisional_uuid") or payload.get("feature_uuid") or _new_uuid()

        feature = Feature(
            uuid=feature_uuid,
            slug=slug,
            title=title,
            parent_uuid=parent_uuid,
            intent=intent,
            description=description,
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
        from codoc.core.apply import apply_accepted_transaction
        apply_accepted_transaction(tx, store)

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


@tx_app.command("split")
def split_command(
    feature_uuid: str = typer.Argument(..., help="UUID of the feature to split"),
    child_a_slug: str = typer.Option(..., "--a-slug", help="Slug for child A"),
    child_a_intent: str = typer.Option(..., "--a-intent", help="Intent prose for child A"),
    child_a_bindings: str = typer.Option(
        "",
        "--a-bindings",
        help="Comma-separated binding UUIDs to assign to child A",
    ),
    child_b_slug: str = typer.Option(..., "--b-slug", help="Slug for child B"),
    child_b_intent: str = typer.Option(..., "--b-intent", help="Intent prose for child B"),
    child_b_bindings: str = typer.Option(
        "",
        "--b-bindings",
        help="Comma-separated binding UUIDs to assign to child B",
    ),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    author: str = typer.Option("user", "--author", help="Author identifier"),
) -> None:
    """Split a feature into two new children (Phase 2 SPLIT)."""
    from codoc.pipelines.intentional.split import split_feature

    codoc_dir = _require_codoc_dir(root_dir)
    store, jsonl_log, tx_log = _open_stores(codoc_dir)

    a_uuids = [s.strip() for s in child_a_bindings.split(",") if s.strip()]
    b_uuids = [s.strip() for s in child_b_bindings.split(",") if s.strip()]

    try:
        tx, obligations = split_feature(
            feature_uuid=feature_uuid,
            child_a_slug=child_a_slug,
            child_a_intent=child_a_intent,
            child_a_binding_uuids=a_uuids,
            child_b_slug=child_b_slug,
            child_b_intent=child_b_intent,
            child_b_binding_uuids=b_uuids,
            store=store,
            tx_log=tx_log,
            jsonl_log=jsonl_log,
            author=author,
        )
    except ValueError as exc:
        typer.echo(f"Error: split failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(f"SPLIT [{tx.hlc.to_str()[:30]}] → emitted {len(obligations)} cascade obligation(s)")


@tx_app.command("merge")
def merge_command(
    source_uuids: str = typer.Argument(..., help="Comma-separated source feature UUIDs"),
    target_slug: str = typer.Option(..., "--slug", help="Slug for the merged target feature"),
    target_intent: str = typer.Option(..., "--intent", help="Intent prose for the merged target"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    author: str = typer.Option("user", "--author", help="Author identifier"),
) -> None:
    """Merge multiple features into one new target (Phase 2 MERGE)."""
    from codoc.pipelines.intentional.merge import merge_features

    codoc_dir = _require_codoc_dir(root_dir)
    sources = [s.strip() for s in source_uuids.split(",") if s.strip()]
    if len(sources) < 1:
        typer.echo("Error: at least one source UUID is required.", err=True)
        raise typer.Exit(code=1)

    store, jsonl_log, tx_log = _open_stores(codoc_dir)

    try:
        tx, obligations = merge_features(
            source_uuids=sources,
            target_slug=target_slug,
            target_intent=target_intent,
            store=store,
            tx_log=tx_log,
            jsonl_log=jsonl_log,
            author=author,
        )
    except ValueError as exc:
        typer.echo(f"Error: merge failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(f"MERGE [{tx.hlc.to_str()[:30]}] → emitted {len(obligations)} cascade obligation(s)")


@tx_app.command("restructure")
def restructure_command(
    feature_uuid: str = typer.Argument(..., help="UUID of the feature to move"),
    new_parent_uuid: Optional[str] = typer.Option(
        None,
        "--parent",
        help="UUID of the new parent feature; omit or set to '' to move to root",
    ),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    author: str = typer.Option("user", "--author", help="Author identifier"),
) -> None:
    """Change a feature's parent in the tree (Phase 2 RESTRUCTURE)."""
    from codoc.pipelines.intentional.restructure import restructure_feature

    codoc_dir = _require_codoc_dir(root_dir)
    store, jsonl_log, tx_log = _open_stores(codoc_dir)

    new_parent = new_parent_uuid if new_parent_uuid else None

    try:
        tx, obligations = restructure_feature(
            feature_uuid=feature_uuid,
            new_parent_uuid=new_parent,
            store=store,
            tx_log=tx_log,
            jsonl_log=jsonl_log,
            author=author,
        )
    except ValueError as exc:
        typer.echo(f"Error: restructure failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(
        f"RESTRUCTURE [{tx.hlc.to_str()[:30]}] → emitted {len(obligations)} cascade obligation(s)"
    )


@tx_app.command("rewind")
def rewind_command(
    feature_uuid: str = typer.Argument(..., help="UUID of the feature to rewind"),
    target_hlc: str = typer.Argument(..., help="Target HLC string to rewind to"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    author: str = typer.Option("user", "--author", help="Author identifier"),
) -> None:
    """Rewind a feature's slug/intent to a prior HLC state (Phase 2 REWIND)."""
    from codoc.pipelines.intentional.rewind import rewind_feature

    codoc_dir = _require_codoc_dir(root_dir)
    store, jsonl_log, tx_log = _open_stores(codoc_dir)

    try:
        tx, obligations = rewind_feature(
            feature_uuid=feature_uuid,
            target_hlc_str=target_hlc,
            store=store,
            tx_log=tx_log,
            jsonl_log=jsonl_log,
            author=author,
        )
    except ValueError as exc:
        typer.echo(f"Error: rewind failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(
        f"REWIND [{tx.hlc.to_str()[:30]}] → emitted {len(obligations)} cascade obligation(s)"
    )


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
