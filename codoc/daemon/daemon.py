"""The daemon (``codoc watch``): one process, the only applier
(docs/concurrency.md, Processes and The daemon's batch).

Per debounced batch of filesystem events it does, in order:

1. rotate the session log if ``HEAD`` moved (a commit, a checkout), noting
   the hand-offs the new commits name in a ``Codoc-Handoff`` trailer when
   ``HEAD`` moved forward on the branch it was on;
2. drain every client inbox into ops, warning once about an inbox file whose
   name maps to no actor;
3. read the agent epoch (``activity.json``): while an interactive session is
   open, code changes accumulate instead of triggering a reflect pass, since the
   agent owns those files; when the epoch closes, one scoped reflect pass runs
   over everything it touched and cites every queued hand-off. A hard-killed
   session (no hook fired) is recovered after ``EPOCH_STALE_SECONDS`` of silence;
4. otherwise, reflect the changed code files, citing the hand-offs of step 1;
5. publish the derived files.

After a reflect pass and at the end of every batch, when no agent epoch is
open, every queued hand-off that an applied binding op now cites is settled
(``loops.realize.settle``), whoever wrote the op.

``process_batch`` is pure enough to test with an injected reflect; ``run``
wires it to ``watchfiles``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path, PurePosixPath

from codoc.daemon.epoch import EPOCH_STALE_SECONDS, close_epoch, epoch_alive, epoch_touched_files, read_epoch
from codoc.daemon.pidfile import clear_pidfile, daemon_running, write_pidfile
from codoc.daemon.publish import Publisher, realizing_is_fresh
from codoc.index.git import handoffs_landed, head_branch, head_sha
from codoc.index.graph import Graph
from codoc.index.lang import detect_language
from codoc.loops import realize as R
from codoc.workspace import Roles, Workspace
from codoc.workspace import log as L
from codoc.workspace.layout import ACTIVITY, APPLIER_LOCK, CODOC_DIR, GRAPH_CACHE, atomic_write, locked

DEBOUNCE_MS = 600


@dataclass
class DaemonState:
    epoch_open: bool = False
    epoch_id: str = ""
    suppressed: set[str] = field(default_factory=set)
    head: str = ""
    branch: str = ""


class Daemon:
    def __init__(self, root_dir: str | Path, codoc_dir: str | Path | None = None, *, reflect=None,
                 changeset=None, propose=None, printer=print, after_batch=None) -> None:
        self.root = Path(root_dir)
        self.codoc_dir = Path(codoc_dir or self.root / CODOC_DIR)
        self.ws = Workspace(self.codoc_dir, node=_node_name())
        # A torn last line (a crash mid-write) is cut so every later rebuild
        # agrees with this one; every other log problem is reported.
        for f in self.ws.torn:
            if L.truncate_torn(f):
                printer(f"▸ cut a torn last line off {f.path.name}")
        for p in self.ws.problems:
            printer(f"▸ log problem: {p.path}:{p.line}: {p.reason}")
        self.graph = Graph.load(self.codoc_dir / GRAPH_CACHE)
        self.state = DaemonState(head=head_sha(self.root), branch=head_branch(self.root))
        self.say = printer
        self.after_batch = after_batch       # called with the daemon after every watched batch
        self._reflect = reflect
        self._changeset = changeset
        self._propose = propose
        self._unknown: set[str] = set()
        self.publisher = Publisher(self.codoc_dir, str(self.root))

    def identity(self, client: str, codoc_dir: Path) -> str | None:
        """The actor an inbox file writes as (``L.actor_for``). A file whose
        name maps to no actor is never drained, so say so once."""
        actor = L.actor_for(client, codoc_dir)
        if actor is None and client not in self._unknown:
            self._unknown.add(client)
            self.say(f"▸ not draining {L.inbox_dir(codoc_dir) / client}.jsonl: no actor has that name. "
                     "An older codoc MCP server wrote it; restart the agent so it runs the current one.")
        return actor

    # ── routing ────────────────────────────────────────────────────────────
    @cached_property
    def _source(self):
        # ponytail: built on first use (importing the index takes seconds) and
        # once, so a .gitignore edited while the daemon runs counts from its next start.
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

    def classify(self, paths: list[str]) -> tuple[set[str], bool]:
        """``(code_files, activity_touched)``. An inbox change needs no flag:
        every batch drains the inboxes."""
        code: set[str] = set()
        activity = False
        act = (self.codoc_dir / ACTIVITY).resolve()
        for p in paths:
            if Path(p).resolve() == act:
                activity = True
            elif (rel := self.code_path(p)) is not None:
                code.add(rel)
        return code, activity

    def process_batch(self, paths: list[str], *, now: float | None = None) -> str:
        now = now if now is not None else time.time()
        code, activity = self.classify(paths)
        notes: list[str] = []
        rebuilt = False
        # 1. HEAD moved → the set of logs on disk may have changed (a checkout,
        # a pull): rebuild from them, then start a new session file.
        sha, branch = head_sha(self.root), head_branch(self.root)
        landed: frozenset[str] = frozenset()
        if sha != self.state.head:
            # A trailer counts only when HEAD moved forward on the branch it was
            # on (a commit, a pull, a merge). Checking out a hub pull request's
            # branch to review it merges nothing.
            # ponytail: the trailer is read when the batch that sees HEAD move
            # reflects the merged files; a merge whose files were reflected in an
            # earlier batch (a conflicted merge committed later), or that no batch
            # saw on the same branch, cites nothing, and `codoc done` settles it.
            if branch and branch == self.state.branch:
                landed = handoffs_landed(self.root, self.state.head, sha)
            self.state.head = sha
            self.ws.rebuild()
            self.ws.rotate_session()
            self.publisher.invalidate()      # a different set of logs is a different tree
            rebuilt = True
            notes.append("session rotated (HEAD moved)")
        self.state.branch = branch
        # 2. Client ops, under the roles the tracked config names right now
        # (the hub adds a login's role when they sign in). Every batch drains:
        # the drain reads each inbox from its saved offset, so this is cheap,
        # and a batch with no inbox event (startup, `codoc sync`) still picks
        # up what clients wrote while no daemon was running.
        self.ws.roles.table = Roles.load(self.codoc_dir).table
        done = self.ws.drain_inbox(self.identity)
        if done:
            notes.append(f"{len(done)} client op(s)")
        # 3. Epoch edges and stale recovery. The pass that runs because an agent
        # session ended cites the queued hand-offs, as an agent's own
        # `codoc_reflect` and a merged commit's trailer do.
        under_epoch = False
        if self.state.epoch_open and _stale(self.codoc_dir, now):
            dead = self.state.epoch_id
            files = self.state.suppressed | set(epoch_touched_files(self.codoc_dir))
            self.state.epoch_open = False
            self.state.suppressed = set()
            if close_epoch(self.codoc_dir, dead, now=now, stale_after=EPOCH_STALE_SECONDS):
                code |= files
                under_epoch = True
                notes.append("recovered a dead agent session")
        if activity:
            ep = read_epoch(self.codoc_dir)
            if ep is not None:
                if ep.get("open") and not self.state.epoch_open:
                    self.state.epoch_open = True
                    self.state.epoch_id = ep.get("id", "")
                    self.state.suppressed = set()
                    notes.append("agent session opened")
                elif self.state.epoch_open and not ep.get("open") and ep.get("id") == self.state.epoch_id:
                    self.state.epoch_open = False
                    code |= self.state.suppressed | set(epoch_touched_files(self.codoc_dir))
                    self.state.suppressed = set()
                    under_epoch = True
                    notes.append("agent session closed")
        # 4. Reflect.
        if code:
            if self.state.epoch_open:
                self.state.suppressed |= code
                notes.append(f"holding {len(code)} file(s) for the open session")
            else:
                notes.append(self.reflect(code, under_epoch=under_epoch, landed=landed))
        self._settle(sha=sha, now=now)       # what the drain landed, and a session that just closed
        # 5. Publish (the publisher skips the write when nothing moved).
        realizing = set(R.queued(self.ws.store)) if realizing_is_fresh(self.codoc_dir, now) else set()
        self.publisher.publish(self.ws.store, realizing=realizing, force=rebuilt)
        return "; ".join(notes) or "no-op"

    def reflect(self, files: set[str], *, under_epoch: bool = False, landed: frozenset[str] = frozenset()) -> str:
        from codoc.loops.reflect import reflect as _reflect
        cs = (self._changeset or _changeset)(str(self.root), str(self.codoc_dir), files)
        if cs is None:
            return "index unavailable"
        self.graph.update(cs.rows, cs.touched_files())
        atomic_write(self.codoc_dir / GRAPH_CACHE, self.graph.dumps())
        res = (self._reflect or _reflect)(self.ws, cs, self.graph, propose=self._propose or _propose(),
                                          under_epoch=under_epoch, landed=landed)
        self._settle(res, sha=head_sha(self.root))
        # Kind blocks that derive from code (a diagram) follow the bindings.
        from codoc.kinds.lift import lift_pass
        lifted = lift_pass(self.ws, self.graph, str(self.codoc_dir))
        return f"reflect: {res.summary()}" + (f" · {lifted} block(s) lifted" if lifted else "")

    def _settle(self, res=None, *, sha: str, now: float | None = None) -> None:
        """Settle the hand-offs binding ops now cite, unless an agent epoch is
        open: its build is in progress, so the feature stays queued and the
        agent's amends land as plans. The close runs the same scan."""
        if not epoch_alive(self.codoc_dir, now=now):
            R.settle(self.ws, res, sha=sha)

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> None:
        """Become the only applier: write the pidfile, then rebuild under the
        applier lock. A daemonless writer that applied after this store was
        built and before the pidfile appeared wrote a log this store has not
        read; one that takes the lock after this sees the pidfile and submits
        to the inbox (``client.applying``)."""
        # ponytail: the logs are replayed twice at start; build the store after
        # the pidfile if start-up time on a long log matters.
        write_pidfile(self.codoc_dir)
        with locked(self.codoc_dir / APPLIER_LOCK):
            self.ws.rebuild()
        self.publisher.invalidate()

    def run(self, *, debounce_ms: int = DEBOUNCE_MS) -> None:  # pragma: no cover - blocking loop
        import atexit
        import watchfiles
        if daemon_running(self.codoc_dir):
            self.say("A codoc daemon is already watching this repo.")
            raise SystemExit(3)
        self.start()
        atexit.register(clear_pidfile, self.codoc_dir)
        atexit.register(self.ws.close)
        self.process_batch([])   # absorb anything that queued while we were down
        self.say(f"codoc watching {self.root} (session {self.ws.session})")
        for changes in watchfiles.watch(str(self.root), debounce=debounce_ms, watch_filter=_filter(self.codoc_dir, self.code_path)):
            paths = [p for _c, p in changes]
            try:
                note = self.process_batch(paths)
            except Exception as e:  # noqa: BLE001 (a bad batch must not kill the daemon)
                note = f"error: {e}"
            self.say(f"▸ {note}")
            if self.after_batch:
                self.after_batch(self)


# ── helpers ──────────────────────────────────────────────────────────────────

def _node_name() -> str:
    import socket
    return (socket.gethostname() or "local").split(".")[0][:24]


def _stale(codoc_dir: Path, now: float) -> bool:
    try:
        return now - (codoc_dir / ACTIVITY).stat().st_mtime > EPOCH_STALE_SECONDS
    except OSError:
        return True


def _changeset(root: str, codoc_dir: str, files: set[str]):
    from codoc.index.changes import compute_changeset
    return compute_changeset(root, codoc_dir, file_scope=files or None)


def _propose():
    from codoc.llm.passes import propose_tree_update_raw
    return propose_tree_update_raw


def _filter(codoc_dir: Path, code_path):
    inbox = L.inbox_dir(codoc_dir).resolve()
    act = (codoc_dir / ACTIVITY).resolve()

    def keep(_change, path: str) -> bool:
        rp = Path(path).resolve()
        return (rp == act or (inbox in rp.parents and not rp.name.endswith(".ack.jsonl"))
                or code_path(path) is not None)
    return keep
