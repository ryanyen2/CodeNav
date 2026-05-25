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
    from codoc.codoc_file.render import write_tree

    store = open_store(codoc_dir)
    try:
        write_tree(store, codoc_dir)
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
) -> tuple[str, str] | None:
    """Handle one debounced change batch. Returns (label, summary) or None."""
    tp = tree_path(codoc_dir)
    codoc_touched, inbox_touched, activity_touched, code_files = _classify(
        paths, root_dir, codoc_dir
    )

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

    # ── Step 2: Content-hash self-render guard (unchanged). ────────────────────
    if codoc_touched and _hash(tp) == state.last_tree_hash:
        codoc_touched = False

    # ── Step 3: While an epoch is open, suppress independent Loop A. ───────────
    if state.epoch_open:
        if code_files:
            state.suppressed_files |= code_files  # accumulate; agent owns these
        # tree.codoc changes during an epoch (e.g. codoc propose re-render) are
        # codoc's own writes and are absorbed by the hash guard above.  A genuine
        # human tree edit mid-epoch (rare) is allowed through to Loop B below.
        if not (codoc_touched or inbox_touched):
            return None  # pure code churn during epoch → suppressed

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


def run_watch(
    root_dir: str,
    codoc_dir: str,
    *,
    no_realize: bool = False,
    dry_run: bool = False,
    printer=print,
) -> None:  # pragma: no cover - blocking I/O loop
    import watchfiles

    _render(codoc_dir)
    state = WatchState(last_tree_hash=_hash(tree_path(codoc_dir)))
    printer(f"codoc watching {root_dir} — edit code or .codoc/tree.codoc (Ctrl-C to stop)")
    for changes in watchfiles.watch(root_dir, watch_filter=watch_filter(codoc_dir), debounce=600):
        out = process_batch([p for _, p in changes], root_dir, codoc_dir, state,
                            no_realize=no_realize, dry_run=dry_run)
        if out:
            printer(f"▸ {out[0]}  {out[1]}")
