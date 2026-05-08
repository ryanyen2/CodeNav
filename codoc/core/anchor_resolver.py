"""Resolve an Anchor to a byte range in the actual source file.

Resolution order:
1. symbol_path → ask language_adapter.resolve_symbol_path()
2. ts_query     → run tree-sitter query, optionally scoped to the symbol_path region;
                   apply occurrence_index to pick the Nth match.
3. Neither resolves → return None (binding is Severed).
"""

from codoc.model.anchor import Anchor


def resolve_anchor(
    anchor: Anchor,
    file_source: str,
    language_adapter,  # LanguageAdapter (codoc.lang.base)
) -> tuple[int, int] | None:
    """Resolve anchor to (start_byte, end_byte) or None if unresolvable."""
    source_bytes = file_source.encode("utf-8")
    symbol_range: tuple[int, int] | None = None

    # --- Step 1: symbol_path resolution ---
    if anchor.symbol_path is not None:
        symbol_range = _resolve_symbol_path(
            anchor.symbol_path, file_source, language_adapter
        )
        if symbol_range is None and anchor.ts_query is None:
            # Only symbol_path specified and it failed → unresolvable.
            return None

    # --- Step 2: ts_query refinement ---
    if anchor.ts_query is not None:
        query_result = _run_ts_query(
            anchor.ts_query,
            anchor.occurrence_index,
            file_source,
            source_bytes,
            language_adapter,
            scope=symbol_range,
        )
        if query_result is not None:
            return query_result
        # ts_query failed; fall through.

    # If only symbol_path was set and it resolved, return that range.
    if symbol_range is not None and anchor.ts_query is None:
        return symbol_range

    return None


def read_chunk_source(
    file_source: str,
    start_byte: int,
    end_byte: int,
) -> str:
    """Extract the chunk text from file source bytes."""
    return file_source.encode("utf-8")[start_byte:end_byte].decode(errors="replace")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_symbol_path(
    symbol_path: str,
    file_source: str,
    language_adapter,
) -> tuple[int, int] | None:
    """Delegate to the language adapter's symbol_path resolver."""
    try:
        result = language_adapter.resolve_symbol_path(file_source, symbol_path)
    except Exception:
        return None
    if result is None:
        return None
    start, end = result
    return (int(start), int(end))


def _run_ts_query(
    ts_query: str,
    occurrence_index: int,
    file_source: str,
    source_bytes: bytes,
    language_adapter,
    scope: tuple[int, int] | None,
) -> tuple[int, int] | None:
    """Run a tree-sitter S-expression query and return the Nth match's byte range.

    If *scope* is provided the query is executed over the full tree but only
    matches whose start_byte falls within [scope[0], scope[1]) are considered.
    """
    try:
        tree = language_adapter.parse(file_source)
    except Exception:
        return None

    try:
        query = language_adapter.build_query(ts_query)
    except Exception:
        return None

    try:
        captures = query.captures(tree.root_node)
    except Exception:
        return None

    # Normalise captures: tree-sitter v0.21+ returns dict; older returns list of (node, name).
    nodes: list = []
    if isinstance(captures, dict):
        for node_list in captures.values():
            nodes.extend(node_list)
    else:
        nodes = [node for node, _name in captures]

    # Sort by byte position for stable occurrence_index semantics.
    nodes.sort(key=lambda n: n.start_byte)

    # Filter to scope if given.
    if scope is not None:
        scope_start, scope_end = scope
        nodes = [n for n in nodes if scope_start <= n.start_byte < scope_end]

    if occurrence_index < 0 or occurrence_index >= len(nodes):
        return None

    node = nodes[occurrence_index]
    return (node.start_byte, node.end_byte)
