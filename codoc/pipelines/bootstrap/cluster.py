"""
codoc.pipelines.bootstrap.cluster — chunk extraction, embedding, and clustering.

Phase 1 of bootstrap:
  1. Walk the repo and extract code chunks via language adapters.
  2. Embed each chunk via codoc.config.embed().
  3. Cluster embeddings with FAISS k-means so each cluster becomes one candidate feature.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np
import faiss

from codoc.lang import get_adapter, detect_language, Chunk
from codoc.config import embed, get_embedder_config


@dataclass
class ClusterNode:
    """A node in the recursive cluster tree."""

    chunk_indices: list[int]
    children: list["ClusterNode"] = field(default_factory=list)
    depth: int = 0

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0


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


def cluster_chunks(
    chunks: list[Chunk],
    vectors: list[list[float]],
    target_cluster_size: int = 8,
) -> list[list[int]]:
    """Cluster chunks by embedding similarity using FAISS k-means.

    Parameters
    ----------
    chunks:
        The source chunks (used only for length; indices are returned).
    vectors:
        Embedding vectors parallel to *chunks*.
    target_cluster_size:
        Desired average number of chunks per cluster. The actual cluster count
        is ``k = max(1, len(chunks) // target_cluster_size)``.

    Returns
    -------
    list[list[int]]
        A list of *k* clusters, each being a list of chunk indices belonging to
        that cluster. Empty clusters are omitted.
    """
    n = len(chunks)
    if n == 0:
        return []
    if len(vectors) != n:
        raise ValueError(
            f"chunks ({n}) and vectors ({len(vectors)}) must have the same length"
        )

    k = max(1, n // target_cluster_size)

    # Build the float32 matrix that FAISS expects.
    mat = np.array(vectors, dtype=np.float32)
    d = mat.shape[1]

    # Clamp k so it never exceeds the number of data points.
    k = min(k, n)

    kmeans = faiss.Kmeans(d, k, niter=20, verbose=False)
    kmeans.train(mat)

    # Assign each vector to its nearest centroid.
    _distances, labels = kmeans.index.search(mat, 1)
    labels = labels.flatten().tolist()

    # Group chunk indices by cluster label.
    clusters: dict[int, list[int]] = {}
    for chunk_idx, label in enumerate(labels):
        clusters.setdefault(label, []).append(chunk_idx)

    # Return in deterministic label order; omit any empty clusters.
    return [clusters[label] for label in sorted(clusters) if clusters[label]]


def cluster_recursive(
    chunks: list[Chunk],
    vectors: list[list[float]],
    target_leaf_size: int = 8,
    max_depth: int = 5,
    branching_factor: int | None = None,
    _depth: int = 0,
    _indices: list[int] | None = None,
) -> ClusterNode:
    """Recursively cluster *chunks* by embedding similarity.

    Produces a :class:`ClusterNode` tree of arbitrary depth.  Recursion stops
    when a node has ≤ *target_leaf_size* chunks or *max_depth* is reached.

    Parameters
    ----------
    chunks:
        Full list of all extracted chunks (indexed by position).
    vectors:
        Embedding vectors parallel to *chunks*.
    target_leaf_size:
        Stop splitting a node when it contains ≤ this many chunks.
    max_depth:
        Hard recursion limit.
    branching_factor:
        Number of child clusters per split.  Defaults to
        ``max(2, ceil(n / target_leaf_size))`` capped at 10.
    _depth / _indices:
        Internal recursion parameters — do not pass.

    Returns
    -------
    ClusterNode
        Root node whose ``chunk_indices`` contains *all* chunk indices and
        whose ``children`` recursively contain sub-nodes.
    """
    import math

    if _indices is None:
        _indices = list(range(len(chunks)))

    root = ClusterNode(chunk_indices=_indices, depth=_depth)

    n = len(_indices)
    if n == 0:
        return root

    # Base cases: too small to split further or maximum depth reached.
    if n <= target_leaf_size or _depth >= max_depth:
        return root

    # Choose k (branching factor for this level).
    if branching_factor is not None:
        k = branching_factor
    else:
        # Broader at top levels (smaller k), finer at deeper levels.
        k = max(2, min(10, math.ceil(n / target_leaf_size)))

    k = min(k, n)

    sub_mat = np.array([vectors[i] for i in _indices], dtype=np.float32)
    d = sub_mat.shape[1]

    kmeans = faiss.Kmeans(d, k, niter=20, verbose=False)
    kmeans.train(sub_mat)
    _, labels = kmeans.index.search(sub_mat, 1)
    labels = labels.flatten().tolist()

    # Group local indices by cluster label.
    child_groups: dict[int, list[int]] = {}
    for local_idx, label in enumerate(labels):
        child_groups.setdefault(label, []).append(_indices[local_idx])

    for label in sorted(child_groups):
        group = child_groups[label]
        if not group:
            continue
        child = cluster_recursive(
            chunks=chunks,
            vectors=vectors,
            target_leaf_size=target_leaf_size,
            max_depth=max_depth,
            branching_factor=branching_factor,
            _depth=_depth + 1,
            _indices=group,
        )
        root.children.append(child)

    return root


def cluster_hierarchical(
    chunks: list[Chunk],
    vectors: list[list[float]],
    n_chapters: int = 8,
    target_leaf_size: int = 8,
) -> list[list[list[int]]]:
    """Two-level hierarchical clustering: chapters → sections → chunk indices.

    Outer pass: k-means with ``k = n_chapters`` → chapter clusters.
    Inner pass: for each chapter cluster, sub-cluster with
    ``k = max(1, len(chapter_indices) // target_leaf_size)``.

    Returns
    -------
    list[list[list[int]]]
        chapters → sections → chunk indices.
        ``result[c][s]`` is a list of chunk indices in chapter *c*, section *s*.
    """
    n = len(chunks)
    if n == 0:
        return []

    mat = np.array(vectors, dtype=np.float32)
    d = mat.shape[1]

    # Outer: chapter clusters.
    k_chapters = min(n_chapters, n)
    kmeans_outer = faiss.Kmeans(d, k_chapters, niter=20, verbose=False)
    kmeans_outer.train(mat)
    _, outer_labels = kmeans_outer.index.search(mat, 1)
    outer_labels = outer_labels.flatten().tolist()

    chapter_indices: dict[int, list[int]] = {}
    for idx, label in enumerate(outer_labels):
        chapter_indices.setdefault(label, []).append(idx)

    result: list[list[list[int]]] = []
    for chapter_label in sorted(chapter_indices):
        ch_indices = chapter_indices[chapter_label]
        k_sections = max(1, len(ch_indices) // target_leaf_size)
        k_sections = min(k_sections, len(ch_indices))

        if k_sections == 1:
            result.append([ch_indices])
            continue

        ch_mat = np.array([vectors[i] for i in ch_indices], dtype=np.float32)
        kmeans_inner = faiss.Kmeans(d, k_sections, niter=20, verbose=False)
        kmeans_inner.train(ch_mat)
        _, inner_labels = kmeans_inner.index.search(ch_mat, 1)
        inner_labels = inner_labels.flatten().tolist()

        sections: dict[int, list[int]] = {}
        for local_idx, label in enumerate(inner_labels):
            sections.setdefault(label, []).append(ch_indices[local_idx])

        result.append([sections[l] for l in sorted(sections) if sections[l]])

    return result
