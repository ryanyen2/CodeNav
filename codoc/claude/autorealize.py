"""Starting a realize pass: ``codoc realize`` and ``codoc watch --auto-realize``.

A hand-off waits in ``.codoc/realize.md`` until an agent builds it. A person's
own Claude Code session does that with ``/codoc:realize``. ``codoc realize``
starts a pass in the foreground, and with ``--auto-realize`` the daemon starts
one when a hand-off is queued that no pass has been started for and no session
is live, so a plan accepted with nobody at the keyboard still lands code
(:func:`auto_realizer`).

Both go through :func:`start_realize`. Two engines: the Claude Agent SDK
runner (:mod:`codoc.claude.sdk_realize`, chosen by ``auto`` when it is
installed: it prints one line per action and records the files it touches in
``activity.json``) and ``claude -p "/codoc:realize"``, which this module runs
as ``python -m codoc.claude.autorealize <root> <permission mode>``
(:func:`run_cli`). Either way the agent uses the MCP server and commands the
repository already has installed, and the pass ends with one line saying how
it ended, how long it took and what it cost (:func:`summary`).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from codoc.claude.hook import queued_ids
from codoc.workspace.epoch import epoch_alive
from codoc.daemon.publish import REALIZE_OWNER_ENV, realizing_is_fresh, write_realizing
from codoc.workspace.layout import CODOC_DIR, atomic_write

# After a failed pass the next waits this long from when it ended, doubled for
# each failure in a row, up to an hour.
FAILED_BACKOFF_S = 60.0
# A hand-off that this many passes in a row failed on is left alone until the
# daemon restarts: a pass that fails after spending (an error result, the turn
# limit) spends again.
MAX_FAILED_PASSES = 3
# Where a pass's child records when it ended: the daemon may notice the exit
# long after it happened, and the back-off counts from the exit.
PASS_ENDED = "cache/realize-ended.json"


class EngineMissing(RuntimeError):
    """The engine asked for is not installed. The message says what to install."""


def find_claude() -> str | None:
    """The ``claude`` CLI on PATH, or None when it is not installed."""
    return shutil.which("claude")


def start_realize(root_dir: str, codoc_dir: str, *, engine: str = "auto", permission_mode: str = "acceptEdits",
                  detail: str) -> subprocess.Popen:
    """Start a realize pass in ``root_dir`` and return its process.

    ``sdk`` runs ``python -m codoc.claude.sdk_realize``; ``cli`` runs
    :func:`run_cli`; ``auto`` takes the SDK when it is importable. The pass
    takes the realizing lease with ``detail`` and the child's pid, so the lease
    holds while the child lives, and is named its owner in the child's
    environment, so its own agent does not read the lease as another pass's and
    stand down. Raises :class:`EngineMissing` when the engine is not
    installed."""
    from codoc.claude.sdk_realize import resolve_engine, sdk_available

    if resolve_engine(engine) == "sdk":
        if not sdk_available():
            raise EngineMissing("claude-agent-sdk is not installed. Run pip install 'codoc[sdk]', or use --engine cli")
        argv = [sys.executable, "-m", "codoc.claude.sdk_realize", root_dir, permission_mode]
    else:
        if find_claude() is None:
            raise EngineMissing("the `claude` CLI is not on PATH")
        argv = [sys.executable, "-m", "codoc.claude.autorealize", root_dir, permission_mode]
    owner = uuid.uuid4().hex
    proc = subprocess.Popen(argv, cwd=root_dir, env={**os.environ, REALIZE_OWNER_ENV: owner}, stdin=subprocess.DEVNULL)
    try:
        write_realizing(codoc_dir, detail=detail, owner=owner, pid=proc.pid)
    except OSError:  # status is advisory
        pass
    return proc


def summary(ok: bool, seconds: float, cost: float | None, detail: str = "") -> str:
    """The last line a realize pass prints: how it ended, what it did or why it
    failed, how long it took and what it cost."""
    parts = ["✓ done" if ok else "✗ failed", *([detail] if detail else []), f"{int(seconds)}s",
             f"${cost:.2f}" if isinstance(cost, (int, float)) else "cost unknown"]
    return "  " + " · ".join(parts)


def run_cli(root_dir: str, permission_mode: str, *, printer=print) -> int:
    """``claude -p "/codoc:realize"`` in ``root_dir``, then the agent's last
    message and the :func:`summary`, the cost read from the JSON result."""
    claude = find_claude()
    if claude is None:
        printer("the `claude` CLI is not on PATH")
        return 2
    started = time.monotonic()
    run = subprocess.run([claude, "-p", "/codoc:realize", "--permission-mode", permission_mode,
                          "--output-format", "json"], cwd=root_dir, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, text=True)
    try:
        result = json.loads(run.stdout)
    except ValueError:
        result = {}
    ok = run.returncode == 0 and not result.get("is_error")
    printer(str(result.get("result") or run.stdout).strip())
    printer(summary(ok, time.monotonic() - started, result.get("total_cost_usd")))
    return 0 if ok else 1


def record_end(codoc_dir: str | Path, code: int) -> int:
    """Record, as the pass's child exits, its pid, its exit code and the wall
    time; returns ``code``. Both engines' entry points call it."""
    try:
        atomic_write(Path(codoc_dir) / PASS_ENDED, json.dumps({"pid": os.getpid(), "code": code, "at": time.time()}))
    except OSError:  # the daemon then counts the back-off from the pass's start
        pass
    return code


def _ended(codoc_dir: str, pid: int) -> float | None:
    """When the pass with ``pid`` recorded its exit, or None."""
    try:
        d = json.loads((Path(codoc_dir) / PASS_ENDED).read_text(encoding="utf-8"))
        return float(d["at"]) if d.get("pid") == pid else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def auto_realizer(root_dir: str, codoc_dir: str, *, clock=time.time, printer=print):
    """A callable for ``Daemon(after_batch=…)``. After each batch it starts a
    pass when ``realize.md`` holds a hand-off no finished pass was started for,
    the pass this started last has ended, no other pass holds a fresh
    realizing lease and no agent session is live.

    A pass costs a model session. One that ends with work still queued (a
    hand-off the agent could not satisfy, a build that moved no bound symbol)
    is not started again for the same hand-offs: running it again would change
    nothing, and its own Stop hook wakes the daemon the moment it ends. A pass
    that failed (a nonzero exit, such as no login or a rate limit) leaves its
    hand-offs to be tried again :data:`FAILED_BACKOFF_S` after it ended, doubled
    for each failed pass in a row. A failed pass counts against each hand-off
    it was started for, and a hand-off that :data:`MAX_FAILED_PASSES` passes in
    a row failed on is counted as tried, which ``printer`` says once; a
    hand-off queued later starts a pass of its own, and a daemon restart forgets
    the counts. The daemon's periodic tick runs this between file events, so
    the retry does not wait for one. ``clock`` is wall time, as the child
    records it."""
    proc: subprocess.Popen | None = None
    tried: set[str] = set()           # hand-offs a pass that finished was started with, or given up on
    failed: dict[str, int] = {}       # hand-off -> passes in a row that failed on it
    started: list[str] = []
    started_at = 0.0
    streak, wait_until = 0, 0.0       # failed passes in a row, whatever they were for: the back-off

    def run(daemon) -> None:
        nonlocal proc, started, started_at, streak, wait_until
        if proc is not None:
            if proc.poll() is None:
                return
            if proc.returncode == 0:
                tried.update(started)
                for d in started:
                    failed.pop(d, None)
                streak = 0
            else:
                streak += 1
                ended = _ended(codoc_dir, proc.pid) or started_at
                wait_until = ended + min(FAILED_BACKOFF_S * 2 ** (streak - 1), 3600.0)
                given_up = []
                for d in started:
                    failed[d] = failed.get(d, 0) + 1
                    if failed[d] >= MAX_FAILED_PASSES and d not in tried:
                        tried.add(d)
                        given_up.append(d)
                if given_up:
                    printer(f"auto-realize: {MAX_FAILED_PASSES} passes in a row failed on "
                            f"{', '.join('d:' + d for d in given_up)} (the last exited {proc.returncode}); "
                            "not retrying until the daemon restarts")
            proc = None
        if daemon.state.epoch_open or clock() < wait_until or realizing_is_fresh(codoc_dir) or epoch_alive(codoc_dir):
            return
        queue = queued_ids(codoc_dir)
        if not set(queue) - tried:
            return
        try:
            proc = start_realize(root_dir, codoc_dir, detail="building: codoc watch --auto-realize")
        except EngineMissing:
            return
        started, started_at = queue, clock()

    return run


if __name__ == "__main__":
    raise SystemExit(record_end(Path(sys.argv[1]) / CODOC_DIR, run_cli(sys.argv[1], sys.argv[2])))
