"""Schema for the cocoindex-managed code chunk index.

This module defines the LanceDB row shape that cocoindex writes to and that
the rest of codoc reads from. It is the single source of truth for the
indexing layer's contract.

Chunk embeddings are OPT-IN (``CODOC_EMBED_CHUNKS=1``): nothing in the loops,
bootstrap, or graph reads the vectors today, so the default schema omits the
column and the pipeline never imports sentence-transformers (a multi-second
cold cost per process). Flipping the flag is detected by the runner, which
rebuilds the index from scratch under the other schema.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from numpy.typing import NDArray

import cocoindex as coco

if TYPE_CHECKING:  # heavy import — only for type checkers, never at runtime
    from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder

LANCE_DB = coco.ContextKey["lancedb_module.LanceAsyncConnection"]("codoc_lance_db")
EMBEDDER = coco.ContextKey["SentenceTransformerEmbedder"]("codoc_embedder")

EMBEDDER_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
LANCE_TABLE_NAME = "code_chunks"


def embed_chunks_enabled() -> bool:
    """Whether chunk embeddings are computed + stored (default: off)."""
    return os.environ.get("CODOC_EMBED_CHUNKS", "").strip().lower() in ("1", "true", "yes")


def embed_chunks_requested() -> bool | None:
    """The user's EXPLICIT embed choice, or None when the env var is unset.

    The runner treats an unset var as "follow the index's recorded state": two
    long-lived processes with different environments (a daemon that predates an
    export vs a fresh CLI) must not alternately wipe and rebuild the index on
    every pass. Only an explicit setting flips the recorded state."""
    raw = os.environ.get("CODOC_EMBED_CHUNKS")
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in ("1", "true", "yes")


@dataclass
class CodeChunkLite:
    """One indexed AST chunk plus its identity signals (no embedding column).

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


@dataclass
class CodeChunk(CodeChunkLite):
    """A chunk row with its embedding vector (only when ``CODOC_EMBED_CHUNKS=1``)."""

    embedding: Annotated[NDArray, EMBEDDER]
