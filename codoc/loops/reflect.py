"""Code → document: the reflect pass.

Given a change set from the index, the pass:

1. maintains bindings deterministically: ``bind.refresh`` for modified bound
   chunks, ``bind.detach`` for removed ones, ``bind.attach`` for relocations
   (identical content moved, or a unique AST shape renamed);
2. opens a ``delete`` plan for every feature that lost its last binding in this pass
   (the loop never deletes; it proposes);
3. asks one LLM pass to place the added chunks and repair stale prose. Each
   edit in its answer is compiled by ``loops.edits`` and landed by ``_land``:

   | edit | lands |
   |---|---|
   | ``attach`` | on the trunk (attribution, not intent); under the node's plan when the node is new in this answer |
   | ``add_node``, ``move_node``, ``retire_node`` | as a plan |
   | ``amend`` | on the trunk when every part is a small rewording and the feature has nothing pending; otherwise as ONE plan for the whole amend, the loop's open plan on the feature when it has one |

4. covers what the model left unplaced: a chunk lands with the feature that
   owns most of its graph neighbours, else in a plan for a node named after
   its file (one per file).

When the pass runs because an agent session closed (``under_epoch``), every op
it writes on a feature with queued work carries ``caused_by`` the feature's
latest hand-off; when it runs over commits whose ``Codoc-Handoff`` trailer
names a hand-off (``landed``), the ops on that hand-off's feature do. That is
what lets the daemon mark the feature realized and lets History chain a change
back to the request. A pass triggered by a hand edit cites nothing, since
nobody implemented anything.

``propose`` is injectable: a callable ``(changes, subtree, all_titles, **kw) →
list[dict]`` of raw ops in the prompt's JSON shape. Tests inject a fake.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from codoc.calculus import ops as K
from codoc.calculus.derive import HELD, LOOP, open_plans_on, phase
from codoc.calculus.ingest import Ingest
from codoc.calculus.ops import Op, covered
from codoc.calculus.store import Store
from codoc.index.changes import ChangeSet, ChunkRef
from codoc.index.graph import Graph
from codoc.loops.context import impacted_features, owner_of, select_context
from codoc.calculus.ingest import strike_bodies
from codoc.loops.edits import PLAN_KIND, Change, Compiler, delete_open, title_from_file
from codoc.workspace import Workspace

MAX_ADDED_PER_CALL = 25


@dataclass
class ReflectResult:
    auto: Counter = field(default_factory=Counter)
    plans: list[str] = field(default_factory=list)
    llm_calls: int = 0
    refused: list[tuple[str, str]] = field(default_factory=list)   # (bid or kind, reason)
    impacted: list[str] = field(default_factory=list)
    caused: dict[str, str] = field(default_factory=dict)            # fid -> handoff op cited
    removed: set[str] = field(default_factory=set)                  # symbols removed from the code
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
        self.plan_of: dict[str, str] = {}      # a node this pass proposed -> its plan

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
            under_epoch: bool = False, landed: frozenset[str] = frozenset()) -> ReflectResult:
    store = ws.store
    res = ReflectResult()
    caused_by = {fid: did for fid, did in _causes(store).items() if under_epoch or did in landed}
    res.caused = dict(caused_by)
    res.removed = {r.symbol_path for r in cs.removed}
    p = _Pass(ws, res, caused_by)

    # 1. Deterministic binding maintenance.
    removed_owner: dict[str, str] = {}
    bodies: list[dict] = []
    for r in cs.removed:
        b = store.bindings.get(r.symbol_path)
        if b:
            removed_owner[r.symbol_path] = b.feature
            bodies.append(_bind(K.BIND_DETACH, b.feature, r.symbol_path, caused_by=p.cause(b.feature)))
    for c in cs.modified:
        b = store.bindings.get(c.symbol_path)
        if b and (b.fingerprint != c.fingerprint or b.types_hash != c.types_hash):
            bodies.append(_bind(K.BIND_REFRESH, b.feature, c.symbol_path, c.fingerprint, c.types_hash,
                                caused_by=p.cause(b.feature)))
    if bodies:
        for op in p.submit(bodies):
            if op.conflict is None:
                res.auto[op.kind.split(".")[1]] += 1
    relocated: set[str] = set()
    reloc_bodies = []
    for added, removed, _kind in _relocations(cs, removed_owner):
        owner = removed_owner[removed.symbol_path]
        reloc_bodies.append(_bind(K.BIND_ATTACH, owner, added.symbol_path, added.fingerprint,
                                  added.types_hash, caused_by=p.cause(owner)))
        relocated.add(added.symbol_path)
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
    added_unbound = [a for a in cs.added if a.symbol_path not in store.bindings and a.symbol_path not in relocated]
    modified_bound = [(c, store.bindings[c.symbol_path].feature) for c in cs.modified
                      if c.symbol_path in store.bindings]
    if not added_unbound and not modified_bound:
        return res
    changed_symbols = {c.symbol_path for c in cs.added + cs.modified + cs.removed}
    subtree, all_titles, graph_ctx = select_context(store, graph, cs.touched_files(), changed_symbols, mode="loop")
    impacted = impacted_features(store, graph, changed_symbols)
    res.impacted = sorted(impacted)
    changes: dict = {
        "added": [],
        "modified": [{"file": c.file, "symbol_path": c.symbol_path, "current_feature_id": fid,
                      "source": (c.source or "")[:600]} for c, fid in modified_bound],
        "removed": [{"file": r.file, "symbol_path": r.symbol_path,
                     "current_feature_id": removed_owner.get(r.symbol_path)} for r in cs.removed],
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

    # The model may bind only what this change set added or modified: those
    # chunks exist by construction, and untouched code is not its to reshuffle.
    hashes = {c.symbol_path: (c.fingerprint, c.types_hash) for c in cs.added + cs.modified}
    compiler = Compiler(store, hashes=hashes, bindable=set(hashes))
    covered: set[str] = set()
    for raw in raw_ops:
        change = compiler.compile(raw)
        if change.refused:
            res.refused.append((change.kind, change.refused))
            continue
        if change.kind == "retire_node" and not (allow_delete and phase(store, change.target) not in HELD):
            continue
        if _land(p, change):
            covered.update(change.bound)

    # 4. Coverage net for what the model left unplaced.
    homeless: dict[str, list[str]] = {}
    for a in added_unbound:
        if a.symbol_path in covered or a.symbol_path in store.bindings:
            continue
        home = graph.neighbor_feature(a.symbol_path, lambda s: owner_of(store, s))
        if home and store.blocks.get(home) and store.blocks[home].live:
            p.submit([_bind(K.BIND_ATTACH, home, a.symbol_path, a.fingerprint, a.types_hash,
                            caused_by=p.cause(home))])
            res.auto["net"] += 1
        else:
            homeless.setdefault(a.file, []).append(a.symbol_path)
    for file, symbols in homeless.items():
        _land(p, compiler.compile({"kind": "add_node", "title": title_from_file(file), "bindings": symbols,
                                   "rationale": "no feature covered this file"}))
    return res


# ── helpers ──────────────────────────────────────────────────────────────────

def _bind(kind: str, fid: str, symbol: str, fingerprint: str = "", types_hash: str = "",
          caused_by: str | None = None) -> dict:
    body = {"kind": kind, "target": {"fid": fid}, "payload": {"symbol": symbol}}
    if kind != K.BIND_DETACH:
        body["payload"].update({"fingerprint": fingerprint, "types_hash": types_hash})
    if caused_by:
        body["caused_by"] = caused_by
    return body


def _emptied(store: Store) -> list[str]:
    """Live features with no bindings that had some once, and no delete
    plan (of any verdict: a rejected one is an answer, not an invitation)."""
    bound = {b.feature for b in store.bindings.values()}
    proposed = {pl.target for pl in store.plans.values() if pl.kind == "delete"}
    # A symbol an OPEN plan borrowed is not a symbol the feature lost: a plan's
    # `bind.attach` applies live and displaces the prior owner (I8), and
    # rejecting it gives the symbol back. Asking whether to delete the owner
    # before the person has answered the proposal that emptied it is asking
    # them the same question twice, in the wrong order.
    for o in store.ops:
        if o.kind != K.BIND_ATTACH or o.conflict or not o.plan:
            continue
        pl = store.plans.get(o.plan)
        displaced = o.payload.get("displaced")
        if pl is not None and pl.status == "open" and displaced:
            bound.add(displaced["feature"])
    out = []
    for h in store.headings():
        if h.id in bound or h.id in proposed:
            continue
        # "Once had code" is any attach it ever held, not a detach: a feature
        # whose only symbol was re-attached elsewhere (I8 displacement) has no
        # detach of its own and is equally empty. The store remembers it, so a
        # snapshot does not make the tree forget.
        if h.id in store.bound_once:
            out.append(h.id)
    return out


def _causes(store: Store) -> dict[str, str]:
    """feature → its latest hand-off op id, for every hand-off not yet covered
    by a realization (whatever the person typed since; that ink is beyond the
    frontier and stays unsent)."""
    return {fid: w.handoffs[-1] for fid, w in store.watermarks_by_fid.items()
            if w.handoffs and not covered(w.handoff, w.realized)}


def _relocations(cs: ChangeSet, removed_owner: dict) -> list[tuple[ChunkRef, ChunkRef, str]]:
    removed = [r for r in cs.removed if r.symbol_path in removed_owner]
    used: set[str] = set()
    out = []
    by_tok: dict[str, list[ChunkRef]] = {}
    for r in removed:
        by_tok.setdefault(r.fingerprint, []).append(r)
    matched: set[str] = set()
    for a in cs.added:
        cands = [c for c in by_tok.get(a.fingerprint, []) if c.symbol_path not in used]
        if a.fingerprint and cands:
            c = cands[0]
            used.add(c.symbol_path)
            matched.add(a.symbol_path)
            out.append((a, c, "move"))
    # rename: a unique AST shape among everything that left the index (bound
    # or not) matched with a unique shape among what arrived
    shapes_removed_all = Counter(r.types_hash for r in cs.removed if r.types_hash)
    by_shape_removed: dict[str, list[ChunkRef]] = {}
    for r in removed:
        if r.symbol_path not in used and r.types_hash:
            by_shape_removed.setdefault(r.types_hash, []).append(r)
    by_shape_added: dict[str, list[ChunkRef]] = {}
    for a in cs.added:
        if a.symbol_path not in matched and a.types_hash:
            by_shape_added.setdefault(a.types_hash, []).append(a)
    for shape, adds in by_shape_added.items():
        rems = by_shape_removed.get(shape, [])
        if len(adds) == 1 and len(rems) == 1 and shapes_removed_all[shape] == 1:
            out.append((adds[0], rems[0], "rename"))
            used.add(rems[0].symbol_path)
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


# ── landing a compiled edit ──────────────────────────────────────────────────

def _land(p: _Pass, change: Change) -> bool:
    """Write one compiled edit (see the table in the module docstring).
    Returns whether anything was written."""
    fid, cause = change.target, p.cause(change.target)
    if change.kind in ("attach", "detach"):
        plan = p.plan_of.get(fid)
        ops = p.submit(change.bodies, plan=plan, batch=plan, caused_by=cause)
        landed = sum(1 for o in ops if o.conflict is None)
        p.res.auto[change.kind] += landed
        return landed > 0
    if change.kind == "amend":
        # The model read the loop's open plan here (the ``loop`` read mode),
        # so its repair adds to that plan rather than asking a second time.
        own = next((pid for pid in open_plans_on(p.store, fid) if p.store.plans[pid].actor == LOOP), None)
        done = p.ws.amend(fid, rewrites=[(r.bid, r.path, r.new_md, None) for r in change.rewrites],
                          bodies=change.bodies, strikes=change.strikes, actor=LOOP, note=change.note,
                          caused_by=cause, structural=change.structural, join=own)
        p.res.ops.extend(done.ops)
        p.res.refused.extend(done.refused)
        if done.plan and done.plan != own:
            p.res.plans.append(done.plan)
        return bool(done.ops)
    if change.kind == "retire_node" and delete_open(p.store, fid):
        return False          # already proposed for deletion in this pass or an earlier one
    pid = p.open_plan(kind=PLAN_KIND[change.kind], target=fid, note=change.note)
    if change.kind == "add_node":
        p.plan_of[fid] = pid
    p.submit(change.bodies + strike_bodies(p.store, change.strikes, pid), plan=pid, batch=pid, caused_by=cause)
    return True


def _delete_plan(p: _Pass, fid: str, note: str) -> str:
    pid = p.open_plan(kind="delete", target=fid, note=note)
    bodies = strike_bodies(p.store, [b.id for b in p.store.feature_body(fid)], pid)
    if bodies:
        p.submit(bodies, plan=pid, batch=pid)
    return pid
