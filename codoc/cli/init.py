"""codoc init — initialize .codoc/ and run bootstrap."""

from __future__ import annotations

from pathlib import Path

import typer


def init(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    repo_name: str = typer.Option("", "--repo-name", help="Human-readable repository name"),
    cluster_size: int = typer.Option(8, "--cluster-size", help="Target average chunks per cluster"),
    no_bootstrap: bool = typer.Option(False, "--no-bootstrap", help="Skip automatic bootstrap"),
) -> None:
    """Initialize codoc: creates .codoc/, installs git hook, and runs bootstrap.

    On first run, bootstrap clusters the codebase and proposes features for
    review. Re-running 'codoc init' on an already-initialized repo is safe —
    it will run reflect to catch up on any commits since last run instead of
    re-running bootstrap from scratch.
    """
    root = Path(root_dir).resolve()
    codoc_dir = root / ".codoc"
    already_initialized = codoc_dir.is_dir()

    # ------------------------------------------------------------------
    # Infrastructure setup
    # ------------------------------------------------------------------
    codoc_dir.mkdir(exist_ok=True)

    from codoc.storage.sqlite_store import SQLiteStore
    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    has_features = len(store.list_features()) > 0
    store.close()

    hook_installed = _install_hook(root)

    if already_initialized:
        typer.echo(f"codoc already initialized at {codoc_dir}")
    else:
        typer.echo(f"Initialized codoc at {codoc_dir}")
    if hook_installed:
        typer.echo("Installed git post-commit hook")

    if no_bootstrap:
        typer.echo("\nSkipping bootstrap (--no-bootstrap). Run 'codoc bootstrap' manually.")
        return

    # ------------------------------------------------------------------
    # Smart mode selection: bootstrap on first init, reflect on re-init
    # ------------------------------------------------------------------
    if has_features:
        typer.echo("\nFeature tree found — running reflect to catch up on recent commits ...")
        _run_reflect(root, codoc_dir)
    else:
        typer.echo("\nNo features yet — running bootstrap to attribute your codebase ...")
        _run_bootstrap(root, codoc_dir, repo_name or root.name, cluster_size)


def _install_hook(root: Path) -> bool:
    hooks_dir = root / ".git" / "hooks"
    if not hooks_dir.exists():
        typer.echo("Warning: .git/hooks/ not found — not a git repository?", err=True)
        return False
    hook_path = hooks_dir / "post-commit"
    hook_script = '#!/bin/sh\ncodoc reflect --root-dir "$(git rev-parse --show-toplevel)"\n'
    hook_path.write_text(hook_script, encoding="utf-8")
    hook_path.chmod(0o755)
    return True


def _run_bootstrap(root: Path, codoc_dir: Path, repo_name: str, cluster_size: int) -> None:
    from codoc.cli._utils import check_llm_config

    try:
        check_llm_config()
    except SystemExit:
        typer.echo(
            "LLM not configured — skipping bootstrap. Configure OPENAI_API_KEY and run 'codoc bootstrap'.",
            err=True,
        )
        return

    try:
        from codoc.pipelines.bootstrap.runner import run_bootstrap

        result = run_bootstrap(
            root_dir=str(root),
            codoc_dir=str(codoc_dir),
            repo_name=repo_name,
            target_cluster_size=cluster_size,
        )
    except Exception as exc:
        typer.echo(f"Bootstrap failed: {exc}", err=True)
        typer.echo("Run 'codoc bootstrap' manually after fixing the issue.", err=True)
        return

    proposal_count = result.get("proposal_count", 0)
    chunk_count = result.get("chunk_count", 0)
    typer.echo(f"  Extracted {chunk_count} chunks, emitted {proposal_count} proposals.")
    if proposal_count > 0:
        typer.echo("Review proposals with 'codoc tx list'.")
    else:
        typer.echo("No proposals — check CODOC_LOG_PROMPTS=1 to debug LLM output.", err=True)


def _run_reflect(root: Path, codoc_dir: Path) -> None:
    try:
        from codoc.pipelines.reflective.runner import run_reflect

        result = run_reflect(
            root_dir=str(root),
            codoc_dir=str(codoc_dir),
            from_ref=None,
            to_ref="HEAD",
            repo_name=root.name,
        )
        proposals = result.get("proposal_count", 0)
        typer.echo(f"  Reflect complete — {proposals} new proposal(s).")
        if proposals:
            typer.echo("Review with 'codoc tx list'.")
    except Exception as exc:
        typer.echo(f"Reflect failed: {exc}", err=True)
