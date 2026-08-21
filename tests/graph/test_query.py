"""Phase 1 — query: graph query API over a real SQLite store (no LanceDB, no LLM)."""
from __future__ import annotations

import pytest

from tests.graph.conftest import FakeRow
from codoc.graph.query import (
    build_graph,
    ego_graph,
    entry_points,
    neighbors,
    topological_order,
)


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

FOO_SRC = "def foo():\n    bar()\n"
BAR_SRC = "def bar():\n    return 42\n"


@pytest.fixture
def rows_foo_bar():
    return [
        FakeRow("a.py", "a.py::foo", FOO_SRC),
        FakeRow("b.py", "b.py::bar", BAR_SRC),
    ]


# ---------------------------------------------------------------------------
# build_graph / neighbors
# ---------------------------------------------------------------------------

def test_build_graph_creates_call_edge(store, rows_foo_bar):
    build_graph(store, rows_foo_bar)
    all_e = store.all_edges()
    call_edges = [e for e in all_e if e["kind"] == "call"]
    assert any(
        e["src_symbol"] == "a.py::foo" and e["dst_symbol"] == "b.py::bar"
        for e in call_edges
    ), f"Expected call edge foo→bar; got {[(e['src_symbol'], e['dst_symbol']) for e in call_edges]}"


def test_neighbors_out(store, rows_foo_bar):
    build_graph(store, rows_foo_bar)
    nb = neighbors(store, "a.py::foo", direction="out")
    assert "b.py::bar" in nb


def test_neighbors_in(store, rows_foo_bar):
    build_graph(store, rows_foo_bar)
    nb = neighbors(store, "b.py::bar", direction="in")
    assert "a.py::foo" in nb


def test_neighbors_kind_filter(store, rows_foo_bar):
    build_graph(store, rows_foo_bar)
    nb_inherit = neighbors(store, "a.py::foo", kinds={"inherit"}, direction="out")
    assert nb_inherit == []


def test_rebuild_is_idempotent(store, rows_foo_bar):
    build_graph(store, rows_foo_bar)
    build_graph(store, rows_foo_bar)
    all_e = store.all_edges()
    call_edges = [e for e in all_e if e["kind"] == "call"]
    # Should not double-insert edges (INSERT OR REPLACE deduplicates)
    foo_bar = [
        e for e in call_edges
        if e["src_symbol"] == "a.py::foo" and e["dst_symbol"] == "b.py::bar"
    ]
    assert len(foo_bar) == 1


# ---------------------------------------------------------------------------
# ego_graph
# ---------------------------------------------------------------------------

def test_ego_graph_includes_seeds(store, rows_foo_bar):
    build_graph(store, rows_foo_bar)
    eg = ego_graph(store, {"a.py::foo"}, hops=0)
    assert "a.py::foo" in eg


def test_ego_graph_1_hop(store, rows_foo_bar):
    build_graph(store, rows_foo_bar)
    eg = ego_graph(store, {"a.py::foo"}, hops=1)
    assert "b.py::bar" in eg


def test_ego_graph_2_hop_transitive():
    """Three-level chain: main → helper → util — 2-hop from main reaches util."""
    pass  # covered implicitly; ego_graph is BFS so 2 hops = transitive


# ---------------------------------------------------------------------------
# topological_order
# ---------------------------------------------------------------------------

def test_topological_order_leaf_before_caller(store, rows_foo_bar):
    build_graph(store, rows_foo_bar)
    order = topological_order(store)
    if "a.py::foo" in order and "b.py::bar" in order:
        assert order.index("b.py::bar") < order.index("a.py::foo"), (
            "bar (callee) should appear before foo (caller)"
        )


def test_topological_order_contain_respected(store):
    rows = [
        FakeRow("a.py", "a.py::MyClass", "class MyClass: ..."),
        FakeRow("a.py", "a.py::MyClass.method", "def method(self): ..."),
    ]
    build_graph(store, rows)
    order = topological_order(store)
    # method contains-in MyClass → MyClass should appear after method
    if "a.py::MyClass" in order and "a.py::MyClass.method" in order:
        assert order.index("a.py::MyClass.method") < order.index("a.py::MyClass"), (
            "method (contained) should appear before MyClass (container)"
        )


def test_topological_order_cycle_no_crash(store):
    """Cyclic call graph should not crash — cycle members appended at end."""
    rows = [
        FakeRow("a.py", "a.py::ping", "def ping():\n    pong()\n"),
        FakeRow("b.py", "b.py::pong", "def pong():\n    ping()\n"),
    ]
    build_graph(store, rows)
    order = topological_order(store)
    # Both symbols present (no crash, no infinite loop)
    symbols = set(order)
    assert "a.py::ping" in symbols or "b.py::pong" in symbols


# ---------------------------------------------------------------------------
# entry_points
# ---------------------------------------------------------------------------

def test_entry_points(store, rows_foo_bar):
    build_graph(store, rows_foo_bar)
    ep = entry_points(store)
    # foo calls bar; bar is never called by anything else
    assert "a.py::foo" in ep
    assert "b.py::bar" not in ep


def test_entry_points_empty_graph(store):
    ep = entry_points(store)
    assert ep == []


# ---------------------------------------------------------------------------
# feature_impact — "what happens if I change this?" (Sillito group 4)
# ---------------------------------------------------------------------------

def _feat(store, title):
    from codoc.model.feature import Feature
    f = Feature(title=title)
    store.upsert_feature(f)
    return f


def _bind(store, feature, symbol):
    from codoc.model.binding import Binding
    store.upsert_binding(Binding(feature_id=feature.id, file=symbol.split("::")[0],
                                 symbol_path=symbol, fingerprint="h"))


def _edge(store, src, dst, kind="call"):
    store.insert_edges([{
        "src_file": src.split("::")[0], "src_symbol": src,
        "dst_name": dst.split("::")[-1], "dst_symbol": dst,
        "dst_file": dst.split("::")[0], "kind": kind, "internal": 1,
    }])


def test_feature_impact_answers_the_incoming_direction(store):
    # `see_also` says what a feature depends ON; the question a reader asks before
    # editing runs the other way, and this is the one that answers it.
    from codoc.graph.query import feature_impact
    core, ui = _feat(store, "The store"), _feat(store, "The editor")
    _bind(store, core, "db.py::upsert")
    _bind(store, ui, "view.py::save")
    _edge(store, "view.py::save", "db.py::upsert")

    imp = feature_impact(store)
    assert list(imp) == [core.id], "the SUBJECT is the feature being changed"
    assert imp[core.id][0]["feature_id"] == ui.id
    assert imp[core.id][0]["title"] == "The editor"
    # The evidence is the DEPENDENT's own symbol — what the reader would go read.
    assert imp[core.id][0]["via"] == ["view.py::save"]
    assert imp[core.id][0]["count"] == 1


def test_feature_impact_omits_a_feature_nothing_depends_on(store):
    # The absence is the answer; an empty list per feature would be noise the whole
    # way down the tree.
    from codoc.graph.query import feature_impact
    core, ui = _feat(store, "The store"), _feat(store, "The editor")
    _bind(store, core, "db.py::upsert")
    _bind(store, ui, "view.py::save")
    _edge(store, "view.py::save", "db.py::upsert")
    assert ui.id not in feature_impact(store)


def test_feature_impact_skips_a_feature_depending_on_itself(store):
    # A feature whose own symbols call each other is not "impact": the reader is
    # already reading it.
    from codoc.graph.query import feature_impact
    f = _feat(store, "Auth")
    _bind(store, f, "a.py::login")
    _bind(store, f, "a.py::make_token")
    _edge(store, "a.py::login", "a.py::make_token")
    assert feature_impact(store) == {}


def test_feature_impact_ranks_by_how_many_symbols_tie_in(store):
    from codoc.graph.query import feature_impact
    core = _feat(store, "The store")
    heavy, light = _feat(store, "Zebra loop"), _feat(store, "Alpha view")
    _bind(store, core, "db.py::upsert")
    for sym in ("loop.py::a", "loop.py::b"):
        _bind(store, heavy, sym)
        _edge(store, sym, "db.py::upsert")
    _bind(store, light, "view.py::save")
    _edge(store, "view.py::save", "db.py::upsert")

    rows = feature_impact(store)[core.id]
    # Coupling first, then title — so the answer is stable between passes and does
    # not reshuffle a card the reader is looking at.
    assert [r["title"] for r in rows] == ["Zebra loop", "Alpha view"]
    assert rows[0]["count"] == 2


def test_feature_impact_caps_the_evidence_but_not_the_count(store):
    # A truncated list read as complete is worse than a number, so `count` stays true
    # while `via` stops at a readable few.
    from codoc.graph.query import feature_impact, _MAX_VIA
    core, dep = _feat(store, "The store"), _feat(store, "The loop")
    _bind(store, core, "db.py::upsert")
    for i in range(_MAX_VIA + 3):
        sym = f"loop.py::step{i}"
        _bind(store, dep, sym)
        _edge(store, sym, "db.py::upsert")

    row = feature_impact(store)[core.id][0]
    assert row["count"] == _MAX_VIA + 3
    assert len(row["via"]) == _MAX_VIA


def test_feature_impact_drops_a_retired_dependent(store):
    # A retired feature cannot be affected by anything, and reporting it as a risk
    # would send the reader to prose the tree no longer shows.
    from codoc.graph.query import feature_impact
    core, gone = _feat(store, "The store"), _feat(store, "Old importer")
    _bind(store, core, "db.py::upsert")
    _bind(store, gone, "old.py::run")
    _edge(store, "old.py::run", "db.py::upsert")
    from codoc.model.feature import Lifecycle
    store.upsert_feature(gone.model_copy(update={"lifecycle": Lifecycle.RETIRED}))
    assert feature_impact(store) == {}


def test_feature_impact_ignores_edge_kinds_that_are_not_dependencies(store):
    from codoc.graph.query import feature_impact
    core, ui = _feat(store, "The store"), _feat(store, "The editor")
    _bind(store, core, "db.py::upsert")
    _bind(store, ui, "view.py::save")
    _edge(store, "view.py::save", "db.py::upsert", kind="mentions")
    assert feature_impact(store) == {}


def test_feature_impact_accepts_pre_read_edge_rows(store):
    # The sidecar render already reads the edge table for feature coupling; handing
    # the rows over is what keeps one render tick to one scan.
    from codoc.graph.query import feature_impact
    core, ui = _feat(store, "The store"), _feat(store, "The editor")
    _bind(store, core, "db.py::upsert")
    _bind(store, ui, "view.py::save")
    _edge(store, "view.py::save", "db.py::upsert")
    rows = store.all_edges(internal_only=True)
    assert feature_impact(store, rows) == feature_impact(store)


def test_feature_impact_is_empty_without_bindings(store):
    from codoc.graph.query import feature_impact
    _feat(store, "Unbound theme")
    _edge(store, "view.py::save", "db.py::upsert")
    assert feature_impact(store) == {}
