"""codoc plan — run the planning agent and write proposals."""

from __future__ import annotations

from pathlib import Path

import typer


def plan_command(
    prompt: str = typer.Argument(..., help="Planning prompt (what to build or change)"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    repo_name: str = typer.Option("codebase", "--repo-name", help="Repository name for agent context"),
) -> None:
    """Run the planning agent and write plan proposals to .codoc/tree/."""
    root = Path(root_dir).resolve()
    codoc_dir = root / ".codoc"

    if not codoc_dir.exists():
        typer.echo(f"Error: .codoc/ not found at {codoc_dir}. Run 'codoc init' first.", err=True)
        raise typer.Exit(code=1)

    try:
        from codoc.pipelines.planning.runner import run_plan
        result = run_plan(
            prompt=prompt,
            root_dir=str(root),
            codoc_dir=str(codoc_dir),
            repo_name=repo_name,
        )
    except Exception as exc:
        typer.echo(f"Error: plan failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    proposals_emitted = result.get("proposals_emitted", 0)
    session_id = result.get("session_id", "")
    typer.echo(f"Plan complete (session {session_id[:8]}).")
    typer.echo(f"  Proposals emitted: {proposals_emitted}")

    proposals = result.get("proposals", [])
    if proposals:
        typer.echo("\nProposed changes:")
        for p in proposals:
            kind = p.get("kind", "")
            slug = p.get("slug", "")
            title = p.get("title", "")
            display = f"{title} ({slug})" if title and title != slug else slug
            typer.echo(f"  {kind:<12}  {display}")

    if proposals_emitted > 0:
        typer.echo("\nReview with 'codoc proposals' and accept or reject.")
