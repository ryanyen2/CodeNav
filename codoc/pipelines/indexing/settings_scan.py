"""Which settings files the walk indexes: the ones this repo's own code reads.

A glob is the wrong rule here. `**/*.toml` in a real repo is `pyproject.toml`, a
tool's config, a fixture, a vendored schema and — somewhere among them — the one
file a person moved a decision into. Indexing all of them spends the tree's first
nodes on the build, and indexing none of them is the silence this module exists to
end (see `codoc/settings_files.py`).

The rule that separates them is already written in the codebase: **a settings file
holds a decision if the code reads it.** `rules.toml` earns a place in the index
because some module opens it, and the feature that owns that module is where its
sections belong; `pyproject.toml` earns none, because nothing in the repo reads it —
the packaging tool does. So this scan looks for the evidence rather than guessing
from the name, and `settings_files.NOT_INTENT` stays a backstop for the manifests a
repo does sometimes read itself.

**The evidence is a mention of the file's name in source text.** A repo names the
file it opens: `open("rules.toml")`, `Path(__file__).parent / "rules.toml"`,
`CONFIG = "config/rules.toml"`. Matching the basename anywhere in a source file
catches all three without following an import graph or resolving a path, and it
costs one read of each source file with no parse. A name assembled at runtime
(`f"{env}.toml"`) is not found, and that is a bound worth stating plainly: the
answer is then the same as before this module existed, not a wrong answer.

A mention in a comment or a docstring counts. It is still somebody saying this file
matters to this code, which is the question being asked.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

from codoc.lang import detect_language
from codoc.pipelines.indexing.gate import too_large_to_read
from codoc.settings_files import FORMATS, NOT_INTENT, available_formats

#: walk patterns for every settings extension, readable in this process or not — the
#: unreadable ones are reported rather than dropped (see `SettingsScan.unreadable`).
CANDIDATE_PATTERNS: list[str] = [f"**/*{ext}" for ext in sorted(FORMATS)]


@dataclass
class SettingsScan:
    """The settings files a repo reads, and the ones it reads that codoc cannot."""

    #: repo-relative paths to index, sorted
    read_by_code: list[str] = field(default_factory=list)
    #: read by the code, but this process has no parser for the format (YAML
    #: without PyYAML). Reported, because a file skipped for a missing dependency
    #: and a file nobody reads are different facts about the same silence.
    unreadable: list[str] = field(default_factory=list)
    #: candidates no source file mentions — a count, not a list, because it is
    #: usually most of them and none of them is news
    unreferenced: int = 0


def scan(root: str | pathlib.Path, matcher, *, max_entries: int = 200_000) -> SettingsScan:
    """Walk *root* and report which settings files its code reads.

    *matcher* decides what the walk may enter and see; pass the indexer's own, so a
    file under `node_modules/` or a `.gitignore`d directory is as invisible here as
    it is there. It must accept every extension the walk needs — code files and
    `CANDIDATE_PATTERNS`.

    ``max_entries`` is the same runaway guard the survey carries: this runs on the
    way into an index update, and it must never be the reason a pathological tree
    hangs.
    """
    root = pathlib.Path(root).resolve()
    candidates: list[str] = []
    sources: list[pathlib.Path] = []
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
                stack.clear()
                break
            visited += 1
            rel = entry.relative_to(root).as_posix()
            if entry.is_dir():
                if entry.is_symlink() or not matcher.is_dir_included(pathlib.Path(rel)):
                    continue
                stack.append(entry)
                continue
            if not matcher.is_file_included(pathlib.Path(rel)):
                continue
            if detect_language(rel) is not None:
                sources.append(entry)
            elif _is_candidate(rel):
                candidates.append(rel)

    if not candidates:
        # The common case, and the cheap one: nothing to look for, so no source
        # file is read at all.
        return SettingsScan()

    wanted = {pathlib.PurePosixPath(rel).name for rel in candidates}
    mentioned = _mentioned_in(sources, wanted)
    scan_result = SettingsScan()
    for rel in sorted(candidates):
        if pathlib.PurePosixPath(rel).name not in mentioned:
            scan_result.unreferenced += 1
        elif pathlib.PurePosixPath(rel).suffix.lower().lstrip(".") in _unreadable_suffixes():
            scan_result.unreadable.append(rel)
        else:
            scan_result.read_by_code.append(rel)
    return scan_result


def _is_candidate(rel: str) -> bool:
    """A settings file that could hold an authored decision, readable or not.

    Format availability is deliberately NOT asked here: a YAML file in a process
    without PyYAML is a candidate that gets reported rather than one that never
    existed. What is asked is `NOT_INTENT` — a lock file is machinery even in the
    repo that reads it.
    """
    name = pathlib.PurePosixPath(rel)
    return name.suffix.lower() in FORMATS and name.name.lower() not in NOT_INTENT


def _unreadable_suffixes() -> set[str]:
    """Extensions (no dot) whose format this process has no parser for."""
    return {ext.lstrip(".") for ext, fmt in FORMATS.items()
            if fmt not in available_formats()}


def _mentioned_in(sources: list[pathlib.Path], wanted: set[str]) -> set[str]:
    """The names of *wanted* that appear in the text of any source file.

    One read per source file and no parse. It stops as soon as every name has been
    found, which is the usual outcome long before the walk's largest files: a repo
    reads its settings file near the top of the module that owns it.
    """
    found: set[str] = set()
    for path in sources:
        if len(found) == len(wanted):
            break
        try:
            if too_large_to_read(path.stat().st_size):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.update(name for name in wanted - found if name in text)
    return found
