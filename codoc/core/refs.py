"""Reference resolver — accepts slug-paths, UUID prefixes, and full UUIDs.

Users never need to paste full UUIDs or HLCs. This module maps human-friendly
references to the Feature / Transaction the user meant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codoc.model.feature import Feature
    from codoc.model.transaction import Transaction
    from codoc.storage.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


@dataclass
class NotFoundRef(Exception):
    query: str
    hint: str = ""

    def __str__(self) -> str:
        msg = f"Nothing found for {self.query!r}."
        if self.hint:
            msg += f" {self.hint}"
        return msg


@dataclass
class AmbiguousRef(Exception):
    query: str
    candidates: list[tuple[str, str]] = field(default_factory=list)  # (slug_path, uuid)

    def __str__(self) -> str:
        lines = [f"{self.query!r} matches more than one feature:"]
        for slug_path, uuid in self.candidates:
            lines.append(f"  {slug_path}  ({uuid[:8]})")
        lines.append("Use a more specific slug-path or UUID prefix.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _looks_like_uuid_fragment(s: str) -> bool:
    """True if the string consists only of hex digits and dashes (UUID-like)."""
    return len(s) >= 4 and all(c in "0123456789abcdefABCDEF-" for c in s)


def slug_path_for(feature_uuid: str, store: "SQLiteStore") -> str:
    """Build the slug-path for a feature by walking up the parent chain.

    Returns e.g. 'visualization/spec-parser'.
    """
    parts: list[str] = []
    current_uuid: str | None = feature_uuid
    visited: set[str] = set()
    while current_uuid:
        if current_uuid in visited:
            break
        visited.add(current_uuid)
        feature = store.get_feature(current_uuid)
        if feature is None:
            break
        parts.append(feature.slug)
        current_uuid = feature.parent_uuid
    parts.reverse()
    return "/".join(parts)


# ---------------------------------------------------------------------------
# Feature resolver
# ---------------------------------------------------------------------------


def resolve_feature_ref(ref: str, store: "SQLiteStore") -> "Feature":
    """Resolve a human-friendly reference to a Feature.

    Accepted forms, tried in order:
    1. Exact UUID.
    2. UUID prefix (≥ 4 hex chars, must be unambiguous).
    3. Slug-path  (contains '/').
    4. Plain slug (must be globally unique).

    Raises NotFoundRef or AmbiguousRef on failure.
    """
    ref = ref.strip()
    if not ref:
        raise NotFoundRef(ref, "Reference cannot be empty.")

    # 1. Exact UUID.
    feature = store.get_feature(ref)
    if feature is not None:
        return feature

    # 2. UUID prefix — only try if it looks like a UUID fragment.
    if _looks_like_uuid_fragment(ref):
        matches = store.find_features_by_uuid_prefix(ref.lower().replace("-", ""))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            candidates = [(slug_path_for(f.uuid, store), f.uuid) for f in matches]
            raise AmbiguousRef(ref, candidates)

    # 3. Slug-path (e.g. 'visualization/spec-parser').
    if "/" in ref:
        feature = store.find_feature_by_slug_path(ref)
        if feature is not None:
            return feature
        raise NotFoundRef(
            ref,
            f"No feature at slug-path {ref!r}. Run `codoc list` to browse.",
        )

    # 4. Plain slug — globally unique check.
    matches = store.find_features_by_slug(ref)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        candidates = [(slug_path_for(f.uuid, store), f.uuid) for f in matches]
        raise AmbiguousRef(ref, candidates)

    # Suggest close matches using slug similarity.
    all_features = store.list_features(parent_uuid=None)
    all_slugs = [f.slug for f in all_features]
    close = _close_matches(ref, all_slugs, n=3, cutoff=0.5)
    hint = "Run `codoc list` to browse available features."
    if close:
        hint = f"Did you mean: {', '.join(close)}? Run `codoc list` to browse."
    raise NotFoundRef(ref, hint)


# ---------------------------------------------------------------------------
# Transaction / proposal resolver
# ---------------------------------------------------------------------------


def resolve_tx_ref(ref: str, store: "SQLiteStore") -> "Transaction":
    """Resolve a proposal reference (HLC prefix ≥ 8 chars, or full HLC).

    Also accepts a feature slug-path, in which case the first pending proposal
    for that feature is returned.

    Raises ValueError on failure.
    """
    ref = ref.strip()
    if not ref:
        raise ValueError("Proposal reference cannot be empty.")

    # 1. Exact HLC.
    tx = store.get_transaction(ref)
    if tx is not None:
        return tx

    # 2. HLC prefix (≥ 8 chars, looks like HLC: starts with digits).
    if len(ref) >= 8 and ref[0].isdigit():
        matches = store.find_transactions_by_hlc_prefix(ref)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            hlcs = [t.hlc.to_str()[:20] for t in matches[:4]]
            raise ValueError(
                f"{ref!r} is ambiguous — matches {len(matches)} proposals. "
                f"Use more characters. First few: {', '.join(hlcs)}"
            )

    # 3. Slug-like ref (doesn't start with digits):
    #    a) Match by pending proposal payload slug (INTRODUCE / RENAME proposals).
    #    b) Fall back to finding the first pending proposal for an existing feature.
    if "/" in ref or (not ref[0].isdigit()):
        pending = store.list_transactions(proposal=True, limit=0)

        # a) Direct payload slug match (works for un-accepted INTRODUCE proposals).
        slug_matches = [
            p for p in pending
            if p.payload.get("slug") == ref or p.payload.get("new_slug") == ref
        ]
        if len(slug_matches) == 1:
            return slug_matches[0]
        if len(slug_matches) > 1:
            hlcs = [p.hlc.to_str()[:16] for p in slug_matches[:3]]
            raise ValueError(
                f"{ref!r} matches {len(slug_matches)} proposals: {', '.join(hlcs)}. "
                "Use the HLC prefix to disambiguate."
            )

        # b) Feature slug-path → first pending proposal for that feature.
        try:
            feature = resolve_feature_ref(ref, store)
            for p in pending:
                feat_uuid = p.payload.get("feature_uuid") or p.payload.get("affected_feature_uuid")
                if feat_uuid == feature.uuid:
                    return p
            raise ValueError(
                f"Feature {ref!r} has no pending proposals. "
                "Run `codoc proposals` to see all pending proposals."
            )
        except (NotFoundRef, AmbiguousRef) as exc:
            raise ValueError(str(exc)) from exc

    raise ValueError(
        f"No proposal found for {ref!r}. Run `codoc proposals` to list them."
    )


# ---------------------------------------------------------------------------
# Fuzzy close-match helper (no external deps)
# ---------------------------------------------------------------------------


def _close_matches(word: str, possibilities: list[str], n: int = 3, cutoff: float = 0.6) -> list[str]:
    """Return up to n close matches from possibilities using a simple similarity ratio."""
    def _ratio(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        a, b = a.lower(), b.lower()
        matches = sum(ca == cb for ca, cb in zip(a, b))
        return 2.0 * matches / (len(a) + len(b))

    scored = [(p, _ratio(word, p)) for p in possibilities]
    scored = [(p, r) for p, r in scored if r >= cutoff]
    scored.sort(key=lambda x: -x[1])
    return [p for p, _ in scored[:n]]
