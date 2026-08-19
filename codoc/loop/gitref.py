"""Git anchors for realize directives — "what did the code look like before this?".

A directive is the one moment codoc knows a code change is ABOUT to happen. Recording
the commit the repo sat on then is what later lets the IDE open a real before/after
diff for "show me what the agent did", instead of a list of file names the reader has
to reconstruct the change from themselves.

Deliberately tiny and deliberately advisory. Everything here fails soft to ``""`` —
no git binary, not a repo, a detached worktree, a hung invocation, a repo with no
commits yet: all the same to us, and all mean "no anchor", never an error. A directive
whose anchor is empty still realizes; it just cannot offer the diff.

Why a SHA and not the diff itself: the diff is derivable from the SHA at the moment
someone asks for it, and storing it would freeze a large blob into a control file that
is rewritten on every loop pass. The SHA is 40 bytes and stays true.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_TIMEOUT_S = 5


def _git(root_dir: str | Path, *args: str) -> str:
    """One git invocation under ``root_dir``, or ``""`` for any failure at all.

    The catch is deliberately as broad as the docstring — normally a smell, here the
    contract. This runs on the hand-off path, immediately before Loop B writes the
    realize trigger, and the one thing that must not happen is an optional provenance
    lookup preventing queued work from reaching the agent. Enumerating exception types
    would mean guessing what a subprocess layer can raise: a wrapped or instrumented
    ``Popen`` raises ``AttributeError``, not ``OSError``, and a decoding failure raises
    neither. There is no failure here worth more than an absent anchor.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root_dir), *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S, errors="replace",
        )
    except Exception:  # noqa: BLE001 — advisory read; see the docstring
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def head_sha(root_dir: str | Path) -> str:
    """The full commit sha at ``HEAD``, or ``""`` when there isn't one.

    An empty result covers the genuinely different cases (no git, not a repo, a repo
    with zero commits) identically on purpose: every one of them means the same thing
    to the caller — there is no "before" to diff against.
    """
    sha = _git(root_dir, "rev-parse", "HEAD")
    # rev-parse on an unborn branch prints the ref name back rather than failing on
    # some git versions; a sha is 40 lowercase hex and nothing else.
    if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
        return sha
    return ""


def changed_files(root_dir: str | Path, base_sha: str) -> list[str]:
    """Repo-relative paths that differ between ``base_sha`` and the working tree.

    Combines committed changes since the anchor with what is still uncommitted, because
    a realization run may or may not have been committed by the time anyone looks — and
    to a reader asking "what did the agent change?" that distinction is invisible and
    irrelevant. Paths are deduped and sorted so the surface is stable between calls.
    """
    if not base_sha:
        return []
    out = _git(root_dir, "diff", "--name-only", base_sha, "--")
    if not out:
        # An empty diff and a failed diff are indistinguishable here, and treating a
        # failure as "nothing changed" is the safe reading: the surface offers no diff
        # rather than an empty one that claims the agent did nothing.
        return []
    return sorted({ln.strip() for ln in out.splitlines() if ln.strip()})
