"""The timeline window (spec §6 ``timeline``): the last ``limit`` ops with, per
op, the text of every block it touched as it read right after the op, and
each touched block's text as it read before the window. Derived by
un-applying the window backwards on clones of the live fields, so the
reconstruction is exact for text ops, splits and merges, and the editor's
scrubber never pushes old prose through the recorder. Each entry carries the
op's recorded inverse, which is what undo submits.
"""
from __future__ import annotations

from codoc.calculus import ops as K
from codoc.calculus.doc import text_fields
from codoc.calculus.ids import ref_from_json
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import END, START, Mark, Text

TEXT_KINDS = frozenset({K.TEXT_INSERT, K.TEXT_DELETE, K.TEXT_RESTORE, K.TEXT_MARK})


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

    entries: list[dict] = []
    for o in reversed(ops):
        touched = _touched(store, o)
        after = {bid: t.live_text() for bid, path in touched if (t := clone(bid, path)) is not None and path == "text"}
        entries.append({"pos": store.position[o.id], "id": o.id, "at": o.at, "actor": o.actor, "kind": o.kind,
                        "batch": o.batch, "plan": o.plan, "caused_by": o.caused_by, "blocks": after,
                        "inverse": list(o.inverse)})
        _unapply(o, clone)
    entries.reverse()
    before = {bid: t.live_text() for (bid, path), t in clones.items() if path == "text"}
    return {"start": (store.position[ops[0].id] if ops else store.pos), "head": store.pos, "before": before, "ops": entries}


def _touched(store: Store, o: Op) -> list[tuple[str, str]]:
    p, t = o.payload, o.target
    if o.kind in TEXT_KINDS:
        return [(p.get("bid") or t.get("bid"), p.get("path") or t.get("path", "text"))]
    if o.kind == K.BLOCK_SPLIT:
        return [(p.get("bid") or t["bid"], p.get("path", "text")), (p["new"], p.get("path", "text"))]
    if o.kind == K.BLOCK_MERGE:
        return [(t["into"], p.get("path", "text")), (t["bid"], p.get("path", "text"))]
    if o.kind == K.BLOCK_ADD:
        return [(t["bid"], f) for f in text_fields(p.get("kind", ""))]
    if o.kind in (K.BLOCK_DELETE, K.BLOCK_RESTORE):
        return [(i, "text") for i in p.get("killed" if o.kind == K.BLOCK_DELETE else "revived", []) if i in store.blocks]
    return []


def _unapply(o: Op, clone) -> None:
    """Reverse one op on the clones. Anything not listed leaves text alone."""
    p, t = o.payload, o.target
    if o.kind in TEXT_KINDS:
        text = clone(p.get("bid") or t.get("bid"), p.get("path") or t.get("path", "text"))
        if text is None:
            return
        if o.kind == K.TEXT_INSERT:
            text.kill_op(o.id, by="view")
        elif o.kind == K.TEXT_DELETE:
            text.revive([tuple(s) for s in p.get("segments", [])], by=o.id)
        elif o.kind == K.TEXT_RESTORE:
            # Re-kill as the op the restore reverted, so that op's own un-apply
            # (a revive by its id) finds them again further back.
            text.kill([tuple(s) for s in p.get("segments", [])], by=p.get("by") or "view")
        elif o.kind == K.TEXT_MARK:
            for mj in p.get("added", []):
                mk = Mark.from_json(mj)
                if mk in text.marks:
                    text.marks.remove(mk)
            for mj in p.get("removed", []):
                mk = Mark.from_json(mj)
                if text.holds(mk.start) and text.holds(mk.end) and mk not in text.marks:
                    text.marks.append(mk)
    elif o.kind == K.BLOCK_SPLIT:
        path = p.get("path", "text")
        head, tail = clone(p.get("bid") or t["bid"], path), clone(p["new"], path)
        if head is None or tail is None:
            return
        # The half that kept the text is `head` unless the new block came first.
        first, second = (tail, head) if p.get("new_first") else (head, tail)
        first.merge(second)
        if p.get("new_first"):
            head.segs, head.marks, tail.segs, tail.marks = tail.segs, tail.marks, [], []
    elif o.kind == K.BLOCK_MERGE:
        path = p.get("path", "text")
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
