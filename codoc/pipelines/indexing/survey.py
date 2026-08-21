"""What the indexing walk never saw — the bound on every claim codoc makes.

`codoc status` already checks that every indexed chunk is attributed to a feature.
That is a coverage report over *the index*, and it reads as 100% on a repo whose Go
half codoc cannot parse, whose generated schema module is over the size cap, or
whose monorepo packages are directory symlinks the walk refuses to follow. A tree
that describes a third of a codebase and calls itself in sync is the one failure a
faithful view of the code must not have.

So the survey walks the repo the way the indexer walks it — the same matcher, the
same patterns, the same size cap, imported from the indexer rather than restated,
because a survey that disagrees with the walk is worse than no survey — and reports
what fell outside.

It reports only codoc's OWN limits. A file under `node_modules/`, `dist/`, or a path
the repo's `.gitignore` names is excluded on purpose and by somebody's decision;
saying so on every `codoc status` would bury the facts that are about codoc.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

from cocoindex.resources.file import PatternFilePathMatcher

from codoc.lang import detect_language
from codoc.pipelines.indexing.cocoindex_app import (
    _EXCLUDED_PATTERNS,
    _INCLUDED_PATTERNS,
    _MAX_FILE_BYTES,
    _gitignore_excludes,
)

# Extensions that mean "somebody's source code lives here and codoc cannot read
# it". Data, docs and lock files are not a gap in codoc's view of the code, so
# counting them would only make the real number harder to see.
_SOURCE_EXTS = {
    ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
    ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".clj", ".ex", ".exs",
    ".rb", ".php", ".pl", ".lua", ".jl", ".dart", ".swift", ".m", ".mm",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cs", ".fs", ".vb",
    ".sh", ".bash", ".zsh", ".ps1", ".sql", ".hs", ".ml", ".erl", ".zig", ".nim",
}


@dataclass
class RepoSurvey:
    """What the walk indexed, and what it could not — each with its reason."""

    indexed: int = 0
    #: extension → file count, for source codoc has no adapter for
    unreadable: dict[str, int] = field(default_factory=dict)
    #: (repo-relative path, size in bytes) for parseable files over the cap
    oversize: list[tuple[str, int]] = field(default_factory=list)
    #: repo-relative directories the walk refused to follow (symlinks)
    symlinked_dirs: list[str] = field(default_factory=list)

    @property
    def unseen(self) -> int:
        """Files that hold code no feature can possibly cover."""
        return sum(self.unreadable.values()) + len(self.oversize)


def survey_repo(root: str | pathlib.Path, *, max_entries: int = 200_000) -> RepoSurvey:
    """Walk *root* as the indexer does and report what fell outside the index.

    ``max_entries`` is a runaway guard, not a policy: the survey is advisory and
    must never be the thing that makes `codoc status` slow on a pathological tree.
    """
    root = pathlib.Path(root).resolve()
    # The PATTERNS only, without the walk's symlink guard on top of them, because
    # the survey has to tell the two apart: a directory the patterns exclude was
    # excluded by somebody's decision, while one the patterns allow and the guard
    # refuses is codoc declining to follow a link, and only the second is news.
    matcher = PatternFilePathMatcher(
        included_patterns=_INCLUDED_PATTERNS,
        excluded_patterns=_EXCLUDED_PATTERNS + _gitignore_excludes(root),
    )
    survey = RepoSurvey()
    visited = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if visited >= max_entries:
                return survey
            visited += 1
            rel = entry.relative_to(root).as_posix()
            if entry.is_dir():
                if not matcher.is_dir_included(pathlib.Path(rel)):
                    continue  # excluded on purpose — see the module docstring
                if entry.is_symlink():
                    # Refused for loop protection, not because anyone excluded it —
                    # a monorepo that links its packages would lose them silently.
                    if _holds_source(entry):
                        survey.symlinked_dirs.append(rel)
                    continue
                stack.append(entry)
                continue
            if detect_language(rel) is None:
                suffix = entry.suffix.lower()
                if suffix in _SOURCE_EXTS:
                    survey.unreadable[suffix] = survey.unreadable.get(suffix, 0) + 1
                continue
            if not matcher.is_file_included(pathlib.Path(rel)):
                continue  # excluded on purpose — see the module docstring
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            if size > _MAX_FILE_BYTES:
                survey.oversize.append((rel, size))
            else:
                survey.indexed += 1
    survey.oversize.sort(key=lambda pair: -pair[1])
    survey.symlinked_dirs.sort()
    return survey


def _holds_source(directory: pathlib.Path) -> bool:
    """True if *directory* directly contains a file some adapter could read.

    Shallow on purpose: a symlink may point anywhere, including at a tree large
    enough that walking it is the cost the loop guard exists to avoid.
    """
    try:
        for entry in directory.iterdir():
            if entry.is_file() and detect_language(entry.name):
                return True
    except OSError:
        return False
    return False


def _with_overflow(names: list[str], max_names: int) -> str:
    shown = ", ".join(names[:max_names])
    rest = len(names) - max_names
    return f"{shown}, +{rest} more" if rest > 0 else shown


def render_survey(survey: RepoSurvey, *, max_names: int = 3) -> list[str]:
    """Advisory lines for `codoc status` — empty when codoc saw the whole repo.

    One line per KIND of blindness, because each is answered differently: another
    language is a standing bound on what the tree can ever say, a file over the cap
    is a threshold somebody can revisit, and an unfollowed symlink is usually a
    monorepo layout codoc should be pointed at directly.
    """
    lines: list[str] = []
    if survey.unreadable:
        ranked = sorted(survey.unreadable.items(), key=lambda kv: (-kv[1], kv[0]))
        counted = _with_overflow([f"{n} {ext}" for ext, n in ranked], max_names)
        lines.append(
            f"  ⚠ outside codoc's view: {counted} — it reads Python and TypeScript, "
            f"so no feature covers those files"
        )
    if survey.oversize:
        names = _with_overflow([path for path, _ in survey.oversize], max_names)
        lines.append(
            f"  ⚠ {len(survey.oversize)} file(s) over the "
            f"{_MAX_FILE_BYTES / 1_000_000:.1f} MB index cap, unindexed: {names}"
        )
    if survey.symlinked_dirs:
        names = _with_overflow(survey.symlinked_dirs, max_names)
        lines.append(
            f"  ⚠ {len(survey.symlinked_dirs)} symlinked directory(ies) not followed "
            f"(loop protection): {names}"
        )
    return lines
