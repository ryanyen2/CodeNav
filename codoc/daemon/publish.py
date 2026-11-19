"""The derived files an applier writes after a turn (docs/model.md, Derived views).

    tree.codoc        the Markdown export
    projection.json   what the editor and the hub render
    history.json      the timeline window: the last ops, each touched block's
                      text after them, and their recorded inverses
    realize.md        the queued directives, as the realizing agent reads them
    status.json       {state, pending, detail, head, at, actor, doc_language}:
                      actor is the person at this machine, the one the editor
                      writes as; doc_language is the tag this process writes
                      prose in (``workspace_doc_language``), which the editor
                      and the hub show. A realizing lease adds {owner, pid,
                      renewed}

All derived, all regenerable, all written atomically. Nothing here is read
back by the calculus. ``status.json`` keeps one input the store does not hold:
the realizing lease. A realize pass takes it with ``write_realizing``, which
stamps ``renewed``. A lease that names its process holds while that process
lives, for at most an hour after its last renewal; one that names none lapses
``REALIZING_LEASE_SECONDS`` after its last renewal (``realizing_is_fresh``).

A ``Publisher`` memoizes the per-block views on the cache keys of
docs/model.md (a block's own head, the structural head, the plans head), so a settle in one paragraph
re-renders that paragraph and not the tree. The memo is a pure-function cache:
every key is a state counter the store advances when the answer can change, so
a cold ``publish()`` and a warm one write the same bytes (a property test).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from codoc.calculus.derive import (
    block_entry, export_markdown, export_spec, feature_claims, offered_plans, phase, projection, queued,
)
from codoc.calculus.store import Store
from codoc.calculus.window import history_window
from codoc.daemon.pidfile import pid_alive
from codoc.doclang import workspace_doc_language
from codoc.loops.realize import write_queue
from codoc.workspace.inbox import local_actor
from codoc.workspace.layout import EXPORT, HISTORY, PROJECTION, REALIZE, STATUS, atomic_write

HISTORY_WINDOW = 200
REALIZING_LEASE_SECONDS = 300            # a lease that names no process lapses this long after its last renewal
# A lease that names its process holds while that process lives, up to this
# long after its last renewal: a pass that died long ago may have left a pid
# another process now has. A live pass renews it with every progress report.
REALIZING_PID_LEASE_SECONDS = 3600
# A realize pass names itself in status.json (``owner``) and in the environment
# of the agent it starts, so that agent does not read the lease of the pass it
# belongs to as another pass's and stand down.
REALIZE_OWNER_ENV = "CODOC_REALIZE_OWNER"


@dataclass
class Publisher:
    """The derived files for one workspace, written incrementally."""
    codoc_dir: Path
    root_dir: str = "."
    _entries: dict[str, tuple] = field(default_factory=dict)    # bid -> (key, entry)
    _specs: dict[str, tuple] = field(default_factory=dict)      # bid -> (key, export spec)
    _phases: dict[str, tuple] = field(default_factory=dict)     # fid -> (key, phase)
    _claims: dict[str, tuple] = field(default_factory=dict)     # fid -> (key, claims)
    _written: tuple | None = None                               # (store.pos, lease fresh, language)

    # ── cache keys ─────────────────────────────────────────────────────────
    @staticmethod
    def _entry_key(store: Store, bid: str) -> tuple:
        return (store.block_head.get(bid, 0),)

    @staticmethod
    def _spec_key(store: Store, bid: str) -> tuple:
        return (store.block_head.get(bid, 0), store.plans_head)

    @staticmethod
    def _feature_key(store: Store, fid: str, realizing: set[str]) -> tuple:
        heads = [store.block_head.get(b.id, 0) for b in store.body(fid)]
        return (store.structural_head, max(heads, default=0), store.plans_head, fid in realizing)

    def _memo(self, cache: dict, key: tuple, ident: str, compute):
        hit = cache.get(ident)
        if hit is not None and hit[0] == key:
            return hit[1]
        value = compute()
        cache[ident] = (key, value)
        return value

    def _feature_keys(self, store: Store, realizing: set[str]) -> dict[str, tuple]:
        return {h.id: self._feature_key(store, h.id, realizing) for h in store.headings()}

    # ── views ──────────────────────────────────────────────────────────────
    def phases(self, store: Store, realizing: set[str] = frozenset(), keys: dict[str, tuple] | None = None) -> dict[str, str]:
        keys = keys if keys is not None else self._feature_keys(store, realizing)
        return {fid: self._memo(self._phases, key, fid, lambda fid=fid: phase(store, fid, realizing))
                for fid, key in keys.items()}

    def projection(self, store: Store, phases: dict[str, str], realizing: set[str] = frozenset(),
                   keys: dict[str, tuple] | None = None) -> dict:
        keys = keys if keys is not None else self._feature_keys(store, realizing)
        return projection(
            store, phases=phases,
            entry=lambda s, b: self._memo(self._entries, self._entry_key(s, b.id), b.id, lambda: block_entry(s, b)),
            claims=lambda s, fid: self._memo(self._claims, keys.get(fid) or self._feature_key(s, fid, realizing), fid,
                                             lambda: feature_claims(s, fid)))

    def export(self, store: Store) -> str:
        return export_markdown(store, spec=lambda s, b: self._memo(
            self._specs, self._spec_key(s, b.id), b.id, lambda: export_spec(s, b)))

    # ── files ──────────────────────────────────────────────────────────────
    def publish(self, store: Store, *, force: bool = False) -> dict:
        """Write every derived file. Returns the status. While another pass
        holds the realizing lease, every queued feature reads as being built.
        Nothing is written when neither the store nor the lease has moved since
        the last publish, unless ``force`` (a rebuild can change the tree
        without changing the op count). A new language is a move: it changes
        ``status.json`` and the queue."""
        fresh = realizing_is_fresh(self.codoc_dir)
        lang = workspace_doc_language(self.codoc_dir)
        stamp = (store.pos, fresh, lang.code)
        if not force and stamp == self._written:
            return read_status(self.codoc_dir)
        realizing = set(queued(store)) if fresh else set()
        keys = self._feature_keys(store, realizing)
        phases = self.phases(store, realizing, keys)
        atomic_write(self.codoc_dir / PROJECTION,
                     json.dumps(self.projection(store, phases, realizing, keys), ensure_ascii=False))
        atomic_write(self.codoc_dir / HISTORY,
                     json.dumps(history_window(store, HISTORY_WINDOW), ensure_ascii=False))
        text = self.export(store)
        path = self.codoc_dir / EXPORT
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            atomic_write(path, text)
        write_queue(self.codoc_dir, store, self.root_dir, lang)
        status = write_status(self.codoc_dir, store, phases=phases)
        self._written = stamp
        return status

    def invalidate(self) -> None:
        """Forget everything (a rebuild replaced the store)."""
        self._entries.clear()
        self._specs.clear()
        self._phases.clear()
        self._claims.clear()
        self._written = None


def read_status(codoc_dir: str | Path) -> dict:
    try:
        return json.loads((Path(codoc_dir) / STATUS).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def realizing_is_fresh(codoc_dir: str | Path, now: float | None = None) -> bool:
    """Whether another realize pass holds the lease: ``status.json`` says
    ``realizing``, a queue is still there, and the holder is alive. A lease
    that names the process holding it (``pid``) is alive while that process
    is, within ``REALIZING_PID_LEASE_SECONDS`` of its last renewal
    (``renewed``); one that names none lapses ``REALIZING_LEASE_SECONDS``
    after it. A lease with no ``renewed`` has lapsed. A lease this process's
    own pass took (``REALIZE_OWNER_ENV`` names its owner) does not count."""
    path = Path(codoc_dir) / STATUS
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("state") != "realizing" or not (Path(codoc_dir) / REALIZE).exists():
            return False
        if d.get("owner") and os.environ.get(REALIZE_OWNER_ENV) == d["owner"]:
            return False
        renewed = d.get("renewed")
        age = (now or time.time()) - (renewed if isinstance(renewed, (int, float)) else 0)
        if isinstance(d.get("pid"), int):
            return pid_alive(d["pid"]) and age < REALIZING_PID_LEASE_SECONDS
        return age < REALIZING_LEASE_SECONDS
    except (OSError, json.JSONDecodeError):
        return False


def write_realizing(codoc_dir: str | Path, *, pending: int | None = None, detail: str | None = None,
                    owner: str | None = None, pid: int | None = None) -> dict:
    """Take or renew the realizing lease: set ``state: realizing`` and
    ``renewed`` (now) in ``status.json`` and leave the fields not named as they
    are. ``pid`` names the process doing the work, which keeps the lease while
    it lives; without one, renewing is what keeps it."""
    d = read_status(codoc_dir)
    d["state"] = "realizing"
    d["renewed"] = time.time()
    for key, value in (("pending", pending), ("detail", detail), ("owner", owner), ("pid", pid)):
        if value is not None:
            d[key] = value
    atomic_write(Path(codoc_dir) / STATUS, json.dumps(d))
    return d


def renew_realizing(codoc_dir: str | Path) -> bool:
    """Renew the lease if one is held; never takes it. True when it renewed."""
    if read_status(codoc_dir).get("state") != "realizing":
        return False
    write_realizing(codoc_dir)
    return True


def store_state(store: Store, phases: dict[str, str]) -> tuple[str, int, str]:
    """``(state, pending, detail)`` as the store has it: queued hand-offs
    first, then features with unsent edits, then plans awaiting a verdict.
    ``phases`` is every feature's phase."""
    q = queued(store)
    edited = sum(1 for p in phases.values() if p == "edited")
    plans = len(offered_plans(store))
    if q:
        return "queued", len(q), f"{len(q)} hand-off(s) ready to build. Run /codoc:realize or codoc realize"
    if edited:
        return "edited", edited, f"{edited} feature(s) with unsent edits. Hand off to build"
    if plans:
        return "proposed", plans, f"{plans} plan(s) awaiting your verdict"
    return "synced", len(phases), ""


def write_status(codoc_dir: str | Path, store: Store, *, phases: dict[str, str] | None = None,
                 detail: str = "") -> dict:
    """Derive the workspace state and write it. A fresh realizing lease is
    left as it is, so the page shows the build until the pass ends. Only a
    changed language is written into it, so the page shows the language the
    daemon writes in during a build. ``renewed`` is never touched here, so a
    pass that died lets the lease lapse however often anything publishes."""
    path = Path(codoc_dir) / STATUS
    if realizing_is_fresh(codoc_dir):
        d = read_status(codoc_dir)
        lang = workspace_doc_language(codoc_dir).code
        if d.get("doc_language") != lang:
            d["doc_language"] = lang
            atomic_write(path, json.dumps(d))
        return d
    phases = phases if phases is not None else {h.id: phase(store, h.id) for h in store.headings()}
    state, pending, said = store_state(store, phases)
    d = {"version": 2, "state": state, "pending": pending, "detail": detail or said, "head": store.pos,
         "features": len(phases), "at": time.time(), "actor": local_actor(Path(codoc_dir).parent),
         "doc_language": workspace_doc_language(codoc_dir).code}
    atomic_write(path, json.dumps(d))
    return d


def publish(codoc_dir: str | Path, store: Store, *, root_dir: str = ".") -> dict:
    """One-shot publish for a process that holds no ``Publisher`` (the CLI, a
    client applying without a daemon)."""
    return Publisher(Path(codoc_dir), root_dir).publish(store, force=True)
