"""Parse markdown tree notation back to SemanticTree (inverse of tree_serializer). Format matches TS parseTreeBlock and tree_serializer output."""

import re
from typing import List, Dict, Optional, Tuple, Any

from api.semantic_tree.models import (
    SemanticTree,
    SemanticNode,
    DepEdge,
    Contract,
    NodeMetadata,
)

SIGILS = ("/", "%", "$", "^", "~")
ARTIFACT_CLASS: Dict[str, str] = {
    "/": "concrete-dir",
    "%": "concrete-file",
    "$": "concrete-leaf",
    "^": "concrete-leaf",
    "~": "abstract",
}
CONTRACT_KEYS = ("sig", "inv", "cls", "exp")
STATUSES = ("resolved", "draft", "unresolved", "planned", "surfaced")
DEP_RELATIONS = ("imports", "invokes", "inherits", "type-refs")


def _parse_status(s: str) -> str:
    t = s.replace("#", "").strip().lower()
    return t if t in STATUSES else "resolved"


def _parse_tree_line(line: str) -> Optional[Tuple[int, str, str, NodeMetadata, Contract, str]]:
    """Parse one tree line. Returns (depth, sigil, feature, metadata, contract, status) or None."""
    stripped = line.lstrip()
    if len(stripped) < 2 or not stripped.startswith("- "):
        return None
    indent = len(line) - len(stripped)
    if indent % 2 != 0:
        return None
    depth = indent // 2
    rest = stripped[2:].strip()  # after "- "
    if len(rest) < 2:
        return None
    sigil = rest[0]
    if sigil not in SIGILS:
        return None
    tail = rest[1:].strip()
    if not tail:
        return None

    feature = ""
    i = 0
    while i < len(tail):
        if i + 2 <= len(tail) and tail[i : i + 2] in (" [", " {", " #"):
            break
        if tail[i] in "[{#":
            break
        feature += tail[i]
        i += 1
    feature = feature.strip()
    tail = tail[i:].strip()

    meta = NodeMetadata()
    # [fpath]
    path_m = re.match(r"^\[([^\]]+)\]\s*", tail)
    if path_m:
        meta.fpath = path_m.group(1).strip()
        if meta.fpath and meta.fpath.endswith("/"):
            meta.type = "directory"
        elif meta.fpath:
            meta.type = "file"
        tail = tail[path_m.end() :].strip()

    # (entity_name)
    entity_m = re.match(r"^\(([^)]+)\)\s*", tail)
    if entity_m:
        meta.entity_name = entity_m.group(1).strip()
        if meta.type != "directory":
            meta.type = "function"
        tail = tail[entity_m.end() :].strip()

    contract = Contract()
    # {key: value} ...
    brace = re.match(r"^\{\s*(\w+)\s*:\s*([^}]*)\}\s*", tail)
    while brace:
        key, value = brace.group(1).strip(), brace.group(2).strip()
        if key in CONTRACT_KEYS:
            setattr(contract, key, value)
        tail = tail[brace.end() :].strip()
        brace = re.match(r"^\{\s*(\w+)\s*:\s*([^}]*)\}\s*", tail)

    status = "resolved"
    hash_m = re.match(r"^#(\S+)", tail)
    if hash_m:
        status = _parse_status(hash_m.group(1))
        tail = tail[hash_m.end() :].strip()

    return (depth, sigil, feature, meta, contract, status)


def _parse_dep_line(line: str) -> Optional[DepEdge]:
    """Parse '(from) --relation--> (to)' -> DepEdge."""
    trimmed = re.sub(r"#.*$", "", line).strip()
    rel_m = re.search(r"--(imports|invokes|inherits|type-refs)-->\s*", trimmed)
    if not rel_m:
        return None
    relation = rel_m.group(1)
    if relation not in DEP_RELATIONS:
        return None
    parts = trimmed.split(rel_m.group(0), 1)
    if len(parts) != 2:
        return None
    from_m = re.search(r"\(([^)]+)\)\s*$", parts[0].strip())
    to_m = re.match(r"^\s*\(([^)]+)\)", parts[1].strip())
    if not from_m or not to_m:
        return None
    from_entity = from_m.group(1).strip()
    to_entity = to_m.group(1).strip()
    return DepEdge(
        from_entity=from_entity,
        to=to_entity,
        relation=relation,
        from_external=from_entity.startswith("ext:"),
        to_external=to_entity.startswith("ext:"),
    )


def _node_id(meta: NodeMetadata, path_from_root: str, sigil: str, feature: str) -> str:
    """Stable id: grounded leaves fpath::entity_name, file fpath, else path_from_root or feature."""
    if meta.fpath and meta.entity_name:
        return f"{meta.fpath}::{meta.entity_name}"
    if meta.fpath:
        return meta.fpath
    return path_from_root or feature or ""


def parse_tree_markdown(md: str) -> SemanticTree:
    """
    Parse markdown tree notation (same format as tree_serializer and TS parseTreeBlock) into SemanticTree.
    Tree lines match ^\\s*-\\s+[/%$^~]. After 'deps:' lines are dependency edges.
    """
    lines = md.splitlines()
    tree_lines: List[str] = []
    deps_start = -1
    for i, line in enumerate(lines):
        if line.strip() == "deps:":
            deps_start = i
            break
        if re.match(r"^\s*-\s+[/%$\^~]", line):
            tree_lines.append(line)

    virtual_root = SemanticNode(
        id="__virtual",
        sigil="~",
        artifact_class="abstract",
        feature="",
        metadata=NodeMetadata(),
        contract=Contract(),
        status="resolved",
        children=[],
    )
    stack: List[Tuple[SemanticNode, int, str]] = [(virtual_root, -1, "")]

    for line in tree_lines:
        parsed = _parse_tree_line(line)
        if not parsed:
            continue
        depth, sigil, feature, meta, contract, status = parsed
        artifact_class = ARTIFACT_CLASS.get(sigil, "abstract")

        while len(stack) > 0 and stack[-1][1] >= depth:
            stack.pop()
        path_from_root = (
            f"{stack[-1][2]}/{feature}".lstrip("/") if stack and stack[-1][2] else feature
        )
        node_id = _node_id(meta, path_from_root, sigil, feature)

        node = SemanticNode(
            id=node_id,
            sigil=sigil,
            artifact_class=artifact_class,
            feature=feature,
            metadata=meta,
            contract=contract,
            status=status,
            children=[],
        )
        parent = stack[-1][0]
        parent.children.append(node)
        node.parent = parent
        stack.append((node, depth, path_from_root))

    root = virtual_root.children[0] if len(virtual_root.children) == 1 else virtual_root
    if root.id == "__virtual":
        root.id = "__empty"

    def apply_path_inheritance(node: SemanticNode, ancestor_fpath: Optional[str]) -> None:
        if node.sigil == "%" and not (node.metadata and node.metadata.fpath):
            if node.metadata:
                node.metadata.fpath = (node.feature or "").strip() or ancestor_fpath
        fpath = (node.metadata.fpath if node.metadata else None) or ancestor_fpath
        if node.sigil in ("$", "^") and node.metadata and not node.metadata.fpath and ancestor_fpath:
            node.metadata.fpath = ancestor_fpath
        if node.metadata and node.metadata.fpath and "::" not in node.id:
            node.id = (
                f"{node.metadata.fpath}::{node.metadata.entity_name}"
                if node.metadata.entity_name
                else node.metadata.fpath
            )
        for child in node.children:
            apply_path_inheritance(child, node.metadata.fpath if node.metadata else fpath)

    apply_path_inheritance(root, None)

    deps: List[DepEdge] = []
    if deps_start >= 0:
        for i in range(deps_start + 1, len(lines)):
            line = lines[i]
            if not line.strip():
                continue
            if line.strip().startswith("---") or line.strip().startswith("==="):
                break
            edge = _parse_dep_line(line)
            if edge:
                deps.append(edge)

    return SemanticTree(root=root, deps=deps)
