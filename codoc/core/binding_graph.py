"""On-demand derivation of the binding graph.

The graph maps feature_uuid → set[referenced_feature_uuid] and is computed
entirely from bindings, file sources, and the language adapter.  It is NEVER
persisted; callers request it when they need it.

Algorithm
---------
For each feature F:
  For each binding B of F:
    1. Resolve B's anchor to a byte range in the file.
    2. Extract the chunk source for that range.
    3. Ask the language adapter for symbol references in the chunk.
    4. For each symbol reference, find a feature whose binding has an
       anchor.symbol_path equal to the qualified name → add edge F → G.
"""

from collections import defaultdict
from typing import Callable

from codoc.model.binding import Binding


def derive_binding_graph(
    bindings: list[Binding],
    file_sources: dict[str, str],  # file path → source text
    language_adapter,
    resolve_anchor_fn: Callable,  # callable(anchor, source, adapter) -> (int, int) | None
) -> dict[str, set[str]]:
    """Return {feature_uuid: set[referenced_feature_uuid]}.

    Parameters
    ----------
    bindings:
        All bindings across all features.
    file_sources:
        Mapping from repo-relative file path to file source text.
    language_adapter:
        A LanguageAdapter instance; must expose ``references_in_chunk(chunk_source)``
        returning a list of objects with a ``qualified_name`` attribute.
    resolve_anchor_fn:
        A callable with the signature of ``codoc.core.anchor_resolver.resolve_anchor``.
        Injected to keep this module free of circular imports.
    """
    # Build a lookup: symbol_path → feature_uuid (using the first binding that
    # has a symbol_path; if multiple bindings share the same path the last one
    # wins — a degenerate case that shouldn't arise in a well-formed store).
    symbol_to_feature: dict[str, str] = {}
    for b in bindings:
        if b.anchor.symbol_path:
            symbol_to_feature[b.anchor.symbol_path] = b.feature_uuid

    # Group bindings by feature.
    feature_bindings: dict[str, list[Binding]] = defaultdict(list)
    for b in bindings:
        feature_bindings[b.feature_uuid].append(b)

    graph: dict[str, set[str]] = defaultdict(set)

    for feature_uuid, fbindings in feature_bindings.items():
        for binding in fbindings:
            file_path = binding.anchor.file
            source = file_sources.get(file_path)
            if source is None:
                continue

            byte_range = resolve_anchor_fn(binding.anchor, source, language_adapter)
            if byte_range is None:
                continue

            start_byte, end_byte = byte_range
            chunk_source = source.encode("utf-8")[start_byte:end_byte].decode(
                errors="replace"
            )

            try:
                refs = language_adapter.references_in_chunk(chunk_source)
            except Exception:
                continue

            for ref in refs:
                qualified_name: str = getattr(ref, "qualified_name", None) or str(ref)
                referenced_feature = symbol_to_feature.get(qualified_name)
                if referenced_feature and referenced_feature != feature_uuid:
                    graph[feature_uuid].add(referenced_feature)

    return dict(graph)


def neighbors_1hop(feature_uuid: str, graph: dict[str, set[str]]) -> set[str]:
    """Return all features directly connected to feature_uuid (in either direction).

    Includes both features *referenced by* the given feature and features
    that *reference* the given feature.
    """
    referenced_by_f: set[str] = graph.get(feature_uuid, set())
    referencing_f: set[str] = {k for k, refs in graph.items() if feature_uuid in refs}
    return referenced_by_f | referencing_f
