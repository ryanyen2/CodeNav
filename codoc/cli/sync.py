"""codoc sync — state-aware single-verb sync command."""

from __future__ import annotations

import sys
from pathlib import Path

import typer


def sync_command(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-accept all pending proposals"),
    no_intent: bool = typer.Option(False, "--no-intent", help="Skip LLM intent generation during bootstrap (offline/testing only)"),
    from_ref: str = typer.Option("HEAD~1", "--from-ref", help="Git ref range start (for reflect)"),
    to_ref: str = typer.Option("HEAD", "--to-ref", help="Git ref range end (for reflect)"),
    post_commit: bool = typer.Option(False, "--post-commit", hidden=True, help="Internal: post-commit hook mode"),
    write_snapshot_pending: bool = typer.Option(False, "--write-snapshot-pending", hidden=True, help="Internal: write .snapshot-pending.json"),
) -> None:
    """Sync the codoc feature tree with the current codebase.

    Automatically detects what stage the repo is in (uninit, needs bootstrap,
    proposals pending, stale render, etc.) and runs the minimum work needed
    to advance toward a clean state.

    This is the one command you need.  Everything else (init, bootstrap,
    reflect, projection render) is handled automatically.
    """
    root = Path(root_dir).resolve()

    if write_snapshot_pending:
        _write_snapshot_pending(root)
        return

    from codoc.core.sync_dispatcher import dispatch

    try:
        result = dispatch(
            root,
            accept_all=yes,
            no_intent=no_intent,
            from_ref=from_ref,
            to_ref=to_ref,
            post_commit=post_commit,
        )
    except Exception as exc:
        typer.echo(f"sync failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if result.actions:
        for action in result.actions:
            typer.echo(f"  → {action}")

    after = result.stage_after
    pending = result.pending_count
    features = result.feature_count

    if after in ("proposals-pending", "bootstrap-review"):
        typer.echo(
            f"\ncodoc: {pending} proposal(s) pending — "
            "run `codoc proposals` to review or re-run `codoc sync --yes` to accept all"
        )
    elif after == "needs-bootstrap":
        typer.echo("codoc: ready to bootstrap — set OPENAI_API_KEY and re-run `codoc sync`")
    elif after == "stale-render" and not result.actions:
        typer.echo(f"codoc: {features} features — render is stale, re-run `codoc sync`")
    elif after == "clean":
        if result.actions:
            typer.echo(f"\ncodoc: {features} features — tree is in sync")
        else:
            typer.echo(f"codoc: {features} features — nothing to do")


def _write_snapshot_pending(root: Path) -> None:
    """Write .codoc/.snapshot-pending.json for the post-commit hook to fill in."""
    import json

    codoc_dir = root / ".codoc"
    if not codoc_dir.is_dir():
        return

    try:
        from codoc.storage.sqlite_store import SQLiteStore
        store = SQLiteStore(str(codoc_dir / "codoc.db"))
        store.open()
        try:
            all_accepted = store.list_transactions(proposal=False, limit=0)
            head_hlc = max((t.hlc for t in all_accepted), default=None)
            head_str = head_hlc.to_str() if head_hlc else ""
        finally:
            store.close()

        pending_path = codoc_dir / ".snapshot-pending.json"
        pending_path.write_text(
            json.dumps({"head_hlc": head_str}),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[codoc pre-commit] snapshot-pending write failed: {exc}", file=sys.stderr)
