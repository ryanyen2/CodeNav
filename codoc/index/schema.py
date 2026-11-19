"""The row shape of the code chunk index: what cocoindex writes to the LanceDB
``code_chunks`` table and what ``reader`` reads back."""
from __future__ import annotations

from dataclasses import dataclass

import cocoindex as coco

LANCE_DB = coco.ContextKey["lancedb_module.LanceAsyncConnection"]("codoc_lance_db")
LANCE_TABLE_NAME = "code_chunks"


@dataclass
class CodeChunk:
    """One indexed AST chunk plus its identity hashes.

    ``id`` is ``generate_id(symbol)``, so the primary key is stable across runs
    while the chunk keeps its name. A moved chunk is a delete and an insert
    here; the reflect pass recognises the move from ``fingerprint`` and
    ``types_hash``.
    """

    id: int
    file: str
    symbol: str
    language: str
    source: str
    fingerprint: str
    types_hash: str
    start_byte: int
    end_byte: int
