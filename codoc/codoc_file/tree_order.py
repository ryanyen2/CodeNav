"""tree_order.py — the ONE parent→children walk shared by the tree pane
(``render.py``'s ``render_tree``) and the doc projection (``doc_render.py``'s
``build_doc_from_store``).

Both used to compute this independently and disagreed on one case: a feature
whose ``parent_id`` points outside the live set (its parent was retired, or the
id is otherwise dangling). ``render_tree`` walked live rows top-down via
``store.children()``, which never surfaces such an orphan (its parent never
appears in any ``children()`` result, so the orphan's whole subtree is silently
unreachable — invisible in the tree). The doc projection instead promoted the
orphan to a root. Same store, two different projections of "the tree" — the
nav and the doc body would disagree about where a feature lives, or whether it
exists at all.

There is exactly one correct answer: a dangling ``parent_id`` must not hide
data, so the orphan is promoted to a root (merged with genuine roots in
creation order) in BOTH projections. This module is that one walk, so the two
call sites structurally cannot diverge again."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codoc.model.feature import Feature


def children_map(features: "list[Feature]") -> "dict[str | None, list[Feature]]":
    """Group ``features`` by ``parent_id``, promoting any feature whose parent
    is missing from ``features`` (retired / dangling) to a root (key ``None``).
    Preserves ``features``' incoming order within each group."""
    by_id = {f.id: f for f in features}
    children: "dict[str | None, list[Feature]]" = {}
    for f in features:
        key = f.parent_id if (f.parent_id and f.parent_id in by_id) else None
        children.setdefault(key, []).append(f)
    return children


def preorder(features: "list[Feature]") -> "list[Feature]":
    """Depth-first pre-order over ``features`` via :func:`children_map`.

    Cycle-safe: a ``seen`` guard bounds the walk, and anything never reached
    (a pre-existing cycle among live ids) is appended afterward so it stays
    visible rather than silently dropped."""
    children = children_map(features)
    ordered: "list[Feature]" = []
    seen: set[str] = set()

    def walk(parent_key: "str | None") -> None:
        for f in children.get(parent_key, []):
            if f.id in seen:
                continue
            seen.add(f.id)
            ordered.append(f)
            walk(f.id)

    walk(None)
    if len(ordered) != len(features):
        for f in features:
            if f.id not in seen:
                ordered.append(f)
    return ordered
