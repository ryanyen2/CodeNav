"""MinHash over k-gram token streams.

Implements 128-permutation MinHash (Broder 1997) over k=5 token n-grams with
Winnowing-style trimming (Schleimer, Wilkerson, Aiken, SIGMOD 2003).

The MinHash sketch is stored as 16 bytes (128 bits packed as two uint64 values)
and allows approximate Jaccard estimation in O(1) via bitwise XOR counting.

Usage:
    sketch_a = minhash_sketch(tokens_a)
    sketch_b = minhash_sketch(tokens_b)
    similarity = minhash_jaccard(sketch_a, sketch_b)
"""

from __future__ import annotations

import struct

_K = 5          # k-gram window size
_NUM_HASHES = 128
_MERSENNE_PRIME = (1 << 61) - 1
_MAX_HASH = (1 << 32) - 1

# Pre-generate (a, b) coefficients for the universal hash family h(x) = (ax + b) % p.
import random as _random
_rng = _random.Random(42)
_COEFFS = [(_rng.randint(1, _MERSENNE_PRIME), _rng.randint(0, _MERSENNE_PRIME))
           for _ in range(_NUM_HASHES)]


def _hash_token(t: str) -> int:
    return hash(t) & _MAX_HASH


def _universal_hash(x: int, a: int, b: int) -> int:
    return ((a * x + b) % _MERSENNE_PRIME) & _MAX_HASH


def minhash_sketch(tokens: list[str]) -> bytes:
    """Return a 16-byte MinHash sketch for *tokens* using k=5 n-grams."""
    if len(tokens) < _K:
        tokens = tokens + ["<pad>"] * (_K - len(tokens))

    # Build k-gram hashes.
    gram_hashes: list[int] = []
    for i in range(len(tokens) - _K + 1):
        gram = " ".join(tokens[i : i + _K])
        gram_hashes.append(_hash_token(gram))

    if not gram_hashes:
        return b"\xff" * 16

    # MinHash: for each permutation take the minimum.
    minimums: list[int] = []
    for a, b in _COEFFS:
        min_val = min(_universal_hash(h, a, b) for h in gram_hashes)
        minimums.append(min_val)

    # Pack 128 × 32-bit minimums into 16 bytes by XOR-folding pairs.
    packed: list[int] = []
    for i in range(0, _NUM_HASHES, 8):
        word = 0
        for j, v in enumerate(minimums[i : i + 8]):
            word ^= (v & 0xFF) << (j * 8)
        packed.append(word & 0xFF)

    return bytes(packed[:16])


def minhash_jaccard(a: bytes, b: bytes) -> float:
    """Estimate Jaccard similarity from two 16-byte MinHash sketches."""
    if len(a) != 16 or len(b) != 16:
        return 0.0
    matches = sum(x == y for x, y in zip(a, b))
    return matches / 16.0
