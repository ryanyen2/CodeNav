"""The daemon (``codoc watch``): one process, the only applier
(docs/concurrency.md, Processes and The daemon's batch).

Per debounced batch of filesystem events it does, in order:

1. rotate the session log if ``HEAD`` moved (a commit, a checkout);
2. drain every client inbox into ops;
3. read the agent epoch (``activity.json``): while an interactive session is
   open, code changes accumulate instead of triggering a reflect pass, since the
   agent owns those files; when the epoch closes, one scoped reflect pass runs
   over everything it touched, and realizations are settled against the
   hand-offs it cited. A hard-killed session (no hook fired) is recovered after
   ``EPOCH_STALE_SECONDS`` of silence;
4. otherwise, reflect the changed code files;
5. publish the derived files.

``process_batch`` is pure enough to test with an injected reflect; ``run``
wires it to ``watchfiles``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from codoc.daemon.epoch import EPOCH_STALE_SECONDS, close_epoch, epoch_touched_files, read_epoch
from codoc.daemon.pidfile import clear_pidfile, daemon_running, write_pidfile
from codoc.daemon.publish import Publisher, realizing_is_fresh
from codoc.index.git import head_sha
from codoc.index.graph import Graph
from codoc.loops import realize as R
from codoc.workspace import Roles, Workspace
from codoc.workspace import log as L
from codoc.workspace.layout import ACTIVITY, APPLIER_LOCK, CODOC_DIR, GRAPH_CACHE, atomic_write, locked

CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".cpp", ".c", ".h"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache", CODOC_DIR}
DEBOUNCE_MS = 600


@dataclass
class DaemonState:
    epoch_open: bool = False
    epoch_id: str = ""
    suppressed: set[str] = field(default_factory=set)
    head: str = ""


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
        self.state = DaemonState(head=head_sha(self.root))
        self.say = printer
        self.after_batch = after_batch       # called with the daemon after every watched batch
        self._reflect = reflect
        self._changeset = changeset
        self._propose = propose
        self.identity = L.actor_for          # inbox file name → actor (log.CLIENT_IDENTITY, hub-<login>)
        self.publisher = Publisher(self.codoc_dir, str(self.root))
        self._logs_at_start = _log_sizes(self.codoc_dir, self.ws.session)

    # ── routing ────────────────────────────────────────────────────────────
    def classify(self, paths: list[str]) -> tuple[set[str], bool]:
        """``(code_files, activity_touched)``. An inbox change needs no flag:
        every batch drains the inboxes."""
        code: set[str] = set()
        activity = False
        act = (self.codoc_dir / ACTIVITY).resolve()
        for p in paths:
            rp = Path(p).resolve()
            if rp == act:
                activity = True
            elif any(part in SKIP_DIRS for part in rp.parts):
                continue
            elif rp.suffix in CODE_EXTENSIONS and self.root.resolve() in rp.parents:
                code.add(str(rp.relative_to(self.root.resolve())))
        return code, activity

    def process_batch(self, paths: list[str], *, now: float | None = None) -> str:
        now = now if now is not None else time.time()
        code, activity = self.classify(paths)
        notes: list[str] = []
        rebuilt = False
        # 1. HEAD moved → the set of logs on disk may have changed (a checkout,
        # a pull): rebuild from them, then start a new session file.
        sha = head_sha(self.root)
        if sha != self.state.head:
            self.state.head = sha
            self.ws.rebuild()
            self.ws.rotate_session()
            self.publisher.invalidate()      # a different set of logs is a different tree
            rebuilt = True
            notes.append("session rotated (HEAD moved)")
        # 2. Client ops, under the roles the tracked config names right now
        # (the hub adds a login's role when they sign in). Every batch drains:
        # the drain reads each inbox from its saved offset, so this is cheap,
        # and a batch with no inbox event (startup, `codoc sync`) still picks
        # up what clients wrote while no daemon was running.
        self.ws.roles.table = Roles.load(self.codoc_dir).table
        done = self.ws.drain_inbox(self.identity)
        if done:
            notes.append(f"{len(done)} client op(s)")
        # 3. Epoch edges and stale recovery. A pass that runs because an agent
        # session ended is the one kind of evidence that settles a hand-off.
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
                notes.append(self.reflect(code, under_epoch=under_epoch))
        # 5. Publish (the publisher skips the write when nothing moved).
        realizing = set(R.queued(self.ws.store)) if realizing_is_fresh(self.codoc_dir, now) else set()
        self.publisher.publish(self.ws.store, realizing=realizing, force=rebuilt)
        return "; ".join(notes) or "no-op"

    def reflect(self, files: set[str], *, under_epoch: bool = False) -> str:
        from codoc.loops.reflect import reflect as _reflect
        cs = (self._changeset or _changeset)(str(self.root), str(self.codoc_dir), files)
        if cs is None:
            return "index unavailable"
        self.graph.update(cs.rows, cs.touched_files())
        atomic_write(self.codoc_dir / GRAPH_CACHE, self.graph.dumps())
        res = (self._reflect or _reflect)(self.ws, cs, self.graph, propose=self._propose or _propose(),
                                          under_epoch=under_epoch)
        if under_epoch:
            R.settle(self.ws, res, sha=head_sha(self.root), files=sorted(files))
        # Kind blocks that derive from code (a diagram) follow the bindings.
        from codoc.kinds.lift import lift_pass
        lifted = lift_pass(self.ws, self.graph, str(self.codoc_dir))
        return f"reflect: {res.summary()}" + (f" · {lifted} block(s) lifted" if lifted else "")

    # ── lifecycle ──────────────────────────────────────────────────────────
    def take_over(self) -> bool:
        """Become the only applier. Call once the pidfile is written: from then
        on every writer submits to the inbox. A writer that applied without a
        daemon between this store's build and the pidfile wrote a log this
        store has not read, so wait for its lock and rebuild if any log but
        this session's changed. Returns whether it rebuilt."""
        with locked(self.codoc_dir / APPLIER_LOCK):
            now = _log_sizes(self.codoc_dir, self.ws.session)
            if now == self._logs_at_start:
                return False
            self.ws.rebuild()
            self.publisher.invalidate()
            self._logs_at_start = now
            return True

    def run(self, *, debounce_ms: int = DEBOUNCE_MS) -> None:  # pragma: no cover - blocking loop
        import atexit
        import watchfiles
        if daemon_running(self.codoc_dir):
            self.say("A codoc daemon is already watching this repo.")
            raise SystemExit(3)
        write_pidfile(self.codoc_dir)
        atexit.register(clear_pidfile, self.codoc_dir)
        atexit.register(self.ws.close)
        self.take_over()
        self.process_batch([])   # absorb anything that queued while we were down
        self.say(f"codoc watching {self.root} (session {self.ws.session})")
        for changes in watchfiles.watch(str(self.root), debounce=debounce_ms, watch_filter=_filter(self.codoc_dir)):
            paths = [p for _c, p in changes]
            try:
                note = self.process_batch(paths)
            except Exception as e:  # noqa: BLE001 — a bad batch must not kill the daemon
                note = f"error: {e}"
            self.say(f"▸ {note}")
            if self.after_batch:
                self.after_batch(self)


# ── helpers ──────────────────────────────────────────────────────────────────

def _log_sizes(codoc_dir: Path, own_session: str) -> dict[str, int]:
    """Every session log's size but this applier's own."""
    return {p.name: p.stat().st_size for p in L.log_dir(codoc_dir).glob("*.jsonl")
            if p.name != f"{own_session}.jsonl"}


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


def _filter(codoc_dir: Path):
    inbox = L.inbox_dir(codoc_dir).resolve()
    act = (codoc_dir / ACTIVITY).resolve()

    def keep(_change, path: str) -> bool:
        rp = Path(path).resolve()
        if rp == act or (inbox in rp.parents and not rp.name.endswith(".ack.jsonl")):
            return True
        if any(part in SKIP_DIRS for part in rp.parts):
            return False
        return rp.suffix in CODE_EXTENSIONS
    return keep
