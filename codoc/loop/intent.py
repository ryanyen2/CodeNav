"""Captured author intent — the *why* behind a coding session's changes.

The Claude Code ``UserPromptSubmit`` hook appends each user prompt to
``.codoc/intent.jsonl``; Loop A reads the recent tail back into the tree-update
LLM context (``changes["author_intent"]``) so amended/added descriptions and
rationales can state the author's actual purpose instead of reconstructing it
from the diff.

Append-only JSONL (one ``{"session_id", "at", "ts", "prompt"}`` object per
line), trimmed to a bounded tail on write. The file lives inside ``.codoc/``,
which ``codoc init`` gitignores (``bootstrap._write_codoc_gitignore``) — prompts
never reach version control.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from codoc.doclang import terms as _doclang_terms
from codoc.loop.filenames import INTENT_FILENAME

_MAX_PROMPT_CHARS = 2000   # a prompt is context, not a transcript — cap it
_MAX_ENTRIES = 50          # bounded tail; older intent has stopped mattering
_MAX_AGE_S = 2 * 60 * 60   # recall window: intent older than this is stale
_RECALL_LIMIT = 3          # at most this many prompts ride into one LLM pass


def record_intent(codoc_dir: str | Path, session_id: str, prompt: str) -> None:
    """Append one user prompt. Blanks and slash commands (``/codoc:sync`` …)
    carry no intent and are skipped. Never raises — this runs on the hook path,
    where an error would block the user's turn."""
    text = (prompt or "").strip()
    if not text or text.startswith("/"):
        return
    entry = {
        "session_id": session_id or "",
        "at": datetime.now(timezone.utc).isoformat(),
        "ts": time.time(),
        "prompt": text[:_MAX_PROMPT_CHARS],
    }
    path = Path(codoc_dir) / INTENT_FILENAME
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim(path)
    except OSError:
        pass


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= _MAX_ENTRIES:
        return
    # The file is advisory context: a rare append lost to a concurrent trim
    # costs one prompt of recall, never correctness — so a plain replace is fine.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines[-_MAX_ENTRIES:]) + "\n", encoding="utf-8")
    tmp.replace(path)


def _pool(codoc_dir: str | Path, max_age_s: float) -> list[dict]:
    """Fresh captured prompts, oldest → newest, session-preferred.

    Prefers prompts from the session that owns the current activity epoch (the
    session whose writes are being reflected); falls back to any fresh-enough
    prompts when that session captured none. Never raises."""
    path = Path(codoc_dir) / INTENT_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[dict] = []
    for line in raw.splitlines():
        try:
            e = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(e, dict) and e.get("prompt"):
            entries.append(e)
    now = time.time()
    fresh = [e for e in entries
             if now - float(e.get("ts") or 0) <= max_age_s]
    sid = _epoch_session(codoc_dir)
    if sid:
        owned = [e for e in fresh if e.get("session_id") == sid]
        if owned:
            fresh = owned
    return fresh


def recent_intent(
    codoc_dir: str | Path,
    *,
    limit: int = _RECALL_LIMIT,
    max_age_s: float = _MAX_AGE_S,
) -> list[str]:
    """The most recent prompts, oldest → newest. Consecutive duplicates collapse."""
    prompts: list[str] = []
    for e in _pool(codoc_dir, max_age_s)[-limit:]:
        if not prompts or prompts[-1] != e["prompt"]:
            prompts.append(str(e["prompt"]))
    return prompts


def freshest_intent(
    codoc_dir: str | Path,
    *,
    max_age_s: float = _MAX_AGE_S,
) -> dict:
    """The single freshest captured prompt as ``{prompt, session_id, at, ts}``, or ``{}``.

    :func:`recent_intent` deliberately returns bare strings — it feeds a PROMPT, where a
    session id would be noise. A directive is the other reader: it is a durable record of
    why a change happened, and "which session asked for this" is the part that lets the
    IDE walk back from a line of prose to the conversation that produced it. Same pool
    and the same epoch-session preference, so the id returned is genuinely the one behind
    the words the directive quotes.
    """
    pool = _pool(codoc_dir, max_age_s)
    if not pool:
        return {}
    e = pool[-1]
    return {
        "prompt": str(e.get("prompt") or ""),
        "session_id": str(e.get("session_id") or ""),
        "at": str(e.get("at") or ""),
        "ts": float(e.get("ts") or 0),
    }


def _terms(text: str) -> set[str]:
    """Content words of a prompt or symbol path, camelCase and snake_case split.

    Symbols are the bridge between a prompt and a change: a user who wrote
    "make the ollama client retry" and a diff that touched
    ``OllamaClient.complete`` share ``ollama`` and ``client`` once both sides
    are broken into words. Matching on raw identifiers would miss it.

    Delegates to :func:`codoc.doclang.terms`, which segments each script the way
    that script needs. This used to split on ``[^A-Za-z0-9]+``, which discarded
    every non-ASCII character — so a prompt typed in Chinese produced an EMPTY
    term set, scored zero against every symbol, and :func:`relevant_intent` fell
    back to plain recency. The author's actual "why" was on disk and unusable.
    """
    return _doclang_terms(text)


def relevant_intent(
    codoc_dir: str | Path,
    terms: set[str] | list[str] | None = None,
    *,
    limit: int = _RECALL_LIMIT,
    max_age_s: float = _MAX_AGE_S,
) -> list[str]:
    """The captured prompts most likely to explain *this* change, oldest → newest.

    ``recent_intent`` answers "what was the user just doing", which is the right
    question for a status line and the wrong one for a description: in a session
    that touched four areas, plain recency attributes every change to whatever
    the user typed last. Scoring each prompt against the changed symbols picks
    the prompt that is actually about the code in front of us.

    The newest prompt is always kept regardless of score — a change with no
    lexical overlap is usually a follow-up turn ("now do the same for the other
    one"), where recency is the only signal there is. Ties break toward the
    newer prompt for the same reason.
    """
    pool = _pool(codoc_dir, max_age_s)
    if not pool:
        return []
    wanted = _terms(" ".join(str(t) for t in terms)) if terms else set()
    if not wanted:
        return recent_intent(codoc_dir, limit=limit, max_age_s=max_age_s)

    scored: list[tuple[float, int, dict]] = []
    for idx, e in enumerate(pool):
        overlap = _terms(str(e.get("prompt", ""))) & wanted
        # Normalized so a long prompt that mentions everything does not outrank
        # a short one that is precisely about this change.
        score = len(overlap) / (len(wanted) ** 0.5)
        scored.append((score, idx, e))
    newest_idx = len(pool) - 1
    chosen = {newest_idx}
    for _score, idx, _e in sorted(scored, key=lambda s: (-s[0], -s[1])):
        if len(chosen) >= limit:
            break
        if _score > 0:
            chosen.add(idx)

    prompts: list[str] = []
    for idx in sorted(chosen):
        text = str(pool[idx]["prompt"])
        if not prompts or prompts[-1] != text:
            prompts.append(text)
    return prompts


def _epoch_session(codoc_dir: str | Path) -> str | None:
    try:
        from codoc.loop.activity import read_activity

        ep = (read_activity(codoc_dir) or {}).get("epoch") or {}
        eid = ep.get("id") or ""
        return eid[3:] if eid.startswith("ep-") else None
    except Exception:  # noqa: BLE001 — advisory preference only
        return None
