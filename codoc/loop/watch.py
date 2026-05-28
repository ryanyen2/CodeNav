"""The watch daemon — one process running both loops.

Routing: a ``tree.codoc`` edit runs Loop B first (authored intent leads; Loop B
itself re-reflects what the agent wrote); any other source-file change runs
Loop A. Indexing artifacts under ``.codoc`` are filtered out so they never
self-trigger, and a content-hash guard ignores codoc's own re-render of
``tree.codoc`` so the two loops can't ping-pong.

**Agent epoch support:** when a Claude Code session is active (detected via
``.codoc/activity.json``), the daemon accumulates code-file changes rather than
running Loop A on each one — the agent owns those files.  A single scoped Loop A
fires when the epoch closes (interactive origin only; the ``loop_b`` origin is
handled by Loop B's own reflect step).

``process_batch`` is the pure, testable per-cycle step (loops injectable);
``run_watch`` wires it to ``watchfiles``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from codoc.codoc_file.render import tree_path
from codoc.loop import status
from codoc.loop.activity import ACTIVITY_FILENAME, activity_path, epoch_touched_files
from codoc.loop.inbox import inbox_path
from codoc.loop.loop_a import run_loop_a
from codoc.loop.loop_b import run_loop_b
from codoc.store.db import open_store

CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".cpp", ".c", ".h"}
_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache", ".codoc"}

# An epoch with no activity.json write in this long is treated as dead (the agent
# was hard-killed without firing the Stop hook), so the daemon recovers instead of
# suppressing forever.
EPOCH_STALE_SECONDS = 900


@dataclass
class WatchState:
    last_tree_hash: str = ""
    # Agent epoch state — managed by the process_batch epoch-transition logic.
    epoch_open: bool = False
    epoch_origin: str = ""       # "interactive" | "loop_b"
    last_epoch_id: str = ""      # prevents double-processing the same closed epoch
    suppressed_files: set[str] = field(default_factory=set)


def _hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _is_code(path: Path) -> bool:
    return path.suffix in CODE_EXTENSIONS


def _classify(
    paths: list[str],
    root_dir: str,
    codoc_dir: str,
) -> tuple[bool, bool, bool, set[str]]:
    """Classify a batch of changed paths.

    Returns ``(codoc_touched, inbox_touched, activity_touched, code_files)``.
    ``activity_touched`` is True when ``.codoc/activity.json`` changed — the
    epoch-control signal consumed by :func:`process_batch` step 1.
    """
    tp = tree_path(codoc_dir).resolve()
    ip = inbox_path(codoc_dir).resolve()
    ap = activity_path(codoc_dir).resolve()
    root = Path(root_dir).resolve()
    codoc_touched = False
    inbox_touched = False
    activity_touched = False
    code_files: set[str] = set()
    for p in paths:
        rp = Path(p).resolve()
        if rp == tp:
            codoc_touched = True
            continue
        if rp == ip:
            inbox_touched = True
            continue
        if rp == ap:
            activity_touched = True
            continue
        if any(part in _SKIP_DIRS for part in rp.parts):
            continue
        if _is_code(rp):
            try:
                code_files.add(str(rp.relative_to(root)))
            except ValueError:
                pass
    return codoc_touched, inbox_touched, activity_touched, code_files


def _pidfile(codoc_dir: str) -> Path:
    return Path(codoc_dir) / "watch.pid"


def write_pidfile(codoc_dir: str) -> None:
    import os
    _pidfile(codoc_dir).write_text(str(os.getpid()))


def clear_pidfile(codoc_dir: str) -> None:
    try:
        _pidfile(codoc_dir).unlink()
    except OSError:
        pass


def daemon_running(codoc_dir: str) -> bool:
    """True if a live ``codoc watch`` daemon owns this repo (pidfile + live pid).

    Lets the Stop hook decide whether to reflect itself (no daemon) or defer to the
    daemon's epoch-close reconcile — so the two never double-run on one epoch."""
    import os
    try:
        pid = int(_pidfile(codoc_dir).read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)  # signal 0 = liveness probe, doesn't actually signal
        return True
    except OSError:
        return False  # stale pidfile → no live daemon


def _epoch_stale(codoc_dir: str, now: float, *, threshold: float = EPOCH_STALE_SECONDS) -> bool:
    """True if activity.json hasn't been written in ``threshold`` seconds.

    A live agent epoch writes activity.json on every tool call (and at least at
    SessionStart); silence this long means the session died without a clean Stop."""
    try:
        return (now - activity_path(codoc_dir).stat().st_mtime) > threshold
    except OSError:
        return False


def _read_epoch(codoc_dir: str) -> dict | None:
    """Read the ``epoch`` block from activity.json, or None if absent / corrupt."""
    path = activity_path(codoc_dir)
    try:
        data = json.loads(path.read_text())
        ep = data.get("epoch")
        if ep and ep.get("id"):
            return ep
    except (OSError, json.JSONDecodeError):
        pass
    return None


def watch_filter(codoc_dir: str):
    """A watchfiles filter: allow tree.codoc, inbox.json, activity.json, and code
    files; drop everything else (notably .codoc indexing artifacts that churn
    during update_index, and codoc's own status.json/sidecar re-writes)."""
    tp = tree_path(codoc_dir).resolve()
    ip = inbox_path(codoc_dir).resolve()
    ap = activity_path(codoc_dir).resolve()

    def _f(_change, path: str) -> bool:
        rp = Path(path).resolve()
        if rp in (tp, ip, ap):
            return True
        if any(part in _SKIP_DIRS for part in rp.parts):
            return False
        return _is_code(rp)

    return _f


def _render(codoc_dir: str) -> None:
    """Non-destructive render: never overwrite un-applied human edits (H1)."""
    from codoc.loop.reconcile import safe_write_tree

    store = open_store(codoc_dir)
    try:
        safe_write_tree(store, codoc_dir)
    finally:
        store.close()


def process_batch(
    paths: list[str],
    root_dir: str,
    codoc_dir: str,
    state: WatchState,
    *,
    no_realize: bool = False,
    dry_run: bool = False,
    loop_a=run_loop_a,
    loop_b=run_loop_b,
    render=_render,
    has_user_edits=None,
    now=None,
) -> tuple[str, str] | None:
    """Handle one debounced change batch. Returns (label, summary) or None."""
    if has_user_edits is None:
        from codoc.loop.reconcile import has_pending_user_edits as has_user_edits
    if now is None:
        import time as _time
        now = _time.time
    tp = tree_path(codoc_dir)
    codoc_touched, inbox_touched, activity_touched, code_files = _classify(
        paths, root_dir, codoc_dir
    )

    # ── Step 0: Stale-epoch recovery. A hard-killed agent (no Stop hook) leaves
    # the epoch open, which would suppress all loops forever. Detect silence and
    # recover by closing it; for an interactive epoch, fold its suppressed +
    # touched files into this batch so the normal Loop A routing reconciles them. ─
    if state.epoch_open and _epoch_stale(codoc_dir, now()):
        if state.epoch_origin == "interactive" and not no_realize:
            code_files |= state.suppressed_files | set(epoch_touched_files(codoc_dir))
        state.epoch_open = False
        state.epoch_origin = ""
        state.suppressed_files.clear()
        stale_ep = _read_epoch(codoc_dir)
        if stale_ep:  # don't let step 1 reprocess this dead epoch
            state.last_epoch_id = stale_ep.get("id", state.last_epoch_id)

    # ── Step 1: Epoch transitions from activity.json (control only; never starts
    # a loop directly). ─────────────────────────────────────────────────────────
    if activity_touched:
        ep = _read_epoch(codoc_dir)
        if ep is not None:
            ep_id: str = ep.get("id", "")
            ep_open: bool = bool(ep.get("open"))
            ep_origin: str = ep.get("origin", "interactive")

            if ep_open and not state.epoch_open:
                # RISING EDGE — a new agent session just started.
                state.epoch_open = True
                state.epoch_origin = ep_origin
                state.last_epoch_id = ep_id
                state.suppressed_files.clear()

            elif state.epoch_open and not ep_open and ep_id == state.last_epoch_id:
                # FALLING EDGE — the epoch we were tracking just closed.
                state.epoch_open = False
                if state.epoch_origin == "interactive" and not no_realize:
                    # Interactive session ended: reconcile all suppressed +
                    # epoch-touched files in one scoped Loop A pass.
                    ep_touched = set(epoch_touched_files(codoc_dir))
                    touched = state.suppressed_files | ep_touched
                    state.suppressed_files.clear()
                    state.epoch_origin = ""
                    if touched:
                        res = loop_a(root_dir, codoc_dir, file_scope=touched)
                        render(codoc_dir)
                        state.last_tree_hash = _hash(tp)
                        return "agent→codoc", f"(epoch {len(touched)} files) {res.summary()}"
                else:
                    # loop_b origin: Loop B's own reflect owns these files.
                    state.suppressed_files.clear()
                    state.epoch_origin = ""

            elif not ep_open and not state.epoch_open and ep_id != state.last_epoch_id:
                # MISSED loop_b epoch — the daemon was blocked during Loop B's
                # synchronous spawn, so it never saw the epoch open/close.
                # Loop B already reflected; exclude epoch files from code_files
                # to avoid a redundant (idempotent, but wasteful) Loop A pass.
                if ep_origin == "loop_b":
                    ep_touched = set(epoch_touched_files(codoc_dir))
                    code_files -= ep_touched
                state.last_epoch_id = ep_id

        # If no other signal co-occurs, this was a pure activity churn → no-op.
        if not (codoc_touched or inbox_touched or code_files):
            return None

    # ── Step 2: Ignore codoc's own / the agent's MCP re-renders. ───────────────
    # The real question isn't "did the bytes change" (a hash can't see an MCP write
    # done by another process) but "does the file hold un-applied USER intent". If
    # diff_codoc is empty, this tree.codoc write came from codoc rendering the store
    # (daemon render or an agent MCP reflection) → not a human edit → don't route to
    # Loop B. The hash is kept only as a cheap fast-path for the unchanged case.
    if codoc_touched:
        if _hash(tp) == state.last_tree_hash or not has_user_edits(codoc_dir):
            codoc_touched = False

    # ── Step 3: While an epoch is open, suppress independent Loop A AND Loop B. ──
    if state.epoch_open:
        if code_files:
            state.suppressed_files |= code_files  # accumulate; agent owns these
        # A tree.codoc change mid-epoch is the agent's own reflection (it calls the
        # codoc MCP tools, which `write_tree` directly — NOT through this daemon, so
        # the hash guard above can't catch it). Routing it to Loop B would spawn a
        # nested coding agent to "implement" what the agent just reflected. Suppress
        # it; the epoch-close scoped Loop A reconciles everything. (A genuine human
        # tree edit mid-session is rare and is deferred to epoch close.)
        codoc_touched = False
        if not inbox_touched:
            return None  # code churn / agent reflection during epoch → suppressed

    # ── Step 4: Normal routing (unchanged). ────────────────────────────────────
    # A tree edit or an Accept/Reject verdict both drive Loop B (codoc → code).
    if codoc_touched or inbox_touched:
        if codoc_touched:
            status.write_status(codoc_dir, status.TREE_DIRTY, detail="applying tree edits")
        res = loop_b(root_dir, codoc_dir, dry_run=dry_run or no_realize)
        label, summary = "codoc→code", res.summary()
    elif code_files:
        res = loop_a(root_dir, codoc_dir, file_scope=code_files)
        label, summary = "code→codoc", f"({len(code_files)} files) {res.summary()}"
    else:
        return None

    render(codoc_dir)
    state.last_tree_hash = _hash(tp)
    return label, summary


def safe_process_batch(
    paths: list[str],
    root_dir: str,
    codoc_dir: str,
    state: WatchState,
    *,
    no_realize: bool = False,
    dry_run: bool = False,
    printer=print,
    _process=None,
) -> tuple[str, str] | None:
    """Run one batch but never let an exception kill the daemon.

    A failing cycle (an LLM error, a transient index read, a bad tree edit) must
    log and be survived — otherwise the watch loop unwinds and the daemon dies
    silently, which is exactly how a code change can vanish with no trace."""
    proc = _process or process_batch
    try:
        return proc(paths, root_dir, codoc_dir, state,
                    no_realize=no_realize, dry_run=dry_run)
    except Exception as e:  # noqa: BLE001 — resilience over correctness for one cycle
        import traceback
        printer(f"⚠ codoc cycle error (daemon continues): {e}")
        traceback.print_exc()
        return None


def run_watch(
    root_dir: str,
    codoc_dir: str,
    *,
    no_realize: bool = False,
    dry_run: bool = False,
    printer=print,
) -> None:  # pragma: no cover - blocking I/O loop
    import atexit

    import watchfiles

    from codoc.loop.loop_a import reconcile_drift

    write_pidfile(codoc_dir)  # let the Stop hook know a daemon owns this repo
    atexit.register(clear_pidfile, codoc_dir)

    _render(codoc_dir)
    state = WatchState(last_tree_hash=_hash(tree_path(codoc_dir)))

    # Startup drift reconcile: catch any code↔tree divergence that accumulated
    # while the daemon was down (or that a previously-crashed cycle missed). This
    # is what makes a (re)started daemon self-heal instead of sitting blind.
    if not no_realize:
        try:
            res = reconcile_drift(root_dir, codoc_dir)
            if res.proposed or res.applied_structural or res.auto:
                printer(f"▸ startup reconcile  {res.summary()}")
        except Exception as e:  # noqa: BLE001
            printer(f"⚠ startup reconcile failed (continuing to watch): {e}")

    printer(f"codoc watching {root_dir} — edit code or .codoc/tree.codoc (Ctrl-C to stop)")
    for changes in watchfiles.watch(root_dir, watch_filter=watch_filter(codoc_dir), debounce=600):
        out = safe_process_batch([p for _, p in changes], root_dir, codoc_dir, state,
                                 no_realize=no_realize, dry_run=dry_run, printer=printer)
        if out:
            printer(f"▸ {out[0]}  {out[1]}")
