"""Structural tree-alignment helpers for identity resolution during sync.

When the title-path and slug-path lookups in the parser fail (e.g. because
multiple features were renamed simultaneously), this module provides
structural fallback matching based on sibling position and edit distance.

Public API
----------
resolve_uuid_structural(title, parent_uuid, sibling_index_new, old_meta) -> str | None
_compute_feature_hash(title, intent, parent_uuid, retired) -> str
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codoc.projection.meta import TreeMeta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _title_norm_hash(title: str) -> str:
    """sha1 of title lowercased, stripped, with non-alphanumeric chars removed."""
    normalised = re.sub(r"[^a-z0-9 ]", "", title.lower().strip())
    return hashlib.sha1(normalised.encode()).hexdigest()


def _levenshtein(a: str, b: str) -> int:
    """Simple DP Levenshtein distance."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    # Use two rows to save memory.
    prev = list(range(lb + 1))
    curr = [0] * (lb + 1)
    for i in range(1, la + 1):
        curr[0] = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, [0] * (lb + 1)
    return prev[lb]


def _title_to_slug(title: str) -> str:
    """Normalise a title to a lowercase-hyphen slug."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _get_old_siblings(
    parent_uuid: str | None,
    old_meta: "TreeMeta",
) -> list[dict]:
    """Return uuid_to_location entries that are direct children of *parent_uuid*."""
    siblings: list[dict] = []
    for uuid, loc in old_meta.uuid_to_location.items():
        if loc.get("kind") != "feature":
            continue
        if loc.get("parent_uuid") == parent_uuid:
            siblings.append({"uuid": uuid, **loc})
    siblings.sort(key=lambda s: s.get("sibling_index", 0))
    return siblings


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def build_sibling_index(old_meta: "TreeMeta") -> "dict[str | None, list[dict]]":
    """Build a parent_uuid → sorted-children map in O(N) for O(1) lookup per call.

    Call this once per sync cycle and pass the result to resolve_uuid_structural
    to avoid O(N×D) repeated full-dict scans.
    """
    index: dict[str | None, list[dict]] = {}
    for uuid, loc in old_meta.uuid_to_location.items():
        if loc.get("kind") != "feature":
            continue
        parent = loc.get("parent_uuid")
        index.setdefault(parent, []).append({"uuid": uuid, **loc})
    for siblings in index.values():
        siblings.sort(key=lambda s: s.get("sibling_index", 0))
    return index


def resolve_uuid_structural(
    title: str,
    parent_uuid: str | None,
    sibling_index_new: int,
    old_meta: "TreeMeta",
    prebuilt_sibling_index: "dict | None" = None,
) -> str | None:
    """Find the best-matching UUID from *old_meta* for a parsed feature.

    Parameters
    ----------
    title:
        The display title parsed from the .codoc file.
    parent_uuid:
        The resolved UUID of the parent feature (None for root features).
    sibling_index_new:
        0-based position among siblings seen so far under *parent_uuid*.
    old_meta:
        TreeMeta sidecar from the last render.

    Returns
    -------
    UUID string if matched with confidence, None otherwise.

    Matching
    --------
    One scoring pass over all siblings: each candidate gets a score in [0, 1]
    combining exact-title, slug-normalisation, and Levenshtein similarity
    (weighted by sibling-position agreement).  The best candidate is returned
    if its score ≥ 0.6; ties and ambiguous exact matches return None.

    Score formula:
      - Exact title match (case-insensitive)  → score = 1.0
      - Exact slug match                      → score = 0.9
      - Levenshtein similarity ≥ 0.6 with same sibling_index  → similarity score
      - All other cases                        → score = 0.0 (filtered)
    """
    if prebuilt_sibling_index is not None:
        siblings = prebuilt_sibling_index.get(parent_uuid, [])
    else:
        siblings = _get_old_siblings(parent_uuid, old_meta)
    if not siblings:
        # The new parent had no children in the previous render — the feature
        # may have moved here from somewhere else.  Try cross-tree matching.
        return _resolve_uuid_cross_tree(title, old_meta)

    title_stripped = title.strip().lower()
    title_as_slug = _title_to_slug(title)
    t1 = title.strip()

    _MIN_SCORE = 0.6

    scored: list[tuple[float, str]] = []  # (score, uuid)

    for sib in siblings:
        uuid = sib["uuid"]
        stored_title = sib.get("title", "").strip().lower()
        stored_slug = sib.get("slug", "")
        t2 = sib.get("title", "").strip()

        if stored_title == title_stripped:
            scored.append((1.0, uuid))
            continue

        if stored_slug == title_as_slug:
            scored.append((0.9, uuid))
            continue

        # Levenshtein similarity for same-position siblings.
        if sib.get("sibling_index") == sibling_index_new:
            max_len = max(len(t1), len(t2))
            if max_len == 0:
                scored.append((0.8, uuid))
                continue
            dist = _levenshtein(t1.lower(), t2.lower())
            sim = 1.0 - dist / max_len
            if sim >= _MIN_SCORE:
                scored.append((sim, uuid))

    if not scored:
        # Fallback: feature may have been moved to a new parent.  Scan ALL
        # features in old_meta for a unique exact-title match across the tree.
        # Only return a hit if exactly one feature in the old tree had that
        # title — otherwise the move is ambiguous and we fall through to
        # INTRODUCE+RETIRE to be safe.
        return _resolve_uuid_cross_tree(title, old_meta)

    # Sort by score descending.
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_uuid = scored[0]

    # Reject if ambiguous at the top score.
    top_matches = [u for s, u in scored if s == best_score]
    if len(top_matches) > 1:
        return None

    return best_uuid


def _resolve_uuid_cross_tree(title: str, old_meta: "TreeMeta") -> str | None:
    """Last-resort: globally unique title match across the previous tree.

    Used when a feature moved between parents, so the same-parent resolver
    can't find it.  Title matching is case-insensitive after stripping.
    Returns None when there are zero or multiple matches (ambiguous).
    """
    title_stripped = title.strip().lower()
    title_as_slug = _title_to_slug(title)
    matches: list[str] = []
    for uuid, loc in old_meta.uuid_to_location.items():
        if loc.get("kind") != "feature":
            continue
        stored_title = loc.get("title", "").strip().lower()
        stored_slug = loc.get("slug", "")
        if stored_title == title_stripped or stored_slug == title_as_slug:
            matches.append(uuid)
    if len(matches) == 1:
        return matches[0]
    return None


# ---------------------------------------------------------------------------
# Feature hash helper
# ---------------------------------------------------------------------------


def _compute_feature_hash(
    title: str,
    intent: str,
    parent_uuid: str | None,
    retired: bool,
) -> str:
    """sha1(title + "|" + intent + "|" + (parent_uuid or "") + "|" + str(retired))."""
    raw = title + "|" + intent + "|" + (parent_uuid or "") + "|" + str(retired)
    return hashlib.sha1(raw.encode()).hexdigest()
