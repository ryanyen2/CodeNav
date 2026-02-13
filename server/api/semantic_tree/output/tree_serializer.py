"""Serialize SemanticTree to markdown notation parseable by src/parser/tree-parser.ts parseTreeBlock()."""

from typing import List

from api.semantic_tree.models import SemanticTree, SemanticNode, DepEdge, Contract

# Contract keys in order (TS CONTRACT_KEYS)
CONTRACT_KEYS = ("sig", "inv", "cls", "exp")


def _contract_str(c: Contract) -> str:
    parts = []
    for key in CONTRACT_KEYS:
        val = getattr(c, key, None)
        if val is not None and str(val).strip():
            parts.append(f"{{{key}: {val}}}")
    return " ".join(parts)


def _line(node: SemanticNode) -> str:
    """One tree line: '- <sigil> feature [path] (entity) {sig: ...} #status' (parseTreeLine grammar)."""
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
    indent = "  " * depth
    lines.append(indent + "- " + _line(node))
    for child in node.children:
        _dump_node(child, depth + 1, lines)


def tree_to_markdown(tree: SemanticTree) -> str:
    """
    Produce markdown block: tree lines then 'deps:' and dependency lines.
    Matches grammar in src/parser/tree-parser.ts (parseTreeBlock).
    """
    lines: List[str] = []
    _dump_node(tree.root, 0, lines)
    if tree.deps:
        lines.append("")
        lines.append("deps:")
        for d in tree.deps:
            # DepEdge has from_entity (alias "from") and to
            from_id = getattr(d, "from_entity", None) or getattr(d, "from", None) or "?"
            rel = d.relation
            lines.append(f"  ({from_id}) --{rel}--> ({d.to})")
    return "\n".join(lines)


def tree_to_json(tree: SemanticTree) -> dict:
    """Export tree to JSON shape compatible with TS SemanticTree (for API responses)."""
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

    deps_list = []
    for d in tree.deps:
        from_val = getattr(d, "from_entity", None) or getattr(d, "from", None)
        deps_list.append({
            "from": from_val,
            "to": d.to,
            "relation": d.relation,
            "fromExternal": getattr(d, "from_external", None),
            "toExternal": getattr(d, "to_external", None),
        })

    return {
        "root": node_to_dict(tree.root),
        "deps": deps_list,
    }
