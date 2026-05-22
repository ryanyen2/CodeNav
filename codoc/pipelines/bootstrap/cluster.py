"""
codoc.pipelines.bootstrap.cluster — chunk extraction and embedding.

Phase 1 of bootstrap:
  1. Walk the repo and extract code chunks via language adapters.
  2. Embed each chunk via codoc.config.embed().
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from codoc.lang import get_adapter, detect_language, Chunk
from codoc.config import embed, get_embedder_config


# Directories to skip unconditionally during the walk.
_DEFAULT_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".codoc",
        "__pycache__",
        "node_modules",
        "dist",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        ".eggs",
        "*.egg-info",
    }
)


def extract_all_chunks(
    root_dir: str,
    excluded_dirs: set[str] | None = None,
) -> list[Chunk]:
    """Walk *root_dir*, detect language for each file, and extract chunks.

    Uses Phase A's :class:`~codoc.core.ignore.IgnoreRules` (pathspec-based
    .gitignore / .codocignore / linguist markers / size cap) as the primary
    filter.  The legacy *excluded_dirs* parameter is still accepted but
    superseded by IgnoreRules when available.

    Parameters
    ----------
    root_dir:
        Absolute or relative path to the repository root.
    excluded_dirs:
        Additional directory names to skip (merged into legacy fallback set).

    Returns
    -------
    list[Chunk]
        All chunks extracted from supported files, in walk order.
    """
    try:
        from codoc.core.ignore import IgnoreRules
        ignore: "IgnoreRules | None" = IgnoreRules.for_root(root_dir)
    except Exception:
        ignore = None

    excluded: frozenset[str] = _DEFAULT_EXCLUDED_DIRS
    if excluded_dirs:
        excluded = excluded | frozenset(excluded_dirs)

    root = Path(root_dir).resolve()
    chunks: list[Chunk] = []

    for dirpath, dirnames, filenames in os.walk(root):
        if ignore is not None:
            # Prune directories that IgnoreRules would skip.
            kept: list[str] = []
            for d in dirnames:
                ok, _ = ignore.should_index(str(Path(dirpath) / d))
                if ok:
                    kept.append(d)
            dirnames[:] = kept
        else:
            dirnames[:] = [d for d in dirnames if d not in excluded]

        for filename in filenames:
            full_path = Path(dirpath) / filename
            try:
                rel_path = full_path.relative_to(root).as_posix()
            except ValueError:
                rel_path = str(full_path)

            if ignore is not None:
                ok, _ = ignore.should_index(str(full_path))
                if not ok:
                    continue

            language = detect_language(str(full_path))
            if language is None:
                continue

            try:
                adapter = get_adapter(language)
            except ValueError:
                continue

            try:
                source = full_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            try:
                file_chunks = adapter.extract_chunks(rel_path, source)
            except Exception:
                continue

            chunks.extend(file_chunks)

    return chunks


def embed_chunks(chunks: list[Chunk], batch_size: int = 64) -> list[list[float]]:
    """Embed all chunks in batches.

    The text fed to the embedder for each chunk is::

        "{chunk.symbol_path}\\n{chunk.source[:500]}"

    Parameters
    ----------
    chunks:
        Chunks to embed.
    batch_size:
        Number of texts to embed per API/model call. Keeps memory bounded for
        large codebases and respects token-rate limits on hosted embedders.

    Returns
    -------
    list[list[float]]
        Embedding vectors in the same order as *chunks*.
    """
    if not chunks:
        return []

    texts = [f"{c.symbol_path}\n{c.source[:500]}" for c in chunks]
    config = get_embedder_config()

    all_vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        vectors = embed(batch, config)
        all_vectors.extend(vectors)

    return all_vectors


