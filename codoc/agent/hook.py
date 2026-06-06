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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codoc.agent.paths import find_codoc_dir as _find_codoc_dir

ACTIVITY_FILENAME = "activity.json"
BINDINGS_FILENAME = "tree.bindings.json"
REALIZE_FILENAME = "realize.md"

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

def _resolve_features(rel_path: str, codoc_dir: str) -> list[str]:
    """Map a repo-relative file path → feature_ids via ``tree.bindings.json``.

    Reads the sidecar rather than opening the SQLite store to avoid WAL
    contention with the running watch daemon.
    """
    sidecar_path = Path(codoc_dir) / BINDINGS_FILENAME
    try:
        sidecar = json.loads(sidecar_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    by_file: dict = sidecar.get("by_file", {})
    entries = by_file.get(rel_path, [])
    return [e["feature_id"] for e in entries if "feature_id" in e]


# ─── Activity.json atomic writer ─────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_activity(codoc_dir: str) -> dict:
    path = Path(codoc_dir) / ACTIVITY_FILENAME
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {
            "version": 1,
            "epoch": {"id": "", "origin": "interactive", "open": False,
                      "started_at": None, "ended_at": None},
            "touched": {},
            "recent": [],
        }


def _write_activity(codoc_dir: str, data: dict) -> None:
    """Write activity.json atomically (tmp → os.replace).

    Uses a *lock file* so concurrent hook invocations within one CC session
    don't corrupt the JSON.  ``filelock`` is already a project dependency.
    """
    try:
        from filelock import FileLock
        lock_path = Path(codoc_dir) / (ACTIVITY_FILENAME + ".lock")
        lock = FileLock(str(lock_path), timeout=5)
    except ImportError:
        lock = None  # type: ignore[assignment]

    def _do_write() -> None:
        dest = Path(codoc_dir) / ACTIVITY_FILENAME
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, dest)

    if lock is not None:
        with lock:
            _do_write()
    else:
        _do_write()


# ─── Hook handlers ───────────────────────────────────────────────────────────

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
        },
        "touched": {},
        "recent": [],
    }
    _write_activity(codoc_dir, data)


def handle_stop(payload: dict[str, Any], codoc_dir: str) -> None:
    """Close the epoch; keep ``touched`` so the reconciler can read it. Then, for
    an interactive session with no running daemon, spawn a detached reflection so
    code→tree sync happens even without ``codoc watch``."""
    data = _read_activity(codoc_dir)
    ep = data.get("epoch") or {}
    ep["open"] = False
    ep["ended_at"] = _now_iso()
    data["epoch"] = ep
    # The agent stopped → no feature is "being edited now"; clear phases so the
    # doc view settles (any content updates already flowed through the sidecar).
    data["features"] = {}
    _write_activity(codoc_dir, data)
    _maybe_spawn_reflect(codoc_dir, data, ep)


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


def _handle_tool(
    payload: dict[str, Any],
    codoc_dir: str,
    phase: str,   # "pre" | "post"
) -> None:
    """Record a tool-call touch event in activity.json."""
    tool_name = payload.get("tool_name", "")
    tool_input: dict = payload.get("tool_input") or {}
    file_path: str | None = tool_input.get("file_path")
    if not file_path:
        return

    root = _root_dir(codoc_dir)
    rel = _rel(file_path, root)
    if rel is None:
        return  # outside the repo

    mode = "read" if tool_name == "Read" else "write"
    feature_ids = _resolve_features(rel, codoc_dir)

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


def handle_post_tool(payload: dict[str, Any], codoc_dir: str) -> None:
    _handle_tool(payload, codoc_dir, phase="post")


def handle_user_prompt(payload: dict[str, Any], codoc_dir: str) -> None:
    """Nudge the live session when accepted tree edits are queued for realization.

    Loop B hands code-implying tree edits to the session by writing
    ``.codoc/realize.md`` (instead of spawning a headless agent). On each user
    prompt, if that file exists, inject a one-line ``additionalContext`` reminder
    so the session knows to run ``/codoc:realize``. Emits the UserPromptSubmit
    hook JSON on stdout; stays silent (no output) when nothing is queued.

    Daemon-free fallback: if proposal verdicts are sitting unprocessed in the inbox
    (the user accepted a plan but no ``codoc watch`` daemon is draining it), run
    Loop B here first so the acceptance is applied and ``realize.md`` is queued —
    making "accept a plan, then keep working in Claude" close the loop with no
    daemon. When a daemon *is* running we defer to it (it owns the drain)."""
    _drain_inbox_fallback(codoc_dir)
    realize = Path(codoc_dir) / REALIZE_FILENAME
    if not realize.exists():
        return
    try:
        n = sum(1 for line in realize.read_text().splitlines() if line.lstrip().startswith("### "))
    except OSError:
        n = 0
    count = f"{n} change(s)" if n else "changes"
    msg = (
        f"codoc: {count} from accepted tree edits are queued in .codoc/realize.md. "
        "Run /codoc:realize to implement them — read the file, apply each directive "
        "(respecting its `Edit only:` scope and never touching .codoc/), call "
        "codoc_reflect to bind the code, then delete .codoc/realize.md."
    )
    out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}
    print(json.dumps(out))


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
