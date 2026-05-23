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
    # Wipe-and-rebootstrap: archive pre-v2 .codoc/ if schema_version != 2
    # ------------------------------------------------------------------
    if already_initialized and _needs_archive(codoc_dir):
        _archive_codoc_dir(codoc_dir)
        already_initialized = False
        typer.echo(
            "Archived pre-v2 .codoc/ to .codoc.pre-overhaul/ and starting fresh. "
            "Run 'codoc bootstrap' to re-attribute the codebase."
        )

    # ------------------------------------------------------------------
    # Infrastructure setup
    # ------------------------------------------------------------------
    codoc_dir.mkdir(exist_ok=True)
    _ensure_node_id(codoc_dir)

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


_CODOC_HOOK_MARKER = "# codoc-managed v=2"


def _install_hook(root: Path) -> bool:
    hooks_dir = root / ".git" / "hooks"
    if not hooks_dir.exists():
        typer.echo("Warning: .git/hooks/ not found — not a git repository?", err=True)
        return False

    _install_single_hook(
        hooks_dir / "post-commit",
        body=(
            '#!/bin/sh\n'
            f'{_CODOC_HOOK_MARKER}\n'
            'codoc reflect --root-dir "$(git rev-parse --show-toplevel)" || true\n'
        ),
    )

    _install_single_hook(
        hooks_dir / "pre-commit",
        body=(
            '#!/bin/sh\n'
            f'{_CODOC_HOOK_MARKER}\n'
            'STAGED="$(git diff --cached --name-only 2>/dev/null)"\n'
            'if [ -n "$STAGED" ]; then\n'
            '  codoc commit-preflight --staged "$STAGED"'
            ' --root-dir "$(git rev-parse --show-toplevel)" || true\n'
            'fi\n'
            'exit 0\n'
        ),
    )

    return True


def _install_single_hook(hook_path: Path, body: str) -> None:
    """Write *body* to *hook_path*.

    If the file already exists without the codoc marker, we install into
    .git/hooks/<name>.d/codoc and add a chain-runner to the original hook
    so we never clobber user-written hooks.
    """
    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8", errors="replace")
        if _CODOC_HOOK_MARKER in existing:
            # Already managed by codoc — update in-place.
            hook_path.write_text(body, encoding="utf-8")
            return
        # User hook present without our marker — use .d/ pattern.
        d_dir = hook_path.parent / f"{hook_path.name}.d"
        d_dir.mkdir(exist_ok=True)
        codoc_part = d_dir / "codoc"
        codoc_part.write_text(body, encoding="utf-8")
        codoc_part.chmod(0o755)
        # Add run-parts call if not already present.
        chain_line = f'\n# codoc chain-runner\nfor f in "$(dirname "$0")/{hook_path.name}.d"/*; do [ -x "$f" ] && "$f" "$@"; done\n'
        if chain_line.strip() not in existing:
            hook_path.write_text(existing.rstrip() + chain_line, encoding="utf-8")
        return

    hook_path.write_text(body, encoding="utf-8")
    hook_path.chmod(0o755)


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
            # target_cluster_size=cluster_size,
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
            from_ref="HEAD~1",
            to_ref="HEAD",
            repo_name=root.name,
        )
        proposals = result.get("proposal_count", 0)
        typer.echo(f"  Reflect complete — {proposals} new proposal(s).")
        if proposals:
            typer.echo("Review with 'codoc proposals'.")
    except Exception as exc:
        typer.echo(f"Reflect failed: {exc}", err=True)


def _needs_archive(codoc_dir: Path) -> bool:
    """Return True if the existing .codoc/ is pre-v2 (no schema_version=2 row)."""
    db_path = codoc_dir / "codoc.db"
    if not db_path.exists():
        return False
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA busy_timeout=2000")
        try:
            row = conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            is_v2 = row and row[0] == "2"
        except sqlite3.OperationalError:
            is_v2 = False
        finally:
            conn.close()
        return not is_v2
    except Exception:
        return False


def _archive_codoc_dir(codoc_dir: Path) -> None:
    """Rename .codoc/ to .codoc.pre-overhaul/ (timestamped if one already exists)."""
    import shutil
    import time

    root = codoc_dir.parent
    archive = root / ".codoc.pre-overhaul"
    if archive.exists():
        ts = int(time.time())
        archive = root / f".codoc.pre-overhaul.{ts}"
    shutil.move(str(codoc_dir), str(archive))


def _ensure_node_id(codoc_dir: Path) -> None:
    """Write a unique node_id to .codoc/node_id if not present."""
    import secrets
    nid_file = codoc_dir / "node_id"
    if not nid_file.exists():
        nid_file.write_text(secrets.token_hex(4), encoding="utf-8")
