"""Synchronous orchestrator for the cocoindex code-indexing pipeline.

Codoc's bootstrap and reflective pipelines call :func:`update_index` to ensure
the LanceDB-backed chunk index is current before reading from it. Calls are
idempotent and cheap when nothing has changed (cocoindex memoization).
"""
from __future__ import annotations

import os
from pathlib import Path


def _lance_path(codoc_dir: Path) -> Path:
    return codoc_dir / "lancedb"


def _cocoindex_db_path(codoc_dir: Path) -> Path:
    return codoc_dir / "cocoindex.db"


def update_index(
    sourcedir: str | Path,
    codoc_dir: str | Path = ".codoc",
    *,
    report_to_stdout: bool = False,
) -> None:
    """Run the cocoindex code-indexing app once against *sourcedir*.

    Persists chunks + embeddings to ``{codoc_dir}/lancedb`` and tracks
    memoization state in ``{codoc_dir}/cocoindex.db``. Safe to call repeatedly:
    files whose fingerprint is unchanged are skipped; killed runs resume from
    the last committed component.
    """
    codoc_path = Path(codoc_dir).resolve()
    codoc_path.mkdir(parents=True, exist_ok=True)

    os.environ["COCOINDEX_DB"] = str(_cocoindex_db_path(codoc_path))
    os.environ["CODOC_LANCE_PATH"] = str(_lance_path(codoc_path))
    os.environ["CODOC_INDEX_SOURCE"] = str(Path(sourcedir).resolve())

    from codoc.pipelines.indexing.cocoindex_app import make_app

    app = make_app(Path(sourcedir).resolve())
    app.update_blocking(report_to_stdout=report_to_stdout)
