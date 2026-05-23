"""
codoc.pipelines._shared.prompt_context — shared tree-context builders for both
the bootstrap and reflective pipelines.

Provides:
  build_tree_context(store)         → dict with root features + naming examples
  build_neighborhood_features(...)  → 1-hop neighbourhood (moved from reflective/propose.py)
"""

from __future__ import annotations

from codoc.storage.sqlite_store import SQLiteStore
from codoc.core.logging import get_logger

_log = get_logger(__name__)

_MAX_ROOT_FEATURES = 12  # cap to avoid bloating prompt context


def build_tree_context(store: SQLiteStore) -> dict:
    """Return a dict describing the existing feature tree for LLM context.

    Includes:
      - ``root_features``: top-level features (slug + title + intent), capped at
        _MAX_ROOT_FEATURES, so the LLM can see the naming style and grain.
      - ``naming_style``: a short reminder of the slug convention.
      - ``total_feature_count``: rough scale indicator.

    Returns an empty dict when the store has no features yet.
    """
    try:
        all_features = store.list_features()
    except Exception:
        return {}

    active = [f for f in all_features if not f.retired]
    if not active:
        return {}

    # Root features = those with no parent_uuid
    roots = [f for f in active if f.parent_uuid is None]
    sample = roots[:_MAX_ROOT_FEATURES] if roots else active[:_MAX_ROOT_FEATURES]

    return {
        "naming_style": "kebab-case noun-phrase slugs, 3-6 word sentence-case titles",
        "total_feature_count": len(active),
        "root_features": [
            {"slug": f.slug, "title": f.title or f.slug, "intent": f.intent[:120]}
            for f in sample
        ],
    }


def build_neighborhood_features(
    file: str,
    symbol_path: str,
    store: SQLiteStore,
    max_neighbors: int = 20,
) -> list[dict]:
    """Build the 1-hop neighbourhood of features for a changed chunk.

    Neighbours are ranked by proximity in three tiers:
      1. Features with bindings in the same file.
      2. Features whose bindings share the same class/module prefix.
      3. Features that are structurally adjacent by symbol-path prefix.

    Each returned dict: ``{uuid, slug, intent, binding_count}``.
    """
    all_bindings = store.get_all_bindings()

    same_file: dict[str, int] = {}
    adjacent: dict[str, int] = {}
    structural: dict[str, int] = {}

    changed_prefix = _symbol_prefix(symbol_path)
    changed_parts = symbol_path.rsplit(".", 1)

    for b in all_bindings:
        fid = b.feature_uuid
        bsp = b.anchor.symbol_path or ""

        if b.anchor.file == file:
            same_file[fid] = same_file.get(fid, 0) + 1
        elif changed_prefix and _symbol_prefix(bsp) == changed_prefix:
            if fid not in same_file:
                adjacent[fid] = adjacent.get(fid, 0) + 1
        elif len(changed_parts) == 2:
            parent_prefix = changed_parts[0]
            if bsp.startswith(parent_prefix) and fid not in same_file and fid not in adjacent:
                structural[fid] = structural.get(fid, 0) + 1

    ordered: list[str] = []
    seen: set[str] = set()
    for tier in (same_file, adjacent, structural):
        for uid in sorted(tier, key=lambda u: -tier[u]):
            if uid not in seen:
                ordered.append(uid)
                seen.add(uid)
            if len(ordered) >= max_neighbors:
                break
        if len(ordered) >= max_neighbors:
            break

    binding_count: dict[str, int] = {}
    for b in all_bindings:
        binding_count[b.feature_uuid] = binding_count.get(b.feature_uuid, 0) + 1

    result: list[dict] = []
    for uid in ordered[:max_neighbors]:
        feature = store.get_feature(uid)
        if feature is None:
            continue
        result.append({
            "uuid": uid,
            "slug": feature.slug,
            "intent": feature.intent,
            "binding_count": binding_count.get(uid, 0),
        })
    return result


def _symbol_prefix(symbol_path: str) -> str:
    if "::" in symbol_path:
        file_part, entity_part = symbol_path.split("::", 1)
        parts = entity_part.split(".")
        if len(parts) > 1:
            return f"{file_part}::{parts[0]}"
        return file_part
    return symbol_path.rsplit(".", 1)[0] if "." in symbol_path else symbol_path
