"""The derived files an applier writes after a turn (docs/model.md, Derived views).

    tree.codoc        the Markdown export (tracked)
    projection.json   what the editor and the hub render
    history.json      the timeline window: the last ops, each touched block's
                      text after them, and their recorded inverses
    realize.md        the queued directives, as the realizing agent reads them
    status.json       {state, pending, detail, head, at}

All derived, all regenerable, all written atomically. Nothing here is read
back by the calculus. ``status.json`` keeps one input the store does not hold:
the realizing lease. A realize pass takes it with ``write_realizing``, renews it
by rewriting the file, and it lapses ``REALIZING_LEASE_SECONDS`` after the last
write (``realizing_is_fresh``).

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

from codoc.calculus.derive import block_entry, ground, ink, open_plans, phase, plan_claims, projection
from codoc.calculus.export import export_markdown, export_spec
from codoc.calculus.store import Store
from codoc.calculus.window import history_window
from codoc.loops.realize import queued, write_queue
from codoc.workspace.layout import EXPORT, HISTORY, PROJECTION, REALIZE, STATUS, atomic_write

HISTORY_WINDOW = 200
REALIZING_LEASE_SECONDS = 300
# A realize pass names itself in status.json (``owner``) and in the environment
# of the agent it starts, so that agent does not read the lease of the pass it
# belongs to as another pass's and stand down.
REALIZE_OWNER_ENV = "CODOC_REALIZE_OWNER"


@dataclass
class Publisher:
    """The derived files for one workspace, written incrementally."""
    codoc_dir: Path
    root_dir: str = "."
    doc_language: object = None
    _entries: dict[str, tuple] = field(default_factory=dict)    # bid -> (key, entry)
    _specs: dict[str, tuple] = field(default_factory=dict)      # bid -> (key, export spec)
    _phases: dict[str, tuple] = field(default_factory=dict)     # fid -> (key, phase)
    _claims: dict[str, tuple] = field(default_factory=dict)     # fid -> (key, claims)
    _written: tuple | None = None                               # (store.pos, realizing lease)

    # ── cache keys ─────────────────────────────────────────────────────────
    @staticmethod
    def _block_key(store: Store, bid: str) -> tuple:
        return (store.block_head.get(bid, 0), store.plans_head)

    @staticmethod
    def _feature_key(store: Store, fid: str, realizing: set[str]) -> tuple:
        heads = [store.block_head.get(b, 0) for b in store.body_ids(fid)]
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
        p = projection(store, phases=phases, entry=lambda s, b: self._memo(
            self._entries, self._block_key(s, b.id), b.id, lambda: block_entry(s, b)))
        by_feature: dict[str, list[dict]] = {}
        by_file: dict[str, list[str]] = {}
        for b in store.bindings.values():
            by_feature.setdefault(b.feature, []).append({"file": b.file, "symbol": b.symbol})
            by_file.setdefault(b.file, [])
            if b.feature not in by_file[b.file]:
                by_file[b.file].append(b.feature)
        p["bindings"] = {"by_feature": by_feature, "by_file": by_file}
        p["claims"] = {}
        for fid in p["phases"]:
            claims = self._memo(self._claims, keys.get(fid) or self._feature_key(store, fid, realizing), fid,
                                lambda fid=fid: [c.__dict__ for c in ink(store, fid) + ground(store, fid) + plan_claims(store, fid)])
            if claims:
                p["claims"][fid] = claims
        return p

    def export(self, store: Store) -> str:
        return export_markdown(store, spec=lambda s, b: self._memo(
            self._specs, self._block_key(s, b.id), b.id, lambda: export_spec(s, b)))

    # ── files ──────────────────────────────────────────────────────────────
    def publish(self, store: Store, *, realizing: set[str] = frozenset(), force: bool = False) -> dict:
        """Write every derived file. Returns the status. Nothing is written
        when neither the store nor the realizing lease has moved since the last
        publish, unless ``force`` (a rebuild can change the tree without
        changing the op count)."""
        fresh = realizing_is_fresh(self.codoc_dir)
        stamp = (store.pos, tuple(sorted(realizing)), fresh)
        if not force and stamp == self._written:
            return read_status(self.codoc_dir)
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
        write_queue(self.codoc_dir, store, self.root_dir, self.doc_language)
        status = write_status(self.codoc_dir, store, phases=phases, realizing=bool(realizing) or None)
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
    ``realizing``, was written within the lease, and a queue is still there. A
    lease this process's own pass took (``REALIZE_OWNER_ENV`` names its owner)
    does not count."""
    path = Path(codoc_dir) / STATUS
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("state") != "realizing":
            return False
        if d.get("owner") and os.environ.get(REALIZE_OWNER_ENV) == d["owner"]:
            return False
        age = (now or time.time()) - path.stat().st_mtime
        return age < REALIZING_LEASE_SECONDS and (Path(codoc_dir) / REALIZE).exists()
    except (OSError, json.JSONDecodeError):
        return False


def write_realizing(codoc_dir: str | Path, *, pending: int | None = None, detail: str | None = None,
                    owner: str | None = None) -> dict:
    """Take or renew the realizing lease: set ``state: realizing`` in
    ``status.json`` and leave the fields not named as they are. The write is
    what renews the lease, since its clock is the file's mtime."""
    d = read_status(codoc_dir)
    d["state"] = "realizing"
    for key, value in (("pending", pending), ("detail", detail), ("owner", owner)):
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


def write_status(codoc_dir: str | Path, store: Store, *, phases: dict[str, str] | None = None,
                 realizing: bool | None = None, detail: str = "") -> dict:
    """Derive the workspace state and write it. A fresh realizing lease is
    preserved untouched (rewriting it would renew a lease this call only checked)."""
    path = Path(codoc_dir) / STATUS
    if realizing is None:
        if realizing_is_fresh(codoc_dir):
            return read_status(codoc_dir)
        realizing = False
    phases = phases if phases is not None else {h.id: phase(store, h.id) for h in store.headings()}
    q = queued(store)
    edited = sum(1 for p in phases.values() if p == "edited")
    proposals = len(open_plans(store))
    if realizing:
        state, pending = "realizing", len(q)
    elif q:
        state, pending = "queued", len(q)
        detail = detail or f"{len(q)} change(s) ready to implement — run /codoc:sync or codoc realize"
    elif edited:
        state, pending = "edited", edited
        detail = detail or f"{edited} feature(s) with unsent edits — hand off to build"
    elif proposals:
        state, pending = "proposed", proposals
        detail = detail or f"{proposals} proposal(s) awaiting your verdict"
    else:
        state, pending = "synced", len(phases)
    d = {"version": 2, "state": state, "pending": pending, "detail": detail, "head": store.pos,
         "features": len(phases), "at": time.time()}
    if realizing and (owner := read_status(codoc_dir).get("owner")):
        d["owner"] = owner              # the lease stays the pass's own across this rewrite
    atomic_write(path, json.dumps(d))
    return d


def publish(codoc_dir: str | Path, store: Store, *, root_dir: str = ".", realizing: set[str] = frozenset(),
            doc_language=None) -> dict:
    """One-shot publish for a process that holds no ``Publisher`` (the CLI, a
    client applying without a daemon)."""
    return Publisher(Path(codoc_dir), root_dir, doc_language).publish(store, realizing=realizing, force=True)
