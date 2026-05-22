"""codoc health — check and reconcile binding drift."""

from __future__ import annotations

from pathlib import Path

import typer

from codoc.cli._utils import require_codoc_dir


def health_command(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    feature: str = typer.Option("", "--feature", "-f", help="Slug path of a specific feature to check"),
    full: bool = typer.Option(False, "--full", help="Sweep all bindings (default: changed files only)"),
) -> None:
    """Reconcile binding health: check anchors, compare fingerprints, detect drift."""
    codoc_dir = require_codoc_dir(root_dir)
    root = Path(root_dir).resolve()

    from codoc.storage.sqlite_store import SQLiteStore
    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()

    try:
        if feature:
            feat = store.find_feature_by_slug_path(feature)
            if feat is None:
                typer.echo(f"Error: feature {feature!r} not found.", err=True)
                raise typer.Exit(code=1)

            from codoc.pipelines.health.runner import reconcile_feature
            resolutions = reconcile_feature(feat.uuid, store, str(root))
            _print_resolutions(resolutions, feature)
        else:
            from codoc.pipelines.health.runner import reconcile_all
            result = reconcile_all(store, str(root))
            typer.echo(f"Health check complete.")
            typer.echo(f"  Bindings checked : {result['total_checked']}")
            typer.echo(f"  Drifted          : {result['drifted']}")
            typer.echo(f"  Severed          : {result['severed']}")
            aligned = result["total_checked"] - result["drifted"] - result["severed"]
            typer.echo(f"  Still aligned    : {aligned}")
            if result["drifted"] + result["severed"] > 0:
                typer.echo(
                    "\nRun 'codoc health --feature <slug>' for per-feature detail,"
                    " or 'codoc show <slug>' to inspect a feature's state."
                )
    finally:
        store.close()


def _print_resolutions(resolutions: list[dict], slug: str) -> None:
    if not resolutions:
        typer.echo(f"No bindings found for {slug!r}.")
        return
    typer.echo(f"Bindings for {slug!r}:")
    for r in resolutions:
        uuid_short = r["binding_uuid"][:8]
        verdict = r.get("verdict", "unknown")
        sim = r.get("similarity")
        sim_str = f" ({sim:.0%} similar)" if sim is not None else ""
        mark = "✓" if verdict == "still_aligned" else "~" if verdict == "partially_drifted" else "✗"
        typer.echo(f"  {mark}  {uuid_short}  {verdict}{sim_str}")
