"""Cheap, embedding-free selection of the subtree the LLM needs.

Seeds = features already bound in any file the change touched (exact file match
via the bindings index); expand one hop to parents + direct children. The seed
subtree is sent with full descriptions + bindings; the whole tree's titles are
sent separately as de-duplication context. File-locality is enough — a new chunk
almost always lands in a file that already hosts related features.
"""
from __future__ import annotations

from codoc.loop.diff import ChangeSet
from codoc.store.db import Store


def select_relevant_subtree(cs: ChangeSet, store: Store) -> tuple[list[dict], list[dict]]:
    """Return ``(subtree, all_titles)`` as JSON-ready dicts for the LLM prompt."""
    touched = cs.touched_files()
    seeds = {b.feature_id for b in store.bindings_in_files(touched)}

    ids: set[str] = set(seeds)
    for sid in seeds:
        f = store.get_feature(sid)
        if f and f.parent_id:
            ids.add(f.parent_id)
        for child in store.children(sid):
            ids.add(child.id)

    subtree: list[dict] = []
    for fid in ids:
        f = store.get_feature(fid)
        if not f or f.retired:
            continue
        subtree.append({
            "id": f.id,
            "title": f.title,
            "description": f.description,
            "parent_id": f.parent_id,
            "bindings": [b.symbol_path for b in store.bindings_for_feature(fid)],
        })

    all_titles = [
        {"id": f.id, "title": f.title, "parent_id": f.parent_id}
        for f in store.list_features()
    ]
    return subtree, all_titles
