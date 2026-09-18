"""Snapshots: fold the log into one file and archive the rest (spec §7.5).

A snapshot is a session file whose header carries the ``Frontier`` it folded
and whose ops rebuild the store: one ``block.add`` per block, live or dead,
with its full Text (segments, actors, clocks, marks) as ``texts``; one
``bind.attach`` per binding; one ``plan.open`` per plan (carrying the ops it
owned and the op that opened it, which now live in the archive) with its
status restored by a verdict; one ``feature.handoff`` — with the words the
person asked for — and one ``feature.realized`` per watermark. Replay applies
a snapshot first, without the authority check (these ops were authorized when
they were written; a fold is not a new decision), then every op outside its
frontier with the check on.

Dead blocks are kept because anchors may still name their characters from a
branch forked before the snapshot. Attribution survives because segments
carry their actor. ``state_fingerprint`` is equal across a fold.
"""
from __future__ import annotations

import json
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.doc import text_fields
from codoc.calculus.ids import START
from codoc.calculus.store import Store
from codoc.substrate import log as L
from codoc.substrate.clock import Clock

SNAPSHOT_ACTOR = "import"


def snapshot_ops(store: Store, session: str, clock: Clock) -> list[dict]:
    """The ops that rebuild ``store`` from nothing, as JSON records."""
    out: list[dict] = []
    seq = 0

    def emit(kind, target, payload, **extra):
        nonlocal seq
        seq += 1
        d = {"id": f"{session}:{seq}", "at": clock.tick(), "actor": SNAPSHOT_ACTOR, "batch": session,
             "kind": kind, "target": target, "payload": payload}
        d.update(extra)
        out.append(d)
        return d["id"]

    prev = START
    for b in store.all_blocks():
        texts = {f: b.text(f).to_json() for f in text_fields(b.kind)}
        content = {k: v for k, v in b.content.items() if k not in text_fields(b.kind)}
        emit(K.BLOCK_ADD, {"bid": b.id}, {"kind": b.kind, "level": b.level, "after": prev, "rank": b.rank,
                                          "content": content, "texts": texts})
        if not b.live:
            emit(K.BLOCK_DELETE, {"bid": b.id}, {"ids": [b.id]})
        prev = b.id
    for bnd in store.bindings.values():
        emit(K.BIND_ATTACH, {"fid": bnd.feature}, {"file": bnd.file, "symbol": bnd.symbol,
                                                   "fingerprint": bnd.fingerprint, "types_hash": bnd.types_hash})
    for p in store.plans.values():
        emit(K.PLAN_OPEN, {"pid": p.id}, {"kind": p.kind, "target": p.target, "note": p.note,
                                          "ops": list(p.ops), "opened_by": p.opened_by}, actor=p.actor)
        if p.status == "accepted":
            emit(K.PLAN_ACCEPT, {"pid": p.id}, {})
        elif p.status == "rejected":
            emit(K.PLAN_REJECT, {"pid": p.id}, {})
    for fid, w in store.watermarks_by_fid.items():
        if fid not in store.blocks:
            continue
        if w.handoff:
            asked = store.by_id.get(w.handoffs[-1]) if w.handoffs else None
            payload = {"covers": w.handoff}
            for k in ("asked", "base_sha", "delete_code"):
                if asked is not None and asked.payload.get(k):
                    payload[k] = asked.payload[k]
            emit(K.FEATURE_HANDOFF, {"fid": fid}, payload)
        if w.realized:
            emit(K.FEATURE_REALIZED, {"fid": fid}, {"covers": w.realized})
    return out


def write_snapshot(codoc_dir: str | Path, store: Store, clock: Clock, *, node: str = "local") -> Path:
    """Write ``log/snapshot-<hlc>.jsonl`` folding the current frontier and move
    the folded session files to ``log/archive/``."""
    d = L.log_dir(codoc_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = clock.tick()
    session = f"snap-{stamp.split('-')[0]}"
    ops = snapshot_ops(store, session, clock)
    path = d / f"{L.SNAPSHOT_PREFIX}{stamp}.jsonl"
    header = {"header": 1, "session": session, "opened": stamp, "node": node, "frontier": dict(store.frontier),
              "snapshot": True}
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(header) + "\n")
        for o in ops:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
    tmp.replace(path)
    archive = d / L.ARCHIVE_DIR
    archive.mkdir(exist_ok=True)
    for old in L.list_sessions(codoc_dir):
        sess = L.read_session(old)
        if sess.valid and sess.session in store.frontier:
            old.replace(archive / old.name)
    for old_snap in L.list_snapshots(codoc_dir):
        if old_snap != path:
            old_snap.replace(archive / old_snap.name)
    return path
