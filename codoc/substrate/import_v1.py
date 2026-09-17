"""Import a v1 workspace (``.codoc/codoc.db``) as one bootstrap-like batch.

v1 kept whole descriptions with no character identity, so its history cannot
be replayed into runs without a diff deciding attribution, which the thesis
forbids. The import therefore writes each feature once: the heading, one
paragraph per blank-line-separated paragraph, its bindings, with
``actor`` taken from ``feature_writers`` (``human:import`` for a human-written
node, ``loop`` otherwise) so the amend gate keeps treating a person's prose
strictly. The old ledger stays readable through v1's own ``codoc history``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START, new_batch_id, new_block_id
from codoc.substrate.workspace import Workspace


def import_v1(ws: Workspace, db_path: str | Path) -> dict:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        feats = con.execute(
            "SELECT id, title, description, parent_id, rank, lifecycle FROM features "
            "WHERE lifecycle != 'retired' ORDER BY rank, created_at").fetchall()
        binds = con.execute("SELECT feature_id, file, symbol_path, fingerprint, types_hash FROM bindings").fetchall()
        try:
            writers = {r["feature_id"]: r["role"] for r in con.execute("SELECT feature_id, role FROM feature_writers")}
        except sqlite3.OperationalError:
            writers = {}
    finally:
        con.close()
    by_parent: dict[str | None, list] = {}
    for f in feats:
        by_parent.setdefault(f["parent_id"], []).append(f)
    batch = new_batch_id()
    fid_map: dict[str, str] = {}
    last = START
    count = 0

    def visit(parent: str | None, level: int) -> None:
        nonlocal last, count
        for f in by_parent.get(parent, []):
            actor = "human:import" if writers.get(f["id"]) == "human" else "loop"
            fid = new_block_id()
            fid_map[f["id"]] = fid
            ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": fid},
                        "payload": {"kind": HEADING, "level": min(level, 6), "after": last}}], actor=actor, batch=batch)
            ws.ingest(fid, "text", base_text="", new_md=(f["title"] or "Untitled").strip(), actor=actor, batch=batch)
            last = fid
            for para in [p.strip() for p in (f["description"] or "").split("\n\n") if p.strip()]:
                bid = new_block_id()
                ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": PARAGRAPH, "after": last}}],
                          actor=actor, batch=batch)
                ws.ingest(bid, "text", base_text="", new_md=para, actor=actor, batch=batch)
                last = bid
            count += 1
            visit(f["id"], level + 1)

    visit(None, 1)
    # Orphans (a parent that is retired or missing) land at the top level.
    for f in [f for f in feats if f["id"] not in fid_map]:
        by_parent[None] = [f]
        visit(None, 1)
    bad = [o for o in ws.store.ops if o.batch == batch and o.conflict]
    if bad:
        raise RuntimeError(f"import wrote {len(bad)} conflicting op(s); first: {bad[0].kind} {bad[0].conflict}")
    bound = 0
    for b in binds:
        fid = fid_map.get(b["feature_id"])
        if not fid:
            continue
        ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": fid},
                    "payload": {"file": b["file"], "symbol": b["symbol_path"], "fingerprint": b["fingerprint"] or "",
                                "types_hash": b["types_hash"] or ""}}], actor="loop", batch=batch)
        bound += 1
    return {"features": count, "bindings": bound, "map": fid_map}
