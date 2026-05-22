"""Unified chunk matcher — move and rename detection in one pass.

Two-pass design (RefDiff-2-style, Silva & Valente, TSE 2020):
  Pass A (intra-file): same-file pairs filtered by ``candidate_filter``.
  Pass B (cross-file): unmatched pairs filtered by minhash Jaccard floor.

Decision thresholds (MatchingThresholds):
  score ≥ moved_threshold   → "moved"  (no LLM).
  score ≥ similar_threshold → "similar" (escalate to LLM).
  score <  similar_threshold → "different" (independent EVICT + INTRODUCE).

Scoring: ``skeleton_weight * type_jaccard + minhash_weight * minhash_jaccard``
where type_jaccard uses bigram skeleton distance and minhash_jaccard uses the
stored MinHash sketches.

Replaces arbiter.py + gumtree.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class MatchingThresholds:
    moved_threshold: float = 0.85
    similar_threshold: float = 0.55
    candidate_minhash_floor: float = 0.6
    skeleton_weight: float = 0.6
    minhash_weight: float = 0.4


@dataclass
class Match:
    old_file: str
    old_symbol_path: str
    new_file: str
    new_symbol_path: str
    score: float
    verdict: str   # "moved" | "similar" | "different"
    evidence: dict = field(default_factory=dict)


# Backward-compatibility alias used by reflective runner.
MatchResult = Match


def score(removed_chunk: dict, added_chunk: dict,
          *, adapter=None, thresholds: MatchingThresholds | None = None) -> float:
    """Combined similarity score between two chunks in [0, 1].

    Uses ``skeleton_weight * type_jaccard + minhash_weight * minhash_jaccard``.
    """
    t = thresholds or MatchingThresholds()
    skel_a = _get_or_compute_skeleton(removed_chunk, adapter)
    skel_b = _get_or_compute_skeleton(added_chunk, adapter)
    mh_a = _get_or_compute_minhash(removed_chunk, adapter)
    mh_b = _get_or_compute_minhash(added_chunk, adapter)
    return _combined_score(skel_a, skel_b, mh_a, mh_b, t)


def match_chunks(
    removed: list[dict],
    added: list[dict],
    *,
    candidate_filter: Callable[[dict, dict], bool] | None = None,
    thresholds: MatchingThresholds | None = None,
    adapter=None,
) -> list[Match]:
    """Match removed chunks to added chunks and return Match list.

    Parameters
    ----------
    removed, added:
        Each entry must have at minimum ``symbol_path``, ``file``, and ``source``.
        Optional pre-computed fields: ``skeleton``, ``minhash_sketch``.
    candidate_filter:
        If given, only pairs where ``candidate_filter(removed_chunk, added_chunk)``
        is True are considered.  Defaults to accepting all pairs.
    thresholds:
        Scoring and decision thresholds.  Defaults to ``MatchingThresholds()``.
    adapter:
        Optional tree-sitter language adapter for on-the-fly skeleton/minhash
        computation.

    Returns
    -------
    list[Match]
        Only "moved" and "similar" matches are returned.  "different" pairs are
        silent (callers treat unmatched removals as EVICT, additions as INTRODUCE).
    """
    if not removed or not added:
        return []

    t = thresholds or MatchingThresholds()

    # Pre-compute identity signals for all chunks.
    def _signals(chunks: list[dict]) -> dict[tuple[str, str], tuple[list[str], bytes]]:
        out = {}
        for c in chunks:
            k = (c["file"], c["symbol_path"])
            out[k] = (
                _get_or_compute_skeleton(c, adapter),
                _get_or_compute_minhash(c, adapter),
            )
        return out

    removed_sig = _signals(removed)
    added_sig = _signals(added)

    # Build candidate pairs applying the filter.
    candidates: list[tuple[dict, dict, float]] = []
    for old_c in removed:
        ok = (old_c["file"], old_c["symbol_path"])
        skel_a, mh_a = removed_sig[ok]
        for new_c in added:
            if candidate_filter is not None and not candidate_filter(old_c, new_c):
                continue
            nk = (new_c["file"], new_c["symbol_path"])
            skel_b, mh_b = added_sig[nk]
            s = _combined_score(skel_a, skel_b, mh_a, mh_b, t)
            if s >= t.similar_threshold:
                candidates.append((old_c, new_c, s))

    # Greedy assignment: highest score first, no double-assignment.
    candidates.sort(key=lambda x: x[2], reverse=True)
    assigned_old: set[tuple[str, str]] = set()
    assigned_new: set[tuple[str, str]] = set()
    results: list[Match] = []

    for old_c, new_c, s in candidates:
        ok = (old_c["file"], old_c["symbol_path"])
        nk = (new_c["file"], new_c["symbol_path"])
        if ok in assigned_old or nk in assigned_new:
            continue
        verdict = "moved" if s >= t.moved_threshold else "similar"
        results.append(Match(
            old_file=old_c["file"],
            old_symbol_path=old_c["symbol_path"],
            new_file=new_c["file"],
            new_symbol_path=new_c["symbol_path"],
            score=s,
            verdict=verdict,
        ))
        assigned_old.add(ok)
        assigned_new.add(nk)

    return results


def match_chunk_sets(
    removed_chunks: list[dict],
    added_chunks: list[dict],
    language_adapter=None,
    thresholds: MatchingThresholds | None = None,
) -> list[Match]:
    """Two-pass matcher (intra-file then cross-file).

    Backward-compatible entry point used by the reflective pipeline.

    Pass A: same-file pairs only.
    Pass B: cross-file pairs where minhash Jaccard ≥ candidate_minhash_floor.
    """
    if not removed_chunks or not added_chunks:
        return []

    t = thresholds or MatchingThresholds()

    # --- Pass A: intra-file ---
    intra_filter: Callable[[dict, dict], bool] = lambda r, a: r["file"] == a["file"]
    intra_results = match_chunks(
        removed_chunks, added_chunks,
        candidate_filter=intra_filter,
        thresholds=t,
        adapter=language_adapter,
    )

    matched_old: set[tuple[str, str]] = set()
    matched_new: set[tuple[str, str]] = set()
    results: list[Match] = list(intra_results)

    for m in intra_results:
        matched_old.add((m.old_file, m.old_symbol_path))
        matched_new.add((m.new_file, m.new_symbol_path))

    # --- Pass B: cross-file ---
    unmatched_removed = [
        c for c in removed_chunks
        if (c["file"], c["symbol_path"]) not in matched_old
    ]
    unmatched_added = [
        c for c in added_chunks
        if (c["file"], c["symbol_path"]) not in matched_new
    ]

    if unmatched_removed and unmatched_added:
        from codoc.core.chunk_matching.minhash import minhash_jaccard

        # Pre-compute minhash for cross-file filter.
        removed_mh: dict[tuple[str, str], bytes] = {
            (c["file"], c["symbol_path"]): _get_or_compute_minhash(c, language_adapter)
            for c in unmatched_removed
        }
        added_mh: dict[tuple[str, str], bytes] = {
            (c["file"], c["symbol_path"]): _get_or_compute_minhash(c, language_adapter)
            for c in unmatched_added
        }

        floor = t.candidate_minhash_floor

        def cross_filter(r: dict, a: dict) -> bool:
            rk = (r["file"], r["symbol_path"])
            ak = (a["file"], a["symbol_path"])
            return minhash_jaccard(removed_mh[rk], added_mh[ak]) >= floor

        cross_results = match_chunks(
            unmatched_removed, unmatched_added,
            candidate_filter=cross_filter,
            thresholds=t,
            adapter=language_adapter,
        )
        # Tag cross-file evidence.
        for m in cross_results:
            m.evidence["cross_file"] = True
        results.extend(cross_results)

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


def _combined_score(
    skel_a: list[str], skel_b: list[str],
    mh_a: bytes, mh_b: bytes,
    t: MatchingThresholds,
) -> float:
    from codoc.core.chunk_matching.skeleton import skeleton_distance
    from codoc.core.chunk_matching.minhash import minhash_jaccard

    skel_sim = 1.0 - skeleton_distance(skel_a, skel_b)
    mh_sim = minhash_jaccard(mh_a, mh_b) if mh_a and mh_b else skel_sim
    return t.skeleton_weight * skel_sim + t.minhash_weight * mh_sim
