"""The agent's read and write surface, used by the MCP server.

Reads replay the logs into a read-only workspace and derive from it. Writes
go through ``daemon.client`` (the daemon's inbox when a daemon owns the
checkout, the applier otherwise) as client ``agent-<CODOC_AGENT>``, and the
receiving side stamps ``actor=agent:<CODOC_AGENT>``, never the agent. A
binding op that cites a hand-off in ``caused_by`` settles it when it lands.

Two writes take tree edits (``loops.edits``). Every edit is compiled against
the current tree and the code on disk before anything is written; if one is
refused, nothing is written and the error names it. A symbol an ``attach`` or
``add_node`` binds must be a chunk the files hold now, hashed as the index
hashes it (``index.app.code_on_disk``), so code written during the turn binds
before any pass has indexed it. An ``attach`` of a symbol the feature already
owns writes a ``bind.refresh`` when it cites a hand-off whose build changed
that code, and nothing otherwise: a re-attach of unchanged code would report
work that did not happen (docs/concurrency.md, Hand-off and realization).

| tool | edit | lands |
|---|---|---|
| ``reflect`` | ``attach``, ``detach`` on an existing feature | on the trunk |
| ``reflect`` | ``amend`` | ``Workspace.amend``: all of it on the trunk when it only rewords and passes the machine gate, else one plan |
| ``reflect`` | ``add_node``, ``move_node``, ``delete_node`` | as ``propose`` writes them, but accepting them builds nothing |
| ``propose`` | all | one plan for everything but deletions; each ``delete_node`` is its own delete plan |

A ``propose`` plan is opened with ``build: true``: it comes before the code,
so accepting it (in the editor or with ``codoc accept``) also hands off the
features it touches (``derive.accept_handoffs``). A ``reflect`` plan describes
code already written, and accepting it takes the words only.
"""
from __future__ import annotations

import os
from pathlib import Path

from codoc.calculus.derive import (
    description, offered_plans, phase, title,
)
from codoc.calculus.doc import HEADING, fold_tree
from codoc.calculus.ids import new_plan_id
from codoc.daemon import client
from codoc.daemon.reads import doc_language_block
from codoc.workspace.epoch import note_cited, read_activity
from codoc.daemon.publish import REALIZE_OWNER_ENV, write_realizing
from codoc.doclang import detect_prose_language, workspace_doc_language
from codoc.index.graph import Graph
from codoc.llm.prompt import titles_outline
from codoc.loops.context import description_blocks, feature_context, select_context, written_by
from codoc.calculus.block_md import BlockSpec
from codoc.calculus.ingest import block_bodies, concat_bodies
from codoc.loops.edits import PLAN_KIND, Change, Compiler, delete_open, symbol
from codoc.workspace.inbox import AGENT_CLIENT
from codoc.workspace.layout import GRAPH_CACHE


def agent_client() -> str:
    """The inbox this server writes to, ``agent-<CODOC_AGENT>``, from which the
    applier stamps ``agent:<CODOC_AGENT>``. The one place the agent's name is
    read: set CODOC_AGENT in the MCP server's environment (cursor, codex, ...)."""
    name = f"agent-{os.environ.get('CODOC_AGENT') or 'claude-code'}"
    if not AGENT_CLIENT.fullmatch(name):
        raise ValueError(f"CODOC_AGENT must be lowercase letters, digits, '-' or '_': {name[6:]!r}")
    return name


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _open(codoc_dir: str):
    ws = client.read(codoc_dir)
    return ws, Graph.load(Path(codoc_dir) / GRAPH_CACHE)


def _lang(text: str, default) -> dict:
    got = detect_prose_language(text, default)
    return {"lang": got.code} if got.code != default.code else {}


# ── reads ────────────────────────────────────────────────────────────────────

def read_context(codoc_dir: str, *, files: list[str] | None = None, feature_id: str | None = None,
                 include_bindings: bool = True) -> dict:
    """``files``: the features bound in them and their neighbours
    (``select_context``). ``feature_id``: that feature, the features under it
    and the features one call away from its code (``feature_context``), then
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
    # `offered_plans`, not every open plan: one with nothing left on the page
    # (its target and its words gone) cannot be answered from any surface, so
    # offering it asks for a verdict nobody can give. It comes back with them.
    plans = [{"plan_id": p.id, "kind": p.kind, "target": p.target, "actor": p.actor, "note": p.note}
             for p in offered_plans(store)]
    return {"ok": True, "features": out, "plans": plans, "doc_language": doc_language_block(codoc_dir)}


def _find(nodes, fid):
    for n in nodes:
        if n.id == fid:
            return n
        hit = _find(n.children, fid)
        if hit is not None:
            return hit
    return None


# ── writes ───────────────────────────────────────────────────────────────────

def _compile(codoc_dir: str, root_dir: str | None, store, ops: list[dict],
             cites: str | None) -> tuple[list[Change], str | None]:
    """Every edit compiled in order, or the first refusal as an error (see the
    module docstring for what a binding may name). ``cites`` is the hand-off
    the edits report as built.

    A binding's file is first put the way the index names it
    (``index.app.canonical_file``: repo-relative POSIX, ``./``, ``..`` and an
    absolute path folded lexically, a linked file under its link's name), so
    the compiler sees only keys a reflect pass would produce, and a file
    outside the repository is refused."""
    from codoc.index.app import canonical_file, code_on_disk
    root = root_dir or Path(codoc_dir).parent
    ops = [dict(raw) if isinstance(raw, dict) else raw for raw in ops or []]
    binds: dict[int, list[str]] = {}
    for i, raw in enumerate(ops):
        if not isinstance(raw, dict) or not isinstance(raw.get("bindings"), list):
            continue
        named = []
        for b in raw["bindings"]:
            if (sym := symbol(b)) is None:
                continue                # not a binding: dropped, as the compiler drops it
            file, _, name = sym.partition("::")
            if (rel := canonical_file(root, file)) is None:
                return [], f"ops[{i}] ({raw.get('kind')}): {sym} names a file outside the repository"
            named.append(f"{rel}::{name}")
        raw["bindings"] = named
        if raw.get("kind") in ("attach", "add_node"):
            binds[i] = named
    hashes = code_on_disk(root, {s.split("::")[0] for named in binds.values() for s in named})
    compiler = Compiler(store, hashes=hashes, bindable=set(hashes), cites=cites)
    changes = []
    for i, raw in enumerate(ops):
        missing = [s for s in binds.get(i, []) if s not in hashes]
        if missing:
            return [], f"ops[{i}] ({raw['kind']}): " + "; ".join(_unbindable(root, s) for s in missing)
        change = compiler.compile(raw)
        if change.refused:
            return [], f"ops[{i}] ({change.kind}): {change.refused}"
        changes.append(change)
    return changes, None


def _unbindable(root, sym: str) -> str:
    """Why ``sym`` cannot be bound: its file is code the index's excludes leave
    out, or the file does not define it."""
    from pathlib import PurePosixPath
    from codoc.index.app import source_matcher
    from codoc.index.lang import detect_language
    file = sym.split("::")[0]
    if ((Path(root) / file).is_file() and detect_language(file) is not None
            and not source_matcher(Path(root).resolve()).is_file_included(PurePosixPath(file))):
        return (f"{sym} is in a file codoc does not index (index_exclude in .codoc/config.json, or the root "
                ".gitignore, covers it), so it cannot be bound")
    return f"the code does not define {sym}; bind only symbols the files hold now"


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
    changes, err = _compile(codoc_dir, root_dir, ws.store, ops, caused_by)
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
    # A plan describes code not yet written, so it reports no build: an owned
    # symbol is not refreshed under it.
    changes, err = _compile(codoc_dir, root_dir, ws.store, ops, None)
    if err:
        return _err(err)
    if not changes:
        return _err("no ops")
    out = _write_plans(codoc_dir, ws.store, changes, note=note, caused_by=caused_by, root_dir=root_dir, build=True)
    _cite(codoc_dir, caused_by, [out])
    return out


def _write_plans(codoc_dir: str, store, changes: list[Change], *, note: str, caused_by: str | None,
                 root_dir: str | None, build: bool) -> dict:
    """Each deletion as its own delete plan (accepting one deletes that
    feature), everything else as one plan. A feature already proposed for
    deletion, by an open plan or earlier in this call, is not proposed again.
    ``build`` says accepting the plan asks for the code: true for a plan
    made before the code, false for a reflection of code already written.
    Returns every plan written, and why each part that was not written was
    refused; an error only when no plan was written."""
    deletions: dict[str, Change] = {}
    for c in changes:
        if c.kind == "delete_node" and c.target not in deletions and not delete_open(store, c.target):
            deletions[c.target] = c
    rest = [c for c in changes if c.kind != "delete_node"]
    groups = ([(rest, note, build)] if rest else []) + [([c], c.note or note, False) for c in deletions.values()]
    results = [_write_plan(codoc_dir, store, group, note=n, caused_by=caused_by, root_dir=root_dir, build=b)
               for group, n, b in groups]
    written = [r for r in results if r.get("ok")]
    if not written:
        return results[0] if results else _err("every feature named is already proposed for deletion")
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
    kind = kinds.pop() if len(kinds) == 1 else "edit"      # a delete plan holds exactly one deletion
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
        bodies, (bid,) = block_bodies([BlockSpec(kind, content=content)], after=store.body(feature_id)[-1].id)
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
    """Renew the realizing lease so the editor shows 'building n of m'.
    The lease is this server's own (the realize pass's that started the agent,
    else this process's), so this agent's status does not read it as another
    pass. Returns the hand-offs this turn has reported."""
    shown = min(done + 1, total)
    write_realizing(codoc_dir, pending=max(0, total - done), owner=os.environ.setdefault(REALIZE_OWNER_ENV, f"mcp-{os.getpid()}"),
                    detail=f"building {shown} of {total}" + (f": {current}" if current else ""))
    return {"ok": True, "reported": [f"d:{d}" for d in read_activity(codoc_dir).get("cited") or []]}
