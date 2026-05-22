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

    Matching passes
    ---------------
    1. Exact title match (case-insensitive strip) within parent.
    2. Exact slug match: normalise *title* to slug form and compare to
       stored ``slug`` field.
    3. Sibling-index match with Levenshtein edit-distance guard:
       ``dist(title, old_title) / max(len(title), len(old_title)) <= 0.4``.
    """
    if prebuilt_sibling_index is not None:
        siblings = prebuilt_sibling_index.get(parent_uuid, [])
    else:
        siblings = _get_old_siblings(parent_uuid, old_meta)
    if not siblings:
        return None

    title_stripped = title.strip().lower()

    # --- Pass 1: exact title (case-insensitive) ---
    pass1: list[str] = []
    for sib in siblings:
        stored_title = sib.get("title", "").strip().lower()
        if stored_title == title_stripped:
            pass1.append(sib["uuid"])
    if len(pass1) == 1:
        return pass1[0]
    if len(pass1) > 1:
        return None  # ambiguous

    # --- Pass 2: slug match ---
    title_as_slug = _title_to_slug(title)
    pass2: list[str] = []
    for sib in siblings:
        stored_slug = sib.get("slug", "")
        if stored_slug == title_as_slug:
            pass2.append(sib["uuid"])
    if len(pass2) == 1:
        return pass2[0]
    if len(pass2) > 1:
        return None  # ambiguous

    # --- Pass 3: sibling_index + Levenshtein guard ---
    pass3: list[str] = []
    for sib in siblings:
        if sib.get("sibling_index") != sibling_index_new:
            continue
        old_title = sib.get("title", "")
        t1 = title.strip()
        t2 = old_title.strip()
        max_len = max(len(t1), len(t2))
        if max_len == 0:
            pass3.append(sib["uuid"])
            continue
        dist = _levenshtein(t1.lower(), t2.lower())
        if dist / max_len <= 0.4:
            pass3.append(sib["uuid"])
    if len(pass3) == 1:
        return pass3[0]

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
