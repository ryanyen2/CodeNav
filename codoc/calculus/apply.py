"""``apply(store, op)`` — total, deterministic, O(touched blocks) (spec §5).

Every op is recorded: applied, or stored with ``conflict`` set and the state
untouched. Handlers are two-phase: they RESOLVE every anchor, block and mark
they will touch before the first mutation, so a conflict discovered midway
never leaves a partial change behind. On success the op leaves with its
canonical payload (what it actually did) and its inverse (op bodies that undo
it). The inverse of an inverse is the original, which is what makes undo a
new op and the timeline an un-apply through the same code.

Conflict strings are stable identifiers, not prose; the UI maps them.
"""
from __future__ import annotations

from dataclasses import replace

from codoc.calculus import ops as K
from codoc.calculus.doc import HEADING, PARAGRAPH, Block, clamp_level, empty_content, is_text_field, text_fields
from codoc.calculus.ids import END, START, CharRef, OpId, ref_from_json, ref_to_json
from codoc.calculus.ops import Op, OpBody, body, frontier_union
from codoc.calculus.store import Binding, Plan, Store
from codoc.calculus.text import SUGGEST_DEL, AnchorError, Mark, MarkError, Text, nfc

RESERVED_PATHS = frozenset({"id", "rank", "dead"})


class Conflict(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code


def _ref(v) -> CharRef | str:
    try:
        return ref_from_json(v)
    except ValueError:
        raise Conflict("bad_anchor", repr(v))


def _char(v) -> CharRef:
    r = _ref(v)
    if isinstance(r, str):
        raise Conflict("bad_anchor", "a character is required here")
    return r


def apply(store: Store, op: Op) -> Op:
    """Apply ``op`` to ``store``. Returns the op as recorded. An id already
    applied returns the stored record and changes nothing."""
    if op.id in store.by_id:
        return store.by_id[op.id]
    touched: set[str] = set()
    structural = op.kind in K.STRUCTURAL
    try:
        if op.kind not in K.KINDS:
            raise Conflict("unknown_kind", op.kind)
        if store.policy is not None:
            denial = store.policy(op)
            if denial:
                raise Conflict("authority", denial)
        if op.plan:
            plan = store.plans.get(op.plan)
            if plan is None or plan.status != "open":
                raise Conflict("plan_not_open", op.plan)
            if op.kind not in K.PLANNABLE:
                raise Conflict("not_plannable", op.kind)
        canonical, inverse = _HANDLERS[op.kind](store, op, touched)
        if canonical.pop("_structural", False):
            structural = True
        op.payload, op.inverse, op.conflict = canonical, inverse, None
        if op.plan:
            store.plans[op.plan].ops.append(op.id)
    except Conflict as e:
        op.conflict, op.inverse, touched = e.code, [], set()
    except AnchorError:
        op.conflict, op.inverse, touched = "unknown_anchor", [], set()
    except MarkError:
        op.conflict, op.inverse, touched = "mark_partial", [], set()
    except KeyError as e:
        op.conflict, op.inverse, touched = f"missing:{e.args[0] if e.args else ''}", [], set()
    except (ValueError, TypeError):
        op.conflict, op.inverse, touched = "malformed", [], set()
    store.record(op, touched, structural and op.conflict is None)
    return op


# ── text ─────────────────────────────────────────────────────────────────────

def _live_text_field(store: Store, bid: str, path: str) -> tuple[Block, Text]:
    b = store.blocks.get(bid)
    if b is None:
        raise Conflict("no_block", bid)
    if not b.live:
        raise Conflict("dead_block", bid)
    if not is_text_field(b.kind, path):
        raise Conflict("not_text", f"{b.kind}.{path}")
    return b, b.text(path)


def _h_text_insert(store: Store, op: Op, touched: set[str]):
    bid, path = op.target["bid"], op.target.get("path", "text")
    after = _ref(op.payload["after"])
    chars = nfc(op.payload["text"])
    if not chars:
        raise Conflict("empty_insert")
    if after == START:
        block, text = _live_text_field(store, bid, path)
    elif after == END:
        raise Conflict("bad_anchor", "insert after END; use the last character")
    else:
        block, path = store.locate_char(after, (bid, path))  # type: ignore[arg-type]
        if not block.live:
            raise Conflict("dead_block", block.id)
        text = block.text(path)
    seg = text.insert(after, chars, op=op.id, actor=op.actor, at=op.at, plan=op.plan)
    store.index_text(block.id, path, text)
    touched.add(block.id)
    canonical = {"after": ref_to_json(after), "text": chars, "bid": block.id, "path": path}
    return canonical, [body(K.TEXT_DELETE, {"bid": block.id, "path": path}, {"segments": [[seg.op, seg.k0, seg.len]]})]


def _segment_holders(store: Store, segs: list[tuple[str, int, int]], hint: tuple[str, str],
                     live_only: bool) -> list[tuple[Block, str]]:
    """The holders of the named characters: both ends of every segment are
    located by identity (a segment split across two blocks has two holders).
    Other blocks holding OTHER characters of the same op are not touched."""
    work: list[tuple[Block, str]] = []
    for o, k, n in segs:
        for ref in ((o, k), (o, k + n - 1)):
            h = store.locate_char(ref, hint)
            if live_only and not h[0].live:
                raise Conflict("dead_block", h[0].id)
            if h not in work:
                work.append(h)
    return work


def _h_text_delete(store: Store, op: Op, touched: set[str]):
    bid, path = op.target["bid"], op.target.get("path", "text")
    if "segments" in op.payload:
        segs = [(str(o), int(k), int(n)) for o, k, n in op.payload["segments"]]
        work = _segment_holders(store, segs, (bid, path), live_only=True)
        killed: list[list] = []
        for block, p in work:
            killed.extend([list(k) for k in block.text(p).kill(segs, by=op.id)])
            touched.add(block.id)
        canonical = {"segments": killed, "bid": bid, "path": path}
        return canonical, [body(K.TEXT_RESTORE, {"bid": bid, "path": path}, {"segments": killed, "by": op.id})]
    start, end = _char(op.payload["from"]), _char(op.payload["to"])
    block, p = store.locate_char(start, (bid, path))
    if not block.live:
        raise Conflict("dead_block", block.id)
    text = block.text(p)
    if not text.holds(end):
        raise Conflict("range_spans_blocks")
    killed = [list(k) for k in text.delete(start, end, by=op.id)]
    touched.add(block.id)
    canonical = {"segments": killed, "bid": block.id, "path": p}
    return canonical, [body(K.TEXT_RESTORE, {"bid": block.id, "path": p}, {"segments": killed, "by": op.id})]


def _h_text_restore(store: Store, op: Op, touched: set[str]):
    bid, path = op.target["bid"], op.target.get("path", "text")
    by = op.payload.get("by")
    segs = [(str(o), int(k), int(n)) for o, k, n in op.payload["segments"]]
    work = _segment_holders(store, segs, (bid, path), live_only=False)
    revived: list[list] = []
    for block, p in work:
        revived.extend([list(k) for k in block.text(p).revive(segs, by=by)])
        touched.add(block.id)
    canonical = {"segments": revived, "bid": bid, "path": path}
    if by:
        canonical["by"] = by
    return canonical, [body(K.TEXT_DELETE, {"bid": bid, "path": path}, {"segments": revived})]


def _h_text_mark(store: Store, op: Op, touched: set[str]):
    bid, path = op.target["bid"], op.target.get("path", "text")
    kind = op.payload["kind"]
    attrs = dict(op.payload.get("attrs") or {})
    start, end = _char(op.payload["start"]), _char(op.payload["end"])
    on = bool(op.payload.get("on", True))
    block, p = store.locate_char(start, (bid, path))
    if not block.live:
        raise Conflict("dead_block", block.id)
    text = block.text(p)
    if not text.holds(end):
        raise Conflict("range_spans_blocks")
    if kind == SUGGEST_DEL and on:
        if not op.plan:
            raise Conflict("suggest_del_needs_plan")
        attrs["plan"] = op.plan
    # A restored mark keeps its original placer and actor: only an inverse says
    # so, and an inverse is the person's own op.
    actor = op.payload.get("actor") or op.actor
    placer = op.payload.get("op") or op.id
    removed, added = text.set_mark(kind, attrs, start, end, on, actor=actor, op=placer)
    store.index_text(block.id, p, text)
    touched.add(block.id)
    canonical = {"kind": kind, "attrs": attrs, "start": ref_to_json(start), "end": ref_to_json(end), "on": on,
                 "bid": block.id, "path": p, "removed": [m.to_json() for m in removed],
                 "added": [m.to_json() for m in added]}
    inverse: list[OpBody] = []
    for m in added:
        inverse.append(body(K.TEXT_MARK, {"bid": block.id, "path": p},
                            {"kind": m.kind, "attrs": m.attrs, "start": list(m.start), "end": list(m.end), "on": False}))
    for m in removed:
        inverse.append(body(K.TEXT_MARK, {"bid": block.id, "path": p},
                            {"kind": m.kind, "attrs": m.attrs, "start": list(m.start), "end": list(m.end),
                             "on": True, "actor": m.actor, "op": m.op}))
    return canonical, inverse


# ── blocks ───────────────────────────────────────────────────────────────────

def _after(store: Store, after) -> str | None:
    if after == START or after is None:
        return None
    if after not in store.blocks:
        raise Conflict("no_block", str(after))
    return after


def _h_block_add(store: Store, op: Op, touched: set[str]):
    bid = op.target["bid"]
    if bid in store.blocks:
        raise Conflict("duplicate_block", bid)
    kind = op.payload["kind"]
    level = clamp_level(op.payload.get("level", 1)) if kind == HEADING else 0
    after = op.payload.get("after", START)
    anchor = _after(store, after)
    content = empty_content(kind, op.payload.get("content"))
    for f, tj in (op.payload.get("texts") or {}).items():
        if f in text_fields(kind):
            content[f] = Text.from_json(tj)
    rank = op.payload.get("rank") or store.rank_after(anchor)
    block = Block(bid, kind, rank, level, content)
    store.add_block(block)
    for f in text_fields(kind):
        store.index_text(bid, f, content[f])
    touched.add(bid)
    canonical = {"kind": kind, "level": level, "after": after, "rank": block.rank,
                 "content": {k: v for k, v in content.items() if not isinstance(v, Text)}}
    if op.payload.get("texts"):
        canonical["texts"] = op.payload["texts"]
    return canonical, [body(K.BLOCK_DELETE, {"bid": bid}, {"ids": [bid]})]


def _h_block_set(store: Store, op: Op, touched: set[str]):
    bid, path = op.target["bid"], op.target["path"]
    b = store.blocks.get(bid)
    if b is None:
        raise Conflict("no_block", bid)
    if not b.live:
        raise Conflict("dead_block", bid)
    if path in RESERVED_PATHS:
        raise Conflict("reserved_path", path)
    value = op.payload["value"]
    canonical: dict = {"value": value}
    if path == "kind":
        if set(text_fields(value)) != set(text_fields(b.kind)):
            raise Conflict("kind_incompatible", f"{b.kind}->{value}")
        prev_kind, prev_level = b.kind, b.level
        b.kind = value
        b.level = clamp_level(op.payload.get("level", prev_level or 1)) if value == HEADING else 0
        canonical.update({"prev": prev_kind, "prev_level": prev_level, "level": b.level, "_structural": True})
        inverse = [body(K.BLOCK_SET, {"bid": bid, "path": "kind"}, {"value": prev_kind, "level": prev_level})]
    elif path == "level":
        if b.kind != HEADING:
            raise Conflict("not_heading", bid)
        prev = b.level
        b.level = clamp_level(value)
        canonical.update({"value": b.level, "prev": prev, "_structural": True})
        inverse = [body(K.BLOCK_SET, {"bid": bid, "path": "level"}, {"value": prev})]
    else:
        if is_text_field(b.kind, path):
            raise Conflict("text_field", f"use text ops for {path}")
        prev = b.content.get(path)
        b.content[path] = value
        canonical["prev"] = prev
        inverse = [body(K.BLOCK_SET, {"bid": bid, "path": path}, {"value": prev})]
    touched.add(bid)
    return canonical, inverse


def _h_block_split(store: Store, op: Op, touched: set[str]):
    bid, path = op.target["bid"], op.target.get("path", "text")
    new = op.payload["new"]
    at = _ref(op.payload["at"])
    forced = op.payload.get("new_takes")           # set only by a merge's inverse
    if at == START:
        b, text = _live_text_field(store, bid, path)
    elif at == END:
        raise Conflict("bad_anchor", "split at END; use the last character")
    else:
        b, path = store.locate_char(at, (bid, path))  # type: ignore[arg-type]
        if not b.live:
            raise Conflict("dead_block", b.id)
        text = b.text(path)
    existing = store.blocks.get(new)
    if existing is not None and (existing.live or not forced):
        raise Conflict("duplicate_block", new)
    tail = text.split(at)
    head_live, tail_live = text.live_len() > 0, tail.live_len() > 0
    new_first = (forced == "head") if forced else ((not head_live) and tail_live)
    new_text, keep_text = (text, tail) if new_first else (tail, text)
    b.content[path] = keep_text
    new_kind = PARAGRAPH if b.kind == HEADING else b.kind
    rank = op.payload.get("rank") or (store.rank_before(b.id) if new_first else store.rank_after(b.id))
    if existing is not None:
        existing.dead, existing.kind, existing.level = None, new_kind, 0
        existing.content[path] = new_text
        store.set_rank(new, rank)
        nb = existing
    else:
        nb = Block(new, new_kind, rank, 0, empty_content(new_kind))
        nb.content[path] = new_text
        store.add_block(nb)
    store.index_text(b.id, path, keep_text)
    store.index_text(new, path, new_text)
    store.prune_index(b.id, path, keep_text)
    touched.update({b.id, new})
    canonical = {"at": ref_to_json(at), "new": new, "new_first": new_first, "rank": nb.rank, "bid": b.id, "path": path}
    if forced:
        canonical["new_takes"] = forced
    inv_payload = {"rejoin": True} if b.kind == HEADING else {}
    return canonical, [body(K.BLOCK_MERGE, {"into": b.id, "bid": new, "path": path}, inv_payload)]


def _h_block_merge(store: Store, op: Op, touched: set[str]):
    into, bid = op.target["into"], op.target["bid"]
    path = op.target.get("path", "text")
    a, b = store.blocks.get(into), store.blocks.get(bid)
    if a is None or b is None:
        raise Conflict("no_block", into if a is None else bid)
    if not (a.live and b.live):
        raise Conflict("dead_block")
    # A heading never dies by merge (that would destroy a feature by the back
    # door), and prose never merges INTO a heading except to undo a heading
    # split, which is the one merge that says so.
    if b.kind == HEADING or (a.kind == HEADING and not op.payload.get("rejoin")):
        raise Conflict("merge_heading")
    if set(text_fields(a.kind)) != set(text_fields(b.kind)):
        raise Conflict("kind_incompatible")
    prev, nxt = store.live_neighbours_of(into)
    if bid not in (prev, nxt):
        raise Conflict("not_adjacent")
    before = bid == prev
    ta, tb = a.text(path), b.text(path)
    if before:
        joined = Text(tb.segs, tb.marks)
        seam = joined.merge(ta)
        a.content[path] = joined
        b.content[path] = Text()
    else:
        seam = ta.merge(tb)
    b.dead = op.id
    store.index_text(into, path, a.text(path))
    store.prune_index(bid, path, b.text(path))
    touched.update({into, bid})
    canonical = {"at": ref_to_json(seam), "order": "before" if before else "after", "path": path, "rank": b.rank}
    if op.payload.get("rejoin"):
        canonical["rejoin"] = True
    inverse = [body(K.BLOCK_SPLIT, {"bid": into, "path": path},
                    {"at": ref_to_json(seam if seam != END else START), "new": bid, "rank": b.rank,
                     "new_takes": "head" if before else "tail"})]
    return canonical, inverse


def _h_block_move(store: Store, op: Op, touched: set[str]):
    if "restore" in op.payload:
        rows = [(str(i), str(r), int(l)) for i, r, l in op.payload["restore"]]
        for i, _, _ in rows:
            if i not in store.blocks:
                raise Conflict("no_block", i)
        prev = [[i, store.blocks[i].rank, store.blocks[i].level] for i, _, _ in rows]
        for i, r, l in rows:
            b = store.blocks[i]
            store.set_rank(i, r)
            if b.kind == HEADING:
                b.level = clamp_level(l)
            touched.add(i)
        return {"restore": [list(x) for x in rows]}, [body(K.BLOCK_MOVE, dict(op.target), {"restore": prev})]
    bid = op.target["bid"]
    after = op.payload.get("after", START)
    delta = int(op.payload.get("level_delta", 0))
    b = store.blocks.get(bid)
    if b is None or not b.live:
        raise Conflict("no_block", bid)
    ids = store.dominated_ids(bid)
    anchor = _after(store, after)
    if anchor in ids:
        raise Conflict("bad_target", str(after))
    prev = [[i, store.blocks[i].rank, store.blocks[i].level] for i in ids]
    ranks = store.ranks_after(anchor, len(ids), set(ids))
    for i, r in zip(ids, ranks):
        x = store.blocks[i]
        store.set_rank(i, r)
        if x.kind == HEADING and delta:
            x.level = clamp_level(x.level + delta)
        touched.add(i)
    canonical = {"after": after, "level_delta": delta, "range": ids, "prev": prev}
    return canonical, [body(K.BLOCK_MOVE, {"bid": bid}, {"restore": prev})]


def _h_block_delete(store: Store, op: Op, touched: set[str]):
    if "ids" in op.payload:
        ids = [str(i) for i in op.payload["ids"]]
        for i in ids:
            if i not in store.blocks:
                raise Conflict("no_block", i)
    else:
        bid = op.target["bid"]
        b = store.blocks.get(bid)
        if b is None or not b.live:
            raise Conflict("no_block", bid)
        ids = store.body_ids(bid)
    killed = []
    for i in ids:
        b = store.blocks[i]
        if b.live:
            b.dead = op.id
            killed.append(i)
        touched.add(i)
    return {"ids": ids, "killed": killed}, [body(K.BLOCK_RESTORE, dict(op.target), {"ids": killed, "by": op.id})]


def _h_block_restore(store: Store, op: Op, touched: set[str]):
    ids = [str(i) for i in op.payload["ids"]]
    by = op.payload.get("by")
    for i in ids:
        if i not in store.blocks:
            raise Conflict("no_block", i)
    revived = []
    for i in ids:
        b = store.blocks[i]
        if not b.live and (by is None or b.dead == by):
            b.dead = None
            revived.append(i)
        touched.add(i)
    canonical = {"ids": ids, "revived": revived}
    if by:
        canonical["by"] = by
    return canonical, [body(K.BLOCK_DELETE, dict(op.target), {"ids": revived})]


# ── bindings ─────────────────────────────────────────────────────────────────

def _live_feature(store: Store, fid: str) -> Block:
    b = store.blocks.get(fid)
    if b is None or b.kind != HEADING:
        raise Conflict("no_feature", fid)
    if not b.live:
        raise Conflict("dead_block", fid)
    return b


def _h_bind_attach(store: Store, op: Op, touched: set[str]):
    fid = op.target["fid"]
    _live_feature(store, fid)
    p = op.payload
    key = (p["file"], p["symbol"])
    fp, th = p.get("fingerprint", ""), p.get("types_hash", "")
    prior = store.bindings.get(key)
    touched.add(fid)
    if prior and prior.feature == fid:
        store.bindings[key] = replace(prior, fingerprint=fp, types_hash=th)
        canonical = {**p, "displaced": None, "prev_fingerprint": prior.fingerprint, "prev_types_hash": prior.types_hash}
        return canonical, [body(K.BIND_REFRESH, {"fid": fid}, {"file": prior.file, "symbol": prior.symbol,
                                                              "fingerprint": prior.fingerprint, "types_hash": prior.types_hash})]
    store.bindings[key] = Binding(fid, p["file"], p["symbol"], fp, th)
    canonical = {**p, "displaced": prior.to_json() if prior else None}
    inverse = [body(K.BIND_DETACH, {"fid": fid}, {"file": p["file"], "symbol": p["symbol"]})]
    if prior:
        touched.add(prior.feature)
        inverse.append(body(K.BIND_ATTACH, {"fid": prior.feature},
                            {"file": prior.file, "symbol": prior.symbol,
                             "fingerprint": prior.fingerprint, "types_hash": prior.types_hash}))
    return canonical, inverse


def _h_bind_detach(store: Store, op: Op, touched: set[str]):
    fid = op.target["fid"]
    key = (op.payload["file"], op.payload["symbol"])
    prior = store.bindings.get(key)
    if prior is None or prior.feature != fid:
        raise Conflict("not_bound", f"{key} on {fid}")
    del store.bindings[key]
    touched.add(fid)
    canonical = {**op.payload, "fingerprint": prior.fingerprint, "types_hash": prior.types_hash}
    return canonical, [body(K.BIND_ATTACH, {"fid": fid}, {"file": prior.file, "symbol": prior.symbol,
                                                          "fingerprint": prior.fingerprint, "types_hash": prior.types_hash})]


def _h_bind_refresh(store: Store, op: Op, touched: set[str]):
    fid = op.target["fid"]
    key = (op.payload["file"], op.payload["symbol"])
    prior = store.bindings.get(key)
    if prior is None or prior.feature != fid:
        raise Conflict("not_bound", f"{key} on {fid}")
    store.bindings[key] = replace(prior, fingerprint=op.payload.get("fingerprint", ""),
                                  types_hash=op.payload.get("types_hash", ""))
    touched.add(fid)
    canonical = {**op.payload, "prev_fingerprint": prior.fingerprint, "prev_types_hash": prior.types_hash}
    return canonical, [body(K.BIND_REFRESH, {"fid": fid}, {"file": prior.file, "symbol": prior.symbol,
                                                           "fingerprint": prior.fingerprint, "types_hash": prior.types_hash})]


# ── plans ────────────────────────────────────────────────────────────────────

def _h_plan_open(store: Store, op: Op, touched: set[str]):
    pid = op.target["pid"]
    if pid in store.plans:
        raise Conflict("duplicate_plan", pid)
    kind = op.payload.get("kind", "edit")
    target = op.payload.get("target")
    if kind == "delete":
        if not target:
            raise Conflict("delete_plan_needs_target")
        _live_feature(store, target)
    store.plans[pid] = Plan(pid, op.actor, kind, target, op.payload.get("note", ""), "open", [], op.id)
    return {"kind": kind, "target": target, "note": op.payload.get("note", "")}, []


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
            deps = [v[0] for key in ("after", "start", "end", "at")
                    if isinstance(v := o.payload.get(key), list) and len(v) == 2 and v[0] in by_id]
            dep = creators.get(o.payload.get("bid") or o.target.get("bid") or "")
            if dep:
                deps.append(dep)
            for d in deps:
                if d not in want:
                    want.add(d)
                    changed = True
    return [o.id for o in plan_ops if o.id in want]


def _undo_plan_op(store: Store, o: Op, by: OpId, touched: set[str], record: dict) -> None:
    """Undo one plan op the way a verdict does: inserts die by identity, marks
    are removed by their placing op, a block the plan created dies if it is now
    empty, a set is reverted, a move is restored unless someone re-ranked the
    block since (their move stands)."""
    if o.kind == K.TEXT_INSERT:
        for block, p in store.holders(o.id):
            got = block.text(p).kill_op(o.id, by=by)
            if got:
                record.setdefault("killed", []).append({"bid": block.id, "path": p, "segments": [list(k) for k in got]})
                touched.add(block.id)
    elif o.kind == K.TEXT_MARK:
        for block, p in store.mark_holders(o.id):
            for m in block.text(p).remove_marks(op=o.id):
                record.setdefault("marks", []).append({"bid": block.id, "path": p, "mark": m.to_json()})
                touched.add(block.id)
    elif o.kind in (K.BLOCK_ADD, K.BLOCK_SPLIT):
        bid = o.target["bid"] if o.kind == K.BLOCK_ADD else o.payload["new"]
        b = store.blocks.get(bid)
        if b is not None and b.live and all(b.content[f].live_len() == 0 for f in text_fields(b.kind)):
            b.dead = by
            record.setdefault("blocks", []).append(bid)
            touched.add(bid)
    elif o.kind == K.BLOCK_SET:
        bid, path = o.target["bid"], o.target["path"]
        b = store.blocks.get(bid)
        if b is None or not b.live or not o.inverse:
            return
        inv = o.inverse[0]["payload"]
        was = {"bid": bid, "path": path, "value": {"kind": b.kind, "level": b.level}.get(path, b.content.get(path)),
               "level": b.level}
        if path == "kind":
            b.kind = inv["value"]
            b.level = clamp_level(inv.get("level", 1)) if inv["value"] == HEADING else 0
        elif path == "level":
            b.level = clamp_level(inv["value"])
        else:
            b.content[path] = inv["value"]
        record.setdefault("sets", []).append(was)
        touched.add(bid)
    elif o.kind == K.BLOCK_MOVE:
        pos_of_move = store.ops.index(o) + 1
        for bid, rank, level in o.payload.get("prev", []):
            b = store.blocks.get(bid)
            if b is None or store.rank_head.get(bid, 0) > pos_of_move:
                continue
            record.setdefault("moved", []).append([bid, b.rank, b.level])
            store.set_rank(bid, rank)
            if b.kind == HEADING:
                b.level = clamp_level(level)
            touched.add(bid)


def _h_plan_accept(store: Store, op: Op, touched: set[str]):
    pid = op.target["pid"]
    plan = store.plans.get(pid)
    if plan is None:
        raise Conflict("no_plan", pid)
    if plan.status != "open":
        raise Conflict("plan_not_open", pid)
    pops = _plan_ops(store, plan)
    subset = op.payload.get("subset")
    accepted = _closure(pops, [str(s) for s in subset]) if subset is not None else [o.id for o in pops]
    acc = set(accepted)
    rejected = [o.id for o in pops if o.id not in acc]
    if plan.kind == "delete" and plan.target:
        _live_feature(store, plan.target)
    # Resolve every mark to strike before mutating anything.
    strike: list[tuple[Block, str, Mark]] = []
    for o in pops:
        if o.id in acc and o.kind == K.TEXT_MARK and o.payload.get("kind") == SUGGEST_DEL and o.payload.get("on", True):
            for block, p in store.mark_holders(o.id):
                strike.extend((block, p, m) for m in block.text(p).marks_of(o.id))
    record: dict = {}
    for o in reversed(pops):
        if o.id not in acc:
            _undo_plan_op(store, o, op.id, touched, record)
    for block, p, m in strike:
        t = block.text(p)
        if m not in t.marks:
            continue
        got = t.delete(m.start, m.end, by=op.id)
        t.marks.remove(m)
        record.setdefault("struck", []).append({"bid": block.id, "path": p, "mark": m.to_json(),
                                                "segments": [list(k) for k in got]})
        touched.add(block.id)
    deleted: list[str] = []
    if plan.kind == "delete" and plan.target:
        for i in store.body_ids(plan.target):
            store.blocks[i].dead = op.id
            deleted.append(i)
            touched.add(i)
    plan.status = "accepted"
    canonical = {"accepted": accepted, "rejected": rejected, "deleted_blocks": deleted, **record}
    return canonical, [body(K.PLAN_REOPEN, {"pid": pid},
                            {"from": "accepted", "subset": accepted if subset is not None else None,
                             "deleted_blocks": deleted, "by": op.id, **record})]


def _h_plan_reject(store: Store, op: Op, touched: set[str]):
    pid = op.target["pid"]
    plan = store.plans.get(pid)
    if plan is None:
        raise Conflict("no_plan", pid)
    if plan.status != "open":
        raise Conflict("plan_not_open", pid)
    record: dict = {}
    for o in reversed(_plan_ops(store, plan)):
        _undo_plan_op(store, o, op.id, touched, record)
    plan.status = "rejected"
    return dict(record), [body(K.PLAN_REOPEN, {"pid": pid}, {"from": "rejected", "by": op.id, **record})]


def _h_plan_reopen(store: Store, op: Op, touched: set[str]):
    """The inverse of a verdict: restore everything the verdict changed."""
    pid = op.target["pid"]
    plan = store.plans.get(pid)
    if plan is None:
        raise Conflict("no_plan", pid)
    if plan.status == "open":
        raise Conflict("plan_open", pid)
    p = op.payload
    by = p.get("by")
    for entry in p.get("killed", []) + p.get("struck", []) + p.get("marks", []) + p.get("sets", []):
        if entry["bid"] not in store.blocks:
            raise Conflict("no_block", entry["bid"])
    for i in p.get("blocks", []) + p.get("deleted_blocks", []) + [m[0] for m in p.get("moved", [])]:
        if i not in store.blocks:
            raise Conflict("no_block", i)
    for entry in p.get("killed", []) + p.get("struck", []):
        segs = [tuple(s) for s in entry["segments"]]
        if not segs:
            continue
        for block, f in _segment_holders(store, segs, (entry["bid"], entry["path"]), live_only=False):
            block.text(f).revive(segs, by=by)
            touched.add(block.id)
    for entry in p.get("struck", []) + p.get("marks", []):
        mk = Mark.from_json(entry["mark"])
        try:
            block, f = store.locate_char(mk.start, (entry["bid"], entry["path"]))
        except AnchorError:
            continue
        t = block.text(f)
        if t.holds(mk.end) and mk not in t.marks:
            t.marks.append(mk)
            store.index_text(block.id, f, t)
        touched.add(block.id)
    for i in p.get("blocks", []) + p.get("deleted_blocks", []):
        b = store.blocks[i]
        if b.dead == by:
            b.dead = None
        touched.add(i)
    for entry in p.get("sets", []):
        b = store.blocks[entry["bid"]]
        if entry["path"] == "kind":
            b.kind = entry["value"]
            b.level = int(entry.get("level", 0)) if entry["value"] == HEADING else 0
        elif entry["path"] == "level":
            b.level = clamp_level(entry["value"])
        else:
            b.content[entry["path"]] = entry["value"]
        touched.add(b.id)
    for bid, rank, level in p.get("moved", []):
        b = store.blocks[bid]
        store.set_rank(bid, rank)
        if b.kind == HEADING:
            b.level = clamp_level(level)
        touched.add(bid)
    plan.status = "open"
    verdict = K.PLAN_ACCEPT if p.get("from") == "accepted" else K.PLAN_REJECT
    vpayload = {"subset": p["subset"]} if verdict == K.PLAN_ACCEPT and p.get("subset") is not None else {}
    return dict(p), [body(verdict, {"pid": pid}, vpayload)]


# ── watermarks ───────────────────────────────────────────────────────────────

def _h_feature_handoff(store: Store, op: Op, touched: set[str]):
    fid = op.target["fid"]
    b = store.blocks.get(fid)
    if b is None or b.kind != HEADING:
        raise Conflict("no_feature", fid)
    w = store.watermarks(fid)
    prev = dict(w.handoff)
    touched.add(fid)
    if "restore" in op.payload:
        undone = op.payload.get("handoff")
        w.handoff = dict(op.payload["restore"])
        if undone in w.handoffs:
            w.handoffs.remove(undone)
        return {"restore": op.payload["restore"], "handoff": undone, "prev": prev}, \
            [body(K.FEATURE_HANDOFF, {"fid": fid}, {"covers": prev, "reinstate": undone})]
    covers = dict(op.payload.get("covers") or store.frontier)
    w.handoff = frontier_union(w.handoff, covers)
    hid = op.payload.get("reinstate") or op.id
    if hid not in w.handoffs:
        w.handoffs.append(hid)
    canonical = {"covers": covers, "base_sha": op.payload.get("base_sha", ""), "asked": op.payload.get("asked", ""),
                 "delete_code": bool(op.payload.get("delete_code", False))}
    if op.payload.get("reinstate"):
        canonical["reinstate"] = hid
    return canonical, [body(K.FEATURE_HANDOFF, {"fid": fid}, {"restore": prev, "handoff": hid})]


def _h_feature_realized(store: Store, op: Op, touched: set[str]):
    fid = op.target["fid"]
    b = store.blocks.get(fid)
    if b is None or b.kind != HEADING:
        raise Conflict("no_feature", fid)
    w = store.watermarks(fid)
    covers = dict(op.payload.get("covers") or w.handoff)
    w.realized = frontier_union(w.realized, covers)
    touched.add(fid)
    canonical = {"covers": covers, "sha": op.payload.get("sha", ""),
                 "files": list(op.payload.get("files") or []), "summary": op.payload.get("summary", "")}
    return canonical, []


_HANDLERS = {
    K.TEXT_INSERT: _h_text_insert, K.TEXT_DELETE: _h_text_delete, K.TEXT_RESTORE: _h_text_restore,
    K.TEXT_MARK: _h_text_mark,
    K.BLOCK_ADD: _h_block_add, K.BLOCK_SET: _h_block_set, K.BLOCK_SPLIT: _h_block_split,
    K.BLOCK_MERGE: _h_block_merge, K.BLOCK_MOVE: _h_block_move, K.BLOCK_DELETE: _h_block_delete,
    K.BLOCK_RESTORE: _h_block_restore,
    K.BIND_ATTACH: _h_bind_attach, K.BIND_DETACH: _h_bind_detach, K.BIND_REFRESH: _h_bind_refresh,
    K.PLAN_OPEN: _h_plan_open, K.PLAN_ACCEPT: _h_plan_accept, K.PLAN_REJECT: _h_plan_reject,
    K.PLAN_REOPEN: _h_plan_reopen,
    K.FEATURE_HANDOFF: _h_feature_handoff, K.FEATURE_REALIZED: _h_feature_realized,
}
