"""Step 6: Tree assembly — merge hierarchy, grounding metadata, and import edges into SemanticTree."""

import logging
from typing import List, Dict, Optional, Tuple

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

    for path, file_paths in path_to_files.items():
        parts = [p.strip() for p in path.split("/") if p.strip()]
        if len(parts) < 3:
            parts.extend(["other", "general"] * (3 - len(parts)))
        area, category, subcategory = parts[0], parts[1], parts[2]

        # Abstract chain: area -> category -> subcategory
        area_feature = area.replace("-", " ").replace("_", " ")
        category_feature = category.replace("-", " ").replace("_", " ")
        sub_feature = subcategory.replace("-", " ").replace("_", " ")

        area_node = SemanticNode(
            id=path,
            sigil=SIGIL_ABSTRACT,
            artifact_class="abstract",
            feature=area_feature,
            metadata=NodeMetadata(),
            contract=Contract(),
            status="resolved",
            children=[],
        )
        category_node = SemanticNode(
            id=f"{path}/_cat",
            sigil=SIGIL_ABSTRACT,
            artifact_class="abstract",
            feature=category_feature,
            metadata=NodeMetadata(),
            contract=Contract(),
            status="resolved",
            children=[],
        )
        sub_node = SemanticNode(
            id=f"{path}/_sub",
            sigil=SIGIL_ABSTRACT,
            artifact_class="abstract",
            feature=sub_feature,
            metadata=NodeMetadata(),
            contract=Contract(),
            status="resolved",
            children=[],
        )

        # Attach area under root if not already present
        existing = next((c for c in root.children if c.feature == area_feature), None)
        if existing is None:
            root.children.append(area_node)
            area_node.parent = root
            parent_for_cat = area_node
        else:
            parent_for_cat = existing

        existing_cat = next((c for c in parent_for_cat.children if c.feature == category_feature), None)
        if existing_cat is None:
            parent_for_cat.children.append(category_node)
            category_node.parent = parent_for_cat
            parent_for_sub = category_node
        else:
            parent_for_sub = existing_cat

        existing_sub = next((c for c in parent_for_sub.children if c.feature == sub_feature), None)
        if existing_sub is None:
            parent_for_sub.children.append(sub_node)
            sub_node.parent = parent_for_sub
            parent_for_files = sub_node
        else:
            parent_for_files = existing_sub

        for fpath in file_paths:
            entities = file_entities.get(fpath, [])
            exp_list = [e.name for e in entities]
            exp_str = ", ".join(exp_list) if exp_list else ""

            file_node = SemanticNode(
                id=fpath,
                sigil=SIGIL_FILE,
                artifact_class="concrete-file",
                feature=fpath.split("/")[-1].replace(".py", "").replace("_", " "),
                metadata=NodeMetadata(type="file", fpath=fpath),
                contract=Contract(exp=exp_str) if exp_str else Contract(),
                status="resolved",
                children=[],
            )
            parent_for_files.children.append(file_node)
            file_node.parent = parent_for_files

            for e in entities:
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
            file_node = SemanticNode(
                id=fpath,
                sigil=SIGIL_FILE,
                artifact_class="concrete-file",
                feature=fpath.split("/")[-1].replace(".py", ""),
                metadata=NodeMetadata(type="file", fpath=fpath),
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
