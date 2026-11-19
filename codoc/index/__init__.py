"""The code side: what the code is, read without the document.

- ``lang``:       tree-sitter adapters (Python, TypeScript): chunks and references.
- ``tree_walk``:  the two identity hashes of a chunk.
- ``app``, ``runner``, ``schema``, ``reader``: the cocoindex/LanceDB chunk index.
- ``changes``:    what an index update added, removed and modified.
- ``extract``, ``graph``, ``flows``: the dependency graph over the chunks.
- ``git``:        the commit at HEAD and what recent commits said about a file.

Everything here is derived from the source tree and can be rebuilt.
"""
import os

# lancedb's native logger reads LANCEDB_LOG once, when lancedb first loads,
# which is always through this package. Writing a table that does not exist yet
# logs a warning, and every first index does that; every other warning, and
# every error, still prints. A LANCEDB_LOG the user set wins.
os.environ.setdefault("LANCEDB_LOG", "warn,lance::dataset::write::insert=error")

# cocoindex sends usage events (app created, app updated) to its maintainers
# unless this is set. Indexing someone's code is not the place for that by
# default; a value the user set wins.
os.environ.setdefault("COCOINDEX_DISABLE_USAGE_TRACKING", "1")
