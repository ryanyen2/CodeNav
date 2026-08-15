"""Repositories for the replay evaluation, split before anything is run.

The split is the whole point. Shaking the harness out on the same repositories
we report on would make the reported numbers a product of tuning: fix bugs until
the figures look good, then publish the figures. Naming the two sets up front,
in code, and never running the reporting set until the system is frozen is what
makes "we fixed the bugs first" a protocol rather than an excuse.

DEV is beaten on freely and never reported. REPORTING is not to be run until
Phase 1 is finished and the code is frozen. If a reporting repository then
exposes a new bug, that is a result and gets written down — fixing it and
re-running means starting a new Phase 2 against a fresh held-out set.
"""
from __future__ import annotations

from dataclasses import dataclass

from evals.corpora import Corpus


@dataclass(frozen=True)
class ReplayCorpus:
    corpus: Corpus
    depth: int = 300      # commits to replay back from HEAD
    note: str = ""

    @property
    def name(self) -> str:
        return self.corpus.name

    @property
    def scope(self) -> str:
        """The path prefix the ground truth filters on.

        Empty, i.e. the whole repository — deliberately NOT ``corpus.subdir``.
        That field narrows the N1/N2 comparison so two arms describe the same
        subtree, which is the right thing there. Here it would be actively
        wrong: ``codoc init`` indexes the whole repository, so a ground truth
        scoped to one package would leave every change outside it out of
        ``file_scope``. The index would never learn those chunks changed, and
        the harness would score its own blind spot as staleness in the system.
        """
        return ""


DEV: tuple[ReplayCorpus, ...] = (
    ReplayCorpus(
        Corpus(name="requests", source="https://github.com/psf/requests",
               subdir="src/requests"),
        depth=200,
        note="Small and stable — the first thing to get working end to end.",
    ),
    ReplayCorpus(
        Corpus(name="flask", source="https://github.com/pallets/flask",
               subdir="src/flask"),
        depth=250,
        note="Has real package-level file moves; exercises the rename path.",
    ),
    ReplayCorpus(
        Corpus(name="altair", source="https://github.com/vega/altair",
               subdir="altair"),
        depth=250,
        note="Larger and more layered; where per-commit cost will show up.",
    ),
    ReplayCorpus(
        Corpus(name="httpx", source="https://github.com/encode/httpx",
               subdir="httpx"),
        depth=250,
        note="Heavily refactored early history — renames and moves.",
    ),
    ReplayCorpus(
        Corpus(name="rich", source="https://github.com/Textualize/rich",
               subdir="rich"),
        depth=250,
        note="High commit volume, many small edits — the in-place-edit case.",
    ),
)

# DO NOT RUN until Phase 1 is complete and the system is frozen.
REPORTING: tuple[ReplayCorpus, ...] = (
    ReplayCorpus(
        Corpus(name="click", source="https://github.com/pallets/click",
               subdir="src/click"), depth=250),
    ReplayCorpus(
        Corpus(name="pydantic", source="https://github.com/pydantic/pydantic",
               subdir="pydantic"), depth=250),
    ReplayCorpus(
        Corpus(name="starlette", source="https://github.com/encode/starlette",
               subdir="starlette"), depth=250),
    ReplayCorpus(
        Corpus(name="typer", source="https://github.com/fastapi/typer",
               subdir="typer"), depth=250),
    ReplayCorpus(
        Corpus(name="pyright-ts", source="https://github.com/microsoft/pyright",
               subdir="packages/pyright-internal/src"), depth=200,
        note="The TypeScript arm. File-level ground truth only — no PyRef."),
)


# Candidates for the drift experiment, which needs the opposite of a stable
# repository: the frozen document only decays where code actually moves, and on
# a quiet project all three arms score the same (flask showed nothing). Screened
# on the `sym` column — symbol-level movement in files codoc would index — not
# on file renames, which counted a directory of test fixtures as churn once and
# cost a frozen run its relocation arm.
CANDIDATES: tuple[ReplayCorpus, ...] = (
    ReplayCorpus(Corpus(name="litestar", source="https://github.com/litestar-org/litestar",
                        subdir="litestar"), depth=250,
                 note="Renamed from starlite and restructured repeatedly."),
    ReplayCorpus(Corpus(name="hypothesis", source="https://github.com/HypothesisWorks/hypothesis",
                        subdir="hypothesis-python/src"), depth=250),
    ReplayCorpus(Corpus(name="pytest", source="https://github.com/pytest-dev/pytest",
                        subdir="src/_pytest"), depth=250),
    ReplayCorpus(Corpus(name="mypy", source="https://github.com/python/mypy",
                        subdir="mypy"), depth=250),
)


def by_name(name: str) -> ReplayCorpus:
    for c in DEV + REPORTING + CANDIDATES:
        if c.name == name:
            return c
    raise KeyError(
        f"unknown replay corpus {name!r} "
        f"(dev: {[c.name for c in DEV]}; reporting: {[c.name for c in REPORTING]})"
    )


def is_reporting(name: str) -> bool:
    return any(c.name == name for c in REPORTING)
