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


def _module_assignment_name(node: ts.Node) -> str | None:
    """If *node* declares a single public module-level NAME, return it.

    Covers ``NAME = value`` and the annotated ``NAME: T = value``, plus PEP 695's
    ``type NAME = …``. Returns None for private names (leading underscore),
    augmented assignments, destructuring patterns, and anything that isn't a
    single-identifier LHS.
    """
    if node.type == "type_alias_statement":
        # PEP 695 `type Alias = int | str`. A public alias is a named entity a
        # description cites by name, and the pre-695 spelling (`Alias: TypeAlias =
        # …`) already got an address as an ordinary assignment — the newer syntax
        # for the same declaration should not be the one that disappears into glue.
        for child in node.children:
            if child.type == "type":
                ident = next((c for c in child.children if c.type == "identifier"), None)
                if ident is None:
                    continue  # the `type` keyword itself, not the alias name
                name = ident.text.decode("utf-8", errors="replace")
                return name if name and not name.startswith("_") else None
        return None
    if node.type != "expression_statement":
        return None
    for child in node.children:
        if child.type == "assignment":
            targets = [c for c in child.children if c.type not in {"=", "type"}]
            if not targets:
                return None
            lhs = targets[0]
            if lhs.type == "identifier":
                name = lhs.text.decode("utf-8", errors="replace")
                if name and not name.startswith("_"):
                    return name
    return None


_DEF_KINDS = {"function_definition", "class_definition"}

# Statements that hold definitions WITHOUT opening a scope. A `def` inside an
# `if` or an `except` branch binds the same namespace as one at the top of the
# file — `loads` in an optional-dependency fallback is still `file.py::loads` —
# and real Python keeps a great deal of code there: compat shims, `try/except
# ImportError` fallbacks, `if TYPE_CHECKING` protocols, version branches.
# Walking only direct children left every one of those with no address of its
# own: unbindable, undescribable, and swallowed whole into `__module__`.
_TRANSPARENT = {
    "if_statement", "elif_clause", "else_clause",
    "try_statement", "except_clause", "except_group_clause", "finally_clause",
    "with_statement", "for_statement", "while_statement",
    "match_statement", "case_clause", "block",
}


def _peel(node: ts.Node) -> tuple[ts.Node, ts.Node] | None:
    """``(chunk node, definition node)`` if *node* defines a function or class.

    The two differ for a decorated definition: the chunk is the whole
    ``decorated_definition`` (a decorator is part of what the entity IS), while
    the name and the body come from the definition inside it.
    """
    if node.type in _DEF_KINDS:
        return node, node
    if node.type == "decorated_definition":
        inner = next((c for c in node.children if c.type in _DEF_KINDS), None)
        return (node, inner) if inner is not None else None
    return None


def _same_scope_defs(node: ts.Node) -> list[tuple[ts.Node, ts.Node]]:
    """Definitions *node* contributes to its ENCLOSING scope, in source order.

    Descends through transparent statements only, and stops at every definition
    it finds — a nested `def` inside a conditionally defined function belongs to
    that function's scope, not to this one.
    """
    found: list[tuple[ts.Node, ts.Node]] = []
    for child in node.children:
        peeled = _peel(child)
        if peeled is not None:
            found.append(peeled)
        elif child.type in _TRANSPARENT:
            found.extend(_same_scope_defs(child))
    return found


def _line_start(source_bytes: bytes, pos: int) -> int:
    """*pos* moved back over its line's leading whitespace, if that is all of it.

    A definition node starts at its ``def`` or its ``@``, so a nested one arrives
    already stripped of the indentation its body still carries. Harmless for a
    single piece; for several joined together it would make every piece after the
    first read as though it were dedented on its opening line alone.
    """
    start = source_bytes.rfind(b"\n", 0, pos) + 1
    return start if not source_bytes[start:pos].strip() else pos


def _text(source_bytes: bytes, spans: list[tuple[int, int]]) -> str:
    """The text of *spans*: one is sliced whole, several are joined.

    Joined rather than spanned from first to last, because the definitions of one
    name are not always adjacent — a property's getter and its setter can sit
    either side of three other methods, and a span would swallow those into this
    chunk as well, so editing one of them would read as a change to this one.
    Dropping the gaps keeps a chunk's source to the code it is actually about.
    """
    if len(spans) == 1:
        start, end = spans[0]
        return source_bytes[start:end].decode("utf-8", errors="replace")
    parts = [source_bytes[spans[0][0]:spans[0][1]]]
    parts += [source_bytes[_line_start(source_bytes, start):end] for start, end in spans[1:]]
    return "\n\n".join(part.decode("utf-8", errors="replace") for part in parts)


def _extract_chunks_recursive(
    node: ts.Node,
    source_bytes: bytes,
    file: str,
    prefix: str,
    chunks: list[Chunk],
) -> None:
    """Walk one scope's statements and emit a Chunk per named entity.

    prefix  – dot-separated qualified name of the enclosing scope, e.g. "MyClass"
               or "" for module scope.
    Definitions inside transparent statements (`if`, `try`, …) belong to THIS
    scope and are collected here. At module scope, the statements that define
    nothing accumulate into runs that become the single `__module__` chunk; a
    class body has no such chunk (only its methods and nested classes matter).
    """
    is_module_scope = prefix == ""
    module_runs: list[tuple[int, int]] = []
    run: list[int] | None = None
    # (qualified name, start, end) in source order; merged by name at the end.
    pieces: list[tuple[str, int, int]] = []

    def close_run() -> None:
        nonlocal run
        if run is not None:
            module_runs.append((run[0], run[1]))
            run = None

    def recurse_class(def_node: ts.Node, qualified: str) -> None:
        if def_node.type != "class_definition":
            return
        body = next((c for c in def_node.children if c.type == "block"), None)
        if body is not None:
            _extract_chunks_recursive(body, source_bytes, file, qualified, chunks)

    def emit(chunk_node: ts.Node, def_node: ts.Node) -> None:
        name = _node_name(def_node)
        if name is None:
            return
        qualified = f"{prefix}.{name}" if prefix else name
        pieces.append((qualified, chunk_node.start_byte, chunk_node.end_byte))
        recurse_class(def_node, qualified)

    for child in node.children:
        peeled = _peel(child)
        if peeled is not None:
            close_run()
            emit(*peeled)
            continue

        if child.type in _TRANSPARENT:
            inner = _same_scope_defs(child)
            if inner:
                close_run()
                names = {_node_name(d) for _, d in inner}
                if len(names) == 1 and None not in names:
                    # The guard IS the definition. One name defined under a
                    # condition — `if sys.version_info >= (3, 12)` with a fallback
                    # in the `else`, a `try` import with a pure-python `except`
                    # branch — is one entity, and the whole statement is its chunk,
                    # so a reader keeps the condition it exists under instead of
                    # one arbitrary branch stripped of its guard.
                    only = str(names.pop())
                    qualified = f"{prefix}.{only}" if prefix else only
                    pieces.append((qualified, child.start_byte, child.end_byte))
                    for _, def_node in inner:
                        recurse_class(def_node, qualified)
                else:
                    # Several names under one guard: no single entity to give the
                    # statement to, so each definition keeps its own address and
                    # the guard is simply not part of any chunk.
                    for peeled_inner in inner:
                        emit(*peeled_inner)
                continue
            # Defines nothing → ordinary module glue, handled below.

        if is_module_scope and child.type not in {"comment", "newline", ""}:
            assign_name = _module_assignment_name(child)
            if assign_name:
                # A public module-level constant is its own bindable entity.
                close_run()
                pieces.append((assign_name, child.start_byte, child.end_byte))
            else:
                if run is None:
                    run = [child.start_byte, child.end_byte]
                else:
                    run[1] = child.end_byte
    close_run()

    # One qualified name, one address. Real Python defines a name several times in
    # one scope — `@overload` stubs before the implementation, a property and its
    # setter, an `if`/`else` pair — and `(file, symbol_path)` is the index key, the
    # chunk id, and the UNIQUE binding key. Those definitions used to collapse to
    # whichever came first, so an overloaded function was indexed as its empty
    # signature stub and described from it. All the definitions of one name are ONE
    # chunk: the address stays stable when an overload is added, and the hash moves
    # when any part of the entity moves.
    by_name: dict[str, list[tuple[int, int]]] = {}
    for qualified, start, end in pieces:
        by_name.setdefault(qualified, []).append((start, end))
    for qualified, spans in by_name.items():
        chunks.append(
            Chunk(
                symbol_path=f"{file}::{qualified}",
                file=file,
                start_byte=spans[0][0],
                end_byte=spans[-1][1],
                source=_text(source_bytes, spans),
            )
        )

    if is_module_scope and module_runs:
        # The module chunk is the glue and nothing else. It used to span from the
        # first top-level statement to the last, which put every constant and every
        # conditionally defined function inside it as well — so editing one line of
        # a function marked the module's own feature changed too.
        chunks.append(
            Chunk(
                symbol_path=f"{file}::__module__",
                file=file,
                start_byte=module_runs[0][0],
                end_byte=module_runs[-1][1],
                source=_text(source_bytes, module_runs),
            )
        )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class PythonAdapter:
    language = LANGUAGE_NAME

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def extract_chunks(self, file: str, source: str) -> list[Chunk]:
        """Every chunk this file contributes, one per named entity, no repeats.

        Uniqueness of ``(file, symbol_path)`` is a guarantee of the walk rather
        than an accident of the code being simple: same-name definitions are
        merged in the scope that holds them (see ``_extract_chunks_recursive``).
        """
        source_bytes = source.encode("utf-8")
        tree = self.parse(source)
        chunks: list[Chunk] = []
        _extract_chunks_recursive(tree.root_node, source_bytes, file, "", chunks)
        return chunks

    @property
    def comment_node_kinds(self) -> set[str]:  # type: ignore[override]
        return {"comment"}

    def resolve_symbol_path(self, source: str, symbol_path: str) -> tuple[int, int] | None:
        """Resolve "file.py::ClassName.method" → the byte range of that entity.

        Answered by extracting the file's chunks and looking the address up, so
        this cannot disagree with what the index holds: a conditionally defined
        function resolves to the statement that defines it, and a name with
        several definitions resolves to the range that holds them all. It used to
        walk the tree itself, one level of direct children at a time — a second,
        subtly different traversal that missed everything the walk above exists
        to find.
        """
        qualified = symbol_path.split("::", 1)[1] if "::" in symbol_path else symbol_path
        for chunk in self.extract_chunks("", source):
            if chunk.symbol_path == f"::{qualified}":
                return (chunk.start_byte, chunk.end_byte)
        return None

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
            # Use the first capture group's first node.
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
