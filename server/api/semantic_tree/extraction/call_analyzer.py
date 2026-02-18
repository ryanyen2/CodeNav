"""Call-graph extraction from Python AST: which function invokes which (same-file)."""

import ast
import logging
from typing import List, Set

from api.semantic_tree.models import CodeEntity, ImportEdge

logger = logging.getLogger(__name__)


def _entity_names(entities: List[CodeEntity]) -> Set[str]:
    """Set of callable names in this file (functions, classes)."""
    return {e.name for e in entities}


def extract_invoke_edges(
    tree: ast.AST,
    fpath: str,
    entities: List[CodeEntity],
) -> List[ImportEdge]:
    """
    Extract invoke edges from a Python AST: caller -> callee within the same file.
    Only resolves simple calls (Name(id)) to known entities in this file.
    """
    if not entities:
        return []
    callable_names = _entity_names(entities)

    edges: List[ImportEdge] = []
    current_caller: str | None = None

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nonlocal current_caller
            prev = current_caller
            current_caller = node.name
            self.generic_visit(node)
            current_caller = prev

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            nonlocal current_caller
            prev = current_caller
            current_caller = node.name
            self.generic_visit(node)
            current_caller = prev

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            nonlocal current_caller
            prev = current_caller
            current_caller = node.name
            self.generic_visit(node)
            current_caller = prev

        def visit_Call(self, node: ast.Call) -> None:
            if current_caller is None:
                self.generic_visit(node)
                return
            callee_name: str | None = None
            if isinstance(node.func, ast.Name):
                callee_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                # self.method() or obj.method() - use attr as callee for same-file methods
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                    callee_name = node.func.attr
                else:
                    self.generic_visit(node)
                    return
            else:
                self.generic_visit(node)
                return
            if callee_name and callee_name in callable_names and callee_name != current_caller:
                target_id = f"{fpath}::{callee_name}"
                edges.append(
                    ImportEdge(
                        source_entity=f"{fpath}::{current_caller}",
                        target_entity=target_id,
                        relation="invokes",
                        is_external=False,
                        source_fpath=fpath,
                        target_fpath=fpath,
                        call_site_line=node.lineno,
                    )
                )
            self.generic_visit(node)

    try:
        Visitor().visit(tree)
    except Exception as e:
        logger.warning("Call analysis failed for %s: %s", fpath, e)
    return edges
