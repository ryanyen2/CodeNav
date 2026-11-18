"""What git knows about the code: the commit at HEAD, the hand-offs new commits
realize, and what recent commits said about a file.

``head_sha`` anchors a hand-off to the commit the work starts from, so a reader
can later diff what the agent did. ``handoffs_landed`` reads the hand-off ids
commits name in a trailer, so a merged pull request cites its hand-off by
identity. ``commit_rationales`` gives a bootstrap pass
the commit messages, where a repository routinely records why a change was made: code says
what a program does, and a model asked for the reason with only code in front
of it invents one.

Everything here is advisory and fails soft: no git binary, not a repository, no
commits or a hung invocation all yield ``""`` or ``[]``, never an error.
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

    The catch is as broad as that sentence on purpose. This runs on the
    hand-off path, and an optional provenance lookup must not stop queued work
    from reaching the agent. A wrapped ``Popen`` raises ``AttributeError``, not
    ``OSError``, and a decoding failure raises neither, so enumerating
    exception types would mean guessing.
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


def head_sha(root_dir: str | Path) -> str:
    """The full commit sha at ``HEAD``, or ``""`` when there isn't one (no git,
    not a repo, a repo with zero commits): all mean there is no "before" to
    diff against."""
    sha = _git(root_dir, "rev-parse", "HEAD")
    # rev-parse on an unborn branch prints the ref name back rather than failing on
    # some git versions; a sha is 40 lowercase hex and nothing else.
    if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
        return sha
    return ""


# The trailer a commit names the hand-off it realizes by: the hub worker writes
# it on every commit it builds, and anyone may write it on their own.
HANDOFF_TRAILER = "Codoc-Handoff"


def handoffs_landed(root_dir: str | Path, old: str, new: str) -> frozenset[str]:
    """The hand-off op ids that the commits in ``old..new`` name in a
    ``Codoc-Handoff`` trailer, as git parses trailers."""
    if not old or not new:
        return frozenset()
    out = _git(root_dir, "log", f"--format=%(trailers:key={HANDOFF_TRAILER},valueonly)", f"{old}..{new}")
    return frozenset(line.strip() for line in out.splitlines() if line.strip())


# ── commit messages ──────────────────────────────────────────────────────────

# The scan window is the one number worth understanding: rationale from 600
# commits ago is not why the code looks like it does today, so scanning deeper
# would cost time to surface mostly-misleading context.
_LOG_SCAN_COMMITS = 600     # how far back one repo-wide scan reads
_LOG_MAX_CHARS = 4_000_000  # hard stop on a pathological log payload
_LOG_TTL_S = 120.0          # a pass every few seconds must not re-shell out
_PER_FILE_COMMITS = 3       # per file: the newest few explain the shape
_MAX_COMMITS = 8            # across one call
_SUBJECT_CHARS = 140        # written for Latin prose; the caller's ``cap`` rescales
_BODY_CHARS = 320

# Subjects that match carry no rationale: they describe the edit, not its
# reason. Including them teaches the model that this evidence is noise, which
# is worse than no evidence.
_NOISE_SUBJECT = re.compile(
    r"^\s*(wip\b|fixup!|squash!|revert\s+\"|merge\s+(branch|pull|remote)\b"
    r"|bump\b|release\s+v?\d|version\s+v?\d"
    r"|(fix(es|ed)?\s+)?(typo|lint(ing)?|format(ting)?|whitespace|indentation|spelling)\b"
    r"|(update|upgrade|bump)\s+(deps|dependencies|lock ?file|submodules?)\b"
    r"|(update|add)\s+changelog\b"
    r"|clean\s?up\.?$|cleanup\b|tidy\b|nit\b|minor\b|misc\b|small\s+fix)",
    re.IGNORECASE,
)
# A subject this short cannot state a reason ("fix bug", "update").
_MIN_SUBJECT_CHARS = 12

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


def _is_noise(subject: str) -> bool:
    return len(subject) < _MIN_SUBJECT_CHARS or bool(_NOISE_SUBJECT.match(subject))


def commit_rationales(
    root_dir: str | Path,
    files: set[str] | list[str],
    *,
    per_file: int = _PER_FILE_COMMITS,
    limit: int = _MAX_COMMITS,
    cap: Callable[[int, str], int] = lambda chars, _text: chars,
) -> list[dict]:
    """Recent commits that touched ``files``, newest first, noise filtered, as
    ``{files, subject, why?}``.

    A commit that touched several of the requested files appears once, listing
    them. ``cap(chars, text)`` rescales a character cap written for Latin prose
    to the script of ``text`` (``doclang.char_budget``); the default applies
    the cap as written.
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
        if _is_noise(subject):
            continue
        entry: dict = {"files": sorted(hits), "subject": subject[:cap(_SUBJECT_CHARS, subject)]}
        gist = _body_gist(body)
        if gist:
            entry["why"] = gist[:cap(_BODY_CHARS, gist)].rstrip()
        out.append(entry)
    return out
