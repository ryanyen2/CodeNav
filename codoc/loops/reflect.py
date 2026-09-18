"""Code → document (was ``loop_a.py``, 1,561 lines; the decision table's rows 1–6).

Given a change set from the index, the pass:

1. maintains bindings deterministically: ``bind.refresh`` for modified bound
   chunks, ``bind.detach`` for removed ones, ``bind.attach`` for relocations
   (identical content moved, or a unique AST shape renamed);
2. opens a ``delete`` plan for every feature that just lost its last binding
   (the loop never deletes; it proposes);
3. asks one LLM pass to place the added chunks and repair stale prose. Its
   answer is block-keyed and validated before anything is written.
   Attachments to existing features apply on the trunk (attribution, not
   intent). New nodes, moves and deletions open plans; a new node's bindings
   are part of its plan. Prose repairs enter through ``ingest`` under the amend
   gate: an amend whose every part is a small repair lands on the trunk with
   ``actor=loop``; one that rewrites a block past the gate, adds or strikes a
   paragraph, or touches a feature with unsent ink or queued work, becomes ONE
   plan (the document wins, and one repair gets one verdict);
4. covers what the model left unplaced: a chunk lands with the feature that
   owns most of its graph neighbours, else in a plan for a node named after
   its file.

When the pass runs because an agent session closed (``under_epoch``), every op
it writes on a feature with queued work carries ``caused_by`` the feature's
latest hand-off: that is what lets the daemon mark the feature realized and
lets History chain a change back to the request. A pass triggered by a hand
edit cites nothing, since nobody implemented anything.

``propose`` is injectable: a callable ``(changes, subtree, all_titles, **kw) →
list[dict]`` of raw ops in the prompt's JSON shape. Tests inject a fake.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from codoc.calculus import ops as K
from codoc.calculus.derive import phase
from codoc.calculus.doc import HEADING, PARAGRAPH, text_fields
from codoc.calculus.ids import START, new_block_id
from codoc.calculus.ingest import Ingest
from codoc.calculus.ops import Op, covered
from codoc.calculus.store import Store
from codoc.calculus.text import SUGGEST_DEL
from codoc.graph.graph import Graph
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loops.context import block_written_by, impacted_features, owner_of, select_context
from codoc.substrate.workspace import Workspace

LOOP = "loop"
MAX_ADDED_PER_CALL = 25
GATE_MACHINE = 0.5      # deleted fraction above which a loop repair becomes a plan
GATE_HUMAN = 0.15       # …when a person last wrote the block
HELD = ("edited", "queued", "planned")


@dataclass
class ReflectResult:
    auto: Counter = field(default_factory=Counter)
    plans: list[str] = field(default_factory=list)
    llm_calls: int = 0
    refused: list[tuple[str, str]] = field(default_factory=list)   # (bid or kind, reason)
    impacted: list[str] = field(default_factory=list)
    caused: dict[str, str] = field(default_factory=dict)            # fid -> handoff op cited
    removed: set[tuple[str, str]] = field(default_factory=set)      # (file, symbol) the code no longer has
    ops: list[Op] = field(default_factory=list)                     # everything this pass wrote

    def summary(self) -> str:
        parts = [", ".join(f"{n} {k}" for k, n in sorted(self.auto.items())) or "no auto ops"]
        if self.plans:
            parts.append(f"{len(self.plans)} plan(s)")
        if self.llm_calls:
            parts.append(f"{self.llm_calls} LLM call(s)")
        if self.refused:
            parts.append(f"{len(self.refused)} refused")
        return " · ".join(parts)


class _Pass:
    """One reflect pass's writer: every submit and ingest lands in ``res.ops``."""

    def __init__(self, ws: Workspace, res: ReflectResult, caused_by: dict[str, str]) -> None:
        self.ws, self.res, self._caused = ws, res, caused_by

    @property
    def store(self) -> Store:
        return self.ws.store

    def cause(self, fid: str | None) -> str | None:
        return self._caused.get(fid) if fid else None

    def submit(self, bodies: list[dict], **kw) -> list[Op]:
        ops = self.ws.submit(bodies, actor=LOOP, **kw)
        self.res.ops.extend(ops)
        return ops

    def commit(self, ing: Ingest, **kw) -> list[Op]:
        ops = self.ws.commit_ingest(ing, actor=LOOP, **kw)
        self.res.ops.extend(ops)
        return ops

    def open_plan(self, **kw) -> str:
        pid = self.ws.open_plan(actor=LOOP, **kw)
        self.res.ops.append(self.store.by_id[self.store.plans[pid].opened_by])
        self.res.plans.append(pid)
        return pid


def reflect(ws: Workspace, cs: ChangeSet, graph: Graph, *, propose, repo_name: str = "codebase",
            config=None, doc_language=None, allow_delete: bool = True, extra_changes: dict | None = None,
            under_epoch: bool = False) -> ReflectResult:
    store = ws.store
    res = ReflectResult()
    fp = cs.fingerprints()
    th = cs.types_hashes()
    caused_by = _causes(store) if under_epoch else {}
    res.caused = dict(caused_by)
    res.removed = {(r.file, r.symbol_path) for r in cs.removed}
    p = _Pass(ws, res, caused_by)

    # 1. Deterministic binding maintenance.
    removed_owner: dict[tuple[str, str], str] = {}
    bodies: list[dict] = []
    for r in cs.removed:
        b = store.bindings.get((r.file, r.symbol_path))
        if b:
            removed_owner[(r.file, r.symbol_path)] = b.feature
            bodies.append(_bind(K.BIND_DETACH, b.feature, r.file, r.symbol_path, caused_by=p.cause(b.feature)))
    for c in cs.modified:
        b = store.bindings.get((c.file, c.symbol_path))
        if b and (b.fingerprint != c.fingerprint or b.types_hash != c.types_hash):
            bodies.append(_bind(K.BIND_REFRESH, b.feature, c.file, c.symbol_path, c.fingerprint, c.types_hash,
                                caused_by=p.cause(b.feature)))
    if bodies:
        for op in p.submit(bodies):
            if op.conflict is None:
                res.auto[op.kind.split(".")[1]] += 1
    relocated: set[tuple[str, str]] = set()
    reloc_bodies = []
    for added, removed, _kind in _relocations(cs, removed_owner):
        owner = removed_owner[(removed.file, removed.symbol_path)]
        reloc_bodies.append(_bind(K.BIND_ATTACH, owner, added.file, added.symbol_path, added.fingerprint,
                                  added.types_hash, caused_by=p.cause(owner)))
        relocated.add((added.file, added.symbol_path))
    if reloc_bodies:
        for op in p.submit(reloc_bodies):
            if op.conflict is None:
                res.auto["relocate"] += 1

    # 2. Emptied features → delete plans. The rule is state-based, not edge-
    # triggered: any live feature that once had code and has none now, has
    # never been proposed for deletion, and is not held. A feature emptied
    # while the person was typing is therefore proposed on the next quiet pass
    # instead of being missed forever.
    if allow_delete:
        for fid in _emptied(store):
            if phase(store, fid) not in HELD:     # the document wins while they are re-specifying it
                _delete_plan(p, fid, note="lost all of its code")

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
                           for o in (fresh or []) if isinstance(o, dict) and o.get("kind") == "add_node" and o.get("title")]
        res.llm_calls += 1

    covered: set[tuple[str, str]] = set()
    # The model may bind only what this change set added or modified: those
    # chunks exist by construction, and untouched code is not its to reshuffle.
    touched_keys = set(fp)
    for raw in raw_ops:
        err = validate_raw(raw)
        if err:
            res.refused.append((str(raw.get("kind") if isinstance(raw, dict) else raw), err))
            continue
        kind = raw["kind"]
        try:
            if kind == "attach":
                fid = raw["feature_id"]
                if fid not in store.blocks or not store.blocks[fid].live:
                    continue
                binds = _valid_binds(raw.get("bindings", []), touched_keys)
                if binds:
                    p.submit([_bind(K.BIND_ATTACH, fid, f, s, fp.get((f, s), ""), th.get((f, s), ""),
                                    caused_by=p.cause(fid)) for f, s in binds])
                    covered.update(binds)
                    res.auto["attach"] += len(binds)
            elif kind == "add_node":
                binds = _valid_binds(raw.get("bindings", []), touched_keys)
                if _add_plan(p, raw, binds, fp, th):
                    covered.update(binds)
            elif kind == "amend":
                _amend(p, raw)
            elif kind == "move_node":
                _move_plan(p, raw)
            elif kind == "retire_node":
                fid = raw["feature_id"]
                if allow_delete and fid in store.blocks and store.blocks[fid].live and phase(store, fid) not in HELD:
                    _delete_plan(p, fid, note=raw.get("rationale", ""))
        except (KeyError, TypeError, ValueError) as e:   # unreachable after validate_raw; recorded, never swallowed
            res.refused.append((kind, repr(e)))

    # 4. Coverage net for what the model left unplaced.
    for a in added_unbound:
        key = (a.file, a.symbol_path)
        if key in covered or key in store.bindings:
            continue
        home = graph.neighbor_feature(a.symbol_path, lambda s: owner_of(store, s))
        if home and store.blocks.get(home) and store.blocks[home].live:
            p.submit([_bind(K.BIND_ATTACH, home, a.file, a.symbol_path, a.fingerprint, a.types_hash,
                            caused_by=p.cause(home))])
            res.auto["net"] += 1
        else:
            _add_plan(p, {"title": _title_from_file(a.file), "parent_id": None, "description": "",
                          "rationale": "no feature covered this file"}, [key], fp, th)
    return res


# ── the model's answer ───────────────────────────────────────────────────────

_RAW_FIELDS = {
    "attach": {"feature_id": str, "bindings": list},
    "add_node": {"title": str},
    "amend": {"feature_id": str},
    "move_node": {"feature_id": str},
    "retire_node": {"feature_id": str},
}


def validate_raw(raw) -> str | None:
    """Why a raw op from the model cannot be acted on, or None. Checked before
    the first write so a malformed answer never leaves a half-built plan."""
    if not isinstance(raw, dict):
        return "not an object"
    kind = raw.get("kind")
    if kind not in _RAW_FIELDS:
        return f"unknown kind {kind!r}"
    for name, typ in _RAW_FIELDS[kind].items():
        if not isinstance(raw.get(name), typ):
            return f"{kind}.{name} must be {typ.__name__}"
    for name in ("title", "description", "rationale", "parent_id"):
        if raw.get(name) is not None and not isinstance(raw[name], str):
            return f"{kind}.{name} must be a string"
    if "bindings" in raw and not all(isinstance(b, (list, tuple)) and len(b) == 2 for b in raw["bindings"] or []):
        return f"{kind}.bindings must be [file, symbol] pairs"
    if kind == "amend":
        blocks = raw.get("blocks")
        if blocks is not None:
            if not isinstance(blocks, list) or not all(isinstance(e, dict) for e in blocks):
                return "amend.blocks must be a list of objects"
            for e in blocks:
                if e.get("id") is not None and not isinstance(e["id"], str):
                    return "amend.blocks[].id must be a string"
                if e.get("text") is not None and not isinstance(e["text"], str):
                    return "amend.blocks[].text must be a string"
                if e.get("id") is None and not e.get("delete") and not (e.get("text") or "").strip():
                    return "amend.blocks[] entry has neither id nor text"
    return None


# ── helpers ──────────────────────────────────────────────────────────────────

def _bind(kind: str, fid: str, file: str, symbol: str, fingerprint: str = "", types_hash: str = "",
          caused_by: str | None = None) -> dict:
    body = {"kind": kind, "target": {"fid": fid}, "payload": {"file": file, "symbol": symbol}}
    if kind != K.BIND_DETACH:
        body["payload"].update({"fingerprint": fingerprint, "types_hash": types_hash})
    if caused_by:
        body["caused_by"] = caused_by
    return body


def _emptied(store: Store) -> list[str]:
    """Live features with no bindings that used to have some, and no delete
    plan (of any verdict: a rejected one is an answer, not an invitation)."""
    bound = {b.feature for b in store.bindings.values()}
    proposed = {pl.target for pl in store.plans.values() if pl.kind == "delete"}
    out = []
    for h in store.headings():
        if h.id in bound or h.id in proposed:
            continue
        # "Once had code" is any attach it ever held, not a detach: a feature
        # whose only symbol was re-attached elsewhere (I8 displacement) has no
        # detach of its own and is just as empty.
        if any(o.kind == K.BIND_ATTACH and o.target.get("fid") == h.id and not o.conflict
               for o in store.ops_on([h.id])):
            out.append(h.id)
    return out


def _causes(store: Store) -> dict[str, str]:
    """feature → its latest hand-off op id, for every hand-off not yet covered
    by a realization (whatever the person typed since; that ink is beyond the
    frontier and stays unsent)."""
    return {fid: w.handoffs[-1] for fid, w in store.watermarks_by_fid.items()
            if w.handoffs and not covered(w.handoff, w.realized)}


def _valid_binds(raw_binds, touched_keys) -> list[tuple[str, str]]:
    return [(str(b[0]), str(b[1])) for b in raw_binds or []
            if isinstance(b, (list, tuple)) and len(b) == 2 and (str(b[0]), str(b[1])) in touched_keys]


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
    # rename: a unique AST shape among everything that left the index (bound
    # or not) matched with a unique shape among what arrived
    shapes_removed_all = Counter(r.types_hash for r in cs.removed if r.types_hash)
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
        if len(adds) == 1 and len(rems) == 1 and shapes_removed_all[shape] == 1:
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


def _add_plan(p: _Pass, raw: dict, binds: list[tuple[str, str]], fp: dict, th: dict) -> str | None:
    """A proposed node: heading, title, paragraphs and its bindings, all under
    one plan. Rejecting it detaches the bindings with the rest."""
    title = (raw.get("title") or "").strip()
    if not title:
        return None
    after, level = _place_after(p.store, raw.get("parent_id"))
    fid = new_block_id()
    paragraphs = [(new_block_id(), para.strip()) for para in (raw.get("description") or "").split("\n\n") if para.strip()]
    pid = p.open_plan(kind="add", target=fid, note=raw.get("rationale", ""))
    bodies = [
        {"kind": K.BLOCK_ADD, "target": {"bid": fid}, "payload": {"kind": HEADING, "level": level, "after": after}},
        {"kind": K.TEXT_INSERT, "target": {"bid": fid, "path": "text"}, "payload": {"after": START, "text": title}},
    ]
    last = fid
    for bid, _ in paragraphs:
        bodies.append({"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": PARAGRAPH, "after": last}})
        last = bid
    bodies.extend(_bind(K.BIND_ATTACH, fid, f, s, fp.get((f, s), ""), th.get((f, s), "")) for f, s in binds)
    p.submit(bodies, plan=pid, batch=pid)
    for bid, para in paragraphs:
        ing = p.ws.plan_ingest(bid, "text", base_text="", new_md=para, actor=LOOP, trunk=False)
        if ing.refused:
            p.res.refused.append((bid, ing.refused))
        else:
            p.commit(ing, plan=pid, batch=pid)
    return pid


def _move_plan(p: _Pass, raw: dict) -> str | None:
    fid = raw["feature_id"]
    store = p.store
    if fid not in store.blocks or not store.blocks[fid].live:
        return None
    after, level = _place_after(store, raw.get("parent_id"))
    if after == fid or after in {b.id for b in store.feature_range(fid)}:
        return None
    pid = p.open_plan(kind="move", target=fid, note=raw.get("rationale", ""))
    p.submit([{"kind": K.BLOCK_MOVE, "target": {"bid": fid},
               "payload": {"after": after, "level_delta": level - store.blocks[fid].level}}], plan=pid, batch=pid)
    return pid


def _strike(store: Store, bids: list[str], pid: str) -> list[dict]:
    """Propose removing every word of these blocks: one ``suggest_del`` per
    Text field (a diagram's caption is struck with its source)."""
    bodies = []
    for bid in bids:
        b = store.blocks[bid]
        for f in text_fields(b.kind):
            t = b.text(f)
            if t.live_len():
                bodies.append({"kind": K.TEXT_MARK, "target": {"bid": bid, "path": f},
                               "payload": {"kind": SUGGEST_DEL, "attrs": {"plan": pid},
                                           "start": list(t.ref_at(0)), "end": list(t.last_live_ref()), "on": True}})
    return bodies


def _delete_plan(p: _Pass, fid: str, note: str) -> str:
    pid = p.open_plan(kind="delete", target=fid, note=note)
    bodies = _strike(p.store, [b.id for b in p.store.feature_body(fid)], pid)
    if bodies:
        p.submit(bodies, plan=pid, batch=pid)
    return pid


def _amend(p: _Pass, raw: dict) -> None:
    """A block-keyed repair: ``title`` and/or ``blocks: [{id, text} |
    {id, delete: true} | {text}]``. A listed id is re-ingested; ``delete``
    strikes it; an entry without an id is a new paragraph after the previous
    entry; a paragraph the model does not mention is unchanged. Everything is
    computed first, then written as one unit: on the trunk when every part is
    a small repair to a feature with nothing pending, else under one plan."""
    store, fid = p.store, raw["feature_id"]
    if fid not in store.blocks or not store.blocks[fid].live:
        return
    held = phase(store, fid) in HELD
    need_plan = held
    ingests: list[Ingest] = []
    adds: list[tuple[str, str, str]] = []    # (after, new bid, text)
    strikes: list[str] = []

    def consider(bid: str, new_md: str) -> None:
        # Computed as a trunk write first; a repair the trunk refuses (it would
        # drop a person's mark) or the gate demotes becomes part of the plan,
        # where the person decides and nothing is refused.
        nonlocal need_plan
        base = store.blocks[bid].text().live_text()
        ing = p.ws.plan_ingest(bid, "text", base_text=base, new_md=new_md, actor=LOOP, trunk=not need_plan)
        if ing.refused and ing.refused.startswith("would_drop_human_"):
            need_plan = True
            ing = p.ws.plan_ingest(bid, "text", base_text=base, new_md=new_md, actor=LOOP, trunk=False)
        if ing.refused:
            p.res.refused.append((bid, ing.refused))
            return
        if ing.unchanged:
            return
        gate = GATE_HUMAN if block_written_by(store, bid) == "human" else GATE_MACHINE
        if ing.deleted_fraction > gate:
            need_plan = True
        ingests.append(ing)

    if raw.get("title"):
        consider(fid, raw["title"])
    body_ids = {b.id for b in store.feature_body(fid)[1:] if b.kind == PARAGRAPH}
    last = fid
    for entry in raw.get("blocks") or []:
        bid, text = entry.get("id"), (entry.get("text") or "").strip()
        if bid:
            if bid not in body_ids:
                continue
            if entry.get("delete"):
                strikes.append(bid)
                need_plan = True
            else:
                consider(bid, text)
            last = bid
        elif text:
            nb = new_block_id()
            adds.append((last, nb, text))
            need_plan = True
            last = nb
    if not ingests and not adds and not strikes:
        return
    plan = p.open_plan(kind="edit", target=fid, note="the document has pending intent" if held else "a larger repair") \
        if need_plan else None
    batch, cause = plan or None, p.cause(fid)
    for ing in ingests:
        p.commit(ing, plan=plan, batch=batch, caused_by=cause)
    for after, nb, text in adds:
        p.submit([{"kind": K.BLOCK_ADD, "target": {"bid": nb}, "payload": {"kind": PARAGRAPH, "after": after}}],
                 plan=plan, batch=batch, caused_by=cause)
        ing = p.ws.plan_ingest(nb, "text", base_text="", new_md=text, actor=LOOP, trunk=plan is None)
        if ing.refused:
            p.res.refused.append((nb, ing.refused))
        else:
            p.commit(ing, plan=plan, batch=batch, caused_by=cause)
    if strikes:
        p.submit(_strike(store, strikes, plan), plan=plan, batch=batch, caused_by=cause)
