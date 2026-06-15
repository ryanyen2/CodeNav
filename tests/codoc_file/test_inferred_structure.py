"""Lightweight INFERRED structure slices (sidecar v5): kind + See-Also.

Both are pure derivation in :mod:`codoc.codoc_file.render` — never an LLM call,
never a model column, never a ``> …`` steering line, never tree.codoc /
tree.doc.json content:

- ``_compute_kinds`` classifies each feature over the full binding-less taxonomy
  (retired → ``retired``; binding-less + children + realized → ``overview`` theme
  parent; bound → ``reference``; binding-less leaf → ``unclassified``).
- ``_compute_see_also`` ranks + caps the top-N coupled ``feature_edges`` neighbours
  per feature with the edge kind as a one-line rationale.
"""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.render import (
    BINDINGS_FILENAME,
    SEE_ALSO_MAX,
    _compute_feature_edges,
    _compute_kinds,
    _compute_see_also,
    write_tree,
)
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _bind(store, feature_id: str, file: str, symbol: str) -> None:
    store.upsert_binding(
        Binding(feature_id=feature_id, file=file, symbol_path=symbol, fingerprint="h")
    )


def _edge(src_sym: str, dst_sym: str, kind: str = "call") -> dict:
    return {
        "src_file": src_sym.split("::", 1)[0],
        "src_symbol": src_sym,
        "dst_name": dst_sym.rsplit("::", 1)[-1],
        "dst_symbol": dst_sym,
        "dst_file": dst_sym.split("::", 1)[0],
        "kind": kind,
        "internal": 1,
    }


# ---------------------------------------------------------------------------
# _compute_kinds — the binding-less taxonomy
# ---------------------------------------------------------------------------

def test_theme_parent_is_overview_not_unrealized(store):
    # A binding-less parent WITH children and realized=True is an org-pass theme
    # parent — must read as `overview`, never `unrealized`.
    theme = Feature(title="Auth", realized=True)
    child = Feature(title="Login flow", parent_id=theme.id)
    store.upsert_feature(theme)
    store.upsert_feature(child)
    _bind(store, child.id, "auth.py", "auth.py::login")

    kinds = _compute_kinds(store)
    assert kinds[theme.id] == "overview"


def test_bound_leaf_is_reference(store):
    f = Feature(title="Login flow")
    store.upsert_feature(f)
    _bind(store, f.id, "auth.py", "auth.py::login")

    assert _compute_kinds(store)[f.id] == "reference"


def test_retired_feature_is_suppressed(store):
    f = Feature(title="Old thing")
    store.upsert_feature(f)
    store.retire_feature(f.id)

    kinds = _compute_kinds(store)
    # Retired → the suppressing tag, NEVER overview/reference.
    assert kinds[f.id] == "retired"
    assert kinds[f.id] not in ("overview", "reference")


def test_binding_less_leaf_is_unclassified(store):
    # No bindings, no children, realized (e.g. just-detached) → unclassified.
    f = Feature(title="Detached node", realized=True)
    store.upsert_feature(f)

    assert _compute_kinds(store)[f.id] == "unclassified"


def test_unrealized_placeholder_leaf_is_unclassified(store):
    # An unrealized /codoc:plan placeholder with no children/bindings is a leaf,
    # never overview (which requires realized + children).
    f = Feature(title="Planned but unbuilt", realized=False)
    store.upsert_feature(f)

    assert _compute_kinds(store)[f.id] == "unclassified"


def test_binding_less_realized_parent_overrides_leaf(store):
    # A realized parent that HAS children is overview even with no bindings;
    # a realized parent that LOST its children falls back to unclassified.
    parent = Feature(title="Theme", realized=True)
    child = Feature(title="Child", parent_id=parent.id)
    store.upsert_feature(parent)
    store.upsert_feature(child)
    # child is binding-less leaf → unclassified; parent has a child → overview
    kinds = _compute_kinds(store)
    assert kinds[parent.id] == "overview"
    assert kinds[child.id] == "unclassified"


# ---------------------------------------------------------------------------
# _compute_see_also — ranked + capped neighbours with rationale
# ---------------------------------------------------------------------------

def test_see_also_lists_top_neighbours_with_rationale(store):
    src = Feature(title="Caller")
    a = Feature(title="A")
    b = Feature(title="B")
    for f in (src, a, b):
        store.upsert_feature(f)
    _bind(store, src.id, "s.py", "s.py::caller")
    _bind(store, a.id, "a.py", "a.py::a_fn")
    _bind(store, b.id, "b.py", "b.py::b_fn")

    # src calls a twice (weight 2) and imports b once (weight 1).
    store.insert_edges([
        _edge("s.py::caller", "a.py::a_fn", "call"),
    ])
    # bump a's weight to 2 with a second distinct edge kind row
    store.insert_edges([
        {**_edge("s.py::caller", "a.py::a_fn", "call"), "dst_name": "a_fn2"},
    ])
    store.insert_edges([_edge("s.py::caller", "b.py::b_fn", "import")])

    see = _compute_see_also(_compute_feature_edges(store))
    assert src.id in see
    rows = see[src.id]
    # Ranked by weight: a (weight 2) before b (weight 1).
    assert rows[0]["to"] == a.id
    assert rows[0]["weight"] == 2
    assert rows[1]["to"] == b.id
    # Each row carries an edge-kind rationale.
    assert rows[0]["rationale"] == "call"
    assert rows[1]["rationale"] == "import"
    assert rows[0]["kinds"] == ["call"]


def test_see_also_is_capped_at_max(store):
    src = Feature(title="Hub")
    store.upsert_feature(src)
    _bind(store, src.id, "hub.py", "hub.py::hub")
    # Create more than SEE_ALSO_MAX neighbours, each with a distinct weight.
    for i in range(SEE_ALSO_MAX + 4):
        nb = Feature(title=f"N{i}")
        store.upsert_feature(nb)
        _bind(store, nb.id, f"n{i}.py", f"n{i}.py::fn")
        # weight i+1 via i+1 distinct dst_name rows
        for j in range(i + 1):
            store.insert_edges([
                {**_edge("hub.py::hub", f"n{i}.py::fn", "call"), "dst_name": f"fn_{i}_{j}"}
            ])

    rows = _compute_see_also(_compute_feature_edges(store))[src.id]
    assert len(rows) == SEE_ALSO_MAX
    # Capped to the HEAVIEST neighbours (descending weight).
    weights = [r["weight"] for r in rows]
    assert weights == sorted(weights, reverse=True)
    # The single heaviest (last created, weight SEE_ALSO_MAX+4) is first.
    assert rows[0]["weight"] == SEE_ALSO_MAX + 4


def test_feature_with_no_edges_has_empty_see_also(store):
    lonely = Feature(title="Island")
    store.upsert_feature(lonely)
    _bind(store, lonely.id, "i.py", "i.py::island")

    see = _compute_see_also(_compute_feature_edges(store))
    # Absent ⇒ an empty See-Also (no key at all).
    assert lonely.id not in see


# ---------------------------------------------------------------------------
# Sidecar integration + the hard `> …`-channel invariant (R5 / KTD4)
# ---------------------------------------------------------------------------

def test_sidecar_emits_kind_and_see_also_slices(store, tmp_path):
    theme = Feature(title="Theme", realized=True)
    bound = Feature(title="Bound", parent_id=theme.id)
    other = Feature(title="Other", parent_id=theme.id)
    for f in (theme, bound, other):
        store.upsert_feature(f)
    _bind(store, bound.id, "a.py", "a.py::a_fn")
    _bind(store, other.id, "b.py", "b.py::b_fn")
    store.insert_edges([_edge("a.py::a_fn", "b.py::b_fn", "call")])

    write_tree(store, tmp_path)
    sidecar = json.loads((tmp_path / BINDINGS_FILENAME).read_text())

    assert sidecar["version"] == 5  # additive — no further bump
    assert sidecar["feature_kind"][theme.id] == "overview"
    assert sidecar["feature_kind"][bound.id] == "reference"
    assert sidecar["feature_see_also"][bound.id][0]["to"] == other.id
    assert sidecar["feature_see_also"][bound.id][0]["rationale"] == "call"


def test_see_also_never_emits_a_steering_line(store, tmp_path):
    # See-Also is sidecar metadata ONLY — no `> …` line may appear anywhere in
    # tree.codoc, and the slice must never carry a blockquote-shaped string.
    src = Feature(title="Source")
    dst = Feature(title="Dest")
    store.upsert_feature(src)
    store.upsert_feature(dst)
    _bind(store, src.id, "s.py", "s.py::src")
    _bind(store, dst.id, "d.py", "d.py::dst")
    store.insert_edges([_edge("s.py::src", "d.py::dst", "call")])

    path = write_tree(store, tmp_path)
    tree_text = path.read_text()
    # No steering-comment line anywhere in the rendered tree.
    for line in tree_text.splitlines():
        assert not line.lstrip().startswith(">"), f"unexpected `> …` line: {line!r}"

    # And the See-Also slice itself carries no blockquote-shaped payload.
    see = _compute_see_also(_compute_feature_edges(store))
    for rows in see.values():
        for r in rows:
            assert not str(r.get("rationale", "")).lstrip().startswith(">")
