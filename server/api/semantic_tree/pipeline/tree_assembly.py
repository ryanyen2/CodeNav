"""Step 6: Tree assembly — merge hierarchy, grounding metadata, and import edges into SemanticTree."""

import logging
from typing import List, Dict, Optional, Tuple, Set

from api.semantic_tree.models import (
    CodebaseSnapshot,
    SemanticFeature,
    HierarchyMapping,
    SemanticNode,
    SemanticTree,
    DepEdge,
    NodeMetadata,
    Contract,
    ImportEdge,
    CodeEntity,
)

logger = logging.getLogger(__name__)

SIGIL_FILE = "%"
SIGIL_DIR = "/"
SIGIL_FUNC = "$"
SIGIL_CLASS = "^"
SIGIL_ABSTRACT = "~"


def _entity_key(e: CodeEntity) -> Tuple[str, str]:
    return (e.fpath, e.name)


def _ensure_abstract_chain(
    root: SemanticNode,
    parts: List[str],
    path: str,
) -> SemanticNode:
    """
    Walk or create abstract nodes for the given path parts (any depth).
    Returns the leaf abstract node under which to attach file nodes.
    """
    if not parts:
        return root
    parent = root
    prefix = ""
    for i, segment in enumerate(parts):
        prefix = f"{prefix}/{segment}" if prefix else segment
        node_id = path if i == len(parts) - 1 and prefix == path else f"{path}/_level_{i}"
        feature = segment.replace("-", " ").replace("_", " ")
        existing = next((c for c in parent.children if c.sigil == SIGIL_ABSTRACT and c.feature == feature), None)
        if existing is not None:
            parent = existing
        else:
            child = SemanticNode(
                id=node_id,
                sigil=SIGIL_ABSTRACT,
                artifact_class="abstract",
                feature=feature,
                metadata=NodeMetadata(),
                contract=Contract(),
                status="resolved",
                children=[],
            )
            parent.children.append(child)
            child.parent = parent
            parent = child
    return parent


def _feature_by_entity(
    features: List[SemanticFeature],
    entity_name: str,
) -> str:
    """Primary feature string for an entity (first feature or entity name)."""
    for sf in features:
        if sf.entity_name == entity_name and sf.features:
            return sf.features[0]
    return entity_name.replace("_", " ")


def _contract_from_entity(e: CodeEntity) -> Contract:
    c = Contract()
    if e.entity_type == "class" and e.signature:
        c.cls = e.signature
    elif e.entity_type in ("function", "method") and e.signature:
        c.sig = e.signature
    return c


def _entity_to_dep_relation(imp: ImportEdge) -> str:
    return imp.relation if imp.relation in ("imports", "invokes", "inherits", "type-refs") else "imports"


def _file_line_range(entities: List[CodeEntity]) -> Optional[Tuple[int, int]]:
    """Line range spanning all entities in the file (for file-node correspondence highlight)."""
    if not entities:
        return None
    starts = [e.line_range[0] for e in entities if e.line_range]
    ends = [e.line_range[1] for e in entities if e.line_range]
    if not starts or not ends:
        return None
    return (min(starts), max(ends))


def assemble_tree(
    snapshot: CodebaseSnapshot,
    semantic_features: List[SemanticFeature],
    hierarchy_mappings: List[HierarchyMapping],
    include_deps: bool = True,
) -> SemanticTree:
    """
    Build SemanticTree from snapshot, features, and hierarchy.
    - Creates ~ abstract nodes for each 3-level path.
    - Creates % file nodes and $/^ leaf nodes with grounding.
    - Sets contract from extraction (sig, cls); file nodes get exp from entity list.
    - Adds DepEdge from ImportEdge (source/target by entity or module).
    """
    entity_by_key: Dict[Tuple[str, str], CodeEntity] = {}
    for e in snapshot.all_entities:
        entity_by_key[_entity_key(e)] = e

    # File path -> list of entities (for exp and children)
    file_entities: Dict[str, List[CodeEntity]] = {}
    for e in snapshot.all_entities:
        file_entities.setdefault(e.fpath, []).append(e)

    # Group name in hierarchy = file path. path -> list of file paths (entity_groups)
    path_to_files: Dict[str, List[str]] = {}
    for hm in hierarchy_mappings:
        path_to_files[hm.path] = list(hm.entity_groups)

    root = SemanticNode(
        id="__root",
        sigil=SIGIL_ABSTRACT,
        artifact_class="abstract",
        feature="",
        metadata=NodeMetadata(),
        contract=Contract(),
        status="resolved",
        children=[],
    )

    placed_entities: Set[Tuple[str, str]] = set()
    # Per parent node id: set of fpaths already placed under it (avoid duplicate file nodes)
    placed_files_by_parent: Dict[str, Set[str]] = {}

    for path, file_paths in path_to_files.items():
        parts = [p.strip() for p in path.split("/") if p.strip()]
        if not parts:
            parts = ["uncategorized"]
        parent_for_files = _ensure_abstract_chain(root, parts, path)

        for fpath in file_paths:
            parent_id = parent_for_files.id or ""
            placed_files = placed_files_by_parent.setdefault(parent_id, set())
            if fpath in placed_files:
                file_node = next(
                    (c for c in parent_for_files.children if c.sigil == SIGIL_FILE and getattr(c.metadata, "fpath", None) == fpath),
                    None,
                )
                if not file_node:
                    continue
            else:
                placed_files.add(fpath)
                entities = file_entities.get(fpath, [])
                exp_list = [e.name for e in entities]
                exp_str = ", ".join(exp_list) if exp_list else ""
                file_lr = _file_line_range(entities)

                file_node = SemanticNode(
                    id=fpath,
                    sigil=SIGIL_FILE,
                    artifact_class="concrete-file",
                    feature=fpath.split("/")[-1].replace(".py", "").replace("_", " "),
                    metadata=NodeMetadata(type="file", fpath=fpath, line_range=file_lr),
                    contract=Contract(exp=exp_str) if exp_str else Contract(),
                    status="resolved",
                    children=[],
                )
                parent_for_files.children.append(file_node)
                file_node.parent = parent_for_files

            entities = file_entities.get(fpath, [])
            for e in entities:
                key = _entity_key(e)
                if key in placed_entities:
                    continue
                placed_entities.add(key)
                primary_feature = _feature_by_entity(semantic_features, e.name)
                sigil = SIGIL_CLASS if e.entity_type == "class" else SIGIL_FUNC
                meta = NodeMetadata(
                    type=e.entity_type,
                    fpath=e.fpath,
                    entity_name=e.name,
                    line_range=e.line_range,
                )
                contract = _contract_from_entity(e)
                node_id = f"{e.fpath}::{e.name}"

                leaf = SemanticNode(
                    id=node_id,
                    sigil=sigil,
                    artifact_class="concrete-leaf",
                    feature=primary_feature,
                    metadata=meta,
                    contract=contract,
                    status="resolved",
                    children=[],
                )
                file_node.children.append(leaf)
                leaf.parent = file_node

    # If no hierarchy mappings, create a flat list under root
    if not hierarchy_mappings:
        for fpath, entities in file_entities.items():
            exp_str = ", ".join(e.name for e in entities)
            file_lr = _file_line_range(entities)
            file_node = SemanticNode(
                id=fpath,
                sigil=SIGIL_FILE,
                artifact_class="concrete-file",
                feature=fpath.split("/")[-1].replace(".py", ""),
                metadata=NodeMetadata(type="file", fpath=fpath, line_range=file_lr),
                contract=Contract(exp=exp_str) if exp_str else Contract(),
                status="resolved",
                children=[],
            )
            root.children.append(file_node)
            file_node.parent = root
            for e in entities:
                primary_feature = _feature_by_entity(semantic_features, e.name)
                sigil = SIGIL_CLASS if e.entity_type == "class" else SIGIL_FUNC
                leaf = SemanticNode(
                    id=f"{e.fpath}::{e.name}",
                    sigil=sigil,
                    artifact_class="concrete-leaf",
                    feature=primary_feature,
                    metadata=NodeMetadata(type=e.entity_type, fpath=e.fpath, entity_name=e.name, line_range=e.line_range),
                    contract=_contract_from_entity(e),
                    status="resolved",
                    children=[],
                )
                file_node.children.append(leaf)
                leaf.parent = file_node

    deps: List[DepEdge] = []
    if include_deps:
        for imp in snapshot.all_imports:
            from_id = imp.source_entity or imp.source_fpath or "unknown"
            to_id = imp.target_entity
            deps.append(
                DepEdge(
                    from_entity=from_id,
                    to=to_id,
                    relation=_entity_to_dep_relation(imp),
                    from_external=imp.is_external,
                    to_external=None,
                )
            )

    # If root has single child, use it as logical root
    if len(root.children) == 1:
        root = root.children[0]
        root.parent = None

    return SemanticTree(root=root, deps=deps)
