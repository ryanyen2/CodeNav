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


def recent_intent(
    codoc_dir: str | Path,
    *,
    limit: int = _RECALL_LIMIT,
    max_age_s: float = _MAX_AGE_S,
) -> list[str]:
    """The recent prompts most likely to explain the change under reflection,
    oldest → newest.

    Prefers prompts from the session that owns the current activity epoch (the
    session whose writes are being reflected); falls back to any fresh-enough
    prompts when that session captured none. Consecutive duplicates collapse.
    Never raises."""
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
    prompts: list[str] = []
    for e in fresh[-limit:]:
        if not prompts or prompts[-1] != e["prompt"]:
            prompts.append(str(e["prompt"]))
    return prompts


def _epoch_session(codoc_dir: str | Path) -> str | None:
    try:
        from codoc.loop.activity import read_activity

        ep = (read_activity(codoc_dir) or {}).get("epoch") or {}
        eid = ep.get("id") or ""
        return eid[3:] if eid.startswith("ep-") else None
    except Exception:  # noqa: BLE001 — advisory preference only
        return None
