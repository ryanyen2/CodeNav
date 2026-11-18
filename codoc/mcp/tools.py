"""The agent's read and write surface, used by the MCP server.

Reads replay the logs into a read-only workspace and derive from it. Writes
go through ``daemon.client`` (the daemon's inbox when a daemon owns the
checkout, the applier otherwise) as client ``agent-<CODOC_AGENT>``, and the
receiving side stamps ``actor=agent:<CODOC_AGENT>``, never the agent. A
binding op that cites a hand-off in ``caused_by`` settles it when it lands.

Two writes take tree edits (``loops.edits``). Every edit is compiled against
the current tree before anything is written; if one is refused, nothing is
written and the error names it.

| tool | edit | lands |
|---|---|---|
| ``reflect`` | ``attach``, ``detach`` on an existing feature | on the trunk |
| ``reflect`` | ``amend`` | ``Workspace.amend``: all of it on the trunk when it only rewords and passes the machine gate, else one plan |
| ``reflect`` | ``add_node``, ``move_node``, ``retire_node`` | as ``propose`` writes them, but accepting them builds nothing |
| ``propose`` | all | one plan for everything but removals; each ``retire_node`` is its own delete plan |

A ``propose`` plan is opened with ``build: true``: it comes before the code,
so accepting it (in the editor or with ``codoc accept``) also hands off the
features it touches (``derive.accept_handoffs``). A ``reflect`` plan describes
code already written, and accepting it takes the words only.
"""
from __future__ import annotations

import os
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.derive import (
    attribution, dead_cites, description, history as feature_history, open_plans, phase, title,
)
from codoc.calculus.doc import HEADING, fold_tree
from codoc.calculus.ids import new_plan_id
from codoc.daemon import client
from codoc.daemon.epoch import note_cited, read_activity
from codoc.daemon.publish import read_status as published_status
from codoc.daemon.publish import REALIZE_OWNER_ENV, realizing_is_fresh, write_realizing
from codoc.doclang import detect_prose_language, workspace_doc_language
from codoc.index.graph import Graph
from codoc.llm.prompt import titles_outline
from codoc.loops.context import description_blocks, feature_context, select_context, written_by
from codoc.calculus.block_md import BlockSpec
from codoc.calculus.ingest import block_bodies, concat_bodies
from codoc.loops.edits import PLAN_KIND, Change, Compiler, delete_open
from codoc.loops.realize import queued
from codoc.mcp.walkthrough import AskStep, build_walkthrough, clear_walkthrough, read_walkthrough, write_walkthrough
from codoc.workspace import log as L
from codoc.workspace.layout import GRAPH_CACHE


def agent_client() -> str:
    """The inbox this server writes to, ``agent-<CODOC_AGENT>``, from which the
    applier stamps ``agent:<CODOC_AGENT>``. The one place the agent's name is
    read: set CODOC_AGENT in the MCP server's environment (cursor, codex, ...)."""
    name = f"agent-{os.environ.get('CODOC_AGENT') or 'claude-code'}"
    if not L.AGENT_CLIENT.fullmatch(name):
        raise ValueError(f"CODOC_AGENT must be lowercase letters, digits, '-' or '_': {name[6:]!r}")
    return name


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
    """``files``: the features bound in them and their neighbours
    (``select_context``). ``feature_id``: that feature, the features under it
    and the features owning what its code calls (``feature_context``), then
    any the files add."""
    ws, graph = _open(codoc_dir)
    store = ws.store
    if not files and not feature_id:
        return _err("pass files=[...] and/or feature_id")
    if feature_id:
        b = store.blocks.get(feature_id)
        if b is None or not b.live or b.kind != HEADING:
            return _err(f"unknown feature_id {feature_id!r}")
        subtree, all_titles, ctx = feature_context(store, graph, feature_id)
        if files:
            have = {e["id"] for e in subtree}
            subtree += [e for e in select_context(store, graph, set(files))[0] if e["id"] not in have]
    else:
        subtree, all_titles, ctx = select_context(store, graph, set(files))
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
    """``queued_list`` is what is left to build; ``reported_list`` is what this
    turn's calls cited (``epoch.note_cited``), which settles when the turn
    ends. State ``reported`` says every queued item is reported."""
    ws, _ = _open(codoc_dir)
    store = ws.store
    heads = store.headings()
    phases = {h.id: phase(store, h.id) for h in heads}
    cited = set(read_activity(codoc_dir).get("cited") or [])
    items: dict[bool, list] = {False: [], True: []}
    for f in queued(store):
        did = (store.watermarks(f).handoffs or [""])[-1]
        items[did in cited].append({"feature_id": f, "title": title(store, f, "rejected"), "directive": f"d:{did}"})
    todo, reported = items[False], items[True]
    state = published_status(codoc_dir).get("state", "synced")
    if state == "realizing" and not realizing_is_fresh(codoc_dir):
        # The lease lapsed, or it is the lease of the pass this agent belongs to:
        # either way no other pass is working the queue.
        state = "queued" if todo or reported else "synced"
    if state == "queued" and not todo:
        state = "reported"
    dead = [{"block": bid, "symbol": m.attrs.get("symbol")} for bid, m in dead_cites(store)]
    edited = [f for f, p in phases.items() if p == "edited"]
    return {"ok": True, "state": state, "features": len(heads), "queued": len(todo), "queued_list": todo[:20],
            "reported": len(reported), "reported_list": reported[:20],
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
             for b in store.feature_body(feature_id) for f in b.layout.text}
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


def _handoff(caused_by: str | None) -> str | None:
    """The hand-off op id a call cites. The queue shows a hand-off as
    ``⟨d:<op id>⟩`` and tells the agent to pass that ``d:`` id; ops cite the op
    id itself."""
    return caused_by.removeprefix("d:") if caused_by else caused_by


def _cite(codoc_dir: str, did: str | None, results: list[dict]) -> None:
    """Record on the open epoch, where the Stop hook reads what this turn
    reported, that the call cited ``did``, once an op of it landed, applied
    or planned: ``results`` are the call's acks and plan results."""
    if did and any(r.get("ops") and not r.get("conflict") for r in results):
        try:
            note_cited(codoc_dir, did)
        except OSError:          # a lock timeout: at worst the Stop hook asks once more
            pass


def reflect(codoc_dir: str, *, ops: list[dict], rationale: str = "", caused_by: str | None = None,
            root_dir: str | None = None) -> dict:
    """What the agent did to the code, as tree edits (see the module table)."""
    caused_by = _handoff(caused_by)
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
        out["applied"] = client.write(codoc_dir, client=agent_client(), bodies=bodies, caused_by=caused_by, root_dir=root_dir)
    for c in amends:
        rewrites = [{"bid": r.bid, "path": r.path, "new_md": r.new_md,
                     "base_text": ws.store.blocks[r.bid].text(r.path).live_text()} for r in c.rewrites]
        got = client.amend(codoc_dir, client=agent_client(), fid=c.target, rewrites=rewrites, bodies=c.bodies,
                           strikes=c.strikes, structural=c.structural, note=c.note or rationale,
                           caused_by=caused_by, root_dir=root_dir)
        out["amended"].append({"feature_id": c.target, **got})
    if planned:
        out["planned"] = _write_plans(codoc_dir, ws.store, planned, note=rationale, caused_by=caused_by,
                                      root_dir=root_dir, build=False)
    _cite(codoc_dir, caused_by, [*out["applied"], *out["amended"], out["planned"] or {}])
    return out


def propose(codoc_dir: str, *, ops: list[dict], note: str = "", caused_by: str | None = None,
            root_dir: str | None = None) -> dict:
    """A plan of tree edits, drawn in place for the person to accept or reject."""
    caused_by = _handoff(caused_by)
    ws, _ = _open(codoc_dir)
    changes, err = _compile(ws.store, ops)
    if err:
        return _err(err)
    if not changes:
        return _err("no ops")
    out = _write_plans(codoc_dir, ws.store, changes, note=note, caused_by=caused_by, root_dir=root_dir, build=True)
    _cite(codoc_dir, caused_by, [out])
    return out


def _write_plans(codoc_dir: str, store, changes: list[Change], *, note: str, caused_by: str | None,
                 root_dir: str | None, build: bool) -> dict:
    """Each removal as its own delete plan (accepting one deletes that
    feature), everything else as one plan. A feature already proposed for
    deletion, by an open plan or earlier in this call, is not proposed again.
    ``build`` says accepting the plan asks for the code: true for a proposal
    made before the code, false for a reflection of code already written.
    Returns every plan written, and why each part that was not written was
    refused; an error only when no plan was written."""
    removals: dict[str, Change] = {}
    for c in changes:
        if c.kind == "retire_node" and c.target not in removals and not delete_open(store, c.target):
            removals[c.target] = c
    rest = [c for c in changes if c.kind != "retire_node"]
    groups = ([(rest, note, build)] if rest else []) + [([c], c.note or note, False) for c in removals.values()]
    results = [_write_plan(codoc_dir, store, group, note=n, caused_by=caused_by, root_dir=root_dir, build=b)
               for group, n, b in groups]
    written = [r for r in results if r.get("ok")]
    if not written:
        return results[0] if results else _err("every feature named is already proposed for removal")
    out = {"ok": True, "plan_id": written[0]["plan_id"], "plan_ids": [r["plan_id"] for r in written],
           "ops": sum(r["ops"] for r in written), "note": written[0]["note"]}
    refused = [x for r in written for x in r.get("refused", [])] + [r["error"] for r in results if not r.get("ok")]
    if refused:
        out["refused"] = refused
    return out


def _write_plan(codoc_dir: str, store, changes: list[Change], *, note: str, caused_by: str | None,
                root_dir: str | None, build: bool) -> dict:
    """One plan holding every change, written as one ``propose`` submission:
    the applier opens it, writes the bodies, rewrites and strikes under it, and
    takes it back when nothing landed (``Workspace.propose``)."""
    kinds = {PLAN_KIND.get(c.kind, "edit") for c in changes}
    kind = kinds.pop() if len(kinds) == 1 else "edit"      # a delete plan holds exactly one removal
    target = changes[0].target if len(changes) == 1 and kind != "add" else None
    pid, bodies = new_plan_id(), []
    for c in changes:
        bodies = concat_bodies(bodies, c.bodies)
    got = client.propose(codoc_dir, client=agent_client(), pid=pid, kind=kind, target=target, note=note, build=build, bodies=bodies,
                         rewrites=[{"bid": r.bid, "path": r.path, "new_md": r.new_md,
                                    "base_text": store.blocks[r.bid].text(r.path).live_text()}
                                   for c in changes for r in c.rewrites],
                         strikes=[bid for c in changes for bid in c.strikes], caused_by=caused_by, root_dir=root_dir)
    if got["status"] == "unchanged":
        return _err("nothing was written: " + ("; ".join(got["refused"]) or "no block would change"))
    out = {"ok": True, "plan_id": pid, "ops": len(got["ops"]),
           "note": "The person accepts or rejects this plan in the editor. "
                   + ("Accepting it deletes the feature; removing its code is a separate hand-off."
                      if kind == "delete" else
                      "Accepting it queues the work for you." if build else
                      "Accepting it takes the words only; nothing is queued.")}
    if got["status"] is None:
        out["note"] += " The daemon has not answered yet; read the tree to see the plan."
    if got["refused"]:
        out["refused"] = got["refused"]
    return out


def write_block(codoc_dir: str, *, content: dict, bid: str | None = None, kind: str | None = None,
                feature_id: str | None = None, plan: str | None = None, caused_by: str | None = None,
                root_dir: str | None = None) -> dict:
    """An agent writes content to a kind block (or adds one at the end of a
    feature's body). Content in, ops out: non-Text fields become ``block.set``,
    Text fields go through ``ingest``, all as the agent, all under ``plan``
    when the agent is proposing rather than reflecting."""
    from codoc.kinds import get, kinds
    from codoc.kinds.write import plan_write
    caused_by = _handoff(caused_by)
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
        added = client.write(codoc_dir, client=agent_client(), bodies=bodies, plan=plan, caused_by=caused_by, root_dir=root_dir)
        refused = [a["conflict"] for a in added if a.get("conflict")]
        if refused:
            return _err(f"block.add refused: {refused[0]}")
        _cite(codoc_dir, caused_by, added)
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
        results["applied"] = client.write(codoc_dir, client=agent_client(), bodies=p.sets, plan=plan, caused_by=caused_by, root_dir=root_dir)
    for f, base, new_md in p.ingests:
        results["ingested"].append({"field": f, **client.ingest(codoc_dir, client=agent_client(), bid=bid, path=f, base_text=base,
                                                                 new_md=new_md, plan=plan, caused_by=caused_by, root_dir=root_dir)})
    _cite(codoc_dir, caused_by, [*results["applied"], *results["ingested"]])
    return results


def realize_progress(codoc_dir: str, *, done: int, total: int, current: str = "") -> dict:
    """Renew the realizing lease so the editor shows 'implementing n of m'.
    The lease is this server's own (the realize pass's that started the agent,
    else this process's), so this agent's status does not read it as another
    pass. Returns the items this turn has reported."""
    shown = min(done + 1, total)
    write_realizing(codoc_dir, pending=max(0, total - done), owner=os.environ.setdefault(REALIZE_OWNER_ENV, f"mcp-{os.getpid()}"),
                    detail=f"implementing {shown} of {total}" + (f": {current}" if current else ""))
    return {"ok": True, "reported": [f"d:{d}" for d in read_activity(codoc_dir).get("cited") or []]}


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
        return _err("no step named a live feature. Read the tree first and pass real ids")
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
