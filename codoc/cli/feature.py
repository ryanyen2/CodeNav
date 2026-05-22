"""codoc feature — feature inspection and mutation commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from codoc.cli._utils import require_codoc_dir as _require_codoc_dir

_DEPRECATION_NOTICE = (
    "[deprecated] `codoc feature` has been replaced by top-level commands.\n"
    "  codoc feature show    →  codoc show <slug-path-or-prefix>\n"
    "  codoc feature amend   →  codoc edit <slug-path> [--intent TEXT]\n"
    "  codoc feature rename  →  codoc rename <slug-path> <new-slug>\n"
    "  codoc feature retire  →  codoc retire <slug-path>\n"
)


def _deprecation_callback(ctx: typer.Context):
    import sys
    if ctx.invoked_subcommand:
        print(_DEPRECATION_NOTICE, file=sys.stderr)


feature_app = typer.Typer(
    help="[deprecated] Use `codoc show`, `codoc edit`, `codoc rename`, `codoc retire` instead.",
    no_args_is_help=True,
    callback=_deprecation_callback,
    invoke_without_command=True,
)



@feature_app.command("show")
def show_feature(
    uuid: str = typer.Argument(..., help="Feature UUID"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
) -> None:
    """Show details for a feature including its bindings."""
    codoc_dir = _require_codoc_dir(root_dir)

    from codoc.storage.sqlite_store import SQLiteStore

    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    try:
        feature = store.get_feature(uuid)
        if feature is None:
            typer.echo(f"Error: Feature {uuid!r} not found.", err=True)
            raise typer.Exit(code=1)

        bindings = store.list_bindings(uuid)
    finally:
        store.close()

    typer.echo(f"UUID    : {feature.uuid}")
    typer.echo(f"Slug    : {feature.slug}")
    typer.echo(f"Retired : {feature.retired}")
    if feature.parent_uuid:
        typer.echo(f"Parent  : {feature.parent_uuid}")
    typer.echo(f"Intent  :")
    typer.echo(f"  {feature.intent}" if feature.intent else "  (none)")
    typer.echo(f"Created : {feature.created_at_hlc.to_str()[:30]}")
    typer.echo(f"Updated : {feature.updated_at_hlc.to_str()[:30]}")

    typer.echo(f"\nBindings ({len(bindings)}):")
    if not bindings:
        typer.echo("  (none)")
    for b in bindings:
        anchor = b.anchor
        loc = anchor.symbol_path or anchor.ts_query or anchor.file
        typer.echo(f"  [{b.uuid[:12]}]  {anchor.file}  {loc or ''}")


@feature_app.command("amend")
def amend_feature_cmd(
    uuid: str = typer.Argument(..., help="Feature UUID"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
) -> None:
    """Interactively edit a feature's intent prose."""
    codoc_dir = _require_codoc_dir(root_dir)

    from codoc.storage.sqlite_store import SQLiteStore

    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    try:
        feature = store.get_feature(uuid)
        if feature is None:
            typer.echo(f"Error: Feature {uuid!r} not found.", err=True)
            raise typer.Exit(code=1)
        current_intent = feature.intent
    finally:
        store.close()

    # Try to open the user's $EDITOR; fall back to typer.prompt.
    new_intent: str | None = None
    try:
        import click

        edited = click.edit(current_intent or "", require_save=True)
        if edited is not None:
            new_intent = edited.strip()
    except Exception:
        pass

    if new_intent is None:
        typer.echo(f"Current intent: {current_intent!r}")
        new_intent = typer.prompt("New intent", default=current_intent or "")
        new_intent = new_intent.strip()

    if not new_intent:
        typer.echo("No changes made (empty intent).")
        return

    if new_intent == (current_intent or "").strip():
        typer.echo("No changes made (intent unchanged).")
        return

    try:
        from codoc.pipelines.intentional.runner import IntentionalRunner

        with IntentionalRunner(str(codoc_dir), author="user") as runner:
            tx = runner.amend(uuid, new_intent)
    except Exception as exc:
        typer.echo(f"Error: amend failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Amended feature {uuid[:12]} — intent updated.")
    typer.echo(f"Transaction: {tx.hlc.to_str()[:30]}")


@feature_app.command("rename")
def rename_feature_cmd(
    uuid: str = typer.Argument(..., help="Feature UUID"),
    new_slug: str = typer.Argument(..., help="New slug for the feature"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
) -> None:
    """Rename a feature (change its slug)."""
    codoc_dir = _require_codoc_dir(root_dir)

    new_slug = new_slug.strip()
    if not new_slug:
        typer.echo("Error: new_slug must not be empty.", err=True)
        raise typer.Exit(code=1)

    try:
        from codoc.pipelines.intentional.runner import IntentionalRunner

        with IntentionalRunner(str(codoc_dir), author="user") as runner:
            tx = runner.rename(uuid, new_slug)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.echo(f"Error: rename failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Renamed feature {uuid[:12]} to '{new_slug}'.")
    typer.echo(f"Transaction: {tx.hlc.to_str()[:30]}")


@feature_app.command("retire")
def retire_feature_cmd(
    uuid: str = typer.Argument(..., help="Feature UUID"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
) -> None:
    """Retire a feature (marks it as no longer active; does not delete bindings)."""
    codoc_dir = _require_codoc_dir(root_dir)

    try:
        from codoc.pipelines.intentional.runner import IntentionalRunner

        with IntentionalRunner(str(codoc_dir), author="user") as runner:
            tx = runner.retire(uuid)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.echo(f"Error: retire failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Retired feature {uuid[:12]}.")
    typer.echo(f"Transaction: {tx.hlc.to_str()[:30]}")
