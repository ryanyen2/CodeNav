"""Claude Code hook handler for the codoc agent-epoch system.

Invoked by CC hooks as ``python -m codoc.agent.hook <event>``, where ``<event>``
is one of ``session-start``, ``stop``, ``pre-tool``, ``post-tool``,
``user-prompt``.  CC passes the hook payload as JSON on **stdin**.

This handler:

1. Discovers the ``.codoc`` directory by walking up from ``cwd``.
2. Reads ``tree.bindings.json`` (the sidecar) to map touched files → feature ids.
3. Atomically writes/updates ``.codoc/activity.json`` (epoch state + touch log).

**Contracts:**

* Never raises — wrap everything in try/except; always ``sys.exit(0)``.  A hook
  that exits non-zero blocks the agent.
* Never opens the SQLite store (``codoc.db``) — uses the sidecar only, to avoid
  WAL contention with the running daemon.
* The ``CODOC_EPOCH_ORIGIN`` env var, if set to ``"loop_b"``, marks a
  non-interactive agent-owned epoch so the watch daemon skips independent
  reconciliation; codoc sessions are ``"interactive"`` by default. (Loop B no
  longer spawns a headless agent — it queues directives in ``.codoc/realize.md``
  for the live session, surfaced by the ``user-prompt`` handler below.)
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

from codoc.agent.paths import find_codoc_dir as _find_codoc_dir

from codoc.loop.activity import read_activity as _read_activity
from codoc.loop.activity import write_activity as _write_activity
from codoc.loop.filenames import REALIZE_FILENAME
from codoc.loop.fsio import read_json

BINDINGS_FILENAME = "tree.bindings.json"

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


# ─── Sidecar reader ──────────────────────────────────────────────────────────

def _resolve_features(
    rel_path: str,
    codoc_dir: str,
    *,
    tool_name: str | None = None,
    tool_input: dict | None = None,
    abs_path: str | None = None,
) -> list[str]:
    """Map a repo-relative file path → feature_ids via ``tree.bindings.json``.

    Reads the sidecar rather than opening the SQLite store to avoid WAL
    contention with the running watch daemon.

    A file can be bound to several features (e.g. a shared helper, or — in a
    single-file project — every feature). When it is, narrow in priority order:

    1. **Symbol level** — for an ``Edit``/``MultiEdit`` we know the exact text
       being changed (``old_string``); map it to the enclosing code symbol
       (tree-sitter parse via :mod:`codoc.lang`, no store access) and attribute
       only to the feature(s) bound to that symbol. This is the ground truth of
       "what's being edited" and works even with no realize session.
    2. **Directive level** — otherwise, whichever candidate feature has a
       handed-off realize directive bound to this file (the one being realized).
    3. **File level** — otherwise the full set (ad hoc reads/edits with no finer
       signal; showing every bound feature is the honest fallback).
    """
    sidecar = read_json(Path(codoc_dir) / BINDINGS_FILENAME, default={})
    by_file: dict = sidecar.get("by_file", {})
    entries = by_file.get(rel_path, [])
    all_fids = list(dict.fromkeys(e["feature_id"] for e in entries if "feature_id" in e))
    if len(all_fids) <= 1:
        return all_fids
    # 1. symbol level — most precise, when this is a locatable edit
    if tool_name and tool_input is not None and abs_path:
        scoped = _symbol_scoped_features(abs_path, rel_path, tool_name, tool_input, entries)
        scoped = [f for f in scoped if f in set(all_fids)]
        if scoped:
            return scoped
    # 2. directive level
    narrowed = _realizing_features_for_file(rel_path, codoc_dir, sidecar, all_fids)
    return narrowed if narrowed else all_fids


def _symbol_scoped_features(
    abs_path: str, rel_path: str, tool_name: str, tool_input: dict, entries: list,
) -> list[str]:
    """Narrow to the feature(s) whose bound symbol encloses the edited line(s).

    For an ``Edit``/``MultiEdit``, locate each anchor (``old_string``, falling
    back to ``new_string`` when the edit already applied) in the file, map each
    touched line to the INNERMOST bound symbol containing it, and return those
    symbols' features. Returns ``[]`` when it can't attribute at the symbol level
    — unsupported language, anchor not found, or no touched line falls inside a
    bound symbol — so the caller falls back to the coarser directive/file level.
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
        from codoc import settings_files
        from codoc.lang import detect_language, get_adapter

        lang = detect_language(rel_path)
        if lang is not None:
            chunks = get_adapter(lang).extract_chunks(rel_path, source)
        elif settings_files.detect_format(rel_path) is not None:
            # A settings file has sections rather than symbols, and an agent that
            # changed one changed one decision — the same narrowing a function edit
            # gets, and the reason the sections are addressable at all.
            chunks = settings_files.extract_chunks(rel_path, source)
        else:
            return []
    except Exception:  # noqa: BLE001 — parse is best-effort; never break the hook
        return []

    # symbol_path → feature_ids bound to it (only symbols in THIS file's sidecar slice)
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


def _realizing_features_for_file(
    rel_path: str, codoc_dir: str, sidecar: dict, candidate_fids: list[str],
) -> list[str]:
    """Of ``candidate_fids`` (all bound to ``rel_path``), return those with a
    handed-off realize directive that is ITSELF bound to ``rel_path`` (via the
    sidecar's ``by_feature`` index) — the feature(s) actually being realized.
    Best-effort: any failure (missing/corrupt manifest) skips narrowing."""
    try:
        from codoc.loop.edits import read_manifest

        candidates = set(candidate_fids)
        directive_fids = {d.feature_id for d in read_manifest(codoc_dir)
                           if d.handed_off and d.feature_id in candidates}
    except Exception:  # noqa: BLE001 — best-effort disambiguation, never break the hook
        return []
    if not directive_fids:
        return []
    by_feature: dict = sidecar.get("by_feature", {})
    return [fid for fid in candidate_fids if fid in directive_fids
            and rel_path in {e.get("file") for e in by_feature.get(fid, [])}]


# ─── Hook handlers ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def handle_session_start(payload: dict[str, Any], codoc_dir: str) -> None:
    """Open a new agent epoch, recording the origin (interactive vs loop_b)."""
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
            # W1: which coding agent owns this epoch, so presence/ribbon/blame
            # attribute to the real agent instead of a hardcoded "Claude". Any
            # agent that drives codoc exports CODOC_AGENT (claude-code | codex |
            # gemini | cursor | …); default is Claude Code. Additive — a reader
            # that predates this key still works (falls back to the default).
            "agent": {"id": os.environ.get("CODOC_AGENT") or "claude-code"},
        },
        "touched": {},
        "recent": [],
    }
    _write_activity(codoc_dir, data)


def _reopen_epoch_if_needed(payload: dict[str, Any], codoc_dir: str) -> None:
    """Re-open the agent epoch when a turn starts — the rising edge for turns 2+.

    ``SessionStart`` opens the epoch and ``Stop`` closes it at the end of EVERY
    turn, so without a re-opener every turn after the first ran with the epoch
    closed: the watch daemon treated the agent's own saves as ordinary edits and
    ran full LLM drift passes against half-written code mid-implementation
    (duplicate nodes for code whose reflect hadn't landed, ``__module__``
    leftovers, re-amends of prose the agent had just written). Tool activity and
    the user's prompt are the turn's rising edge, so they re-open it.

    Called from the PRE phase on purpose: PreToolUse fires before the tool writes
    the file, so the daemon always observes the rising edge no later than the
    code change it must suppress.

    Ownership mirrors :func:`handle_stop`: this session re-opens only its OWN
    closed epoch (turn 2+ of the same session), or claims the slot fresh when the
    closed epoch belongs to a session that is gone (a missed SessionStart). An
    OPEN epoch — ours or a concurrent session's — is never touched.

    ``touched`` resets on re-open: Stop closes each turn and both of its
    consumers (the falling-edge scoped reconcile, the no-daemon Stop reflect) are
    per-turn, so each turn starts its own write set. ``recent`` (the ribbon's
    narration history) survives — it is display state, not a write set."""
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


def handle_stop(payload: dict[str, Any], codoc_dir: str, *, event: str = "stop") -> None:
    """Close the epoch; keep ``touched`` so the reconciler can read it. Then, for
    an interactive session with no running daemon, spawn a detached reflection so
    code→tree sync happens even without ``codoc watch``.

    ``Stop`` fires at the end of EVERY agent turn (not only the last), so the
    ``stop`` event always reflects the turn's writes — turn 1's Stop already
    closed the epoch, and turns 2+ must not lose their reflection to that.
    ``SessionEnd`` (``event="session_end"``) is the backstop for exits that skip
    Stop (Esc mid-turn, terminal close): it reflects only when the epoch is
    still open — after a clean exit whose final Stop already reflected, it
    skips, so a graceful exit never launches two ``codoc reflect`` subprocesses
    racing each other over the same write set.

    Ownership guard: only the session that opened the current epoch may close
    it. ``SessionEnd`` can fire long after the final turn — by then a NEWER
    session may own activity.json, and closing it would wipe that session's
    live state and reflect over its write set. A stale close skipped here is
    harmless: the epoch lease (``epoch_alive``) expires it on its own."""
    data = _read_activity(codoc_dir)
    ep = data.get("epoch") or {}
    sid = payload.get("session_id")
    if sid and ep.get("id") and ep["id"] != f"ep-{sid}":
        return
    already_closed = not ep.get("open", False)
    ep["open"] = False
    ep["ended_at"] = _now_iso()
    data["epoch"] = ep
    # The agent stopped → no feature is "being edited now"; clear phases so the
    # doc view settles (any content updates already flowed through the sidecar).
    data["features"] = {}
    _write_activity(codoc_dir, data)
    if event == "stop" or not already_closed:
        _maybe_spawn_reflect(codoc_dir, data, ep)


def handle_session_end(payload: dict[str, Any], codoc_dir: str) -> None:
    """``SessionEnd`` → same close as ``stop``, but reflect only if the final
    ``Stop`` didn't already handle it (see :func:`handle_stop`)."""
    handle_stop(payload, codoc_dir, event="session_end")


def _maybe_spawn_reflect(codoc_dir: str, data: dict, ep: dict) -> None:
    """Fire-and-forget a ``codoc reflect`` on the files this session wrote.

    Skipped when: a Loop B-owned epoch (Loop B reflects itself), a live ``codoc
    watch`` daemon owns the repo (it reconciles on epoch close — avoids a double
    run / index race), the user opted out, or nothing was written."""
    import os
    import subprocess
    import sys

    if os.environ.get("CODOC_NO_STOP_REFLECT"):
        return
    if ep.get("origin") == "loop_b":
        return
    try:
        from codoc.loop.watch import daemon_running
        if daemon_running(codoc_dir):
            return
    except Exception:  # noqa: BLE001
        pass  # if we can't tell, err toward reflecting

    touched: dict = data.get("touched") or {}
    write_files = [rel for rel, e in touched.items() if (e or {}).get("mode") == "write"]
    if not write_files:
        return

    root = _root_dir(codoc_dir)
    cmd = [sys.executable, "-m", "codoc.cli.main", "reflect",
           "--root", root, "--scope", ",".join(write_files)]
    try:
        subprocess.Popen(  # detached: outlives this hook + the agent session
            cmd, cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:  # noqa: BLE001 — never block the agent's stop
        pass


# Bash actions worth narrating in the agent ribbon (P2b / W1): test runs and
# repo-mutating git verbs. Everything else (ls, cat, grep, cd …) is noise the
# ribbon must not drown in.
_TEST_CMD_RE = re.compile(
    r"\b(pytest|vitest|jest|tsc|mypy|ruff|eslint|go test|cargo test|npm (?:run )?test)\b")
_GIT_CMD_RE = re.compile(
    r"(?:^|&&|;|\|)\s*git\s+(commit|push|merge|rebase|revert|cherry-pick|stash)\b")


def _classify_bash(command: str) -> tuple[str, str] | None:
    """(action, ribbon label) for a Bash command worth showing, else None."""
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
        # No file to touch — narrate the ACTION (tests / git) in the ribbon,
        # attributed to the features the agent is editing right now.
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

    # A write to a bound file means the agent is reworking that feature now →
    # phase "editing" so the IDE doc view shows a skeleton until reflection lands.
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
        text = (Path(codoc_dir) / REALIZE_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("### "))


def _nudge_if_queue_grew(codoc_dir: str) -> None:
    """Tell the WORKING session, mid-turn, that the realize queue appeared or grew.

    The UserPromptSubmit nudge only fires when the human types their next
    message — so a user who accepted tree edits and then watched the agent keep
    working saw "N to implement" in the status bar while the agent literally had
    no way to learn it (observed live in the first pilot). PostToolUse fires on
    every tool call, and its ``additionalContext`` reaches the model within one
    tool round-trip. Emits ONLY when the count exceeds the last count this hook
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
    """Nudge the live session when accepted tree edits are queued for realization.

    Loop B hands code-implying tree edits to the session by writing
    ``.codoc/realize.md`` (instead of spawning a headless agent). On each user
    prompt, if that file exists, inject a one-line ``additionalContext`` reminder
    so the session knows to run ``/codoc:sync``. Emits the UserPromptSubmit
    hook JSON on stdout; stays silent (no output) when nothing is queued.

    Daemon-free fallback: if proposal verdicts are sitting unprocessed in the inbox
    (the user accepted a plan but no ``codoc watch`` daemon is draining it), run
    Loop B here first so the acceptance is applied and ``realize.md`` is queued —
    making "accept a plan, then keep working in Claude" close the loop with no
    daemon. When a daemon *is* running we defer to it (it owns the drain)."""
    try:
        # The user's prompt is the earliest rising edge a turn has — re-open the
        # epoch here so the daemon suppresses this turn's saves even before the
        # first tool call (see _reopen_epoch_if_needed).
        _reopen_epoch_if_needed(payload, codoc_dir)
    except Exception:  # noqa: BLE001 — epoch upkeep must never break the turn
        pass
    try:
        # Capture the prompt itself — the author's stated intent — so the
        # reflection that follows this session's writes can attribute the WHY
        # of the change (see codoc.loop.intent).
        from codoc.loop.intent import record_intent
        record_intent(codoc_dir, payload.get("session_id") or "",
                      payload.get("prompt") or "")
    except Exception:  # noqa: BLE001 — capture is advisory; never break the turn
        pass
    _drain_inbox_fallback(codoc_dir)
    realize = Path(codoc_dir) / REALIZE_FILENAME
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


def _drain_inbox_fallback(codoc_dir: str) -> None:
    """Apply any pending inbox verdicts via Loop B when no daemon owns this repo.

    Best-effort and silent: a hook must never fail the user's turn, so all errors
    are swallowed. No-ops when the inbox is empty or a live ``codoc watch`` daemon
    is already responsible for draining it."""
    from codoc.loop import inbox
    from codoc.loop.watch import daemon_running

    try:
        if daemon_running(codoc_dir) or not inbox.read_verdicts(codoc_dir):
            return
        from codoc.loop.loop_b import run_loop_b
        run_loop_b(str(Path(codoc_dir).parent), codoc_dir)
    except Exception:  # noqa: BLE001 — fallback is advisory; never break the prompt
        pass


# ─── Entrypoint ──────────────────────────────────────────────────────────────

_HANDLERS = {
    "session-start": handle_session_start,
    "stop": handle_stop,
    "session-end": handle_session_end,
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
