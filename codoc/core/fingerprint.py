import hashlib


def fingerprint_chunk(source: str, language_adapter) -> str:
    """Compute SHA-256 fingerprint of source text via tree-sitter token stream.

    language_adapter is a LanguageAdapter instance (codoc.lang.base).
    Comment nodes (per language_adapter.comment_node_kinds) are excluded.
    Token text is joined with single spaces then SHA-256 hashed.
    """
    tokens = _extract_tokens(source, language_adapter)
    token_string = " ".join(tokens)
    return hashlib.sha256(token_string.encode("utf-8")).hexdigest()


def fingerprint_source(source: str, language_adapter) -> str:
    """Same as fingerprint_chunk but for a whole file."""
    return fingerprint_chunk(source, language_adapter)


def are_fingerprints_meaningfully_different(fp1: str, fp2: str) -> bool:
    """Simple equality check; both must be SHA-256 hex strings.

    Returns True if they differ (i.e., the chunk has changed meaningfully).
    """
    return fp1 != fp2


def _extract_tokens(source: str, language_adapter) -> list[str]:
    """Walk the tree-sitter parse tree and collect leaf token texts,
    excluding comment node kinds reported by the language adapter."""
    comment_kinds: frozenset[str] = frozenset(
        getattr(language_adapter, "comment_node_kinds", ())
    )

    try:
        tree = language_adapter.parse(source)
    except Exception:
        # Fallback: treat the whole source as a single whitespace-normalised token.
        return _whitespace_normalize(source)

    tokens: list[str] = []
    _walk(tree.root_node, comment_kinds, source.encode("utf-8"), tokens)
    return tokens


def _walk(
    node,
    comment_kinds: frozenset[str],
    source_bytes: bytes,
    tokens: list[str],
) -> None:
    """Recursively walk tree-sitter nodes, collecting non-comment leaf text."""
    if node.type in comment_kinds:
        return
    if node.child_count == 0:
        # Leaf node: grab its text.
        text = source_bytes[node.start_byte : node.end_byte].decode(
            "utf-8", errors="replace"
        ).strip()
        if text:
            tokens.append(text)
        return
    for child in node.children:
        _walk(child, comment_kinds, source_bytes, tokens)


def _whitespace_normalize(source: str) -> list[str]:
    """Split source on whitespace as a best-effort fallback when parsing fails."""
    return [t for t in source.split() if t]
