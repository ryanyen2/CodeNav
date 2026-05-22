"""GumTree-inspired intra-file chunk matcher.

Given the old and new chunk sets for a single file, produces a mapping
old_chunk → new_chunk for chunks that appear to be the same (possibly renamed
or lightly modified).

Algorithm outline (Falleri, Morandat, Blanc, Martinez, Monperrus, ASE 2014):
  1. Top-down phase: match chunks whose skeleton hashes are identical.
  2. Bottom-up phase: for unmatched old chunks, find the best candidate among
     unmatched new chunks by (skeleton_distance + minhash_jaccard) combined score.

This is an intra-file implementation only.  Cross-file moves are handled by
the arbiter (which uses the Code Structure Tree approach from RefDiff 2,
Silva & Valente, TSE 2020).

Each "chunk" here is a dict with at minimum:
    {symbol_path, source, file}
plus optional pre-computed fields:
    {skeleton_hash, minhash_sketch}
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChunkMatch:
    old_symbol_path: str
    new_symbol_path: str
    score: float          # 0..1, higher = more confident match
    match_kind: str       # "exact_skeleton" | "similar" | "weak"


def match_file_chunks(
    old_chunks: list[dict],
    new_chunks: list[dict],
    language_adapter=None,
    *,
    similar_threshold: float = 0.55,
    moved_threshold: float = 0.85,
) -> list[ChunkMatch]:
    """Match old chunks to new chunks within the same file.

    Returns a list of ChunkMatch objects.  Unmatched old chunks = removed.
    Unmatched new chunks = added.

    Parameters
    ----------
    old_chunks, new_chunks:
        Each entry must have at minimum 'symbol_path' and 'source'.
    language_adapter:
        Optional adapter used to compute skeleton/minhash on the fly if not
        pre-computed.
    similar_threshold:
        Minimum combined score for a "similar" (LLM-escalation) match.
    moved_threshold:
        Minimum combined score for a direct "moved" match (no LLM needed).
    """
    if not old_chunks or not new_chunks:
        return []

    old_by_sp = {c["symbol_path"]: c for c in old_chunks}
    new_by_sp = {c["symbol_path"]: c for c in new_chunks}

    # Phase 1: exact symbol_path match (fast-path — same position).
    matched_old: set[str] = set()
    matched_new: set[str] = set()
    results: list[ChunkMatch] = []

    for sp, old_c in old_by_sp.items():
        if sp in new_by_sp:
            results.append(ChunkMatch(sp, sp, 1.0, "exact_skeleton"))
            matched_old.add(sp)
            matched_new.add(sp)

    # Phase 2: skeleton + minhash matching for unmatched pairs.
    unmatched_old = [c for sp, c in old_by_sp.items() if sp not in matched_old]
    unmatched_new = [c for sp, c in new_by_sp.items() if sp not in matched_new]

    if not unmatched_old or not unmatched_new:
        return results

    # Compute skeleton hashes for unmatched chunks.
    old_skels = {c["symbol_path"]: _get_or_compute_skeleton(c, language_adapter)
                 for c in unmatched_old}
    new_skels = {c["symbol_path"]: _get_or_compute_skeleton(c, language_adapter)
                 for c in unmatched_new}
    old_mh = {c["symbol_path"]: _get_or_compute_minhash(c, language_adapter)
              for c in unmatched_old}
    new_mh = {c["symbol_path"]: _get_or_compute_minhash(c, language_adapter)
              for c in unmatched_new}

    # Phase 3: for each unmatched old, find the best scoring unmatched new.
    best_matches: list[tuple[str, str, float]] = []  # (old_sp, new_sp, score)

    for old_c in unmatched_old:
        old_sp = old_c["symbol_path"]
        best_sp: str | None = None
        best_score = 0.0

        for new_c in unmatched_new:
            new_sp = new_c["symbol_path"]
            score = _combined_score(
                old_skels[old_sp], new_skels[new_sp],
                old_mh[old_sp], new_mh[new_sp],
            )
            if score > best_score:
                best_score = score
                best_sp = new_sp

        if best_sp is not None and best_score >= similar_threshold:
            best_matches.append((old_sp, best_sp, best_score))

    # Greedily assign best matches (highest score first, no double-assignment).
    best_matches.sort(key=lambda x: x[2], reverse=True)
    assigned_old: set[str] = set()
    assigned_new: set[str] = set()

    for old_sp, new_sp, score in best_matches:
        if old_sp in assigned_old or new_sp in assigned_new:
            continue
        kind = "moved" if score >= moved_threshold else "similar"
        results.append(ChunkMatch(old_sp, new_sp, score, kind))
        assigned_old.add(old_sp)
        assigned_new.add(new_sp)

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_or_compute_skeleton(chunk: dict, adapter) -> list[str]:
    if "skeleton" in chunk:
        return chunk["skeleton"]
    if adapter is None:
        return chunk.get("source", "").split()
    try:
        from codoc.core.chunk_matching.skeleton import extract_skeleton
        return extract_skeleton(chunk.get("source", ""), adapter)
    except Exception:
        return chunk.get("source", "").split()


def _get_or_compute_minhash(chunk: dict, adapter) -> bytes:
    if "minhash_sketch" in chunk and isinstance(chunk["minhash_sketch"], bytes):
        return chunk["minhash_sketch"]
    tokens = _get_or_compute_skeleton(chunk, adapter)
    try:
        from codoc.core.chunk_matching.minhash import minhash_sketch
        return minhash_sketch(tokens)
    except Exception:
        return b""


def _combined_score(skel_a: list[str], skel_b: list[str],
                    mh_a: bytes, mh_b: bytes) -> float:
    """Weighted combination of skeleton similarity and minhash Jaccard."""
    from codoc.core.chunk_matching.skeleton import skeleton_distance
    from codoc.core.chunk_matching.minhash import minhash_jaccard

    skel_sim = 1.0 - skeleton_distance(skel_a, skel_b)
    mh_sim = minhash_jaccard(mh_a, mh_b) if mh_a and mh_b else skel_sim

    # Weight: 60% skeleton (structural), 40% minhash (token content).
    return 0.6 * skel_sim + 0.4 * mh_sim
