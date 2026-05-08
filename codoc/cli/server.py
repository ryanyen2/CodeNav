"""codoc server — start the codoc FastAPI server."""

from __future__ import annotations

import os
from pathlib import Path

import typer


def server(
    root_dir: str = typer.Option(".", "--root-dir", "-d", help="Root directory of the codebase"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host address to bind"),
    port: int = typer.Option(8001, "--port", help="Port to listen on"),
) -> None:
    """Start the codoc FastAPI server."""
    root = Path(root_dir).resolve()
    os.environ["CODOC_ROOT_DIR"] = str(root)

    try:
        import uvicorn
    except ImportError:
        typer.echo("Error: uvicorn is not installed. Install it with: pip install uvicorn", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Starting codoc server on {host}:{port}")
    typer.echo(f"Root directory: {root}")

    uvicorn.run("codoc.api.app:app", host=host, port=port, reload=False)
