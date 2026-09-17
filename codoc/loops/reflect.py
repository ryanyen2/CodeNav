"""Code → document (was ``loop_a.py``, 1,561 lines; the decision table's rows 1–6).

Given a change set from the index, the pass:

1. maintains bindings deterministically: ``bind.refresh`` for modified bound
   chunks, ``bind.detach`` for removed ones, ``bind.attach`` for relocations
   (identical content moved, or a unique AST shape renamed);
2. opens a ``delete`` plan for every feature that just lost its last binding
   (the loop never deletes; it proposes);
3. asks one LLM pass to place the added chunks and repair stale prose. Its
   answer is block-keyed. Attachments apply on the trunk (attribution, not
   intent). New nodes, moves and deletions open plans. Prose repairs enter
   through ``ingest`` under the amend gate: a small repair lands on the trunk
   with ``actor=loop``; a large one, or any repair on a feature with unsent
   ink or queued work, becomes a plan (the document wins);
4. covers what the model left unplaced: a chunk lands with the feature that
   owns most of its graph neighbours, else in a plan for a node named after
   its file.

Every op this pass writes on a feature with queued work carries ``caused_by``
the feature's latest hand-off, which is what lets the daemon mark the feature
realized and lets History chain a change back to the request.

``propose`` is injectable: a callable ``(changes, subtree, all_titles, **kw) →
list[dict]`` of raw ops in the prompt's JSON shape. Tests inject a fake.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from codoc.calculus import ops as K
from codoc.calculus.derive import phase
from codoc.calculus.doc import HEADING, PARAGRAPH, parent_of
from codoc.calculus.ids import START, new_block_id
from codoc.calculus.store import Store
from codoc.calculus.text import SUGGEST_DEL
from codoc.graph.graph import Graph
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loops.context import impacted_features, owner_of, select_context, written_by
from codoc.substrate.workspace import Workspace

LOOP = "loop"
MAX_ADDED_PER_CALL = 25
GATE_MACHINE = 0.5      # deleted fraction above which a loop repair becomes a plan
GATE_HUMAN = 0.15       # …when a person last wrote the block


@dataclass
class ReflectResult:
    auto: Counter = field(default_factory=Counter)
    plans: list[str] = field(default_factory=list)
    llm_calls: int = 0
    refused: list[tuple[str, str]] = field(default_factory=list)   # (bid, reason)
    impacted: list[str] = field(default_factory=list)
    caused: dict[str, str] = field(default_factory=dict)            # fid -> handoff op cited

    def summary(self) -> str:
        parts = [", ".join(f"{n} {k}" for k, n in sorted(self.auto.items())) or "no auto ops"]
        if self.plans:
            parts.append(f"{len(self.plans)} plan(s)")
        if self.llm_calls:
            parts.append(f"{self.llm_calls} LLM call(s)")
        if self.refused:
            parts.append(f"{len(self.refused)} refused")
        return " · ".join(parts)


def reflect(ws: Workspace, cs: ChangeSet, graph: Graph, *, propose, repo_name: str = "codebase",
            config=None, doc_language=None, allow_delete: bool = True, extra_changes: dict | None = None,
            index_keys: set[tuple[str, str]] | None = None) -> ReflectResult:
    store = ws.store
    res = ReflectResult()
    fp = cs.fingerprints()
    th = cs.types_hashes()
    index_keys = index_keys if index_keys is not None else ({(r.file, r.symbol_path) for r in cs.rows} or None)
    caused_by = _causes(store)
    res.caused = dict(caused_by)

    def cause(fid: str | None) -> str | None:
        return caused_by.get(fid) if fid else None

    # 1. Deterministic binding maintenance.
    removed_owner: dict[tuple[str, str], str] = {}
    bodies: list[dict] = []
    for r in cs.removed:
        b = store.bindings.get((r.file, r.symbol_path))
        if b:
            removed_owner[(r.file, r.symbol_path)] = b.feature
            bodies.append(_bind(K.BIND_DETACH, b.feature, r.file, r.symbol_path, caused_by=cause(b.feature)))
    for c in cs.modified:
        b = store.bindings.get((c.file, c.symbol_path))
        if b and (b.fingerprint != c.fingerprint or b.types_hash != c.types_hash):
            bodies.append(_bind(K.BIND_REFRESH, b.feature, c.file, c.symbol_path, c.fingerprint, c.types_hash,
                                caused_by=cause(b.feature)))
    if bodies:
        for op in ws.submit(bodies, actor=LOOP):
            if op.conflict is None:
                res.auto[op.kind.split(".")[1]] += 1
    relocated: set[tuple[str, str]] = set()
    reloc_bodies = []
    for added, removed, kind in _relocations(cs, removed_owner):
        owner = removed_owner[(removed.file, removed.symbol_path)]
        reloc_bodies.append(_bind(K.BIND_ATTACH, owner, added.file, added.symbol_path, added.fingerprint,
                                  added.types_hash, caused_by=cause(owner)))
        relocated.add((added.file, added.symbol_path))
    if reloc_bodies:
        for op in ws.submit(reloc_bodies, actor=LOOP):
            if op.conflict is None:
                res.auto["relocate"] += 1

    # 2. Emptied features → delete plans.
    if allow_delete:
        for fid in sorted(set(removed_owner.values())):
            b = store.blocks.get(fid)
            if b is None or not b.live or any(x.feature == fid for x in store.bindings.values()):
                continue
            if phase(store, fid) in ("edited", "queued", "planned"):
                continue   # the document wins; the person is re-specifying it
            if any(p.kind == "delete" and p.target == fid and p.status == "open" for p in store.plans.values()):
                continue
            res.plans.append(_delete_plan(ws, fid, note="lost all of its code"))

    # 3. The LLM pass over what needs judgment.
    added_unbound = [a for a in cs.added if (a.file, a.symbol_path) not in store.bindings
                     and (a.file, a.symbol_path) not in relocated]
    modified_bound = [(c, store.bindings[(c.file, c.symbol_path)].feature) for c in cs.modified
                      if (c.file, c.symbol_path) in store.bindings]
    if not added_unbound and not modified_bound:
        return res
    changed_symbols = {c.symbol_path for c in cs.added + cs.modified + cs.removed}
    subtree, all_titles, graph_ctx = select_context(store, graph, cs.touched_files(), changed_symbols)
    impacted = impacted_features(store, graph, changed_symbols)
    res.impacted = sorted(impacted)
    changes: dict = {
        "added": [],
        "modified": [{"file": c.file, "symbol_path": c.symbol_path, "current_feature_id": fid,
                      "source": (c.source or "")[:600]} for c, fid in modified_bound],
        "removed": [{"file": r.file, "symbol_path": r.symbol_path,
                     "current_feature_id": removed_owner.get((r.file, r.symbol_path))} for r in cs.removed],
        "graph": graph_ctx,
    }
    if impacted:
        changes["impacted"] = [{"feature_id": f, "via": sorted(v)} for f, v in impacted.items()]
    if extra_changes:
        changes.update(extra_changes)
    batches = _batches(added_unbound, MAX_ADDED_PER_CALL) or [[]]
    titles = list(all_titles)
    raw_ops: list[dict] = []
    for batch in batches:
        changes["added"] = [{"file": a.file, "symbol_path": a.symbol_path, "source": (a.source or "")[:600]}
                            for a in batch]
        fresh = propose(changes, subtree, titles, repo_name=repo_name, config=config, doc_language=doc_language)
        raw_ops.extend(fresh or [])
        titles = titles + [{"id": "(proposed this pass)", "title": o.get("title"), "parent_id": o.get("parent_id")}
                           for o in (fresh or []) if o.get("kind") == "add_node" and o.get("title")]
        res.llm_calls += 1

    covered: set[tuple[str, str]] = set()
    touched_keys = set(fp.keys()) | {(r.file, r.symbol_path) for r in cs.removed}
    for raw in raw_ops:
        kind = raw.get("kind")
        try:
            if kind == "attach":
                fid = raw.get("feature_id")
                if fid not in store.blocks or not store.blocks[fid].live:
                    continue
                binds = _valid_binds(raw.get("bindings", []), index_keys, touched_keys)
                if binds:
                    ws.submit([_bind(K.BIND_ATTACH, fid, f, s, fp.get((f, s), ""), th.get((f, s), ""),
                                     caused_by=cause(fid)) for f, s in binds], actor=LOOP)
                    covered.update(binds)
                    res.auto["attach"] += len(binds)
            elif kind == "add_node":
                binds = _valid_binds(raw.get("bindings", []), index_keys, touched_keys)
                pid = _add_plan(ws, raw, binds, fp, th)
                if pid:
                    res.plans.append(pid)
                    covered.update(binds)
            elif kind == "amend":
                _amend(ws, raw, res, cause)
            elif kind == "move_node":
                pid = _move_plan(ws, raw)
                if pid:
                    res.plans.append(pid)
            elif kind == "retire_node":
                fid = raw.get("feature_id")
                if allow_delete and fid in store.blocks and store.blocks[fid].live \
                        and phase(store, fid) not in ("edited", "queued", "planned"):
                    res.plans.append(_delete_plan(ws, fid, note=raw.get("rationale", "")))
        except (KeyError, TypeError, ValueError):
            continue

    # 4. Coverage net for what the model left unplaced.
    for a in added_unbound:
        key = (a.file, a.symbol_path)
        if key in covered or key in store.bindings:
            continue
        home = graph.neighbor_feature(a.symbol_path, lambda s: owner_of(store, s))
        if home and store.blocks.get(home) and store.blocks[home].live:
            ws.submit([_bind(K.BIND_ATTACH, home, a.file, a.symbol_path, a.fingerprint, a.types_hash,
                             caused_by=cause(home))], actor=LOOP)
            res.auto["net"] += 1
        else:
            pid = _add_plan(ws, {"title": _title_from_file(a.file), "parent_id": None,
                                 "description": "", "rationale": "no feature covered this file"},
                            [key], fp, th)
            if pid:
                res.plans.append(pid)
    return res


# ── helpers ──────────────────────────────────────────────────────────────────

def _bind(kind: str, fid: str, file: str, symbol: str, fingerprint: str = "", types_hash: str = "",
          caused_by: str | None = None) -> dict:
    body = {"kind": kind, "target": {"fid": fid}, "payload": {"file": file, "symbol": symbol}}
    if kind != K.BIND_DETACH:
        body["payload"].update({"fingerprint": fingerprint, "types_hash": types_hash})
    if caused_by:
        body["caused_by"] = caused_by
    return body


def _causes(store: Store) -> dict[str, str]:
    """feature → its latest hand-off op id, for features with queued work."""
    out = {}
    for fid, w in store.watermarks_by_fid.items():
        if w.handoffs and phase(store, fid) in ("queued", "planned"):
            out[fid] = w.handoffs[-1]
    return out


def _valid_binds(raw_binds, index_keys, touched_keys) -> list[tuple[str, str]]:
    out = []
    for b in raw_binds or []:
        if not isinstance(b, (list, tuple)) or len(b) != 2:
            continue
        key = (str(b[0]), str(b[1]))
        if index_keys is not None and key not in index_keys:
            continue      # the model invented a symbol
        if key not in touched_keys:
            continue      # outside the change set: the model may not reshuffle untouched code
        out.append(key)
    return out


def _relocations(cs: ChangeSet, removed_owner: dict) -> list[tuple[ChunkRef, ChunkRef, str]]:
    removed = [r for r in cs.removed if (r.file, r.symbol_path) in removed_owner]
    used: set[tuple[str, str]] = set()
    out = []
    by_tok: dict[str, list[ChunkRef]] = {}
    for r in removed:
        by_tok.setdefault(r.fingerprint, []).append(r)
    matched: set[tuple[str, str]] = set()
    for a in cs.added:
        cands = [c for c in by_tok.get(a.fingerprint, []) if (c.file, c.symbol_path) not in used]
        if a.fingerprint and cands:
            c = cands[0]
            used.add((c.file, c.symbol_path))
            matched.add((a.file, a.symbol_path))
            out.append((a, c, "move"))
    # rename: a unique AST shape among the still-unmatched, same file first then anywhere
    by_shape_removed: dict[str, list[ChunkRef]] = {}
    for r in removed:
        if (r.file, r.symbol_path) not in used and r.types_hash:
            by_shape_removed.setdefault(r.types_hash, []).append(r)
    by_shape_added: dict[str, list[ChunkRef]] = {}
    for a in cs.added:
        if (a.file, a.symbol_path) not in matched and a.types_hash:
            by_shape_added.setdefault(a.types_hash, []).append(a)
    for shape, adds in by_shape_added.items():
        rems = by_shape_removed.get(shape, [])
        if len(adds) == 1 and len(rems) == 1:
            out.append((adds[0], rems[0], "rename"))
            used.add((rems[0].file, rems[0].symbol_path))
    return out


def _batches(items: list, size: int) -> list[list]:
    if not items:
        return []
    by_file: dict[str, list] = {}
    for a in items:
        by_file.setdefault(a.file, []).append(a)
    batches: list[list] = []
    cur: list = []
    for f in sorted(by_file):
        chunk = by_file[f]
        if cur and len(cur) + len(chunk) > size:
            batches.append(cur)
            cur = []
        cur.extend(chunk)
    if cur:
        batches.append(cur)
    return batches


def _title_from_file(file: str) -> str:
    import os
    stem = os.path.splitext(os.path.basename(file))[0]
    words = stem.replace("-", " ").replace("_", " ").strip()
    return (words[:1].upper() + words[1:]) if words else file


def _place_after(store: Store, parent: str | None) -> tuple[str, int]:
    """Where a new child of ``parent`` goes: after the parent's last dominated
    block, one level deeper. Top level: at the end."""
    if parent and parent in store.blocks and store.blocks[parent].live:
        rng = store.feature_range(parent)
        return rng[-1].id, store.blocks[parent].level + 1
    live = store.ordered()
    return (live[-1].id if live else START), 1


def _add_plan(ws: Workspace, raw: dict, binds: list[tuple[str, str]], fp: dict, th: dict) -> str | None:
    title = (raw.get("title") or "").strip()
    if not title:
        return None
    parent = raw.get("parent_id")
    after, level = _place_after(ws.store, parent)
    fid = new_block_id()
    pid = ws.open_plan(actor=LOOP, kind="add", target=fid, note=raw.get("rationale", ""))
    bodies = [
        {"kind": K.BLOCK_ADD, "target": {"bid": fid}, "payload": {"kind": HEADING, "level": level, "after": after}},
        {"kind": K.TEXT_INSERT, "target": {"bid": fid, "path": "text"}, "payload": {"after": START, "text": title}},
    ]
    ws.submit(bodies, actor=LOOP, plan=pid, batch=pid)
    desc = (raw.get("description") or "").strip()
    last = fid
    for para in [p.strip() for p in desc.split("\n\n") if p.strip()]:
        bid = new_block_id()
        ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": PARAGRAPH, "after": last}}],
                  actor=LOOP, plan=pid, batch=pid)
        ws.ingest(bid, "text", base_text="", new_md=para, actor=LOOP, plan=pid, batch=pid)
        last = bid
    if binds:
        # Bindings are attribution and land on the trunk even for a planned
        # node; rejecting the plan deletes the node and its bindings with it.
        ws.submit([_bind(K.BIND_ATTACH, fid, f, s, fp.get((f, s), ""), th.get((f, s), "")) for f, s in binds],
                  actor=LOOP, batch=pid)
    return pid


def _move_plan(ws: Workspace, raw: dict) -> str | None:
    fid = raw.get("feature_id")
    store = ws.store
    if fid not in store.blocks or not store.blocks[fid].live:
        return None
    parent = raw.get("parent_id")
    after, level = _place_after(store, parent)
    if after == fid or after in {b.id for b in store.feature_range(fid)}:
        return None
    pid = ws.open_plan(actor=LOOP, kind="move", target=fid, note=raw.get("rationale", ""))
    ws.submit([{"kind": K.BLOCK_MOVE, "target": {"bid": fid},
                "payload": {"after": after, "level_delta": level - store.blocks[fid].level}}],
              actor=LOOP, plan=pid, batch=pid)
    return pid


def _delete_plan(ws: Workspace, fid: str, note: str) -> str:
    pid = ws.open_plan(actor=LOOP, kind="delete", target=fid, note=note)
    bodies = []
    for b in ws.store.feature_body(fid):
        t = b.text()
        if t.live_len():
            bodies.append({"kind": K.TEXT_MARK, "target": {"bid": b.id, "path": "text"},
                           "payload": {"kind": SUGGEST_DEL, "attrs": {"plan": pid},
                                       "start": list(t.ref_at(0)), "end": list(t.last_live_ref()), "on": True}})
    if bodies:
        ws.submit(bodies, actor=LOOP, plan=pid, batch=pid)
    return pid


def _amend(ws: Workspace, raw: dict, res: ReflectResult, cause) -> None:
    """A block-keyed repair: ``title`` and/or ``blocks: [{id?, text}]``. Listed
    ids are re-ingested; an entry without an id is a new paragraph after the
    previous one; ids the model omitted are struck under a plan."""
    store = ws.store
    fid = raw.get("feature_id")
    if fid not in store.blocks or not store.blocks[fid].live:
        return
    held = phase(store, fid) in ("edited", "queued", "planned")
    human = written_by(store, fid) == "human"
    gate = GATE_HUMAN if human else GATE_MACHINE
    plan: str | None = None
    if held:
        plan = ws.open_plan(actor=LOOP, kind="edit", target=fid, note="the document has pending intent")
        res.plans.append(plan)
    batch = plan or None
    if raw.get("title"):
        r, _ = ws.ingest(fid, "text", base_text=store.blocks[fid].text().live_text(), new_md=raw["title"],
                         actor=LOOP, plan=plan, gate=None if plan else gate, caused_by=cause(fid), batch=batch)
        if r.refused:
            res.refused.append((fid, r.refused))
        elif r.demote_to_plan and not plan:
            res.plans.append(_last_plan(ws))
    if "blocks" not in raw:
        return
    body = [b for b in store.feature_body(fid)[1:] if b.kind == PARAGRAPH]
    seen: set[str] = set()
    last = fid
    for entry in raw["blocks"]:
        text = (entry.get("text") or "").strip()
        bid = entry.get("id")
        if bid and bid in store.blocks and store.blocks[bid].live:
            seen.add(bid)
            r, _ = ws.ingest(bid, "text", base_text=store.blocks[bid].text().live_text(), new_md=text, actor=LOOP,
                             plan=plan, gate=None if plan else gate, caused_by=cause(fid), batch=batch)
            if r.refused:
                res.refused.append((bid, r.refused))
            elif r.demote_to_plan and not plan:
                res.plans.append(_last_plan(ws))
            last = bid
        elif text:
            nb = new_block_id()
            p = plan or ws.open_plan(actor=LOOP, kind="edit", target=fid, note="new paragraph")
            if not plan:
                res.plans.append(p)
            ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": nb}, "payload": {"kind": PARAGRAPH, "after": last}}],
                      actor=LOOP, plan=p, batch=p, caused_by=cause(fid))
            ws.ingest(nb, "text", base_text="", new_md=text, actor=LOOP, plan=p, batch=p, caused_by=cause(fid))
            last = nb
    omitted = [b for b in body if b.id not in seen]
    if omitted:
        p = plan or ws.open_plan(actor=LOOP, kind="edit", target=fid, note="paragraph no longer true")
        if not plan:
            res.plans.append(p)
        bodies = []
        for b in omitted:
            t = b.text()
            if t.live_len():
                bodies.append({"kind": K.TEXT_MARK, "target": {"bid": b.id, "path": "text"},
                               "payload": {"kind": SUGGEST_DEL, "attrs": {"plan": p},
                                           "start": list(t.ref_at(0)), "end": list(t.last_live_ref()), "on": True}})
        if bodies:
            ws.submit(bodies, actor=LOOP, plan=p, batch=p, caused_by=cause(fid))


def _last_plan(ws: Workspace) -> str:
    return max(ws.store.plans.values(), key=lambda p: p.opened_by or "").id
