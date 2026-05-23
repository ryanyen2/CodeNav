"""Read-side API over the LanceDB code chunk index.

Bootstrap clustering, reflective comparison, and any future queries against
indexed chunks go through this module — neither cocoindex nor LanceDB
internals leak past this boundary.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from codoc.pipelines.indexing.schema import LANCE_TABLE_NAME


def _lance_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / "lancedb"


@dataclass
class ChunkRow:
    """A row read out of the LanceDB ``code_chunks`` table.

    Mirrors :class:`codoc.pipelines.indexing.schema.CodeChunk` but with the
    embedding kept as a plain numpy array (or None if not yet loaded).
    """

    id: int
    file: str
    symbol_path: str
    language: str
    source: str
    tokens_hash: str
    types_hash: str
    minhash: bytes
    start_byte: int
    end_byte: int
    embedding: np.ndarray | None = None

    @classmethod
    def from_raw(cls, raw: dict) -> "ChunkRow":
        emb = raw.get("embedding")
        if emb is not None and not isinstance(emb, np.ndarray):
            emb = np.asarray(emb)
        return cls(
            id=raw["id"],
            file=raw["file"],
            symbol_path=raw["symbol_path"],
            language=raw["language"],
            source=raw["source"],
            tokens_hash=raw["tokens_hash"],
            types_hash=raw["types_hash"],
            minhash=raw["minhash"],
            start_byte=raw["start_byte"],
            end_byte=raw["end_byte"],
            embedding=emb,
        )


async def _open_table(codoc_dir: str | Path):
    from lancedb import connect_async

    conn = await connect_async(str(_lance_path(codoc_dir)))
    return await conn.open_table(LANCE_TABLE_NAME)


async def _read_all(codoc_dir: str | Path) -> list[ChunkRow]:
    try:
        tbl = await _open_table(codoc_dir)
    except (FileNotFoundError, ValueError):
        return []
    rows = await tbl.query().to_list()
    return [ChunkRow.from_raw(r) for r in rows]


def read_all_chunks(codoc_dir: str | Path = ".codoc") -> list[ChunkRow]:
    """Return every chunk currently in the index (synchronous)."""
    return asyncio.run(_read_all(codoc_dir))


def chunks_by_file(rows: list[ChunkRow]) -> dict[str, list[ChunkRow]]:
    """Group rows by file path. Order within each file follows ``start_byte``."""
    grouped: dict[str, list[ChunkRow]] = defaultdict(list)
    for r in rows:
        grouped[r.file].append(r)
    for chunks in grouped.values():
        chunks.sort(key=lambda c: c.start_byte)
    return grouped


def per_file_mean_embeddings(
    rows: list[ChunkRow],
) -> dict[str, np.ndarray | None]:
    """Mean of chunk embeddings per file. Files with no embeddings map to None."""
    by_file = chunks_by_file(rows)
    result: dict[str, np.ndarray | None] = {}
    for file, chunks in by_file.items():
        vecs = [c.embedding for c in chunks if c.embedding is not None]
        if not vecs:
            result[file] = None
            continue
        result[file] = np.mean(np.stack(vecs), axis=0)
    return result


def fingerprint_index(rows: list[ChunkRow]) -> dict[tuple[str, str], ChunkRow]:
    """Index rows by (file, symbol_path) for fast lookup during reconciliation."""
    return {(r.file, r.symbol_path): r for r in rows}
