"""codoc projection — render and sync .codoc/tree/ files."""

from __future__ import annotations

from pathlib import Path

import typer

from codoc.cli._utils import require_codoc_dir as _require_codoc_dir

proj_app = typer.Typer(
    help="Projection commands: render and sync .codoc/tree/ files.",
    no_args_is_help=True,
)


@proj_app.command("render")
def render_cmd(
    root_dir: str = typer.Option(".", "--root-dir", "-r", help="Repo root directory"),
) -> None:
    """Render SQLite state to .codoc/tree/ files."""
    from codoc.pipelines.intentional.runner import open_stores
    from codoc.projection.tree_codoc import write_tree

    codoc_dir = _require_codoc_dir(root_dir)
    store, _, tx_log = open_stores(str(codoc_dir))
    try:
        meta = write_tree(str(codoc_dir), store, tx_log)
    finally:
        store.close()
    typer.echo(f"Rendered .codoc/tree/ ({len(meta.uuid_to_location)} entries) at HLC {meta.base_hlc[:30] or '<empty>'}")


@proj_app.command("sync")
def sync_cmd(
    root_dir: str = typer.Option(".", "--root-dir", "-r"),
    author: str = typer.Option("user", "--author"),
) -> None:
    """Parse .codoc/tree/ files, diff against SQLite, apply transactions, re-render."""
    from codoc.projection.sync import sync_from_dir

    codoc_dir = _require_codoc_dir(root_dir)
    result = sync_from_dir(str(codoc_dir), author=author)

    typer.echo(f"status: {result.status}")
    if result.applied:
        typer.echo(f"applied {len(result.applied)} transaction(s):")
        for line in result.applied:
            typer.echo(f"  - {line}")
    if result.errors:
        typer.echo(f"{len(result.errors)} error(s):")
        for err in result.errors:
            loc = f" {err.file}:{err.line}" if err.file else ""
            typer.echo(f"  [{err.kind}]{loc} {err.message}", err=True)
    if result.status == "parse_error":
        raise typer.Exit(code=1)


@proj_app.command("diff")
def diff_cmd(
    root_dir: str = typer.Option(".", "--root-dir", "-r"),
) -> None:
    """Show inferred transactions without applying (dry run)."""
    from codoc.pipelines.intentional.runner import open_stores
    from codoc.projection.differ import (
        AcceptOp,
        AmendOp,
        RejectOp,
        RenameOp,
        RestructureOp,
        RetireOp,
        diff_tree,
    )
    from codoc.projection.meta import read_meta
    from codoc.projection.parser import parse_tree_dir

    codoc_dir = _require_codoc_dir(root_dir)
    old_meta = read_meta(str(codoc_dir))
    if old_meta is None:
        typer.echo(
            "No tree.meta.json — run `codoc projection render` first.", err=True
        )
        raise typer.Exit(code=1)

    parsed = parse_tree_dir(str(codoc_dir), old_meta=old_meta)
    store, _, _ = open_stores(str(codoc_dir))
    try:
        ops, errors = diff_tree(parsed, store)
        # Build slug lookup while store is still open.
        uuid_to_slug = {f.uuid: f.slug for f in store.list_features()}
    finally:
        store.close()

    if not ops and not errors:
        typer.echo("(no changes)")
        return

    parsed_slug = {pf.uuid: pf.slug for pf in parsed.features}

    def _label(uuid: str) -> str:
        return parsed_slug.get(uuid) or uuid_to_slug.get(uuid) or uuid[:8]

    for op in ops:
        if isinstance(op, AmendOp):
            typer.echo(f"AMEND {_label(op.uuid)} → {op.new_intent[:60]!r}")
        elif isinstance(op, RenameOp):
            typer.echo(f"RENAME {_label(op.uuid)} → {op.new_slug}")
        elif isinstance(op, RetireOp):
            typer.echo(f"RETIRE {_label(op.uuid)}")
        elif isinstance(op, RestructureOp):
            parent = _label(op.new_parent_uuid) if op.new_parent_uuid else "<root>"
            typer.echo(f"RESTRUCTURE {_label(op.uuid)} → parent={parent}")
        elif isinstance(op, AcceptOp):
            typer.echo(f"ACCEPT {op.hlc[:30]}")
        elif isinstance(op, RejectOp):
            typer.echo(f"REJECT {op.hlc[:30]}")

    for err in errors:
        loc = f" {err.file}:{err.line}" if err.file else ""
        typer.echo(f"[{err.kind}]{loc} {err.message}", err=True)
