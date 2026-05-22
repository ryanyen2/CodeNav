"""Single tree-sitter walker that emits three identity signals from one pass.

Returns a WalkResult with:
  tokens_hash   — SHA-256 of the whitespace-normalized token stream (comment-stripped).
                  Identical to fingerprint_chunk(source, adapter).
  types_hash    — SHA-256 of the node-type sequence (rename-invariant structural identity).
                  Identical to skeleton_hash(source, adapter).
  minhash       — 128-permutation MinHash sketch over k=5 token n-grams (16 bytes).
                  Used for fast Jaccard approximation in the chunk matcher.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # LanguageAdapter is imported lazily to avoid circular imports

from codoc.core.chunk_matching.minhash import minhash_sketch as _minhash_sketch


@dataclass(frozen=True)
class WalkResult:
    tokens_hash: str   # hex SHA-256
    types_hash: str    # hex SHA-256
    minhash: bytes     # 16-byte sketch


_COMMENT_TYPES = frozenset({
    "comment", "block_comment", "line_comment",
    "string_comment", "documentation_comment",
})

_IDENTIFIER_LIKE = frozenset({
    "identifier", "string", "integer", "float", "comment",
    "string_content", "escape_sequence",
    "type_identifier", "field_identifier",
})


def walk(source: str, adapter=None) -> WalkResult:
    """Walk a source snippet and return all three identity signals."""
    tokens: list[str] = []
    types: list[str] = []

    if adapter is not None:
        comment_kinds: frozenset[str] = frozenset(
            getattr(adapter, "comment_node_kinds", ())
        ) | _COMMENT_TYPES
        try:
            tree = adapter.parse(source)
            _walk_tree(tree.root_node, comment_kinds, source.encode("utf-8"), tokens, types)
        except Exception:
            # Fallback: split on whitespace for tokens; no type sequence.
            tokens = source.split()
            types = []
    else:
        tokens = source.split()
        types = []

    token_str = " ".join(tokens)
    tokens_hash = hashlib.sha256(token_str.encode()).hexdigest()
    types_str = " ".join(types)
    types_hash = hashlib.sha256(types_str.encode()).hexdigest()
    mh = _minhash_sketch(tokens)
    return WalkResult(tokens_hash=tokens_hash, types_hash=types_hash, minhash=mh)


def _walk_tree(
    node,
    comment_kinds: frozenset[str],
    source_bytes: bytes,
    tokens: list[str],
    types: list[str],
) -> None:
    """Depth-first traversal collecting tokens and node types."""
    if node.type in comment_kinds:
        return
    if node.child_count == 0:
        # Leaf node: grab token text.
        text = source_bytes[node.start_byte:node.end_byte].decode(
            "utf-8", errors="replace"
        ).strip()
        if text:
            tokens.append(text)
        # Collect type for the types sequence.
        if node.type not in _IDENTIFIER_LIKE:
            types.append(node.type)
        else:
            types.append(f"<{node.type}>")
    else:
        # Internal node: record type in types sequence, recurse.
        types.append(f"({node.type}")
        for child in node.children:
            _walk_tree(child, comment_kinds, source_bytes, tokens, types)
        types.append(")")
