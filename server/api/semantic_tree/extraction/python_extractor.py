"""Python AST-based entity extraction (functions, classes, methods, signatures, line ranges)."""

import ast
import logging
from typing import List, Optional

from api.semantic_tree.models import CodeEntity, FileInfo, ImportEdge
from api.semantic_tree.extraction.import_analyzer import extract_import_edges
from api.semantic_tree.extraction.call_analyzer import extract_invoke_edges

logger = logging.getLogger(__name__)


def _get_docstring(node: ast.AST) -> Optional[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        doc = ast.get_docstring(node)
        if doc:
            return doc
    return None


def _signature_from_ast(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Build a minimal signature string (args) -> return."""
    args = node.args
    parts: List[str] = []
    for a in args.posonlyargs:
        parts.append(a.arg)
    if args.posonlyargs:
        parts.append("/")
    for a in args.args:
        if a.arg in ("self", "cls"):
            continue
        # Check if has default
        num_defaults = len(args.defaults)
        num_args = len(args.args)
        idx = num_args - num_defaults
        default_idx = next((i for i, arg in enumerate(args.args) if arg == a), 0)
        if default_idx >= idx:
            parts.append(f"{a.arg}=...")
        else:
            parts.append(a.arg)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    for a in args.kwonlyargs:
        parts.append(a.arg)
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    sig = "(" + ", ".join(parts) + ")"
    if node.returns:
        try:
            sig += " -> " + ast.unparse(node.returns)
        except Exception:
            sig += " -> ..."
    return sig


def _class_method_names(class_node: ast.ClassDef) -> List[str]:
    names: List[str] = []
    for stmt in class_node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(stmt.name)
    return names


def extract_python_file(
    fpath: str,
    source: str,
    root_dir: str,
    include_imports: bool = True,
) -> FileInfo:
    """
    Parse Python source and return FileInfo with entities and optional import edges.
    fpath: relative path (e.g. requests/api.py). root_dir: unused, kept for API compatibility.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        logger.warning("Python parse error in %s: %s", fpath, e)
        return FileInfo(fpath=fpath, language="python", entities=[], imports=[])

    entities, imports = _collect_entities_and_imports(tree, source, fpath)
    if not include_imports:
        imports = []
    invokes = extract_invoke_edges(tree, fpath, entities)

    return FileInfo(
        fpath=fpath,
        language="python",
        entities=entities,
        imports=imports,
        invokes=invokes,
    )


def _collect_entities_and_imports(
    tree: ast.AST,
    source: str,
    fpath: str,
) -> tuple[List[CodeEntity], List[ImportEdge]]:
    entities: List[CodeEntity] = []
    imports = extract_import_edges(tree, fpath, source)

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._in_class = False

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            sig = _signature_from_ast(node)
            doc = _get_docstring(node)
            start, end = node.lineno, node.end_lineno or node.lineno
            body = ast.get_source_segment(source, node) or ""
            entity_type = "method" if self._in_class else "function"
            entities.append(
                CodeEntity(
                    name=node.name,
                    entity_type=entity_type,
                    signature=sig,
                    fpath=fpath,
                    line_range=(start, end),
                    body_text=body,
                    docstring=doc,
                )
            )
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            sig = _signature_from_ast(node)
            doc = _get_docstring(node)
            start, end = node.lineno, node.end_lineno or node.lineno
            body = ast.get_source_segment(source, node) or ""
            entity_type = "method" if self._in_class else "function"
            entities.append(
                CodeEntity(
                    name=node.name,
                    entity_type=entity_type,
                    signature=sig,
                    fpath=fpath,
                    line_range=(start, end),
                    body_text=body,
                    docstring=doc,
                )
            )
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            methods = _class_method_names(node)
            cls_sig = f"methods=[{', '.join(methods)}]"
            doc = _get_docstring(node)
            start, end = node.lineno, node.end_lineno or node.lineno
            body = ast.get_source_segment(source, node) or ""
            entities.append(
                CodeEntity(
                    name=node.name,
                    entity_type="class",
                    signature=cls_sig,
                    fpath=fpath,
                    line_range=(start, end),
                    body_text=body,
                    docstring=doc,
                )
            )
            self._in_class = True
            self.generic_visit(node)
            self._in_class = False

    Visitor().visit(tree)
    return entities, imports
