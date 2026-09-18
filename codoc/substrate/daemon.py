"""The daemon: one process, the only applier (spec §1, §7.1).

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

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from codoc.calculus.derive import phase
from codoc.graph.graph import Graph
from codoc.loop.activity import close_epoch, epoch_touched_files, read_epoch
from codoc.loop.gitref import head_sha
from codoc.loops import realize as R
from codoc.substrate import log as L
from codoc.substrate.publish import Publisher, realizing_is_fresh
from codoc.substrate.workspace import Roles, Workspace

CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".cpp", ".c", ".h"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache", ".codoc"}
EPOCH_STALE_SECONDS = 900
DEBOUNCE_MS = 600
GRAPH_CACHE = "cache/graph.json"
PIDFILE = "watch.pid"


@dataclass
class DaemonState:
    epoch_open: bool = False
    epoch_id: str = ""
    epoch_origin: str = ""
    suppressed: set[str] = field(default_factory=set)
    head: str = ""


class Daemon:
    def __init__(self, root_dir: str | Path, codoc_dir: str | Path | None = None, *, reflect=None,
                 changeset=None, propose=None, printer=print, auto_realize: bool = False) -> None:
        self.root = Path(root_dir)
        self.codoc_dir = Path(codoc_dir or self.root / ".codoc")
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
        self.auto_realize = auto_realize
        self._reflect = reflect
        self._changeset = changeset
        self._propose = propose
        self.identity = L.actor_for          # inbox file name → actor (log.CLIENT_IDENTITY, hub-<login>)
        self.publisher = Publisher(self.codoc_dir, str(self.root))

    # ── routing ────────────────────────────────────────────────────────────
    def classify(self, paths: list[str]) -> tuple[set[str], bool, bool]:
        """``(code_files, inbox_touched, activity_touched)``."""
        code: set[str] = set()
        inbox = activity = False
        inbox_dir = L.inbox_dir(self.codoc_dir).resolve()
        act = (self.codoc_dir / "activity.json").resolve()
        for p in paths:
            rp = Path(p).resolve()
            if rp == act:
                activity = True
            elif inbox_dir in rp.parents and rp.suffix == ".jsonl" and not rp.name.endswith(".ack.jsonl"):
                inbox = True
            elif any(part in SKIP_DIRS for part in rp.parts):
                continue
            elif rp.suffix in CODE_EXTENSIONS and self.root.resolve() in rp.parents:
                code.add(str(rp.relative_to(self.root.resolve())))
        return code, inbox, activity

    def process_batch(self, paths: list[str], *, now: float | None = None) -> str:
        now = now if now is not None else time.time()
        code, inbox, activity = self.classify(paths)
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
        # (the hub adds a login's role when they sign in).
        if inbox:
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
                    self.state.epoch_origin = ep.get("origin", "interactive")
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
        self.graph.save(self.codoc_dir / GRAPH_CACHE)
        res = (self._reflect or _reflect)(self.ws, cs, self.graph, propose=self._propose or _propose(),
                                          under_epoch=under_epoch)
        if under_epoch:
            R.settle(self.ws, res, sha=head_sha(self.root), files=sorted(files))
        # Kind blocks that derive from code (a diagram) follow the bindings.
        from codoc.kinds.lift import lift_pass
        lifted = lift_pass(self.ws, self.graph, str(self.codoc_dir))
        return f"reflect: {res.summary()}" + (f" · {lifted} block(s) lifted" if lifted else "")

    # ── lifecycle ──────────────────────────────────────────────────────────
    def run(self, *, debounce_ms: int = DEBOUNCE_MS) -> None:  # pragma: no cover - blocking loop
        import atexit
        import watchfiles
        if daemon_running(self.codoc_dir):
            self.say("A codoc daemon is already watching this repo.")
            raise SystemExit(3)
        write_pidfile(self.codoc_dir)
        atexit.register(clear_pidfile, self.codoc_dir)
        atexit.register(self.ws.close)
        self.process_batch([])   # absorb anything that queued while we were down
        self.say(f"codoc watching {self.root} (session {self.ws.session})")
        for changes in watchfiles.watch(str(self.root), debounce=debounce_ms, watch_filter=_filter(self.codoc_dir)):
            paths = [p for _c, p in changes]
            try:
                note = self.process_batch(paths)
            except Exception as e:  # noqa: BLE001 — a bad batch must not kill the daemon
                note = f"error: {e}"
            self.say(f"▸ {note}")
            if self.auto_realize:
                self.maybe_auto_realize()

    def maybe_auto_realize(self) -> None:  # pragma: no cover - spawns a process
        """Unattended fallback: run the queue when no session is live."""
        from codoc.loop import autorealize
        proc = getattr(self, "_realize_proc", None)
        in_flight = proc is not None and proc.poll() is None
        if self.state.epoch_open or not autorealize.should_spawn(str(self.codoc_dir), in_flight=in_flight):
            return
        self._realize_proc = autorealize.spawn_realize(str(self.root), str(self.codoc_dir))


# ── helpers ──────────────────────────────────────────────────────────────────

def _node_name() -> str:
    import socket
    return (socket.gethostname() or "local").split(".")[0][:24]


def _stale(codoc_dir: Path, now: float) -> bool:
    try:
        return now - (codoc_dir / "activity.json").stat().st_mtime > EPOCH_STALE_SECONDS
    except OSError:
        return True


def _changeset(root: str, codoc_dir: str, files: set[str]):
    from codoc.loop.diff import compute_changeset
    return compute_changeset(root, codoc_dir, file_scope=files or None)


def _propose():
    from codoc.agent.tree_update import propose_tree_update_raw
    return propose_tree_update_raw


def _filter(codoc_dir: Path):
    inbox = L.inbox_dir(codoc_dir).resolve()
    act = (codoc_dir / "activity.json").resolve()

    def keep(_change, path: str) -> bool:
        rp = Path(path).resolve()
        if rp == act or (inbox in rp.parents and not rp.name.endswith(".ack.jsonl")):
            return True
        if any(part in SKIP_DIRS for part in rp.parts):
            return False
        return rp.suffix in CODE_EXTENSIONS
    return keep


def _pidfile(codoc_dir: Path) -> Path:
    return codoc_dir / PIDFILE


def write_pidfile(codoc_dir: Path) -> None:
    _pidfile(codoc_dir).write_text(str(os.getpid()))


def clear_pidfile(codoc_dir: Path) -> None:
    p = _pidfile(codoc_dir)
    try:
        if p.read_text().strip() == str(os.getpid()):
            p.unlink()
    except OSError:
        pass


def daemon_running(codoc_dir: Path) -> bool:
    p = _pidfile(codoc_dir)
    try:
        pid = int(p.read_text().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        p.unlink(missing_ok=True)
        return False
