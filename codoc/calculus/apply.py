"""``apply(store, op)``: total, deterministic, O(the segments and marks of the
blocks it touches), plus the ordered insert a structural op pays
(docs/model.md, What ``apply`` does).

Every op is recorded: applied, or stored with ``conflict`` set and the state
untouched. Handlers are two-phase: they RESOLVE every anchor, block and mark
they will touch before the first mutation, so a conflict discovered midway
never leaves a partial change behind. A verdict undoes, strikes and applies
mark records in turn; a proposal it cannot apply is one the document has
already satisfied, not a reason to stop half way.

On success the op leaves with its canonical payload (what it actually did) and
its inverse (op bodies that undo it). The inverse of an inverse is the
original, including for a plan's mark toggle, whose redo names the record it
is restoring rather than carrying the plan tag. This is what makes undo a new op
and the timeline an un-apply through the same code.

``replay_recorded`` is how a RECORDED op comes back: as the record says, never
re-judged. Rebuild and the fold both go through it.

Conflict strings are stable identifiers, not prose; the UI maps them.
"""
from __future__ import annotations

from dataclasses import replace

from codoc.calculus import ops as K
from codoc.calculus.doc import (
    HEADING, ITEMS, LEVELLED, PARAGRAPH, Block, clamp_level, empty_content, is_one_line, is_text_field, level_for,
    text_fields,
)
from codoc.calculus.ids import END, START, CharRef, OpId, actor_kind, ref_from_json, ref_to_json
from codoc.calculus.ops import Op, OpBody, body, covered, frontier_union
from codoc.calculus.rank import RankError
from codoc.calculus.store import Binding, Plan, Store
from codoc.calculus.text import (
    PROPOSALS, SUGGEST_DEL, SUGGEST_MARK, AnchorError, Mark, MarkError, Text, nfc, struck_chars,
)

RESERVED_PATHS = frozenset({"id", "rank", "dead"})


class Conflict(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code


class Wire(dict):
    """A payload or a target exactly as it arrived. A key a handler needs and
    the client did not send is the client's mistake, recorded as
    ``missing:<key>``; a ``KeyError`` from anywhere else inside a handler is
    this code's mistake and has to surface as one."""

    def __missing__(self, key):
        raise Conflict(f"missing:{key}")


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
    op.payload, op.target = Wire(op.payload), Wire(op.target)
    try:
        if op.kind not in K.KINDS:
            raise Conflict("unknown_kind", op.kind)
        if store.policy is not None:
            denial = store.policy(store, op)
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
    except RankError:
        op.conflict, op.inverse, touched = "bad_rank", [], set()
    except (ValueError, TypeError):
        op.conflict, op.inverse, touched = "malformed", [], set()
    store.record(op, touched, structural and op.conflict is None)
    return op


def replay_recorded(store: Store, op: Op) -> Op:
    """Apply a RECORDED op as its record says, not as today would judge it.

    A refused op is recorded refused and applied not at all: the authority
    check is a question about the moment the op arrived, and so is every
    other conflict: an op that failed on an anchor its neighbours have since
    restored must not succeed on the second reading. Rebuild and the
    snapshot's fold both replay through this function, which keeps
    `replay(log) == store` (I6) true of a folded log too.
    """
    wire = Op(id=op.id, at=op.at, actor=op.actor, batch=op.batch, kind=op.kind,
              target=dict(op.target), payload=dict(op.payload), base=dict(op.base),
              plan=op.plan, caused_by=op.caused_by)
    if op.conflict:
        wire.conflict, wire.inverse = op.conflict, []
        store.record(wire, set(), False)
        return wire
    policy, store.policy = store.policy, None
    try:
        return apply(store, wire)
    finally:
        store.policy = policy


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
    if "\n" in chars and is_one_line(block.kind):
        raise Conflict("newline_in_line", block.kind)     # a line break in a heading or prose block is a new block
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
    """The canonical ``bid`` is the FIRST holder these characters were found
    in, not all of them: a delete whose range a split has since parted acts on
    every holder (``segments`` is the record that matters) and names one. A
    reader that needs the holders resolves ``segments`` by identity rather than
    trusting this field, as the timeline does."""
    bid, path = op.target["bid"], op.target.get("path", "text")
    if "segments" in op.payload:
        segs = [(str(o), int(k), int(n)) for o, k, n in op.payload["segments"]]
        work = _segment_holders(store, segs, (bid, path), live_only=True)
        _only_own_plan(store, op, work, segs)
        if not work:
            # Nothing named is held anywhere (a replayed no-op): the block the
            # canonical names is still the one this op is history of.
            b0 = store.blocks.get(bid)
            if b0 is None:
                raise Conflict("no_block", bid)
            work = [(b0, path)]
        killed: list[list] = []
        for block, p in work:
            killed.extend([list(k) for k in block.text(p).kill(segs, by=op.id, by_actor=op.actor, by_at=op.at)])
            touched.add(block.id)
        bid, path = work[0][0].id, work[0][1]
        canonical = {"segments": killed, "bid": bid, "path": path}
        return canonical, [body(K.TEXT_RESTORE, {"bid": bid, "path": path}, {"segments": killed, "by": op.id})]
    start, end = _char(op.payload["from"]), _char(op.payload["to"])
    block, p = store.locate_char(start, (bid, path))
    if not block.live:
        raise Conflict("dead_block", block.id)
    text = block.text(p)
    if not text.holds(end):
        raise Conflict("range_spans_blocks")
    _only_own_plan(store, op, [(block, p)], [(o, k, 1) for o, k in
                                             ((r[0], r[1]) for r in text.ops_between_refs(start, end))])
    killed = [list(k) for k in text.delete(start, end, by=op.id, by_actor=op.actor, by_at=op.at)]
    touched.add(block.id)
    canonical = {"segments": killed, "bid": block.id, "path": p}
    return canonical, [body(K.TEXT_RESTORE, {"bid": block.id, "path": p}, {"segments": killed, "by": op.id})]


def _only_own_plan(store: Store, op: Op, work, segs) -> None:
    """A plan taking back words it proposed is the proposal getting smaller; a
    plan reaching past its own words is not its to make.

    Under a plan, `text.delete` and `text.restore` may name ONLY characters
    that plan minted. Beyond tagged writes, this is the only rule a contributor
    needs, since every edit a suggester makes is part of their suggestion. It
    lives here, where anchors are resolved and the segments are in hand, rather
    than in the policy, which has only the plans to reason from."""
    if not op.plan:
        return
    if op.kind == K.TEXT_RESTORE:
        # A restore under a plan undoes a delete of that plan's. Reviving
        # characters somebody ELSE killed would override their op. A
        # maintainer removing a plan's words is an ordinary trunk delete and a
        # proposal does not get to answer it.
        undone = store.by_id.get(str(op.payload.get("by") or ""))
        if undone is None or undone.plan != op.plan:
            raise Conflict("not_this_plans_delete", str(op.payload.get("by") or ""))
        return
    named = {(str(o), int(k) + i) for o, k, n in segs for i in range(int(n))}
    for block, p in work:
        for sg in block.text(p).segs:
            for i in range(len(sg.chars)):
                if (sg.op, sg.k0 + i) in named and sg.plan != op.plan:
                    raise Conflict("not_this_plans_words", sg.op)


def _h_text_restore(store: Store, op: Op, touched: set[str]):
    """The canonical ``bid`` is the block the op NAMED, which after a split may
    hold none of these characters. As with the delete, ``segments`` is the
    record and identity is how a reader finds where they are."""
    bid, path = op.target["bid"], op.target.get("path", "text")
    by = op.payload.get("by")
    segs = [(str(o), int(k), int(n)) for o, k, n in op.payload["segments"]]
    work = _segment_holders(store, segs, (bid, path), live_only=False)
    _only_own_plan(store, op, work, segs)
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
    if kind in PROPOSALS and on:
        # A new proposal must CARRY its plan. An op that only names one in
        # `attrs` is allowed in exactly one case: it restores a record that was
        # already placed, and says which op placed it (`payload["op"]`). That is
        # the inverse of a plan mark's inverse, which must apply because the
        # inverse of an inverse is the original. Even then the plan must still
        # be open.
        pid = op.plan or (str(attrs.get("plan") or "") if op.payload.get("op") else "")
        plan = store.plans.get(pid)
        if plan is None or plan.status != "open":
            raise Conflict("suggest_needs_plan")
        attrs["plan"] = pid
    if kind == SUGGEST_DEL and on and "chars" not in attrs:
        # WHICH characters this proposes to cut, by identity, recorded when it
        # is placed, as `text.delete` stores its explicit segments: a range
        # struck later would take whatever had since been typed between the
        # ends with it, including the person's own answer to the proposal.
        attrs["chars"] = ",".join(f"{o}:{k}" for o, k in text.ops_between_refs(start, end))
    # A restored mark keeps its original placer and actor: only an inverse says
    # so, and an inverse is the person's own op. Both ride in the canonical
    # payload so a replay restores the same mark (I6).
    actor = op.payload.get("actor") or op.actor
    placer = op.payload.get("op") or op.id
    at = op.payload.get("at") or op.at
    if op.plan and kind not in PROPOSALS:
        # A plan proposes; it does not edit the trunk's marks. The toggle is
        # recorded beside them and a verdict is what applies or drops it, so
        # nothing later has to find a mark that a merge, a split or another
        # plan has moved since.
        record = Mark(SUGGEST_MARK, {"plan": op.plan, "mark": kind, "on": "1" if on else "",
                                     **{f"a:{k}": v for k, v in attrs.items()}}, start, end, actor, placer, at)
        if record not in text.marks:
            text.marks.append(record)
        store.index_text(block.id, p, text)
        touched.add(block.id)
        canonical = {"kind": kind, "attrs": attrs, "start": ref_to_json(start), "end": ref_to_json(end), "on": on,
                     "bid": block.id, "path": p, "proposed": record.to_json(), "removed": [], "added": []}
        return canonical, [body(K.TEXT_MARK, {"bid": block.id, "path": p},
                                {"kind": SUGGEST_MARK, "attrs": dict(record.attrs), "start": list(start),
                                 "end": list(end), "on": False})]
    removed, added = text.set_mark(kind, attrs, start, end, on, actor=actor, op=placer, at=at)
    store.index_text(block.id, p, text)
    touched.add(block.id)
    canonical = {"kind": kind, "attrs": attrs, "start": ref_to_json(start), "end": ref_to_json(end), "on": on,
                 "bid": block.id, "path": p, "removed": [m.to_json() for m in removed],
                 "added": [m.to_json() for m in added]}
    if op.payload.get("actor"):
        canonical["actor"] = actor
    if op.payload.get("op"):
        canonical["op"] = placer
    if op.payload.get("at"):
        canonical["at"] = at          # a restored mark keeps when it was placed, as it keeps who
    inverse: list[OpBody] = []
    for m in added:
        inverse.append(body(K.TEXT_MARK, {"bid": block.id, "path": p},
                            {"kind": m.kind, "attrs": m.attrs, "start": list(m.start), "end": list(m.end), "on": False}))
    for m in removed:
        inverse.append(body(K.TEXT_MARK, {"bid": block.id, "path": p},
                            {"kind": m.kind, "attrs": m.attrs, "start": list(m.start), "end": list(m.end),
                             "on": True, "actor": m.actor, "op": m.op, "at": m.at}))
    return canonical, inverse


# ── blocks ───────────────────────────────────────────────────────────────────

def _after(store: Store, after) -> str | None:
    if after == START or after is None:
        return None
    if after not in store.blocks:
        raise Conflict("no_block", str(after))
    return after


def _under(store: Store, under, moving: list[str]) -> tuple[str, int]:
    """Where a heading placed ``under`` a parent goes, as ``(after, level)``:
    after the last live block of the parent's range, one level deeper.
    ``START`` is the document: after its last live block, at level 1. The
    blocks ``moving`` are left out of the range they are leaving."""
    if under == START:
        last = next((i for _, i in reversed(store.order) if store.blocks[i].live and i not in moving), START)
        return last, 1
    parent = _live_feature(store, str(under))
    if parent.id in moving:
        raise Conflict("bad_target", parent.id)
    return [i for i in store.dominated_ids(parent.id) if i not in moving][-1], clamp_level(parent.level + 1)


def _h_block_add(store: Store, op: Op, touched: set[str]):
    bid = op.target["bid"]
    if bid in store.blocks:
        raise Conflict("duplicate_block", bid)
    kind = op.payload["kind"]
    if "under" in op.payload:
        # Resolved here, against the tree as it is now; the record keeps the
        # place it resolved to, so replay does not resolve it again.
        if kind != HEADING:
            raise Conflict("malformed", "under places a heading")
        after, level = _under(store, op.payload["under"], [])
    else:
        after, level = op.payload.get("after", START), level_for(kind, op.payload.get("level"))
    anchor = _after(store, after)
    content = empty_content(kind, op.payload.get("content"))
    for f, tj in (op.payload.get("texts") or {}).items():
        if f in text_fields(kind):
            content[f] = Text.from_json(tj)
    # A pinned rank comes from a snapshot or a recorded inverse re-placing a
    # block exactly where it was; a fresh add derives one from its anchor.
    rank = op.payload.get("rank") or store.rank_after(anchor)
    if op.payload.get("bound_once"):
        store.bound_once.add(bid)        # a snapshot restoring a feature that once had code
    block = Block(bid, kind, rank, level, content)
    if op.payload.get("dead"):               # a snapshot restoring a block with its killer
        block.kill(op.payload["dead"], op.payload.get("dead_actor", ""), op.payload.get("dead_at", ""),
                   op.payload.get("dead_in", ""))
    if op.payload.get("detached"):
        block.detached = [dict(e) for e in op.payload["detached"]]
    if op.payload.get("code_at") or op.payload.get("realized_at"):
        w = store.watermarks(bid)
        w.code_at = str(op.payload.get("code_at") or "")
        w.realized_at = str(op.payload.get("realized_at") or "")
    store.add_block(block)
    for f in text_fields(kind):
        store.index_text(bid, f, content[f])
    touched.add(bid)
    canonical = {"kind": kind, "level": level, "after": after, "rank": block.rank,
                 "content": {k: v for k, v in content.items() if not isinstance(v, Text)}}
    if op.payload.get("texts"):
        canonical["texts"] = op.payload["texts"]
    if block.dead:
        canonical["dead"] = block.dead
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
        if not (is_one_line(value) and is_one_line(b.kind)):
            raise Conflict("kind_incompatible", f"{b.kind}->{value}")    # only heading and the prose kinds convert
        prev_kind, prev_level = b.kind, b.level
        # bullet <-> numbered keeps the depth; any other change starts at level 1 unless told.
        keep = prev_level if prev_kind in ITEMS and value in ITEMS else None
        b.kind = value
        b.level = level_for(value, op.payload.get("level") or keep)
        canonical.update({"prev": prev_kind, "prev_level": prev_level, "level": b.level, "_structural": True})
        inverse = [body(K.BLOCK_SET, {"bid": bid, "path": "kind"}, {"value": prev_kind, "level": prev_level})]
    elif path == "level":
        if b.kind not in LEVELLED:
            raise Conflict("no_level", bid)
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
    if not is_one_line(b.kind):
        raise Conflict("not_splittable", b.kind)     # a newline in a source or plugin field is content
    existing = store.blocks.get(new)
    if existing is not None and (existing.live or not forced):
        raise Conflict("duplicate_block", new)
    was = text.ops()
    tail = text.split(at)
    head_live, tail_live = text.live_len() > 0, tail.live_len() > 0
    new_first = (forced == "head") if forced else ((not head_live) and tail_live)
    new_text, keep_text = (text, tail) if new_first else (tail, text)
    b.content[path] = keep_text
    new_kind = PARAGRAPH if b.kind == HEADING else b.kind
    new_level = b.level if new_kind in ITEMS else 0      # a list item splits into two at the same depth
    rank = op.payload.get("rank") or (store.rank_before(b.id) if new_first else store.rank_after(b.id))
    if existing is not None:
        # A merge's inverse: the merged block comes back as it was, with its
        # own kind and level (a numbered item merged into a bullet stays numbered).
        existing.revive()
        existing.content[path] = new_text
        store.set_rank(new, rank)
        nb = existing
    else:
        nb = Block(new, new_kind, rank, new_level, empty_content(new_kind))
        nb.content[path] = new_text
        store.add_block(nb)
    store.index_text(b.id, path, keep_text)
    store.index_text(new, path, new_text)
    store.prune_index(b.id, path, keep_text, was)
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
    if not (is_one_line(a.kind) and is_one_line(b.kind)):
        raise Conflict("kind_incompatible")
    prev, nxt = store.live_neighbours_of(into)
    if bid not in (prev, nxt):
        raise Conflict("not_adjacent")
    before = bid == prev
    ta, tb = a.text(path), b.text(path)
    was = tb.ops()
    if before:
        joined = Text(tb.segs, tb.marks)
        seam = joined.merge(ta)
        a.content[path] = joined
        b.content[path] = Text()
    else:
        seam = ta.merge(tb)
    b.kill(op.id, op.actor, op.at)
    store.index_text(into, path, a.text(path))
    store.prune_index(bid, path, b.text(path), was)
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
    b = store.blocks.get(bid)
    if b is None or not b.live:
        raise Conflict("no_block", bid)
    ids = store.dominated_ids(bid)
    if "under" in op.payload:
        if b.kind != HEADING:
            raise Conflict("malformed", "under places a heading")
        after, level = _under(store, op.payload["under"], ids)
        delta = level - b.level
    else:
        after, delta = op.payload.get("after", START), int(op.payload.get("level_delta", 0))
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


def _detach_dead(store: Store, fids: list[str], touched: set[str]) -> list[dict]:
    """A dead heading owns no code: drop its bindings, remember them
    ON THE BLOCK (the deleted heading's settle and the undo both read them there, so a
    fold may take this op away), and return them for the inverse."""
    out = []
    for fid in fids:
        mine = []
        for key, b in list(store.bindings.items()):
            if b.feature == fid:
                mine.append({"fid": fid, **b.to_json()})
                del store.bindings[key]
                touched.add(fid)
        if mine:
            store.blocks[fid].detached = mine
        out.extend(mine)
    return out


def _reattach(store: Store, entries: list[dict], touched: set[str]) -> None:
    """Re-attach what a delete detached, unless the symbol has an owner now
    (their attach stands)."""
    for e in entries:
        key = (e["file"], e["symbol"])
        owner = store.blocks.get(e["fid"])
        if key not in store.bindings and owner is not None:
            store.bindings[key] = Binding(e["fid"], e["file"], e["symbol"], e.get("fingerprint", ""), e.get("types_hash", ""))
            touched.add(e["fid"])
        if owner is not None:
            owner.detached = [d for d in owner.detached if (d["file"], d["symbol"]) != key]


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
    killed = [i for i in ids if store.blocks[i].live]
    store.kill(killed, op.id, op.actor, op.at)
    touched.update(ids)
    detached = _detach_dead(store, [i for i in killed if store.blocks[i].kind == HEADING], touched)
    canonical = {"ids": ids, "killed": killed, "detached": detached}
    return canonical, [body(K.BLOCK_RESTORE, dict(op.target), {"ids": killed, "by": op.id, "bindings": detached})]


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
            b.revive()
            revived.append(i)
        touched.add(i)
    alive = set(revived)
    _reattach(store, [e for e in op.payload.get("bindings") or [] if e.get("fid") in alive], touched)
    canonical = {"ids": ids, "revived": revived}
    if by:
        canonical["by"] = by
    if op.payload.get("bindings"):
        canonical["bindings"] = op.payload["bindings"]
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
    # After the payload is read, not before: a malformed attach conflicts, and
    # a feature that never had code must not be remembered as one that lost it
    # (`reflect._emptied` proposes deleting exactly those).
    store.bound_once.add(fid)
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
    store.watermarks(fid).code_at = max(store.watermarks(fid).code_at, op.at)
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
    store.watermarks(fid).code_at = max(store.watermarks(fid).code_at, op.at)
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
    if kind == "delete" and not op.payload.get("opened_by"):
        # A snapshot restoring an accepted delete plan names a heading that is
        # dead by now; only a fresh proposal has to name a live one.
        if not target:
            raise Conflict("delete_plan_needs_target")
        _live_feature(store, target)
    # ``ops`` and ``opened_by`` are how a snapshot restores a plan whose own ops
    # now live in the archive; an ordinary plan.open sends neither.
    ops = [str(i) for i in op.payload.get("ops") or []]
    opened_by = op.payload.get("opened_by") or op.id
    # A build request is a coding agent's proposal made before the code; from
    # anyone else (a suggester's plan is about words) it is dropped, on replay too.
    build = bool(op.payload.get("build")) and actor_kind(op.actor) == "agent"
    store.plans[pid] = store.open_plans[pid] = Plan(
        pid, op.actor, kind, target, op.payload.get("note", ""), "open", ops, opened_by, build)
    canonical = {"kind": kind, "target": target, "note": op.payload.get("note", "")}
    if build:
        canonical["build"] = True       # accepting it asks for the code (derive.accept_handoffs)
    if ops:
        canonical["ops"] = ops
    if op.payload.get("opened_by"):
        canonical["opened_by"] = opened_by
    return canonical, []


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
            dep = creators.get(o.payload.get("bid") or o.target.get("bid") or o.target.get("fid") or "")
            if dep:
                deps.append(dep)
            for d in deps:
                if d not in want:
                    want.add(d)
                    changed = True
    return [o.id for o in plan_ops if o.id in want]


def _undo_plan_op(store: Store, o: Op, by: OpId, touched: set[str], record: dict,
                  by_actor: str = "", by_at: str = "") -> None:
    """Undo one plan op the way a verdict does: inserts die by identity, marks
    are removed by their placing op, a block the plan created dies if it is now
    empty, a set is reverted, a move is restored unless someone re-ranked the
    block since (their move stands)."""
    if o.kind == K.TEXT_INSERT:
        for block, p in store.holders(o.id):
            got = block.text(p).kill_op(o.id, by=by, by_actor=by_actor, by_at=by_at)
            if got:
                record.setdefault("killed", []).append({"bid": block.id, "path": p, "segments": [list(k) for k in got]})
                touched.add(block.id)
    elif o.kind == K.TEXT_MARK:
        # A plan's mark toggle never touched the trunk: it is a record beside
        # it, and undoing the op is dropping the record. Found by the plan it
        # names, so a merge, a split or another plan moving the trunk's marks
        # cannot hide it.
        for block, p in store.mark_holders(o.id):
            t = block.text(p)
            for m in [x for x in t.proposals(o.plan) if x.op == o.id]:
                t.marks.remove(m)
                record.setdefault("marks", []).append({"bid": block.id, "path": p, "mark": m.to_json()})
                touched.add(block.id)
    elif o.kind in (K.BLOCK_ADD, K.BLOCK_SPLIT):
        bid = o.target["bid"] if o.kind == K.BLOCK_ADD else o.payload["new"]
        b = store.blocks.get(bid)
        if b is not None and b.live and all(b.content[f].live_len() == 0 for f in text_fields(b.kind)):
            b.kill(by)
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
            b.level = level_for(inv["value"], inv.get("level"))
        elif path == "level":
            b.level = clamp_level(inv["value"])
        else:
            b.content[path] = inv["value"]
        record.setdefault("sets", []).append(was)
        touched.add(bid)
    elif o.kind == K.BIND_ATTACH:
        key = (o.payload["file"], o.payload["symbol"])
        fid = o.target["fid"]
        cur = store.bindings.get(key)
        if cur is None or cur.feature != fid:
            return                      # someone re-bound it since; their attach stands
        record.setdefault("binds", []).append({"fid": fid, **cur.to_json()})
        if "prev_fingerprint" in o.payload:
            # The plan only refreshed a binding the feature already had.
            store.bindings[key] = replace(cur, fingerprint=o.payload.get("prev_fingerprint", ""),
                                          types_hash=o.payload.get("prev_types_hash", ""))
            touched.add(fid)
            return
        displaced = o.payload.get("displaced")
        if displaced:
            store.bindings[key] = Binding(displaced["feature"], displaced["file"], displaced["symbol"],
                                          displaced.get("fingerprint", ""), displaced.get("types_hash", ""))
            touched.add(displaced["feature"])
        else:
            del store.bindings[key]
        touched.add(fid)
    elif o.kind == K.BLOCK_MOVE:
        # An archived op (position -1) predates every re-rank this store knows,
        # so "did someone move it since?" cannot be asked of the ranks here.
        pos = store.position.get(o.id, -1)
        pos_of_move = pos + 1 if pos >= 0 else None
        for bid, rank, level in o.payload.get("prev", []):
            b = store.blocks.get(bid)
            if b is None or (pos_of_move is not None and store.rank_head.get(bid, 0) > pos_of_move):
                continue
            record.setdefault("moved", []).append([bid, b.rank, b.level])
            store.set_rank(bid, rank)
            if b.kind == HEADING:
                b.level = clamp_level(level)
            touched.add(bid)


def _proposed_marks(store: Store, pid: str, accepted: set[str]) -> list[tuple[Block, str, Mark]]:
    """This plan's ``suggest_mark`` records, in the order they were placed. A
    subset accept takes only the ones whose op it took."""
    out: list[tuple[Block, str, Mark, int]] = []
    for block in store.blocks.values():
        for p in text_fields(block.kind):
            for m in block.text(p).proposals(pid, SUGGEST_MARK):
                if m.op in accepted:
                    out.append((block, p, m, store.position.get(m.op, 0)))
    out.sort(key=lambda t: t[3])
    return [(b, p, m) for b, p, m, _ in out]




def _h_plan_accept(store: Store, op: Op, touched: set[str]):
    pid = op.target["pid"]
    plan = store.plans.get(pid)
    if plan is None:
        raise Conflict("no_plan", pid)
    if plan.status != "open":
        raise Conflict("plan_not_open", pid)
    pops = _plan_ops(store, plan)
    _touch_plan(store, plan, pops, touched)
    subset = op.payload.get("subset")
    accepted = _closure(pops, [str(s) for s in subset]) if subset is not None else [o.id for o in pops]
    acc = set(accepted)
    rejected = [o.id for o in pops if o.id not in acc]
    if plan.kind == "delete" and plan.target and plan.target not in store.blocks:
        raise Conflict("no_feature", plan.target)
    # Resolve every mark to strike before mutating anything.
    strike: list[tuple[Block, str, Mark]] = []
    for o in pops:
        if o.id in acc and o.kind == K.TEXT_MARK and o.payload.get("kind") == SUGGEST_DEL and o.payload.get("on", True):
            for block, p in store.mark_holders(o.id):
                strike.extend((block, p, m) for m in block.text(p).marks_of(o.id))
    record: dict = {}
    for o in reversed(pops):
        if o.id not in acc:
            _undo_plan_op(store, o, op.id, touched, record, op.actor, op.at)
    for block, p, m in strike:
        t = block.text(p)
        if m not in t.marks:
            continue
        got = t.kill(struck_chars(m), by=op.id, by_actor=op.actor, by_at=op.at)
        t.marks.remove(m)
        record.setdefault("struck", []).append({"bid": block.id, "path": p, "mark": m.to_json(),
                                                "segments": [list(k) for k in got]})
        touched.add(block.id)
    # The accepted mark proposals become trunk marks now, under the actor who
    # proposed them and the op that did. This is the only moment a plan's mark
    # toggle touches the trunk, which is why nothing has to find it again later.
    for block, p, m in _proposed_marks(store, plan.id, acc):
        t = block.text(p)
        want = {k[2:]: v for k, v in m.attrs.items() if k.startswith("a:")}
        try:
            was_removed, was_added = t.set_mark(m.attrs.get("mark", ""), want, m.start, m.end,
                                                bool(m.attrs.get("on")), actor=m.actor, op=m.op, at=m.at)
        except MarkError:
            # A proposal to turn a WHOLE mark off whose mark the person has
            # already removed asks for a state the document is in. Agreeing
            # costs nothing; raising here would leave the strike above applied,
            # the plan open, and every later accept failing the same way. That
            # would break the two-phase rule (see this module's header) in the
            # handler that mutates most.
            t.marks.remove(m)
            store.index_text(block.id, p, t)
            record.setdefault("applied", []).append({"bid": block.id, "path": p, "mark": m.to_json(),
                                                     "removed": [], "added": []})
            touched.add(block.id)
            continue
        t.marks.remove(m)
        store.index_text(block.id, p, t)
        record.setdefault("applied", []).append(
            {"bid": block.id, "path": p, "mark": m.to_json(),
             "removed": [x.to_json() for x in was_removed], "added": [x.to_json() for x in was_added]})
        touched.add(block.id)
    deleted: list[str] = []
    # A feature the person deleted by hand before answering the proposal is
    # already gone: the verdict agrees with them rather than conflicting.
    if plan.kind == "delete" and plan.target and store.blocks[plan.target].live:
        deleted = store.body_ids(plan.target)
        store.kill(deleted, op.id, op.actor, op.at)
        touched.update(deleted)
        record["detached"] = _detach_dead(store, [plan.target], touched)
    plan.status, plan.decided_by = "accepted", op.actor
    store.open_plans.pop(pid, None)
    canonical = {"subset": subset, "accepted": accepted, "rejected": rejected, "deleted_blocks": deleted, **record}
    return canonical, [body(K.PLAN_REOPEN, {"pid": pid},
                            {"from": "accepted", "closure": accepted if subset is not None else None,
                             "deleted_blocks": deleted, "by": op.id, **record})]


def _h_plan_reject(store: Store, op: Op, touched: set[str]):
    pid = op.target["pid"]
    plan = store.plans.get(pid)
    if plan is None:
        raise Conflict("no_plan", pid)
    if plan.status != "open":
        raise Conflict("plan_not_open", pid)
    record: dict = {}
    pops = _plan_ops(store, plan)
    _touch_plan(store, plan, pops, touched)
    for o in reversed(pops):
        _undo_plan_op(store, o, op.id, touched, record, op.actor, op.at)
    plan.status, plan.decided_by = "rejected", op.actor
    store.open_plans.pop(pid, None)
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
    for entry in p.get("killed", []) + p.get("struck", []) + p.get("marks", []) + p.get("sets", []) + p.get("applied", []):
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
    for entry in p.get("applied", []):
        # An accept turned this proposal into trunk marks; the reopen puts the
        # trunk back as it was and the proposal beside it again.
        b = store.blocks[entry["bid"]]
        t = b.text(entry["path"])
        for mj in entry.get("added", []):
            mk = Mark.from_json(mj)
            if mk in t.marks:
                t.marks.remove(mk)
        for mj in entry.get("removed", []):
            mk = Mark.from_json(mj)
            if mk not in t.marks and t.holds(mk.start) and t.holds(mk.end):
                t.marks.append(mk)
        proposal = Mark.from_json(entry["mark"])
        if proposal not in t.marks and t.holds(proposal.start) and t.holds(proposal.end):
            t.marks.append(proposal)
        store.index_text(b.id, entry["path"], t)
        touched.add(b.id)
    for i in p.get("blocks", []) + p.get("deleted_blocks", []):
        b = store.blocks[i]
        if b.dead == by:
            b.revive()
        touched.add(i)
    _reattach(store, p.get("detached", []), touched)
    # A reopen re-applies the verdict's own record, so its `moved` and `sets`
    # are the values it SETS. What it DISPLACED (what a reader of the page
    # before it needs) has nowhere else to come from, so record it.
    undid: dict = {"moved": [], "sets": []}
    for entry in p.get("sets", []):
        b = store.blocks[entry["bid"]]
        if entry["path"] == "kind":
            undid["sets"].append({"bid": b.id, "path": "kind", "value": b.kind, "level": b.level})
            b.kind = entry["value"]
            b.level = level_for(entry["value"], entry.get("level"))
        elif entry["path"] == "level":
            undid["sets"].append({"bid": b.id, "path": "level", "value": b.level})
            b.level = clamp_level(entry["value"])
        else:
            b.content[entry["path"]] = entry["value"]
        touched.add(b.id)
    for bid, rank, level in p.get("moved", []):
        b = store.blocks[bid]
        undid["moved"].append([bid, b.rank, b.level])
        store.set_rank(bid, rank)
        if b.kind == HEADING:
            b.level = clamp_level(level)
        touched.add(bid)
    for entry in p.get("binds", []):
        owner = store.blocks.get(entry["fid"])
        if owner is None or not owner.live:
            continue        # a dead heading owns no code, whatever the verdict undid
        key = (entry["file"], entry["symbol"])
        prior = store.bindings.get(key)
        if prior is not None:
            touched.add(prior.feature)
        store.bindings[key] = Binding(entry["fid"], entry["file"], entry["symbol"],
                                      entry.get("fingerprint", ""), entry.get("types_hash", ""))
        touched.add(entry["fid"])
    plan.status, plan.decided_by = "open", ""
    store.open_plans[pid] = plan
    _touch_plan(store, plan, _plan_ops(store, plan), touched)
    verdict = K.PLAN_ACCEPT if p.get("from") == "accepted" else K.PLAN_REJECT
    vpayload = {"subset": p["closure"]} if verdict == K.PLAN_ACCEPT and p.get("closure") is not None else {}
    return {**p, "undid": undid}, [body(verdict, {"pid": pid}, vpayload)]


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
    canonical = {"covers": covers, "asked": op.payload.get("asked", ""),
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
    if covered(w.handoff, w.realized):
        # Realization settles a request. With none there is nothing
        # to settle, and recording a no-op would report it as done.
        raise Conflict("nothing_queued", fid)
    covers = dict(op.payload.get("covers") or w.handoff)
    w.realized = frontier_union(w.realized, covers)
    w.realized_at = max(w.realized_at, op.at)
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
