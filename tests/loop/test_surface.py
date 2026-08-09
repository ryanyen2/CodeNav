"""Deriving flows from the call graph.

The organization pass used to see only coupling counts, which can express "these
two touch often" and cannot express "these are steps in one job, in this order".
The first shape produces a filing cabinet; the second produces a map. These tests
pin the properties that make the difference — that a path crosses modules, that
it follows the spine of an operation rather than the first helper it meets, and
that eight aliases of one operation are told once.
"""
from __future__ import annotations

import pytest

from codoc.loop.surface import dominant_path, entry_symbols, flow_lines, flows
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _calls(store, pairs):
    store.insert_edges([
        {"src_file": src.split("::")[0], "src_symbol": src,
         "dst_name": dst.split("::")[-1], "dst_symbol": dst,
         "dst_file": dst.split("::")[0], "kind": "call", "internal": 1}
        for src, dst in pairs
    ])


# A miniature of the shape this exists to capture: a public entry, a preparing
# stage, a sending stage, each in its own module.
LIFECYCLE = [
    ("api.py::get", "sessions.py::Session.request"),
    ("sessions.py::Session.request", "models.py::PreparedRequest.prepare"),
    ("models.py::PreparedRequest.prepare", "utils.py::requote_uri"),
    ("sessions.py::Session.request", "adapters.py::HTTPAdapter.send"),
]


class TestEntrySymbols:
    def test_finds_the_symbol_nothing_calls(self, store):
        _calls(store, LIFECYCLE)
        assert "api.py::get" in entry_symbols(store)

    def test_excludes_symbols_that_are_called(self, store):
        _calls(store, LIFECYCLE)
        assert "utils.py::requote_uri" not in entry_symbols(store)

    def test_excludes_private_helpers(self, store):
        _calls(store, [("mod.py::_helper", "mod.py::thing")])
        assert "mod.py::_helper" not in entry_symbols(store)

    def test_keeps_dunder_methods(self, store):
        """__init__ and __call__ are reached constantly from outside; dropping
        them cuts most flows off at their first step."""
        _calls(store, [("auth.py::HTTPDigestAuth.__call__", "auth.py::build_header")])
        assert "auth.py::HTTPDigestAuth.__call__" in entry_symbols(store)

    def test_excludes_the_module_pseudo_symbol(self, store):
        _calls(store, [("mod.py::__module__", "mod.py::thing")])
        assert "mod.py::__module__" not in entry_symbols(store)


class TestDominantPath:
    def test_follows_the_branch_that_keeps_going(self, store):
        """Taking the first callee wanders into whichever validator sorts first;
        taking the busiest-onward one traces the spine of the operation."""
        _calls(store, [
            ("a.py::entry", "a.py::aardvark_check"),   # sorts first, goes nowhere
            ("a.py::entry", "b.py::worker"),
            ("b.py::worker", "c.py::deeper"),
            ("b.py::worker", "c.py::deeper2"),
        ])
        assert dominant_path(store, "a.py::entry")[1] == "b.py::worker"

    def test_stops_at_a_leaf(self, store):
        _calls(store, [("a.py::entry", "b.py::leaf")])
        assert dominant_path(store, "a.py::entry") == ["a.py::entry", "b.py::leaf"]

    def test_does_not_loop_forever_on_a_cycle(self, store):
        _calls(store, [("a.py::x", "b.py::y"), ("b.py::y", "a.py::x")])
        path = dominant_path(store, "a.py::x")
        assert len(path) == len(set(path))

    def test_respects_max_depth(self, store):
        _calls(store, [(f"m{i}.py::s", f"m{i + 1}.py::s") for i in range(20)])
        assert len(dominant_path(store, "m0.py::s", max_depth=4)) == 4


class TestFlows:
    def test_a_cross_module_path_is_reported(self, store):
        _calls(store, LIFECYCLE)
        line = " → ".join(flows(store)[0])
        assert line.startswith("api.py::get")
        assert "sessions.py::Session.request" in line

    def test_a_path_inside_one_file_is_not_architecture(self, store):
        """Seven calls within one class is that class's internals; the reader
        who needs a map is asking how the pieces fit."""
        _calls(store, [(f"solo.py::step{i}", f"solo.py::step{i + 1}") for i in range(6)])
        assert flows(store) == []

    def test_aliases_of_one_operation_are_told_once(self, store):
        """get/post/put/delete all become Session.request immediately — one
        story with different first words, and eight of them would crowd out
        every other flow."""
        _calls(store, LIFECYCLE)
        _calls(store, [(f"api.py::{verb}", "sessions.py::Session.request")
                       for verb in ("post", "put", "delete", "head", "patch")])
        heads = [p[0] for p in flows(store)]
        assert len([h for h in heads if h.startswith("api.py::")]) == 1

    def test_longer_module_reach_ranks_first(self, store):
        _calls(store, LIFECYCLE)
        _calls(store, [("x.py::small", "y.py::end")])
        assert len(set(s.split("::")[0] for s in flows(store)[0])) >= 3

    def test_empty_graph_yields_nothing(self, store):
        assert flow_lines(store) == []

    def test_lines_render_as_arrow_chains(self, store):
        _calls(store, LIFECYCLE)
        assert all(" → " in line for line in flow_lines(store))
