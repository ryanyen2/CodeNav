import ctypes
import functools
import pathlib
import warnings
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import tree_sitter as ts


@dataclass
class Chunk:
    """A named, addressable unit of code extracted from a file."""
    symbol: str             # the chunk's key, e.g. "pkg/file.py::ClassName.method_name"
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
        """Every function, class and method in ``source`` as a chunk, plus one
        ``__module__`` chunk for the module-level statements."""
        ...

    @property
    def comment_node_kinds(self) -> set[str]:
        """The tree-sitter node types that are comments in this language.
        ``tree_walk`` drops these when it fingerprints a chunk."""
        ...

    def references_in_chunk(self, chunk_source: str, file: str) -> list[SymbolRef]:
        """Every symbol ``chunk_source`` references (imports, calls, base
        classes, type annotations), for the dependency graph."""
        ...

    def parse(self, source: str):
        """The tree-sitter parse tree for ``source``."""
        ...


@functools.cache
def parser(name: str) -> ts.Parser:
    """A tree-sitter parser for the grammar ``name``.

    ``tree_sitter_languages`` ships precompiled grammars in ``languages.so``,
    built against the tree-sitter 0.20 API. Loading the library with ctypes
    gives the raw ``TSLanguage`` pointer, which the 0.25 binding accepts.
    """
    import tree_sitter_languages

    lib = ctypes.cdll.LoadLibrary(str(pathlib.Path(tree_sitter_languages.__file__).parent / "languages.so"))
    fn = getattr(lib, f"tree_sitter_{name}")
    fn.restype = ctypes.c_void_p
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return ts.Parser(ts.Language(fn()))
