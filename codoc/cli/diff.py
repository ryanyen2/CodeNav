"""codoc diff — show the semantic tree diff between two codoc versions."""

from __future__ import annotations

from pathlib import Path

import typer


def diff_command(
    ref: str = typer.Argument(..., help="Git SHA, HLC prefix, or 'HEAD~N' to diff against"),
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
) -> None:
    """Show the semantic diff between a past codoc snapshot and the current tree.

    REF can be:
    - A git SHA (matched against SNAPSHOT transactions committed at that SHA)
    - An HLC prefix (first N chars of a transaction HLC string)
    - A git refspec like HEAD~1 (resolved to a commit SHA, then matched to SNAPSHOT)

    Example:
        codoc diff HEAD~1
        codoc diff abc1234
    """
    root = Path(root_dir).resolve()
    codoc_dir = root / ".codoc"

    if not codoc_dir.is_dir():
        typer.echo("Error: no .codoc/ found — run `codoc sync` first", err=True)
        raise typer.Exit(code=1)

    from codoc.storage.sqlite_store import SQLiteStore
    from codoc.model.transaction import TransactionKind

    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    try:
        base_hlc = _resolve_ref_to_hlc(ref, root, store)
        if base_hlc is None:
            typer.echo(f"Error: could not resolve {ref!r} to a codoc snapshot", err=True)
            raise typer.Exit(code=1)

        _print_diff(base_hlc, store, root)
    finally:
        store.close()


def _resolve_ref_to_hlc(ref: str, root: Path, store) -> str | None:
    """Resolve a ref (git SHA / HLC prefix / refspec) to a base HLC string.

    Strategy:
    1. If ref looks like a git refspec (contains ~, ^, HEAD), resolve to SHA.
    2. Search SNAPSHOT transactions for a matching git_sha prefix.
    3. If still nothing, treat ref as an HLC prefix and search the tx table.
    4. Return the ``head_hlc`` field from the matched SNAPSHOT payload.
    """
    import subprocess

    sha: str | None = None

    # Try to resolve as a git ref.
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", ref], cwd=str(root), text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        sha = None

    # If we got a SHA, look for a SNAPSHOT tx with that sha in payload.
    if sha:
        snapshots = store.list_transactions(proposal=False, limit=0)
        for tx in snapshots:
            if tx.kind == TransactionKind.SNAPSHOT:
                tx_sha = tx.payload.get("git_sha", "")
                if tx_sha == sha or tx_sha.startswith(ref):
                    return tx.payload.get("head_hlc") or tx.hlc.to_str()

    # Fallback: treat ref as an HLC prefix — find the first accepted tx that starts with it.
    all_txs = store.list_transactions(proposal=False, limit=0)
    for tx in sorted(all_txs, key=lambda t: t.hlc.to_str()):
        if tx.hlc.to_str().startswith(ref):
            return tx.hlc.to_str()

    return None


def _print_diff(base_hlc: str, store, root: Path) -> None:
    """Emit a human-readable summary of changes since base_hlc."""
    all_txs = store.list_transactions(proposal=False, limit=0)
    since = [t for t in all_txs if t.hlc.to_str() > base_hlc]

    if not since:
        typer.echo("No changes since that snapshot.")
        return

    typer.echo(f"Changes since {base_hlc[:20]}...  ({len(since)} transaction(s))\n")
    for tx in sorted(since, key=lambda t: t.hlc.to_str()):
        slug = (
            tx.payload.get("slug")
            or tx.payload.get("new_slug")
            or tx.payload.get("feature_uuid", "?")[:8]
        )
        typer.echo(f"  {tx.kind.value:<20}  {slug}  [{tx.hlc.to_str()[:18]}]")
