"""codoc CLI — four commands.

``init``   bootstrap a fresh repo into a feature tree + render tree.codoc
``watch``  the daemon: run both loops as you edit code / tree.codoc
``status`` tree size, pending proposals, recent activity
``sync``   one-shot escape hatch: apply tree.codoc edits, then reflect code

Everything else is done by editing ``.codoc/tree.codoc`` and letting watch/sync
react.
"""
from __future__ import annotations

import json
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
def init(
    root: str = typer.Option(".", "--root", help="Repository root."),
    hooks: bool = typer.Option(
        True,
        "--hooks/--no-hooks",
        help="Install codoc Claude Code hooks into .claude/settings.json.",
    ),
):
    """Index the repo, propose an initial feature tree, render tree.codoc."""
    from codoc.loop.bootstrap import run_init

    typer.echo(f"Indexing {root} and bootstrapping the feature tree…")
    res = run_init(root)
    typer.echo(f"✓ {res.summary()}")
    typer.echo(f"  Edit {_codoc_dir(root)}/tree.codoc, then run `codoc watch`.")

    if hooks:
        try:
            from codoc.agent.install_hooks import install_hooks
            install_hooks(root)
            typer.echo("  ✓ Claude Code hooks installed in .claude/settings.json")
        except Exception as exc:
            typer.echo(f"  ⚠  Could not install hooks: {exc}", err=True)


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
        from codoc.loop.status import refresh_status

        st = refresh_status(_codoc_dir(root), store)
        state = json.loads(st.read_text()).get("state", "in_sync")
        typer.echo(f"codoc · {len(feats)} features · {len(pending)} pending · state: {state}")
        if pending:
            typer.echo("\nPending proposals (review in tree.codoc, Accept/Reject in the IDE):")
            for e in pending:
                title = e.op.title or e.op.feature_id or ""
                typer.echo(f"  · {e.op.kind.value:11} {title}  ⟨{e.id}⟩")
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


@app.command()
def propose(
    kind: str = typer.Argument(..., help="add_node | amend | retire_node | move_node"),
    root: str = typer.Option(".", "--root", help="Repository root."),
    title: str = typer.Option(None, "--title", help="Feature title (add_node / amend)."),
    description: str = typer.Option(None, "--description", help="Feature description prose."),
    parent: str = typer.Option(None, "--parent", help="Parent feature id (add_node / move_node)."),
    feature: str = typer.Option(None, "--feature", help="Target feature id (amend / retire_node / move_node)."),
    rationale: str = typer.Option("", "--rationale", help="One-line justification shown in the diff block."),
    bind: list[str] = typer.Option(None, "--bind", help="Binding as file.py::symbol_path (repeatable)."),
):
    """Author an agent plan proposal in the codoc feature tree.

    Creates a pending proposal (applied=False Event) tagged as an **agent plan**
    in the ``# ── pending changes`` block of ``tree.codoc``.  The user can Accept
    or Reject it in the VS Code IDE; acceptance triggers Loop B to implement.

    Example::

        codoc propose add_node --title "Date formatting" \\
            --description "ISO-8601 date helpers." \\
            --bind utils/dates.py::format_date
    """
    from codoc.agent.propose import propose_plan

    try:
        eid = propose_plan(
            root,
            kind=kind,
            title=title,
            description=description,
            parent_id=parent,
            feature_id=feature,
            rationale=rationale,
            binds=list(bind) if bind else [],
        )
        typer.echo(f"✓ Proposal created  ⟨{eid}⟩")
        typer.echo(f"  Accept it in the VS Code IDE (inline action on the diff block).")
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command(name="install-hooks")
def install_hooks_cmd(
    root: str = typer.Option(".", "--root", help="Repository root."),
):
    """Install codoc Claude Code hooks into .claude/settings.json.

    Idempotent — safe to run multiple times.
    """
    from codoc.agent.install_hooks import install_hooks

    install_hooks(root)
    typer.echo("✓ Claude Code hooks installed in .claude/settings.json")
    typer.echo("  Hooks: SessionStart, Stop, PreToolUse(Edit|Write|Read), PostToolUse(Edit|Write)")


if __name__ == "__main__":
    app()
