"""Serialize SemanticTree to markdown (parseTreeBlock-compatible) and JSON."""

from typing import List

from api.semantic_tree.models import SemanticTree, SemanticNode, DepEdge, Contract

CONTRACT_KEYS = ("sig", "inv", "cls", "exp")


def _dep_from_id(d: DepEdge) -> str:
    return getattr(d, "from_entity", None) or getattr(d, "from", None) or "?"


def _contract_str(c: Contract) -> str:
    parts = []
    for k in CONTRACT_KEYS:
        val = getattr(c, k, None)
        if val is not None and str(val).strip():
            parts.append(f"{{{k}: {val}}}")
    return " ".join(parts)


def _line(node: SemanticNode) -> str:
    parts = [str(node.sigil), " ", (node.feature or "").strip() or " "]
    if node.metadata and node.metadata.fpath:
        parts.append(f" [{node.metadata.fpath}]")
    if node.metadata and node.metadata.entity_name:
        parts.append(f" ({node.metadata.entity_name})")
    if node.contract:
        cs = _contract_str(node.contract)
        if cs:
            parts.append(" " + cs)
    parts.append(f" #{node.status}")
    return "".join(parts)


def _dump_node(node: SemanticNode, depth: int, lines: List[str]) -> None:
    lines.append("  " * depth + "- " + _line(node))
    for child in node.children:
        _dump_node(child, depth + 1, lines)


def tree_to_markdown(tree: SemanticTree) -> str:
    lines: List[str] = []
    _dump_node(tree.root, 0, lines)
    if tree.deps:
        lines.append("")
        lines.append("deps:")
        for d in tree.deps:
            lines.append(f"  ({_dep_from_id(d)}) --{d.relation}--> ({d.to})")
    return "\n".join(lines)


def tree_to_json(tree: SemanticTree) -> dict:
    def node_to_dict(n: SemanticNode) -> dict:
        return {
            "id": n.id,
            "sigil": n.sigil,
            "artifactClass": n.artifact_class,
            "feature": n.feature,
            "metadata": n.metadata.model_dump() if n.metadata else {},
            "contract": n.contract.model_dump() if n.contract else {},
            "status": n.status,
            "children": [node_to_dict(c) for c in n.children],
        }

    deps_list = [
        {
            "from": _dep_from_id(d),
            "to": d.to,
            "relation": d.relation,
            "fromExternal": getattr(d, "from_external", None),
            "toExternal": getattr(d, "to_external", None),
        }
        for d in tree.deps
    ]
    return {"root": node_to_dict(tree.root), "deps": deps_list}
