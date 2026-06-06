"""Schema for the cocoindex-managed code chunk index.

This module defines the LanceDB row shape that cocoindex writes to and that
the rest of codoc reads from. It is the single source of truth for the
indexing layer's contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from numpy.typing import NDArray

import cocoindex as coco
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder

LANCE_DB = coco.ContextKey["lancedb_module.LanceAsyncConnection"]("codoc_lance_db")
EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder]("codoc_embedder")

EMBEDDER_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
LANCE_TABLE_NAME = "code_chunks"


@dataclass
class CodeChunk:
    """One indexed AST chunk plus its identity signals and embedding.

    ``id`` is derived from ``(file, symbol_path)`` via ``generate_id`` so the
    primary key is stable across runs as long as the chunk's location is
    stable. A moved chunk is delete+insert at the indexing layer; codoc's
    reconciler detects moves at a higher level via ``tokens_hash``/``types_hash``.
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
    embedding: Annotated[NDArray, EMBEDDER]
