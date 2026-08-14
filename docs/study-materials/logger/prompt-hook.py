#!/usr/bin/env python3
"""Record that a prompt was sent, in both conditions.

Claude Code runs this on every UserPromptSubmit. It appends one line to the same
interaction log the study logger writes, so prompts sit in the same stream as
everything else and sort by the same clock.

Why this exists when codoc already captures prompts. codoc's own hook records the
prompt through ``record_intent``, but codoc's hooks are installed in one condition
only. A measure that exists on one side and not the other is not a comparison, so
prompt capture is owned by the study and installed in both.

What it records: when, how long the prompt was, and how many words. Not the text.
The words are already in the Claude Code transcript, which travels in the session
zip; the interaction log is the thing that gets mirrored to a database we promised
would hold no content.

Contract, inherited from every other hook: never raise, never block, always exit 0.
A hook that fails takes the participant's turn with it.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def log_path() -> Path:
    """The same file the logger extension writes.

    Both derive it the same way, from the project folder's name, so the two
    streams land together without either having to be told where the other is.
    """
    override = os.environ.get("CODOC_STUDY_LOG")
    if override:
        return Path(override)
    workspace = Path.cwd().name or "no-folder"
    return Path.home() / "codoc-study" / "session-logs" / f"interaction-{workspace}.jsonl"


def condition_of(workspace: str) -> str:
    return "baseline" if "baseline" in workspace else "codoc"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — a malformed payload must not break the turn
        return 0

    try:
        prompt = payload.get("prompt") or ""
        workspace = Path.cwd().name or "no-folder"
        line = {
            "t": int(time.time() * 1000),
            "p": os.environ.get("CODOC_STUDY_PARTICIPANT", ""),
            "ws": workspace,
            "ev": "prompt",
            "chars": len(prompt),
            "words": len(prompt.split()),
            "lines": prompt.count("\n") + 1 if prompt else 0,
            "condition": condition_of(workspace),
        }
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — recording is advisory, the turn is not
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
