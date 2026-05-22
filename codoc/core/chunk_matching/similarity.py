"""Token-level similarity helpers used by the health reconciler and arbiter."""

from __future__ import annotations


def token_jaccard(fp_a: str, fp_b: str) -> float:
    """Approximate Jaccard similarity from two SHA-256 fingerprints.

    Since we can't reverse the hash, this falls back to comparing the hex
    strings at nibble level — not a true Jaccard but a stable distance proxy
    for the reconciler's rough severity estimate.

    For real similarity, use the arbiter which computes over token streams.
    """
    if fp_a == fp_b:
        return 1.0
    if not fp_a or not fp_b:
        return 0.0
    # Nibble-level Jaccard between two hex strings of equal length.
    length = min(len(fp_a), len(fp_b))
    matches = sum(a == b for a, b in zip(fp_a[:length], fp_b[:length]))
    return matches / length


def embedding_cosine(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity between two embedding vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = sum(x * x for x in vec_a) ** 0.5
    mag_b = sum(x * x for x in vec_b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def minhash_jaccard(a: bytes, b: bytes) -> float:
    from codoc.core.chunk_matching.minhash import minhash_jaccard as _mj
    return _mj(a, b)
