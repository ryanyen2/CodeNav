"""Verdicts: ``plan.accept``, ``plan.reject`` and their undo, ``plan.reopen``
(docs/concurrency.md, Plans and verdicts).

A verdict undoes the plan ops it does not take, kills what the taken
``suggest_del`` marks list, applies the taken ``suggest_mark`` records, and
records each change as an ordered step. A reopen undoes the steps the store
recorded on the plan's current verdict, newest first. Neither reads steps from
a payload: a verdict computes them from its decision and the plan's ops each
time it applies, a replay included, so a log cannot carry an effect its
verdict would not have. A step names what it changed by identity and carries
what it replaced, and records the change as it took effect, so undoing it
needs the step alone. A proposal a verdict cannot apply asks for a state the
document is already in, which is not a reason to stop half way.

The handlers here are called by ``apply`` like any other and follow its
two-phase rule: everything they read is resolved before the first mutation.
"""
from __future__ import annotations

from codoc.calculus import ops as K
from codoc.calculus.doc import HEADING, Block, level_for
from codoc.calculus.ids import OpId
from codoc.calculus.ops import Conflict, Op, body
from codoc.calculus.store import Binding, Plan, Store
from codoc.calculus.text import SUGGEST_DEL, SUGGEST_MARK, Mark, MarkError, struck_chars


def _plan_ops(store: Store, plan: Plan) -> list[Op]:
    return [store.by_id[i] for i in plan.ops if i in store.by_id and store.by_id[i].conflict is None]


def _closure(plan_ops: list[Op], subset: list[OpId]) -> list[OpId]:
    """The accepted ops plus every plan op that minted an anchor they use or
    the block they live in."""
    by_id = {o.id: o for o in plan_ops}
    creators: dict[str, OpId] = {}
    for o in plan_ops:
        if o.kind == K.BLOCK_ADD:
            creators[o.target["bid"]] = o.id
        elif o.kind == K.BLOCK_SPLIT:
            creators[o.payload.get("new", "")] = o.id
    want = {i for i in subset if i in by_id}
    changed = True
    while changed:
        changed = False
        for i in list(want):
            o = by_id[i]
            deps = [v[0] for key in K.ANCHOR_KEYS
                    if isinstance(v := o.payload.get(key), list) and len(v) == 2 and v[0] in by_id]
            dep = creators.get(o.payload.get("bid") or o.target.get("bid") or o.target.get("fid") or "")
            if dep:
                deps.append(dep)
            for d in deps:
                if d not in want:
                    want.add(d)
                    changed = True
    return [o.id for o in plan_ops if o.id in want]


# ── steps ────────────────────────────────────────────────────────────────────
#
# The step vocabulary, what each step did and how it is undone, is the table in
# docs/concurrency.md (Plans and verdicts, Steps).

_SHAPE = ("kind", "level", "rank", "ranked_by")      # block attributes a `set` step changes; any other key is a content field


def _seglist(v) -> list[tuple[str, int, int]]:
    return [(str(o), int(k), int(n)) for o, k, n in v]


def _mark_step(block: Block, path: str, m: Mark, on: bool) -> dict:
    """A ``mark`` step: the mark, and the characters it covers here. Undoing
    it finds the mark by those characters, so a split, a merge or a move since
    does not hide it: removed, every piece of it goes; put back, it is placed
    in each field that holds any of them."""
    return {"do": "mark", "bid": block.id, "path": path, "mark": m.to_json(), "on": on,
            "segments": [list(x) for x in block.text(path).segments_between(m.start, m.end)]}


def _run(store: Store, me: Op, steps, touched: set[str], undo_of: str | None = None) -> list[dict]:
    """Do ``steps`` oldest first, or, with ``undo_of`` (the verdict they
    belong to), undo them newest first. Returns them as they took effect, in
    the list's own order, without the ones that changed nothing."""
    order = list(reversed(steps)) if undo_of else list(steps)
    done = [_step(store, me, s, touched, undo_of) for s in order]
    if undo_of:
        done.reverse()
    return [x for d in done for x in d]


def _step(store: Store, me: Op, s: dict, touched: set[str], undo_of: str | None) -> list[dict]:
    fwd = undo_of is None
    do = s["do"]
    if do in ("kill", "revive"):
        dies = (do == "kill") == fwd
        if s.get("by"):
            by, actor, at = str(s["by"]), s.get("actor", ""), s.get("at", "")
        else:                   # the op that recorded the step: this one, or the verdict being undone
            by, actor, at = (me.id, me.actor, me.at) if fwd else (undo_of, "", "")
        segs = _seglist(s["segments"])
        out = []
        for block, p in _holders_of(store, segs):
            t = block.text(p)
            got = t.kill(segs, by=by, by_actor=actor, by_at=at) if dies else t.revive(segs, by=by)
            if got:
                out.append({**s, "bid": block.id, "path": p, "segments": [list(g) for g in got]})
                touched.add(block.id)
        return out
    if do == "mark":
        mk, segs = Mark.from_json(s["mark"]), _seglist(s["segments"])
        named = lambda ref: any(o == ref[0] and k <= ref[1] < k + n for o, k, n in segs)
        out = []
        for block, p in _holders_of(store, segs):
            t = block.text(p)
            if bool(s["on"]) == fwd:
                ends = t.bounds(segs)
                got = [x for x in ([mk.piece(*ends)] if ends else []) if x not in t.marks]
                t.marks.extend(got)
            else:
                got = [x for x in t.marks if x.piece(mk.start, mk.end) == mk and named(x.start)]
                t.marks = [x for x in t.marks if x not in got]
            if got:
                store.index_text(block.id, p, t)
                touched.add(block.id)
                out += [{**s, "bid": block.id, "path": p, "mark": x.to_json()} for x in got]
        return out
    if do == "set":
        b = store.blocks[s["bid"]]
        frm, to = dict(s.get("from") or {}), dict(s.get("to") or {})
        want, keys = (to if fwd else frm), list(dict.fromkeys([*frm, *to]))
        if not fwd and "rank" in to and b.ranked_by != to["ranked_by"]:
            return []           # ranked since the verdict: that move stands, as it does against a reject
        was = _read(b, keys)
        for k in keys:
            if k == "rank":
                store.set_rank(b.id, want[k], want["ranked_by"])
            elif k == "ranked_by":
                continue        # set with the rank
            elif k in _SHAPE:
                setattr(b, k, want[k])
            elif k in want:
                b.content[k] = want[k]
            else:
                b.content.pop(k, None)
        if "kind" in keys or "level" in keys:
            b.level = level_for(b.kind, b.level)     # a paragraph has no level, as block.set gives it
        now = _read(b, keys)
        if now == was:
            return []
        touched.add(b.id)
        out = {k: v for k, v in s.items() if k not in ("detached", "attached")}
        out.update({"from": was, "to": now} if fwd else {"from": now, "to": was})
        if "kind" in keys:
            # A feature leaves or comes back as `block.set` has it. Undoing a
            # plan's op takes back the symbols its record names (`bindings`); a
            # reopen, the ones this step recorded as `detached`. What each was
            # bound with comes from the block.
            named = s.get("bindings") if fwd else s.get("detached")
            detached, attached = store.refeature(b, was["kind"], [e["symbol"] for e in named or []])
            out.update({k: v for k, v in (("detached", detached), ("attached", attached)) if v})
        return [out]
    if do == "void":
        b, move = store.blocks[s["bid"]], str(s["op"])
        if fwd == (move in b.void):
            return []
        if fwd:
            b.void[move] = list(s["to"])
        else:
            del b.void[move]
        touched.add(b.id)
        return [dict(s)]
    if do == "kill_block":
        b = store.blocks[s["bid"]]
        if fwd and b.live:
            feature = str(s.get("in") or "")
            b.kill(me.id, me.actor, me.at, feature)
            if feature:
                store.removed.setdefault(feature, set()).add(b.id)
        elif not fwd and b.dead == undo_of:
            b.revive()
        else:
            return []
        touched.add(b.id)
        return [dict(s)]
    if do == "bind":
        key = str(s["symbol"])
        want = s.get("to") if fwd else s.get("from")
        owner = store.blocks.get(want["feature"]) if want else None
        if want and (owner is None or not owner.live or owner.kind != HEADING):
            # Only a live heading owns code: a reject leaves the symbol unbound,
            # and a reopen leaves it where it is.
            if not fwd:
                return []
            want = None
        cur = store.bindings.get(key)
        if want:
            store.bindings[key] = Binding(want["feature"], key, want.get("fingerprint", ""), want.get("types_hash", ""),
                                          want.get("set_by", ""))
            touched.add(want["feature"])
        else:
            store.bindings.pop(key, None)
        if cur is not None:
            touched.add(cur.feature)
        was = cur.to_json() if cur else None
        return [{**s, "from": was, "to": want} if fwd else {**s, "from": want, "to": was}]
    # detach, the one kind left: only this module writes steps
    if fwd:
        got = store.detach_dead([s["fid"]])
        if not got:
            return []
        touched.add(s["fid"])
        return [{"do": "detach", "fid": s["fid"], "bindings": got}]
    touched.update(store.reattach(s.get("bindings") or []))
    return [dict(s)]


def _read(b: Block, keys) -> dict:
    return {k: (getattr(b, k) if k in _SHAPE else b.content[k]) for k in keys if k in _SHAPE or k in b.content}


def _holders_of(store: Store, segs) -> list[tuple[Block, str]]:
    """Every field holding characters of the ops these segments name."""
    seen: dict[tuple[str, str], tuple[Block, str]] = {}
    for o in dict.fromkeys(s[0] for s in segs):
        for b, p in store.holders(o):
            seen.setdefault((b.id, p), (b, p))
    return list(seen.values())


def _undo_steps(store: Store, o: Op) -> list[dict]:
    """What undoes one plan op, read from the op and the store as they are
    now. An insert's characters die. A delete's come back and a restore's go
    back to dead by the delete it undid, so a plan that typed, took back and
    gave back the same words is undone one op at a time. A mark record is
    dropped. A block the plan created dies if it is now empty. A set goes back
    to the previous value, absent included. A move is put back while it is
    still the op that ranked the block; otherwise the later move stands and
    this one's place is void. A binding goes back to the owner it displaced
    unless someone re-bound it since (their attach stands)."""
    p, t = o.payload, o.target
    if o.kind == K.TEXT_INSERT:
        return [{"do": "kill", "bid": p["bid"], "path": p["path"], "segments": [[o.id, 0, len(p["text"])]]}]
    if o.kind == K.TEXT_DELETE:
        return [{"do": "revive", "bid": p["bid"], "path": p["path"], "segments": p["segments"],
                 "by": o.id, "actor": o.actor, "at": o.at}]
    if o.kind == K.TEXT_RESTORE:
        undone = store.by_id.get(str(p.get("by") or ""))
        return [{"do": "kill", "bid": p["bid"], "path": p["path"], "segments": p["segments"], "by": p.get("by"),
                 "actor": undone.actor if undone else "", "at": undone.at if undone else ""}]
    if o.kind == K.TEXT_MARK:
        # A plan's mark toggle never touched the trunk: it is a record beside
        # it, found by the op that placed it, so a merge, a split or another
        # plan moving the trunk's marks cannot hide it.
        return [_mark_step(b, f, m, False) for b, f, m in store.placed_by(o.id) if m in b.text(f).proposals(o.plan)]
    if o.kind in (K.BLOCK_ADD, K.BLOCK_SPLIT):
        b = store.blocks.get(t["bid"] if o.kind == K.BLOCK_ADD else p["new"])
        if b is not None and b.live and all(b.content[f].live_len() == 0 for f in b.layout.text):
            return [{"do": "kill_block", "bid": b.id}]
        return []
    if o.kind == K.BLOCK_SET:
        b, path = store.blocks.get(t["bid"]), t["path"]
        if b is None or not b.live:
            return []
        if path == "kind":
            to = {"kind": p["prev"], "level": p["prev_level"]}
        elif path == "level":
            to = {"level": p["prev"]}
        else:
            to = {} if o.inverse[0]["payload"].get("unset") else {path: p["prev"]}
        step = {"do": "set", "bid": b.id, "from": _read(b, list(to) or [path]), "to": to}
        if p.get("detached"):
            step["bindings"] = p["detached"]      # a heading the plan made prose takes its code back
        return [step]
    if o.kind == K.BLOCK_MOVE:
        out = []
        for bid, *was in p.get("prev", []):
            b = store.blocks[bid]
            if b.ranked_by != o.id:
                out.append({"do": "void", "bid": bid, "op": o.id, "to": was})
                continue
            rank, level, by = b.unvoid(*was)
            to = {"rank": rank, "ranked_by": by, **({"level": level} if b.kind == HEADING else {})}
            out.append({"do": "set", "bid": bid, "from": _read(b, list(to)), "to": to})
        return out
    if o.kind == K.BIND_ATTACH:
        cur = store.bindings.get(p["symbol"])
        if cur is None or cur.feature != t["fid"]:
            return []
        # A plan that only refreshed a binding the feature already had gives the old hashes back.
        to = {**cur.to_json(), "fingerprint": p.get("prev_fingerprint", ""), "types_hash": p.get("prev_types_hash", ""),
              "set_by": p.get("prev_set_by", "")} if "prev_fingerprint" in p else p.get("displaced")
        return [{"do": "bind", "symbol": p["symbol"], "from": cur.to_json(), "to": to}]
    return []


def _proposed_marks(store: Store, pid: str, accepted: set[str]) -> list[tuple[Block, str, Mark]]:
    """This plan's ``suggest_mark`` records, in the order they were placed. A
    subset accept takes only the ones whose op it took."""
    out: list[tuple[Block, str, Mark, int]] = []
    for block in store.blocks.values():
        for p in block.layout.text:
            for m in block.text(p).proposals(pid, SUGGEST_MARK):
                if m.op in accepted:
                    out.append((block, p, m, store.position.get(m.op, 0)))
    out.sort(key=lambda t: t[3])
    return [(b, p, m) for b, p, m, _ in out]


def _apply_proposals(store: Store, pid: str, take: set[str], touched: set[str]) -> list[dict]:
    """The accepted mark proposals become trunk marks now, under the actor who
    proposed them and the op that did. This is the only moment a plan's mark
    toggle touches the trunk, which is why nothing has to find it again later."""
    steps: list[dict] = []
    for block, p, m in _proposed_marks(store, pid, take):
        t = block.text(p)
        want = {k[2:]: v for k, v in m.attrs.items() if k.startswith("a:")}
        try:
            removed, added = t.set_mark(m.attrs.get("mark", ""), want, m.start, m.end, bool(m.attrs.get("on")),
                                        actor=m.actor, op=m.op, at=m.at)
        except MarkError:
            # Turning off a mark removed whole that the person already removed
            # asks for a state the document is in. Agreeing costs nothing;
            # refusing would leave the plan open and every later accept
            # failing the same way.
            removed, added = [], []
        t.marks.remove(m)
        store.index_text(block.id, p, t)
        touched.add(block.id)
        steps += [_mark_step(block, p, x, on)
                  for x, on in [*((x, False) for x in removed), *((x, True) for x in added), (m, False)]]
    return steps


def verdict(store: Store, op: Op, touched: set[str], accept: bool):
    """``plan.accept`` and ``plan.reject``: the decision is ``subset`` (an
    accept of part of the plan) and nothing else in the payload is read. What
    the verdict does is computed from the decision and the plan's ops as they
    are now, so a redo is the verdict again, judged against the plan as it
    has become since the reopen."""
    pid = op.target["pid"]
    plan = store.plans.get(pid)
    if plan is None:
        raise Conflict("no_plan", pid)
    if plan.status != "open":
        raise Conflict("plan_not_open", pid)
    p = op.payload
    every = _plan_ops(store, plan)
    _touch_plan(store, plan, every, touched)
    if "verdict" in p:
        # A snapshot restoring a verdict it folded: the blocks it restored
        # already hold what the verdict did, and the plan keeps its verdict op.
        _decide(store, plan, op, accept, str(p["verdict"]))
        return {"verdict": plan.verdict}, [body(K.PLAN_REOPEN, {"pid": pid}, {"by": plan.verdict})]
    subset = p.get("subset") if accept else None
    if not accept:
        take: set[str] = set()
    elif subset is None:
        take = {o.id for o in every}
    else:
        take = set(_closure(every, [str(s) for s in subset]))
    if accept and plan.kind == "delete" and plan.target and plan.target not in store.blocks:
        raise Conflict("no_feature", plan.target)
    strike = [(b, f, m) for o in every if o.id in take and o.kind == K.TEXT_MARK
              and o.payload.get("kind") == SUGGEST_DEL and o.payload.get("on", True)
              for b, f, m in store.placed_by(o.id)]
    steps: list[dict] = []
    for o in reversed(every):
        if o.id not in take:
            steps += _run(store, op, _undo_steps(store, o), touched)
    for b, f, m in strike:
        if m in b.text(f).marks:
            steps += _run(store, op, [{"do": "kill", "bid": b.id, "path": f, "segments": [list(c) for c in struck_chars(m)]},
                                      _mark_step(b, f, m, False)], touched)
    if accept:
        steps += _apply_proposals(store, plan.id, take, touched)
        # A feature the person deleted by hand before answering the proposal
        # is already gone: the verdict agrees with them rather than conflicting.
        if plan.kind == "delete" and plan.target and store.blocks[plan.target].live:
            steps += _run(store, op, [{"do": "kill_block", "bid": b.id, "in": plan.target} for b in store.body(plan.target)]
                          + [{"do": "detach", "fid": plan.target}], touched)
    _decide(store, plan, op, accept, op.id)
    record: dict = {"rejected": [o.id for o in every if o.id not in take], "steps": steps}
    if accept:
        record = {"subset": subset, "accepted": [o.id for o in every if o.id in take], **record}
    return record, [body(K.PLAN_REOPEN, {"pid": pid}, {"by": op.id})]


def _decide(store: Store, plan: Plan, op: Op, accept: bool, verdict_op: str) -> None:
    plan.status, plan.decided_by, plan.verdict = ("accepted" if accept else "rejected"), op.actor, verdict_op
    store.open_plans.pop(plan.id, None)


def reopen(store: Store, op: Op, touched: set[str]):
    """The inverse of a verdict: undo the steps the store recorded for the
    plan's current verdict, newest first. Nothing but ``by`` is read from the
    payload. Its own inverse is that verdict's decision again."""
    pid = op.target["pid"]
    plan = store.plans.get(pid)
    if plan is None:
        raise Conflict("no_plan", pid)
    if plan.status == "open":
        raise Conflict("plan_open", pid)
    by = str(op.payload["by"])
    if by != plan.verdict:
        raise Conflict("not_current_verdict", by)
    decided = store.by_id.get(by)
    if decided is None or decided.kind not in (K.PLAN_ACCEPT, K.PLAN_REJECT) or decided.target.get("pid") != pid:
        # A fold archived it, and with it the plan's ops, which any verdict
        # after this reopen, its redo included, would have to judge.
        raise Conflict("verdict_folded", by)
    undone = _run(store, op, decided.payload["steps"], touched, undo_of=by)
    was = plan.status
    plan.status, plan.decided_by, plan.verdict = "open", "", ""
    store.open_plans[pid] = plan
    _touch_plan(store, plan, _plan_ops(store, plan), touched)
    subset = decided.payload.get("subset")
    redo = body(decided.kind, {"pid": pid}, {} if subset is None else {"subset": subset})
    return {"from": was, "by": by, "steps": undone}, [redo]


def _touch_plan(store: Store, plan: Plan, pops: list[Op], touched: set[str]) -> None:
    """A verdict is history of the plan's target and of every block its ops
    landed in, whether or not it changes them."""
    if plan.target and plan.target in store.blocks:
        touched.add(plan.target)
    for o in pops:
        for key in ("bid", "into", "fid"):
            b = o.payload.get(key) if key == "bid" else o.target.get(key)
            if b in store.blocks:
                touched.add(b)
        nb = o.payload.get("new") if o.kind == K.BLOCK_SPLIT else None
        if nb in store.blocks:
            touched.add(nb)
