"""
codoc.pipelines.bootstrap.semantic_cluster — hybrid hierarchical file clusterer.

Groups files into semantic clusters using a composite similarity signal:
  - Embedding cosine similarity (mean chunk embedding per file)
  - Import-graph Jaccard overlap (which modules each file imports)
  - Lexical overlap of identifiers in symbol paths and module docstrings

Returns a SemanticGroup tree shaped so every internal node has 3-7 children
(literature: Miller's law applied to architecture recovery).  Falls back
gracefully when embeddings are unavailable — import + lexical signals alone
produce reasonable groupings for most codebases.
"""

from __future__ import annotations

import re
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from codoc.lang import Chunk
from codoc.core.logging import get_logger

_log = get_logger(__name__)

# Composite similarity weights: embedding + import Jaccard + lexical
_W_EMBED = 0.50
_W_IMPORT = 0.30
_W_LEXICAL = 0.20


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SemanticGroup:
    """A node in the semantic cluster tree."""

    group_id: int
    file_paths: list[str]
    chunk_indices: list[int]
    children: list["SemanticGroup"] = field(default_factory=list)

    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def all_chunk_indices(self) -> list[int]:
        if self.is_leaf():
            return self.chunk_indices
        result: list[int] = []
        for child in self.children:
            result.extend(child.all_chunk_indices())
        return result


# ---------------------------------------------------------------------------
# Per-file metadata extraction
# ---------------------------------------------------------------------------


def extract_module_docstring(source: str) -> str:
    """Return the module-level docstring, or empty string."""
    try:
        tree = ast.parse(source)
        return ast.get_docstring(tree) or ""
    except Exception:
        return ""


def extract_imports(source: str) -> set[str]:
    """Return top-level module names imported by this file."""
    modules: set[str] = set()
    for m in re.finditer(r"^(?:import|from)\s+([\w.]+)", source, re.MULTILINE):
        modules.add(m.group(1).split(".")[0])
    return modules


def extract_identifiers(symbol_paths: list[str], docstring: str) -> set[str]:
    """Extract meaningful tokens from symbol paths and docstring for lexical overlap."""
    tokens: set[str] = set()
    for sp in symbol_paths:
        # Split on ::, ., _, CamelCase
        parts = re.split(r"[:/._]", sp)
        for part in parts:
            sub = re.sub(r"([A-Z])", r" \1", part).split()
            tokens.update(t.lower() for t in sub if len(t) > 2)
    # Add docstring words
    for word in re.findall(r"[a-zA-Z]{3,}", docstring):
        tokens.add(word.lower())
    return tokens


# ---------------------------------------------------------------------------
# Per-file summary computation
# ---------------------------------------------------------------------------


def _file_summaries(
    chunks: list[Chunk],
    root_dir: str,
) -> dict[str, dict]:
    """Build a summary dict per file: {embedding, imports, identifiers, chunk_indices, source}."""
    from pathlib import Path as _Path

    files: dict[str, dict] = {}
    for i, chunk in enumerate(chunks):
        file = chunk.file
        if file not in files:
            source = ""
            abs_path = _Path(root_dir) / file if root_dir else _Path(file)
            try:
                source = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
            files[file] = {
                "chunk_indices": [],
                "symbol_paths": [],
                "module_docstring": extract_module_docstring(source),
                "imports": extract_imports(source),
                "identifiers": set(),
                "source": source,
            }
        files[file]["chunk_indices"].append(i)
        files[file]["symbol_paths"].append(chunk.symbol_path)

    for file, info in files.items():
        info["identifiers"] = extract_identifiers(
            info["symbol_paths"], info["module_docstring"]
        )

    return files


# ---------------------------------------------------------------------------
# Similarity computation
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return dot / (na * nb)


def _jaccard(s1: set, s2: set) -> float:
    if not s1 and not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def _file_pair_similarity(
    file_a: str,
    file_b: str,
    summaries: dict[str, dict],
    embeddings: dict[str, Optional[list[float]]],
) -> float:
    ea = embeddings.get(file_a)
    eb = embeddings.get(file_b)
    embed_sim = _cosine(ea, eb) if (ea and eb) else 0.0

    import_sim = _jaccard(summaries[file_a]["imports"], summaries[file_b]["imports"])
    lex_sim = _jaccard(summaries[file_a]["identifiers"], summaries[file_b]["identifiers"])

    weights = _W_EMBED if (ea and eb) else 0.0
    remaining = 1.0 - weights
    # Redistribute embed weight if unavailable
    w_import = _W_IMPORT / (_W_IMPORT + _W_LEXICAL) * remaining + (weights > 0) * _W_EMBED * 0
    w_lexical = _W_LEXICAL / (_W_IMPORT + _W_LEXICAL) * remaining
    w_embed = weights

    # Simpler: when embeddings available use 3-signal mix; else 2-signal
    if ea and eb:
        return _W_EMBED * embed_sim + _W_IMPORT * import_sim + _W_LEXICAL * lex_sim
    else:
        total_w = _W_IMPORT + _W_LEXICAL
        return (_W_IMPORT * import_sim + _W_LEXICAL * lex_sim) / total_w


def _group_avg_similarity(
    group_a: list[str],
    group_b: list[str],
    sim_matrix: dict[tuple[str, str], float],
) -> float:
    """Average-linkage similarity between two groups."""
    total, count = 0.0, 0
    for fa in group_a:
        for fb in group_b:
            key = (min(fa, fb), max(fa, fb))
            total += sim_matrix.get(key, 0.0)
            count += 1
    return total / count if count > 0 else 0.0


# ---------------------------------------------------------------------------
# Hierarchical agglomerative clustering
# ---------------------------------------------------------------------------


def _hac_cluster(
    files: list[str],
    summaries: dict[str, dict],
    embeddings: dict[str, Optional[list[float]]],
    target_k: int,
) -> list[list[str]]:
    """Average-linkage HAC stopping at target_k clusters (or when merging would hurt quality)."""
    # Build pairwise similarity matrix
    sim_matrix: dict[tuple[str, str], float] = {}
    for i, fa in enumerate(files):
        for fb in files[i + 1:]:
            key = (min(fa, fb), max(fa, fb))
            sim_matrix[key] = _file_pair_similarity(fa, fb, summaries, embeddings)

    groups: list[list[str]] = [[f] for f in files]

    while len(groups) > target_k:
        best_sim = -1.0
        best_i, best_j = 0, 1
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                s = _group_avg_similarity(groups[i], groups[j], sim_matrix)
                if s > best_sim:
                    best_sim, best_i, best_j = s, i, j

        # Stop early if best merge is very low similarity (unrelated files)
        if best_sim < 0.05 and len(groups) <= target_k + 2:
            break

        merged = groups[best_i] + groups[best_j]
        groups = [g for k, g in enumerate(groups) if k not in (best_i, best_j)]
        groups.append(merged)

    return groups


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_hierarchical_clusters(
    chunks: list[Chunk],
    root_dir: str = "",
    max_fanout: int = 7,
    min_top_level: int = 3,
    *,
    file_embeddings: dict[str, "Optional[list[float]]"] | None = None,
) -> SemanticGroup:
    """Cluster *chunks* into a hierarchical SemanticGroup tree.

    Strategy:
      1. Group chunks by file; build per-file summaries (docstring, imports, identifiers).
      2. Optionally embed each file (mean of chunk embeddings).
      3. HAC to top-level clusters targeting min_top_level..max_fanout groups.
      4. For groups with > max_fanout files, recurse one level.

    Returns a root SemanticGroup whose children are the top-level clusters.
    Each leaf cluster's chunks are directly attached; each internal cluster
    owns the union of its children's chunks.
    """
    if not chunks:
        return SemanticGroup(group_id=0, file_paths=[], chunk_indices=[])

    summaries = _file_summaries(chunks, root_dir)
    files = list(summaries.keys())
    n_files = len(files)

    if n_files == 1:
        # Single file — trivial root with one leaf
        root = SemanticGroup(group_id=0, file_paths=files, chunk_indices=summaries[files[0]]["chunk_indices"])
        return root

    embeddings = (
        {f: (file_embeddings or {}).get(f) for f in summaries}
    )

    # Choose target k based on number of files
    target_k = min(max_fanout, max(min_top_level, n_files // 3))
    top_groups = _hac_cluster(files, summaries, embeddings, target_k)

    root = SemanticGroup(group_id=0, file_paths=files, chunk_indices=list(range(len(chunks))))
    group_id_counter = [1]

    for group_files in top_groups:
        group_chunk_indices: list[int] = []
        for f in group_files:
            group_chunk_indices.extend(summaries[f]["chunk_indices"])

        node = SemanticGroup(
            group_id=group_id_counter[0],
            file_paths=group_files,
            chunk_indices=group_chunk_indices,
        )
        group_id_counter[0] += 1

        # Recurse if this group is too large
        if len(group_files) > max_fanout:
            sub_target = min(max_fanout, max(min_top_level, len(group_files) // 3))
            sub_groups = _hac_cluster(group_files, summaries, embeddings, sub_target)
            for sub_files in sub_groups:
                sub_indices: list[int] = []
                for f in sub_files:
                    sub_indices.extend(summaries[f]["chunk_indices"])
                sub_node = SemanticGroup(
                    group_id=group_id_counter[0],
                    file_paths=sub_files,
                    chunk_indices=sub_indices,
                )
                group_id_counter[0] += 1
                node.children.append(sub_node)

        root.children.append(node)

    return root


def cluster_into_parents(
    groups: list[SemanticGroup],
    chunks: list[Chunk],
    root_dir: str = "",
    n_target: int = 5,
    *,
    file_embeddings: dict[str, "Optional[list[float]]"] | None = None,
) -> list[SemanticGroup]:
    """Merge leaf groups into n_target parent groups using group-level HAC.

    Clusters the *groups themselves* (not their constituent files) so each
    original group belongs to exactly one parent.  The old file-level approach
    scattered a single group's files across multiple merged parents, causing
    the same group to appear as a child of several parents and producing
    massive feature duplication downstream in ``_walk``.

    Used as a post-pass when ``build_hierarchical_clusters`` returns >6
    top-level children.  Each returned SemanticGroup wraps the original
    sub-groups as children, giving ``_walk`` a two-level tree to recurse.
    """
    if len(groups) <= n_target:
        return groups

    file_embs = file_embeddings or {}
    summaries = _file_summaries(chunks, root_dir)

    # Build one pseudo-summary and one centroid embedding per group so we can
    # reuse _hac_cluster with groups as the "files".
    group_keys: list[str] = []
    pseudo_summaries: dict[str, dict] = {}
    pseudo_embeddings: dict[str, Optional[list[float]]] = {}

    for g in groups:
        key = f"__group_{g.group_id}__"
        group_keys.append(key)

        all_imports: set = set()
        all_identifiers: set = set()
        all_chunk_indices: list[int] = []
        all_symbol_paths: list[str] = []
        for f in g.file_paths:
            if f in summaries:
                all_imports |= summaries[f]["imports"]
                all_identifiers |= summaries[f]["identifiers"]
                all_chunk_indices.extend(summaries[f]["chunk_indices"])
                all_symbol_paths.extend(summaries[f]["symbol_paths"])

        pseudo_summaries[key] = {
            "chunk_indices": all_chunk_indices,
            "symbol_paths": all_symbol_paths,
            "module_docstring": "",
            "imports": all_imports,
            "identifiers": all_identifiers,
            "source": "",
        }

        # Centroid = mean of available file embeddings for this group.
        vecs = [file_embs[f] for f in g.file_paths if file_embs.get(f)]
        if vecs:
            dim = len(vecs[0])
            centroid: list[float] = [
                sum(v[i] for v in vecs) / len(vecs) for i in range(dim)
            ]
            pseudo_embeddings[key] = centroid
        else:
            pseudo_embeddings[key] = None

    # Cluster groups (not files) — each group_key maps 1:1 to one parent bucket.
    merged_key_lists = _hac_cluster(
        group_keys, pseudo_summaries, pseudo_embeddings, n_target
    )

    key_to_group: dict[str, SemanticGroup] = {
        f"__group_{g.group_id}__": g for g in groups
    }
    next_id = max((g.group_id for g in groups), default=0) + 1
    parent_groups: list[SemanticGroup] = []

    for merged_keys in merged_key_lists:
        child_groups = [key_to_group[k] for k in merged_keys if k in key_to_group]
        all_chunk_indices = []
        all_file_paths: list[str] = []
        for cg in child_groups:
            all_chunk_indices.extend(cg.all_chunk_indices())
            all_file_paths.extend(cg.file_paths)

        parent = SemanticGroup(
            group_id=next_id,
            file_paths=all_file_paths,
            chunk_indices=all_chunk_indices,
            children=child_groups,
        )
        next_id += 1
        parent_groups.append(parent)

    return parent_groups


def build_cluster_input(
    group: SemanticGroup,
    chunks: list[Chunk],
    root_dir: str = "",
    max_snippet_chars: int = 600,
) -> "ClusterInput":
    """Convert a SemanticGroup → ClusterInput for the LLM agent."""
    from codoc.agents.bootstrap_clustering import ClusterInput
    from pathlib import Path as _Path

    # Build per-file metadata cache once
    summaries = _file_summaries(chunks, root_dir)

    chunk_dicts: list[dict] = []
    seen_symbol_paths: set[str] = set()

    for i in group.chunk_indices:
        chunk = chunks[i]
        sp = chunk.symbol_path
        if sp in seen_symbol_paths:
            continue
        seen_symbol_paths.add(sp)

        info = summaries.get(chunk.file, {})
        chunk_dicts.append({
            "symbol_path": sp,
            "file": chunk.file,
            "source_snippet": chunk.source[:max_snippet_chars],
            "module_docstring": info.get("module_docstring", ""),
            "imports": sorted(info.get("imports", set())),
        })

    return ClusterInput(chunks=chunk_dicts, cluster_id=group.group_id)
