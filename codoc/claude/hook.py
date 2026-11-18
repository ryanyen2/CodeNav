"""The handler Claude Code's hooks run.

Invoked as ``python -m codoc.claude.hook <event>``, where ``<event>`` is one of
``session-start``, ``stop``, ``session-end``, ``pre-tool``, ``post-tool``,
``user-prompt``. Claude Code passes the hook payload as JSON on stdin.

It does two things:

1. Keeps ``.codoc/activity.json``: the agent epoch (open while a turn is
   running, so the daemon holds its reflect pass until the turn ends) and the
   files the turn touched, which that pass reflects. With no daemon, the close
   starts a detached process that settles what the turn built.
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
import time
from datetime import datetime, timezone
from pathlib import Path
from subprocess import DEVNULL, Popen
from typing import Any

from codoc.daemon.epoch import read_activity as _read_activity
from codoc.daemon.epoch import write_activity as _write_activity
from codoc.daemon.pidfile import daemon_running
from codoc.workspace.layout import REALIZE
from codoc.workspace.layout import find_codoc_dir as _find_codoc_dir


# ─── Filesystem helpers ───────────────────────────────────────────────────────

def _root_dir(codoc_dir: str) -> str:
    return str(Path(codoc_dir).parent)


def _rel(abs_path: str, root: str) -> str | None:
    """Return *abs_path* relative to *root*, or None if outside the root."""
    try:
        return str(Path(abs_path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return None


# ─── Hook handlers ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def handle_session_start(payload: dict[str, Any], codoc_dir: str) -> None:
    """Open a new agent epoch."""
    session_id = payload.get("session_id") or str(int(time.time()))
    _write_activity(codoc_dir, {
        "version": 1,
        "epoch": {"id": f"ep-{session_id}", "open": True, "started_at": _now_iso(), "ended_at": None},
        "touched": [],
    })


def _reopen_epoch_if_needed(payload: dict[str, Any], codoc_dir: str) -> None:
    """Re-open the agent epoch when a turn starts: the rising edge for turns 2+.

    ``SessionStart`` opens the epoch and ``Stop`` closes it at the end of EVERY
    turn, so without a re-opener every turn after the first would run with the
    epoch closed: the daemon would treat the agent's own saves as ordinary edits
    and run reflect passes against half-written code. Tool activity and the
    user's prompt are the turn's rising edge, so they re-open it.

    Called from the PRE phase on purpose: PreToolUse fires before the tool writes
    the file, so the daemon always observes the rising edge no later than the
    code change it must suppress.

    Ownership mirrors :func:`handle_stop`: this session re-opens only its OWN
    closed epoch (turn 2+ of the same session), or claims the slot fresh when the
    closed epoch belongs to a session that is gone (a missed SessionStart). An
    OPEN epoch, ours or a concurrent session's, is never touched.

    ``touched`` resets on re-open: Stop closes each turn and the daemon reflects
    per turn, so each turn starts its own set."""
    data = _read_activity(codoc_dir)
    ep = data.get("epoch") or {}
    if ep.get("open", False):
        return
    sid = payload.get("session_id")
    epoch_id = f"ep-{sid}" if sid else ""
    if ep.get("id") and epoch_id and ep["id"] != epoch_id:
        handle_session_start(payload, codoc_dir)
        return
    ep["open"] = True
    ep["ended_at"] = None
    ep["id"] = ep.get("id") or epoch_id
    ep.setdefault("started_at", _now_iso())
    data["epoch"] = ep
    data["touched"] = []
    data["cited"] = []
    _write_activity(codoc_dir, data)


def handle_stop(payload: dict[str, Any], codoc_dir: str) -> None:
    """Close the epoch; keep ``touched`` so the daemon can read what the turn
    wrote and reflect it.

    ``Stop`` fires at the end of every agent turn. ``SessionEnd`` runs the same
    close, as the backstop for exits that skip ``Stop`` (Esc mid-turn, a closed
    terminal).

    Only the session that opened the current epoch may close it. ``SessionEnd``
    can fire long after the final turn, and by then a newer session may own
    activity.json; closing it would wipe that session's state. A stale close
    skipped here is harmless: the daemon closes an epoch that goes silent.

    With no daemon, nothing applies after the close, and every MCP write of the
    turn landed while the epoch was open, so nothing settled. When something
    is queued, start a detached process that settles once and exits
    (``client.settle_closed_epoch``). The hook does not wait for it."""
    data = _read_activity(codoc_dir)
    ep = data.get("epoch") or {}
    sid = payload.get("session_id")
    if sid and ep.get("id") and ep["id"] != f"ep-{sid}":
        return
    was_open = ep.get("open")
    ep["open"] = False
    ep["ended_at"] = _now_iso()
    data["epoch"] = ep
    _write_activity(codoc_dir, data)
    if was_open and _queue_size(codoc_dir) and not daemon_running(codoc_dir):
        Popen([sys.executable, "-m", "codoc.daemon.client", str(Path(codoc_dir).resolve())],
              stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)


def _handle_tool(payload: dict[str, Any], codoc_dir: str, phase: str) -> None:
    """Add the file a tool call names to the turn's ``touched``. The write
    also renews the epoch's lease (``epoch_alive``)."""
    if phase == "pre":
        # Turn rising edge (see _reopen_epoch_if_needed), before the touch is
        # recorded, so the file lands in the freshly-reset per-turn set.
        _reopen_epoch_if_needed(payload, codoc_dir)
    file_path = (payload.get("tool_input") or {}).get("file_path")
    rel = _rel(file_path, _root_dir(codoc_dir)) if file_path else None
    if rel is None:
        return  # no file, or one outside the repo
    data = _read_activity(codoc_dir)
    touched = list(data.get("touched") or [])
    if rel not in touched:
        touched.append(rel)
    data["touched"] = touched
    _write_activity(codoc_dir, data)


def handle_pre_tool(payload: dict[str, Any], codoc_dir: str) -> None:
    _handle_tool(payload, codoc_dir, phase="pre")


def _queue_size(codoc_dir: str) -> int:
    """Count of `### ` items in realize.md; 0 when absent/unreadable."""
    try:
        text = (Path(codoc_dir) / REALIZE).read_text(encoding="utf-8")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("### "))


def _nudge_if_queue_grew(codoc_dir: str) -> None:
    """Tell the WORKING session, mid-turn, that the realize queue appeared or grew.

    The UserPromptSubmit nudge only fires when the person types their next
    message, so an agent that keeps working after they accept tree edits has no
    other way to learn of them. PostToolUse fires on every tool call, and its
    ``additionalContext`` reaches the model within one tool round-trip. Emits
    ONLY when the count exceeds the last count this hook
    announced (tracked in activity.json), so a static queue nudges once, not on
    every tool call, and a queue the agent is draining (count falling) stays
    silent."""
    n = _queue_size(codoc_dir)
    data = _read_activity(codoc_dir)
    seen = int(data.get("realize_seen") or 0)
    if n == 0:
        if seen:  # queue drained/closed: reset so the NEXT queue nudges again
            data["realize_seen"] = 0
            _write_activity(codoc_dir, data)
        return
    if n <= seen:
        return
    data["realize_seen"] = n
    _write_activity(codoc_dir, data)
    msg = (
        f"codoc: the realize queue grew: {n} change(s) now queued in "
        ".codoc/realize.md (the user accepted or added tree edits while you "
        "work). Read the file and implement each item, then codoc_reflect with "
        "caused_by=<its d-id>; the citation marks it done. Never delete the "
        "queue files."
    )
    out = {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                  "additionalContext": msg}}
    print(json.dumps(out, ensure_ascii=False))


def handle_post_tool(payload: dict[str, Any], codoc_dir: str) -> None:
    _handle_tool(payload, codoc_dir, phase="post")
    try:
        _nudge_if_queue_grew(codoc_dir)
    except Exception:  # noqa: BLE001 (a nudge must never break the turn)
        pass


def handle_user_prompt(payload: dict[str, Any], codoc_dir: str) -> None:
    """Tell the session when hand-offs are queued for it.

    The person's prompt is the earliest rising edge a turn has, so this first
    re-opens the epoch (see ``_reopen_epoch_if_needed``). Then, if
    ``.codoc/realize.md`` exists, it emits a one-line ``additionalContext``
    reminder to run ``/codoc:sync``. Silent when nothing is queued."""
    try:
        _reopen_epoch_if_needed(payload, codoc_dir)
    except Exception:  # noqa: BLE001 (epoch upkeep must never break the turn)
        pass
    realize = Path(codoc_dir) / REALIZE
    if not realize.exists():
        return
    try:
        n = sum(1 for line in realize.read_text(encoding="utf-8").splitlines()
                if line.lstrip().startswith("### "))
    except OSError:
        n = 0
    count = f"{n} change(s)" if n else "changes"
    msg = (
        f"codoc: {count} from accepted tree edits are queued in .codoc/realize.md. "
        "Run /codoc:sync to implement them: read the file, apply each directive "
        "(never touching .codoc/), and call codoc_reflect with caused_by=<the "
        "item's d-id> to bind the code. That citation marks the item done; never "
        "delete the queue files yourself."
    )
    out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}
    print(json.dumps(out, ensure_ascii=False))


def _uncited(codoc_dir: str) -> list[str]:
    """Queued items this turn's MCP calls did not cite, when they cited at
    least one: the agent worked the queue and left some items unreported. A
    turn that never touched the queue is not asked about it. The MCP server
    records each citation on the open epoch (``epoch.note_cited``), with or
    without a daemon, and the epoch's open resets the list."""
    try:
        text = (Path(codoc_dir) / REALIZE).read_text(encoding="utf-8")
    except OSError:
        return []
    queued = re.findall(r"^### ⟨d:([^⟩]+)⟩", text, re.M)
    cited = set(_read_activity(codoc_dir).get("cited") or [])
    if not cited.intersection(queued):
        return []
    return [d for d in queued if d not in cited]


def handle_turn_stop(payload: dict[str, Any], codoc_dir: str) -> None:
    """End of a turn. Settling waits for the epoch to close, so an item the
    agent built but did not report with ``codoc_reflect`` would stay queued
    with nothing to say why. Ask once (``stop_hook_active`` is set on the
    retry), keeping the epoch open while the agent answers."""
    if not payload.get("stop_hook_active"):
        left = _uncited(codoc_dir)
        if left:
            ids = ", ".join(f"d:{d}" for d in left)
            print(json.dumps({"decision": "block", "reason": (
                f"codoc: {len(left)} queued item(s) in .codoc/realize.md have no codoc_reflect "
                f"citing them: {ids}. For each one you built, call codoc_reflect with that id as "
                "caused_by; for one you did not build, say why.")}, ensure_ascii=False))
            return
    handle_stop(payload, codoc_dir)


# ─── Entrypoint ──────────────────────────────────────────────────────────────

_HANDLERS = {
    "session-start": handle_session_start,
    "stop": handle_turn_stop,
    "session-end": handle_stop,
    "pre-tool": handle_pre_tool,
    "post-tool": handle_post_tool,
    "user-prompt": handle_user_prompt,
}


def main(argv: list[str] | None = None) -> int:  # noqa: D103
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        return 0  # no event name → no-op (called without subcommand)

    event_name = args[0]
    handler = _HANDLERS.get(event_name)
    if handler is None:
        return 0  # unknown event → ignore silently

    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        return 0

    cwd = payload.get("cwd") or os.getcwd()
    codoc_dir = _find_codoc_dir(cwd)
    if codoc_dir is None:
        return 0  # repo not codoc-initialized: stay out of the way

    try:
        handler(payload, codoc_dir)
    except Exception:  # noqa: BLE001 (never block the agent)
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
