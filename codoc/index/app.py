"""What codoc reads as code: the cocoindex app that indexes a source tree,
and the one reader of code on disk that a hand-off's evidence is read by.

The app (:func:`make_app`, run by :mod:`codoc.index.runner`) walks a source
directory, extracts AST chunks with the ``codoc.index.lang`` adapters, hashes
each one (``tree_walk``), and writes the rows to the LanceDB table in
``.codoc/lancedb``. A re-run re-processes only files whose content changed,
and a killed run resumes: cocoindex keeps per-file memo state in
``.codoc/cocoindex.db``.

The reader answers for a file before the index has caught up with an edit,
by the same rules: ``canonical_file`` names it as the walk names it,
``why_not_indexed`` says whether the walk reads it (the daemon asks this of
every changed file), and ``code_on_disk`` hashes its chunks as the app does
(``file_chunks``).
"""
from __future__ import annotations

import codecs
import json
import os
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

_EXCLUDED_BY_CONFIG = "index_exclude in .codoc/config.json, or the root .gitignore, covers it"


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


# Byte-order marks, longest first: a file that starts with one is decoded in its
# encoding, any other as UTF-8.
_BOMS = ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
         (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"), (codecs.BOM_UTF8, "utf-8-sig"))


def file_chunks(file_str: str, raw: bytes) -> list[tuple]:
    """The chunks the index keeps for one file's bytes, each with its
    ``tree_walk`` hashes: the one way codoc reads a source file, for the index
    and for an agent's evidence alike. The bytes are decoded by their
    byte-order mark, else as UTF-8, with undecodable bytes replaced and line
    endings kept as they are. The symbol is a row's key: its id is
    ``generate_id(symbol)``, and the change set and the bindings are keyed by
    it. A class can define a name twice (a property's getter and setter, a
    redefinition), and a second row with the same id is a "target state already
    declared" error that drops the whole file, so the first chunk per symbol is
    kept."""
    encoding = next((enc for bom, enc in _BOMS if raw.startswith(bom)), "utf-8")
    source = raw.decode(encoding, "replace")
    adapter = get_adapter(detect_language(file_str))
    seen: set[str] = set()
    out = []
    for c in adapter.extract_chunks(file_str, source):
        if c.symbol not in seen:
            seen.add(c.symbol)
            out.append((c, tree_walk(c.source, adapter)))
    return out


def canonical_file(sourcedir: str | pathlib.Path, file: str) -> str | None:
    """``file`` as the index's walk names it: repo-relative POSIX. A relative
    path is read against the root and an absolute one as given, ``.`` and
    ``..`` folded lexically; the root is the nearest ancestor that is the
    repository, however it was reached (a symlinked parent directory, macOS
    ``/var`` for ``/private/var``). The part below it is kept as written, so a
    linked file keeps its link's name, as the walk names it.

    When no ancestor is the repository (a link from outside into a
    subdirectory, ``/tmp/src -> repo/src``), the path is named by its real
    path below the root, if the walk keys the file by that name: a real path
    through no excluded directory. None otherwise."""
    root = os.path.realpath(sourcedir)
    path = pathlib.PurePath(os.path.normpath(os.path.join(root, file)))
    for base in path.parents:
        if os.path.realpath(base) == root:
            return path.relative_to(base).as_posix()
    try:
        rel = pathlib.PurePath(os.path.realpath(path)).relative_to(root).as_posix()
    except ValueError:
        return None
    return rel if why_not_indexed(root, rel) is None else None


def why_not_indexed(sourcedir: str | pathlib.Path, rel: str, matcher: _SourceMatcher | None = None) -> str | None:
    """Why the index does not read the repo-relative POSIX file ``rel``, as a
    clause that follows its name; None when it reads it. The walk asks the
    same of every directory and file it meets (``_SourceMatcher``); this asks
    it of one path, and adds what a walk has by construction: each part of
    the path is spelled as its directory lists it, so on a case-insensitive
    filesystem ``SRC/rate.py`` is not ``src/rate.py``. A path nothing is at
    is judged by its name, so a file the index read counts after it is
    removed. ``matcher`` is ``source_matcher(sourcedir)``, for a caller that
    asks about many files."""
    root = pathlib.Path(sourcedir)
    matcher = matcher or source_matcher(root)
    path = pathlib.PurePosixPath(rel)
    here = root
    for depth, name in enumerate(path.parts, 1):
        if os.path.lexists(here / name) and name not in os.listdir(here):
            return "is not spelled as its directory lists it (a path names a file case for case)"
        here = here / name
        if depth < len(path.parts) and (why := matcher.dir_refusal(pathlib.PurePosixPath(*path.parts[:depth]))):
            return why
    return matcher.file_refusal(path)


def code_on_disk(sourcedir: str | pathlib.Path, files: set[str]) -> dict[str, tuple[str, str]]:
    """``(fingerprint, types_hash)`` of every chunk ``files`` hold on disk now,
    read and hashed as the index reads and hashes them (``file_chunks``): what
    a binding may cite before the index has caught up with an edit. ``files``
    are canonical (``canonical_file``); a file the index does not read
    (``why_not_indexed``) holds none."""
    root = pathlib.Path(sourcedir).resolve()
    matcher = source_matcher(root)
    out: dict[str, tuple[str, str]] = {}
    for rel in files:
        if why_not_indexed(root, rel, matcher) is not None:
            continue
        try:
            raw = (root / rel).read_bytes()
        except OSError:
            continue
        out.update({c.symbol: (w.fingerprint, w.types_hash) for c, w in file_chunks(rel, raw)})
    return out


@coco.fn(memo=True)
async def _process_file(
    file: FileLike,
    sourcedir: pathlib.Path,
    target: lancedb.TableTarget,
) -> None:
    # The walk yields only files the index reads (`_SourceMatcher`).
    file_str = _repo_relative(pathlib.Path(file.file_path.path), sourcedir)
    lang = detect_language(file_str)
    for c, walk_result in file_chunks(file_str, await file.read()):
        target.declare_row(
            row=CodeChunk(
                id=await generate_id(c.symbol), file=file_str, symbol=c.symbol, language=lang, source=c.source,
                fingerprint=walk_result.fingerprint, types_hash=walk_result.types_hash,
                start_byte=c.start_byte, end_byte=c.end_byte,
            )
        )


class _SourceMatcher(PatternFilePathMatcher):
    """Which files the index reads, as the walk asks it: a directory is
    entered when it is not symlinked and the patterns keep it; a file is read
    when it is of a language codoc parses, the patterns keep it, and it is at
    most ``_MAX_FILE_BYTES``. Each refusal says why, for ``why_not_indexed``.

    A directory symlink to itself or an ancestor (a monorepo package link,
    ``docs/latest -> .``) would make the recursive walk descend without bound,
    and one pointing outside the repository would take the walk out of it. The
    index follows the real tree only."""

    def __init__(self, sourcedir: pathlib.Path, *, included_patterns, excluded_patterns):
        super().__init__(included_patterns=included_patterns, excluded_patterns=excluded_patterns)
        self._sourcedir = pathlib.Path(sourcedir).resolve()

    def dir_refusal(self, path: pathlib.PurePath) -> str | None:
        """Why the walk does not enter the directory ``path``, as a clause
        following the name of a file under it; None when it does."""
        try:
            linked = (self._sourcedir / path).is_symlink()
        except OSError:
            return f"is under {path}/, a directory the index cannot read"
        if linked:
            return f"is under {path}/, a symlinked directory the index does not enter; name it by its real directory"
        if not super().is_dir_included(path):
            return f"is under {path}/, a directory codoc does not index ({_EXCLUDED_BY_CONFIG})"
        return None

    def file_refusal(self, path: pathlib.PurePath) -> str | None:
        """Why the walk does not read the file ``path``, as a clause following
        its name; None when it does. A path nothing is at is judged by its name."""
        if detect_language(str(path)) is None:
            return "is not in a language codoc indexes"
        if not super().is_file_included(path):
            return f"is a file codoc does not index ({_EXCLUDED_BY_CONFIG})"
        try:
            if (self._sourcedir / path).stat().st_size > _MAX_FILE_BYTES:
                return f"is over {_MAX_FILE_BYTES:,} bytes, larger than codoc indexes"
        except OSError:
            pass
        return None

    def is_dir_included(self, path) -> bool:  # noqa: ANN001 (matches base signature)
        return self.dir_refusal(path) is None

    def is_file_included(self, path) -> bool:  # noqa: ANN001 (matches base signature)
        return self.file_refusal(path) is None


@coco.fn
async def app_main(sourcedir: pathlib.Path) -> None:
    target = await lancedb.mount_table_target(
        LANCE_DB,
        table_name=LANCE_TABLE_NAME,
        table_schema=await lancedb.TableSchema.from_class(CodeChunk, primary_key=["id"]),
    )
    files = localfs.walk_dir(sourcedir, recursive=True, path_matcher=source_matcher(sourcedir))
    await coco.mount_each(_process_file, files.items(), sourcedir, target)


def source_matcher(sourcedir: pathlib.Path) -> _SourceMatcher:
    """The files the index walks: the adapters' extensions, minus the built-in
    excludes, the root ``.gitignore`` and ``index_exclude``."""
    return _SourceMatcher(
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

