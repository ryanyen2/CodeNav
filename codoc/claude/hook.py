"""The handler Claude Code's hooks run.

Invoked as ``python -m codoc.claude.hook <event>``, where ``<event>`` is one of
``session-start``, ``user-prompt``, ``pre-tool``, ``post-tool``, ``stop``,
``session-end``. Claude Code passes the hook payload as JSON on stdin.

It does two things:

1. Keeps the agent epoch in ``.codoc/activity.json`` (``workspace.epoch``): open
   while a turn runs, so the daemon defers its reflect pass until the turn
   ends; that pass reads what changed from the index, so a file the turn
   changed through a shell counts too. With no daemon, the close starts a
   detached process that settles what the turn built.
2. Tells the session when hand-offs are queued in ``.codoc/realize.md``, at
   each user prompt and mid-turn when the queue grows, and, when a turn ends
   having reported some queued items but not all, asks it once to report or
   explain the rest.

It never raises and always exits 0: a hook that fails blocks the agent. It
never opens the workspace; it reads published files only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from subprocess import DEVNULL, Popen
from typing import Any

from codoc.workspace import epoch
from codoc.daemon.pidfile import daemon_running
from codoc.workspace.layout import REALIZE, find_codoc_dir


def queued_ids(codoc_dir: str) -> list[str]:
    """The hand-offs queued in ``realize.md``, as its ``### ⟨d:<id>⟩``
    headings name them; empty when nothing is queued."""
    try:
        text = (Path(codoc_dir) / REALIZE).read_text(encoding="utf-8")
    except OSError:
        return []
    return re.findall(r"^### ⟨d:([^⟩]+)⟩", text, re.M)


# ─── Handlers, one per event ─────────────────────────────────────────────────

def handle_session_start(payload: dict[str, Any], codoc_dir: str) -> None:
    epoch.open_epoch(codoc_dir, payload.get("session_id"))


def handle_user_prompt(payload: dict[str, Any], codoc_dir: str) -> None:
    """The person's prompt is a turn's first rising edge: reopen the epoch.
    Then, when hand-offs are queued, say so and how to build them, and record
    how many the session was told, so the first tool call of the turn does not
    announce them again as growth."""
    n = len(queued_ids(codoc_dir))
    try:
        epoch.reopen_epoch(codoc_dir, payload.get("session_id"))
        epoch.note_queue_size(codoc_dir, n)
    except Exception:  # noqa: BLE001 (epoch upkeep must never break the turn)
        pass
    if not (Path(codoc_dir) / REALIZE).exists():
        return
    count = f"{n} hand-off(s)" if n else "hand-offs"
    msg = (
        f"codoc: {count} queued in .codoc/realize.md. "
        "Run /codoc:realize to build them: read the file, build each hand-off "
        "(never touching .codoc/), and call codoc_reflect with caused_by=<the "
        "hand-off's d: id> to bind the code. That citation reports the hand-off; never "
        "delete the queue files yourself. It settles when your turn ends."
    )
    out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}
    print(json.dumps(out, ensure_ascii=False))


def handle_pre_tool(payload: dict[str, Any], codoc_dir: str) -> None:
    """A tool call is also a rising edge, and the pre-tool hook fires before
    the tool writes, so the daemon sees the epoch open no later than the
    change it must defer. Each turn after the first starts with the epoch the
    last turn's stop closed. Then a call that names a file in the repository
    renews the epoch's lease."""
    epoch.reopen_epoch(codoc_dir, payload.get("session_id"))
    _touch(payload, codoc_dir)


def handle_post_tool(payload: dict[str, Any], codoc_dir: str) -> None:
    """Renew the epoch's lease again, and tell the
    working session when the realize queue grew: the person may accept edits
    while the agent works, and the next prompt is too late to learn of them."""
    _touch(payload, codoc_dir)
    try:
        n = len(queued_ids(codoc_dir))
        if not epoch.note_queue_size(codoc_dir, n):
            return
    except Exception:  # noqa: BLE001 (a nudge must never break the turn)
        return
    msg = (
        f"codoc: the realize queue grew: {n} hand-off(s) now queued in "
        ".codoc/realize.md (the user handed off or accepted tree edits while you "
        "work). Read the file and build each hand-off, then codoc_reflect with "
        "caused_by=<its d: id>; that reports it. Never delete the queue files. "
        "It settles when your turn ends."
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}},
                     ensure_ascii=False))


def handle_stop(payload: dict[str, Any], codoc_dir: str) -> None:
    """A turn ends. Settling waits for the epoch to close, so an item the
    agent built but did not report with ``codoc_reflect`` would stay queued
    with nothing to say why. When the turn's MCP calls cited some queued items
    but not all, block the stop once and name the rest (``stop_hook_active``
    is set on the retry), keeping the epoch open while the agent answers. A
    turn that never touched the queue just ends."""
    if not payload.get("stop_hook_active"):
        queued = queued_ids(codoc_dir)
        cited = set(epoch.read_activity(codoc_dir).get("cited") or [])
        left = [d for d in queued if d not in cited] if cited.intersection(queued) else []
        if left:
            ids = ", ".join(f"d:{d}" for d in left)
            print(json.dumps({"decision": "block", "reason": (
                f"codoc: {len(left)} queued hand-off(s) in .codoc/realize.md have no codoc_reflect "
                f"citing them: {ids}. For each one you built, call codoc_reflect with that id as "
                "caused_by; for one you did not build, say why.")}, ensure_ascii=False))
            return
    _close(payload, codoc_dir)


def handle_session_end(payload: dict[str, Any], codoc_dir: str) -> None:
    """The backstop for exits that skip the stop hook (Esc mid-turn, a closed
    terminal)."""
    _close(payload, codoc_dir)


def _touch(payload: dict[str, Any], codoc_dir: str) -> None:
    file_path = (payload.get("tool_input") or {}).get("file_path")
    if file_path and epoch.repo_relative(file_path, codoc_dir) is not None:
        epoch.touch_epoch(codoc_dir)


def _close(payload: dict[str, Any], codoc_dir: str) -> None:
    """Close this session's epoch. Only the session that opened the epoch
    closes it: the session end can fire long after the last turn, when a newer
    session owns the file. A close skipped here is harmless, since the daemon
    closes an epoch that goes silent.

    With no daemon, nothing applies after the close, and every MCP write of the
    turn landed while the epoch was open, so nothing settled. When something
    is queued, start a detached process that settles once and exits
    (``client.settle_closed_epoch``). The hook does not wait for it."""
    sid = payload.get("session_id")
    if (epoch.close_epoch(codoc_dir, f"ep-{sid}" if sid else "") and queued_ids(codoc_dir)
            and not daemon_running(codoc_dir)):
        Popen([sys.executable, "-m", "codoc.daemon.client", str(Path(codoc_dir).resolve())],
              stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)


# ─── Entrypoint ──────────────────────────────────────────────────────────────

_HANDLERS = {
    "session-start": handle_session_start,
    "user-prompt": handle_user_prompt,
    "pre-tool": handle_pre_tool,
    "post-tool": handle_post_tool,
    "stop": handle_stop,
    "session-end": handle_session_end,
}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    handler = _HANDLERS.get(args[0]) if args else None
    if handler is None:
        return 0                    # no event, or one codoc does not handle
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        return 0
    codoc_dir = find_codoc_dir(payload.get("cwd") or os.getcwd())
    if codoc_dir is None:
        return 0                    # not a codoc repository: stay out of the way
    try:
        handler(payload, codoc_dir)
    except Exception:  # noqa: BLE001 (never block the agent)
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
