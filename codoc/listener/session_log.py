"""Append-only JSONL log for Claude Code hook events, one file per session."""
from __future__ import annotations

import json
from pathlib import Path


def log_event(codoc_dir: str, session_id: str, event: dict) -> None:
    """Append a hook event to .codoc/claude-sessions/<session_id>.jsonl. Errors are non-fatal."""
    sessions_dir = Path(codoc_dir) / "claude-sessions"
    sessions_dir.mkdir(exist_ok=True)
    log_path = sessions_dir / f"{session_id}.jsonl"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except OSError:
        pass
