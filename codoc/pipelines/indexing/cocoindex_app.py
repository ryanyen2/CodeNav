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
    CodeChunkLite,
    embed_chunks_enabled,
)

_DEFAULT_LANCE_PATH = "./.codoc/lancedb"
_DEFAULT_SOURCE = "./test/small_python_repo"

# Match every extension the tree-sitter adapters (codoc.lang.detect_language)
# understand, so a file codoc *could* parse is never silently skipped at the walk.
_INCLUDED_PATTERNS = ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.mts", "**/*.cts"]
_EXCLUDED_PATTERNS = [
    ".*/**",                 # dot-dirs (.git, .tox, .mypy_cache, …)
    "**/__pycache__/**",
    "**/.venv/**",
    "**/venv/**",
    "**/env/**",
    "**/virtualenv/**",
    "**/node_modules/**",
    "**/bower_components/**",
    "**/.codoc/**",
    "**/vendor/**",
    "**/third_party/**",
    "**/dist/**",
    "**/build/**",
    "**/out/**",
    "**/target/**",          # rust/java build output
    "**/.next/**",
    "**/coverage/**",
    "**/site-packages/**",
    "**/*.d.ts",             # generated TypeScript declarations — not authored intent
    "**/*.min.js",
    "**/*.min.ts",
]

# Files above this many bytes are skipped: minified bundles, generated blobs, and
# vendored single-file libs are not authored intent, and parsing/embedding a 10 MB
# file stalls the loop and bloats the index. Overridable for unusual repos.
_MAX_FILE_BYTES = 1_500_000


def _gitignore_excludes(sourcedir: pathlib.Path) -> list[str]:
    """Best-effort: fold the repo's own ``.gitignore`` directory entries into the
    walker's exclude set so a project that ignores, say, ``env2/`` or ``generated/``
    doesn't get it indexed anyway. Conservative by design — it only ADDS excludes,
    skips negations/comments and the catch-all ``*``/``**`` lines (which would nuke
    everything), and never touches include patterns. Full gitignore semantics are
    not reproduced; the common directory-ignore case is."""
    patterns: list[str] = []
    try:
        text = (sourcedir / ".gitignore").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return patterns
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        line = line.rstrip("/")
        if line in ("", "*", "**", ".", "/"):
            continue  # too broad — would exclude the whole repo
        anchored = line.startswith("/")
        body = line.lstrip("/")
        if "/" in body and not anchored:
            continue  # a specific nested path — leave to git; avoid over-excluding
        prefix = "" if anchored else "**/"
        patterns.append(f"{prefix}{body}/**")
        patterns.append(f"{prefix}{body}")
    return patterns


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
    if embed_chunks_enabled():
        # Heavy import (sentence-transformers pulls torch, ~6s cold) — only when
        # the opt-in embedding column is actually being produced.
        from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder

        builder.provide(EMBEDDER, SentenceTransformerEmbedder(EMBEDDER_MODEL))
    yield


@coco.fn
async def _embed_chunk(
    item: tuple[str, str, str, str, int, int, str, str],
    target: lancedb.TableTarget[CodeChunk],
) -> None:
    """Embed + declare one chunk row (embeddings-on path).

    All CPU-bound parsing already happened in ``_process_file``, so every task
    spawned by ``coco.map`` reaches ``embed()`` almost immediately — the
    embedder's adaptive batching then coalesces the concurrent calls into large
    micro-batches instead of the size-1 batches an interleaved sync workload
    produces.
    """
    file_str, lang, symbol_path, source, start_byte, end_byte, tokens_hash, types_hash = item
    embedding = await coco.use_context(EMBEDDER).embed(source)
    chunk_id = await generate_id((file_str, symbol_path))
    target.declare_row(
        row=CodeChunk(
            id=chunk_id,
            file=file_str,
            symbol_path=symbol_path,
            language=lang,
            source=source,
            tokens_hash=tokens_hash,
            types_hash=types_hash,
            start_byte=start_byte,
            end_byte=end_byte,
            embedding=embedding,
        )
    )


@coco.fn(memo=True)
async def _process_file(
    file: FileLike,
    sourcedir: pathlib.Path,
    target: lancedb.TableTarget,
    embed: bool,
) -> None:
    file_abs = pathlib.Path(file.file_path.path)
    file_str = _repo_relative(file_abs, sourcedir)
    lang = detect_language(file_str)
    if lang is None:
        return
    # Skip oversize files (minified bundles, generated blobs) before reading them into
    # memory — they are not authored intent and would stall the loop / bloat the index.
    try:
        if file_abs.stat().st_size > _MAX_FILE_BYTES:
            return
    except OSError:
        return
    source = await file.read_text()
    adapter = get_adapter(lang)
    chunks = adapter.extract_chunks(file_str, source)
    # The chunk id is generate_id((file, symbol_path)) and the LanceDB PK, so the
    # system treats (file, symbol_path) as unique (bindings are UNIQUE on it, the
    # changeset keys on it). Real code legitimately repeats a qualified name
    # (@overload stubs, conditional/try-except redefinitions), which would yield
    # the same memoized id and a "target state already declared" PK collision —
    # dropping the whole file. Keep the first chunk per symbol_path so the file
    # still indexes and the uniqueness invariant holds.
    seen: set[str] = set()
    items = []
    for c in chunks:
        if c.symbol_path in seen:
            continue
        seen.add(c.symbol_path)
        walk_result = tree_walk(c.source, adapter)
        items.append((file_str, lang, c.symbol_path, c.source, c.start_byte,
                      c.end_byte, walk_result.tokens_hash, walk_result.types_hash))
    if embed:
        await coco.map(_embed_chunk, items, target)
        return
    for (f, lg, sym, src, sb, eb, th, tyh) in items:
        chunk_id = await generate_id((f, sym))
        target.declare_row(
            row=CodeChunkLite(
                id=chunk_id, file=f, symbol_path=sym, language=lg, source=src,
                tokens_hash=th, types_hash=tyh, start_byte=sb, end_byte=eb,
            )
        )


class _SymlinkAwareMatcher(PatternFilePathMatcher):
    """Pattern matcher plus symlink-loop protection the glob patterns can't express.

    A repo with a self-referential or ancestor-pointing directory symlink (a monorepo
    package link, ``docs/latest -> .``) would make the recursive walk descend without
    bound — an unrecoverable hang during ``codoc init``. We refuse to descend into any
    symlinked directory (indexing follows the real tree only); a symlink pointing
    OUTSIDE the repo is also refused, so the walk can't escape the repo root."""

    def __init__(self, sourcedir: pathlib.Path, *, included_patterns, excluded_patterns):
        super().__init__(included_patterns=included_patterns, excluded_patterns=excluded_patterns)
        self._sourcedir = pathlib.Path(sourcedir).resolve()

    def is_dir_included(self, path) -> bool:  # noqa: ANN001 — matches base signature
        try:
            # `path` is repo-relative (or absolute); joining an absolute right-hand
            # side simply yields it, so this resolves correctly either way.
            abs_path = (self._sourcedir / pathlib.Path(str(path)))
            if abs_path.is_symlink():
                return False
        except OSError:
            return False
        return super().is_dir_included(path)


@coco.fn
async def app_main(sourcedir: pathlib.Path) -> None:
    embed = embed_chunks_enabled()
    target = await lancedb.mount_table_target(
        LANCE_DB,
        table_name=LANCE_TABLE_NAME,
        table_schema=await lancedb.TableSchema.from_class(
            CodeChunk if embed else CodeChunkLite, primary_key=["id"]
        ),
    )
    files = localfs.walk_dir(
        sourcedir,
        recursive=True,
        path_matcher=_SymlinkAwareMatcher(
            sourcedir,
            included_patterns=_INCLUDED_PATTERNS,
            excluded_patterns=_EXCLUDED_PATTERNS + _gitignore_excludes(pathlib.Path(sourcedir)),
        ),
    )
    await coco.mount_each(_process_file, files.items(), sourcedir, target, embed)


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
