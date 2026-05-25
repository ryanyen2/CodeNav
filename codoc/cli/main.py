"""codoc CLI — four commands.

``init``   bootstrap a fresh repo into a feature tree + render tree.codoc
``watch``  the daemon: run both loops as you edit code / tree.codoc
``status`` tree size, pending proposals, recent activity
``sync``   one-shot escape hatch: apply tree.codoc edits, then reflect code

Everything else is done by editing ``.codoc/tree.codoc`` and letting watch/sync
react.
"""
from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(
    name="codoc",
    help="codoc keeps a feature-tree view of your code, synced as you edit.",
    no_args_is_help=True,
)


def _codoc_dir(root: str) -> str:
    return str(Path(root) / ".codoc")


@app.command()
def init(root: str = typer.Option(".", "--root", help="Repository root.")):
    """Index the repo, propose an initial feature tree, render tree.codoc."""
    from codoc.loop.bootstrap import run_init

    typer.echo(f"Indexing {root} and bootstrapping the feature tree…")
    res = run_init(root)
    typer.echo(f"✓ {res.summary()}")
    typer.echo(f"  Edit {_codoc_dir(root)}/tree.codoc, then run `codoc watch`.")


@app.command()
def watch(
    root: str = typer.Option(".", "--root", help="Repository root."),
    no_realize: bool = typer.Option(False, "--no-realize", help="Reflect + sync, but don't spawn the coding agent."),
    dry: bool = typer.Option(False, "--dry", help="Build coding directives but don't spawn the agent."),
):
    """Watch code + tree.codoc and run both loops continuously."""
    from codoc.loop.watch import run_watch

    run_watch(_root := root, _codoc_dir(root), no_realize=no_realize, dry_run=dry, printer=typer.echo)


@app.command()
def status(root: str = typer.Option(".", "--root", help="Repository root.")):
    """Show feature count, pending proposals, and recent activity."""
    from codoc.store.db import open_store

    store = open_store(_codoc_dir(root))
    try:
        feats = store.list_features()
        pending = store.pending_events()
        typer.echo(f"codoc · {len(feats)} features · {len(pending)} pending proposal(s)")
        if pending:
            typer.echo("\nPending proposals (review in tree.codoc):")
            for e in pending:
                title = e.op.title or e.op.feature_id or ""
                typer.echo(f"  ? {e.op.kind.value:11} {title}  ⟨{e.id}⟩")
        recent = [e for e in store.recent_events(8) if e.applied]
        if recent:
            typer.echo("\nRecent changes:")
            for e in recent:
                title = e.op.title or e.op.feature_id or ""
                typer.echo(f"  · {e.source:9} {e.op.kind.value:11} {title}")
    finally:
        store.close()


@app.command()
def sync(
    root: str = typer.Option(".", "--root", help="Repository root."),
    dry: bool = typer.Option(False, "--dry", help="Don't spawn the coding agent for tree edits."),
):
    """One-shot: apply tree.codoc edits (Loop B), then reflect code (Loop A)."""
    from codoc.codoc_file.render import write_tree
    from codoc.loop.loop_a import run_loop_a
    from codoc.loop.loop_b import run_loop_b
    from codoc.store.db import open_store

    cd = _codoc_dir(root)
    rb = run_loop_b(root, cd, dry_run=dry)
    typer.echo(f"▸ codoc→code  {rb.summary()}")
    ra = run_loop_a(root, cd)
    typer.echo(f"▸ code→codoc  {ra.summary()}")
    store = open_store(cd)
    try:
        write_tree(store, cd)
    finally:
        store.close()


if __name__ == "__main__":
    app()
