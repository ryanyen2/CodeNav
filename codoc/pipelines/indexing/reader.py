"""Read-side API over the LanceDB code chunk index.

Bootstrap clustering, reflective comparison, and any future queries against
indexed chunks go through this module — neither cocoindex nor LanceDB
internals leak past this boundary.
"""
from __future__ import annotations

import asyncio
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
            language=raw.get("language", ""),
            source=raw.get("source", ""),
            tokens_hash=raw.get("tokens_hash", ""),
            types_hash=raw.get("types_hash", ""),
            start_byte=raw.get("start_byte", 0),
            end_byte=raw.get("end_byte", 0),
            embedding=emb,
        )


async def _open_table(codoc_dir: str | Path):
    from lancedb import connect_async

    conn = await connect_async(str(_lance_path(codoc_dir)))
    return await conn.open_table(LANCE_TABLE_NAME)


_BASE_COLUMNS = ["id", "file", "symbol_path", "language", "tokens_hash",
                 "types_hash", "start_byte", "end_byte"]


async def _read_all(
    codoc_dir: str | Path,
    files: set[str] | None = None,
    with_embeddings: bool = True,
    with_source: bool = True,
) -> list[ChunkRow]:
    try:
        tbl = await _open_table(codoc_dir)
    except (FileNotFoundError, ValueError):
        return []
    q = tbl.query()
    if files is not None:
        if not files:
            return []
        quoted = ", ".join("'" + f.replace("'", "''") + "'" for f in sorted(files))
        q = q.where(f"file IN ({quoted})")
    cols = list(_BASE_COLUMNS)
    if with_source:
        cols.append("source")
    if with_embeddings:
        cols.append("embedding")
    rows = await q.select(cols).to_list()
    return [ChunkRow.from_raw(r) for r in rows]


def read_all_chunks(
    codoc_dir: str | Path = ".codoc",
    *,
    files: set[str] | None = None,
    with_embeddings: bool = True,
    with_source: bool = True,
) -> list[ChunkRow]:
    """Read chunks from the index (synchronous).

    ``files`` pushes a ``file IN (…)`` predicate down to LanceDB so a scoped
    loop pass reads only the touched files instead of the whole table.
    ``with_embeddings`` / ``with_source`` drop the two heavy columns when the
    caller only needs identity hashes (the loops never need embeddings; the
    graph symbol table doesn't even need source). Excluded columns come back as
    ``None`` / ``""`` on the row.
    """
    return asyncio.run(_read_all(codoc_dir, files, with_embeddings, with_source))


