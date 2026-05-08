"""Per-feature Merkle-style subtree hash.

A feature's subtree hash covers:
  - its own canonical representation (uuid, slug, intent, retired)
  - the sorted subtree hashes of all its direct children

This allows cheap comparison of any two tree states: if two subtree hashes
are equal the entire subtree rooted at that feature is identical.
"""

import hashlib


def feature_canonical_hash(feature) -> str:
    """SHA-256 of the feature's identity-stable canonical form.

    The canonical string is: ``"{uuid}|{slug}|{intent}|{retired}"``
    """
    canonical = f"{feature.uuid}|{feature.slug}|{feature.intent}|{feature.retired}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def subtree_hash(feature, children_subtree_hashes: list[str]) -> str:
    """Merkle subtree hash: sha256(feature_canonical_bytes + sorted children hashes).

    The children list is sorted before hashing so that insertion order of
    children does not affect the digest — only the *set* of children matters.
    """
    canonical = feature_canonical_hash(feature)
    # Sort child hashes for order-independence.
    sorted_children = sorted(children_subtree_hashes)
    combined = canonical + "".join(sorted_children)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
