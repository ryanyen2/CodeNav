"""The timeline window (spec §6 ``timeline``): the last ``limit`` ops with, per
op, the text of every block it touched as it read right after the op, and
each touched block's text as it read before the window. Derived by
un-applying the window backwards on clones of the live fields, so the
reconstruction is exact — a fuzz checks it against a prefix replay — and the
editor's scrubber never pushes old prose through the recorder. Each entry
carries the op's recorded inverse, which is what undo submits.

Characters are found **by identity, in the clones**, never by the block an op
named: a split or a merge since then moved them, and the walk has already put
them back where they were. Today's store answers only when no clone holds
them. That is also what makes the un-apply unconditional: by the time an op is
reversed, every later effect on its characters is already gone, so whatever
state they are in is the one this op left.

A block born inside the window is absent from ``before`` and named in ``born``,
so a reader never shows it before it existed. Three more facts are folded
forward by the reader the same way ``before`` is — the window says what was
true entering it and what each op changed:

- ``dead`` and each entry's ``live``: which blocks were not on the page. A
  reader without this shows a deleted paragraph on every page after its
  deletion, and a merged-away one as an empty phantom, because their text is
  still in the store.
- ``ranks``: per block, the rank it held ENTERING the window, folded back from
  each op that displaced one — a move, or a verdict that undid one, including a
  ``plan.reopen``, which applies the verdict's own record and so reports what
  it displaced separately (``undid``). Each ENTRY's ``ranks`` is the same kind
  of row for that op alone, read forward like its ``blocks``: what the page
  looked like once it had applied. What never moved keeps today's rank, which
  is what it had; a reader folds the two together.

Each entry carries ``batch_first``: where its batch began, so an undo can tell
a batch it holds whole from one that reaches back past the window.
"""
from __future__ import annotations

from codoc.calculus import ops as K
from codoc.calculus.doc import text_fields
from codoc.calculus.ids import END, ref_from_json
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import START, Mark, Segment, Text

VERDICTS = frozenset({K.PLAN_ACCEPT, K.PLAN_REJECT, K.PLAN_REOPEN})
WHOLE_OP = -1          # a character range standing for "everything this op minted"


def _main_field(store: Store, bid: str) -> str | None:
    """The Text field the window reports for a block: ``text`` for a heading
    or prose block, ``source`` for a table, diagram or snippet, the first
    declared one for a plugin kind."""
    b = store.blocks.get(bid)
    fields = text_fields(b.kind) if b is not None else ()
    return fields[0] if fields else None


def history_window(store: Store, limit: int = 200) -> dict:
    ops = [o for o in store.ops[-limit:] if o.conflict is None]
    clones: dict[tuple[str, str], Text] = {}

    def clone(bid: str, path: str) -> Text | None:
        key = (bid, path)
        if key not in clones:
            b = store.blocks.get(bid)
            if b is None or not isinstance(b.content.get(path), Text):
                return None
            clones[key] = b.content[path].clone()
        return clones[key]

    def holders(path: str, chars: list[Segment], default: str | None = None) -> list[tuple[str, str]]:
        """Every field holding any of these characters as the walk has them
        now: the clones first, today's store when none of them does, and the
        block the op named when the store has lost them too."""
        hit = [k for k, t in clones.items() if k[1] == path and _holds_any(t, chars)]
        if not hit:
            for op, _, _ in chars:
                for b, p in store.holders(op):
                    if p == path and (b.id, p) not in hit:
                        hit.append((b.id, p))
        if not hit and default and default in store.blocks:
            hit.append((default, path))
        return hit

    def touched(o: Op) -> list[tuple[str, str]]:
        p, t = o.payload, o.target
        path = p.get("path") or t.get("path", "text")
        bid = p.get("bid") or t.get("bid")
        if o.kind in (K.TEXT_DELETE, K.TEXT_RESTORE) and p.get("segments"):
            return holders(path, _segs(p["segments"]), bid)
        if o.kind == K.TEXT_INSERT:
            return holders(path, [(o.id, 0, WHOLE_OP)], bid)
        if o.kind == K.TEXT_MARK:
            return holders(path, [_one(m["start"]) for m in p.get("added", []) + p.get("removed", [])], bid)
        if o.kind in (K.TEXT_DELETE, K.TEXT_RESTORE):
            return [(bid, path)] if bid in store.blocks else []
        if o.kind == K.BLOCK_SPLIT:
            return [(bid or t["bid"], path), (p["new"], path)]
        if o.kind == K.BLOCK_MERGE:
            return [(t["into"], path), (t["bid"], path)]
        if o.kind == K.BLOCK_ADD:
            return [(t["bid"], f) for f in text_fields(p.get("kind", ""))]
        if o.kind in (K.BLOCK_DELETE, K.BLOCK_RESTORE):
            key = "killed" if o.kind == K.BLOCK_DELETE else "revived"
            return [(i, f) for i in p.get(key, []) if (f := _main_field(store, i))]
        if o.kind in VERDICTS:
            out: list[tuple[str, str]] = []
            for e in _verdict_entries(p):
                for h in holders(e.get("path", "text"), _segs(e["segments"]), e.get("bid")):
                    if h not in out:
                        out.append(h)
            return out
        return []

    def unapply(o: Op) -> None:
        """Reverse one op on the clones. Anything not listed leaves text alone."""
        p, t = o.payload, o.target
        path = p.get("path") or t.get("path", "text")
        if o.kind in (K.TEXT_DELETE, K.TEXT_RESTORE) and p.get("segments"):
            segs = _segs(p["segments"])
            for text in _each(clone, touched(o)):
                text.revive(segs) if o.kind == K.TEXT_DELETE else text.kill(segs, by="view")
        elif o.kind == K.TEXT_INSERT:
            for text in _each(clone, touched(o)):
                text.kill_op(o.id, by="view")
        elif o.kind == K.TEXT_MARK:
            for mj in p.get("added", []):
                mk = Mark.from_json(mj)
                for text in _each(clone, holders(path, [_one(mj["start"])], p.get("bid") or t.get("bid"))):
                    if mk in text.marks:
                        text.marks.remove(mk)
            for mj in p.get("removed", []):
                mk = Mark.from_json(mj)
                for text in _each(clone, holders(path, [_one(mj["start"])], p.get("bid") or t.get("bid"))):
                    if mk not in text.marks and text.holds(mk.start) and text.holds(mk.end):
                        text.marks.append(mk)
        elif o.kind == K.BLOCK_SPLIT:
            head, tail = clone(p.get("bid") or t["bid"], path), clone(p["new"], path)
            if head is None or tail is None:
                return
            # The half that kept the text is `head` unless the new block came first.
            first, second = (tail, head) if p.get("new_first") else (head, tail)
            first.merge(second)
            if p.get("new_first"):
                head.segs, head.marks, tail.segs, tail.marks = tail.segs, tail.marks, [], []
        elif o.kind == K.BLOCK_MERGE:
            into, dead = clone(t["into"], path), clone(t["bid"], path)
            if into is None or dead is None:
                return
            at = ref_from_json(p["at"]) if "at" in p else END
            if p.get("order") == "before":
                # The dying block's words were put in front: they are the head.
                tail = into.split(at if at != END else START)
                dead.segs, dead.marks, into.segs, into.marks = into.segs, into.marks, tail.segs, tail.marks
            else:
                tail = into.split(START if at == END else at)
                dead.segs, dead.marks = tail.segs, tail.marks
        elif o.kind in VERDICTS:
            # An accept tombstones the words it struck and the ops it did not take;
            # a reject tombstones the whole proposal; a reopen brings them back.
            revive = o.kind != K.PLAN_REOPEN
            for e in _verdict_entries(p):
                segs = _segs(e["segments"])
                for text in _each(clone, holders(e.get("path", "text"), segs, e.get("bid"))):
                    text.revive(segs) if revive else text.kill(segs, by="view")

    entries: list[dict] = []
    born: set[str] = set()
    dead = {b.id for b in store.blocks.values() if not b.live}
    rank = {b.id: b.rank for b in store.blocks.values()}
    shape = {b.id: {"kind": b.kind, "level": b.level} for b in store.blocks.values()}
    for o in reversed(ops):
        # A block born after this op did not exist then, and `born` holds
        # exactly those, since we are walking newest first.
        # A block that was not on the page then read as nothing, whatever text
        # the store still holds for it: a deleted paragraph keeps its words so
        # an anchor can still name them, and a reader that showed them would
        # put the paragraph back on every page after its deletion.
        after = {bid: ("" if bid in dead else t.live_text()) for bid, path in touched(o)
                 if bid not in born and path == _main_field(store, bid) and (t := clone(bid, path)) is not None}
        gone, back = _liveness(o)
        moved = _rank_rows(o)
        entries.append({"pos": store.position[o.id], "id": o.id, "at": o.at, "actor": o.actor, "kind": o.kind,
                        "batch": o.batch, "batch_first": store.batch_first.get(o.batch, store.position[o.id]),
                        "plan": o.plan, "caused_by": o.caused_by, "blocks": after, "inverse": list(o.inverse),
                        "live": {**{i: False for i in gone}, **{i: True for i in back}},
                        "ranks": {i: rank[i] for i, _ in moved if i in rank},
                        "shape": {i: dict(shape[i]) for i, _ in _shape_rows(o) if i in shape}})
        unapply(o)
        dead.difference_update(gone)
        dead.update(back)
        rank.update(moved)
        for i, was in _shape_rows(o):
            if i in shape:
                shape[i].update(was)
        if o.kind == K.BLOCK_ADD:
            born.add(o.target["bid"])
        elif o.kind == K.BLOCK_SPLIT and not o.payload.get("new_takes"):
            born.add(o.payload["new"])
    entries.reverse()
    before = {bid: ("" if bid in dead else t.live_text()) for (bid, path), t in clones.items()
              if path == _main_field(store, bid) and bid not in born}
    named = {bid for bid, _ in clones} | born | {i for e in entries for i in e["live"]} | {i for e in entries for i in e["ranks"]}
    return {"start": (store.position[ops[0].id] if ops else store.pos), "head": store.pos, "before": before,
            "born": sorted(born), "dead": sorted(dead & named),
            "ranks": {b: r for b, r in rank.items() if b in named},
            # A block the window names but the projection does not — one deleted
            # inside it, or one whose kind has changed since — has nowhere else
            # to say what it was. Folded forward like the ranks.
            "shape": {b: shape[b] for b in sorted(named) if b in shape},
            "ops": entries}


def _liveness(o: Op) -> tuple[list[str], list[str]]:
    """The blocks this op took off the page, and the blocks it put back."""
    p = o.payload
    if o.kind == K.BLOCK_DELETE:
        return [str(i) for i in p.get("killed", [])], []
    if o.kind == K.BLOCK_RESTORE:
        return [], [str(i) for i in p.get("revived", [])]
    if o.kind == K.BLOCK_MERGE:
        return [str(o.target.get("bid"))], []
    if o.kind == K.BLOCK_SPLIT and p.get("new_takes"):
        return [], [str(p.get("new"))]              # a merge's inverse brings the dead block back
    struck = [str(i) for i in list(p.get("deleted_blocks", [])) + list(p.get("blocks", []))]
    if o.kind in (K.PLAN_ACCEPT, K.PLAN_REJECT):
        return struck, []
    if o.kind == K.PLAN_REOPEN:
        return [], struck
    return [], []


def _displaced(o: Op) -> dict:
    """What a verdict's rows record. A reject or accept records what it
    DISPLACED; a reopen re-applies that same record, so its own rows are what
    it SET and the thing a reader of the page before it needs is in ``undid``."""
    return (o.payload.get("undid") or {}) if o.kind == K.PLAN_REOPEN else o.payload


def _shape_rows(o: Op) -> list[tuple[str, dict]]:
    """(block, what its kind and level were before this op). Only ``block.set``
    and a verdict undoing one change either."""
    p = o.payload
    if o.kind == K.BLOCK_SET and o.target.get("path") == "kind":
        return [(str(o.target["bid"]), {"kind": str(p.get("prev")), "level": int(p.get("prev_level") or 0)})]
    if o.kind == K.BLOCK_SET and o.target.get("path") == "level":
        return [(str(o.target["bid"]), {"level": int(p.get("prev") or 0)})]
    if o.kind in VERDICTS:
        out: list[tuple[str, dict]] = []
        for e in _displaced(o).get("sets", []):
            if e.get("path") == "kind":
                out.append((str(e["bid"]), {"kind": str(e["value"]), "level": int(e.get("level") or 0)}))
            elif e.get("path") == "level":
                out.append((str(e["bid"]), {"level": int(e["value"] or 0)}))
        return out
    return []


def _rank_rows(o: Op) -> list[tuple[str, str]]:
    """(block, the rank it held before this op). A move records what it
    displaced as ``prev`` (or, when it is itself a restore, in its inverse); a
    verdict that undoes a planned move records the same thing as ``moved``,
    and a page before it is in the order it undid."""
    if o.kind == K.BLOCK_MOVE:
        rows = o.payload.get("prev")
        if rows is None and o.inverse:
            rows = (o.inverse[0].get("payload") or {}).get("restore")
    elif o.kind in VERDICTS:
        rows = _displaced(o).get("moved")
    else:
        return []
    return [(str(r[0]), str(r[1])) for r in (rows or [])]


def _segs(segments) -> list[Segment]:
    return [(str(s[0]), int(s[1]), int(s[2])) for s in segments or ()]


def _one(ref) -> Segment:
    """A single character, as a one-long segment."""
    return (str(ref[0]), int(ref[1]), 1)


def _holds_any(t: Text, chars: list[Segment]) -> bool:
    return any(s.op == op and (ln == WHOLE_OP or (s.k0 < k0 + ln and s.k0 + len(s.chars) > k0))
               for s in t.segs for op, k0, ln in chars)


def _each(clone, keys):
    for bid, path in keys:
        text = clone(bid, path)
        if text is not None:
            yield text


def _verdict_entries(payload: dict) -> list[dict]:
    """The records a verdict (or its inverse) made that moved text: segments it
    tombstoned when it struck or undid, or revived when it reopened."""
    return [e for e in payload.get("killed", []) + payload.get("struck", []) if e.get("segments")]
