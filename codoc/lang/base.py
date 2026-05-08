from typing import Protocol, runtime_checkable
from dataclasses import dataclass


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
