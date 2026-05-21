"""codoc reflect — run the reflective pipeline."""

from __future__ import annotations

from pathlib import Path

import typer


def reflect_command(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    from_ref: str = typer.Option("HEAD~1", "--from-ref", help="Git ref to diff from"),
    to_ref: str = typer.Option("HEAD", "--to-ref", help="Git ref to diff to"),
    files: list[str] = typer.Option(
        None, "--file", "-f",
        help="Process specific files (repo-relative). Can be repeated. Skips git diff.",
    ),
) -> None:
    """Run the reflective pipeline for recent commits and emit proposals.

    Pass --file to process specific files without git refs (useful for on-save hooks).
    """
    root = Path(root_dir).resolve()
    codoc_dir = root / ".codoc"

    if not codoc_dir.exists():
        typer.echo(
            f"Error: .codoc/ not found at {codoc_dir}. Run 'codoc init' first.", err=True
        )
        raise typer.Exit(code=1)

    try:
        if files:
            from codoc.pipelines.reflective.runner import run_reflect_files

            result = run_reflect_files(
                root_dir=str(root),
                codoc_dir=str(codoc_dir),
                file_paths=list(files),
            )
            mode_label = f"file(s): {', '.join(files)}"
            changed_files = result.get("processed_files", 0)
        else:
            from codoc.pipelines.reflective.runner import run_reflect

            result = run_reflect(
                root_dir=str(root),
                codoc_dir=str(codoc_dir),
                from_ref=from_ref,
                to_ref=to_ref,
            )
            mode_label = f"{from_ref}..{to_ref}"
            changed_files = result.get("changed_files", 0)
    except Exception as exc:
        typer.echo(f"Error: reflect failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    changed_chunks = result.get("changed_chunks", 0)
    skipped_unchanged = result.get("skipped_unchanged", 0)
    evicted_directly = result.get("evicted_directly", 0)
    escalated = result.get("escalated_to_llm", 0)
    proposals_emitted = result.get("proposals_emitted", 0)
    proposals = result.get("proposals", [])

    typer.echo(f"Reflect complete ({mode_label}).")
    typer.echo(f"  Changed files    : {changed_files}")
    typer.echo(f"  Changed chunks   : {changed_chunks}")
    typer.echo(f"  Skipped unchanged: {skipped_unchanged}")
    typer.echo(f"  Evicted directly : {evicted_directly}")
    typer.echo(f"  Escalated to LLM : {escalated}")
    typer.echo(f"  Proposals emitted: {proposals_emitted}")

    if proposals:
        typer.echo("\nNew proposals:")
        for p in proposals:
            hlc_short = p.get("hlc", "")[:30]
            kind = p.get("kind", "")
            symbol_path = p.get("symbol_path", "")
            plan_flag = " [plan-aligned]" if p.get("plan_aligned") else ""
            typer.echo(f"  [{hlc_short}]  {kind:<20}  {symbol_path}{plan_flag}")

    if proposals_emitted > 0:
        typer.echo(
            f"\nReview with 'codoc proposals' and accept or reject proposals."
        )
