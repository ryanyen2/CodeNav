"""Evaluation corpora — repositories with their commit history intact.

The vendored copies under ``test/`` cannot be used here. They ship without
``.git``, and this evaluation's N2 ground truth is generated *from* commit
bodies: a repository with no history offers no reason for any of its code, so
both arms would be scored against nothing.

Two kinds of corpus, deliberately:

* **Third-party clones** — the primary result. Their commit hygiene is whatever
  it happens to be, which is the honest condition: a method that only works on
  repositories whose authors wrote careful commit messages should be reported
  as such.
* **This repository** — a second arm, and a ceiling. Its commit bodies state
  reasons at unusual length, so it shows what the evidence channel does when the
  record is good. Reported separately, never pooled with the clones: its commits
  were written by people who knew codoc would read them, and pooling would hide
  that behind an average.

Clones are shallow but deeper than the evidence channel's scan window — a clone
shallower than :data:`codoc.loop.why._LOG_SCAN_COMMITS` would silently starve
the codoc arm and be scored as though the method had failed.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codoc.loop.why import _LOG_SCAN_COMMITS

# Comfortably past the window the codoc arm reads, so clone depth never becomes
# a hidden independent variable.
CLONE_DEPTH = _LOG_SCAN_COMMITS + 400


@dataclass(frozen=True)
class Corpus:
    name: str
    source: str          # a git URL, or a local path for the self arm
    subdir: str = ""     # the source subtree to index; "" = the whole repo
    arm: str = "third_party"   # "third_party" | "self"
    note: str = ""

    @property
    def is_local(self) -> bool:
        return not self.source.startswith(("http://", "https://", "git@"))


CORPORA: tuple[Corpus, ...] = (
    Corpus(
        name="requests",
        source="https://github.com/psf/requests",
        subdir="src/requests",
        note="Widely read, long-lived, mixed commit hygiene — the realistic case.",
    ),
    Corpus(
        name="altair",
        source="https://github.com/vega/altair",
        subdir="altair",
        note="Larger and more layered; tests whether the tree stays navigable.",
    ),
    Corpus(
        name="codenav",
        source=str(Path(__file__).resolve().parent.parent),
        subdir="codoc",
        arm="self",
        note="Ceiling arm: commit bodies state reasons at length. Reported alone.",
    ),
)


def by_name(name: str) -> Corpus:
    for c in CORPORA:
        if c.name == name:
            return c
    raise KeyError(f"unknown corpus {name!r} (have: {[c.name for c in CORPORA]})")


def materialize(corpus: Corpus, workdir: Path, *, fresh: bool = False) -> Path:
    """Clone ``corpus`` into ``workdir`` and return the checkout path.

    A local corpus is cloned rather than used in place: the evaluation runs
    ``codoc init`` and a baseline generator that both write into the tree, and
    neither belongs anywhere near the working copy they are measuring.
    """
    dest = workdir / corpus.name
    if dest.exists():
        if not fresh:
            return dest
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = f"file://{corpus.source}" if corpus.is_local else corpus.source
    subprocess.run(
        ["git", "clone", "--depth", str(CLONE_DEPTH), source, str(dest)],
        check=True, capture_output=True, text=True,
    )
    return dest


def eval_root(corpus: Corpus, checkout: Path) -> Path:
    """The subtree both arms describe. Keeping it narrower than the repo root
    holds the two artifacts to the same scope — a baseline that summarized the
    whole repository while codoc indexed one package would be compared on
    different material, not different methods."""
    return checkout / corpus.subdir if corpus.subdir else checkout


def history_depth(checkout: Path) -> int:
    out = subprocess.run(["git", "-C", str(checkout), "rev-list", "--count", "HEAD"],
                         capture_output=True, text=True)
    return int(out.stdout.strip() or 0) if out.returncode == 0 else 0
