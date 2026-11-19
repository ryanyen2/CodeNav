"""What git knows about the code: the commit and branch at HEAD, the hand-offs new commits
realize, and what recent commits said about a file.

``head`` and ``head_sha`` let an applier notice ``HEAD`` move and stamp a
``feature.realized`` with the commit it was settled at. ``handoffs_landed``
reads the hand-off ids commits name in a trailer, so a pull request merged
into the branch it names cites its hand-off by identity. ``commit_rationales``
gives a bootstrap pass the commit messages, where a repository records why a
change was made: code says what a program does, and a model asked for the
reason with only code in front of it invents one.

Everything here fails soft: no git binary, not a repository, no commits or a
hung invocation all yield ``""`` or ``[]``, never an error.
"""
from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

_GIT_TIMEOUT_S = 5


# ── HEAD ─────────────────────────────────────────────────────────────────────

def _git(root_dir: str | Path, *args: str) -> str:
    """One git invocation under ``root_dir``, or ``""`` for any failure at all.

    It catches every exception, because the daemon's batch and the settle step
    call it, and a failed git lookup must not stop either. A wrapped ``Popen``
    raises ``AttributeError``, not ``OSError``, and a decoding failure raises
    neither, so a list of exception types would miss some.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root_dir), *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S, errors="replace",
        )
    except Exception:  # noqa: BLE001 (advisory read; see the docstring)
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def head(root_dir: str | Path) -> tuple[str, str]:
    """The full commit sha at ``HEAD`` and the branch it is on, from one git
    call, so a checkout between two calls cannot pair one commit with another
    branch. The branch is ``""`` when ``HEAD`` is detached; both are ``""``
    when there is no commit (no git, not a repo, zero commits)."""
    sha, _, branch = _git(root_dir, "rev-parse", "HEAD", "--abbrev-ref", "HEAD").partition("\n")
    # rev-parse on an unborn branch prints the ref name back rather than failing on
    # some git versions; a sha is 40 lowercase hex and nothing else.
    if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
        return sha, "" if branch == "HEAD" else branch
    return "", ""


def is_tracked(root_dir: str | Path, path: str | Path) -> bool:
    """Whether git's index holds ``path``."""
    return bool(_git(root_dir, "ls-files", "--", str(path)))


def head_sha(root_dir: str | Path) -> str:
    """The commit at ``HEAD``, or ``""`` when there is none."""
    return head(root_dir)[0]


# The trailers a commit names the hand-off it realizes by, and the branch it
# realizes it on: the hub worker writes both on every commit it builds, the base
# being the branch its pull request targets, and anyone may write them by hand.
HANDOFF_TRAILER = "Codoc-Handoff"
BASE_TRAILER = "Codoc-Base"


def handoffs_landed(root_dir: str | Path, old: str, new: str, branch: str) -> frozenset[str]:
    """The hand-off op ids that the commits in ``old..new`` name in a
    ``Codoc-Handoff`` trailer, as git parses trailers, from each commit whose
    ``Codoc-Base`` trailer names ``branch``, the branch ``HEAD`` is on now. So
    a merge, a rebase pull or a checkout of the base counts the commit, and a
    checkout or pull of the pull request's own branch does not. A detached
    ``HEAD`` counts nothing."""
    if not old or not new or not branch:
        return frozenset()
    out = _git(root_dir, "log", f"--format=%x1e%(trailers:key={HANDOFF_TRAILER},key={BASE_TRAILER})", f"{old}..{new}")
    found: set[str] = set()
    for commit in out.split("\x1e"):
        pairs = [(k.strip().lower(), v.strip()) for k, _, v in (line.partition(":") for line in commit.splitlines())]
        if (BASE_TRAILER.lower(), branch) in pairs:
            found.update(v for k, v in pairs if k == HANDOFF_TRAILER.lower() and v)
    return frozenset(found)


# ── commit messages ──────────────────────────────────────────────────────────

# Rationale from 600 commits ago is not why the code looks as it does today,
# so the scan stops there.
_LOG_SCAN_COMMITS = 600     # how far back one repo-wide scan reads
_LOG_MAX_CHARS = 4_000_000  # hard stop on a pathological log payload
_LOG_TTL_S = 120.0          # a pass every few seconds must not re-shell out
_PER_FILE_COMMITS = 3       # per file: the newest few explain the shape
_MAX_COMMITS = 8            # across one call
# Character counts written for Latin prose; the caller's ``rescale`` ports them
# to the script of the text at hand.
_SUBJECT_CHARS = 140
_BODY_CHARS = 320
_MIN_SUBJECT_CHARS = 12     # shorter cannot state a reason ("fix bug", "update")

# Subjects that describe the edit rather than its reason: git's own (merge,
# revert, fixup!, squash!) and common English ones. Including them teaches the
# model that this evidence is noise, which is worse than no evidence. A subject
# in another language passes unless it starts with one of these words.
_NOISE_SUBJECT = re.compile(
    r"^\s*(wip\b|fixup!|squash!|revert\s+\"|merge\s+(branch|pull|remote)\b"
    r"|bump\b|release\s+v?\d|version\s+v?\d"
    r"|(fix(es|ed)?\s+)?(typo|lint(ing)?|format(ting)?|whitespace|indentation|spelling)\b"
    r"|(update|upgrade|bump)\s+(deps|dependencies|lock ?file|submodules?)\b"
    r"|(update|add)\s+changelog\b"
    r"|clean\s?up\.?$|cleanup\b|tidy\b|nit\b|minor\b|misc\b|small\s+fix)",
    re.IGNORECASE,
)

# Body lines that are metadata, not reasoning. The colon is required:
# "Fixes: #412" is a trailer, while "Fixes applied from the review:" is the
# opening sentence of the reasoning.
_TRAILER = re.compile(
    r"^\s*(?:"
    r"(?:co-authored-by|signed-off-by|claude-session|claude-code|reviewed-by"
    r"|acked-by|tested-by|cc|refs?|closes|fixes|resolves|see also|change-id)"
    r"\s*:"
    r"|🤖\s*generated with"      # no colon in the marker git-tools emit
    r")",
    re.IGNORECASE,
)

# One parsed repo-wide log per root, with a TTL. Keyed by resolved path.
_log_cache: dict[str, tuple[float, list[tuple[str, str, list[str]]]]] = {}


def _run_git_log(root_dir: str | Path) -> str:
    """One repo-wide ``git log`` over the recent window, or "" if unavailable.

    Records are separated by RS and fields by US so a subject or body
    containing newlines cannot be mistaken for the ``--name-only`` file list
    that follows it.
    """
    try:
        proc = subprocess.run(
            [
                "git", "-C", str(root_dir), "log", "--no-merges",
                f"-n{_LOG_SCAN_COMMITS}", "--date-order",
                "--format=%x1e%H%x1f%s%x1f%b%x1f", "--name-only",
            ],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "")[:_LOG_MAX_CHARS]


def _parse_log(out: str) -> list[tuple[str, str, list[str]]]:
    """``(subject, body, files)`` per commit, newest first."""
    records: list[tuple[str, str, list[str]]] = []
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        parts = rec.split("\x1f")
        if len(parts) < 4:
            continue
        subject = parts[1].strip()
        body = parts[2]
        files = [ln.strip() for ln in parts[3].splitlines() if ln.strip()]
        if subject:
            records.append((subject, body, files))
    return records


def _repo_log(root_dir: str | Path) -> list[tuple[str, str, list[str]]]:
    """The cached, parsed commit window for ``root_dir`` (newest first)."""
    key = str(Path(root_dir).resolve()) if root_dir else ""
    if not key:
        return []
    hit = _log_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] <= _LOG_TTL_S:
        return hit[1]
    records = _parse_log(_run_git_log(root_dir))
    _log_cache[key] = (now, records)
    return records


def _body_gist(body: str) -> str:
    """The first real paragraph of a commit body, trailers stripped. A body's
    reasoning is nearly always its opening paragraph; what follows is
    implementation notes and metadata."""
    kept: list[str] = []
    for raw in (body or "").splitlines():
        line = raw.strip()
        if _TRAILER.match(line):
            break
        if not line:
            if kept:  # end of the first paragraph
                break
            continue
        kept.append(line)
    return " ".join(kept).strip()


def commit_rationales(
    root_dir: str | Path,
    files: set[str] | list[str],
    *,
    per_file: int = _PER_FILE_COMMITS,
    limit: int = _MAX_COMMITS,
    rescale: Callable[[int, str], int] = lambda chars, _text: chars,
) -> list[dict]:
    """Recent commits that touched ``files``, newest first, noise filtered, as
    ``{files, subject, why?}``.

    A commit that touched several of the requested files appears once, listing
    them. ``rescale(chars, text)`` ports a character count written for Latin
    prose to the script of ``text`` (``doclang.char_budget``); the default
    keeps it as written.
    """
    wanted = {str(f) for f in files}
    if not wanted:
        return []
    budget = dict.fromkeys(wanted, per_file)
    out: list[dict] = []
    for subject, body, touched in _repo_log(root_dir):
        if len(out) >= limit:
            break
        hits = [f for f in touched if f in wanted and budget.get(f, 0) > 0]
        if not hits:
            continue
        # A noisy commit still spends the budget: it is the most recent thing
        # that touched the file, and skipping it for free would let one file's
        # old history crowd out every other file's recent one.
        for f in hits:
            budget[f] -= 1
        if len(subject) < rescale(_MIN_SUBJECT_CHARS, subject) or _NOISE_SUBJECT.match(subject):
            continue
        entry: dict = {"files": sorted(hits), "subject": subject[:rescale(_SUBJECT_CHARS, subject)]}
        gist = _body_gist(body)
        if gist:
            entry["why"] = gist[:rescale(_BODY_CHARS, gist)].rstrip()
        out.append(entry)
    return out
