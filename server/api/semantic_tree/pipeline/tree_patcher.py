"""Patch engine: apply EntityDelta to an existing SemanticTree in-place (no full rebuild, no embeddings)."""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

from api.semantic_tree.models import (
    CodebaseSnapshot,
    CodeEntity,
    SemanticTree,
    SemanticNode,
    Contract,
    NodeMetadata,
    DepEdge,
)
from api.semantic_tree.state.models import SemanticCacheEntry
from api.semantic_tree.state.fingerprint import compute_entity_fingerprint

logger = logging.getLogger(__name__)

SIGIL_FILE = "%"
SIGIL_ABSTRACT = "~"
SIGIL_FUNC = "$"
SIGIL_CLASS = "^"


def _entity_key(e: CodeEntity) -> str:
    return f"{e.fpath}::{e.name}"


@dataclass
class PatchResult:
    modified_nodes: List[str] = field(default_factory=list)
    added_nodes: List[str] = field(default_factory=list)
    removed_nodes: List[str] = field(default_factory=list)
    needs_feature_update: List[str] = field(default_factory=list)  # entity keys needing LLM features


def build_node_index(tree: SemanticTree) -> Tuple[Dict[str, SemanticNode], Dict[str, SemanticNode]]:
    """Walk tree; return (by_id: node.id -> node, by_fpath: fpath -> first file node with that fpath)."""
    by_id: Dict[str, SemanticNode] = {}
    by_fpath: Dict[str, SemanticNode] = {}

    def walk(node: SemanticNode) -> None:
        if node.id:
            by_id[node.id] = node
        if node.sigil == SIGIL_FILE and node.metadata and node.metadata.fpath:
            if node.metadata.fpath not in by_fpath:
                by_fpath[node.metadata.fpath] = node
        for c in node.children:
            walk(c)

    walk(tree.root)
    return by_id, by_fpath


def _contract_from_entity(e: CodeEntity) -> Contract:
    c = Contract()
    if e.entity_type == "class" and e.signature:
        c.cls = e.signature
    elif e.entity_type in ("function", "method") and e.signature:
        c.sig = e.signature
    return c


def patch_modified_entity(node: SemanticNode, entity: CodeEntity) -> None:
    """Update contract (sig/cls) and line_range from AST; keep feature and status unchanged."""
    if node.contract is None:
        node.contract = Contract()
    node.contract.sig = None
    node.contract.cls = None
    if entity.entity_type == "class" and entity.signature:
        node.contract.cls = entity.signature
    elif entity.entity_type in ("function", "method") and entity.signature:
        node.contract.sig = entity.signature
    if node.metadata:
        node.metadata.line_range = entity.line_range


def patch_removed_entity(
    node: SemanticNode,
    parent: Optional[SemanticNode],
    by_id: Dict[str, SemanticNode],
    by_fpath: Dict[str, SemanticNode],
    result: PatchResult,
) -> None:
    """Remove node from parent.children; if file node is empty after removal, cascade remove. Update file exp contract."""
    if parent and node in parent.children:
        parent.children.remove(node)
    result.removed_nodes.append(node.id)
    if node.id in by_id:
        del by_id[node.id]

    if node.sigil in (SIGIL_FUNC, SIGIL_CLASS) and parent and parent.sigil == SIGIL_FILE:
        remaining = [c for c in parent.children if c.sigil in (SIGIL_FUNC, SIGIL_CLASS)]
        if parent.contract:
            parent.contract.exp = ", ".join(
                (c.metadata.entity_name or "") for c in remaining if c.metadata and c.metadata.entity_name
            ) if remaining else ""
        if not parent.children and parent.parent:
            grand = parent.parent
            if parent in grand.children:
                grand.children.remove(parent)
            if parent.id in by_id:
                del by_id[parent.id]
            if parent.metadata and parent.metadata.fpath:
                by_fpath.pop(parent.metadata.fpath, None)
            result.removed_nodes.append(parent.id)


def patch_added_entity(
    entity: CodeEntity,
    file_node: SemanticNode,
    semantic_cache: Dict[str, SemanticCacheEntry],
    result: PatchResult,
    by_id: Dict[str, SemanticNode],
) -> SemanticNode:
    """Create new leaf node under file_node. Check semantic_cache by content_hash for feature; if miss, use entity.name and #draft."""
    content_hash, _ = compute_entity_fingerprint(entity)
    entry = semantic_cache.get(content_hash)
    if entry and entry.features:
        feature = entry.features[0]
        status = "resolved"
    else:
        feature = entity.name.replace("_", " ")
        status = "draft"
        result.needs_feature_update.append(_entity_key(entity))

    sigil = SIGIL_CLASS if entity.entity_type == "class" else SIGIL_FUNC
    node_id = _entity_key(entity)
    meta = NodeMetadata(
        type=entity.entity_type,
        fpath=entity.fpath,
        entity_name=entity.name,
        line_range=entity.line_range,
    )
    contract = _contract_from_entity(entity)
    leaf = SemanticNode(
        id=node_id,
        sigil=sigil,
        artifact_class="concrete-leaf",
        feature=feature,
        metadata=meta,
        contract=contract,
        status=status,
        children=[],
    )
    leaf.parent = file_node
    file_node.children.append(leaf)
    by_id[node_id] = leaf
    result.added_nodes.append(node_id)

    if file_node.contract:
        current_exp = (file_node.contract.exp or "").strip()
        names = [n.strip() for n in current_exp.split(",") if n.strip()]
        if entity.name not in names:
            names.append(entity.name)
            file_node.contract.exp = ", ".join(names)
    return leaf


def find_or_create_file_node(
    tree: SemanticTree,
    fpath: str,
    by_id: Dict[str, SemanticNode],
    by_fpath: Dict[str, SemanticNode],
    hierarchy_cache: List[Dict],
) -> SemanticNode:
    """
    Find existing file node for fpath, or create one. For new files: check hierarchy_cache for sibling
    file in same directory, reuse that abstract chain; fallback: attach under root with #draft.
    """
    if fpath in by_fpath:
        return by_fpath[fpath]

    root = tree.root
    parent: Optional[SemanticNode] = None
    path_parts: List[str] = ["uncategorized"]

    for h in hierarchy_cache:
        groups = h.get("entity_groups")
        if not isinstance(groups, list):
            continue
        if fpath in groups:
            path_str = h.get("path", "")
            path_parts = [p.strip() for p in path_str.split("/") if p.strip()]
            if not path_parts:
                path_parts = ["uncategorized"]
            for sibling_fpath in groups:
                if sibling_fpath != fpath and sibling_fpath in by_fpath:
                    parent = by_fpath[sibling_fpath].parent
                    break
            break

    if parent is None:
        def ensure_abstract_chain(r: SemanticNode, parts: List[str]) -> SemanticNode:
            if not parts:
                return r
            p = r
            prefix = ""
            for i, seg in enumerate(parts):
                prefix = f"{prefix}/{seg}".lstrip("/") if prefix else seg
                feature = seg.replace("-", " ").replace("_", " ")
                existing = next(
                    (c for c in p.children if c.sigil == SIGIL_ABSTRACT and (c.feature or "").strip() == feature),
                    None,
                )
                if existing is not None:
                    p = existing
                else:
                    child = SemanticNode(
                        id=prefix,
                        sigil=SIGIL_ABSTRACT,
                        artifact_class="abstract",
                        feature=feature,
                        metadata=NodeMetadata(),
                        contract=Contract(),
                        status="resolved",
                        children=[],
                    )
                    child.parent = p
                    p.children.append(child)
                    p = child
            return p
        parent = ensure_abstract_chain(root, path_parts)
    file_name = fpath.split("/")[-1].replace(".py", "").replace("_", " ")
    file_node = SemanticNode(
        id=fpath,
        sigil=SIGIL_FILE,
        artifact_class="concrete-file",
        feature=file_name,
        metadata=NodeMetadata(type="file", fpath=fpath),
        contract=Contract(),
        status="draft",
        children=[],
    )
    file_node.parent = parent
    parent.children.append(file_node)
    by_id[fpath] = file_node
    by_fpath[fpath] = file_node
    return file_node


def patch_tree(
    tree: SemanticTree,
    delta: Any,
    snapshot: CodebaseSnapshot,
    hierarchy_cache: List[Dict],
    semantic_cache: Dict[str, SemanticCacheEntry],
) -> PatchResult:
    """
    Apply delta to tree in-place. Modified: update contract; Removed: remove node, cascade empty file;
    Added: find/create file parent, create leaf; Renamed: update node id and metadata.
    Update file exp contracts and deps (remove edges for removed, add from snapshot).
    """
    from api.semantic_tree.state.delta import EntityDelta

    if not isinstance(delta, EntityDelta):
        raise TypeError("delta must be EntityDelta")
    result = PatchResult()
    by_id, by_fpath = build_node_index(tree)

    entity_by_key: Dict[str, CodeEntity] = {_entity_key(e): e for e in snapshot.all_entities}

    for entity in delta.modified:
        key = _entity_key(entity)
        node = by_id.get(key)
        if node:
            patch_modified_entity(node, entity)
            result.modified_nodes.append(key)

    for old_key, _old_fp in delta.removed:
        node = by_id.get(old_key)
        if node:
            patch_removed_entity(node, node.parent, by_id, by_fpath, result)
            if node.sigil == SIGIL_FILE and node.metadata and node.metadata.fpath:
                by_fpath.pop(node.metadata.fpath, None)

    for new_entity, old_key in delta.renamed:
        node = by_id.get(old_key)
        if node:
            new_key = _entity_key(new_entity)
            if node.id in by_id:
                del by_id[node.id]
            old_fpath = node.metadata.fpath if node.metadata else None
            new_fpath = new_entity.fpath
            if old_fpath != new_fpath and node.parent:
                node.parent.children.remove(node)
                file_node = find_or_create_file_node(tree, new_fpath, by_id, by_fpath, hierarchy_cache)
                node.parent = file_node
                file_node.children.append(node)
            node.id = new_key
            if node.metadata:
                node.metadata.fpath = new_entity.fpath
                node.metadata.entity_name = new_entity.name
                node.metadata.line_range = new_entity.line_range
            node.contract = _contract_from_entity(new_entity)
            by_id[new_key] = node
            result.modified_nodes.append(new_key)

    for entity in delta.added:
        key = _entity_key(entity)
        if key in by_id:
            continue
        file_node = find_or_create_file_node(tree, entity.fpath, by_id, by_fpath, hierarchy_cache)
        patch_added_entity(entity, file_node, semantic_cache, result, by_id)

    for entity in delta.modified:
        key = _entity_key(entity)
        file_node = by_fpath.get(entity.fpath)
        if file_node and file_node.contract:
            names = [e.name for e in snapshot.all_entities if e.fpath == entity.fpath]
            file_node.contract.exp = ", ".join(names) if names else ""

    removed_keys = {ek for ek, _ in delta.removed}
    for rn in result.removed_nodes:
        removed_keys.add(rn)
    entity_keys_in_snapshot = {_entity_key(e) for e in snapshot.all_entities}

    def entity_id_from_key(k: str) -> Optional[str]:
        if "::" in k:
            return k
        e = entity_by_key.get(k)
        return _entity_key(e) if e else None

    new_deps: List[DepEdge] = []
    for imp in snapshot.all_imports:
        from_id = imp.source_entity or imp.source_fpath or "unknown"
        to_id = imp.target_entity
        from_ok = from_id in entity_keys_in_snapshot or not any(from_id == ek for ek in removed_keys)
        to_ok = to_id in entity_keys_in_snapshot
        if from_ok and to_ok:
            rel = imp.relation if imp.relation in ("imports", "invokes", "inherits", "type-refs") else "imports"
            new_deps.append(
                DepEdge(
                    from_entity=from_id,
                    to=to_id,
                    relation=rel,
                    from_external=imp.is_external,
                    to_external=None,
                )
            )
    tree.deps = new_deps

    return result
