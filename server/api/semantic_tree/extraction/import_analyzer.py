"""Import edge extraction from Python AST (Import / ImportFrom)."""

import ast
import logging
from typing import List, Optional

from api.semantic_tree.models import ImportEdge

logger = logging.getLogger(__name__)


def _is_internal_target(module: str, root_package: str) -> bool:
    """Heuristic: same package prefix => internal."""
    if not root_package:
        return False
    return module == root_package or module.startswith(root_package + ".")


def extract_import_edges(
    tree: ast.AST,
    fpath: str,
    source: str,
    root_package: Optional[str] = None,
) -> List[ImportEdge]:
    """
    Extract import edges from a Python AST. relation is always "imports".
    root_package: e.g. "requests" for requests/api.py; used to mark internal vs external.
    """
    if root_package is None:
        # Infer top-level package from fpath (first path component)
        parts = fpath.replace("\\", "/").split("/")
        root_package = parts[0] if parts else ""

    edges: List[ImportEdge] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.asname or alias.name
                is_ext = not _is_internal_target(alias.name, root_package)
                edges.append(
                    ImportEdge(
                        source_entity=None,
                        target_entity=module,
                        relation="imports",
                        is_external=is_ext,
                        source_fpath=fpath,
                        target_fpath=None,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            module = node.module
            is_ext = not _is_internal_target(module, root_package)
            for alias in node.names:
                name = alias.asname or alias.name
                target = f"{module}.{name}" if name != "*" else module
                edges.append(
                    ImportEdge(
                        source_entity=None,
                        target_entity=target,
                        relation="imports",
                        is_external=is_ext,
                        source_fpath=fpath,
                        target_fpath=None,
                    )
                )

    return edges
