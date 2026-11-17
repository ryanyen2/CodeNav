"""The handler Claude Code's hooks run.

Invoked as ``python -m codoc.claude.hook <event>``, where ``<event>`` is one of
``session-start``, ``stop``, ``session-end``, ``pre-tool``, ``post-tool``,
``user-prompt``. Claude Code passes the hook payload as JSON on stdin.

It does two things:

1. Keeps ``.codoc/activity.json``: the agent epoch (open while a turn is
   running, so the daemon holds its reflect pass until the turn ends) and what
   the session touched, attributed to features through the bindings in the
   published ``projection.json``.
2. Tells the session when hand-offs are queued in ``.codoc/realize.md``, at
   each user prompt and mid-turn when the queue grows.

It never raises and always exits 0: a hook that fails blocks the agent. It
never opens the workspace; it reads published files only.

``CODOC_EPOCH_ORIGIN`` sets the epoch's ``origin`` (``interactive`` by default;
a realize pass sets ``loop_b``) and ``CODOC_AGENT`` names the agent.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codoc.daemon.epoch import read_activity as _read_activity
from codoc.daemon.epoch import write_activity as _write_activity
from codoc.workspace.layout import PROJECTION, REALIZE, read_json
from codoc.workspace.layout import find_codoc_dir as _find_codoc_dir


def _bindings_by_file(codoc_dir: str) -> dict[str, list[dict]]:
    """``file → [{feature_id, symbol}]`` from the published ``projection.json``."""
    proj = read_json(Path(codoc_dir) / PROJECTION, default=None)
    bindings = proj.get("bindings") if isinstance(proj, dict) else None
    out: dict[str, list[dict]] = {}
    for fid, rows in ((bindings or {}).get("by_feature") or {}).items():
        for row in rows:
            out.setdefault(row["file"], []).append({"feature_id": fid, "symbol": row["symbol"]})
    return out


# Maximum recent-events to keep in the rolling log.
_MAX_RECENT = 20


# ─── Filesystem helpers ───────────────────────────────────────────────────────

def _root_dir(codoc_dir: str) -> str:
    return str(Path(codoc_dir).parent)


def _rel(abs_path: str, root: str) -> str | None:
    """Return *abs_path* relative to *root*, or None if outside the root."""
    try:
        return str(Path(abs_path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return None


# ─── File → features ─────────────────────────────────────────────────────────

def _resolve_features(
    rel_path: str,
    codoc_dir: str,
    *,
    tool_name: str | None = None,
    tool_input: dict | None = None,
    abs_path: str | None = None,
) -> list[str]:
    """Map a repo-relative file path → the feature ids bound to it.

    A file can be bound to several features (a shared helper, or every feature
    of a single-file project). When it is, narrow:

    1. **Symbol level** — for an ``Edit``/``MultiEdit`` the exact text being
       changed is known (``old_string``); map it to the enclosing code symbol
       (a tree-sitter parse via :mod:`codoc.index.lang`) and attribute only to
       the feature(s) bound to that symbol.
    2. **File level** — otherwise every feature bound to the file.
    """
    entries = _bindings_by_file(codoc_dir).get(rel_path, [])
    all_fids = list(dict.fromkeys(e["feature_id"] for e in entries if "feature_id" in e))
    if len(all_fids) <= 1:
        return all_fids
    # 1. symbol level — most precise, when this is a locatable edit
    if tool_name and tool_input is not None and abs_path:
        scoped = _symbol_scoped_features(abs_path, rel_path, tool_name, tool_input, entries)
        scoped = [f for f in scoped if f in set(all_fids)]
        if scoped:
            return scoped
    return all_fids


def _symbol_scoped_features(
    abs_path: str, rel_path: str, tool_name: str, tool_input: dict, entries: list,
) -> list[str]:
    """Narrow to the feature(s) whose bound symbol encloses the edited line(s).

    For an ``Edit``/``MultiEdit``, locate each anchor (``old_string``, falling
    back to ``new_string`` when the edit already applied) in the file, map each
    touched line to the INNERMOST bound symbol containing it, and return those
    symbols' features. Returns ``[]`` when it can't attribute at the symbol level
    — unsupported language, anchor not found, or no touched line falls inside a
    bound symbol — so the caller falls back to the file level.
    Best-effort throughout: any parse/IO failure yields ``[]``, never raises.
    """
    edits: list[dict] = []
    if tool_name == "Edit":
        edits = [tool_input]
    elif tool_name == "MultiEdit":
        edits = [e for e in (tool_input.get("edits") or []) if isinstance(e, dict)]
    if not edits:
        return []  # Write (whole-file) / Read (no anchor) → coarser fallback

    try:
        source = Path(abs_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    # The 1-based lines each edit touches, located by its anchor text.
    touched: set[int] = set()
    for e in edits:
        for key in ("old_string", "new_string"):  # prefer old_string; new after apply
            v = e.get(key)
            if not isinstance(v, str) or not v:
                continue
            idx = source.find(v)
            if idx < 0:
                continue
            start_line = source[:idx].count("\n") + 1
            touched.update(range(start_line, start_line + v.count("\n") + 1))
            break
    if not touched:
        return []
    if len(touched) > 500:  # bound the work on a huge multi-symbol edit
        touched = set(sorted(touched)[:500])

    try:
        from codoc.index.lang import detect_language, get_adapter

        lang = detect_language(rel_path)
        if lang is None:
            return []
        chunks = get_adapter(lang).extract_chunks(rel_path, source)
    except Exception:  # noqa: BLE001 — parse is best-effort; never break the hook
        return []

    # symbol_path → feature_ids bound to it (only symbols bound in THIS file)
    sym_to_fids: dict[str, list[str]] = {}
    for e in entries:
        s, fid = e.get("symbol"), e.get("feature_id")
        if s and fid:
            sym_to_fids.setdefault(s, []).append(fid)

    # each BOUND chunk's 1-based inclusive line span
    sb = source.encode("utf-8")
    bound_spans: list[tuple[int, int, str]] = []  # (start_line, end_line, symbol_path)
    for c in chunks:
        if c.symbol_path not in sym_to_fids:
            continue
        start_line = sb[: c.start_byte].count(b"\n") + 1
        end_line = sb[: c.end_byte].count(b"\n") + 1
        bound_spans.append((start_line, end_line, c.symbol_path))
    if not bound_spans:
        return []

    # for each touched line, the innermost (smallest-span) enclosing bound symbol
    fids: list[str] = []
    for line in touched:
        containing = [(en - st, sp) for (st, en, sp) in bound_spans if st <= line <= en]
        if not containing:
            continue
        containing.sort()  # smallest span first = innermost / most specific symbol
        fids.extend(sym_to_fids.get(containing[0][1], []))
    return list(dict.fromkeys(fids))


# ─── Hook handlers ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def handle_session_start(payload: dict[str, Any], codoc_dir: str) -> None:
    """Open a new agent epoch, recording its origin and which agent owns it."""
    session_id = payload.get("session_id") or str(int(time.time()))
    origin = os.environ.get("CODOC_EPOCH_ORIGIN", "interactive")
    epoch_id = f"ep-{session_id}"

    data: dict[str, Any] = {
        "version": 1,
        "epoch": {
            "id": epoch_id,
            "origin": origin,
            "open": True,
            "started_at": _now_iso(),
            "ended_at": None,
            # Which coding agent owns this epoch. An agent that drives codoc
            # exports CODOC_AGENT (claude-code | codex | gemini | cursor | …).
            "agent": {"id": os.environ.get("CODOC_AGENT") or "claude-code"},
        },
        "touched": {},
        "recent": [],
    }
    _write_activity(codoc_dir, data)


def _reopen_epoch_if_needed(payload: dict[str, Any], codoc_dir: str) -> None:
    """Re-open the agent epoch when a turn starts — the rising edge for turns 2+.

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
    OPEN epoch — ours or a concurrent session's — is never touched.

    ``touched`` resets on re-open: Stop closes each turn and the daemon reflects
    per turn, so each turn starts its own write set. ``recent`` (the rolling log
    of tool calls) survives — it is display state, not a write set."""
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
    ep.setdefault("origin", os.environ.get("CODOC_EPOCH_ORIGIN", "interactive"))
    ep.setdefault("agent", {"id": os.environ.get("CODOC_AGENT") or "claude-code"})
    data["epoch"] = ep
    data["touched"] = {}
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
    skipped here is harmless: the daemon closes an epoch that goes silent."""
    data = _read_activity(codoc_dir)
    ep = data.get("epoch") or {}
    sid = payload.get("session_id")
    if sid and ep.get("id") and ep["id"] != f"ep-{sid}":
        return
    ep["open"] = False
    ep["ended_at"] = _now_iso()
    data["epoch"] = ep
    # The agent stopped, so no feature is being edited now.
    data["features"] = {}
    _write_activity(codoc_dir, data)


# Bash actions worth a line in ``recent``: test runs and repo-mutating git
# verbs. Everything else (ls, cat, grep, cd …) is noise.
_TEST_CMD_RE = re.compile(
    r"\b(pytest|vitest|jest|tsc|mypy|ruff|eslint|go test|cargo test|npm (?:run )?test)\b")
_GIT_CMD_RE = re.compile(
    r"(?:^|&&|;|\|)\s*git\s+(commit|push|merge|rebase|revert|cherry-pick|stash)\b")


def _classify_bash(command: str) -> tuple[str, str] | None:
    """(action, label) for a Bash command worth logging, else None."""
    m = _TEST_CMD_RE.search(command or "")
    if m:
        return ("test", f"running {m.group(1).split()[0]}")
    m = _GIT_CMD_RE.search(command or "")
    if m:
        return ("git", f"git {m.group(1)}")
    return None


def _handle_tool(
    payload: dict[str, Any],
    codoc_dir: str,
    phase: str,   # "pre" | "post"
) -> None:
    """Record a tool-call touch event in activity.json."""
    if phase == "pre":
        # Turn rising edge (see _reopen_epoch_if_needed) — before the touch is
        # recorded, so the file lands in the freshly-reset per-turn write set.
        _reopen_epoch_if_needed(payload, codoc_dir)
    tool_name = payload.get("tool_name", "")
    tool_input: dict = payload.get("tool_input") or {}
    if tool_name == "Bash":
        # No file to touch: log the ACTION (tests / git), attributed to the
        # features the agent is editing right now.
        if phase != "pre":
            return
        act = _classify_bash(tool_input.get("command", ""))
        if act is None:
            return
        action, label = act
        data = _read_activity(codoc_dir)
        feats = data.get("features") or {}
        editing = [fid for fid, e in feats.items() if (e or {}).get("phase") == "editing"]
        recent: list = data.get("recent") or []
        recent.append({
            "tool": "Bash", "action": action, "label": label,
            "file": "", "feature_ids": editing, "at": _now_iso(), "phase": phase,
        })
        if len(recent) > _MAX_RECENT:
            recent = recent[-_MAX_RECENT:]
        data["recent"] = recent
        _write_activity(codoc_dir, data)
        return
    file_path: str | None = tool_input.get("file_path")
    if not file_path:
        return

    root = _root_dir(codoc_dir)
    rel = _rel(file_path, root)
    if rel is None:
        return  # outside the repo

    mode = "read" if tool_name == "Read" else "write"
    feature_ids = _resolve_features(
        rel, codoc_dir, tool_name=tool_name, tool_input=tool_input, abs_path=file_path,
    )

    data = _read_activity(codoc_dir)
    touched: dict = data.get("touched") or {}

    entry = touched.get(rel) or {"symbols": [], "feature_ids": [], "last": None, "mode": mode}
    # Upgrade mode: write beats read
    if mode == "write":
        entry["mode"] = "write"
    entry["last"] = _now_iso()
    # Merge feature_ids
    existing_fids: list = entry.get("feature_ids") or []
    merged = list(dict.fromkeys(existing_fids + feature_ids))
    entry["feature_ids"] = merged
    touched[rel] = entry

    # Rolling recent log
    recent: list = data.get("recent") or []
    recent.append({
        "tool": tool_name,
        "file": rel,
        "feature_ids": feature_ids,
        "at": _now_iso(),
        "phase": phase,
    })
    if len(recent) > _MAX_RECENT:
        recent = recent[-_MAX_RECENT:]

    # A write to a bound file means the agent is reworking that feature now:
    # phase "editing", which the editor draws until the reflection lands.
    if mode == "write" and feature_ids:
        feats = data.get("features")
        if not isinstance(feats, dict):
            feats = {}
        for fid in feature_ids:
            feats[fid] = {"phase": "editing", "at": _now_iso()}
        data["features"] = feats

    data["touched"] = touched
    data["recent"] = recent
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
    every tool call — and a queue the agent is draining (count falling) stays
    silent."""
    n = _queue_size(codoc_dir)
    data = _read_activity(codoc_dir)
    seen = int(data.get("realize_seen") or 0)
    if n == 0:
        if seen:  # queue drained/closed — reset so the NEXT queue nudges again
            data["realize_seen"] = 0
            _write_activity(codoc_dir, data)
        return
    if n <= seen:
        return
    data["realize_seen"] = n
    _write_activity(codoc_dir, data)
    msg = (
        f"codoc: the realize queue grew — {n} change(s) now queued in "
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
    except Exception:  # noqa: BLE001 — a nudge must never break the turn
        pass


def handle_user_prompt(payload: dict[str, Any], codoc_dir: str) -> None:
    """Tell the session when hand-offs are queued for it.

    The person's prompt is the earliest rising edge a turn has, so this first
    re-opens the epoch (see ``_reopen_epoch_if_needed``). Then, if
    ``.codoc/realize.md`` exists, it emits a one-line ``additionalContext``
    reminder to run ``/codoc:sync``. Silent when nothing is queued."""
    try:
        _reopen_epoch_if_needed(payload, codoc_dir)
    except Exception:  # noqa: BLE001 — epoch upkeep must never break the turn
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
        "Run /codoc:sync to implement them — read the file, apply each directive "
        "(respecting its `Edit only:` scope and never touching .codoc/), and call "
        "codoc_reflect with caused_by=<the item's d-id> to bind the code. That "
        "citation marks the item done; never delete the queue files yourself."
    )
    out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}
    print(json.dumps(out, ensure_ascii=False))


# ─── Entrypoint ──────────────────────────────────────────────────────────────

_HANDLERS = {
    "session-start": handle_session_start,
    "stop": handle_stop,
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
        return 0  # repo not codoc-initialized — stay out of the way

    try:
        handler(payload, codoc_dir)
    except Exception:  # noqa: BLE001 — never block the agent
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
