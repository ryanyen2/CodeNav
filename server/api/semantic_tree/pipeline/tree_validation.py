"""Post-assembly tree validation: duplicate leaves, empty abstract nodes, coverage."""

import logging
from typing import List, Set, Tuple

from api.semantic_tree.models import SemanticTree, SemanticNode, CodebaseSnapshot

logger = logging.getLogger(__name__)


def _leaf_key(n: SemanticNode) -> Tuple[str, str] | None:
    """Return (fpath, entity_name) for a concrete leaf; None otherwise."""
    if n.artifact_class != "concrete-leaf" or not n.metadata:
        return None
    fpath = getattr(n.metadata, "fpath", None) or ""
    entity_name = getattr(n.metadata, "entity_name", None) or ""
    if fpath and entity_name:
        return (fpath, entity_name)
    return None


def _walk(node: SemanticNode) -> List[SemanticNode]:
    """Depth-first list of all nodes."""
    out: List[SemanticNode] = [node]
    for c in node.children or []:
        out.extend(_walk(c))
    return out


def validate_tree(tree: SemanticTree, snapshot: CodebaseSnapshot | None) -> List[str]:
    """
    Walk tree and collect validation warnings.
    - No duplicate entity leaf nodes (same fpath::name).
    - No empty abstract nodes (abstract with zero children).
    - If snapshot given: all snapshot entities represented in tree.
    """
    warnings: List[str] = []
    seen_leaf_keys: Set[Tuple[str, str]] = set()
    tree_entity_keys: Set[Tuple[str, str]] = set()
    nodes = _walk(tree.root)

    for n in nodes:
        key = _leaf_key(n)
        if key:
            tree_entity_keys.add(key)
            if key in seen_leaf_keys:
                warnings.append(f"Duplicate leaf node: {key[0]}::{key[1]}")
            seen_leaf_keys.add(key)
        if n.sigil == "~" and n.artifact_class == "abstract":
            child_count = len(n.children) if n.children else 0
            if child_count == 0:
                warnings.append(f"Empty abstract node: feature={n.feature!r} id={n.id!r}")

    if snapshot is not None:
        snapshot_keys = {(e.fpath, e.name) for e in snapshot.all_entities}
        missing = snapshot_keys - tree_entity_keys
        if missing:
            for fpath, name in sorted(missing)[:10]:
                warnings.append(f"Snapshot entity not in tree: {fpath}::{name}")
            if len(missing) > 10:
                warnings.append(f"... and {len(missing) - 10} more missing entities")

    return warnings
