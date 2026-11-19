"""Snapshots: fold the settled prefix of the log into one file
(docs/concurrency.md, Snapshots).

A snapshot is a session file whose header carries the ``Frontier`` it folded
and whose ops rebuild the store at that frontier: one ``block.add`` per block,
live or dead (a dead one carries the op that killed it, so an undo from a
branch forked before the fold still finds it), with its full Text (segments,
actors, clocks, marks) as ``texts``; one ``bind.attach`` per binding; one
``plan.open`` per plan with its status restored by a verdict; one
``feature.handoff`` per watermark, which sets both frontiers on the block
whatever its kind is now and carries the words the person asked for. None
carries an inverse, and replay gives a record without one none: they
re-express state and are nobody's gesture, so no undo picks the fold's batch.

**The fold stops at the first op of open work.** A verdict on an open plan
undoes the plan's own ops by identity, so those ops stay in the log. The fold
runs up to the first op that names a feature with an open plan on it, an open
plan, its target or one of its ops, or a plan decided or reopened after that
point, since it was open across it; everything from there on stays an
ordinary session log. A tree with no open plan folds whole.

Replay applies a snapshot first, then every op outside its frontier. Neither
is checked for authority again: an op was judged when it arrived, and replay
applies it as its record says.

Dead blocks are kept because anchors may still name their characters from a
branch forked before the snapshot. Attribution survives because segments carry
their actor, and the directive, drift and `ground` read clocks and actors kept
on segments, marks and watermarks. ``state_fingerprint`` is equal across a
fold and so is every derived view of the document; `history` and the timeline
are views of the log and lose the folded ops.
"""
from __future__ import annotations

import json
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.apply import replay_recorded
from codoc.calculus.derive import reads_its_ops
from codoc.calculus.doc import layout_entry
from codoc.calculus.ids import IMPORT, START
from codoc.calculus.ops import Op, frontier_union, in_frontier
from codoc.calculus.store import Store
from codoc.workspace import log as L
from codoc.workspace.clock import Clock
from codoc.workspace.epoch import epoch_alive
from codoc.workspace.layout import atomic_write

def settled_prefix(store: Store) -> int:
    """How many of ``store.ops`` may be folded: everything before the oldest op
    that something still unsettled was written by.

    A feature with no plan open on it is finished: its ops are history and
    the state they produced is enough, because every other reader reads state
    (``derive.reads_its_ops`` says which and why). Stopping at the oldest op of
    any feature that is not is conservative: the cut lands at that feature's
    creation rather than at the oldest op its verdict will need. It costs one
    linear pass more each time a plan is found in flight across the cut, where
    replaying forward to test each prefix would be quadratic.
    """
    live: set[str] = set()
    for b in list(store.blocks.values()):
        if b.kind == "heading" and reads_its_ops(store, b.id):
            live.update(x.id for x in store.body(b.id))
            live.add(b.id)
    for p in store.open_plans.values():
        live.update(p.ops)
        live.add(p.id)
        if p.target:
            live.add(p.target)
    while True:
        cut = next((i for i, op in enumerate(store.ops) if live & {
            op.target.get("bid"), op.target.get("fid"), op.target.get("into"), op.target.get("pid"),
            op.payload.get("bid"), op.payload.get("new"), op.id}), len(store.ops))
        # A plan answered or reopened after the cut was in flight across it: a
        # verdict there judges the plan's ops, so they stay, from its opening.
        late = {str(op.target.get("pid")) for op in store.ops[cut:] if op.kind in K.TURNS and op.conflict is None} - live
        if not late:
            return cut
        for pid in late:
            live.add(pid)
            live.update(store.plans[pid].ops if pid in store.plans else ())


def state_at(store: Store, cut: int) -> Store:
    """The store as the first ``cut`` ops left it. Folding everything is the
    store itself; a partial fold replays, which ``codoc snapshot`` can afford
    once for a log it is about to make shorter."""
    if cut >= len(store.ops):
        return store
    out = Store()
    for op in store.ops[:cut]:
        # As a rebuild replays: an op the applier REFUSED is refused again, not
        # re-judged against the state it would meet the second time. Otherwise
        # a stranger's refused op in a merged log becomes trunk text at the
        # next fold, and the snapshot asserts a state the log never described.
        replay_recorded(out, Op.from_json(json.loads(json.dumps(op.to_json()))))
    return out


def snapshot_ops(store: Store, session: str, clock: Clock) -> list[dict]:
    """The ops that rebuild ``store`` from nothing, as JSON records."""
    out: list[dict] = []
    seq = 0

    def emit(kind, target, payload, **extra):
        nonlocal seq
        seq += 1
        d = {"id": f"{session}:{seq}", "at": clock.tick(), "actor": IMPORT, "batch": session,
             "kind": kind, "target": target, "payload": payload}
        d.update(extra)
        out.append(d)
        return d["id"]

    prev = START
    for b in store.all_blocks():
        texts = {f: b.text(f).to_json() for f in b.layout.text}
        content = {k: v for k, v in b.content.items() if k not in b.layout.text}
        # `ranked_by` and `void` are how a verdict judges a move, and the
        # moves they name are what this fold takes away.
        payload = {"kind": b.kind, "level": b.level, "after": prev, "rank": b.rank, "ranked_by": b.ranked_by,
                   "content": content, "texts": texts, **layout_entry(b.kind, b.layout)}
        if b.added_by:
            payload["added_by"] = b.added_by       # who may write into a comment they added
        if b.added_plan:
            payload["added_plan"] = b.added_plan   # a verdict undone after the fold makes it a proposal again
        if b.prior:
            payload["prior"] = {k: [list(e) for e in v] for k, v in b.prior.items()}    # what `lower` compares against
        if b.void:
            payload["void"] = {k: list(v) for k, v in b.void.items()}
        if not b.live:
            payload["dead"] = b.dead      # the killer keeps its name, so an undo still matches
            if b.dead_actor:
                payload["dead_actor"], payload["dead_at"] = b.dead_actor, b.dead_at
            if b.dead_in:
                payload["dead_in"] = b.dead_in
        if b.detached:
            # What this dead heading owned: the phantom's settle asks whether
            # the agent removed exactly these, and the undo re-attaches them.
            # Entries marked `set` are what a kind change took off, which
            # only its undo may re-attach.
            payload["detached"] = [dict(e) for e in b.detached]
        if b.id in store.bound_once:
            # That this feature ever had code is state, not history: the reflect
            # pass proposes deleting a feature that had code and has none now,
            # and after a fold there is no `bind.attach` left to read.
            payload["bound_once"] = True
        w = store.watermarks_by_fid.get(b.id)
        if w is not None and (w.code_at or w.realized_at):
            # And so is WHEN the code under it last moved, and when a
            # realization last settled it: drift is the comparison of those two
            # with the prose's own clock, and the ops that carried them are
            # exactly what this fold is taking away.
            payload["code_at"], payload["realized_at"] = w.code_at, w.realized_at
        emit(K.BLOCK_ADD, {"bid": b.id}, payload)
        prev = b.id
    for bnd in store.bindings.values():
        emit(K.BIND_ATTACH, {"fid": bnd.feature}, {"symbol": bnd.symbol, "fingerprint": bnd.fingerprint,
                                                   "types_hash": bnd.types_hash, "set_by": bnd.set_by})
    for p in store.plans.values():
        # Every plan in a folded prefix is decided: an open one is work in
        # flight, so `settled_prefix` stopped before its first op.
        emit(K.PLAN_OPEN, {"pid": p.id}, {"kind": p.kind, "target": p.target, "note": p.note,
                                          "ops": list(p.ops), "opened_by": p.opened_by,
                                          **({"build": True} if p.build else {})}, actor=p.actor)
        # The person who answered, not the importer: after the fold the log
        # that said so is archived, and a verdict nobody signed is a verdict
        # the History card cannot explain.
        # It names the verdict it folded: the plan keeps it, and the blocks
        # above already hold what that verdict did.
        emit(K.PLAN_ACCEPT if p.status == "accepted" else K.PLAN_REJECT, {"pid": p.id}, {"verdict": p.verdict},
             actor=p.decided_by or IMPORT)
    for fid, w in store.watermarks_by_fid.items():
        if fid not in store.blocks or not (w.handoff or w.realized):
            continue
        # One op restores both frontiers on the block whatever its kind now
        # (`realized` makes it the fold's form). It comes after the bindings:
        # it is the latest hand-off, and a settle reads evidence from the ops
        # after that one, which must not include the attaches above.
        asked = store.by_id.get(w.handoffs[-1]) if w.handoff and w.handoffs else None
        payload = {"covers": w.handoff, "realized": w.realized}
        for k in ("asked", "delete_code"):
            if asked is not None and asked.payload.get(k):
                payload[k] = asked.payload[k]
        emit(K.FEATURE_HANDOFF, {"fid": fid}, payload, actor=asked.actor if asked is not None else IMPORT)
    return out


class TurnOpen(RuntimeError):
    """An agent's turn is open: a fold now would re-emit a queued hand-off
    under a new op id after the turn's build cited the old one, and the build
    would never settle."""


def write_snapshot(codoc_dir: str | Path, store: Store, clock: Clock) -> Path | None:
    """Write ``log/snapshot-<hlc>.jsonl`` folding the settled prefix of the log
    and archive the session files it covers whole. None when nothing is settled
    enough to fold. Raises ``TurnOpen`` while an agent epoch is open.

    A daemonless writer between calls may hold one of the archived files open;
    at its next call it finds the file gone and opens a new session over the
    logs on disk (``Workspace.follow_disk``), so nothing is appended under
    ``log/archive/``."""
    if epoch_alive(codoc_dir):
        raise TurnOpen("an agent's turn is open; its build settles when the turn ends. Snapshot after that.")
    cut = settled_prefix(store)
    if cut == 0:
        return None
    folded = state_at(store, cut)
    frontier: dict[str, int] = {}
    for op in store.ops[:cut]:
        sid, _, seq = op.id.rpartition(":")
        frontier = frontier_union(frontier, {sid: int(seq)})
    d = L.log_dir(codoc_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = clock.tick()
    session = f"snap-{stamp.split('-')[0]}"
    ops = snapshot_ops(folded, session, clock)
    path = d / f"{L.SNAPSHOT_PREFIX}{stamp}.jsonl"
    header = {"header": 1, "session": session, "opened": stamp, "frontier": frontier,
              "snapshot": True}
    atomic_write(path, "".join([json.dumps(header) + "\n"] + [json.dumps(o, ensure_ascii=False) + "\n" for o in ops]))
    archive = d / L.ARCHIVE_DIR
    archive.mkdir(exist_ok=True)
    # Only a file the fold covers whole: one that also holds ops after the cut
    # is still the log, and the frontier keeps its folded half from applying twice.
    for old in L.list_sessions(codoc_dir):
        sess = L.read_session(old)
        if sess.valid and sess.ops and all(in_frontier(o.id, frontier) for o in sess.ops):
            old.replace(archive / old.name)
    for old_snap in L.list_snapshots(codoc_dir):
        if old_snap != path:
            old_snap.replace(archive / old_snap.name)
    return path
