"""The code side: what the code is, read without the document.

- ``lang``      — tree-sitter adapters (Python, TypeScript): chunks and references.
- ``tree_walk`` — the two identity hashes of a chunk.
- ``app``, ``runner``, ``schema``, ``reader`` — the cocoindex/LanceDB chunk index.
- ``changes``   — what an index update added, removed and modified.
- ``extract``, ``graph``, ``flows`` — the dependency graph over the chunks.
- ``git``       — the commit at HEAD and what recent commits said about a file.

Everything here is derived from the source tree and can be rebuilt.
"""
