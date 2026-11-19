"""The daemon (``codoc watch``): one process, the only applier
(docs/concurrency.md, Processes and The daemon's batch).

Per debounced batch of filesystem events it does, in order:

1. follow the logs on disk (``Workspace.follow_disk``): when ``HEAD`` moved
   (its sha or its branch) or git took the session file, start a new session
   and rebuild, noting the hand-offs the commits ``HEAD`` moved over name in a
   ``Codoc-Handoff`` trailer, each from a commit whose ``Codoc-Base`` names
   the branch ``HEAD`` is on; when another log file appeared, vanished or
   changed, rebuild;
2. drain every client inbox into ops, warning once about an inbox file whose
   name maps to no actor;
3. read the agent epoch (``activity.json``): while a coding agent's session is
   open, changed code files are deferred, since the agent is mid-edit; when it
   closes, or after ``EPOCH_STALE_SECONDS`` of silence, they and every file
   the session touched go to step 4, and that pass cites every queued hand-off;
4. reflect the changed code files that are not deferred, citing the hand-offs
   of step 1;
5. settle the hand-offs applied binding ops now cite, unless an epoch is open
   (``realize.settle_if_idle``); a reflect pass settles too;
6. publish the derived files.

``process_batch`` runs with an injected change set and model in tests; ``run``
wires ``batch`` to ``watchfiles``, filtered by ``wakes``.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path, PurePosixPath

from codoc.daemon.pidfile import clear_pidfile, daemon_running, write_pidfile
from codoc.daemon.publish import Publisher
from codoc.doclang import workspace_doc_language
from codoc.index.git import handoffs_landed, head, is_tracked
from codoc.index.graph import Graph
from codoc.index.lang import detect_language
from codoc.loops.realize import settle_if_idle
from codoc.workspace import Roles, Workspace
from codoc.workspace import inbox as I
from codoc.workspace import log as L
from codoc.workspace.epoch import EPOCH_STALE_SECONDS, close_epoch, epoch_age, epoch_touched_files, read_activity
from codoc.workspace.layout import (
    ACTIVITY, APPLIER_LOCK, CODOC_DIR, EXPORT, GRAPH_CACHE, atomic_write, locked, write_gitignore,
)

DEBOUNCE_MS = 600


@dataclass
class DaemonState:
    epoch_open: bool = False
    deferred: set[str] = field(default_factory=set)   # code files changed while the epoch was open


class Daemon:
    def __init__(self, root_dir: str | Path, codoc_dir: str | Path | None = None, *,
                 changeset=None, propose=None, printer=print, after_batch=None) -> None:
        self.root = Path(root_dir)
        self.codoc_dir = Path(codoc_dir or self.root / CODOC_DIR)
        self.ws = Workspace(self.codoc_dir)
        self.ws.head = head(self.root)
        # A torn last line (a crash mid-write) is cut so every later rebuild
        # agrees with this one; every other log problem is reported.
        for f in self.ws.torn:
            if L.truncate_torn(f):
                printer(f"▸ cut a torn last line off {f.path.name}")
        for p in self.ws.problems:
            printer(f"▸ log problem: {p.path}:{p.line}: {p.reason}")
        self.graph = Graph.load(self.codoc_dir / GRAPH_CACHE)
        self.state = DaemonState()
        self.say = printer
        self.after_batch = after_batch       # called with the daemon after every watched batch
        self._changeset = changeset
        self._propose = propose
        self._unknown: set[str] = set()
        self.publisher = Publisher(self.codoc_dir, str(self.root))

    def identity(self, client: str, codoc_dir: Path) -> str | None:
        """The actor an inbox file writes as (``inbox.actor_for``). A file
        whose name maps to no actor is never drained, so say so once."""
        actor = I.actor_for(client, codoc_dir)
        if actor is None and client not in self._unknown:
            self._unknown.add(client)
            self.say(f"▸ not draining {I.inbox_dir(codoc_dir) / client}.jsonl: no actor has that name. "
                     "An older codoc MCP server wrote it; restart the agent so it runs the current one.")
        return actor

    # ── routing ────────────────────────────────────────────────────────────
    @cached_property
    def _source(self):
        # Built on first use, since importing the index takes seconds. Limit:
        # built once, so a .gitignore edited while the daemon runs counts from
        # its next start.
        from codoc.index.app import source_matcher
        return source_matcher(self.root)

    def code_path(self, path: str) -> str | None:
        """``path`` relative to the root when the index reads it (a language
        adapter parses it and no index exclude covers it), else None."""
        try:
            rel = Path(path).resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return None
        return rel if detect_language(rel) and self._source.is_file_included(PurePosixPath(rel)) else None

    def wakes(self, _change, path: str) -> bool:
        """Whether a filesystem event starts a batch: a code file the index
        reads, ``activity.json``, a client's inbox file, a log file other than
        this daemon's own session (another writer, or git, changed the logs on
        disk), or its own session file when git took or replaced it (``git
        stash -u``, ``git clean``, a ``stash pop`` that puts it back). The
        daemon's own writes (its appends to its session file, the acks,
        ``offsets.json``, ``atomic_write``'s tmp files) never do, or every
        batch would start the next."""
        rp = Path(path).resolve()
        if rp == (self.codoc_dir / ACTIVITY).resolve():
            return True
        if rp.suffix == ".jsonl" and rp.parent == I.inbox_dir(self.codoc_dir).resolve():
            return not rp.name.endswith(".ack.jsonl")
        if rp.suffix == ".jsonl" and rp.parent == L.log_dir(self.codoc_dir).resolve():
            return rp.name != f"{self.ws.session}.jsonl" or not self.ws.writer.intact()
        return self.code_path(path) is not None

    def batch(self, paths: list[str]) -> None:
        """One batch as ``run`` runs it, the first included: its note printed
        when it did something, an error printed rather than raised, then
        ``after_batch``."""
        try:
            note = self.process_batch(paths)
        except Exception as e:  # noqa: BLE001 (a bad batch must not kill the daemon)
            note = f"error: {e}"
        if note:
            self.say(f"▸ {note}")
        if self.after_batch:
            self.after_batch(self)

    def process_batch(self, paths: list[str], *, now: float | None = None) -> str:
        """Run one batch; returns what it did, empty when nothing."""
        now = now if now is not None else time.time()
        code = {rel for p in paths if (rel := self.code_path(p)) is not None}
        notes: list[str] = []
        # 1. The logs on disk.
        was = self.ws.head
        sha, branch = head(self.root)
        landed: frozenset[str] = frozenset()
        rebuilt = self.ws.follow_disk(sha, branch)
        if rebuilt:
            # A trailer counts from the commits HEAD moved over when HEAD is on
            # the branch the commit's `Codoc-Base` names (a merge, a pull with or
            # without rebase, a checkout of the base). Checking out or pulling a
            # hub pull request's own branch merges nothing. Limit: the trailer
            # is read when the batch that sees HEAD move reflects the merged
            # files; a merge whose files were reflected in an earlier batch (a
            # conflicted merge committed later) cites nothing, and `codoc done`
            # settles it.
            landed = handoffs_landed(self.root, was[0], sha, branch) if was and was[0] != sha else frozenset()
            self.publisher.invalidate()      # a different set of logs is a different tree
            notes.append("rebuilt from the logs on disk")
        # 2. Client ops, under the roles the tracked config names right now
        # (the hub adds a login's role when they sign in). Every batch drains:
        # the drain reads each inbox from its saved offset, so this is cheap,
        # and a batch with no inbox event (startup, `codoc sync`) still picks
        # up what clients wrote while no daemon was running.
        self.ws.roles.table = Roles.load(self.codoc_dir).table
        done = self.ws.drain_inbox(self.identity)
        if done:
            notes.append(f"{len(done)} client op(s)")
        # 3. The agent epoch, read every batch, so a daemon started during a
        # turn defers that turn's files and reflects them when it closes. The
        # pass that runs because a session ended cites the queued hand-offs, as
        # an agent's own `codoc_reflect` and a merged commit's trailer do.
        under_epoch = False
        ep = read_activity(self.codoc_dir).get("epoch") or {}
        is_open = bool(ep.get("open"))
        # A session killed without its hooks: close it, and reflect its turn
        # whether or not this daemon saw it open.
        stale = (is_open and epoch_age(self.codoc_dir, now) > EPOCH_STALE_SECONDS
                 and close_epoch(self.codoc_dir, ep.get("id", ""), now=now, stale_after=EPOCH_STALE_SECONDS))
        is_open = is_open and not stale
        if is_open and not self.state.epoch_open:
            self.state = DaemonState(epoch_open=True)
            notes.append("agent session opened")
        elif not is_open and (self.state.epoch_open or stale):
            code |= self.state.deferred | set(epoch_touched_files(self.codoc_dir))
            self.state = DaemonState()
            under_epoch = True
            notes.append("recovered a dead agent session" if stale else "agent session closed")
        # 4. Reflect.
        if code:
            if self.state.epoch_open:
                self.state.deferred |= code
                notes.append(f"deferring {len(code)} file(s) for the open session")
            else:
                notes.append(self.reflect(code, under_epoch=under_epoch, landed=landed))
        # 5. Settle what the drain landed, and a session that just closed.
        settle_if_idle(self.ws, sha=sha, now=now)
        # 6. Publish (the publisher skips the write when nothing moved).
        self.publisher.publish(self.ws.store, force=rebuilt)
        return "; ".join(notes)

    def reflect(self, files: set[str], *, under_epoch: bool = False, landed: frozenset[str] = frozenset()) -> str:
        from codoc.kinds.lift import lift_pass
        from codoc.loops.reflect import reflect
        cs = index_code(self.root, self.codoc_dir, self.graph, files, changeset=self._changeset)
        res = reflect(self.ws, cs, self.graph, propose=self._propose or _propose(),
                      doc_language=workspace_doc_language(self.codoc_dir), under_epoch=under_epoch, landed=landed)
        settle_if_idle(self.ws, res, sha=head(self.root)[0])
        # Kind blocks that derive from code (a diagram) follow the bindings.
        lifted = lift_pass(self.ws, self.graph, str(self.codoc_dir))
        return f"reflect: {res.summary()}" + (f" · {lifted} block(s) lifted" if lifted else "")

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> None:
        """Become the only applier: write the pidfile, then rebuild under the
        applier lock. A daemonless writer that applied after this store was
        built and before the pidfile appeared wrote a log this store has not
        read; one that takes the lock after this sees the pidfile and submits
        to the inbox (``client.applying``)."""
        # Limit: the logs are replayed twice at start; build the store after
        # the pidfile if start-up time on a long log matters.
        write_pidfile(self.codoc_dir)
        # Codoc owns `.codoc/.gitignore`, so a workspace an older codoc set up
        # takes the current one. Untracking a file git already tracks is the
        # person's commit to make.
        write_gitignore(self.codoc_dir)
        if is_tracked(self.root, self.codoc_dir / EXPORT):
            self.say(f"▸ {CODOC_DIR}/{EXPORT} is still tracked by git; "
                     f"`git rm --cached {CODOC_DIR}/{EXPORT}` untracks it")
        with locked(self.codoc_dir / APPLIER_LOCK):
            self.ws.rebuild()
        self.publisher.invalidate()

    def run(self, *, debounce_ms: int = DEBOUNCE_MS) -> None:  # pragma: no cover - blocking loop
        import atexit
        import signal
        import watchfiles
        if daemon_running(self.codoc_dir):
            self.say("A codoc daemon is already watching this repo.")
            raise SystemExit(3)
        self.start()
        atexit.register(clear_pidfile, self.codoc_dir)
        atexit.register(self.ws.close)
        # The hub and the editor stop the daemon with SIGTERM, whose default
        # skips atexit and would leave the pidfile behind.
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        self.say(f"codoc watching {self.root} (session {self.ws.session})")
        self.batch([])           # what clients wrote while no daemon ran
        for changes in watchfiles.watch(str(self.root), debounce=debounce_ms, watch_filter=self.wakes):
            self.batch([p for _c, p in changes])


# ── helpers ──────────────────────────────────────────────────────────────────

def index_code(root: str | Path, codoc_dir: str | Path, graph: Graph, files: set[str] = frozenset(), *,
               changeset=None):
    """Bring the chunk index up to date, update the code graph from it, and
    cache the graph where every reader loads it. ``files`` limits the
    comparison to what a batch saw change, and the graph re-extracts only
    their edges; with none, every file is compared and the graph is built
    whole (``codoc init``, ``codoc sync``). ``changeset(root, codoc_dir,
    files)`` stands in for the index in tests. Returns the change set."""
    cs = (changeset or _changeset)(str(root), str(codoc_dir), set(files))
    if files:
        graph.update(cs.rows, cs.touched_files())
    else:
        graph.rebuild(cs.rows)
    atomic_write(Path(codoc_dir) / GRAPH_CACHE, graph.dumps())
    return cs


def _changeset(root: str, codoc_dir: str, files: set[str]):
    from codoc.index.changes import compute_changeset
    return compute_changeset(root, codoc_dir, file_scope=files or None)


def _propose():
    from codoc.llm.passes import propose_tree_update_raw
    return propose_tree_update_raw
