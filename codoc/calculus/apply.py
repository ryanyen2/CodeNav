"""``apply(store, op)``: total, deterministic, O(the segments and marks of the
blocks it touches), plus the ordered insert a structural op pays
(docs/model.md, What ``apply`` does).

Every op is recorded: applied, or stored with ``conflict`` set and the state
untouched. Handlers are two-phase: they resolve every anchor, block and mark
they will touch before the first mutation, so a conflict discovered midway
never leaves a partial change behind. The handlers mirror docs/model.md,
Operations; the verdicts and their undo are in ``verdict``.

On success the op leaves with its canonical payload (what it actually did) and
its inverse (op bodies that undo it). The inverse of an inverse is the
original, including for a plan's mark toggle, whose redo names the record it
is restoring rather than carrying the plan tag. Undo submits the inverse as a
new op, and the timeline un-applies through the same records.

``replay_recorded`` is how a recorded op comes back: as the record says, never
re-judged. Rebuild and the fold both go through it.

Conflict strings are stable identifiers, not prose; the UI maps them.
"""
from __future__ import annotations

from dataclasses import replace
from itertools import dropwhile

from codoc.calculus import ops as K
from codoc.calculus import rank as R
from codoc.calculus.doc import (
    BUILTIN, HEADING, ITEMS, LAYOUT_KEY, LEVELLED, PARAGRAPH, Block, Layout, clamp_level, empty_content, is_one_line,
    layout_of, level_for,
)
from codoc.calculus.ids import END, START, CharRef, actor_kind, is_human, ref_from_json, ref_to_json
from codoc.calculus.ops import Conflict, Op, OpBody, body, frontier_union, in_frontier
from codoc.calculus.rank import RankError
from codoc.calculus.store import Binding, Plan, Store
from codoc.calculus.text import PROPOSALS, SUGGEST_DEL, SUGGEST_MARK, WHOLE, AnchorError, Mark, MarkError, Text, nfc
from codoc.calculus.verdict import reopen, verdict

RESERVED_PATHS = frozenset({"id", "rank", "ranked_by", "dead", LAYOUT_KEY})   # LAYOUT_KEY: the fence's own key for the layout


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
    """Apply a recorded op as its record says, not as today would judge it.

    A refused op is recorded refused and applied not at all: the authority
    check is a question about the moment the op arrived, and so is every
    other conflict: an op that failed on an anchor its neighbours have since
    restored must not succeed on the second reading. Rebuild and the
    snapshot's fold both replay through this function, which keeps
    `replay(log) == store` (I6) true of a folded log too.

    The inverse is computed again, never read from the record, since undo
    sends it as the person's own op. A record that has none gets none: the
    ops a fold writes re-express state, are nobody's gesture, and carry no
    inverse, so undo never takes one of them for the person's last change.
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
        got = apply(store, wire)
    finally:
        store.policy = policy
    if got is wire and not op.inverse:
        got.inverse = []
    return got


# ── text ─────────────────────────────────────────────────────────────────────

def _live_text_field(store: Store, bid: str, path: str) -> tuple[Block, Text]:
    b = store.blocks.get(bid)
    if b is None:
        raise Conflict("no_block", bid)
    if not b.live:
        raise Conflict("dead_block", bid)
    if path not in b.layout.text:
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
        block, path = _holder(store, op, after)  # type: ignore[arg-type]
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


def _holder(store: Store, op: Op, ref: CharRef) -> tuple[Block, str]:
    """The field holding character ``ref``, as ``store.locate_char`` finds it
    from the op's target (``_named``)."""
    block, path = store.locate_char(ref, (op.target["bid"], op.target.get("path", "text")))
    _named(store, op, block)
    return block, path


def _named(store: Store, op: Op, block: Block) -> None:
    """A text op writes into the block that holds its characters, and the
    policy judged it by the block it names (``roles.Roles.policy``), so the
    two must be one block: a suggester's op on their own comment must not
    reach the paragraph its anchor is in. The one exception is a split or a
    merge since the client read. Those move characters between headings and
    prose blocks only, so between two of those the name is a hint and the op
    follows the characters. Any other kind's characters, a comment's among
    them, never move."""
    bid = op.target["bid"]
    named = store.blocks.get(bid)
    if block.id != bid and not (named is not None and is_one_line(named.kind) and is_one_line(block.kind)):
        raise Conflict("bad_anchor", f"{block.id} holds it, not {bid}")


def _segment_holders(store: Store, op: Op, segs: list[tuple[str, int, int]], live_only: bool) -> list[tuple[Block, str]]:
    """The holders of the named characters: both ends of every segment are
    located by identity (a segment split across two blocks has two holders).
    Other blocks holding other characters of the same op are not touched."""
    work: list[tuple[Block, str]] = []
    for o, k, n in segs:
        for ref in ((o, k), (o, k + n - 1)):
            h = _holder(store, op, ref)
            if live_only and not h[0].live:
                raise Conflict("dead_block", h[0].id)
            if h not in work:
                work.append(h)
    return work


def _h_text_delete(store: Store, op: Op, touched: set[str]):
    """The canonical ``bid`` is the first holder these characters were found
    in, not all of them: a delete whose range a split has since parted acts on
    every holder (``segments`` is the record that matters) and names one. A
    reader that needs the holders resolves ``segments`` by identity rather than
    trusting this field, as the timeline does."""
    bid, path = op.target["bid"], op.target.get("path", "text")
    if "segments" in op.payload:
        segs = [(str(o), int(k), int(n)) for o, k, n in op.payload["segments"]]
        work = _segment_holders(store, op, segs, live_only=True)
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
    block, p = _holder(store, op, start)
    if not block.live:
        raise Conflict("dead_block", block.id)
    text = block.text(p)
    if not text.holds(end):
        raise Conflict("range_spans_blocks")
    _only_own_plan(store, op, [(block, p)], [(o, k, 1) for o, k in text.live_chars_between(start, end)])
    killed = [list(k) for k in text.delete(start, end, by=op.id, by_actor=op.actor, by_at=op.at)]
    touched.add(block.id)
    canonical = {"segments": killed, "bid": block.id, "path": p}
    return canonical, [body(K.TEXT_RESTORE, {"bid": block.id, "path": p}, {"segments": killed, "by": op.id})]


def _only_own_plan(store: Store, op: Op, work, segs) -> None:
    """Under a plan, ``text.delete`` may name only characters that plan
    minted, and ``text.restore`` may only undo a delete of that plan's: a plan
    may take back its own words, and asks for anyone else's to go with
    ``suggest_del``. Checked here, where the segments are resolved, because
    the policy has only the plans to reason from."""
    if not op.plan:
        return
    if op.kind == K.TEXT_RESTORE:
        # Reviving characters somebody else killed would override their op;
        # a maintainer removing a plan's words is a trunk delete a proposal
        # does not get to answer. Asked of the plan's op list, which a fold
        # keeps, not of the op, which it may archive.
        by = str(op.payload.get("by") or "")
        if by not in store.plans[op.plan].ops:
            raise Conflict("not_this_plans_delete", by)
        return
    named = {(str(o), int(k) + i) for o, k, n in segs for i in range(int(n))}
    for block, p in work:
        for sg in block.text(p).segs:
            for i in range(len(sg.chars)):
                if (sg.op, sg.k0 + i) in named and sg.plan != op.plan:
                    raise Conflict("not_this_plans_words", sg.op)


def _h_text_restore(store: Store, op: Op, touched: set[str]):
    """The canonical ``bid`` is the block the op named, which after a split may
    hold none of these characters. As with the delete, ``segments`` is the
    record and identity is how a reader finds where they are."""
    bid, path = op.target["bid"], op.target.get("path", "text")
    by = op.payload.get("by")
    segs = [(str(o), int(k), int(n)) for o, k, n in op.payload["segments"]]
    work = _segment_holders(store, op, segs, live_only=False)
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
    kind = op.payload["kind"]
    attrs = dict(op.payload.get("attrs") or {})
    start, end = _char(op.payload["start"]), _char(op.payload["end"])
    on = bool(op.payload.get("on", True))
    if not on and kind in WHOLE and op.payload.get("op"):
        return _whole_mark_off(store, op, kind, attrs, touched)
    block, p = _holder(store, op, start)
    if not block.live:
        raise Conflict("dead_block", block.id)
    text = block.text(p)
    if not text.holds(end):
        raise Conflict("range_spans_blocks")
    if kind in PROPOSALS and on:
        # A new proposal must carry its plan. An op that only names one in
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
        # Which characters this proposes to cut, by identity, recorded when it
        # is placed, as `text.delete` stores its explicit segments: a range
        # struck later would take whatever had since been typed between the
        # ends with it, including the person's own answer to the proposal.
        attrs["chars"] = ",".join(f"{o}:{k}" for o, k in text.live_chars_between(start, end))
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
        return canonical, _mark_inverse(block.id, p, [], [record])
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
    return canonical, _mark_inverse(block.id, p, removed, added)


def _mark_inverse(bid: str, path: str, removed: list[Mark], added: list[Mark]) -> list[OpBody]:
    """Off for each added mark, then on for each removed one with its placer.
    An added mark that is removed whole is named by its placer, so a split
    that cut it since does not stop the undo."""
    out: list[OpBody] = []
    for m in added:
        out.append(body(K.TEXT_MARK, {"bid": bid, "path": path},
                        {"kind": m.kind, "attrs": dict(m.attrs), "start": list(m.start), "end": list(m.end), "on": False,
                         **({"op": m.op} if m.kind in WHOLE and m.op else {})}))
    for m in removed:
        out.append(body(K.TEXT_MARK, {"bid": bid, "path": path},
                        {"kind": m.kind, "attrs": dict(m.attrs), "start": list(m.start), "end": list(m.end),
                         "on": True, "actor": m.actor, "op": m.op, "at": m.at}))
    return out


def _whole_mark_off(store: Store, op: Op, kind: str, attrs: dict, touched: set[str]):
    """Turn off a mark that is removed whole, named by the op that placed it:
    every piece of it, in every field that holds one now. Only one placement
    makes marks of that kind, attrs and placer, so these are its pieces."""
    placer = str(op.payload["op"])
    gone = [(b, f, m) for b, f, m in store.placed_by(placer) if m.kind == kind and m.attrs == attrs]
    if not gone:
        raise MarkError(f"no {kind} mark placed by {placer}")
    for b, _, _ in gone:
        _named(store, op, b)
    for b, f, m in gone:
        b.text(f).marks.remove(m)
        touched.add(b.id)
    b0, f0 = gone[0][0].id, gone[0][1]
    canonical = {"kind": kind, "attrs": attrs, "start": op.payload["start"], "end": op.payload["end"], "on": False,
                 "op": placer, "bid": b0, "path": f0, "removed": [m.to_json() for _, _, m in gone], "added": []}
    inverse = [x for b, f, m in gone for x in _mark_inverse(b.id, f, [m], [])]
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
    return [b.id for b in store.range(parent.id) if b.id not in moving][-1], clamp_level(parent.level + 1)


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
    # A builtin kind's layout is the calculus's. Any other kind's is what the
    # writer sent, or this process's registry for an add that sent none (an
    # old log, an old writer); the record keeps the one used, so a replay on a
    # clone without the kind reads the block the same way (I6).
    layout = layout_of(kind) if kind in BUILTIN or "layout" not in op.payload \
        else Layout.from_json(op.payload["layout"])
    given = _mapping(op.payload, "content")
    if LAYOUT_KEY in given:
        raise Conflict("reserved_path", LAYOUT_KEY)
    content = empty_content(layout, given)
    for f, tj in _mapping(op.payload, "texts").items():
        if f in layout.text:
            content[f] = Text.from_json(tj)
    # A pinned rank comes from a snapshot or a recorded inverse re-placing a
    # block exactly where it was; a fresh add derives one from its anchor.
    rank = _pinned_rank(op.payload.get("rank")) or store.rank_after(anchor)
    block = Block(bid, kind, rank, level, content, layout=layout, ranked_by=op.payload.get("ranked_by") or op.id,
                  added_by=op.payload.get("added_by") or op.actor,
                  added_plan=op.payload.get("added_plan") or op.plan or "",
                  void={str(k): list(v) for k, v in _mapping(op.payload, "void").items()},
                  prior={str(k): [list(e) for e in v] for k, v in _mapping(op.payload, "prior").items()})
    if op.payload.get("dead"):               # a snapshot restoring a block with its killer
        block.kill(op.payload["dead"], op.payload.get("dead_actor", ""), op.payload.get("dead_at", ""),
                   op.payload.get("dead_in", ""))
    if op.payload.get("detached"):
        block.detached = [dict(e) for e in op.payload["detached"]]
    # Everything is read; only now does the store change.
    store.add_block(block)
    if op.payload.get("bound_once"):
        store.bound_once.add(bid)        # a snapshot restoring a feature that once had code
    if op.payload.get("code_at") or op.payload.get("realized_at"):
        w = store.watermarks(bid)
        w.code_at = str(op.payload.get("code_at") or "")
        w.realized_at = str(op.payload.get("realized_at") or "")
    for f in layout.text:
        store.index_text(bid, f, content[f])
    touched.add(bid)
    canonical = {"kind": kind, "level": level, "after": after, "rank": block.rank,
                 "content": {k: v for k, v in content.items() if not isinstance(v, Text)}}
    if kind not in BUILTIN:
        canonical["layout"] = layout.to_json()
    # What a snapshot restored stays in the record, which the next fold replays.
    canonical.update({k: op.payload[k] for k in K.SNAPSHOT_ONLY[K.BLOCK_ADD] if op.payload.get(k)})
    return canonical, [body(K.BLOCK_DELETE, {"bid": bid}, {"ids": [bid]})]


def _mapping(payload: dict, key: str) -> dict:
    """``payload[key]`` when it is a JSON object, ``{}`` when absent."""
    v = payload.get(key) or {}
    if not isinstance(v, dict):
        raise Conflict("malformed", key)
    return v


def _pinned_rank(v) -> str:
    """A rank a payload pins, checked before anything changes, or ``""``."""
    if v and not R.valid(v):
        raise Conflict("malformed", "rank")
    return v or ""


def _h_block_set(store: Store, op: Op, touched: set[str]):
    bid, path = op.target["bid"], op.target["path"]
    b = store.blocks.get(bid)
    if b is None:
        raise Conflict("no_block", bid)
    if not b.live:
        raise Conflict("dead_block", bid)
    if path in RESERVED_PATHS:
        raise Conflict("reserved_path", path)
    value = op.payload["value"] if path in ("kind", "level") or not op.payload.get("unset") else None
    canonical: dict = {"value": value}
    if path == "kind":
        if not (is_one_line(value) and is_one_line(b.kind)):
            raise Conflict("kind_incompatible", f"{b.kind}->{value}")    # only heading and the prose kinds convert
        prev_kind, prev_level = b.kind, b.level
        # bullet <-> numbered keeps the depth; any other change starts at level 1 unless told.
        keep = prev_level if prev_kind in ITEMS and value in ITEMS else None
        level = level_for(value, op.payload.get("level") or keep)
        # The undo names the symbols this change took off; what each was bound
        # with comes from the block, as `block.restore` reads it, so no
        # payload, a plan's included, binds code the block never had or says
        # who set its hashes.
        named = _symbols(op.payload.get("bindings"))
        b.kind, b.level = value, level
        canonical.update({"prev": prev_kind, "prev_level": prev_level, "level": b.level, "_structural": True})
        back: dict = {"value": prev_kind, "level": prev_level}
        detached, _ = store.refeature(b, prev_kind, named)
        if detached:
            canonical["detached"] = detached
            back["bindings"] = [e["symbol"] for e in detached]
        elif named:
            canonical["bindings"] = named
        inverse = [body(K.BLOCK_SET, {"bid": bid, "path": "kind"}, back)]
    elif path == "level":
        if b.kind not in LEVELLED:
            raise Conflict("no_level", bid)
        prev = b.level
        b.level = clamp_level(value)
        canonical.update({"value": b.level, "prev": prev, "_structural": True})
        inverse = [body(K.BLOCK_SET, {"bid": bid, "path": "level"}, {"value": prev})]
    else:
        if path in b.layout.text:
            raise Conflict("text_field", f"use text ops for {path}")
        # A field the block did not have comes back absent, not null: the
        # inverse of setting it is `unset`.
        absent, prev = path not in b.content, b.content.get(path)
        if op.payload.get("unset"):
            b.content.pop(path, None)
            canonical = {"unset": True}
        else:
            b.content[path] = value
        canonical["prev"] = prev
        if is_human(op.actor) or op.plan:
            # The author's set, kept with what it replaced (`Block.prior`). A
            # realization settles a person's sets, and only the oldest run of
            # settled ones is dropped. A plan's set is never dropped, since a
            # verdict can be reopened and an accepted plan's value can become a
            # proposal again; nor is a person's set after it, where
            # `without_plans` must stop rather than walk on to the plan's. A
            # machine's untagged set (a lift) is code already written and asks
            # nothing.
            w = store.watermarks_by_fid.get(store.feature_of(bid) or "")
            settled = lambda e: not e[2] and bool(w and in_frontier(e[0], w.realized))   # noqa: E731
            b.prior[path] = [*dropwhile(settled, b.prior.get(path, [])), [op.id, prev, op.plan or ""]]
        inverse = [body(K.BLOCK_SET, {"bid": bid, "path": path}, {"unset": True} if absent else {"value": prev})]
    touched.add(bid)
    return canonical, inverse


def _symbols(v) -> list[str]:
    """The symbols a kind change's undo names: strings, or the ``symbol`` of
    each entry."""
    if v is not None and not isinstance(v, list):
        raise Conflict("malformed", "bindings")
    return [str(e.get("symbol") if isinstance(e, dict) else e) for e in v or [] if isinstance(e, (dict, str))]


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
    pinned = _pinned_rank(op.payload.get("rank"))
    was = text.ops()
    tail = text.split(at)
    head_live, tail_live = text.live_len() > 0, tail.live_len() > 0
    new_first = (forced == "head") if forced else ((not head_live) and tail_live)
    new_text, keep_text = (text, tail) if new_first else (tail, text)
    b.content[path] = keep_text
    new_kind = PARAGRAPH if b.kind == HEADING else b.kind
    new_level = b.level if new_kind in ITEMS else 0      # a list item splits into two at the same depth
    rank = pinned or (store.rank_before(b.id) if new_first else store.rank_after(b.id))
    if existing is not None:
        # A merge's inverse: the merged block comes back as it was, with its
        # own kind and level (a numbered item merged into a bullet stays numbered).
        # It comes back at its own place: a dead block keeps its rank, and a
        # verdict that moved it since (a rejected plan's move put back) stands.
        existing.revive()
        existing.content[path] = new_text
        nb = existing
    else:
        nb = Block(new, new_kind, rank, new_level, ranked_by=op.id, added_by=op.actor)
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
    # A heading never dies by merge (that would end a feature without a
    # delete), and prose never merges into a heading except to undo a heading
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
        # [id, rank, level, ranked_by]; a row without the last is ranked by this op.
        # A plan's restore is always ranked by itself: what it names would decide
        # whether its own reject puts the block back.
        rows = [(str(i), _pinned_rank(r), int(l), str(rb[0]) if rb and op.plan is None else op.id)
                for i, r, l, *rb in op.payload["restore"]]
        for i, r, *_ in rows:
            if i not in store.blocks:
                raise Conflict("no_block", i)
            if not r:
                raise Conflict("malformed", "rank")
        prev = [_place(store.blocks[i]) for i, *_ in rows]
        for i, *row in rows:
            b = store.blocks[i]
            r, l, rb = b.unvoid(*row)
            store.set_rank(i, r, rb)
            if b.kind == HEADING:
                b.level = clamp_level(l)
            touched.add(i)
        # `prev` as a plain move records it, so a verdict can put a plan's restore back too.
        return {"restore": [list(x) for x in rows], "prev": prev}, [body(K.BLOCK_MOVE, dict(op.target), {"restore": prev})]
    bid = op.target["bid"]
    b = store.blocks.get(bid)
    if b is None or not b.live:
        raise Conflict("no_block", bid)
    ids = [x.id for x in store.range(bid)]
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
    prev = [_place(store.blocks[i]) for i in ids]
    for i, r in zip(ids, store.ranks_after(anchor, len(ids), set(ids))):
        x = store.blocks[i]
        store.set_rank(i, r, op.id)
        if x.kind == HEADING and delta:
            x.level = clamp_level(x.level + delta)
        touched.add(i)
    canonical = {"after": after, "level_delta": delta, "range": ids, "prev": prev}
    return canonical, [body(K.BLOCK_MOVE, {"bid": bid}, {"restore": prev})]


def _place(b: Block) -> list:
    """Where a block is, as a move's ``prev`` and ``restore`` rows name it."""
    return [b.id, b.rank, b.level, b.ranked_by]


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
        ids = [x.id for x in store.body(bid)]
    killed = [i for i in ids if store.blocks[i].live]
    store.kill(killed, op.id, op.actor, op.at)
    touched.update(ids)
    detached = store.detach_dead([i for i in killed if store.blocks[i].kind == HEADING])
    canonical = {"ids": ids, "killed": killed, "detached": detached}
    return canonical, [body(K.BLOCK_RESTORE, dict(op.target), {"ids": killed, "by": op.id})]


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
    # What each block kept when it died (or a reopen made it a dead heading);
    # never a payload, so no restore binds code the block never had.
    store.reattach([e for i in revived for e in store.blocks[i].detached if not e.get("set")])
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
    key = p["symbol"]
    fp, th, by = p.get("fingerprint", ""), p.get("types_hash", ""), _set_by(op)
    prior = store.bindings.get(key)
    kept = store.kept(fid, key)
    # After the payload is read, not before: a malformed attach conflicts, and
    # a feature that never had code must not be remembered as one that lost it
    # (`reflect._emptied` proposes deleting exactly those).
    store.bound_once.add(fid)
    touched.add(fid)
    # The canonical says what the attach did, never what the payload claimed:
    # a verdict reads `prev_*` to undo a refresh rather than an attach, and
    # `kept`, what the block kept for the symbol from an earlier kind change,
    # to give the block that back if a kind change since took this binding off.
    sent = {k: v for k, v in p.items()
            if k not in ("displaced", "prev_fingerprint", "prev_types_hash", "prev_set_by", "kept")}
    if kept:
        sent["kept"] = kept
    if prior and prior.feature == fid:
        store.bindings[key] = replace(prior, fingerprint=fp, types_hash=th, set_by=by)
        canonical = {**sent, "displaced": None, "prev_fingerprint": prior.fingerprint,
                     "prev_types_hash": prior.types_hash, "prev_set_by": prior.set_by}
        _code_moved(store, fid, op, canonical)
        return canonical, [body(K.BIND_REFRESH, {"fid": fid}, _hashes(key, prior))]
    store.bindings[key] = Binding(fid, key, fp, th, by)
    canonical = {**sent, "displaced": prior.to_json() if prior else None}
    _code_moved(store, fid, op, canonical)
    inverse = [body(K.BIND_DETACH, {"fid": fid}, {"symbol": key})]
    if prior:
        touched.add(prior.feature)
        inverse.append(body(K.BIND_ATTACH, {"fid": prior.feature}, _hashes(key, prior)))
    return canonical, inverse


def _set_by(op: Op) -> str:
    """Who set a binding's hashes: the op's actor, or, for an op that restores
    a binding (an inverse, a snapshot), the actor its payload names, even an
    empty one, so a fold gives back exactly what the store held."""
    return op.payload["set_by"] if "set_by" in op.payload else op.actor


def _hashes(key: str, b: Binding) -> dict:
    """The payload that puts ``b``'s hashes back, with who set them."""
    return {"symbol": key, "fingerprint": b.fingerprint, "types_hash": b.types_hash, "set_by": b.set_by}


def _h_bind_detach(store: Store, op: Op, touched: set[str]):
    fid = op.target["fid"]
    key = op.payload["symbol"]
    prior = store.bindings.get(key)
    if prior is None or prior.feature != fid:
        raise Conflict("not_bound", f"{key} on {fid}")
    del store.bindings[key]
    touched.add(fid)
    canonical = {**op.payload, "fingerprint": prior.fingerprint, "types_hash": prior.types_hash}
    _code_moved(store, fid, op, canonical)
    return canonical, [body(K.BIND_ATTACH, {"fid": fid}, _hashes(key, prior))]


def _h_bind_refresh(store: Store, op: Op, touched: set[str]):
    fid = op.target["fid"]
    key = op.payload["symbol"]
    prior = store.bindings.get(key)
    if prior is None or prior.feature != fid:
        raise Conflict("not_bound", f"{key} on {fid}")
    store.bindings[key] = replace(prior, fingerprint=op.payload.get("fingerprint", ""),
                                  types_hash=op.payload.get("types_hash", ""), set_by=_set_by(op))
    touched.add(fid)
    canonical = {**op.payload, "prev_fingerprint": prior.fingerprint, "prev_types_hash": prior.types_hash}
    _code_moved(store, fid, op, canonical)
    return canonical, [body(K.BIND_REFRESH, {"fid": fid}, _hashes(key, prior))]


def _code_moved(store: Store, fid: str, op: Op, canonical: dict) -> None:
    """Drift's clock: every bind op that changed code the feature already had
    moves ``code_at`` (``ops.moved_code``)."""
    if K.moved_code(op.kind, canonical):
        store.watermarks(fid).code_at = max(store.watermarks(fid).code_at, op.at)


# ── plans ────────────────────────────────────────────────────────────────────

def _h_plan_open(store: Store, op: Op, touched: set[str]):
    pid = op.target["pid"]
    if pid in store.plans:
        raise Conflict("duplicate_plan", pid)
    kind = op.payload.get("kind", "edit")
    target = op.payload.get("target")
    if target is not None and not isinstance(target, str):
        raise Conflict("malformed", "target")
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


# ── watermarks ───────────────────────────────────────────────────────────────

def _frontier(v) -> dict:
    """A frontier from a payload, ``{session: seq}``, checked before anything
    changes."""
    if not isinstance(v, dict) or not all(isinstance(k, str) and type(n) is int for k, n in v.items()):
        raise Conflict("malformed", "frontier")
    return dict(v)


def _h_feature_handoff(store: Store, op: Op, touched: set[str]):
    fid = op.target["fid"]
    b = store.blocks.get(fid)
    if "realized" in op.payload and b is not None:
        return _restore_watermark(store, op, touched)
    if b is None or b.kind != HEADING:
        raise Conflict("no_feature", fid)
    w = store.watermarks(fid)
    prev = dict(w.handoff)
    touched.add(fid)
    if "restore" in op.payload:
        # The undo of a hand-off: the frontier goes back and the hand-off op
        # leaves `handoffs`. Its redo is a new hand-off asking the same.
        restore, undoes = _frontier(op.payload["restore"]), op.payload.get("undoes")
        if undoes is not None and not isinstance(undoes, str):
            raise Conflict("malformed", "undoes")
        asked = store.by_id.get(undoes) if undoes else None
        redo = {k: asked.payload[k] for k in ("asked", "delete_code") if asked is not None and asked.payload.get(k)}
        w.handoff = restore
        if undoes in w.handoffs:
            w.handoffs.remove(undoes)
        return {"restore": restore, "undoes": undoes, "prev": prev}, \
            [body(K.FEATURE_HANDOFF, {"fid": fid}, {"covers": prev, **redo})]
    covers = _frontier(op.payload.get("covers") or store.frontier)
    w.handoff = frontier_union(w.handoff, covers)
    w.handoffs.append(op.id)
    canonical = {"covers": covers, "asked": op.payload.get("asked", ""),
                 "delete_code": bool(op.payload.get("delete_code", False))}
    return canonical, [body(K.FEATURE_HANDOFF, {"fid": fid}, {"restore": prev, "undoes": op.id})]


def _restore_watermark(store: Store, op: Op, touched: set[str]):
    """A fold's hand-off (``realized`` is a field only a snapshot writes):
    the block's watermark as the fold found it. Both frontiers are set as
    given, and the op becomes the latest hand-off when ``covers`` is not
    empty, carrying that hand-off's words. A watermark belongs to the block
    and stays on it through a kind change, so the block may be of any kind,
    live or dead. The clocks are the ``block.add``'s to restore."""
    fid = op.target["fid"]
    covers, realized = _frontier(op.payload.get("covers") or {}), _frontier(op.payload["realized"])
    w = store.watermarks(fid)
    w.handoff, w.realized = covers, realized
    if covers:
        w.handoffs.append(op.id)
    touched.add(fid)
    return {"covers": covers, "realized": realized, "asked": op.payload.get("asked", ""),
            "delete_code": bool(op.payload.get("delete_code", False))}, []


def _h_feature_realized(store: Store, op: Op, touched: set[str]):
    fid = op.target["fid"]
    b = store.blocks.get(fid)
    if b is None or b.kind != HEADING:
        raise Conflict("no_feature", fid)
    w = store.watermarks(fid)
    if not w.queued:
        # Realization settles a request. With none there is nothing
        # to settle, and recording a no-op would report it as done.
        raise Conflict("nothing_queued", fid)
    covers = _frontier(op.payload.get("covers") or w.handoff)
    canonical = {"covers": covers, "sha": op.payload.get("sha", ""),
                 "files": list(op.payload.get("files") or []), "summary": op.payload.get("summary", "")}
    w.realized = frontier_union(w.realized, covers)
    w.realized_at = max(w.realized_at, op.at)
    touched.add(fid)
    return canonical, []


_HANDLERS = {
    K.TEXT_INSERT: _h_text_insert, K.TEXT_DELETE: _h_text_delete, K.TEXT_RESTORE: _h_text_restore,
    K.TEXT_MARK: _h_text_mark,
    K.BLOCK_ADD: _h_block_add, K.BLOCK_SET: _h_block_set, K.BLOCK_SPLIT: _h_block_split,
    K.BLOCK_MERGE: _h_block_merge, K.BLOCK_MOVE: _h_block_move, K.BLOCK_DELETE: _h_block_delete,
    K.BLOCK_RESTORE: _h_block_restore,
    K.BIND_ATTACH: _h_bind_attach, K.BIND_DETACH: _h_bind_detach, K.BIND_REFRESH: _h_bind_refresh,
    K.PLAN_OPEN: _h_plan_open, K.PLAN_ACCEPT: lambda s, o, t: verdict(s, o, t, accept=True),
    K.PLAN_REJECT: lambda s, o, t: verdict(s, o, t, accept=False),
    K.PLAN_REOPEN: reopen,
    K.FEATURE_HANDOFF: _h_feature_handoff, K.FEATURE_REALIZED: _h_feature_realized,
}
