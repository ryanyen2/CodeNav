"""Python language adapter using tree-sitter via tree_sitter_languages."""

from __future__ import annotations

import ctypes
import pathlib
import warnings

import tree_sitter as ts

from codoc.lang.base import Chunk, SymbolRef

LANGUAGE_NAME = "python"

# ---------------------------------------------------------------------------
# Internal helpers: load the language once at import time.
# tree_sitter_languages 1.10.2 ships precompiled grammars in languages.so but
# was built against the old tree-sitter 0.20 API.  We load the shared library
# directly with ctypes to get the raw TSLanguage pointer, then hand it to the
# tree-sitter 0.25 Python binding, which accepts an integer pointer.
# ---------------------------------------------------------------------------

def _load_language(name: str) -> ts.Language:
    langs_so = (
        pathlib.Path(__file__).parent.parent.parent
        / ".venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "tree_sitter_languages"
        / "languages.so"
    )
    if not langs_so.exists():
        # Fallback: locate it relative to tree_sitter_languages package.
        import tree_sitter_languages as _tsl_pkg  # noqa: PLC0415
        langs_so = pathlib.Path(_tsl_pkg.__file__).parent / "languages.so"

    lib = ctypes.cdll.LoadLibrary(str(langs_so))
    fn = getattr(lib, f"tree_sitter_{name}")
    fn.restype = ctypes.c_void_p
    ptr = fn()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return ts.Language(ptr)


_PY_LANG: ts.Language | None = None
_PY_PARSER: ts.Parser | None = None


def _get_lang() -> ts.Language:
    global _PY_LANG
    if _PY_LANG is None:
        _PY_LANG = _load_language("python")
    return _PY_LANG


def _get_parser() -> ts.Parser:
    global _PY_PARSER
    if _PY_PARSER is None:
        _PY_PARSER = ts.Parser(_get_lang())
    return _PY_PARSER


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _node_name(node: ts.Node) -> str | None:
    """Return the identifier text of a function_definition or class_definition node."""
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8", errors="replace")
    return None


def _extract_chunks_recursive(
    node: ts.Node,
    source_bytes: bytes,
    file: str,
    prefix: str,
    chunks: list[Chunk],
) -> None:
    """Walk *node*'s direct children and emit Chunks for definitions.

    prefix  – dot-separated qualified name of the enclosing scope, e.g. "MyClass"
               or "" for module scope.
    Consecutive non-definition sibling nodes at module scope are collected into a
    single __module__ chunk.  Inside a class body they are skipped (we only care
    about methods / nested classes).
    """
    definition_kinds = {"function_definition", "class_definition", "decorated_definition"}
    is_module_scope = prefix == ""

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

    # Choose the right child list: blocks wrap their children in one level.
    children = node.children

    for child in children:
        effective = child
        # decorated_definition wraps the real function/class — peel it.
        is_decorated = child.type == "decorated_definition"
        if is_decorated:
            effective_type = "decorated_definition"
        else:
            effective_type = child.type

        if effective_type in definition_kinds:
            if is_module_scope:
                flush_module_chunk()

            # Resolve the inner function_definition / class_definition node.
            if is_decorated:
                inner = next(
                    (c for c in child.children if c.type in {"function_definition", "class_definition"}),
                    None,
                )
                if inner is None:
                    continue
                def_node = inner
            else:
                def_node = child

            entity_name = _node_name(def_node)
            if entity_name is None:
                continue

            qualified = f"{prefix}.{entity_name}" if prefix else entity_name
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

            # Recurse into the body of the class or function.
            if def_node.type == "class_definition":
                body = next((c for c in def_node.children if c.type == "block"), None)
                if body is not None:
                    _extract_chunks_recursive(body, source_bytes, file, qualified, chunks)

        else:
            # Non-definition node at module scope → accumulate into __module__.
            if is_module_scope and child.type not in {"comment", "newline", ""}:
                if module_start is None:
                    module_start = child.start_byte
                module_end = child.end_byte

    if is_module_scope:
        flush_module_chunk()


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class PythonAdapter:
    language = LANGUAGE_NAME

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def extract_chunks(self, file: str, source: str) -> list[Chunk]:
        source_bytes = source.encode("utf-8")
        tree = self.parse(source)
        chunks: list[Chunk] = []
        _extract_chunks_recursive(tree.root_node, source_bytes, file, "", chunks)
        # De-duplicate __module__ chunks (merge all into one).
        module_chunks = [c for c in chunks if c.symbol_path.endswith("::__module__")]
        other_chunks = [c for c in chunks if not c.symbol_path.endswith("::__module__")]
        if module_chunks:
            merged_start = min(c.start_byte for c in module_chunks)
            merged_end = max(c.end_byte for c in module_chunks)
            merged_source = source_bytes[merged_start:merged_end].decode("utf-8", errors="replace")
            other_chunks.append(
                Chunk(
                    symbol_path=f"{file}::__module__",
                    file=file,
                    start_byte=merged_start,
                    end_byte=merged_end,
                    source=merged_source,
                )
            )
        return other_chunks

    @property
    def comment_node_kinds(self) -> set[str]:  # type: ignore[override]
        return {"comment"}

    def resolve_symbol_path(self, source: str, symbol_path: str) -> tuple[int, int] | None:
        """Resolve "file.py::ClassName.method" → (start_byte, end_byte)."""
        if "::" in symbol_path:
            qualified = symbol_path.split("::", 1)[1]
        else:
            qualified = symbol_path

        parts = qualified.split(".")
        source_bytes = source.encode("utf-8")
        tree = self.parse(source)

        def search(node: ts.Node, remaining: list[str]) -> tuple[int, int] | None:
            if not remaining:
                return None
            target = remaining[0]
            rest = remaining[1:]

            for child in node.children:
                # decorated_definition wraps function/class
                effective = child
                if child.type == "decorated_definition":
                    inner = next(
                        (c for c in child.children if c.type in {"function_definition", "class_definition"}),
                        None,
                    )
                    if inner is not None:
                        effective = inner
                    else:
                        continue

                if effective.type in {"function_definition", "class_definition"}:
                    name_node = next((c for c in effective.children if c.type == "identifier"), None)
                    if name_node and name_node.text.decode("utf-8", errors="replace") == target:
                        if not rest:
                            return (child.start_byte, child.end_byte)
                        # Dig into the block
                        body = next((c for c in effective.children if c.type == "block"), None)
                        if body is not None:
                            result = search(body, rest)
                            if result is not None:
                                return result

            return None

        return search(tree.root_node, parts)

    def run_ts_query(
        self, source: str, query_str: str,
        scope: tuple[int, int] | None = None,
    ) -> list[tuple[int, int]]:
        source_bytes = source.encode("utf-8")
        tree = self.parse(source)
        lang = _get_lang()
        query = ts.Query(lang, query_str)
        cursor = ts.QueryCursor(query)
        if scope is not None:
            cursor.set_byte_range(scope[0], scope[1])
        matches = cursor.matches(tree.root_node)
        results: list[tuple[int, int]] = []
        for _idx, cap_dict in matches:
            # Use the first capture group's first node.
            for nodes in cap_dict.values():
                if nodes:
                    n = nodes[0]
                    results.append((n.start_byte, n.end_byte))
                break
        return results

    def references_in_chunk(self, chunk_source: str, file: str) -> list[SymbolRef]:
        source_bytes = chunk_source.encode("utf-8")
        tree = self.parse(chunk_source)
        refs: list[SymbolRef] = []

        def walk(node: ts.Node) -> None:
            if node.type == "import_statement":
                # import a, import a.b.c
                for child in node.children:
                    if child.type == "dotted_name":
                        name = child.text.decode("utf-8", errors="replace")
                        refs.append(SymbolRef(
                            qualified_name=name,
                            ref_kind="import",
                            start_byte=child.start_byte,
                            end_byte=child.end_byte,
                        ))
                    elif child.type == "aliased_import":
                        # import x as y  — record the original name
                        orig = next(
                            (c for c in child.children if c.type == "dotted_name"), None
                        )
                        if orig:
                            name = orig.text.decode("utf-8", errors="replace")
                            refs.append(SymbolRef(
                                qualified_name=name,
                                ref_kind="import",
                                start_byte=orig.start_byte,
                                end_byte=orig.end_byte,
                            ))
                return  # don't recurse further into import_statement

            elif node.type == "import_from_statement":
                # from x import a, b
                module_node = next(
                    (c for c in node.children if c.type in {"dotted_name", "relative_import"}),
                    None,
                )
                module_name = ""
                if module_node is not None:
                    module_name = module_node.text.decode("utf-8", errors="replace")

                for child in node.children:
                    if child.type == "dotted_name" and child is not module_node:
                        sym = child.text.decode("utf-8", errors="replace")
                        full_name = f"{module_name}.{sym}" if module_name.strip(".") else sym
                        refs.append(SymbolRef(
                            qualified_name=full_name,
                            ref_kind="import",
                            start_byte=child.start_byte,
                            end_byte=child.end_byte,
                        ))
                    elif child.type == "aliased_import":
                        orig = next(
                            (c for c in child.children if c.type == "dotted_name"), None
                        )
                        if orig:
                            sym = orig.text.decode("utf-8", errors="replace")
                            full_name = f"{module_name}.{sym}" if module_name.strip(".") else sym
                            refs.append(SymbolRef(
                                qualified_name=full_name,
                                ref_kind="import",
                                start_byte=orig.start_byte,
                                end_byte=orig.end_byte,
                            ))
                    elif child.type == "wildcard_import":
                        refs.append(SymbolRef(
                            qualified_name=f"{module_name}.*",
                            ref_kind="import",
                            start_byte=child.start_byte,
                            end_byte=child.end_byte,
                        ))
                return

            elif node.type == "call":
                # function call or method call
                callee = node.children[0] if node.children else None
                if callee is not None:
                    name = callee.text.decode("utf-8", errors="replace")
                    refs.append(SymbolRef(
                        qualified_name=name,
                        ref_kind="call",
                        start_byte=callee.start_byte,
                        end_byte=callee.end_byte,
                    ))
                # Still recurse into arguments.
                for child in node.children[1:]:
                    walk(child)
                return

            elif node.type == "class_definition":
                # Inheritance: class Foo(Base, Mixin):
                arg_list = next(
                    (c for c in node.children if c.type == "argument_list"), None
                )
                if arg_list is not None:
                    for child in arg_list.children:
                        if child.type in {"identifier", "dotted_name", "attribute"}:
                            name = child.text.decode("utf-8", errors="replace")
                            refs.append(SymbolRef(
                                qualified_name=name,
                                ref_kind="inherit",
                                start_byte=child.start_byte,
                                end_byte=child.end_byte,
                            ))

            elif node.type == "type":
                # Type annotations used as type refs
                for child in node.children:
                    if child.type in {"identifier", "dotted_name", "attribute"}:
                        name = child.text.decode("utf-8", errors="replace")
                        refs.append(SymbolRef(
                            qualified_name=name,
                            ref_kind="type_ref",
                            start_byte=child.start_byte,
                            end_byte=child.end_byte,
                        ))
                return

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
