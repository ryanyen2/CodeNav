"""One tree-sitter walk over a chunk that yields its two identity hashes.

``walk`` returns a ``WalkResult`` with:
  fingerprint:    SHA-256 of the whitespace-normalized token stream, comments
                  stripped: the chunk's content, wherever it sits.
  types_hash:     SHA-256 of the node-type sequence: the chunk's shape, whatever
                  its identifiers are called.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class WalkResult:
    fingerprint: str   # hex SHA-256
    types_hash: str    # hex SHA-256


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
    """Walk a source snippet and return both identity signals."""
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

    return WalkResult(
        fingerprint=hashlib.sha256(" ".join(tokens).encode()).hexdigest(),
        types_hash=hashlib.sha256(" ".join(types).encode()).hexdigest(),
    )


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
