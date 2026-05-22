"""Move/rename arbiter — decides between MOVED, LLM-escalation, and EVICT+INTRODUCE.

Two-pass design (RefDiff-2-style, Silva & Valente, TSE 2020):
  Pass A (intra-file): GumTree matching on each (old, new) file pair.
  Pass B (cross-file):  For unmatched deletions and additions, build candidate
                        pairs by skeleton-hash equality or minhash Jaccard ≥ 0.6,
                        then score and assign the best unique mapping.

Decision thresholds:
  ≥ 0.85  → MOVED directly (no LLM).
  0.55–0.85 → escalate to LLM with both snippets + evidence.
  < 0.55  → independent EVICT + INTRODUCE.

These align with RefactoringMiner's statement-mapping threshold rationale
(Tsantalis et al., TSE 2020).
"""

from __future__ import annotations

from dataclasses import dataclass, field


MOVED_THRESHOLD = 0.85
SIMILAR_THRESHOLD = 0.55


@dataclass
class MatchResult:
    old_file: str
    old_symbol_path: str
    new_file: str
    new_symbol_path: str
    score: float
    action: str   # "moved" | "similar" | "independent"
    evidence: dict = field(default_factory=dict)


def match_chunk_sets(
    removed_chunks: list[dict],
    added_chunks: list[dict],
    language_adapter=None,
) -> list[MatchResult]:
    """Match removed chunks to added chunks across all files.

    removed_chunks / added_chunks: list of dicts with at minimum
        {symbol_path, file, source}.

    Returns MatchResult list.  Unmatched removals stay as EVICT.
    Unmatched additions stay as INTRODUCE.
    """
    if not removed_chunks or not added_chunks:
        return []

    # --- Pass A: intra-file (same-file pairs only) ---
    same_file_matches = _intra_file_pass(removed_chunks, added_chunks, language_adapter)

    matched_old: set[tuple[str, str]] = set()
    matched_new: set[tuple[str, str]] = set()
    results: list[MatchResult] = []

    for m in same_file_matches:
        if m.action in ("moved", "similar"):
            results.append(m)
            matched_old.add((m.old_file, m.old_symbol_path))
            matched_new.add((m.new_file, m.new_symbol_path))

    # --- Pass B: cross-file (unmatched chunks) ---
    unmatched_removed = [
        c for c in removed_chunks
        if (c["file"], c["symbol_path"]) not in matched_old
    ]
    unmatched_added = [
        c for c in added_chunks
        if (c["file"], c["symbol_path"]) not in matched_new
    ]

    cross_matches = _cross_file_pass(unmatched_removed, unmatched_added, language_adapter)
    results.extend(cross_matches)

    return results


# ---------------------------------------------------------------------------
# Internal passes
# ---------------------------------------------------------------------------

def _intra_file_pass(
    removed: list[dict],
    added: list[dict],
    adapter,
) -> list[MatchResult]:
    """Run GumTree matching per file for same-file chunk pairs."""
    from codoc.core.chunk_matching.gumtree import match_file_chunks

    by_file_removed: dict[str, list[dict]] = {}
    by_file_added: dict[str, list[dict]] = {}
    for c in removed:
        by_file_removed.setdefault(c["file"], []).append(c)
    for c in added:
        by_file_added.setdefault(c["file"], []).append(c)

    results: list[MatchResult] = []
    for file in set(by_file_removed) & set(by_file_added):
        matches = match_file_chunks(
            by_file_removed[file], by_file_added[file],
            language_adapter=adapter,
        )
        for m in matches:
            if m.match_kind in ("moved", "similar"):
                action = "moved" if m.score >= MOVED_THRESHOLD else "similar"
                results.append(MatchResult(
                    old_file=file, old_symbol_path=m.old_symbol_path,
                    new_file=file, new_symbol_path=m.new_symbol_path,
                    score=m.score, action=action,
                    evidence={"match_kind": m.match_kind},
                ))
    return results


def _cross_file_pass(
    removed: list[dict],
    added: list[dict],
    adapter,
) -> list[MatchResult]:
    """RefDiff-2-style cross-file matching by skeleton + minhash candidate pairs."""
    if not removed or not added:
        return []

    from codoc.core.chunk_matching.gumtree import (
        _get_or_compute_skeleton, _get_or_compute_minhash, _combined_score
    )
    from codoc.core.chunk_matching.minhash import minhash_jaccard
    from codoc.core.chunk_matching.skeleton import skeleton_distance

    # Pre-compute for all chunks.
    removed_skels = {(c["file"], c["symbol_path"]): _get_or_compute_skeleton(c, adapter)
                     for c in removed}
    added_skels = {(c["file"], c["symbol_path"]): _get_or_compute_skeleton(c, adapter)
                   for c in added}
    removed_mh = {(c["file"], c["symbol_path"]): _get_or_compute_minhash(c, adapter)
                  for c in removed}
    added_mh = {(c["file"], c["symbol_path"]): _get_or_compute_minhash(c, adapter)
                for c in added}

    # Candidate pairs: skeleton-hash equality OR minhash Jaccard ≥ 0.6.
    candidates: list[tuple[dict, dict, float]] = []
    for old_c in removed:
        ok = (old_c["file"], old_c["symbol_path"])
        for new_c in added:
            nk = (new_c["file"], new_c["symbol_path"])
            mh_sim = minhash_jaccard(removed_mh[ok], added_mh[nk])
            if mh_sim < 0.6:
                # Quick pre-filter: skip pairs that are too dissimilar.
                continue
            score = _combined_score(
                removed_skels[ok], added_skels[nk],
                removed_mh[ok], added_mh[nk],
            )
            if score >= SIMILAR_THRESHOLD:
                candidates.append((old_c, new_c, score))

    candidates.sort(key=lambda x: x[2], reverse=True)

    assigned_old: set[tuple[str, str]] = set()
    assigned_new: set[tuple[str, str]] = set()
    results: list[MatchResult] = []

    for old_c, new_c, score in candidates:
        ok = (old_c["file"], old_c["symbol_path"])
        nk = (new_c["file"], new_c["symbol_path"])
        if ok in assigned_old or nk in assigned_new:
            continue
        action = "moved" if score >= MOVED_THRESHOLD else "similar"
        results.append(MatchResult(
            old_file=old_c["file"], old_symbol_path=old_c["symbol_path"],
            new_file=new_c["file"], new_symbol_path=new_c["symbol_path"],
            score=score, action=action,
            evidence={"cross_file": True},
        ))
        assigned_old.add(ok)
        assigned_new.add(nk)

    return results
