"""Localization tasks built from real merged changes.

A task is a change request and the set of files that satisfying it actually
touched. Both come from a commit the project's own developers wrote, so the
ground truth is not ours to argue about, and there is nothing to hand-label.

The window matters more than the wording. Tasks are drawn from commits AFTER the
point where every artifact under test was last updated, so no arm has seen the
answer. Drawing them from the replayed range instead would hand the maintained
tree the very change it is being asked to locate.

Two things are deliberately withheld from the request text. Paths and symbol
names are stripped, because a request naming the file it edits measures nothing
but string search. And the request stays at the granularity of a one-line
change description: a benchmark that varies prompt detail found file-level F1
moving from 0.20 to 0.81 on that axis alone (arXiv 2603.26137), which would
swamp any effect an artifact could have.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from evals.replay.gitfacts import _git, commit_facts, commits_between

# Conventional-commit prefixes carry no information about where the code lives.
_PREFIX = re.compile(r"^(feat|fix|chore|docs|test|refactor|ci|build|perf|style)"
                     r"(\([^)]*\))?!?:\s*", re.I)
_PR_REF = re.compile(r"\s*\(#\d+\)\s*$")
_BACKTICK_PATH = re.compile(r"`[^`]*[/.][^`]*`")


@dataclass
class Task:
    sha: str
    request: str
    gold_files: list[str]
    n_files: int

    def as_json(self) -> str:
        return json.dumps(asdict(self))


def _clean(subject: str) -> str:
    """The commit subject as a change request, with location hints removed."""
    s = _PR_REF.sub("", _PREFIX.sub("", subject)).strip()
    # A backticked token containing a dot or slash is a path or a dotted symbol;
    # leaving it in turns localization into grep.
    s = _BACKTICK_PATH.sub("the affected code", s)
    return s


def _leaks_path(request: str, gold: list[str]) -> bool:
    """Whether the request still names one of the answers."""
    low = request.lower()
    for path in gold:
        stem = Path(path).stem.lower()
        if len(stem) > 3 and stem in low:
            return True
    return False


def build_tasks(
    repo: Path, *, start: str, end: str, subdir: str = "",
    max_files: int = 4, min_subject: int = 25,
) -> list[Task]:
    """Tasks from commits in ``start``..``end``.

    Commits touching more than ``max_files`` indexed files are skipped: a
    sweeping change has a diffuse answer, and scoring a localization attempt
    against thirty files rewards breadth rather than aim. Commits whose subject
    is too short to be a request, or that still name a file they touch, are
    dropped rather than rewritten.
    """
    tasks: list[Task] = []
    for sha in commits_between(repo, start, end):
        facts = commit_facts(repo, sha, subdir=subdir)
        gold = sorted(facts.modified | facts.added | set(facts.renamed.values()))
        if not gold or len(gold) > max_files:
            continue
        request = _clean(facts.subject)
        if len(request) < min_subject or _leaks_path(request, gold):
            continue
        # A merge subject describes a branch operation, not a change anyone could
        # be asked to make, and its file list is whatever the merge happened to
        # carry. Nothing to locate.
        if request.lower().startswith(("merge ", "revert ", "bump ", "release ")):
            continue
        tasks.append(Task(sha=sha, request=request, gold_files=gold,
                          n_files=len(gold)))
    return tasks


def write_tasks(tasks: list[Task], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for t in tasks:
            fh.write(t.as_json() + "\n")


def read_tasks(path: Path) -> list[Task]:
    return [Task(**json.loads(line)) for line in path.open() if line.strip()]
