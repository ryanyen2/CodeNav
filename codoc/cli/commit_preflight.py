"""codoc commit-preflight — soft-warn gate before git commit.

Called by the pre-commit hook. POSTs to /commit/preflight and interactively
prompts the user to accept-all / reject-all / proceed anyway / abort when
there are pending codoc proposals touching staged files.

Exits 0 in all cases except explicit "quit" (exits 1 to abort the commit).
In non-interactive mode (no TTY), prints a warning and exits 0.
"""

from __future__ import annotations

import sys
import typer
import httpx
from pathlib import Path

_DEFAULT_PORT = 8001


def commit_preflight(
    staged: str = typer.Option("", "--staged", help="Newline- or space-separated list of staged file paths"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    port: int = typer.Option(_DEFAULT_PORT, "--port", help="codoc server port"),
) -> None:
    """Check for pending proposals before committing (pre-commit hook)."""
    if not staged.strip():
        return  # Nothing staged — nothing to check.

    staged_files = [p.strip() for p in staged.replace("\n", " ").split() if p.strip()]

    base_url = f"http://127.0.0.1:{port}"
    root = Path(root_dir).resolve()

    try:
        resp = httpx.post(
            f"{base_url}/commit/preflight",
            json={"root_dir": str(root), "staged_files": staged_files},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        # Server not running or unreachable — soft-warn and proceed.
        typer.echo(f"  codoc: server not reachable ({exc}), skipping preflight check.", err=True)
        raise typer.Exit(0)

    if not data.get("blocked", False):
        return  # Clean — exit 0 silently.

    pending = data.get("pending", [])
    count = len(pending)

    typer.echo(f"\n  codoc: {count} pending proposal(s) touching staged files:\n")
    for p in pending[:10]:  # Show at most 10
        kind = p.get("kind", "?")
        slug = p.get("slug") or p.get("title") or p.get("hlc", "")[:12]
        typer.echo(f"    [{kind}] {slug}")
    if count > 10:
        typer.echo(f"    ... and {count - 10} more")

    # Non-interactive (CI / pipe): soft-warn and proceed.
    if not sys.stdin.isatty():
        typer.echo(f"\n  codoc: non-interactive — proceeding with {count} unreviewed proposal(s).", err=True)
        raise typer.Exit(0)

    typer.echo(
        "\n  [a] accept all   [r] reject all   [c] continue anyway   [q] quit (abort commit)\n"
    )
    choice = typer.prompt("  choice", default="c").strip().lower()

    if choice in ("a", "r"):
        verb = "accept" if choice == "a" else "reject"
        _bulk_action(base_url, root, verb, count)
        raise typer.Exit(0)
    if choice == "q":
        typer.echo("  codoc: aborting commit.")
        raise typer.Exit(1)
    # 'c' or anything else → proceed.
    raise typer.Exit(0)


def _bulk_action(base_url: str, root: Path, verb: str, count: int) -> None:
    """POST to /tx/<verb>-all and print the result. Errors are reported but not raised."""
    try:
        r = httpx.post(
            f"{base_url}/tx/{verb}-all",
            json={"root_dir": str(root)},
            timeout=30,
        )
        r.raise_for_status()
        n = r.json().get(f"{verb}ed", count)
        typer.echo(f"  codoc: {verb}ed {n} proposal(s).")
    except Exception as exc:
        typer.echo(f"  codoc: {verb}-all failed: {exc}", err=True)
