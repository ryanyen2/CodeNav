"""The agent's read and write surface, used by the MCP server.

Reads replay the logs into a read-only workspace and derive from it. Writes
go through ``daemon.client`` (the daemon's inbox when a daemon owns the
checkout, the applier otherwise) and are stamped ``actor=agent:<id>`` by the
receiving side, never by the agent.

Two writes take tree edits (``loops.edits``). Every edit is compiled against
the current tree before anything is written; if one is refused, nothing is
written and the error names it.

| tool | edit | lands |
|---|---|---|
| ``reflect`` | ``attach``, ``detach`` on an existing feature | on the trunk |
| ``reflect`` | ``amend`` | ``Workspace.amend``: all of it on the trunk when it only rewords and passes the machine gate, else one plan |
| ``reflect`` | ``add_node``, ``move_node``, ``retire_node`` | as ``propose`` writes them |
| ``propose`` | all | one plan for everything but removals; each ``retire_node`` is its own delete plan |

`codoc accept` hands off every feature the plan touches with the verdict; the
editor's Accept sends the verdict alone.
"""
from __future__ import annotations

from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.derive import (
    attribution, dead_cites, description, history as feature_history, open_plans, phase, title,
)
from codoc.calculus.doc import HEADING, fold_tree, text_fields
from codoc.calculus.ids import new_plan_id
from codoc.daemon import client
from codoc.daemon.publish import read_status as published_status
from codoc.daemon.publish import realizing_is_fresh, write_realizing
from codoc.doclang import detect_prose_language, workspace_doc_language
from codoc.index.graph import Graph
from codoc.llm.prompt import titles_outline
from codoc.loops.context import description_blocks, select_context, written_by
from codoc.calculus.block_md import BlockSpec
from codoc.calculus.ingest import block_bodies, strike_bodies
from codoc.loops.edits import Change, Compiler
from codoc.loops.realize import queued
from codoc.mcp.walkthrough import AskStep, build_walkthrough, clear_walkthrough, read_walkthrough, write_walkthrough
from codoc.workspace.layout import GRAPH_CACHE

AGENT = "mcp"


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _open(codoc_dir: str):
    ws = client.read(codoc_dir)
    return ws, Graph.load(Path(codoc_dir) / GRAPH_CACHE)


def doc_language_block(codoc_dir: str) -> dict:
    lang = workspace_doc_language(codoc_dir)
    block = {"code": lang.code, "name": lang.name}
    if not lang.is_default:
        block["instruction"] = (f"This tree is authored in {lang.name}. Write every title, paragraph and note in "
                                f"{lang.name}; never translate identifiers, paths or codoc: targets.")
    return block


def _lang(text: str, default) -> dict:
    got = detect_prose_language(text, default)
    return {"lang": got.code} if got.code != default.code else {}


# ── reads ────────────────────────────────────────────────────────────────────

def read_context(codoc_dir: str, *, files: list[str] | None = None, feature_id: str | None = None,
                 include_bindings: bool = True) -> dict:
    ws, graph = _open(codoc_dir)
    store = ws.store
    file_set = set(files or [])
    symbols: set[str] = set()
    if feature_id:
        if feature_id not in store.blocks or store.blocks[feature_id].kind != HEADING:
            return _err(f"unknown feature_id {feature_id!r}")
        for b in store.bindings.values():
            if b.feature == feature_id:
                file_set.add(b.file)
                symbols.add(b.symbol)
    if not file_set and not symbols:
        return _err("pass files=[...] and/or feature_id")
    subtree, all_titles, ctx = select_context(store, graph, file_set, symbols)
    default = workspace_doc_language(codoc_dir)
    for row in subtree:
        row.update(_lang(" ".join(b["text"] for b in row.get("blocks", [])) or row["title"], default))
        if not include_bindings:
            row["binding_count"] = len(row.pop("bindings", []))
    return {"ok": True, "subtree": subtree, "titles_outline": titles_outline(all_titles), "graph": ctx,
            "doc_language": doc_language_block(codoc_dir)}


def read_tree(codoc_dir: str, *, root_id: str | None = None, depth: int = 0, include_bindings: bool = False) -> dict:
    ws, _ = _open(codoc_dir)
    store = ws.store
    heads = store.headings()
    ids = {h.id for h in heads}
    if root_id and root_id not in ids:
        return _err(f"unknown root_id {root_id!r}")
    default = workspace_doc_language(codoc_dir)
    by_feature: dict[str, list] = {}
    for b in store.bindings.values():
        by_feature.setdefault(b.feature, []).append(b)
    out: list[dict] = []

    def walk(nodes, level, parent):
        for n in nodes:
            binds = by_feature.get(n.id, [])
            out.append({"id": n.id, "title": title(store, n.id, "rejected"), "blocks": description_blocks(store, n.id),
                        "parent_id": parent,
                        "phase": phase(store, n.id), "binding_count": len(binds),
                        "files": sorted({b.file for b in binds}), "written_by": written_by(store, n.id),
                        **({"bindings": sorted(b.symbol for b in binds)} if include_bindings else {}),
                        **_lang(description(store, n.id, "rejected") or title(store, n.id), default)})
            if depth <= 0 or level < depth:
                walk(n.children, level + 1, n.id)

    roots = fold_tree(heads)
    if root_id:
        roots = [_find(roots, root_id)]
    walk(roots, 1, None)
    # `open_plans`, not every open plan: one whose target block is gone cannot
    # be answered from any surface, so offering it asks for a verdict nobody
    # can give. It comes back when the block does.
    plans = [{"plan_id": p.id, "kind": p.kind, "target": p.target, "actor": p.actor, "note": p.note}
             for p in open_plans(store)]
    return {"ok": True, "features": out, "plans": plans, "doc_language": doc_language_block(codoc_dir)}


def _find(nodes, fid):
    for n in nodes:
        if n.id == fid:
            return n
        hit = _find(n.children, fid)
        if hit is not None:
            return hit
    return None


def read_status(codoc_dir: str) -> dict:
    ws, _ = _open(codoc_dir)
    store = ws.store
    heads = store.headings()
    phases = {h.id: phase(store, h.id) for h in heads}
    q = queued(store)
    state = published_status(codoc_dir).get("state", "synced")
    if state == "realizing" and not realizing_is_fresh(codoc_dir):
        # The lease lapsed, or it is the lease of the pass this agent belongs to:
        # either way no other pass is working the queue.
        state = "queued" if q else "synced"
    known = {(b.file, b.symbol) for b in store.bindings.values()}
    dead = [{"block": bid, "file": m.attrs.get("file"), "symbol": m.attrs.get("symbol")} for bid, m in dead_cites(store, known)]
    edited = [f for f, p in phases.items() if p == "edited"]
    return {"ok": True, "state": state, "features": len(heads), "queued": len(q),
            "queued_list": [{"feature_id": f, "title": title(store, f, "rejected")} for f in q[:20]],
            "edited": len(edited), "edited_list": [{"feature_id": f, "title": title(store, f)} for f in edited[:20]],
            "proposals": len(open_plans(store)),
            "dead_cites": len(dead), "dead_cite_list": dead[:20],
            "doc_language": doc_language_block(codoc_dir)}


def read_history(codoc_dir: str, feature_id: str, limit: int = 20) -> dict:
    ws, _ = _open(codoc_dir)
    store = ws.store
    if feature_id not in store.blocks:
        return _err(f"no feature {feature_id!r}")
    ops = feature_history(store, feature_id)[-limit:]
    entries = []
    for o in ops:
        e = {"op": o.id, "at": o.at, "actor": o.actor, "kind": o.kind, "batch": o.batch}
        if o.caused_by:
            e["caused_by"] = o.caused_by
        if o.plan:
            e["plan"] = o.plan
        if o.kind == K.TEXT_INSERT:
            e["text"] = o.payload.get("text")
        if o.kind == K.TEXT_DELETE:
            e["removed_segments"] = o.payload.get("segments")
        entries.append(e)
    blame = {(b.id if f == "text" else f"{b.id}.{f}"):
             [{"actor": r.actor, "start": r.start, "end": r.end} for r in attribution(b.text(f))]
             for b in store.feature_body(feature_id) for f in text_fields(b.kind)}
    return {"ok": True, "feature_id": feature_id, "title": title(store, feature_id), "ops": entries, "attribution": blame}


# ── writes ───────────────────────────────────────────────────────────────────

def _compile(store, ops: list[dict]) -> tuple[list[Change], str | None]:
    """Every edit compiled in order, or the first refusal as an error."""
    compiler = Compiler(store)
    changes = []
    for i, raw in enumerate(ops or []):
        change = compiler.compile(raw)
        if change.refused:
            return [], f"ops[{i}] ({change.kind}): {change.refused}"
        changes.append(change)
    return changes, None


def reflect(codoc_dir: str, *, ops: list[dict], rationale: str = "", caused_by: str | None = None,
            root_dir: str | None = None) -> dict:
    """What the agent did to the code, as tree edits (see the module table)."""
    ws, _ = _open(codoc_dir)
    changes, err = _compile(ws.store, ops)
    if err:
        return _err(err)
    minted = {c.target for c in changes if c.kind == "add_node"}
    trunk = [c for c in changes if c.kind in ("attach", "detach") and c.target not in minted]
    amends = [c for c in changes if c.kind == "amend" and c.target not in minted]
    planned = [c for c in changes if c not in trunk and c not in amends]
    out: dict = {"ok": True, "applied": [], "amended": [], "planned": None}
    bodies = [b for c in trunk for b in c.bodies]
    if bodies:
        out["applied"] = client.write(codoc_dir, client=AGENT, bodies=bodies, caused_by=caused_by, root_dir=root_dir)
    for c in amends:
        rewrites = [{"bid": r.bid, "path": r.path, "new_md": r.new_md,
                     "base_text": ws.store.blocks[r.bid].text(r.path).live_text()} for r in c.rewrites]
        got = client.amend(codoc_dir, client=AGENT, fid=c.target, rewrites=rewrites, bodies=c.bodies,
                           strikes=c.strikes, structural=c.structural, note=c.note or rationale,
                           caused_by=caused_by, root_dir=root_dir)
        out["amended"].append({"feature_id": c.target, **got})
    if planned:
        out["planned"] = _write_plans(codoc_dir, ws.store, planned, note=rationale, caused_by=caused_by,
                                      root_dir=root_dir)
    return out


def propose(codoc_dir: str, *, ops: list[dict], note: str = "", caused_by: str | None = None,
            root_dir: str | None = None) -> dict:
    """A plan of tree edits, drawn in place for the person to accept or reject."""
    ws, _ = _open(codoc_dir)
    changes, err = _compile(ws.store, ops)
    if err:
        return _err(err)
    if not changes:
        return _err("no ops")
    return _write_plans(codoc_dir, ws.store, changes, note=note, caused_by=caused_by, root_dir=root_dir)


def _write_plans(codoc_dir: str, store, changes: list[Change], *, note: str, caused_by: str | None,
                 root_dir: str | None) -> dict:
    """Each removal as its own delete plan (accepting one deletes that
    feature), everything else as one plan. Returns the first plan's id and
    every id."""
    removals = [c for c in changes if c.kind == "retire_node"]
    rest = [c for c in changes if c.kind != "retire_node"]
    results = [_write_plan(codoc_dir, store, [c], note=c.note or note, caused_by=caused_by, root_dir=root_dir)
               for c in removals]
    if rest:
        results.insert(0, _write_plan(codoc_dir, store, rest, note=note, caused_by=caused_by, root_dir=root_dir))
    failed = next((r for r in results if not r.get("ok")), None)
    if failed:
        return failed
    return {"ok": True, "plan_id": results[0]["plan_id"], "plan_ids": [r["plan_id"] for r in results],
            "ops": sum(r["ops"] for r in results), "note": results[0]["note"]}


_PLAN_KIND = {"add_node": "add", "move_node": "move", "retire_node": "delete"}


def _write_plan(codoc_dir: str, store, changes: list[Change], *, note: str, caused_by: str | None,
                root_dir: str | None) -> dict:
    """Open one plan and write every change under it: each change's bodies as
    one batch, then its rewrites as ingests, then its strikes."""
    kinds = {_PLAN_KIND.get(c.kind, "edit") for c in changes}
    kind = kinds.pop() if len(kinds) == 1 else "edit"      # a delete plan holds exactly one removal
    target = changes[0].target if len(changes) == 1 and kind != "add" else None
    pid = new_plan_id()
    opened = client.write(codoc_dir, client=AGENT, bodies=[
        {"kind": K.PLAN_OPEN, "target": {"pid": pid}, "payload": {"kind": kind, "target": target, "note": note}}],
        batch=pid, caused_by=caused_by, root_dir=root_dir)
    if opened and opened[0].get("conflict"):
        return _err(f"plan.open refused: {opened[0]['conflict']}")
    written = 0
    for c in changes:
        if c.bodies:
            written += len(client.write(codoc_dir, client=AGENT, bodies=c.bodies, plan=pid, batch=pid,
                                        caused_by=caused_by, root_dir=root_dir))
        for r in c.rewrites:
            base = store.blocks[r.bid].text(r.path).live_text()
            client.ingest(codoc_dir, client=AGENT, bid=r.bid, path=r.path, base_text=base, new_md=r.new_md,
                          plan=pid, caused_by=caused_by, root_dir=root_dir)
            written += 1
        strikes = strike_bodies(store, c.strikes, pid)
        if strikes:
            written += len(client.write(codoc_dir, client=AGENT, bodies=strikes, plan=pid, batch=pid,
                                        caused_by=caused_by, root_dir=root_dir))
    return {"ok": True, "plan_id": pid, "ops": written,
            "note": "The person accepts or rejects this plan in the editor. Accepting it queues the work for you "
                    "(except a removal, which the person hands off separately)."}


def write_block(codoc_dir: str, *, content: dict, bid: str | None = None, kind: str | None = None,
                feature_id: str | None = None, plan: str | None = None, caused_by: str | None = None,
                root_dir: str | None = None) -> dict:
    """An agent writes content to a kind block (or adds one at the end of a
    feature's body). Content in, ops out: non-Text fields become ``block.set``,
    Text fields go through ``ingest``, all as the agent, all under ``plan``
    when the agent is proposing rather than reflecting."""
    from codoc.kinds import get, kinds
    from codoc.kinds.write import plan_write
    ws, _ = _open(codoc_dir)
    store = ws.store
    if not isinstance(content, dict):
        return _err("content must be an object")
    if bid is None:
        # A new block and its text are one batch: nothing waits for the add
        # to be applied before the words are written.
        k = get(kind) if kind else None
        if k is None:
            return _err(f"unknown kind {kind!r}; known: {sorted(x.name for x in kinds())}")
        if feature_id not in store.blocks or not store.blocks[feature_id].live:
            return _err(f"unknown feature_id {feature_id!r}")
        if err := k.validate(content):
            return _err(err)
        bodies, (bid,) = block_bodies([BlockSpec(kind, content=content)], after=store.feature_body(feature_id)[-1].id)
        added = client.write(codoc_dir, client=AGENT, bodies=bodies, plan=plan, caused_by=caused_by, root_dir=root_dir)
        refused = [a["conflict"] for a in added if a.get("conflict")]
        if refused:
            return _err(f"block.add refused: {refused[0]}")
        return {"ok": True, "bid": bid, "applied": added, "ingested": []}
    else:
        current = store.blocks.get(bid)
        if current is None:
            return _err(f"unknown block {bid!r}")
        p = plan_write(current, content)
        if p.refused:
            return _err(p.refused)
    results: dict = {"ok": True, "bid": bid, "applied": [], "ingested": []}
    if p.sets:
        results["applied"] = client.write(codoc_dir, client=AGENT, bodies=p.sets, plan=plan, caused_by=caused_by, root_dir=root_dir)
    for f, base, new_md in p.ingests:
        results["ingested"].append({"field": f, **client.ingest(codoc_dir, client=AGENT, bid=bid, path=f, base_text=base,
                                                                 new_md=new_md, plan=plan, caused_by=caused_by, root_dir=root_dir)})
    return results


def realize_progress(codoc_dir: str, *, done: int, total: int, current: str = "") -> dict:
    """Renew the realizing lease so the editor shows 'implementing n of m'."""
    shown = min(done + 1, total)
    write_realizing(codoc_dir, pending=max(0, total - done),
                    detail=f"implementing {shown} of {total}" + (f" — {current}" if current else ""))
    return {"ok": True}


# ── walkthrough ──────────────────────────────────────────────────────────────

def walkthrough(codoc_dir: str, *, question: str, answer: str = "", steps: list[dict] | None = None) -> dict:
    rows = list(steps or [])
    if not rows:
        return _err("pass steps=[{feature_id, note, quote?, group?, file?, symbol?, line?}, ...]")
    ws, _ = _open(codoc_dir)
    store = ws.store
    heads = {h.id: h for h in store.headings()}
    by_title = {title(store, h).strip().lower(): h for h in heads}
    built, dropped, unresolved, seen = [], [], [], set()
    for row in rows:
        if not isinstance(row, dict):
            dropped.append({"step": str(row), "why": "not an object"})
            continue
        fid = str(row.get("feature_id") or "").strip()
        h = heads.get(fid) or by_title.get(fid.lower())
        if h is None:
            dropped.append({"feature_id": fid, "why": "no such live feature"})
            continue
        if h.id in seen:
            dropped.append({"feature_id": h.id, "why": "already a stop on this path"})
            continue
        seen.add(h.id)
        quote = str(row.get("quote") or "").strip()
        if quote:
            prose = " ".join((title(store, h.id) + " " + description(store, h.id)).split())
            if " ".join(quote.split()) not in prose:
                unresolved.append({"feature_id": h.id, "quote": quote})
                quote = ""
        line = row.get("line")
        built.append(AskStep(feature_id=h.id, note=str(row.get("note") or ""), quote=quote, group=str(row.get("group") or ""),
                             file=str(row.get("file") or ""), symbol=str(row.get("symbol") or ""),
                             line=int(line) if isinstance(line, (int, float)) else None))
    if not built:
        return _err("no step named a live feature — read the tree first and pass real ids")
    walk = build_walkthrough(question, answer, built)
    write_walkthrough(codoc_dir, walk)
    out: dict = {"ok": True, "id": walk.id, "steps": len(walk.steps), "labels": [s.label for s in walk.steps]}
    if dropped:
        out["dropped"] = dropped
    if unresolved:
        out["unresolved_quotes"] = unresolved
    return out


def clear_walkthrough_tool(codoc_dir: str) -> dict:
    return {"ok": True, "cleared": clear_walkthrough(codoc_dir)}


def read_walkthrough_tool(codoc_dir: str) -> dict:
    return {"ok": True, "walkthrough": read_walkthrough(codoc_dir)}
