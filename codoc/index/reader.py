"""Read the code chunk index.

Everything that queries indexed chunks goes through ``read_all_chunks``;
neither cocoindex nor LanceDB types leave this module.

Reads run on one background event loop per process, with a cached LanceDB
connection per index path (a pass reads up to four times). The table is
re-opened per read, which is cheap and sees the latest committed version.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, fields
from pathlib import Path

from codoc.index.schema import LANCE_TABLE_NAME, CodeChunk


def _lance_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / "lancedb"


@dataclass
class ChunkRow:
    """A row read out of the LanceDB ``code_chunks`` table
    (:class:`codoc.index.schema.CodeChunk`). ``source`` is ``""`` when the
    read left that column out."""

    id: int
    file: str
    symbol: str
    language: str
    source: str
    fingerprint: str
    types_hash: str
    start_byte: int
    end_byte: int

    @classmethod
    def from_raw(cls, raw: dict) -> "ChunkRow":
        return cls(
            id=raw["id"],
            file=raw["file"],
            symbol=raw["symbol"],
            language=raw.get("language", ""),
            source=raw.get("source", ""),
            fingerprint=raw.get("fingerprint", ""),
            types_hash=raw.get("types_hash", ""),
            start_byte=raw.get("start_byte", 0),
            end_byte=raw.get("end_byte", 0),
        )


# --- background loop + connection cache -------------------------------------

# Every op is bounded: an unbounded .result() would wedge the pass that called
# it. 120s comfortably covers the slowest legitimate op observed (the first
# optimize of a long-bloated table: seconds).
_OP_TIMEOUT_S = 120.0

_loop_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_conns: dict[str, object] = {}


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    with _loop_lock:
        # Recreate on a closed loop OR a dead thread (a died thread leaves the
        # loop object open but nothing driving it, so futures would never resolve).
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
    except Exception:  # noqa: BLE001 (closing a dead handle must never raise)
        pass


async def _open_table(codoc_dir: str | Path):
    from lancedb import connect_async

    key = str(_lance_path(codoc_dir).resolve())
    conn = _conns.get(key)
    if conn is None:
        conn = await connect_async(key)
        _conns[key] = conn
    return await conn.open_table(LANCE_TABLE_NAME)


_COLUMNS = [f.name for f in fields(CodeChunk)]


async def _current_table(codoc_dir: str | Path):
    """The chunk table, or None when there is none or its columns are not
    ``CodeChunk``'s (an older codoc wrote it)."""
    try:
        tbl = await _open_table(codoc_dir)
    except (FileNotFoundError, ValueError):
        return None
    return tbl if set((await tbl.schema()).names) == set(_COLUMNS) else None


def has_current_table(codoc_dir: str | Path) -> bool:
    """Whether the chunk table exists with ``CodeChunk``'s columns."""
    return _run(_current_table(codoc_dir)) is not None


async def _read_all(
    codoc_dir: str | Path,
    files: set[str] | None = None,
    with_source: bool = True,
) -> list[ChunkRow]:
    tbl = await _current_table(codoc_dir)
    if tbl is None:
        return []
    q = tbl.query()
    if files is not None:
        if not files:
            return []
        quoted = ", ".join("'" + f.replace("'", "''") + "'" for f in sorted(files))
        q = q.where(f"file IN ({quoted})")
    rows = await q.select([c for c in _COLUMNS if with_source or c != "source"]).to_list()
    return [ChunkRow.from_raw(r) for r in rows]


def read_all_chunks(
    codoc_dir: str | Path = ".codoc",
    *,
    files: set[str] | None = None,
    with_source: bool = True,
) -> list[ChunkRow]:
    """Read chunks from the index (synchronous).

    ``files`` pushes a ``file IN (…)`` predicate down to LanceDB so a scoped
    pass reads only the touched files' rows. ``with_source=False`` leaves the
    one heavy column out; it comes back as ``""`` on the row.
    """
    try:
        return _run(_read_all(codoc_dir, files, with_source))
    except Exception:
        # One retry on a fresh connection. It covers a cached handle to an index
        # directory that was deleted + recreated underneath us.
        invalidate_cache(codoc_dir)
        return _run(_read_all(codoc_dir, files, with_source))
