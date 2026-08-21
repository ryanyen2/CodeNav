"""U5 — the diagram codec: deterministic lift from the graph, deterministic
edge-delta lower, and the draft fallback for unmappable edits."""
from __future__ import annotations

import pytest

from codoc.blocks.base import LiftContext, LowerContext
from codoc.blocks.diagram import DiagramPlugin
from codoc.model.binding import Binding
from codoc.model.block import Block
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _edge(store, src, dst):
    # Files are derived from the symbol paths, so a test can wire an edge that
    # crosses files -- which is the case where the node-id scheme matters.
    store.insert_edges([{
        "src_file": src.split("::")[0], "src_symbol": src,
        "dst_name": dst.split("::")[-1], "dst_symbol": dst,
        "dst_file": dst.split("::")[0], "kind": "call", "internal": 1,
    }])


def _bind(store, feature, symbol):
    b = Binding(feature_id=feature.id, file=symbol.split("::")[0],
                symbol_path=symbol, fingerprint="h")
    store.upsert_binding(b)
    return b


def test_lift_renders_bound_neighborhood(store):
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::login", fingerprint="h")
    store.upsert_binding(b)
    _edge(store, "a.py::login", "a.py::make_token")
    out = DiagramPlugin().lift(LiftContext(feature=f, bindings=[b], store=store))
    assert out.changed
    assert "flowchart TB" in out.content
    # Ids carry the whole symbol path (two files' `login` are two boxes); the label
    # carries the leaf, which is what a reader is there to read.
    assert "a_py__login --> a_py__make_token" in out.content
    assert '["login"]' in out.content and '["make_token"]' in out.content


def test_lift_no_change_when_identical(store):
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::login", fingerprint="h")
    store.upsert_binding(b)
    _edge(store, "a.py::login", "a.py::make_token")
    p = DiagramPlugin()
    first = p.lift(LiftContext(feature=f, bindings=[b], store=store))
    blk = Block(feature_id=f.id, kind="diagram", content=first.content)
    again = p.lift(LiftContext(feature=f, bindings=[b], store=store, block=blk))
    assert not again.changed


def test_lower_removed_edge_becomes_directive():
    f = Feature(title="Auth")
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::login", fingerprint="h")
    old = Block(feature_id=f.id, kind="diagram", content="flowchart TB\n  login --> make_token")
    new = Block(id=old.id, feature_id=f.id, kind="diagram", content="flowchart TB\n  login")
    res = DiagramPlugin().lower(LowerContext(feature=f, old_block=old, new_block=new, bindings=[b]))
    assert res.kind == "directive"
    assert "Remove the dependency" in res.text
    assert "login" in res.text and "make_token" in res.text


def test_lower_added_edge_becomes_directive():
    f = Feature(title="Auth")
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::login", fingerprint="h")
    old = Block(feature_id=f.id, kind="diagram", content="flowchart TB\n  login")
    new = Block(id=old.id, feature_id=f.id, kind="diagram", content="flowchart TB\n  login --> audit")
    res = DiagramPlugin().lower(LowerContext(feature=f, old_block=old, new_block=new, bindings=[b]))
    assert res.kind == "directive"
    assert "Add a dependency" in res.text


def test_lower_unmappable_edit_is_draft():
    f = Feature(title="Auth")
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::login", fingerprint="h")
    old = Block(feature_id=f.id, kind="diagram", content="flowchart TB\n  login --> audit")
    new = Block(id=old.id, feature_id=f.id, kind="diagram",
                content="flowchart TB\n  login --> audit\n  X[some freeform node]")
    res = DiagramPlugin().lower(LowerContext(feature=f, old_block=old, new_block=new, bindings=[b]))
    assert res.kind == "draft"


def test_lower_unbound_is_noop():
    f = Feature(title="Ambient")
    old = Block(feature_id=f.id, kind="diagram", content="flowchart TB\n  a --> b")
    new = Block(id=old.id, feature_id=f.id, kind="diagram", content="flowchart TB\n  a")
    res = DiagramPlugin().lower(LowerContext(feature=f, old_block=old, new_block=new, bindings=[]))
    assert res.kind == "noop"


# --------------------------------------------------------------------------
# the picture answers "how are these related" (Sillito group 3)
# --------------------------------------------------------------------------

def test_the_callers_are_drawn_and_not_only_the_callees(store):
    """Half of "how are these related" is who uses this.

    The first version walked outward only, which answers "what does this feature
    use" and is silent on the question that decides whether a change here is safe.
    """
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = _bind(store, f, "a.py::login")
    _edge(store, "a.py::login", "a.py::make_token")   # callee
    _edge(store, "web.py::handler", "a.py::login")    # caller
    out = DiagramPlugin().lift(LiftContext(feature=f, bindings=[b], store=store))
    assert "a_py__login --> a_py__make_token" in out.content
    assert "web_py__handler --> a_py__login" in out.content


def test_two_files_sharing_a_symbol_name_are_two_boxes(store):
    """The false-edge bug: ids used to collapse to the leaf token.

    `a.py::render` and `b.py::render` became one box, and the diagram then drew an
    edge between two functions that never call each other -- a picture that invents
    a relationship, which is worse than no picture at all.
    """
    f = Feature(title="Rendering")
    store.upsert_feature(f)
    b = _bind(store, f, "a.py::caller")
    _edge(store, "a.py::caller", "a.py::render")
    _edge(store, "b.py::render", "a.py::caller")
    out = DiagramPlugin().lift(LiftContext(feature=f, bindings=[b], store=store))
    assert "a_py__render" in out.content and "b_py__render" in out.content
    # …and no edge between the two of them.
    assert "a_py__render --> b_py__render" not in out.content
    assert "b_py__render --> a_py__render" not in out.content


def test_neighbours_are_grouped_by_the_feature_that_owns_them(store):
    # The tree's own vocabulary: the picture should read as "this feature depends on
    # that feature", not as a symbol graph the reader has to map back to intent.
    f = Feature(title="Auth")
    other = Feature(title="Token minting")
    store.upsert_feature(f)
    store.upsert_feature(other)
    b = _bind(store, f, "a.py::login")
    _bind(store, other, "tok.py::mint")
    _edge(store, "a.py::login", "tok.py::mint")
    out = DiagramPlugin().lift(LiftContext(feature=f, bindings=[b], store=store))
    assert '["Auth"]' in out.content
    assert '["Token minting"]' in out.content


def test_a_neighbour_no_feature_covers_is_said_to_be_outside_the_tree(store):
    # Not a tidiness fallback: code the tree does not cover is exactly what a reader
    # wants to notice in a picture of what this feature touches.
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = _bind(store, f, "a.py::login")
    _edge(store, "a.py::login", "legacy.py::check")
    out = DiagramPlugin().lift(LiftContext(feature=f, bindings=[b], store=store))
    assert "legacy.py (not in the tree)" in out.content


def test_the_same_graph_lifts_to_the_same_picture(store):
    # Ordering is deterministic throughout, or every pass reports a change and the
    # block refreshes forever.
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = _bind(store, f, "a.py::login")
    for dst in ("a.py::z", "a.py::a", "a.py::m"):
        _edge(store, "a.py::login", dst)
    first = DiagramPlugin().lift(LiftContext(feature=f, bindings=[b], store=store))
    again = DiagramPlugin().lift(LiftContext(
        feature=f, bindings=[b], store=store,
        block=Block(feature_id=f.id, kind="diagram", content=first.content)))
    assert not again.changed


def test_a_hub_symbols_neighbourhood_is_capped_and_the_cut_is_drawn(store):
    """An unreadable picture answers the question no better than a blank one.

    What matters as much as the cap is that the reader is TOLD: a diagram that
    silently truncates reads as the whole story.
    """
    from codoc.blocks.diagram import MAX_NEIGHBOURS

    f = Feature(title="Hub")
    store.upsert_feature(f)
    b = _bind(store, f, "a.py::hub")
    for i in range(MAX_NEIGHBOURS + 3):
        _edge(store, "a.py::hub", f"a.py::n{i:02d}")
    out = DiagramPlugin().lift(LiftContext(feature=f, bindings=[b], store=store))
    assert out.content.count("-->") == MAX_NEIGHBOURS
    assert "+3 more related symbols" in out.content


# --------------------------------------------------------------------------
# the round trip back to code
# --------------------------------------------------------------------------

def test_an_edge_deleted_from_a_lifted_diagram_names_the_real_symbol(store):
    """The round trip that makes the codec bidirectional rather than decorative.

    Nothing in the content carries the id -> symbol mapping. Both halves rebuild it
    from the feature's bindings and their neighbours, so an author deleting a line
    from the picture the lift drew gets a directive about the actual code.
    """
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = _bind(store, f, "a.py::login")
    _edge(store, "a.py::login", "a.py::make_token")
    lifted = DiagramPlugin().lift(LiftContext(feature=f, bindings=[b], store=store))
    edited = "\n".join(l for l in lifted.content.splitlines() if "-->" not in l)

    old = Block(feature_id=f.id, kind="diagram", content=lifted.content)
    new = Block(id=old.id, feature_id=f.id, kind="diagram", content=edited)
    res = DiagramPlugin().lower(LowerContext(
        feature=f, old_block=old, new_block=new, bindings=[b], store=store))
    assert res.kind == "directive"
    assert "`a.py::login`" in res.text and "`a.py::make_token`" in res.text


def test_an_edge_to_a_box_with_no_code_quotes_the_authors_own_words(store):
    # An edge drawn to something that does not exist is a request to create it. The
    # sanitized id would hand an agent a mangled string to go searching for, so the
    # label the author wrote is what the directive carries.
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = _bind(store, f, "a.py::login")
    old = Block(feature_id=f.id, kind="diagram",
                content='flowchart TB\n  a_py__login["login"]')
    new = Block(id=old.id, feature_id=f.id, kind="diagram", content=(
        'flowchart TB\n  a_py__login["login"]\n  cache["a per-request cache"]\n'
        "  a_py__login --> cache"))
    res = DiagramPlugin().lower(LowerContext(
        feature=f, old_block=old, new_block=new, bindings=[b], store=store))
    assert res.kind == "directive"
    assert "`a.py::login`" in res.text
    assert "`a per-request cache`" in res.text


def test_a_relabel_is_held_rather_than_guessed_at(store):
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = _bind(store, f, "a.py::login")
    old = Block(feature_id=f.id, kind="diagram",
                content='flowchart TB\n  a_py__login["login"]')
    new = Block(id=old.id, feature_id=f.id, kind="diagram",
                content='flowchart TB\n  a_py__login["sign in"]')
    res = DiagramPlugin().lower(LowerContext(
        feature=f, old_block=old, new_block=new, bindings=[b], store=store))
    assert res.kind == "draft"
    assert "sign in" in res.text


def test_regrouping_without_touching_an_edge_asks_for_no_code_change(store):
    # Moving boxes around says how the author wants to READ the picture. Turning that
    # into a code directive would edit source because somebody tidied a diagram.
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = _bind(store, f, "a.py::login")
    old = Block(feature_id=f.id, kind="diagram", content=(
        'flowchart TB\n  subgraph g1["Auth"]\n    a_py__login["login"]\n  end\n'
        "  a_py__login --> a_py__make_token"))
    new = Block(id=old.id, feature_id=f.id, kind="diagram", content=(
        'flowchart TB\n  subgraph g1["Signing in"]\n    a_py__login["login"]\n  end\n'
        "  a_py__login --> a_py__make_token"))
    res = DiagramPlugin().lower(LowerContext(
        feature=f, old_block=old, new_block=new, bindings=[b], store=store))
    assert res.kind == "noop"
