"""AST skeleton extraction for chunk identity.

The skeleton is the sequence of node *types* (not values) from the tree-sitter
parse tree, with comment nodes excluded.  Two functions that have the same
structural shape (same control flow, same number of parameters, etc.) will
have similar skeletons even after heavy identifier renaming.

Inspired by the top-down anchor phase of GumTree (Falleri et al., ASE 2014):
anchoring is done by hash-equal subtrees, which requires stripping identifiers.
"""

from __future__ import annotations

import hashlib


_IDENTIFIER_LIKE = frozenset({
    "identifier", "string", "integer", "float", "comment",
    "string_content", "escape_sequence",
    "type_identifier", "field_identifier",
})


def extract_skeleton(source: str, language_adapter) -> list[str]:
    """Return the ordered node-type sequence for *source* (identifiers stripped).

    Comments are excluded (per language_adapter.comment_node_kinds).
    Leaf nodes whose type is in _IDENTIFIER_LIKE are replaced with their
    type name only (value stripped) so the skeleton is rename-invariant.
    """
    comment_kinds: frozenset[str] = frozenset(
        getattr(language_adapter, "comment_node_kinds", ())
    )
    try:
        tree = language_adapter.parse(source)
    except Exception:
        return source.split()  # fallback: token sequence

    types: list[str] = []
    _walk(tree.root_node, comment_kinds, types)
    return types


def skeleton_hash(source: str, language_adapter) -> str:
    """SHA-256 of the structural skeleton — rename-invariant chunk fingerprint."""
    types = extract_skeleton(source, language_adapter)
    return hashlib.sha256(" ".join(types).encode("utf-8")).hexdigest()


def skeleton_distance(a: list[str], b: list[str]) -> float:
    """Normalised edit distance between two skeleton sequences in [0, 1].

    Uses a fast Jaccard approximation on bigrams rather than full LCS for O(n)
    performance.  Exact edit distance (APTED — Pawlik & Augsten, Inf. Syst. 2016)
    would give a tighter bound but is too slow for real-time use.
    """
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    # Bigram multisets.
    def bigrams(seq: list[str]) -> dict[tuple[str, str], int]:
        counts: dict[tuple[str, str], int] = {}
        for i in range(len(seq) - 1):
            k = (seq[i], seq[i + 1])
            counts[k] = counts.get(k, 0) + 1
        return counts

    ab = bigrams(a)
    bb = bigrams(b)
    all_keys = set(ab) | set(bb)
    intersection = sum(min(ab.get(k, 0), bb.get(k, 0)) for k in all_keys)
    union = sum(max(ab.get(k, 0), bb.get(k, 0)) for k in all_keys)
    return 1.0 - (intersection / union if union > 0 else 0.0)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _walk(node, comment_kinds: frozenset[str], out: list[str]) -> None:
    if node.type in comment_kinds:
        return
    if node.child_count == 0:
        if node.type not in _IDENTIFIER_LIKE:
            out.append(node.type)
        else:
            out.append(f"<{node.type}>")
        return
    out.append(f"({node.type}")
    for child in node.children:
        _walk(child, comment_kinds, out)
    out.append(")")
