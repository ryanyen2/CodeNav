"""The cocoindex app that indexes a source tree into code chunks.

Walks a source directory, extracts AST chunks with the ``codoc.index.lang``
adapters, hashes each one (``tree_walk``), and writes the rows to the LanceDB
table in ``.codoc/lancedb``. A re-run re-processes only files whose content
changed, and a killed run resumes: cocoindex keeps per-file memo state in
``.codoc/cocoindex.db``.

Entry point: :func:`make_app`, called by :mod:`codoc.index.runner`.
"""
from __future__ import annotations

import json
import pathlib
from typing import AsyncIterator

import cocoindex as coco
from cocoindex.connectors import lancedb, localfs
from cocoindex.resources.file import FileLike, PatternFilePathMatcher
from cocoindex.resources.id import generate_id

from codoc.index.lang import EXTENSIONS, detect_language, get_adapter
from codoc.index.reader import _lance_path
from codoc.index.schema import LANCE_DB, LANCE_TABLE_NAME, CodeChunk
from codoc.index.tree_walk import walk as tree_walk

# Every extension the tree-sitter adapters understand, so the walk never skips
# a file codoc can parse.
_INCLUDED_PATTERNS = [f"**/*{ext}" for ext in EXTENSIONS]
_EXCLUDED_PATTERNS = [
    # Never source, at any depth.
    "**/.codoc/**",
    "**/.next/**",
    "**/__pycache__/**",
    "**/.venv/**",
    "**/venv/**",
    "**/virtualenv/**",
    "**/site-packages/**",
    "**/node_modules/**",
    "**/bower_components/**",
    # Dot-directories, environments, build output and vendored code, at the root
    # only: a package may be named `env` or `build`. A project's .gitignore
    # excludes the nested ones it ignores.
    ".*/**",                 # .git, .tox, .mypy_cache, …
    "env/**",
    "build/**",
    "out/**",
    "dist/**",
    "target/**",
    "coverage/**",
    "vendor/**",
    "third_party/**",
    # Generated files.
    "**/*.d.ts",
    "**/*.min.ts",
]

# `index_exclude` in `.codoc/config.json`, as .gitignore lines, when the file
# sets none. Tests, docs and examples are code, but a tree of what the product
# does has no feature for them, and every indexed file costs a bootstrap call.
DEFAULT_INDEX_EXCLUDE = ["test/", "tests/", "__tests__/", "*.test.*", "*.spec.*",
                         "test_*.py", "*_test.py", "conftest.py", "docs/", "examples/"]

# Files above this many bytes are skipped: minified bundles, generated blobs, and
# vendored single-file libs are not authored intent, and parsing a 10 MB file
# stalls the pass and bloats the index.
_MAX_FILE_BYTES = 1_500_000


def _configured_excludes(sourcedir: pathlib.Path) -> list[str]:
    """The root ``.gitignore`` and ``index_exclude`` in ``.codoc/config.json``
    (``DEFAULT_INDEX_EXCLUDE`` when it sets none), as exclude patterns."""
    try:
        gitignore = (sourcedir / ".gitignore").read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        gitignore = []
    try:
        listed = json.loads((sourcedir / ".codoc" / "config.json").read_text(encoding="utf-8")).get("index_exclude")
    except (OSError, ValueError, AttributeError):
        listed = None
    if not isinstance(listed, list):
        listed = DEFAULT_INDEX_EXCLUDE
    return _ignore_patterns(gitignore + [x for x in listed if isinstance(x, str)])


def _ignore_patterns(lines: list[str]) -> list[str]:
    """.gitignore lines as exclude patterns.

    Only the common case is read: a name or a glob, anchored with ``/`` or not.
    Negations, comments, the catch-all ``*`` and ``**`` lines, and unanchored
    nested paths are skipped, so the result only ever excludes more."""
    patterns: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        line = line.rstrip("/")
        if line in ("", "*", "**", ".", "/"):
            continue  # too broad: would exclude the whole repo
        anchored = line.startswith("/")
        body = line.lstrip("/")
        if "/" in body and not anchored:
            continue  # a specific nested path: leave to git; avoid over-excluding
        prefix = "" if anchored else "**/"
        patterns.append(f"{prefix}{body}/**")
        patterns.append(f"{prefix}{body}")
    return patterns


def _repo_relative(file_path: pathlib.Path, sourcedir: pathlib.Path) -> str:
    try:
        return file_path.relative_to(sourcedir).as_posix()
    except ValueError:
        return file_path.as_posix()


# The `.codoc` directory the lifespan opens, set by `make_app`. cocoindex starts
# its environment, and so runs the lifespan, once per process: a process
# indexes one directory.
_codoc_dir: pathlib.Path | None = None


@coco.lifespan
async def _coco_lifespan(
    builder: coco.EnvironmentBuilder,
) -> AsyncIterator[None]:
    builder.settings.db_path = _codoc_dir / "cocoindex.db"
    builder.provide(LANCE_DB, await lancedb.connect_async(str(_lance_path(_codoc_dir))))
    yield


@coco.fn(memo=True)
async def _process_file(
    file: FileLike,
    sourcedir: pathlib.Path,
    target: lancedb.TableTarget,
) -> None:
    file_abs = pathlib.Path(file.file_path.path)
    file_str = _repo_relative(file_abs, sourcedir)
    lang = detect_language(file_str)
    if lang is None:
        return
    # Skip oversize files (minified bundles, generated blobs) before reading them into
    # memory. They are not authored intent and would stall the loop / bloat the index.
    try:
        if file_abs.stat().st_size > _MAX_FILE_BYTES:
            return
    except OSError:
        return
    source = await file.read_text()
    adapter = get_adapter(lang)
    chunks = adapter.extract_chunks(file_str, source)
    # The symbol is the row's key: its id is generate_id(symbol), and the change
    # set and the bindings are keyed by it. A class can define a name twice (a
    # property's getter and setter, a redefinition), and a second row with the
    # same id is a "target state already declared" error that drops the whole
    # file, so the first chunk per symbol is kept.
    seen: set[str] = set()
    for c in chunks:
        if c.symbol in seen:
            continue
        seen.add(c.symbol)
        walk_result = tree_walk(c.source, adapter)
        target.declare_row(
            row=CodeChunk(
                id=await generate_id(c.symbol), file=file_str, symbol=c.symbol, language=lang, source=c.source,
                fingerprint=walk_result.fingerprint, types_hash=walk_result.types_hash,
                start_byte=c.start_byte, end_byte=c.end_byte,
            )
        )


class _SymlinkAwareMatcher(PatternFilePathMatcher):
    """The pattern matcher, refusing every symlinked directory.

    A directory symlink to itself or an ancestor (a monorepo package link,
    ``docs/latest -> .``) would make the recursive walk descend without bound,
    and one pointing outside the repository would take the walk out of it. The
    index follows the real tree only."""

    def __init__(self, sourcedir: pathlib.Path, *, included_patterns, excluded_patterns):
        super().__init__(included_patterns=included_patterns, excluded_patterns=excluded_patterns)
        self._sourcedir = pathlib.Path(sourcedir).resolve()

    def is_dir_included(self, path) -> bool:  # noqa: ANN001 (matches base signature)
        try:
            # `path` is repo-relative (or absolute); joining an absolute right-hand
            # side yields it, so this resolves correctly either way.
            abs_path = (self._sourcedir / pathlib.Path(str(path)))
            if abs_path.is_symlink():
                return False
        except OSError:
            return False
        return super().is_dir_included(path)


@coco.fn
async def app_main(sourcedir: pathlib.Path) -> None:
    target = await lancedb.mount_table_target(
        LANCE_DB,
        table_name=LANCE_TABLE_NAME,
        table_schema=await lancedb.TableSchema.from_class(CodeChunk, primary_key=["id"]),
    )
    files = localfs.walk_dir(sourcedir, recursive=True, path_matcher=source_matcher(sourcedir))
    await coco.mount_each(_process_file, files.items(), sourcedir, target)


def source_matcher(sourcedir: pathlib.Path) -> _SymlinkAwareMatcher:
    """The files the index walks: the adapters' extensions, minus the built-in
    excludes, the root ``.gitignore`` and ``index_exclude``. The daemon asks it
    too."""
    return _SymlinkAwareMatcher(
        sourcedir,
        included_patterns=_INCLUDED_PATTERNS,
        excluded_patterns=_EXCLUDED_PATTERNS + _configured_excludes(pathlib.Path(sourcedir)),
    )


def make_app(sourcedir: pathlib.Path, codoc_dir: pathlib.Path, app_name: str = "CodocIndex") -> coco.App:
    """A cocoindex App that indexes ``sourcedir`` into ``codoc_dir``."""
    global _codoc_dir
    _codoc_dir = codoc_dir
    return coco.App(
        coco.AppConfig(name=app_name),
        app_main,
        sourcedir=pathlib.Path(sourcedir).resolve(),
    )

