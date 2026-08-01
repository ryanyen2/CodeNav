"""Read-side API over the LanceDB code chunk index.

Bootstrap clustering, reflective comparison, and any future queries against
indexed chunks go through this module — neither cocoindex nor LanceDB
internals leak past this boundary.

Reads run on a process-lifetime background event loop with a cached LanceDB
connection per index path: the previous design paid a fresh event loop + a
fresh connection on every call, and the loops call this up to four times per
pass. Tables are re-opened per read (cheap) so each read sees the latest
committed version.
"""
from __future__ import annotations

import asyncio
import threading
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
    embedding kept as a plain numpy array (or None if not loaded / not stored —
    chunk embeddings are opt-in via ``CODOC_EMBED_CHUNKS``).
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


# --- background loop + connection cache -------------------------------------

# Every op is bounded: an unbounded .result() while the caller holds loop_lock
# would wedge THIS pass forever and make every other codoc process burn its
# 120s lock timeout per pass until someone kills us. 120s comfortably covers
# the slowest legitimate op observed (first-ever optimize of a years-bloated
# table: seconds).
_OP_TIMEOUT_S = 120.0

_loop_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_conns: dict[str, object] = {}


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    with _loop_lock:
        # Recreate on a closed loop OR a dead thread (a died thread leaves the
        # loop object open but nothing driving it — futures would never resolve).
        if (_loop is None or _loop.is_closed()
                or _loop_thread is None or not _loop_thread.is_alive()):
            _loop = asyncio.new_event_loop()
            _conns.clear()  # connections are bound to the old loop
            _loop_thread = threading.Thread(
                target=_loop.run_forever, name="codoc-lancedb-reader", daemon=True)
            _loop_thread.start()
        return _loop


def _run(coro):
    return asyncio.run_coroutine_threadsafe(coro, _get_loop()).result(
        timeout=_OP_TIMEOUT_S)


def invalidate_cache(codoc_dir: str | Path) -> None:
    """Drop (and best-effort close) the cached connection for an index that was
    wiped/rebuilt, so native handles don't leak across wipe cycles."""
    conn = _conns.pop(str(_lance_path(codoc_dir).resolve()), None)
    if conn is None or _loop is None or _loop.is_closed():
        return
    close = getattr(conn, "close", None)
    if close is None:
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            # fire-and-forget on the reader loop; never block the caller on it
            asyncio.run_coroutine_threadsafe(result, _loop)
    except Exception:  # noqa: BLE001 — closing a dead handle must never raise
        pass


async def _open_table(codoc_dir: str | Path):
    from lancedb import connect_async

    key = str(_lance_path(codoc_dir).resolve())
    conn = _conns.get(key)
    if conn is None:
        conn = await connect_async(key)
        _conns[key] = conn
    return await conn.open_table(LANCE_TABLE_NAME)


_BASE_COLUMNS = ["id", "file", "symbol_path", "language", "tokens_hash",
                 "types_hash", "start_byte", "end_byte"]


async def _read_all(
    codoc_dir: str | Path,
    files: set[str] | None = None,
    with_embeddings: bool = False,
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
        # The column exists only on an embeddings-on index; selecting a missing
        # column errors, so check the schema rather than assume.
        schema = await tbl.schema()
        if "embedding" in schema.names:
            cols.append("embedding")
    rows = await q.select(cols).to_list()
    return [ChunkRow.from_raw(r) for r in rows]


def read_all_chunks(
    codoc_dir: str | Path = ".codoc",
    *,
    files: set[str] | None = None,
    with_embeddings: bool = False,
    with_source: bool = True,
) -> list[ChunkRow]:
    """Read chunks from the index (synchronous).

    ``files`` pushes a ``file IN (…)`` predicate down to LanceDB so a scoped
    loop pass reads only the touched files' rows. ``with_embeddings`` /
    ``with_source`` control the two heavy columns; embeddings default OFF
    (nothing in the loops uses them, and the column only exists when
    ``CODOC_EMBED_CHUNKS=1``). Excluded columns come back as ``None`` / ``""``
    on the row.
    """
    try:
        return _run(_read_all(codoc_dir, files, with_embeddings, with_source))
    except Exception:
        # One retry on a fresh connection — covers a cached handle to an index
        # directory that was deleted + recreated underneath us.
        invalidate_cache(codoc_dir)
        return _run(_read_all(codoc_dir, files, with_embeddings, with_source))
