"""The agent's read and write surface (v2), used by the MCP server.

Reads replay the logs into a read-only workspace and derive from it. Writes
go through ``substrate.client`` (the daemon's inbox when a daemon owns the
checkout, the applier otherwise) and are stamped ``actor=agent:<id>`` by the
receiving side, never by the agent.

Two writes:

- ``reflect``: what the agent just did to the code, as bindings (trunk) and
  block-keyed prose repairs (through ``ingest`` under the machine gate). Safe
  ops land; anything that would rewrite a person's words becomes a plan.
- ``propose``: a plan. New nodes, amendments and moves the person will accept
  or reject in the editor; accepting a plan hands the feature off, so a plan is
  by definition a request to build.
"""
from __future__ import annotations

import json
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.derive import (
    attribution, dead_cites, description, history as feature_history, markdown, phase, title,
)
from codoc.calculus.doc import HEADING, PARAGRAPH, fold_tree, text_fields
from codoc.calculus.ids import START, new_block_id, new_plan_id
from codoc.calculus.text import SUGGEST_DEL
from codoc.doclang import detect_prose_language, workspace_doc_language
from codoc.graph.graph import Graph
from codoc.loop.ask import AskStep, build_walkthrough, clear_walkthrough, read_walkthrough, write_walkthrough
from codoc.loops.context import select_context, written_by
from codoc.loops.realize import queued
from codoc.substrate import client
from codoc.substrate.daemon import GRAPH_CACHE
from codoc.substrate.publish import STATUS_FILE, atomic_write

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


def _outline(all_titles: list[dict]) -> str:
    by_parent: dict[str | None, list[dict]] = {}
    for t in all_titles:
        by_parent.setdefault(t.get("parent_id"), []).append(t)
    lines: list[str] = []

    def emit(parent, depth):
        for t in by_parent.get(parent, []):
            mark = " (planned)" if t.get("planned") else ""
            lines.append("  " * depth + f"- [{t['id']}] {t['title']}{mark}")
            emit(t["id"], depth + 1)

    emit(None, 0)
    return "\n".join(lines)


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
    return {"ok": True, "subtree": subtree, "titles_outline": _outline(all_titles), "graph": ctx,
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
            blocks = [{"id": b.id, "text": markdown(b.text(), "rejected", store)}
                      for b in store.feature_body(n.id)[1:] if b.kind == PARAGRAPH]
            out.append({"id": n.id, "title": title(store, n.id, "rejected"), "blocks": blocks, "parent_id": parent,
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
    plans = [{"plan_id": p.id, "kind": p.kind, "target": p.target, "actor": p.actor, "note": p.note}
             for p in store.plans.values() if p.status == "open"]
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
    try:
        st = json.loads((Path(codoc_dir) / STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        st = {}
    q = queued(store)
    known = {(b.file, b.symbol) for b in store.bindings.values()}
    dead = [{"block": bid, "file": m.attrs.get("file"), "symbol": m.attrs.get("symbol")} for bid, m in dead_cites(store, known)]
    edited = [f for f, p in phases.items() if p == "edited"]
    return {"ok": True, "state": st.get("state", "synced"), "features": len(heads), "queued": len(q),
            "queued_list": [{"feature_id": f, "title": title(store, f, "rejected")} for f in q[:20]],
            "edited": len(edited), "edited_list": [{"feature_id": f, "title": title(store, f)} for f in edited[:20]],
            "proposals": sum(1 for p in store.plans.values() if p.status == "open"),
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

def _bind_body(kind: str, fid: str, bind: str) -> dict | None:
    file, _, sym = str(bind).partition("::")
    if not sym:
        return None
    return {"kind": kind, "target": {"fid": fid}, "payload": {"file": file, "symbol": f"{file}::{sym}"}}


def reflect(codoc_dir: str, *, ops: list[dict], rationale: str = "", caused_by: str | None = None,
            root_dir: str | None = None) -> dict:
    """What the agent did, as bindings and block-keyed prose repairs. Bindings
    land on the trunk. Prose enters through the daemon's ``ingest`` under the
    machine gate: a small repair lands, a rewrite or a repair on a feature with
    pending intent becomes a plan."""
    ws, _ = _open(codoc_dir)
    store = ws.store
    bodies: list[dict] = []
    ingests: list[dict] = []
    plans: list[dict] = []
    kinds = {"attach": K.BIND_ATTACH, "detach": K.BIND_DETACH, "refresh": K.BIND_REFRESH}
    for raw in ops or []:
        kind = raw.get("kind")
        fid = raw.get("feature_id")
        if kind in kinds:
            if fid not in store.blocks or store.blocks[fid].kind != HEADING or not store.blocks[fid].live:
                return _err(f"unknown feature_id {fid!r}")
            for b in raw.get("binds", []):
                body = _bind_body(kinds[kind], fid, b)
                if body is None:
                    return _err(f"bad bind {b!r}: use 'file.py::symbol'")
                bodies.append(body)
        elif kind == "amend":
            if fid not in store.blocks:
                return _err(f"unknown feature_id {fid!r}")
            if raw.get("title"):
                ingests.append({"bid": fid, "path": "text", "new_md": raw["title"]})
            for entry in raw.get("blocks", []):
                if entry.get("id") in store.blocks:
                    ingests.append({"bid": entry["id"], "path": "text", "new_md": entry.get("text", "")})
                elif entry.get("text"):
                    plans.append({"kind": "add_paragraph", "feature_id": fid, "text": entry["text"]})
        elif kind in ("add_node", "move_node", "retire_node"):
            plans.append(raw)
        else:
            return _err(f"unknown op kind {kind!r}")
    results: dict = {"ok": True, "applied": [], "ingested": [], "planned": None}
    if bodies:
        results["applied"] = client.write(codoc_dir, client=AGENT, bodies=bodies, caused_by=caused_by, root_dir=root_dir)
    for ing in ingests:
        t = store.blocks[ing["bid"]].text().live_text()
        results["ingested"].append({**ing, **client.ingest(codoc_dir, client=AGENT, bid=ing["bid"], path=ing["path"],
                                                           base_text=t, new_md=ing["new_md"], caused_by=caused_by,
                                                           root_dir=root_dir)})
    if plans:
        results["planned"] = propose(codoc_dir, ops=plans, note=rationale, caused_by=caused_by, root_dir=root_dir)
    return results


def propose(codoc_dir: str, *, ops: list[dict], note: str = "", caused_by: str | None = None,
            root_dir: str | None = None) -> dict:
    """A plan: new nodes, amendments (block-keyed), moves, deletions. Applied as
    gray, tagged text the person accepts or rejects in the editor."""
    ws, _ = _open(codoc_dir)
    store = ws.store
    pid = new_plan_id()
    kinds = {o.get("kind") for o in ops}
    pkind = ("delete" if kinds == {"retire_node"} else "add" if kinds <= {"add_node"}
             else "move" if kinds == {"move_node"} else "edit")
    target = ops[0].get("feature_id") if len(ops) == 1 and pkind != "add" else None
    bodies: list[dict] = [{"kind": K.PLAN_OPEN, "target": {"pid": pid}, "payload": {"kind": pkind, "target": target, "note": note}}]
    ingests: list[dict] = []
    local: dict[str, str] = {}
    live = store.ordered()
    last = live[-1].id if live else START

    def place(parent_ref):
        parent = local.get(parent_ref or "") or (parent_ref if parent_ref in store.blocks else None)
        if parent:
            rng = store.feature_range(parent)
            return rng[-1].id, store.blocks[parent].level + 1
        return last, 1

    def strike(b) -> None:
        for f in text_fields(b.kind):
            t = b.text(f)
            if t.live_len():
                bodies.append({"kind": K.TEXT_MARK, "target": {"bid": b.id, "path": f},
                               "payload": {"kind": SUGGEST_DEL, "attrs": {}, "start": list(t.ref_at(0)),
                                           "end": list(t.last_live_ref()), "on": True}, "plan": pid})

    for raw in ops:
        kind = raw.get("kind")
        fid = raw.get("feature_id")
        if kind == "add_node":
            after, level = place(raw.get("parent_id"))
            nid = new_block_id()
            local[str(raw.get("id") or nid)] = nid
            bodies.append({"kind": K.BLOCK_ADD, "target": {"bid": nid}, "payload": {"kind": HEADING, "level": level, "after": after}, "plan": pid})
            ingests.append({"bid": nid, "new_md": (raw.get("title") or "Untitled").strip(), "base": ""})
            prev = nid
            for para in [p.strip() for p in (raw.get("description") or "").split("\n\n") if p.strip()]:
                bid = new_block_id()
                bodies.append({"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": PARAGRAPH, "after": prev}, "plan": pid})
                ingests.append({"bid": bid, "new_md": para, "base": ""})
                prev = bid
            for b in raw.get("binds", []):
                body = _bind_body(K.BIND_ATTACH, nid, b)
                if body:
                    bodies.append(body)
            last = prev
        elif kind == "amend":
            if fid not in store.blocks:
                return _err(f"unknown feature_id {fid!r}")
            if raw.get("title"):
                ingests.append({"bid": fid, "new_md": raw["title"], "base": None})
            # The amend contract (prompts/tree_update.txt): a listed id is
            # repaired, `delete: true` strikes it, no id adds a paragraph after
            # the previous entry, and a paragraph not mentioned is unchanged.
            prev = fid
            for entry in raw.get("blocks", []):
                bid = entry.get("id")
                if bid in store.blocks:
                    if entry.get("delete"):
                        strike(store.blocks[bid])
                    else:
                        ingests.append({"bid": bid, "new_md": entry.get("text", ""), "base": None})
                    prev = bid
                elif entry.get("text"):
                    nb = new_block_id()
                    bodies.append({"kind": K.BLOCK_ADD, "target": {"bid": nb}, "payload": {"kind": PARAGRAPH, "after": prev}, "plan": pid})
                    ingests.append({"bid": nb, "new_md": entry["text"], "base": ""})
                    prev = nb
        elif kind == "move_node":
            if fid not in store.blocks:
                return _err(f"unknown feature_id {fid!r}")
            after, level = place(raw.get("parent_id"))
            bodies.append({"kind": K.BLOCK_MOVE, "target": {"bid": fid},
                           "payload": {"after": after, "level_delta": level - store.blocks[fid].level}, "plan": pid})
        elif kind == "retire_node":
            if fid not in store.blocks:
                return _err(f"unknown feature_id {fid!r}")
            for b in store.feature_body(fid):
                strike(b)
        elif kind == "add_paragraph":
            nb = new_block_id()
            bodies.append({"kind": K.BLOCK_ADD, "target": {"bid": nb},
                           "payload": {"kind": PARAGRAPH, "after": store.feature_body(fid)[-1].id}, "plan": pid})
            ingests.append({"bid": nb, "new_md": raw.get("text", ""), "base": ""})
        else:
            return _err(f"unknown op kind {kind!r}")
    written = client.write(codoc_dir, client=AGENT, bodies=bodies, batch=pid, caused_by=caused_by, root_dir=root_dir)
    for ing in ingests:
        base = ing["base"] if ing["base"] is not None else store.blocks[ing["bid"]].text().live_text()
        client.ingest(codoc_dir, client=AGENT, bid=ing["bid"], path="text", base_text=base, new_md=ing["new_md"],
                      plan=pid, caused_by=caused_by, root_dir=root_dir)
    return {"ok": True, "plan_id": pid, "ops": len(written),
            "note": "The person accepts or rejects this plan in the editor; accepting hands the feature off to you."}


def write_block(codoc_dir: str, *, content: dict, bid: str | None = None, kind: str | None = None,
                feature_id: str | None = None, plan: str | None = None, caused_by: str | None = None,
                root_dir: str | None = None) -> dict:
    """An agent writes content to a kind block (or adds one at the end of a
    feature's body). Content in, ops out: non-Text fields become ``block.set``,
    Text fields go through ``ingest``, all as the agent, all under ``plan``
    when the agent is proposing rather than reflecting."""
    from codoc.kinds import get, kinds
    from codoc.kinds.write import add_body, plan_write
    ws, _ = _open(codoc_dir)
    store = ws.store
    if not isinstance(content, dict):
        return _err("content must be an object")
    if bid is None:
        if not kind or get(kind) is None:
            return _err(f"unknown kind {kind!r}; known: {sorted(k.name for k in kinds())}")
        if feature_id not in store.blocks or not store.blocks[feature_id].live:
            return _err(f"unknown feature_id {feature_id!r}")
        after = store.feature_body(feature_id)[-1].id
        bid, body, err = add_body(kind, content, after=after)
        if err:
            return _err(err)
        added = client.write(codoc_dir, client=AGENT, bodies=[body], plan=plan, caused_by=caused_by, root_dir=root_dir)
        if added[0].get("conflict"):
            return _err(f"block.add refused: {added[0]['conflict']}")
        texts = {k: v for k, v in content.items() if k in get(kind).text_fields and v}
        p = plan_write(_fresh_block(codoc_dir, bid), texts) if texts else None
    else:
        current = store.blocks.get(bid)
        if current is None:
            return _err(f"unknown block {bid!r}")
        p = plan_write(current, content)
        if p.refused:
            return _err(p.refused)
    results: dict = {"ok": True, "bid": bid, "applied": [], "ingested": []}
    if p is None:
        return results
    if p.sets:
        results["applied"] = client.write(codoc_dir, client=AGENT, bodies=p.sets, plan=plan, caused_by=caused_by, root_dir=root_dir)
    for f, base, new_md in p.ingests:
        results["ingested"].append({"field": f, **client.ingest(codoc_dir, client=AGENT, bid=bid, path=f, base_text=base,
                                                                 new_md=new_md, plan=plan, caused_by=caused_by, root_dir=root_dir)})
    return results


def _fresh_block(codoc_dir: str, bid: str):
    """The block as the applier now has it (after a just-acked add)."""
    return client.read(codoc_dir).store.blocks.get(bid)


def realize_progress(codoc_dir: str, *, done: int, total: int, current: str = "") -> dict:
    """Renew the realizing lease so the editor shows 'implementing n of m'."""
    path = Path(codoc_dir) / STATUS_FILE
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        d = {}
    shown = min(done + 1, total)
    d.update({"state": "realizing", "pending": max(0, total - done),
              "detail": f"implementing {shown} of {total}" + (f" — {current}" if current else "")})
    atomic_write(path, json.dumps(d))
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
