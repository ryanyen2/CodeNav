"""codoc bootstrap — run the bootstrap attribution pipeline."""

from __future__ import annotations

from pathlib import Path

import typer

from codoc.cli._utils import check_llm_config, require_codoc_dir

bootstrap_app = typer.Typer(
    help="Run bootstrap pipeline to attribute an existing codebase.",
    no_args_is_help=False,
    invoke_without_command=True,
)


@bootstrap_app.callback()
def bootstrap_default(
    ctx: typer.Context,
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    repo_name: str = typer.Option("", "--repo-name", help="Human-readable repository name"),
    cluster_size: int = typer.Option(8, "--cluster-size", help="Target average chunks per cluster"),
    hierarchical: bool = typer.Option(False, "--hierarchical", help="[deprecated] Use two-level clustering"),
    reset: bool = typer.Option(False, "--reset", help="Wipe existing .codoc state before running"),
) -> None:
    """Cluster the codebase and propose a deep hierarchical feature tree for review."""
    if ctx.invoked_subcommand is not None:
        return

    _run_bootstrap(root_dir=root_dir, repo_name=repo_name, cluster_size=cluster_size, reset=reset)


@bootstrap_app.command("run")
def run_bootstrap_cmd(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    repo_name: str = typer.Option("", "--repo-name", help="Human-readable repository name"),
    cluster_size: int = typer.Option(8, "--cluster-size", help="Target average chunks per cluster"),
    reset: bool = typer.Option(False, "--reset", help="Wipe existing .codoc state before running"),
) -> None:
    """Cluster the codebase and propose a deep hierarchical feature tree for review."""
    _run_bootstrap(root_dir=root_dir, repo_name=repo_name, cluster_size=cluster_size, reset=reset)


def _run_bootstrap(root_dir: str, repo_name: str, cluster_size: int, reset: bool = False) -> None:
    check_llm_config()

    root = Path(root_dir).resolve()
    codoc_dir = require_codoc_dir(root_dir)

    inferred_name = repo_name or root.name

    if reset:
        typer.echo(f"Resetting {codoc_dir} ...")

    typer.echo(f"Bootstrapping {root} ...")

    try:
        from codoc.pipelines.bootstrap.runner import run_bootstrap

        result = run_bootstrap(
            root_dir=str(root),
            codoc_dir=str(codoc_dir),
            repo_name=inferred_name,
            target_cluster_size=cluster_size,
            reset=reset,
        )
    except Exception as exc:
        typer.echo(f"Error: bootstrap failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    chunk_count = result.get("chunk_count", 0)
    cluster_count = result.get("cluster_count", 0)
    proposal_count = result.get("proposal_count", 0)
    proposals = result.get("proposals", [])

    typer.echo(f"\nBootstrap complete.")
    typer.echo(f"  Chunks extracted : {chunk_count}")
    typer.echo(f"  Clusters formed  : {cluster_count}")
    typer.echo(f"  Proposals emitted: {proposal_count}")

    if proposals:
        typer.echo("\nProposed features (pending review):")
        for p in proposals:
            hlc_short = p.get("hlc", "")[:30]
            slug = p.get("slug", "")
            candidate_count = p.get("candidate_count", 0)
            typer.echo(f"  [{hlc_short}]  {slug}  ({candidate_count} bindings)")

    if proposal_count == 0:
        typer.echo(
            "\nNo proposals generated. Check LLM config or run with CODOC_LOG_PROMPTS=1.",
            err=True,
        )
    else:
        typer.echo(
            "\nReview with 'codoc proposals', then 'codoc bootstrap finish' when done."
        )


@bootstrap_app.command("finish")
def finish_bootstrap_cmd(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
) -> None:
    """Mark bootstrap complete. Unattributed chunks are recorded as intentional."""
    codoc_dir = require_codoc_dir(root_dir)

    typer.echo("Finalizing bootstrap ...")

    try:
        from codoc.pipelines.bootstrap.runner import finish_bootstrap

        result = finish_bootstrap(codoc_dir=str(codoc_dir))
    except Exception as exc:
        typer.echo(f"Error: finish_bootstrap failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    unattributed_count = result.get("unattributed_count", 0)
    typer.echo(f"Bootstrap finished.")
    typer.echo(f"  Unattributed chunks recorded: {unattributed_count}")
    typer.echo(
        "\ncodoc is now in reflective mode. The post-commit hook will propose changes automatically."
    )
