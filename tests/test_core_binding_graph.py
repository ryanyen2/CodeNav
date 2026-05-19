"""Tests for binding graph derivation, neighbor and reachability queries."""

from __future__ import annotations

from dataclasses import dataclass

from codoc.core.binding_graph import derive_binding_graph, neighbors_1hop


# ---------------------------------------------------------------------------
# Synthetic adapter — emits the references we encode in the chunk source.
# Format: each "line" inside a chunk source is "REF qualified_name".
# ---------------------------------------------------------------------------


@dataclass
class _Ref:
    qualified_name: str


class _StubAdapter:
    language = "stub"
    comment_node_kinds: set[str] = set()

    def references_in_chunk(self, chunk_source: str, file: str = "") -> list[_Ref]:
        refs: list[_Ref] = []
        for line in chunk_source.splitlines():
            line = line.strip()
            if line.startswith("REF "):
                refs.append(_Ref(qualified_name=line[4:].strip()))
        return refs


def _resolve_full(anchor, source, adapter):
    return (0, len(source.encode("utf-8")))


def test_binding_graph_one_hop_edge(make_binding) -> None:
    # Feature A has a binding whose chunk references symbol_path of feature B.
    f_a = "feat-a"
    f_b = "feat-b"
    binding_a = make_binding(
        feature_uuid=f_a,
        file="a.py",
        symbol_path="a.py::caller",
    )
    binding_b = make_binding(
        feature_uuid=f_b,
        file="b.py",
        symbol_path="b.py::callee",
    )
    file_sources = {
        "a.py": "REF b.py::callee\n",
        "b.py": "REF nothing\n",
    }
    graph = derive_binding_graph(
        bindings=[binding_a, binding_b],
        file_sources=file_sources,
        language_adapter=_StubAdapter(),
        resolve_anchor_fn=_resolve_full,
    )
    assert graph.get(f_a) == {f_b}
    assert f_b not in graph or graph.get(f_b, set()) == set()


def test_binding_graph_self_reference_excluded(make_binding) -> None:
    f_a = "feat-a"
    binding = make_binding(
        feature_uuid=f_a,
        file="a.py",
        symbol_path="a.py::self",
    )
    file_sources = {"a.py": "REF a.py::self\n"}
    graph = derive_binding_graph(
        bindings=[binding],
        file_sources=file_sources,
        language_adapter=_StubAdapter(),
        resolve_anchor_fn=_resolve_full,
    )
    assert f_a not in graph or graph[f_a] == set()


def test_binding_graph_unknown_reference_dropped(make_binding) -> None:
    f_a = "feat-a"
    binding = make_binding(
        feature_uuid=f_a,
        file="a.py",
        symbol_path="a.py::caller",
    )
    file_sources = {"a.py": "REF non.existent.symbol\n"}
    graph = derive_binding_graph(
        bindings=[binding],
        file_sources=file_sources,
        language_adapter=_StubAdapter(),
        resolve_anchor_fn=_resolve_full,
    )
    assert graph.get(f_a, set()) == set()


def test_neighbors_1hop_includes_both_directions() -> None:
    graph = {
        "a": {"b"},
        "b": {"c"},
        "c": set(),
    }
    # b is referenced by a and references c.
    assert neighbors_1hop("b", graph) == {"a", "c"}
    assert neighbors_1hop("a", graph) == {"b"}
    assert neighbors_1hop("c", graph) == {"b"}


def test_neighbors_1hop_isolated_feature() -> None:
    graph = {"a": set()}
    assert neighbors_1hop("a", graph) == set()


def test_binding_graph_two_features_bidirectional(make_binding) -> None:
    binding_a = make_binding(
        feature_uuid="A",
        file="a.py",
        symbol_path="a.py::a_fn",
    )
    binding_b = make_binding(
        feature_uuid="B",
        file="b.py",
        symbol_path="b.py::b_fn",
    )
    file_sources = {
        "a.py": "REF b.py::b_fn\n",
        "b.py": "REF a.py::a_fn\n",
    }
    graph = derive_binding_graph(
        bindings=[binding_a, binding_b],
        file_sources=file_sources,
        language_adapter=_StubAdapter(),
        resolve_anchor_fn=_resolve_full,
    )
    assert graph.get("A") == {"B"}
    assert graph.get("B") == {"A"}
    assert neighbors_1hop("A", graph) == {"B"}
