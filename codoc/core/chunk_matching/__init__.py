"""MinHash sketch + Jaccard (Broder 1997), used by ``tree_walk`` and the index
schema to fingerprint chunks. The RefDiff move-matcher was removed in the
rewrite — the single LLM tree-update pass subsumes move/fracture/coalesce."""

from codoc.core.chunk_matching.minhash import minhash_jaccard, minhash_sketch

__all__ = ["minhash_jaccard", "minhash_sketch"]
