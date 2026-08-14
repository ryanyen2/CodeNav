"""What git says happened in a commit — the ground truth for attribution.

This module exists to be **independent of codoc's own signals**. Loop A detects
relocations by comparing ``tokens_hash`` (normalized token stream) and
``types_hash`` (AST shape); if the ground truth were built from those same
hashes the evaluation would be measuring nothing but its own consistency.

Git's rename and copy detection is a genuinely different mechanism: it scores
line-level similarity between blobs. It can disagree with codoc, and that is the
point — a disagreement is a finding rather than an artefact.

Granularity is honest about its limits. Git tracks files, not symbols, so what
this module reports is file-level: which paths were added, deleted, modified, or
renamed. Symbol-level movement *within* a file is not visible here and is left
to the resolvability check, which needs no ground truth at all.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Mirrors codoc.pipelines.indexing.cocoindex_app._INCLUDED_PATTERNS. A commit
# that only touches README or CI config produces no chunk-level change, and
# counting it as a replay step would dilute every rate we report.
INDEXED_SUFFIXES = (".py", ".ts", ".tsx", ".mts", ".cts")


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()[:300]}")
    return out.stdout


def is_indexed(path: str, subdir: str = "") -> bool:
    if subdir and not path.startswith(subdir.rstrip("/") + "/"):
        return False
    return path.endswith(INDEXED_SUFFIXES)


@dataclass
class CommitFacts:
    """File-level truth about one commit, restricted to indexed files."""

    sha: str
    parent: str
    subject: str = ""
    added: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)
    modified: set[str] = field(default_factory=set)
    renamed: dict[str, str] = field(default_factory=dict)   # old path → new path
    copied: dict[str, str] = field(default_factory=dict)    # source → new path

    @property
    def touched(self) -> set[str]:
        """Every indexed path involved, on either side of a rename.

        This is what `compute_changeset(file_scope=...)` needs: leaving the OLD
        path out of the scope means the index never learns the old chunks are
        gone, and the move looks like an unrelated add.
        """
        return (
            self.added | self.deleted | self.modified
            | set(self.renamed) | set(self.renamed.values())
            | set(self.copied.values())
        )

    @property
    def is_empty(self) -> bool:
        return not self.touched


def commits_between(repo: Path, base: str, head: str = "HEAD") -> list[str]:
    """Shas from ``base`` (exclusive) to ``head`` (inclusive), oldest first.

    First-parent only. Replaying both sides of a merge would apply the same
    change twice and score the second application against a tree that already
    contains it.
    """
    out = _git(repo, "rev-list", "--first-parent", "--reverse", f"{base}..{head}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def commit_facts(repo: Path, sha: str, *, subdir: str = "") -> CommitFacts:
    """File-level change for ``sha`` against its first parent.

    Rename and copy detection is asked for explicitly and generously:
    ``--find-renames`` with a 50% similarity floor catches a moved function that
    was also lightly edited, which is exactly the case codoc claims to handle
    and the one a stricter threshold would silently drop.
    """
    parent = _git(repo, "rev-parse", f"{sha}^").strip()
    subject = _git(repo, "log", "-1", "--format=%s", sha).strip()
    raw = _git(
        repo, "diff", "--name-status", "--find-renames=50%", "--find-copies=50%",
        "-z", parent, sha,
    )
    facts = CommitFacts(sha=sha, parent=parent, subject=subject)

    # -z output is NUL-separated, and R/C records span THREE fields
    # (status, old, new) while the rest span two. Splitting on newlines instead
    # silently corrupts any path containing one.
    fields = [f for f in raw.split("\0") if f != ""]
    i = 0
    while i < len(fields):
        status = fields[i]
        code = status[0]
        if code in ("R", "C"):
            old, new = fields[i + 1], fields[i + 2]
            i += 3
            old_in, new_in = is_indexed(old, subdir), is_indexed(new, subdir)
            if not (old_in or new_in):
                continue
            # A rename that crosses the indexed boundary is not a rename to us:
            # from codoc's side the code genuinely appeared or genuinely left.
            if old_in and new_in:
                (facts.renamed if code == "R" else facts.copied)[old] = new
            elif new_in:
                facts.added.add(new)
            else:
                facts.deleted.add(old)
            continue
        path = fields[i + 1]
        i += 2
        if not is_indexed(path, subdir):
            continue
        if code == "A":
            facts.added.add(path)
        elif code == "D":
            facts.deleted.add(path)
        else:  # M, T (typechange) — content the index must re-read
            facts.modified.add(path)
    return facts


def checkout(repo: Path, sha: str) -> None:
    """Hard checkout of ``sha``, discarding anything the previous pass left.

    ``codoc init`` writes ``.codoc/`` into the tree, so a plain checkout can
    fail on untracked-file conflicts partway through a long replay. Two paths
    must survive the clean: ``.codoc`` is the state under test, and
    ``.codoc.baseline`` is the post-bootstrap snapshot that lets a run restart
    without paying for another ``codoc init``. Both are untracked, so a bare
    ``git clean -fdx`` deletes them.
    """
    _git(repo, "checkout", "--force", "--detach", sha)
    _git(repo, "clean", "-fdx", "-e", ".codoc", "-e", ".codoc.baseline")
