"""Phase 1 / 7 — incremental graph maintenance.

update_graph(store, rows, changed_files) must:
  - Delete only edges with src_file in changed_files.
  - Re-extract edges for those files, resolving against the full symbol table.
  - Leave edges for unchanged files intact.
"""
from __future__ import annotations

import pytest

from tests.graph.conftest import FakeRow
from codoc.graph.query import build_graph, update_graph


@pytest.fixture
def base_rows():
    return [
        FakeRow("auth.py", "auth.py::login", "def login():\n    create_session()\n"),
        FakeRow("session.py", "session.py::create_session", "def create_session():\n    return {}\n"),
        FakeRow("utils.py", "utils.py::helper", "def helper():\n    return True\n"),
    ]


def test_update_only_rebuilds_changed_file(store, base_rows):
    build_graph(store, base_rows)

    # Snapshot edges before update
    before_utils = [
        e for e in store.all_edges() if e["src_file"] == "utils.py"
    ]

    # Change auth.py — add a call to helper
    new_rows = [
        FakeRow("auth.py", "auth.py::login", "def login():\n    create_session()\n    helper()\n"),
        FakeRow("session.py", "session.py::create_session", "def create_session():\n    return {}\n"),
        FakeRow("utils.py", "utils.py::helper", "def helper():\n    return True\n"),
    ]
    update_graph(store, new_rows, changed_files={"auth.py"})

    # utils.py edges should be unchanged
    after_utils = [
        e for e in store.all_edges() if e["src_file"] == "utils.py"
    ]
    assert before_utils == after_utils or (
        len(before_utils) == len(after_utils) and
        all(b["src_symbol"] == a["src_symbol"] for b, a in zip(before_utils, after_utils))
    )

    # auth.py now has a call to helper
    auth_edges = [e for e in store.all_edges() if e["src_file"] == "auth.py"]
    helper_edge = next(
        (e for e in auth_edges if e["dst_symbol"] == "utils.py::helper"), None
    )
    assert helper_edge is not None, (
        f"Expected call edge auth.py::login → utils.py::helper after update; got {auth_edges}"
    )


def test_update_removes_stale_edges(store, base_rows):
    build_graph(store, base_rows)

    # login calls create_session → edge should exist
    auth_edges_before = [
        e for e in store.all_edges()
        if e["src_file"] == "auth.py" and e["kind"] == "call"
    ]
    assert any(e["dst_symbol"] == "session.py::create_session" for e in auth_edges_before)

    # Rewrite login to NOT call create_session
    new_rows = [
        FakeRow("auth.py", "auth.py::login", "def login():\n    pass\n"),
        FakeRow("session.py", "session.py::create_session", "def create_session():\n    return {}\n"),
        FakeRow("utils.py", "utils.py::helper", "def helper():\n    return True\n"),
    ]
    update_graph(store, new_rows, changed_files={"auth.py"})

    auth_edges_after = [
        e for e in store.all_edges()
        if e["src_file"] == "auth.py" and e["kind"] == "call"
    ]
    assert not any(
        e["dst_symbol"] == "session.py::create_session" for e in auth_edges_after
    ), "Stale call edge was not removed by update_graph"


def test_update_noop_on_empty_changed(store, base_rows):
    build_graph(store, base_rows)
    before = store.all_edges()
    update_graph(store, base_rows, changed_files=set())
    after = store.all_edges()
    assert len(before) == len(after)


def test_update_cross_file_resolution(store):
    """An updated file can still resolve symbols from unchanged files."""
    rows = [
        FakeRow("a.py", "a.py::main", "def main():\n    pass\n"),
        FakeRow("b.py", "b.py::util", "def util():\n    return 1\n"),
    ]
    build_graph(store, rows)

    # Update a.py to now call util from b.py
    new_rows = [
        FakeRow("a.py", "a.py::main", "def main():\n    util()\n"),
        FakeRow("b.py", "b.py::util", "def util():\n    return 1\n"),
    ]
    update_graph(store, new_rows, changed_files={"a.py"})

    call_edges = [e for e in store.all_edges() if e["kind"] == "call"]
    assert any(
        e["src_symbol"] == "a.py::main" and e["dst_symbol"] == "b.py::util"
        for e in call_edges
    ), f"Cross-file resolution failed; call edges: {call_edges}"
