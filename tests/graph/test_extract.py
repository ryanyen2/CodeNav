"""Phase 1 — extract: edge extraction from chunk rows (no LanceDB, no LLM)."""
from __future__ import annotations

from tests.graph.conftest import FakeRow
from codoc.graph.extract import (
    _build_indices,
    _contain_edges,
    _file_to_module,
    _resolve,
    extract_edges,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_file_to_module():
    assert _file_to_module("requests/models.py") == "requests.models"
    assert _file_to_module("codoc/loop/apply.py") == "codoc.loop.apply"
    assert _file_to_module("auth.py") == "auth"
    assert _file_to_module("src/utils.ts") == "src.utils"


def test_build_indices_leaf_and_module():
    rows = [
        FakeRow("auth.py", "auth.py::MyClass", "class MyClass: ..."),
        FakeRow("auth.py", "auth.py::MyClass.login", "def login(self): ..."),
        FakeRow("utils.py", "utils.py::helper", "def helper(): ..."),
    ]
    by_symbol, by_leaf, ftm = _build_indices(rows)

    assert "auth.py::MyClass" in by_symbol
    assert "auth.py::MyClass.login" in by_symbol
    assert "MyClass" in by_leaf
    assert "login" in by_leaf
    assert "helper" in by_leaf
    assert ftm["auth.py"] == "auth"
    assert ftm["utils.py"] == "utils"


def test_resolve_same_file_preferred():
    rows = [
        FakeRow("a.py", "a.py::foo", "def foo(): ..."),
        FakeRow("b.py", "b.py::foo", "def foo(): ..."),
    ]
    by_symbol, by_leaf, ftm = _build_indices(rows)
    result = _resolve("foo", "a.py", by_symbol, by_leaf, ftm)
    assert result is not None and result.file == "a.py"


def test_resolve_module_prefix_match():
    rows = [
        FakeRow("requests/models.py", "requests/models.py::Response", "class Response: ..."),
        FakeRow("other.py", "other.py::Response", "class Response: ..."),
    ]
    by_symbol, by_leaf, ftm = _build_indices(rows)
    # qualified_name "requests.models.Response" should resolve to requests/models.py
    result = _resolve("requests.models.Response", "main.py", by_symbol, by_leaf, ftm)
    assert result is not None and result.file == "requests/models.py"


def test_resolve_strips_self_prefix():
    rows = [FakeRow("a.py", "a.py::send", "def send(): ...")]
    by_symbol, by_leaf, ftm = _build_indices(rows)
    result = _resolve("self.send", "a.py", by_symbol, by_leaf, ftm)
    assert result is not None and result.symbol_path == "a.py::send"


def test_resolve_external_returns_none():
    rows = [FakeRow("a.py", "a.py::foo", "def foo(): ...")]
    by_symbol, by_leaf, ftm = _build_indices(rows)
    result = _resolve("os.path.join", "a.py", by_symbol, by_leaf, ftm)
    # "join" is not in any row
    assert result is None


def test_resolve_rejects_short_leaf():
    rows = [FakeRow("a.py", "a.py::x", "x = 1")]
    by_symbol, by_leaf, ftm = _build_indices(rows)
    result = _resolve("x", "a.py", by_symbol, by_leaf, ftm)
    assert result is None  # len("x") <= 1 → skip


# ---------------------------------------------------------------------------
# Contain edges
# ---------------------------------------------------------------------------

def test_contain_edges_nested():
    rows = [
        FakeRow("auth.py", "auth.py::MyClass", "class MyClass: ..."),
        FakeRow("auth.py", "auth.py::MyClass.login", "def login(self): ..."),
        FakeRow("auth.py", "auth.py::login", "def login(): ..."),
    ]
    edges = _contain_edges(rows)
    assert len(edges) == 1
    e = edges[0]
    assert e["src_symbol"] == "auth.py::MyClass.login"
    assert e["dst_symbol"] == "auth.py::MyClass"
    assert e["kind"] == "contain"
    assert e["internal"] == 1


def test_contain_edges_top_level_not_included():
    rows = [FakeRow("a.py", "a.py::foo", "def foo(): ...")]
    edges = _contain_edges(rows)
    assert edges == []


def test_contain_edges_deep_nesting():
    rows = [
        FakeRow("a.py", "a.py::Outer.Inner.method", "def method(self): ..."),
    ]
    edges = _contain_edges(rows)
    assert len(edges) == 1
    assert edges[0]["dst_symbol"] == "a.py::Outer.Inner"


# ---------------------------------------------------------------------------
# Full extract_edges
# ---------------------------------------------------------------------------

def test_extract_call_edge():
    foo_src = "def foo():\n    bar()\n"
    bar_src = "def bar():\n    return 42\n"
    rows = [
        FakeRow("a.py", "a.py::foo", foo_src),
        FakeRow("b.py", "b.py::bar", bar_src),
    ]
    edges = extract_edges(rows)
    call_edges = [
        e for e in edges
        if e["kind"] == "call" and e["src_symbol"] == "a.py::foo"
    ]
    bar_edge = next((e for e in call_edges if e["dst_symbol"] == "b.py::bar"), None)
    assert bar_edge is not None, f"No call edge to b.py::bar; got {call_edges}"
    assert bar_edge["internal"] == 1


def test_extract_inherit_edge():
    base_src = "class Base:\n    pass\n"
    child_src = "class Child(Base):\n    pass\n"
    rows = [
        FakeRow("base.py", "base.py::Base", base_src),
        FakeRow("child.py", "child.py::Child", child_src),
    ]
    edges = extract_edges(rows)
    inherit_edges = [e for e in edges if e["kind"] == "inherit"]
    assert any(e["dst_symbol"] == "base.py::Base" for e in inherit_edges), (
        f"No inherit edge to base.py::Base; got {inherit_edges}"
    )


def test_external_refs_marked_not_internal():
    src = "import os\ndef foo():\n    os.path.join('a', 'b')\n"
    rows = [FakeRow("a.py", "a.py::foo", src)]
    edges = extract_edges(rows)
    import_os = [e for e in edges if e["kind"] == "import" and "os" in e["dst_name"]]
    assert import_os, "Expected import edge for os"
    for e in import_os:
        assert e["internal"] == 0, f"os import should be external: {e}"


def test_no_self_loops():
    src = "def foo():\n    foo()\n"
    rows = [FakeRow("a.py", "a.py::foo", src)]
    edges = extract_edges(rows)
    for e in edges:
        assert not (e["src_symbol"] == e["dst_symbol"]), f"Self-loop detected: {e}"
