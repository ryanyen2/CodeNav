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
from dataclasses import dataclass, field
from pathlib import Path

from codoc.codoc_file.doc_parse import doc_path
from codoc.codoc_file.render import tree_path
from codoc.loop import status
from codoc.loop.activity import activity_path, close_epoch, epoch_touched_files
from codoc.loop.edits import edits_path, host_ops_path
from codoc.loop.fsio import read_json
from codoc.loop.inbox import host_verdicts_path, inbox_path
from codoc.loop.loop_a import reconcile_drift
from codoc.loop.loop_b import run_loop_b
from codoc.store.db import open_store

CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".cpp", ".c", ".h"}
_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache", ".codoc"}

# An epoch with no activity.json write in this long is treated as dead (the agent
# was hard-killed without firing the Stop hook), so the daemon recovers instead of
# suppressing forever.
EPOCH_STALE_SECONDS = 900

# Batch window for filesystem events: rapid-fire saves coalesce into one pass.
DEBOUNCE_MS = 600

# How often the watch loop wakes when idle to re-check the spawning parent's
# liveness (only relevant when CODOC_WATCH_PARENT_PID is set by the extension).
PARENT_POLL_MS = 3000


@dataclass
class WatchState:
    last_tree_hash: str = ""
    # Self-write guards for the control files Loop B itself drains/clears:
    # edits.json (annotation/steer/cancellation lists) and inbox.json (verdicts).
    # Both are watched files, so Loop B clearing them is a filesystem event that
    # would otherwise re-trigger a redundant no-op Loop B pass — the visible
    # "edits 1" then "edits 0" double-fire. We record their post-Loop-B hash and
    # ignore a batch whose only new signal is that self-write (mirrors last_tree_hash).
    last_edits_hash: str = ""
    last_inbox_hash: str = ""
    # Self-write guard for the IDE→daemon host-op log (edits.host.jsonl): Loop B's merge
    # consumes it (renames it away), which is a filesystem event that would otherwise
    # re-route into a no-op Loop B. Record its post-pass hash ("" once consumed) so the
    # daemon's own consumption isn't mistaken for a fresh IDE append.
    last_host_hash: str = ""
    # Same guard for the IDE's verdict append-log (inbox.host.jsonl): the merge on
    # read consumes it, and that unlink must not re-route into a no-op Loop B.
    last_inbox_host_hash: str = ""
    # Agent epoch state — managed by the process_batch epoch-transition logic.
    epoch_open: bool = False
    epoch_origin: str = ""       # "interactive" | "loop_b"
    last_epoch_id: str = ""      # prevents double-processing the same closed epoch
    suppressed_files: set[str] = field(default_factory=set)
    # --auto-realize: a headless `claude -p /codoc:sync` we launched and that
    # hasn't finished draining realize.md yet (prevents stacking duplicate passes).
    realize_proc: object = None


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
) -> tuple[bool, bool, bool, bool, bool, set[str]]:
    """Classify a batch of changed paths.

    Returns ``(codoc_touched, doc_touched, inbox_touched, edits_touched,
    activity_touched, code_files)``. ``doc_touched`` is True when
    ``.codoc/tree.doc.json`` changed — a webview edit in the single-writer model
    (U2b), routed to Loop B exactly like a ``tree.codoc`` edit. ``activity_touched``
    is the epoch-control signal (step 1); ``edits_touched`` covers
    annotations/suggestions/withdrawals/comment-steers in ``edits.json``.
    """
    tp = tree_path(codoc_dir).resolve()
    dp = doc_path(codoc_dir).resolve()
    ip = inbox_path(codoc_dir).resolve()
    ivp = host_verdicts_path(codoc_dir).resolve()
    ep = edits_path(codoc_dir).resolve()
    hp = host_ops_path(codoc_dir).resolve()
    ap = activity_path(codoc_dir).resolve()
    root = Path(root_dir).resolve()
    codoc_touched = False
    doc_touched = False
    inbox_touched = False
    edits_touched = False
    activity_touched = False
    code_files: set[str] = set()
    for p in paths:
        rp = Path(p).resolve()
        if rp == tp:
            codoc_touched = True
            continue
        if rp == dp:
            doc_touched = True
            continue
        if rp == ip or rp == ivp:
            # inbox.json OR the IDE's verdict append-log (inbox.host.jsonl): both are
            # verdicts for Loop B (the log is merged into inbox.json on first read).
            inbox_touched = True
            continue
        if rp == ep or rp == hp:
            # edits.json OR the IDE's host-op append log (edits.host.jsonl): both are
            # webview intent for Loop B (the log is merged into edits.json at pass start).
            edits_touched = True
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
    return codoc_touched, doc_touched, inbox_touched, edits_touched, activity_touched, code_files


#: Exit status for "another daemon already owns this repo" — a stand-down, not a
#: failure. Distinct from 0 (which would be indistinguishable from a clean stop) and
#: from 1 (which a supervisor should treat as a crash). See the guard in `run_watch`.
STAND_DOWN_EXIT = 3


def _pidfile(codoc_dir: str) -> Path:
    return Path(codoc_dir) / "watch.pid"


def write_pidfile(codoc_dir: str) -> None:
    """Write ``.codoc/watch.pid`` with owner metadata.

    The file is JSON ``{pid, owner, started_at}`` so the VS Code extension can tell
    which window owns the daemon (``owner`` from ``CODOC_WATCH_OWNER``; empty for a
    plain CLI invocation). Readers that only want the pid use :func:`read_pid`,
    which still parses a legacy bare-int file — the format change is backward
    compatible."""
    import json
    import os
    import time

    payload = {
        "pid": os.getpid(),
        "owner": os.environ.get("CODOC_WATCH_OWNER", ""),
        "started_at": time.time(),
    }
    _pidfile(codoc_dir).write_text(json.dumps(payload), encoding="utf-8")


def read_pid(codoc_dir: str) -> int | None:
    """Read the daemon pid from ``watch.pid`` (JSON ``{pid}`` or legacy bare int).

    Returns None when the file is missing/unparseable, so liveness checks degrade
    to "no daemon" rather than raising."""
    import json

    try:
        raw = _pidfile(codoc_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return int(json.loads(raw)["pid"])
    except (ValueError, TypeError, KeyError):
        pass
    try:
        return int(raw)  # legacy bare-int pidfile
    except ValueError:
        return None


def clear_pidfile(codoc_dir: str) -> None:
    """Remove ``watch.pid`` — but ONLY when it still names this process.

    Ownership check matters on a fast restart/bounce: when the extension stops one
    daemon (async SIGTERM) and immediately starts another, the dying daemon's
    ``atexit`` must not unlink the *new* daemon's pidfile (which would orphan a live
    daemon from ``daemon_running``). A foreign/absent pid is left untouched."""
    import os

    pid = read_pid(codoc_dir)
    if pid is not None and pid != os.getpid():
        return  # the pidfile names another (live) daemon — not ours to remove
    try:
        _pidfile(codoc_dir).unlink()
    except OSError:
        pass


def daemon_running(codoc_dir: str) -> bool:
    """True if a live ``codoc watch`` daemon owns this repo (pidfile + live pid).

    Lets the Stop hook decide whether to reflect itself (no daemon) or defer to the
    daemon's epoch-close reconcile — so the two never double-run on one epoch."""
    pid = read_pid(codoc_dir)
    if pid is None:
        return False
    return _pid_alive(pid) and _is_codoc_daemon(pid)


def _pid_alive(pid: int) -> bool:
    """True if ``pid`` is a live process (signal-0 liveness probe)."""
    import os

    try:
        os.kill(pid, 0)  # signal 0 = liveness probe, doesn't actually signal
        return True
    except OSError:
        return False


def _is_codoc_daemon(pid: int) -> bool:
    """True if ``pid`` is actually a ``codoc watch`` process.

    A bare pid is not an identity. Pids are recycled — aggressively so after a
    reboot, when the counter restarts low and races back through exactly the range a
    stale ``watch.pid`` from the previous boot is naming. Believing the number alone
    meant any unrelated process of this user that inherited it made the workspace
    look permanently watched: `codoc watch` refused to start, the Stop hook stood
    down waiting for a daemon that did not exist, and the refusal told the reader to
    "stop the other one first" — a process they could not find because it was their
    browser. Nothing recovered on its own, and the visible symptom was the quietest
    one possible: edits accepted by the editor that no loop ever picked up.

    Unknown answers count as NOT a daemon. The cost of a false negative is a second
    daemon (which the loop lock already makes safe, and which the owner check below
    then resolves); the cost of a false positive is a workspace that never syncs
    again. Those are not close.
    """
    import os
    import subprocess

    # The daemon asking about its own pidfile. Whatever this process is, it is the one
    # that wrote the file — there is no foreign claim to verify, and shelling out to
    # ask `ps` what we already know would make every liveness probe cost a fork.
    if pid == os.getpid():
        return True
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    cmd = out.stdout.strip()
    return "codoc" in cmd and "watch" in cmd


def _store_open_error(codoc_dir: str) -> str | None:
    """None if the store opens cleanly; otherwise a short reason. Used to fail the
    daemon fast on a corrupt/unopenable ``codoc.db`` instead of looping forever with a
    swallowed per-cycle error. A missing db is fine (a fresh `codoc watch` creates it)."""
    from pathlib import Path

    if not (Path(codoc_dir) / "codoc.db").exists():
        return None
    try:
        from codoc.store.db import open_store

        with open_store(codoc_dir):
            return None
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def parent_alive() -> bool:
    """Decide whether the watch loop should keep running w.r.t. its spawner.

    When the VS Code extension spawns the daemon it sets ``CODOC_WATCH_PARENT_PID``
    to its own (extension-host) pid. If that process dies — the window was closed
    or the host crashed without :func:`deactivate` running — the daemon would
    otherwise be orphaned. This returns False once that pid is gone so the loop can
    self-exit. When the env var is unset (a plain ``codoc watch`` from a shell) it
    always returns True, so the manual CLI path is completely unchanged."""
    import os

    raw = os.environ.get("CODOC_WATCH_PARENT_PID")
    if not raw:
        return True  # no managed parent → behave exactly as today
    try:
        parent_pid = int(raw)
    except ValueError:
        return True  # malformed → don't self-exit on a bad value
    return _pid_alive(parent_pid)


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
    data = read_json(activity_path(codoc_dir), default={})
    ep = data.get("epoch")
    return ep if ep and ep.get("id") else None


def watch_filter(codoc_dir: str):
    """A watchfiles filter: allow tree.codoc, tree.doc.json, inbox.json, edits.json,
    activity.json, and code files; drop everything else (notably .codoc indexing
    artifacts that churn during update_index, and codoc's own status.json/sidecar
    re-writes)."""
    tp = tree_path(codoc_dir).resolve()
    dp = doc_path(codoc_dir).resolve()
    ip = inbox_path(codoc_dir).resolve()
    ivp = host_verdicts_path(codoc_dir).resolve()
    ep = edits_path(codoc_dir).resolve()
    hp = host_ops_path(codoc_dir).resolve()
    ap = activity_path(codoc_dir).resolve()

    def _f(_change, path: str) -> bool:
        rp = Path(path).resolve()
        if rp in (tp, dp, ip, ivp, ep, hp, ap):
            return True
        if any(part in _SKIP_DIRS for part in rp.parts):
            return False
        return _is_code(rp)

    return _f


def _render(codoc_dir: str) -> None:
    """Non-destructive render: never overwrite un-applied human edits (H1)."""
    from codoc.loop.reconcile import safe_write_tree

    with open_store(codoc_dir) as store:
        safe_write_tree(store, codoc_dir)


def _floor_status(codoc_dir: str, printer=print, *, realizing: bool | None = None) -> None:
    """Best-effort re-derive of status.json to the ground truth — the shared
    floor for crash paths, stale-epoch heals, and reap paths. Never raises, but
    a failure is LOGGED: silently masking a persistently-failing floor (locked/
    corrupt store, unwritable status.json) would leave a stale status lying
    forever — the exact "lie without an expiry" these recovery paths exist to
    prevent."""
    try:
        with open_store(codoc_dir) as _store:
            status.refresh_status(codoc_dir, _store, realizing=realizing)
    except Exception as e:  # noqa: BLE001 — recovery is best-effort, but leave a trace
        printer(f"⚠ status floor failed (status.json may be stale): {e}")


def process_batch(
    paths: list[str],
    root_dir: str,
    codoc_dir: str,
    state: WatchState,
    *,
    no_realize: bool = False,
    dry_run: bool = False,
    loop_a=reconcile_drift,
    loop_b=run_loop_b,
    render=_render,
    has_user_edits=None,
    now=None,
    printer=print,
) -> tuple[str, str] | None:
    """Handle one debounced change batch. Returns (label, summary) or None.

    The ``loop_a`` slot (code→codoc reflection) defaults to the state-based
    :func:`reconcile_drift`, not the temporal index diff: a watch cycle already
    knows which files changed (it supplies ``file_scope``), so the temporal diff
    is only a scoping hint — the authority for *what diverged* is the index↔store
    reconciliation, which self-heals a missed/crashed cycle."""
    if has_user_edits is None:
        from codoc.loop.reconcile import has_pending_user_edits as has_user_edits
    from codoc.loop.reconcile import has_pending_doc_edits
    if now is None:
        import time as _time
        now = _time.time
    tp = tree_path(codoc_dir)
    codoc_touched, doc_touched, inbox_touched, edits_touched, activity_touched, code_files = _classify(
        paths, root_dir, codoc_dir
    )

    # ── Step 0: Stale-epoch recovery. A hard-killed agent (no Stop/SessionEnd hook)
    # leaves the epoch open, which would suppress all loops forever. Detect silence
    # and recover by closing it; for an interactive epoch, fold its suppressed +
    # touched files into this batch so the normal Loop A routing reconciles them. ─
    if state.epoch_open and _epoch_stale(codoc_dir, now()):
        # The epoch id the daemon actually observed open (recorded at its rising
        # edge). Capturing it BEFORE any mutation is what lets close_epoch reject a
        # fresh SessionStart that raced into the single epoch slot — the old inline
        # heal read the id back AFTER the staleness check, so a racing SessionStart
        # made the "same epoch?" guard trivially true and got clobbered.
        dead_id = state.last_epoch_id
        fold_interactive = state.epoch_origin == "interactive" and not no_realize
        # Files to reconcile if we confirm this epoch is ours and dead (close_epoch
        # preserves `touched`, so reading them before/after the heal is equivalent).
        ep_files = (state.suppressed_files | set(epoch_touched_files(codoc_dir))
                    if fold_interactive else set())
        # Drop our in-memory tracking regardless of the heal outcome: this epoch is
        # no longer ours to suppress. last_epoch_id stays = dead_id so step 1 won't
        # reprocess the dead epoch and a racing fresh epoch registers as a rising edge.
        state.epoch_open = False
        state.epoch_origin = ""
        state.suppressed_files.clear()
        # Heal activity.json ITSELF (not just WatchState) under the file lock, so
        # every OTHER reader (IDE status bar, a second hook, autorealize's spawn
        # guard) also stops believing the dead session is live — but only if the
        # file still names the SAME dead epoch and is still stale under the lock.
        if close_epoch(codoc_dir, dead_id, now=now(), stale_after=EPOCH_STALE_SECONDS):
            if fold_interactive:
                code_files |= ep_files
            # Floor status.json so a stuck `tree_dirty`/`realizing` written by that
            # session doesn't outlive it. Only after a confirmed heal: if a fresh
            # session raced in, its status is live and must not be recomputed away.
            _floor_status(codoc_dir, printer)

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
        if not (codoc_touched or doc_touched or inbox_touched or edits_touched or code_files):
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
    # tree.doc.json (U2b): the host persists it on every payload (comment reconcile /
    # suggestion rebase), so only route to Loop B when it carries a pending feature
    # edit — else a non-edit persist would ping-pong the loop.
    if doc_touched and not has_pending_doc_edits(codoc_dir):
        doc_touched = False
    # edits.json / inbox.json: Loop B itself clears these (drain annotations/steers/
    # cancellations, clear verdicts). That clear is a watched-file event that would
    # re-route straight back into a no-op Loop B pass ("edits 1" → "edits 0"). Ignore
    # it when the file is byte-identical to what the last Loop B pass left behind —
    # the same self-write guard the tree.codoc hash provides above. A genuine host
    # write (a new annotation / verdict) changes the bytes, so it is never suppressed.
    # edits.json / edits.host.jsonl: Loop B drains edits.json AND consumes the IDE's
    # host-op log (merge renames it away). Both writes are watched-file events that would
    # re-route into a no-op Loop B. Suppress ONLY when BOTH are byte-identical to what the
    # last pass left behind — a genuine host append changes edits.host.jsonl's hash, so it
    # is never suppressed; the daemon's own consumption (log gone) is.
    if edits_touched and (_hash(edits_path(codoc_dir)) == state.last_edits_hash
                          and _hash(host_ops_path(codoc_dir)) == state.last_host_hash):
        edits_touched = False
    if inbox_touched and (_hash(inbox_path(codoc_dir)) == state.last_inbox_hash
                          and _hash(host_verdicts_path(codoc_dir)) == state.last_inbox_host_hash):
        inbox_touched = False

    # ── Step 3: While an epoch is open, suppress independent Loop A AND Loop B. ──
    if state.epoch_open:
        if code_files:
            state.suppressed_files |= code_files  # accumulate; agent owns these
        # A tree.codoc change mid-epoch is the agent's own reflection (it calls the
        # codoc MCP tools, which `write_tree` directly — NOT through this daemon, so
        # the hash guard above can't catch it). Routing it to Loop B would spawn a
        # nested coding agent to "implement" what the agent just reflected. Suppress
        # it; the epoch-close scoped Loop A reconciles everything. (A genuine human
        # tree/doc edit mid-session is rare and is deferred to epoch close.)
        codoc_touched = False
        doc_touched = False
        if not (inbox_touched or edits_touched):
            return None  # code churn / agent reflection during epoch → suppressed

    # ── Step 4: Normal routing. ────────────────────────────────────────────────
    # A tree.codoc edit, a tree.doc.json webview edit (U2b), an Accept/Reject verdict,
    # or a doc-ahead suggestion / comment steer (edits.json) drives Loop B (codoc →
    # code); changed code files drive Loop A (code → codoc). When BOTH co-occur in one
    # batch we now run BOTH — Loop B first (authored intent leads), then a scoped Loop A
    # — instead of an ``elif`` that ran only Loop B and starved the code→tree reflection
    # until the next code-only event (under continuous editing the tree lagged the code
    # indefinitely). Loop A stays suppressed while an epoch is open (the agent owns those
    # files; they were accumulated into suppressed_files for the epoch-close reconcile).
    outs: list[tuple[str, str]] = []
    if codoc_touched or doc_touched or inbox_touched or edits_touched:
        if codoc_touched or doc_touched:
            status.write_status(codoc_dir, status.TREE_DIRTY, detail="applying tree edits")
        # --dry / --no-realize must still APPLY the webview's authored edits (else the
        # editor appears frozen — the field bug) and re-render both files; they only
        # suppress handing realization to the agent. So map both flags to realize=False
        # (NOT loop_b's dry_run, which is a read-mostly preview that skips commands).
        res = loop_b(root_dir, codoc_dir, realize=not (dry_run or no_realize))
        # Loop B just drained/cleared edits.json + inbox.json and consumed the host-op
        # log; record their new state so the resulting watch events (its own writes) are
        # recognised as self-writes on the next batch and not re-routed back into Loop B.
        state.last_edits_hash = _hash(edits_path(codoc_dir))
        state.last_inbox_hash = _hash(inbox_path(codoc_dir))
        state.last_host_hash = _hash(host_ops_path(codoc_dir))
        state.last_inbox_host_hash = _hash(host_verdicts_path(codoc_dir))
        outs.append(("codoc→code", res.summary()))
    if code_files and not state.epoch_open:
        res_a = loop_a(root_dir, codoc_dir, file_scope=code_files)
        outs.append(("code→codoc", f"({len(code_files)} files) {res_a.summary()}"))

    if not outs:
        return None

    render(codoc_dir)
    state.last_tree_hash = _hash(tp)
    if len(outs) == 1:
        return outs[0]
    return " + ".join(label for label, _ in outs), " · ".join(summary for _, summary in outs)


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
                    no_realize=no_realize, dry_run=dry_run, printer=printer)
    except Exception as e:  # noqa: BLE001 — resilience over correctness for one cycle
        import traceback
        printer(f"⚠ codoc cycle error (daemon continues): {e}")
        traceback.print_exc()
        # A status write earlier in the crashed pass (e.g. TREE_DIRTY before Loop B
        # ran) must not outlive the pass that wrote it — floor it back to the
        # ground truth now, best-effort, rather than leaving "applying tree
        # edits…"/"implementing…" stuck until the next SUCCESSFUL pass happens to
        # call refresh_status.
        _floor_status(codoc_dir, printer)
        return None


def maybe_auto_realize(state: WatchState, root_dir: str, codoc_dir: str, *, printer=print) -> None:
    """Headless fallback (``--auto-realize``): implement a queued realize.md when
    no interactive session is around to do it. Reaps a finished pass, then launches
    a new one only when :func:`autorealize.should_spawn` agrees (queue present, none
    in flight, no live epoch)."""
    from codoc.loop import autorealize

    proc = state.realize_proc
    if proc is not None and proc.poll() is not None:  # previous pass finished
        state.realize_proc = None
        proc = None
        # Force the honest lifecycle state now that the process is gone (mirrors
        # sdk_realize's own finally-block recovery, WS1.5): without this a
        # crashed/killed `claude -p /codoc:sync` relies on the realizing lease's
        # REALIZING_LEASE_SECONDS timeout to self-heal instead of clearing immediately.
        _floor_status(codoc_dir, printer, realizing=False)
    if not autorealize.should_spawn(codoc_dir, in_flight=proc is not None):
        return
    from codoc.loop.sdk_realize import resolve_engine

    engine = resolve_engine("auto")
    launched = autorealize.spawn_realize(root_dir, codoc_dir, engine=engine)
    if launched is None:
        printer("⚠ --auto-realize: no realize engine available (pip install 'codoc[sdk]' "
                "or put `claude` on PATH); leaving realize.md queued")
        return
    state.realize_proc = launched
    printer(f"▸ auto-realize  spawned {engine} /codoc:sync")


def run_watch(
    root_dir: str,
    codoc_dir: str,
    *,
    no_realize: bool = False,
    dry_run: bool = False,
    auto_realize: bool = False,
    printer=print,
) -> None:  # pragma: no cover - blocking I/O loop
    import atexit

    import watchfiles

    from codoc.loop.loop_a import reconcile_drift

    # Singleton guard: a second daemon on the same repo (a manual `codoc watch` beside
    # the VS Code extension's daemon, or a double-launch) doubles every pass and, with
    # --auto-realize, can spawn two agents on one realize.md. The serve hub and the
    # extension already guard; the bare CLI did not. loop_lock keeps the store safe, but
    # refusing up front avoids the wasted/duplicated work.
    if daemon_running(codoc_dir):
        printer("A codoc daemon is already watching this repo — not starting a second one. "
                "Stop the other one first (or it may be the VS Code extension's).")
        # A DELIBERATE stand-down, and it has to be distinguishable from a crash. The
        # extension supervises this process and counts every exit inside five seconds
        # against a crash-loop budget; past three it stops trying for the life of the
        # window and says so once. Returning 0 here made the two situations identical,
        # so three harmless races — a window reload, a bounce, two windows on one repo —
        # spent the whole budget and left the workspace with no daemon and no retry.
        # `STAND_DOWN_EXIT` is the one exit code that means "nothing is wrong".
        raise SystemExit(STAND_DOWN_EXIT)
    # Nothing live is behind the pidfile, so it is debris from a daemon that was killed
    # rather than stopped (SIGKILL, a machine sleeping, a host crash — `atexit` runs on
    # none of those). Reap it here rather than leaving it for the extension: a CLI
    # `codoc watch` has no supervisor to heal it, and the file is about to be ours.
    if read_pid(codoc_dir) is not None:
        _pidfile(codoc_dir).unlink(missing_ok=True)

    # Fail fast and legibly on an unopenable store instead of looping forever emitting
    # one error per cycle (every open_store inside the loop would raise and be swallowed).
    _bad = _store_open_error(codoc_dir)
    if _bad is not None:
        printer(f"✗ cannot open the codoc store ({_bad}). The database may be corrupt — "
                "back it up and re-run `codoc init --force` to rebuild it.")
        return

    write_pidfile(codoc_dir)  # let the Stop hook know a daemon owns this repo
    atexit.register(clear_pidfile, codoc_dir)

    # One-time, idempotent self-heal for workspaces predating the store-authoritative
    # refactor (U8). Must run BEFORE _render rebuilds tree.doc.json from the store —
    # it reads the pre-existing tree.doc.json comment threads into the store, then
    # converges any re-minted duplicate features. A clean workspace is a no-op.
    migrate_ok = True
    try:
        from codoc.loop.migrate import migrate_workspace

        res = migrate_workspace(codoc_dir)
        if res.changed():
            printer(f"▸ migrate  {res.summary()}")
    except Exception as e:  # noqa: BLE001
        migrate_ok = False
        printer(f"⚠ startup migrate failed (continuing to watch): {e}")

    # Render rebuilds tree.doc.json from the store. On a FAILED (partial) migration the
    # store may not yet hold the comments still living only in the pre-existing
    # tree.doc.json — rendering now would overwrite that file and destroy the un-migrated
    # comments. So skip this startup render when migration failed; the file is left
    # untouched (a later cycle, after the migration is fixed, rebuilds it safely). The
    # daemon stays alive either way.
    if migrate_ok:
        _render(codoc_dir)

    # Absorb any IDE input that queued while the daemon was down. No watch event fires
    # for a file that already EXISTED at startup, so this is the only place these get
    # applied — and the gate used to name `edits.host.jsonl` alone, which silently lost
    # every OTHER channel. A verdict is the one that bites: click Accept with no daemon
    # up, restart the daemon, and the verdict sits in inbox.json forever — the click
    # registered, the IDE says "waiting to apply", and nothing ever applies it. All four
    # channels are Loop B inputs and all four are drained by the same pass.
    if migrate_ok and any(p.exists() for p in (host_ops_path(codoc_dir),
                                               edits_path(codoc_dir),
                                               inbox_path(codoc_dir),
                                               host_verdicts_path(codoc_dir))):
        try:
            res = run_loop_b(root_dir, codoc_dir, realize=not (no_realize or dry_run))
            printer(f"▸ startup edits  {res.summary()}")
        except Exception as e:  # noqa: BLE001
            printer(f"⚠ startup edits merge failed (continuing to watch): {e}")

    state = WatchState(
        last_tree_hash=_hash(tree_path(codoc_dir)),
        last_edits_hash=_hash(edits_path(codoc_dir)),
        last_inbox_hash=_hash(inbox_path(codoc_dir)),
        last_host_hash=_hash(host_ops_path(codoc_dir)),
        last_inbox_host_hash=_hash(host_verdicts_path(codoc_dir)),
    )

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

    # Re-render the derived exports once the daemon is up. Startup is exactly when
    # the IDE opens the tree, and reconcile_drift writes the sidecar and status but
    # neither tree.codoc nor tree.doc.json — so without this the webview's document
    # pane stayed blank on every fresh workspace open until some code file changed
    # and a batch happened to call `_render`. Guarded like every other render: a
    # pending webview edit skips it rather than clobbering an in-flight intent.
    try:
        _render(codoc_dir)
    except Exception as e:  # noqa: BLE001 — a failed seed must not stop the daemon
        printer(f"⚠ startup render failed (the doc pane may be blank): {e}")

    suffix = "  (--auto-realize: unattended implement when no session is open)" if auto_realize else ""
    printer(f"codoc watching {root_dir} — edit code or .codoc/tree.codoc (Ctrl-C to stop){suffix}")
    # `yield_on_timeout` wakes the loop every PARENT_POLL_MS even with no file
    # changes, so the parent-death self-exit (when the extension spawned us) is
    # checked on a steady cadence rather than only on the next edit.
    for changes in watchfiles.watch(
        root_dir,
        watch_filter=watch_filter(codoc_dir),
        debounce=DEBOUNCE_MS,
        rust_timeout=PARENT_POLL_MS,
        yield_on_timeout=True,
    ):
        if not parent_alive():
            printer("▸ codoc watch: spawning extension host gone — self-exiting")
            break
        if not changes:
            # Bare timeout tick — beyond the parent-death check above, give
            # stale-epoch recovery (step 0 of process_batch) a chance to run even
            # when no file event arrives. Without this, a hard-killed session (no
            # Stop/SessionEnd hook) wedges every loop until the user happens to
            # touch a file — which may be never if they've moved on to other work.
            if state.epoch_open:
                out = safe_process_batch([], root_dir, codoc_dir, state,
                                         no_realize=no_realize, dry_run=dry_run, printer=printer)
                if out:
                    printer(f"▸ {out[0]}  {out[1]}")
            if auto_realize:
                # Reap a finished/crashed headless pass and retry a queued
                # realize.md on the idle cadence too — an unattended repo may
                # never see another file event, and a dead child would otherwise
                # stay un-reaped (and the queue un-implemented) indefinitely.
                maybe_auto_realize(state, root_dir, codoc_dir, printer=printer)
            continue
        out = safe_process_batch([p for _, p in changes], root_dir, codoc_dir, state,
                                 no_realize=no_realize, dry_run=dry_run, printer=printer)
        if out:
            printer(f"▸ {out[0]}  {out[1]}")
        if auto_realize:
            maybe_auto_realize(state, root_dir, codoc_dir, printer=printer)
