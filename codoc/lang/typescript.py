"""TypeScript language adapter using tree-sitter via tree_sitter_languages."""

from __future__ import annotations

import ctypes
import pathlib
import warnings

import tree_sitter as ts

from codoc.lang.base import Chunk, SymbolRef

LANGUAGE_NAME = "typescript"

# ---------------------------------------------------------------------------
# Internal helpers: load the language once at import time.
# Same ctypes shim as python.py — see that module for rationale.
# ---------------------------------------------------------------------------

def _load_language(name: str) -> ts.Language:
    import tree_sitter_languages as _tsl_pkg  # noqa: PLC0415

    langs_so = pathlib.Path(_tsl_pkg.__file__).parent / "languages.so"
    lib = ctypes.cdll.LoadLibrary(str(langs_so))
    fn = getattr(lib, f"tree_sitter_{name}")
    fn.restype = ctypes.c_void_p
    ptr = fn()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return ts.Language(ptr)


_TS_LANG: ts.Language | None = None
_TS_PARSER: ts.Parser | None = None


def _get_lang() -> ts.Language:
    global _TS_LANG
    if _TS_LANG is None:
        _TS_LANG = _load_language("typescript")
    return _TS_LANG


def _get_parser() -> ts.Parser:
    global _TS_PARSER
    if _TS_PARSER is None:
        _TS_PARSER = ts.Parser(_get_lang())
    return _TS_PARSER


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

# Node types that define named entities at any scope.
_DEFINITION_KINDS = frozenset({
    "function_declaration",
    "class_declaration",
    "interface_declaration",
    "type_alias_declaration",
    "method_definition",
    "export_statement",         # may wrap any of the above
    "lexical_declaration",      # const/let arrow functions at module level
    "variable_declaration",     # var arrow functions at module level
})


def _node_identifier(node: ts.Node) -> str | None:
    """Return the primary name identifier of a named declaration node."""
    for child in node.children:
        if child.type in {"identifier", "type_identifier", "property_identifier"}:
            return child.text.decode("utf-8", errors="replace")
    return None


def _is_arrow_function_declarator(decl_node: ts.Node) -> tuple[bool, str | None]:
    """Check if a variable_declarator wraps an arrow_function.
    Returns (is_arrow, name_or_None).
    """
    name: str | None = None
    has_arrow = False
    for child in decl_node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8", errors="replace")
        elif child.type == "arrow_function":
            has_arrow = True
    return has_arrow, name


def _simple_declarator_name(decl_node: ts.Node) -> str | None:
    """Return the name if *decl_node* is a ``const NAME = value`` with a plain
    identifier LHS (no destructuring, not private).  Returns None otherwise.
    """
    for child in decl_node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8", errors="replace")
            if name and not name.startswith("_"):
                return name
        elif child.type in {"object_pattern", "array_pattern"}:
            return None  # destructuring — skip
    return None


def _unwrap_export(node: ts.Node) -> ts.Node:
    """If node is an export_statement, return the declaration it wraps."""
    if node.type == "export_statement":
        for child in node.children:
            if child.type in {
                "function_declaration",
                "class_declaration",
                "interface_declaration",
                "type_alias_declaration",
                "lexical_declaration",
                "variable_declaration",
            }:
                return child
    return node


def _extract_chunks_recursive(
    node: ts.Node,
    source_bytes: bytes,
    file: str,
    prefix: str,
    chunks: list[Chunk],
    is_module_scope: bool = True,
) -> None:
    """Walk children of *node* and emit Chunks."""
    module_start: int | None = None
    module_end: int | None = None

    def flush_module_chunk() -> None:
        nonlocal module_start, module_end
        if module_start is not None and module_end is not None:
            raw = source_bytes[module_start:module_end].decode("utf-8", errors="replace")
            chunks.append(
                Chunk(
                    symbol_path=f"{file}::__module__",
                    file=file,
                    start_byte=module_start,
                    end_byte=module_end,
                    source=raw,
                )
            )
        module_start = None
        module_end = None

    for child in node.children:
        inner = _unwrap_export(child)  # strip export wrapper if present
        inner_type = inner.type

        if inner_type in {
            "function_declaration",
            "class_declaration",
            "interface_declaration",
            "type_alias_declaration",
        }:
            if is_module_scope:
                flush_module_chunk()

            name = _node_identifier(inner)
            if name is None:
                continue
            qualified = f"{prefix}.{name}" if prefix else name
            sym_path = f"{file}::{qualified}"
            raw = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
            chunks.append(
                Chunk(
                    symbol_path=sym_path,
                    file=file,
                    start_byte=child.start_byte,
                    end_byte=child.end_byte,
                    source=raw,
                )
            )

            # Recurse into class body for methods.
            if inner_type == "class_declaration":
                body = next((c for c in inner.children if c.type == "class_body"), None)
                if body is not None:
                    _extract_chunks_recursive(
                        body, source_bytes, file, qualified, chunks, is_module_scope=False
                    )

        elif inner_type in {"lexical_declaration", "variable_declaration"} and is_module_scope:
            # Check each declarator for arrow functions.
            found_arrow = False
            for decl in inner.children:
                if decl.type == "variable_declarator":
                    is_arrow, name = _is_arrow_function_declarator(decl)
                    if is_arrow and name:
                        found_arrow = True
                        flush_module_chunk()
                        qualified = f"{prefix}.{name}" if prefix else name
                        sym_path = f"{file}::{qualified}"
                        raw = source_bytes[child.start_byte:child.end_byte].decode(
                            "utf-8", errors="replace"
                        )
                        chunks.append(
                            Chunk(
                                symbol_path=sym_path,
                                file=file,
                                start_byte=child.start_byte,
                                end_byte=child.end_byte,
                                source=raw,
                            )
                        )
            if not found_arrow:
                # Plain const/let/var — emit as named chunk if a simple identifier
                # LHS is present; otherwise accumulate into module-level code.
                named: str | None = None
                for decl in inner.children:
                    if decl.type == "variable_declarator":
                        named = _simple_declarator_name(decl)
                        if named:
                            break
                if named:
                    flush_module_chunk()
                    qualified = f"{prefix}.{named}" if prefix else named
                    sym_path = f"{file}::{qualified}"
                    raw = source_bytes[child.start_byte:child.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    chunks.append(
                        Chunk(
                            symbol_path=sym_path,
                            file=file,
                            start_byte=child.start_byte,
                            end_byte=child.end_byte,
                            source=raw,
                        )
                    )
                elif child.type not in {"comment", ""}:
                    if module_start is None:
                        module_start = child.start_byte
                    module_end = child.end_byte

        elif inner_type == "method_definition" and not is_module_scope:
            # Inside a class body.
            name = _node_identifier(inner)
            if name is None:
                continue
            qualified = f"{prefix}.{name}" if prefix else name
            sym_path = f"{file}::{qualified}"
            raw = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
            chunks.append(
                Chunk(
                    symbol_path=sym_path,
                    file=file,
                    start_byte=child.start_byte,
                    end_byte=child.end_byte,
                    source=raw,
                )
            )

        else:
            # Non-definition node.
            if is_module_scope and child.type not in {"comment", ""}:
                if module_start is None:
                    module_start = child.start_byte
                module_end = child.end_byte

    if is_module_scope:
        flush_module_chunk()


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class TypeScriptAdapter:
    language = LANGUAGE_NAME

    def extract_chunks(self, file: str, source: str) -> list[Chunk]:
        source_bytes = source.encode("utf-8")
        tree = self.parse(source)
        chunks: list[Chunk] = []
        _extract_chunks_recursive(tree.root_node, source_bytes, file, "", chunks)

        # The module chunk is the glue between the declarations, so it is the
        # CONCATENATION of the top-level runs and not the span from the first to
        # the last. Spanning them put every function and class in the file inside
        # the module chunk as well, so a one-line edit to any of them changed the
        # module's own fingerprint too — one edit reported as two changes, in the
        # feature that had least to do with it.
        module_chunks = sorted(
            (c for c in chunks if c.symbol_path.endswith("::__module__")),
            key=lambda c: c.start_byte,
        )
        other_chunks = [c for c in chunks if not c.symbol_path.endswith("::__module__")]
        if module_chunks:
            other_chunks.append(
                Chunk(
                    symbol_path=f"{file}::__module__",
                    file=file,
                    start_byte=module_chunks[0].start_byte,
                    end_byte=module_chunks[-1].end_byte,
                    source="\n\n".join(c.source for c in module_chunks),
                )
            )
        return other_chunks

    @property
    def comment_node_kinds(self) -> set[str]:  # type: ignore[override]
        return {"comment", "multiline_comment"}

    def resolve_symbol_path(self, source: str, symbol_path: str) -> tuple[int, int] | None:
        if "::" in symbol_path:
            qualified = symbol_path.split("::", 1)[1]
        else:
            qualified = symbol_path

        parts = qualified.split(".")
        tree = self.parse(source)

        def search(node: ts.Node, remaining: list[str]) -> tuple[int, int] | None:
            if not remaining:
                return None
            target = remaining[0]
            rest = remaining[1:]

            for child in node.children:
                inner = _unwrap_export(child)
                inner_type = inner.type

                if inner_type in {
                    "function_declaration",
                    "class_declaration",
                    "interface_declaration",
                    "type_alias_declaration",
                }:
                    name = _node_identifier(inner)
                    if name == target:
                        if not rest:
                            return (child.start_byte, child.end_byte)
                        if inner_type == "class_declaration":
                            body = next(
                                (c for c in inner.children if c.type == "class_body"), None
                            )
                            if body is not None:
                                result = search(body, rest)
                                if result is not None:
                                    return result

                elif inner_type in {"lexical_declaration", "variable_declaration"}:
                    for decl in inner.children:
                        if decl.type == "variable_declarator":
                            is_arrow, name = _is_arrow_function_declarator(decl)
                            if is_arrow and name == target and not rest:
                                return (child.start_byte, child.end_byte)

                elif inner_type == "method_definition":
                    name = _node_identifier(inner)
                    if name == target and not rest:
                        return (child.start_byte, child.end_byte)

            return None

        return search(tree.root_node, parts)

    def run_ts_query(
        self, source: str, query_str: str,
        scope: tuple[int, int] | None = None,
    ) -> list[tuple[int, int]]:
        tree = self.parse(source)
        lang = _get_lang()
        query = ts.Query(lang, query_str)
        cursor = ts.QueryCursor(query)
        if scope is not None:
            cursor.set_byte_range(scope[0], scope[1])
        matches = cursor.matches(tree.root_node)
        results: list[tuple[int, int]] = []
        for _idx, cap_dict in matches:
            for nodes in cap_dict.values():
                if nodes:
                    n = nodes[0]
                    results.append((n.start_byte, n.end_byte))
                break
        return results

    def references_in_chunk(self, chunk_source: str, file: str) -> list[SymbolRef]:
        tree = self.parse(chunk_source)
        refs: list[SymbolRef] = []

        def walk(node: ts.Node) -> None:
            if node.type == "import_statement":
                # import { A, B } from "./module"  or  import Def from "..."
                clause = next(
                    (c for c in node.children if c.type == "import_clause"), None
                )
                if clause is not None:
                    named = next(
                        (c for c in clause.children if c.type == "named_imports"), None
                    )
                    if named is not None:
                        for spec in named.children:
                            if spec.type == "import_specifier":
                                id_node = next(
                                    (c for c in spec.children if c.type == "identifier"), None
                                )
                                if id_node:
                                    refs.append(SymbolRef(
                                        qualified_name=id_node.text.decode("utf-8", errors="replace"),
                                        ref_kind="import",
                                        start_byte=id_node.start_byte,
                                        end_byte=id_node.end_byte,
                                    ))
                    # Default import: import DefaultExport from "..."
                    default_id = next(
                        (c for c in clause.children if c.type == "identifier"), None
                    )
                    if default_id:
                        refs.append(SymbolRef(
                            qualified_name=default_id.text.decode("utf-8", errors="replace"),
                            ref_kind="import",
                            start_byte=default_id.start_byte,
                            end_byte=default_id.end_byte,
                        ))
                    # Namespace import: import * as Ns from "..."
                    ns_import = next(
                        (c for c in clause.children if c.type == "namespace_import"), None
                    )
                    if ns_import:
                        id_node = next(
                            (c for c in ns_import.children if c.type == "identifier"), None
                        )
                        if id_node:
                            refs.append(SymbolRef(
                                qualified_name=id_node.text.decode("utf-8", errors="replace"),
                                ref_kind="import",
                                start_byte=id_node.start_byte,
                                end_byte=id_node.end_byte,
                            ))
                return  # don't recurse into import nodes

            elif node.type == "call_expression":
                callee = node.children[0] if node.children else None
                if callee is not None:
                    name = callee.text.decode("utf-8", errors="replace")
                    refs.append(SymbolRef(
                        qualified_name=name,
                        ref_kind="call",
                        start_byte=callee.start_byte,
                        end_byte=callee.end_byte,
                    ))
                # Recurse into arguments.
                for child in node.children[1:]:
                    walk(child)
                return

            elif node.type == "new_expression":
                # new ClassName(...)
                callee = next(
                    (c for c in node.children if c.type in {"identifier", "member_expression"}),
                    None,
                )
                if callee is not None:
                    name = callee.text.decode("utf-8", errors="replace")
                    refs.append(SymbolRef(
                        qualified_name=name,
                        ref_kind="call",
                        start_byte=callee.start_byte,
                        end_byte=callee.end_byte,
                    ))
                for child in node.children:
                    walk(child)
                return

            elif node.type == "class_declaration":
                # extends and implements
                heritage = next(
                    (c for c in node.children if c.type == "class_heritage"), None
                )
                if heritage is not None:
                    extends = next(
                        (c for c in heritage.children if c.type == "extends_clause"), None
                    )
                    if extends is not None:
                        for child in extends.children:
                            if child.type in {"identifier", "member_expression"}:
                                refs.append(SymbolRef(
                                    qualified_name=child.text.decode("utf-8", errors="replace"),
                                    ref_kind="inherit",
                                    start_byte=child.start_byte,
                                    end_byte=child.end_byte,
                                ))
                    implements = next(
                        (c for c in heritage.children if c.type == "implements_clause"), None
                    )
                    if implements is not None:
                        for child in implements.children:
                            if child.type == "type_identifier":
                                refs.append(SymbolRef(
                                    qualified_name=child.text.decode("utf-8", errors="replace"),
                                    ref_kind="inherit",
                                    start_byte=child.start_byte,
                                    end_byte=child.end_byte,
                                ))

            elif node.type == "type_annotation":
                # Walk type annotations for type references.
                for child in node.children:
                    if child.type == "type_identifier":
                        refs.append(SymbolRef(
                            qualified_name=child.text.decode("utf-8", errors="replace"),
                            ref_kind="type_ref",
                            start_byte=child.start_byte,
                            end_byte=child.end_byte,
                        ))

            elif node.type == "type_identifier" and node.parent and node.parent.type not in {
                "type_annotation", "class_declaration", "interface_declaration",
                "type_alias_declaration", "extends_clause", "implements_clause",
            }:
                # Bare type references in type positions not already covered.
                refs.append(SymbolRef(
                    qualified_name=node.text.decode("utf-8", errors="replace"),
                    ref_kind="type_ref",
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                ))

            for child in node.children:
                walk(child)

        walk(tree.root_node)
        return refs

    def parse(self, source: str):
        return _get_parser().parse(source.encode("utf-8"))

    def token_stream(self, source: str, exclude_comment_nodes: bool = True) -> list[str]:
        source_bytes = source.encode("utf-8")
        tree = self.parse(source)
        comment_kinds = self.comment_node_kinds if exclude_comment_nodes else set()
        tokens: list[str] = []

        def walk(node: ts.Node) -> None:
            if node.type in comment_kinds:
                return
            if node.child_count == 0:
                text = source_bytes[node.start_byte:node.end_byte].decode(
                    "utf-8", errors="replace"
                ).strip()
                if text:
                    tokens.append(text)
                return
            for child in node.children:
                walk(child)

        walk(tree.root_node)
        return tokens
