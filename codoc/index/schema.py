"""The row shape of the code chunk index: what cocoindex writes to the LanceDB
``code_chunks`` table and what ``reader`` reads back."""
from __future__ import annotations

from dataclasses import dataclass

import cocoindex as coco

LANCE_DB = coco.ContextKey["lancedb_module.LanceAsyncConnection"]("codoc_lance_db")
LANCE_TABLE_NAME = "code_chunks"


@dataclass
class CodeChunk:
    """One indexed AST chunk plus its identity signals.

    ``id`` is derived from ``(file, symbol_path)`` via ``generate_id`` so the
    primary key is stable across runs as long as the chunk's location is
    stable. A moved chunk is delete+insert here; the reflect pass recognises
    the move from ``tokens_hash`` / ``types_hash``.
    """

    id: int
    file: str
    symbol_path: str
    language: str
    source: str
    tokens_hash: str
    types_hash: str
    start_byte: int
    end_byte: int
