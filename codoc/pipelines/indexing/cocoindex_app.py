"""Cocoindex App for incremental code indexing.

Walks a source directory, extracts AST chunks via codoc.lang adapters, embeds
each chunk with sentence-transformers, and persists to a local LanceDB table.
Re-running only re-processes files whose content fingerprint changed; killing
the process mid-run is safe because cocoindex's internal SQLite state (at
``.codoc/cocoindex.db``) tracks per-component memoization.

Programmatic entry: :func:`make_app` (used by
:mod:`codoc.pipelines.indexing.runner`). For CLI use, the module-level
``app`` object provides defaults from environment variables.
"""
from __future__ import annotations

import os
import pathlib
from typing import AsyncIterator

import cocoindex as coco
from cocoindex.connectors import lancedb, localfs
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from cocoindex.resources.file import FileLike, PatternFilePathMatcher
from cocoindex.resources.id import generate_id

from codoc.core.tree_walk import walk as tree_walk
from codoc.lang import detect_language, get_adapter
from codoc.pipelines.indexing.schema import (
    EMBEDDER,
    EMBEDDER_MODEL,
    LANCE_DB,
    LANCE_TABLE_NAME,
    CodeChunk,
)

_DEFAULT_LANCE_PATH = "./.codoc/lancedb"
_DEFAULT_SOURCE = "./test/small_python_repo"

_INCLUDED_PATTERNS = ["**/*.py", "**/*.ts", "**/*.tsx"]
_EXCLUDED_PATTERNS = [
    ".*/**",
    "**/__pycache__/**",
    "**/.venv/**",
    "**/node_modules/**",
    "**/.codoc/**",
    "**/dist/**",
    "**/build/**",
]


def _repo_relative(file_path: pathlib.Path, sourcedir: pathlib.Path) -> str:
    try:
        return file_path.relative_to(sourcedir).as_posix()
    except ValueError:
        return file_path.as_posix()


@coco.lifespan
async def _coco_lifespan(
    builder: coco.EnvironmentBuilder,
) -> AsyncIterator[None]:
    lance_path = os.environ.get("CODOC_LANCE_PATH", _DEFAULT_LANCE_PATH)
    pathlib.Path(lance_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await lancedb.connect_async(lance_path)
    builder.provide(LANCE_DB, conn)
    builder.provide(EMBEDDER, SentenceTransformerEmbedder(EMBEDDER_MODEL))
    yield


@coco.fn
async def _process_chunk(
    item: tuple[str, str, str, str, int, int],
    target: lancedb.TableTarget[CodeChunk],
) -> None:
    file_str, lang, symbol_path, source, start_byte, end_byte = item
    walk_result = tree_walk(source, get_adapter(lang))
    embedding = await coco.use_context(EMBEDDER).embed(source)
    chunk_id = await generate_id((file_str, symbol_path))
    target.declare_row(
        row=CodeChunk(
            id=chunk_id,
            file=file_str,
            symbol_path=symbol_path,
            language=lang,
            source=source,
            tokens_hash=walk_result.tokens_hash,
            types_hash=walk_result.types_hash,
            minhash=walk_result.minhash,
            start_byte=start_byte,
            end_byte=end_byte,
            embedding=embedding,
        )
    )


@coco.fn(memo=True)
async def _process_file(
    file: FileLike,
    sourcedir: pathlib.Path,
    target: lancedb.TableTarget[CodeChunk],
) -> None:
    file_abs = pathlib.Path(file.file_path.path)
    file_str = _repo_relative(file_abs, sourcedir)
    lang = detect_language(file_str)
    if lang is None:
        return
    source = await file.read_text()
    adapter = get_adapter(lang)
    chunks = adapter.extract_chunks(file_str, source)
    items = [
        (file_str, lang, c.symbol_path, c.source, c.start_byte, c.end_byte)
        for c in chunks
    ]
    await coco.map(_process_chunk, items, target)


@coco.fn
async def app_main(sourcedir: pathlib.Path) -> None:
    target = await lancedb.mount_table_target(
        LANCE_DB,
        table_name=LANCE_TABLE_NAME,
        table_schema=await lancedb.TableSchema.from_class(
            CodeChunk, primary_key=["id"]
        ),
    )
    files = localfs.walk_dir(
        sourcedir,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=_INCLUDED_PATTERNS,
            excluded_patterns=_EXCLUDED_PATTERNS,
        ),
    )
    await coco.mount_each(_process_file, files.items(), sourcedir, target)


def make_app(sourcedir: pathlib.Path, app_name: str = "CodocIndex") -> coco.App:
    """Build a cocoindex App for the given source directory.

    The lifespan picks up ``CODOC_LANCE_PATH`` from the environment, so callers
    should set that env var before invoking ``update_blocking()`` on the
    returned App.
    """
    return coco.App(
        coco.AppConfig(name=app_name),
        app_main,
        sourcedir=pathlib.Path(sourcedir).resolve(),
    )


def _default_sourcedir() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get("CODOC_INDEX_SOURCE", _DEFAULT_SOURCE)
    ).resolve()


if __name__ == "__main__":
    _app = make_app(_default_sourcedir())
    _app.update_blocking(report_to_stdout=True)
