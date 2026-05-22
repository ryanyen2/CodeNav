"""Chunk identity matching for move/rename detection.

Algorithm references:
- GumTree (Falleri et al., ASE 2014) — intra-file AST-diff matcher.
- RefDiff 2 (Silva & Valente, TSE 2020) — cross-file Code Structure Tree matching.
- Winnowing (Schleimer, Wilkerson, Aiken, SIGMOD 2003) + MinHash (Broder 1997).
"""

from codoc.core.chunk_matching.arbiter import MatchResult, match_chunk_sets
from codoc.core.chunk_matching.similarity import token_jaccard, minhash_jaccard

__all__ = ["MatchResult", "match_chunk_sets", "token_jaccard", "minhash_jaccard"]
