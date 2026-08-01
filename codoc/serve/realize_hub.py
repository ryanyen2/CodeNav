"""realize_hub.py — the server-owned realization worker (U7 live wiring).

The deployed hub disables the daemon's ``--auto-realize`` (KTD7): the SERVER decides
what realizes, and only on an authorized HAND-OFF. This is the thin live loop that ties
the pure pieces together:

    status.json + realize.json
        → ``realize_trigger.ready_directives``   (handed-off only)
        → ``realize_trigger.filter_undone``      (skip already-shipped, by id)
        → ``realize_pr.realize_directive``       (worktree → sandboxed agent → PR)
        → ``realize_trigger.mark_done``

Every directive realizes OFF the live tree on a dedicated git worktree + branch, the
agent runs under the ENFORCED sandbox (``realize_agent.make_sandboxed_agent`` — no Bash,
scoped edits, consult-gated WebFetch, no token), and only the orchestrator (holding the
``gh`` token) opens the PR. ``process_ready`` is the pure step (run/agent/readers
injected, tested); :class:`RealizeWorker` is the background-thread wrapper.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Callable

from codoc.loop.edits import manifest_path
from codoc.loop.fsio import read_json
from codoc.loop.status import STATUS_FILENAME
from codoc.serve.realize_agent import make_sandboxed_agent
from codoc.serve.realize_pr import realize_directive
from codoc.serve.realize_trigger import filter_undone, mark_done, read_done, ready_directives

Printer = Callable[[str], None]
_WORKTREE_DIR = "worktrees"


def _read_status(codoc_dir: str) -> dict:
    data = read_json(Path(codoc_dir) / STATUS_FILENAME, default={})
    return data if isinstance(data, dict) else {}


def _read_manifest(codoc_dir: str) -> list[dict]:
    data = read_json(manifest_path(codoc_dir), default={})
    directives = data.get("directives") if isinstance(data, dict) else None
    return [d for d in directives if isinstance(d, dict)] if isinstance(directives, list) else []


def scope_for(codoc_dir: str, feature_id: str) -> list[str] | None:
    """The 'Edit only' scope for a directive: the files the feature is bound to (from
    the sidecar). ``None`` when the feature has no bindings yet (a fresh plan node) —
    the agent may then write anywhere NOT denylisted, per ``sandbox.edit_allowed``."""
    if not feature_id:
        return None
    from codoc.agent.hook import BINDINGS_FILENAME

    sidecar = read_json(Path(codoc_dir) / BINDINGS_FILENAME, default={})
    by_feature = (sidecar.get("by_feature") if isinstance(sidecar, dict) else None) or {}
    files = [b.get("file") for b in (by_feature.get(feature_id) or []) if b.get("file")]
    return sorted(set(files)) or None


def _run(argv, cwd=None) -> int:
    return subprocess.run(argv, cwd=cwd, check=False).returncode


def process_ready(
    root_dir: str,
    codoc_dir: str,
    *,
    base: str = "main",
    run=_run,
    agent=None,
    read_status=_read_status,
    read_manifest=_read_manifest,
    printer: Printer = print,
) -> list[str]:
    """Realize every ready+undone handed-off directive once. Returns the ids realized
    this pass. Pure w.r.t. injected ``run``/``agent``/readers so it is unit-testable."""
    status = read_status(codoc_dir)
    manifest = read_manifest(codoc_dir)
    ready = filter_undone(ready_directives(status, manifest), read_done(codoc_dir))
    if not ready:
        return []
    agent = agent or make_sandboxed_agent()
    realized: list[str] = []
    for directive in ready:
        did = directive.get("id") or ""
        scope = scope_for(codoc_dir, directive.get("feature_id") or "")
        worktree = str(Path(codoc_dir) / _WORKTREE_DIR / (did or "d"))
        try:
            result = realize_directive(directive, worktree, run=run, agent=agent,
                                       scope=scope, base=base)
        except Exception as exc:  # noqa: BLE001 — one bad directive never wedges the loop
            printer(f"  ⚠ hub realize {did} failed: {exc}")
            _cleanup_worktree(worktree, run)
            continue
        if result.ok:
            mark_done(codoc_dir, did)
            realized.append(did)
            printer(f"  ✓ hub realized {did} → PR on {result.branch}")
        elif result.out_of_scope:
            # A write outside scope is a REJECTED realization — record it done so the
            # loop doesn't retry the same unsafe directive forever, and surface it.
            mark_done(codoc_dir, did)
            printer(f"  ✗ hub realize {did} wrote out of scope "
                    f"({', '.join(result.out_of_scope)}) — no PR opened")
        else:
            printer(f"  · hub realize {did}: {result.reason}")
        _cleanup_worktree(worktree, run)
    return realized


def _cleanup_worktree(worktree: str, run) -> None:
    try:
        run(["git", "worktree", "remove", "--force", worktree])
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass


class RealizeWorker:
    """Background thread that calls :func:`process_ready` on an interval until stopped."""

    def __init__(self, root_dir: str, codoc_dir: str, *, base: str = "main",
                 interval: float = 5.0, printer: Printer = print):
        self.root_dir = root_dir
        self.codoc_dir = codoc_dir
        self.base = base
        self.interval = interval
        self.printer = printer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                process_ready(self.root_dir, self.codoc_dir, base=self.base,
                              printer=self.printer)
            except Exception as exc:  # noqa: BLE001 — never let the worker thread die
                self.printer(f"  ⚠ hub realize worker error: {exc}")
            self._stop.wait(self.interval)

    def start(self) -> "RealizeWorker":
        self._thread = threading.Thread(target=self._loop, name="codoc-realize-hub",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def start_realize_worker(root_dir: str, codoc_dir: str, gh_config, *,
                         printer: Printer = print) -> RealizeWorker:
    """Start the hub realize worker (background thread). ``gh_config`` supplies the base
    branch context; the ``gh`` token lives in the orchestrator's env, never the agent's."""
    base = (os.environ.get("CODOC_SERVE_BASE") or "main").strip() or "main"
    return RealizeWorker(root_dir, codoc_dir, base=base, printer=printer).start()
