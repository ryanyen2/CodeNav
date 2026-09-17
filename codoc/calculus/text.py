"""``Text``: attributed characters with identity, and marks over them.

A Text is one sequence of *segments* in document order. A segment is a run of
characters inserted by one op (``(op, k0 .. k0+len)``), carrying that op's actor
and clock, an optional plan tag, and — once deleted — the op that deleted it.
Dead segments keep their place forever (spec §2): a character that was deleted
is still a position an insert, a split or a comment can name.

Every mutation is by character identity, never by offset, and a character
identity is held by exactly one Text at a time (a merge moves segments, it
does not copy them). Offsets exist only at the edges: ``ref_at`` /
``offset_of`` translate for the editor and for ``ingest``.

Two ways to name characters, and they are not interchangeable:

- a *range* ``start..end`` in sequence order, which is what a client means by
  "delete this" (whatever is live between here and there);
- a *segment* ``(op, k0, len)``, which is what the ledger records and what an
  undo or a verdict must act on, because later inserts may sit inside the
  range those characters once spanned. ``kill`` and ``revive`` take segments
  and touch nothing else.

Complexity: every operation is O(segments + marks) of this Text. Segments are
split lazily at the boundaries an op touches, so a paragraph typed in one
sitting stays one segment.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field, replace

from codoc.calculus.ids import END, START, CharRef, OpId

# Mark kinds. ``GROWS_AFTER`` kinds extend over text typed at their end (Peritext
# "after" expansion); every other kind is a fixed anchor (I2). ``EXCLUSIVE``
# kinds cannot overlap one another with different attrs; turning one on
# displaces the other. ``WHOLE`` kinds are toggled off only as an exact match —
# a half-resolved comment has no meaning. Every kind merges with an adjacent
# mark of the same kind and attrs.
BOLD, LINK, CITE, COMMENT, SUGGEST_DEL = "bold", "link", "cite", "comment", "suggest_del"
GROWS_AFTER = frozenset({BOLD})
EXCLUSIVE = frozenset({LINK, CITE})
WHOLE = frozenset({COMMENT, SUGGEST_DEL})

Segment = tuple[OpId, int, int]   # (op, k0, len)


class AnchorError(KeyError):
    """A character identity this Text does not hold (spec I5: report, never guess)."""


class MarkError(ValueError):
    """A mark toggle that is not allowed on this kind (partial removal of a WHOLE mark)."""


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


@dataclass(eq=False)
class Seg:
    op: OpId
    k0: int
    chars: str
    actor: str
    at: str
    plan: str | None = None
    dead: OpId | None = None

    @property
    def len(self) -> int:
        return len(self.chars)

    @property
    def live(self) -> bool:
        return self.dead is None

    def ref(self, i: int) -> CharRef:
        return (self.op, self.k0 + i)

    @property
    def first(self) -> CharRef:
        return (self.op, self.k0)

    @property
    def last(self) -> CharRef:
        return (self.op, self.k0 + len(self.chars) - 1)

    def to_json(self) -> dict:
        d = {"op": self.op, "k0": self.k0, "chars": self.chars, "actor": self.actor, "at": self.at}
        if self.plan:
            d["plan"] = self.plan
        if self.dead:
            d["dead"] = self.dead
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Seg":
        return cls(d["op"], d["k0"], d["chars"], d["actor"], d["at"], d.get("plan"), d.get("dead"))


@dataclass
class Mark:
    kind: str
    attrs: dict
    start: CharRef
    end: CharRef          # inclusive
    actor: str = ""       # who placed it: a person's bold is a Focus directive, the loop's is not
    op: OpId = ""         # the op that placed it; a verdict or an undo finds it by this

    def same(self, other: "Mark") -> bool:
        return self.kind == other.kind and self.attrs == other.attrs

    def piece(self, start: CharRef, end: CharRef) -> "Mark":
        return Mark(self.kind, dict(self.attrs), start, end, self.actor, self.op)

    def to_json(self) -> dict:
        d = {"kind": self.kind, "attrs": self.attrs, "start": list(self.start), "end": list(self.end)}
        if self.actor:
            d["actor"] = self.actor
        if self.op:
            d["op"] = self.op
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Mark":
        return cls(d["kind"], dict(d.get("attrs") or {}), tuple(d["start"]), tuple(d["end"]),
                   d.get("actor", ""), d.get("op", ""))


Pos = tuple[int, int]   # (segment index, offset); comparable


@dataclass
class Text:
    segs: list[Seg] = field(default_factory=list)
    marks: list[Mark] = field(default_factory=list)

    # ── reading ────────────────────────────────────────────────────────────
    def live_text(self) -> str:
        return "".join(s.chars for s in self.segs if s.live)

    def live_len(self) -> int:
        return sum(len(s.chars) for s in self.segs if s.live)

    def is_empty(self) -> bool:
        return self.live_len() == 0

    def locate(self, ref: CharRef) -> Pos:
        """Position of a character, live or dead. Raises ``AnchorError``."""
        op, k = ref
        for i, s in enumerate(self.segs):
            if s.op == op and s.k0 <= k < s.k0 + len(s.chars):
                return (i, k - s.k0)
        raise AnchorError(ref)

    def holds(self, ref: CharRef) -> bool:
        try:
            self.locate(ref)
            return True
        except AnchorError:
            return False

    def is_live(self, ref: CharRef) -> bool:
        i, _ = self.locate(ref)
        return self.segs[i].live

    def ref_at(self, offset: int) -> CharRef:
        """The live character at live offset ``offset``."""
        seen = 0
        for s in self.segs:
            if not s.live:
                continue
            if seen + len(s.chars) > offset:
                return s.ref(offset - seen)
            seen += len(s.chars)
        raise IndexError(offset)

    def anchor_before(self, offset: int) -> CharRef | str:
        return START if offset == 0 else self.ref_at(offset - 1)

    def offset_of(self, ref: CharRef) -> int | None:
        """Live offset of a character, or None when it is dead."""
        i, off = self.locate(ref)
        if not self.segs[i].live:
            return None
        return sum(len(s.chars) for s in self.segs[:i] if s.live) + off

    def first_ref(self) -> CharRef | None:
        return self.segs[0].first if self.segs else None

    def last_ref(self) -> CharRef | None:
        return self.segs[-1].last if self.segs else None

    def last_live_ref(self) -> CharRef | None:
        for s in reversed(self.segs):
            if s.live:
                return s.last
        return None

    def live_refs(self):
        for s in self.segs:
            if s.live:
                for i in range(len(s.chars)):
                    yield s.ref(i)

    def ops(self) -> set[OpId]:
        """Every op with a segment here (live or dead) or a mark here."""
        return {s.op for s in self.segs} | {m.op for m in self.marks if m.op}

    # ── segment surgery ────────────────────────────────────────────────────
    def _cut(self, i: int, off: int) -> None:
        """Ensure a segment boundary at offset ``off`` inside segment ``i``.
        Identity is untouched: both halves keep ``op`` with ``k0`` adjusted."""
        s = self.segs[i]
        if off <= 0 or off >= len(s.chars):
            return
        head = replace(s, chars=s.chars[:off])
        tail = replace(s, k0=s.k0 + off, chars=s.chars[off:])
        self.segs[i : i + 1] = [head, tail]

    def _coalesce(self) -> None:
        """Rejoin adjacent segments that are one run (same op, contiguous k, same
        state)."""
        out: list[Seg] = []
        for s in self.segs:
            if out:
                p = out[-1]
                if (p.op == s.op and p.k0 + len(p.chars) == s.k0 and p.dead == s.dead
                        and p.plan == s.plan and p.actor == s.actor and p.at == s.at):
                    out[-1] = replace(p, chars=p.chars + s.chars)
                    continue
            out.append(s)
        self.segs = out

    def _isolate(self, start: CharRef, end: CharRef) -> tuple[int, int]:
        """Cut so that ``start`` begins a segment and ``end`` ends one; return the
        inclusive segment index range."""
        si, so = self.locate(start)
        ei, eo = self.locate(end)
        if (ei, eo) < (si, so):
            raise AnchorError(("range", start, end))
        self._cut(si, so)
        si, _ = self.locate(start)
        ei, eo = self.locate(end)
        self._cut(ei, eo + 1)
        ei, _ = self.locate(end)
        return si, ei

    def _isolate_identity(self, op: OpId, k0: int, ln: int) -> list[int]:
        """Cut so that the characters ``(op, k0 .. k0+ln)`` present here are whole
        segments; return their indices in sequence order. Characters of that
        identity range not held here are ignored (they live in another Text)."""
        lo, hi = k0, k0 + ln
        i = 0
        while i < len(self.segs):
            s = self.segs[i]
            if s.op == op and s.k0 < hi and s.k0 + len(s.chars) > lo:
                if s.k0 < lo:
                    self._cut(i, lo - s.k0)
                    i += 1
                    s = self.segs[i]
                if s.k0 + len(s.chars) > hi:
                    self._cut(i, hi - s.k0)
            i += 1
        return [i for i, s in enumerate(self.segs) if s.op == op and lo <= s.k0 < hi]

    # ── mutations ──────────────────────────────────────────────────────────
    def insert(self, after: CharRef | str, chars: str, *, op: OpId, actor: str, at: str,
               plan: str | None = None) -> Seg:
        """Mint ``chars`` as one live segment after ``after`` (live or dead) or
        at START. Marks that grow after their end extend over the new segment;
        every other mark stays where it was (I2)."""
        if not chars:
            raise ValueError("insert of empty text")
        seg = Seg(op, 0, chars, actor, at, plan)
        if after == START:
            self.segs.insert(0, seg)
            return seg
        i, off = self.locate(after)  # type: ignore[arg-type]
        self._cut(i, off + 1)
        i, _ = self.locate(after)  # type: ignore[arg-type]
        self.segs.insert(i + 1, seg)
        for m in self.marks:
            if m.kind in GROWS_AFTER and m.end == after:
                m.end = seg.last
        return seg

    def delete(self, start: CharRef, end: CharRef, *, by: OpId) -> list[Segment]:
        """Kill every live character from ``start`` to ``end`` inclusive, in
        sequence order. Returns the canonical segments it killed."""
        si, ei = self._isolate(start, end)
        killed: list[Segment] = []
        for i in range(si, ei + 1):
            s = self.segs[i]
            if s.live:
                self.segs[i] = replace(s, dead=by)
                killed.append((s.op, s.k0, len(s.chars)))
        self._coalesce()
        return killed

    def kill(self, segments: list[Segment], *, by: OpId) -> list[Segment]:
        """Kill exactly the named characters that are live here; nothing that
        sits between them. Returns what it actually killed."""
        killed: list[Segment] = []
        for op, k0, ln in segments:
            for i in self._isolate_identity(op, k0, ln):
                s = self.segs[i]
                if s.live:
                    self.segs[i] = replace(s, dead=by)
                    killed.append((s.op, s.k0, len(s.chars)))
        self._coalesce()
        return killed

    def revive(self, segments: list[Segment], *, by: OpId | None = None) -> list[Segment]:
        """Bring the named dead characters back to life. With ``by`` given, only
        characters killed by that op (an undo never resurrects what a later op
        killed). Returns what it revived."""
        revived: list[Segment] = []
        for op, k0, ln in segments:
            for i in self._isolate_identity(op, k0, ln):
                s = self.segs[i]
                if not s.live and (by is None or s.dead == by):
                    self.segs[i] = replace(s, dead=None)
                    revived.append((s.op, s.k0, len(s.chars)))
        self._coalesce()
        return revived

    def kill_op(self, op: OpId, *, by: OpId) -> list[Segment]:
        """Kill every live character ``op`` minted here (a rejected plan insert)."""
        segs = [(s.op, s.k0, len(s.chars)) for s in self.segs if s.op == op and s.live]
        return self.kill(segs, by=by) if segs else []

    # ── marks ──────────────────────────────────────────────────────────────
    def set_mark(self, kind: str, attrs: dict, start: CharRef, end: CharRef, on: bool,
                 *, actor: str = "", op: OpId = "") -> tuple[list[Mark], list[Mark]]:
        """Toggle a mark over an inclusive identity range. Returns
        ``(removed, added)``, which is exactly the inverse."""
        self.locate(start)
        self.locate(end)
        if self._before(end, start):
            raise AnchorError(("range", start, end))
        new = Mark(kind, dict(attrs), start, end, actor, op)
        removed: list[Mark] = []
        added: list[Mark] = []
        if on:
            if kind in WHOLE:
                for m in self.marks:
                    if m.same(new) and m.start == start and m.end == end:
                        return [], []   # idempotent
                self.marks.append(new)
                return [], [new]
            if kind in EXCLUSIVE:
                for m in list(self.marks):
                    if m.kind in EXCLUSIVE and not m.same(new) and self._overlaps(m, new):
                        self.marks.remove(m)
                        removed.append(m)
                        pieces = self._cut_out(m, new)
                        self.marks.extend(pieces)
                        added.extend(pieces)
            lo, hi = new.start, new.end
            for m in list(self.marks):
                if m.same(new) and (self._overlaps(m, new) or self._adjacent(m, new)):
                    self.marks.remove(m)
                    removed.append(m)
                    lo = m.start if self._before(m.start, lo) else lo
                    hi = m.end if self._before(hi, m.end) else hi
            new = Mark(kind, dict(attrs), lo, hi, actor, op)
            self.marks.append(new)
            added.append(new)
            return removed, added
        if kind in WHOLE:
            for m in list(self.marks):
                if m.same(new) and m.start == start and m.end == end:
                    self.marks.remove(m)
                    return [m], []
            raise MarkError(f"{kind} marks are removed whole; no exact match for {start}..{end}")
        for m in list(self.marks):
            if m.same(new) and self._overlaps(m, new):
                self.marks.remove(m)
                removed.append(m)
                pieces = self._cut_out(m, new)
                self.marks.extend(pieces)
                added.extend(pieces)
        return removed, added

    def remove_marks(self, *, op: OpId | None = None, plan: str | None = None) -> list[Mark]:
        """Remove marks by the op that placed them, or every ``suggest_del`` of a
        plan. Returns the removed marks (the inverse re-adds them)."""
        out = []
        for m in list(self.marks):
            if (op is not None and m.op == op) or (plan is not None and m.kind == SUGGEST_DEL
                                                     and m.attrs.get("plan") == plan):
                self.marks.remove(m)
                out.append(m)
        return out

    def marks_of(self, op: OpId) -> list[Mark]:
        return [m for m in self.marks if m.op == op]

    def _before(self, a: CharRef, b: CharRef) -> bool:
        return self.locate(a) < self.locate(b)

    def _overlaps(self, m: Mark, n: Mark) -> bool:
        return not (self._before(m.end, n.start) or self._before(n.end, m.start))

    def _adjacent(self, m: Mark, n: Mark) -> bool:
        """No live character strictly between them, in either order."""
        return self._next_live(m.end) == self._first_live_from(n.start) or \
            self._next_live(n.end) == self._first_live_from(m.start)

    def _first_live_from(self, ref: CharRef) -> CharRef | None:
        p = self.locate(ref)
        while p is not None:
            if self.segs[p[0]].live:
                return self._ref_at_pos(p)
            p = self._next_pos(p)
        return None

    def _next_live(self, ref: CharRef) -> CharRef | None:
        p = self._next_pos(self.locate(ref))
        return self._first_live_from(self._ref_at_pos(p)) if p is not None else None

    def _next_pos(self, p: Pos) -> Pos | None:
        i, off = p
        if off + 1 < len(self.segs[i].chars):
            return (i, off + 1)
        return (i + 1, 0) if i + 1 < len(self.segs) else None

    def _prev_pos(self, p: Pos) -> Pos | None:
        i, off = p
        if off > 0:
            return (i, off - 1)
        return (i - 1, len(self.segs[i - 1].chars) - 1) if i > 0 else None

    def _ref_at_pos(self, p: Pos) -> CharRef:
        return self.segs[p[0]].ref(p[1])

    def _cut_out(self, m: Mark, hole: Mark) -> list[Mark]:
        """The pieces of ``m`` outside ``hole`` (0, 1 or 2 marks)."""
        out: list[Mark] = []
        if self._before(m.start, hole.start):
            p = self._prev_pos(self.locate(hole.start))
            assert p is not None
            out.append(m.piece(m.start, self._ref_at_pos(p)))
        if self._before(hole.end, m.end):
            p = self._next_pos(self.locate(hole.end))
            assert p is not None
            out.append(m.piece(self._ref_at_pos(p), m.end))
        return out

    # ── structure ──────────────────────────────────────────────────────────
    def split(self, at: CharRef | str) -> "Text":
        """Split after ``at`` (``START`` = before everything). ``self`` keeps the
        head; the tail is returned. Marks spanning the cut become two marks."""
        if at == START:
            cut = 0
        else:
            i, off = self.locate(at)  # type: ignore[arg-type]
            self._cut(i, off + 1)
            i, _ = self.locate(at)  # type: ignore[arg-type]
            cut = i + 1
        tail = Text(self.segs[cut:], [])
        self.segs = self.segs[:cut]
        head_last = self.last_ref()
        tail_first = tail.first_ref()
        keep: list[Mark] = []
        for m in self.marks:
            hs, he = self.holds(m.start), self.holds(m.end)
            if hs and he:
                keep.append(m)
            elif not hs and not he:
                tail.marks.append(m)
            else:
                assert head_last is not None and tail_first is not None
                keep.append(m.piece(m.start, head_last))
                tail.marks.append(m.piece(tail_first, m.end))
        self.marks = keep
        return tail

    def merge(self, tail: "Text") -> CharRef | str:
        """Move ``tail``'s segments and marks onto the end of ``self``; ``tail``
        is left empty (one holder per character). Returns the split point that
        inverts it: the last character of the head, or END if the head was
        empty. Adjacent same-kind marks across the seam are rejoined."""
        at: CharRef | str = self.last_ref() or END
        seam_head, seam_tail = self.last_ref(), tail.first_ref()
        self.segs.extend(tail.segs)
        for tm in tail.marks:
            if seam_head is not None and tm.start == seam_tail:
                hm = next((h for h in self.marks if h.same(tm) and h.end == seam_head), None)
                if hm is not None:
                    hm.end = tm.end
                    continue
            self.marks.append(tm)
        tail.segs, tail.marks = [], []
        self._coalesce()
        return at

    # ── serialization ──────────────────────────────────────────────────────
    def to_json(self) -> dict:
        return {"segs": [s.to_json() for s in self.segs], "marks": [m.to_json() for m in self.marks]}

    @classmethod
    def from_json(cls, d: dict) -> "Text":
        return cls([Seg.from_json(s) for s in d.get("segs", [])], [Mark.from_json(m) for m in d.get("marks", [])])

    def clone(self) -> "Text":
        return Text([replace(s) for s in self.segs], [m.piece(m.start, m.end) for m in self.marks])

    def sorted_marks_json(self) -> list[dict]:
        """Marks in sequence order (for order-insensitive equality)."""
        return [m.to_json() for m in sorted(self.marks, key=lambda m: (self.locate(m.start), m.kind, sorted(m.attrs.items())))]
