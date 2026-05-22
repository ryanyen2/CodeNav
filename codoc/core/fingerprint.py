"""Token-stream fingerprinting for code chunks.

Delegates to codoc.core.tree_walk for the single-pass implementation.
"""
from codoc.core.tree_walk import walk as _tree_walk


def fingerprint_chunk(source: str, language_adapter=None) -> str:
    """Compute SHA-256 fingerprint of source text via tree-sitter token stream.

    language_adapter is a LanguageAdapter instance (codoc.lang.base).
    Comment nodes (per language_adapter.comment_node_kinds) are excluded.
    Token text is joined with single spaces then SHA-256 hashed.

    Delegates to codoc.core.tree_walk.walk for a single-pass implementation.
    """
    return _tree_walk(source, language_adapter).tokens_hash


def fingerprint_source(source: str, language_adapter=None) -> str:
    """Same as fingerprint_chunk but for a whole file."""
    return fingerprint_chunk(source, language_adapter)


def are_fingerprints_meaningfully_different(fp1: str, fp2: str) -> bool:
    """Simple equality check; both must be SHA-256 hex strings.

    Returns True if they differ (i.e., the chunk has changed meaningfully).
    """
    return fp1 != fp2
