"""Python language adapter using tree-sitter via tree_sitter_languages."""

from __future__ import annotations

import tree_sitter as ts

from codoc.index.lang.base import Chunk, SymbolRef, parser

# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _node_name(node: ts.Node) -> str | None:
    """Return the identifier text of a function_definition or class_definition node."""
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8", errors="replace")
    return None


def _is_overload(decorated: ts.Node) -> bool:
    """An ``@overload`` stub: a signature for type checkers. The implementation
    follows under the same name, and it is the chunk."""
    return any(d.type == "decorator"
               and d.text.decode("utf-8", errors="replace").lstrip("@").strip().rsplit(".", 1)[-1] == "overload"
               for d in decorated.children)


def _module_assignment_name(node: ts.Node) -> str | None:
    """If *node* is a simple ``NAME = value`` expression_statement, return NAME.

    Returns None for private names (leading underscore), augmented assignments,
    destructuring patterns, and anything that isn't a single-identifier LHS.
    """
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
    single __module__ chunk.  Inside a class body they are skipped: only methods and
    nested classes are chunks there.
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
                    symbol=f"{file}::__module__",
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
        # decorated_definition wraps the real function/class; peel it.
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
                if inner is None or _is_overload(child):
                    continue
                def_node = inner
            else:
                def_node = child

            entity_name = _node_name(def_node)
            if entity_name is None:
                continue

            qualified = f"{prefix}.{entity_name}" if prefix else entity_name
            symbol = f"{file}::{qualified}"

            raw = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
            chunks.append(
                Chunk(
                    symbol=symbol,
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
            # Non-definition node at module scope.
            if is_module_scope and child.type not in {"comment", "newline", ""}:
                # Emit simple public assignments (NAME = ...) as named chunks so
                # features can bind to specific module-level constructs.
                assign_name = _module_assignment_name(child)
                if assign_name:
                    flush_module_chunk()
                    raw = source_bytes[child.start_byte:child.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    chunks.append(
                        Chunk(
                            symbol=f"{file}::{assign_name}",
                            file=file,
                            start_byte=child.start_byte,
                            end_byte=child.end_byte,
                            source=raw,
                        )
                    )
                else:
                    # Imports, augmented assignments, patterns → __module__ run.
                    if module_start is None:
                        module_start = child.start_byte
                    module_end = child.end_byte

    if is_module_scope:
        flush_module_chunk()


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class PythonAdapter:
    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def extract_chunks(self, file: str, source: str) -> list[Chunk]:
        source_bytes = source.encode("utf-8")
        tree = self.parse(source)
        chunks: list[Chunk] = []
        _extract_chunks_recursive(tree.root_node, source_bytes, file, "", chunks)
        # De-duplicate __module__ chunks (merge all into one).
        module_chunks = [c for c in chunks if c.symbol.endswith("::__module__")]
        other_chunks = [c for c in chunks if not c.symbol.endswith("::__module__")]
        if module_chunks:
            merged_start = min(c.start_byte for c in module_chunks)
            merged_end = max(c.end_byte for c in module_chunks)
            merged_source = source_bytes[merged_start:merged_end].decode("utf-8", errors="replace")
            other_chunks.append(
                Chunk(
                    symbol=f"{file}::__module__",
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
                        # import x as y: record the original name
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
        return parser("python").parse(source.encode("utf-8"))
