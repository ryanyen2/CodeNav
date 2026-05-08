"""Shared CLI utilities."""

from __future__ import annotations

from pathlib import Path

import typer


def find_codoc_dir(start: Path) -> Path | None:
    """Walk up from *start* looking for a .codoc/ directory (git-style discovery)."""
    cur = start.resolve()
    while True:
        candidate = cur / ".codoc"
        if candidate.is_dir():
            return candidate
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


def require_codoc_dir(root_dir: str) -> Path:
    """Return the .codoc/ path for *root_dir*, auto-discovering upward if needed.

    Exits with code 1 and a helpful message when .codoc/ cannot be found.
    """
    root = Path(root_dir).resolve()
    codoc_dir = root / ".codoc"
    if codoc_dir.is_dir():
        return codoc_dir
    # Auto-discover by walking up (only when root_dir was the default '.')
    if root_dir in (".", ""):
        discovered = find_codoc_dir(Path.cwd())
        if discovered:
            return discovered
    typer.echo(
        "Error: .codoc/ not found. Run 'codoc init' to initialize codoc here.",
        err=True,
    )
    raise typer.Exit(code=1)


def check_llm_config() -> None:
    """Fail fast with a clear message if the LLM is not configured."""
    import os
    from dotenv import load_dotenv
    load_dotenv()

    provider = os.environ.get("CODOC_PROVIDER", "openai")
    if provider == "openai":
        if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("CODOC_BASE_URL"):
            typer.echo(
                "Error: OPENAI_API_KEY is not set.\n"
                "Set it in your environment or in a .env file, or switch to Ollama:\n"
                "  export CODOC_PROVIDER=ollama\n"
                "  export CODOC_MODEL=llama3",
                err=True,
            )
            raise typer.Exit(code=1)
