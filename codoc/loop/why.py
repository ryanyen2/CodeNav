"""Why-evidence — the grounded-rationale channel behind every description.

Both loops asked the model for a feature's *why* while handing it only code:
chunk source, call edges, and the prose already on the node. A why is not
recoverable from those. Code records what a program does; the decision behind
it — the constraint it answers, the alternative it rejected — lives in the
heads of the people who wrote it and, when it survives at all, in the prose
they wrote *around* the code. Asked for a why with no such prose in context, a
model supplies the most plausible-sounding story, and a plausible story about a
constraint is the worst possible content for a document whose whole purpose is
to tell a newcomer what they may safely change.

So this module goes and finds the prose. Three sources, in descending order of
how directly they speak to a decision:

  1. **Commit messages.** The one place a working repo routinely records why a
     change was made. Read once per repo (one ``git log`` over a bounded recent
     window, cached), then filtered per file — a per-file subprocess would cost
     one process per file at bootstrap, which is where this evidence matters
     most.
  2. **Realize directives.** When codoc's own Loop B queued the change, the why
     is not inferred at all: the author stated it, and it is sitting in
     ``realized.jsonl``. Not reading it back was the strangest gap of the three
     — the system knew the answer and threw it away.
  3. **Prior rationale.** What a past pass already recorded about this feature.
     Feeding it back is what keeps a description a running theory rather than a
     fresh guess each time; without it, successive amends contradict each other
     and the node's history reads as drift instead of development.

Everything here is advisory. A repo with no git, no history, or no directives
simply yields an empty block and the prompt falls back to purpose-only prose
(see the assertion register in ``prompts/tree_update.txt``). Nothing raises:
this runs inside a loop pass whose actual job is keeping bindings correct, and
missing evidence must never cost a user their tree update.

Every source is capped, and the assembled block is capped again — this text
rides in the volatile tail of a cache-aligned prompt on every pass, so its size
is a recurring bill, not a one-time one.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

# ─── budgets ────────────────────────────────────────────────────────────────
# Sized so a full evidence block stays around a thousand tokens even on a busy
# repo. The scan window is the one number worth understanding: rationale from
# 600 commits ago is not why the code looks like it does today, so scanning
# deeper would cost real time to surface mostly-misleading context.
_GIT_TIMEOUT_S = 5.0
_LOG_SCAN_COMMITS = 600     # how far back one repo-wide scan reads
_LOG_MAX_CHARS = 4_000_000  # hard stop on a pathological log payload
_LOG_TTL_S = 120.0          # a watch tick every few seconds must not re-shell out
_PER_FILE_COMMITS = 3       # per touched file — the newest few explain the shape
_MAX_COMMITS = 8            # …and this many across the whole block
_SUBJECT_CHARS = 140
_BODY_CHARS = 320
_MAX_DIRECTIVES = 3
_DIRECTIVE_CHARS = 400
_MAX_PRIOR_FEATURES = 6
_PRIOR_PER_FEATURE = 2
_PRIOR_CHARS = 200
_TOTAL_CHARS = 4500         # the assembled block, after per-source caps

# Subjects that match carry no rationale — they describe the edit, not its
# reason. Including them trains the model that the evidence channel is noise,
# which is worse than an empty channel: it invites falling back to invention
# while a "source" is nominally present.
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

# Body lines that are metadata, not reasoning.
_TRAILER = re.compile(
    r"^\s*(co-authored-by|signed-off-by|claude-session|claude-code|reviewed-by"
    r"|acked-by|tested-by|cc|refs?|closes|fixes|resolves|see also|change-id"
    r"|🤖\s*generated with)\b[:\s]",
    re.IGNORECASE,
)

# One parsed repo-wide log per root, with a TTL. Keyed by resolved path.
_log_cache: dict[str, tuple[float, list[tuple[str, str, list[str]]]]] = {}


def clear_cache() -> None:
    """Drop the memoized git log (tests, and any caller that just committed)."""
    _log_cache.clear()


# ─── git ────────────────────────────────────────────────────────────────────

def _run_git_log(root_dir: str | Path) -> str:
    """One repo-wide ``git log`` over the recent window, or "" if unavailable.

    Records are separated by RS and fields by US so a subject or body
    containing newlines — the normal case for a body — cannot be mistaken for
    the ``--name-only`` file list that follows it.
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
        return ""  # no git binary, not a repo, or it hung — all the same to us
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


def repo_log(root_dir: str | Path) -> list[tuple[str, str, list[str]]]:
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
    """The first real paragraph of a commit body, trailers stripped.

    A commit body's reasoning is nearly always its opening paragraph; what
    follows is implementation notes and metadata. Keeping only the opening is
    what lets several commits fit in one prompt.
    """
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
    gist = " ".join(kept).strip()
    return gist[:_BODY_CHARS].rstrip()


def _is_noise(subject: str) -> bool:
    return (
        len(subject) < _MIN_SUBJECT_CHARS
        or bool(_NOISE_SUBJECT.match(subject))
    )


def commit_rationales(
    root_dir: str | Path,
    files: set[str] | list[str],
    *,
    per_file: int = _PER_FILE_COMMITS,
    limit: int = _MAX_COMMITS,
) -> list[dict]:
    """Recent commits that touched ``files``, newest first, noise filtered.

    A commit that touched several of the requested files appears once, listing
    them — the reason it states is one reason, and repeating it per file would
    spend the budget on duplicates.
    """
    wanted = {str(f) for f in files}
    if not wanted:
        return []
    budget = dict.fromkeys(wanted, per_file)
    out: list[dict] = []
    for subject, body, touched in repo_log(root_dir):
        if len(out) >= limit:
            break
        hits = [f for f in touched if f in wanted and budget.get(f, 0) > 0]
        if not hits:
            continue
        if _is_noise(subject):
            # Still spend the budget: a noisy commit is the most recent thing
            # that touched the file, and skipping it for free would let one
            # file's ancient history crowd out every other file's recent one.
            for f in hits:
                budget[f] -= 1
            continue
        for f in hits:
            budget[f] -= 1
        entry: dict = {"files": sorted(hits), "subject": subject[:_SUBJECT_CHARS]}
        gist = _body_gist(body)
        if gist:
            entry["why"] = gist
        out.append(entry)
    return out


# ─── codoc's own record ─────────────────────────────────────────────────────

_ASK_LINE = re.compile(
    r"^\s*(Author asked|Author note|New intent|Intent|Focus|Consult)\s*:\s*(.+)$",
)


def _directive_gist(text: str) -> str:
    """The stated-intent lines of a rendered directive, without the scaffolding.

    A directive body carries bound-code listings and edit-scope lines that the
    describing model has no use for; what it needs is the sentence the author
    wrote about what they wanted and why.
    """
    kept: list[str] = []
    for line in (text or "").splitlines():
        m = _ASK_LINE.match(line)
        if m:
            value = m.group(2).strip().strip('"')
            if value and value != "(none)":
                kept.append(f"{m.group(1)}: {value}")
    return " · ".join(kept)[:_DIRECTIVE_CHARS]


def directive_rationales(
    codoc_dir: str | Path,
    feature_ids: set[str] | list[str],
    *,
    limit: int = _MAX_DIRECTIVES,
) -> list[dict]:
    """What the author asked for on these features, from the realize outcomes log.

    This is the only source that is not inference at all — the author said it,
    codoc queued it, an agent implemented it. When a feature appears here, the
    description of the change it just underwent should read as a statement, not
    a hedge.
    """
    wanted = {str(f) for f in feature_ids if f}
    if not wanted:
        return []
    try:
        from codoc.loop.edits import read_realized

        entries = read_realized(codoc_dir)
    except Exception:  # noqa: BLE001 — advisory context only
        return []
    out: list[dict] = []
    for e in reversed(entries):  # newest first
        if len(out) >= limit:
            break
        fid = str(e.get("feature_id") or "")
        if fid not in wanted:
            continue
        gist = _directive_gist(str(e.get("text") or ""))
        if gist:
            out.append({"feature_id": fid, "asked": gist})
    return out


def prior_rationales(store, feature_ids: set[str] | list[str]) -> list[dict]:
    """Rationale already recorded against these features, newest first.

    Showing a feature its own recorded reasoning is what makes an amend an
    *extension* of a theory rather than a fresh derivation of one. Without it
    each pass re-reasons from the current diff, and a node's history reads as a
    sequence of unrelated opinions.
    """
    if store is None:
        return []
    out: list[dict] = []
    for fid in list(feature_ids)[:_MAX_PRIOR_FEATURES]:
        if not fid:
            continue
        try:
            events = store.events_for_feature(fid, limit=20)
        except Exception:  # noqa: BLE001 — advisory context only
            continue
        seen: set[str] = set()
        notes: list[str] = []
        for ev in events:  # events_for_feature is already newest-first
            text = (getattr(ev.op, "rationale", "") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            notes.append(text[:_PRIOR_CHARS])
            if len(notes) >= _PRIOR_PER_FEATURE:
                break
        if notes:
            out.append({"feature_id": fid, "recorded": notes})
    return out


# ─── assembly ───────────────────────────────────────────────────────────────

def _fits(block: dict) -> dict:
    """Trim the assembled block to ``_TOTAL_CHARS``, dropping weakest-source-first.

    Commits are dropped before directives and prior rationale because they are
    the most numerous and the least specific: a directive names what the author
    asked for on this exact feature, while a commit merely touched a file the
    feature happens to bind.
    """
    import json

    while len(json.dumps(block)) > _TOTAL_CHARS and block.get("commits"):
        block["commits"] = block["commits"][:-1]
        if not block["commits"]:
            block.pop("commits")
    while len(json.dumps(block)) > _TOTAL_CHARS and block.get("prior"):
        block["prior"] = block["prior"][:-1]
        if not block["prior"]:
            block.pop("prior")
    return block


def gather_why_evidence(
    *,
    root_dir: str | Path | None = None,
    codoc_dir: str | Path | None = None,
    store=None,
    files: set[str] | list[str] | None = None,
    feature_ids: set[str] | list[str] | None = None,
) -> dict:
    """The evidence block for one pass, or ``{}`` when nothing was recorded.

    Returning an empty dict rather than empty lists is deliberate: the caller
    omits the key entirely, so a repo with no recoverable why sends a prompt
    with no evidence section at all — which is what the assertion register in
    the prompt keys off to stay hedged.
    """
    block: dict = {}
    try:
        if root_dir and files:
            commits = commit_rationales(root_dir, files)
            if commits:
                block["commits"] = commits
        if codoc_dir and feature_ids:
            asked = directive_rationales(codoc_dir, feature_ids)
            if asked:
                block["directives"] = asked
        if store is not None and feature_ids:
            prior = prior_rationales(store, feature_ids)
            if prior:
                block["prior"] = prior
    except Exception:  # noqa: BLE001 — the loop's real work must not fail here
        return block
    return _fits(block) if block else {}
