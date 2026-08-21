from typing import Protocol, runtime_checkable
from dataclasses import dataclass


def tree_is_clean(tree) -> bool:
    """True if *tree* has no error and nothing missing.

    Here rather than beside its first caller because two of them ask the question about
    trees they built differently: ``lang.parses_cleanly`` asks it of a file, and the
    notebook adapter asks it of one cell, to decide whether that cell is Python at all.
    A cell is not a file and cannot go through the file path — it has no name and it is
    read many times per notebook — but the judgment has to be the same one, or a cell
    could be commented out for damage a file would have been forgiven.

    ``has_error`` is true for every ancestor of an error, so a subtree without it cannot
    contain one and is skipped whole: the walk costs the damaged path, not the file.
    """
    cursor = tree.walk()
    while True:
        node = cursor.node
        if node.type == "ERROR" or node.is_missing:
            return False
        if node.has_error and cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return True


@dataclass
class Chunk:
    """A named, addressable unit of code extracted from a file."""
    symbol_path: str        # e.g. "pkg/file.py::ClassName.method_name"
    file: str               # repo-relative posix path
    start_byte: int
    end_byte: int
    source: str             # raw text of the chunk


@dataclass
class SymbolRef:
    """A reference from one chunk to another symbol, found by tree-sitter."""
    qualified_name: str     # e.g. "pkg.module.ClassName.method_name" or bare "function_name"
    ref_kind: str           # "import" | "call" | "inherit" | "type_ref"
    start_byte: int
    end_byte: int


@runtime_checkable
class LanguageAdapter(Protocol):
    language: str           # e.g. "python", "typescript"

    def extract_chunks(self, file: str, source: str) -> list[Chunk]:
        """Extract all top-level and nested entity chunks from source.
        Each function, class, method, and module-level statement group becomes a chunk.
        Returns chunks with populated symbol_path, file, start/end bytes, source."""
        ...

    @property
    def comment_node_kinds(self) -> set[str]:
        """Return the set of tree-sitter node type names that are comments for this language.
        Used by fingerprinting to drop comment nodes.
        Exposed as a property so that codoc.core.fingerprint can read it via getattr."""
        ...

    def resolve_symbol_path(self, source: str, symbol_path: str) -> tuple[int, int] | None:
        """Given source text and a symbol path (e.g. "file.py::ClassName.method"),
        return (start_byte, end_byte) of the named entity, or None if not found."""
        ...

    def run_ts_query(
        self, source: str, query_str: str,
        scope: tuple[int, int] | None = None,
    ) -> list[tuple[int, int]]:
        """Run a tree-sitter query against source (optionally scoped to a byte range).
        Returns list of (start_byte, end_byte) for all matches (capture group 0)."""
        ...

    def references_in_chunk(self, chunk_source: str, file: str) -> list[SymbolRef]:
        """Return all symbols referenced from within chunk_source (imports, calls, inherits, type refs).
        Used for binding-graph derivation."""
        ...

    def parse(self, source: str):
        """Return the tree-sitter parse tree for source."""
        ...

    def token_stream(self, source: str, exclude_comment_nodes: bool = True) -> list[str]:
        """Return ordered list of token texts (leaves of the parse tree), optionally excluding comments.
        Used by fingerprinting."""
        ...
