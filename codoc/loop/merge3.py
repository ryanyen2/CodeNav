"""Three-way text merge — the resolution half of the divergence story.

Detecting that two parties edited the same feature (``loop_b._base_conflict``,
via ``Command.base_text``) stopped the silent overwrites, but it resolved every
disagreement the same way: refuse the incoming edit wholesale and park it as a
proposal. That is right when the two edits genuinely contend and no one has
authority, and wrong the rest of the time — an author fixing a typo in the first
paragraph while an agent rewrote the third was told their edit "conflicts",
even though the two never touched the same words.

This module separates the two questions the old boolean fused:

  1. *Do the edits overlap?*  A textual question, answered here by a diff3 over
     the common base. Disjoint edits merge — both land, nobody reviews anything.
  2. *Who wins where they do overlap?*  A question about authority, answered by
     rank (see :func:`codoc.model.event.outranks`). A person's edit outranks an
     agent's: the human is the author of intent, the agent is maintaining an
     index of it, and when the two disagree about the same sentence the human
     is not proposing — they are correcting.

The losing side is never destroyed. When an agent's text is superseded it stays
in the event ledger (``codoc history`` shows it, with actor and timestamp); when
the incoming side loses, the caller keeps it as a pending proposal. Proposals
exist for text that has no other home, which is why the two directions record
differently rather than symmetrically.

Everything here is pure: no store, no clock, no IO.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

# A change to the base, in BASE line coordinates: [start, end) replaced by `repl`.
# An insertion is zero-width (start == end).
_Change = tuple[int, int, list[str]]


@dataclass(frozen=True)
class Merge:
    """The result of merging one edit into another over their common base.

    ``text`` always resolves contended regions in favour of *incoming*; whether
    that resolution is legitimate is the caller's decision, made by consulting
    ``contended`` together with the two sides' ranks. Keeping the arbitration
    out here means the merge stays a pure textual question and the policy stays
    in one place instead of being smeared across both.
    """

    text: str
    contended: bool  # both sides rewrote the same base lines


def _changes(base: list[str], other: list[str]) -> list[_Change]:
    """Base-coordinate edits turning ``base`` into ``other``, ascending, disjoint.

    ``autojunk`` is off deliberately. Its heuristic drops lines that recur in
    more than 1% of a large input, and in a feature description the recurring
    line is the blank paragraph separator — treating those as junk makes the
    matcher align paragraphs across each other and report edits nobody made.
    """
    out: list[_Change] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, base, other, autojunk=False).get_opcodes():
        if tag != "equal":
            out.append((i1, i2, other[j1:j2]))
    return out


def _render(base: list[str], changes: list[_Change], lo: int, hi: int) -> list[str]:
    """One side's version of base lines ``[lo, hi)``, with ``changes`` applied.

    ``changes`` must be exactly the ones belonging to this span, ascending and
    disjoint — the caller partitions them during the sweep. Filtering by
    coordinates in here instead cannot work: an insertion is zero-width, so no
    interval test distinguishes "inserts at the start of this span" from "ends
    where this span begins", and the version that did dropped every insertion
    it was handed.
    """
    out: list[str] = []
    pos = lo
    for a, b, repl in changes:
        out.extend(base[pos:a])
        out.extend(repl)
        pos = b
    out.extend(base[pos:hi])
    return out


def merge3(base: str, current: str, incoming: str) -> Merge:
    """Merge ``incoming`` and ``current``, both derived from ``base``.

    Contended regions resolve to ``incoming``; ``Merge.contended`` reports that
    it happened so the caller can decide whether that outcome was earned.
    """
    if current == incoming:
        # Convergent: both sides arrived at the same text. This is not a
        # conflict and must not be reported as one — it is what a lagging
        # projection racing an echo of the author's own edit looks like.
        return Merge(text=incoming, contended=False)
    if current == base:
        return Merge(text=incoming, contended=False)
    if incoming == base:
        return Merge(text=current, contended=False)

    # split("\n") rather than splitlines(): it round-trips exactly through
    # "\n".join, so text that never hits a contended region comes back byte for
    # byte. splitlines() would silently eat a trailing newline and split on
    # \x0b/\x0c/ , rewriting prose the author never touched.
    b, c, i = base.split("\n"), current.split("\n"), incoming.split("\n")
    ch_c, ch_i = _changes(b, c), _changes(b, i)

    # One sweep over both sides' edits, clustering any that overlap in base
    # coordinates. Sorting by (start, end) puts a zero-width insertion at p
    # before a replacement starting at p, so an insertion *before* a rewritten
    # region reads as adjacent rather than contended — both land, in order.
    marked = sorted(
        [(a, e, 0, repl) for a, e, repl in ch_c] + [(a, e, 1, repl) for a, e, repl in ch_i],
        key=lambda x: (x[0], x[1]),
    )

    out: list[str] = []
    pos = 0
    contended = False
    idx = 0
    while idx < len(marked):
        lo, hi = marked[idx][0], marked[idx][1]
        cluster = [marked[idx]]
        idx += 1
        # Absorb every following edit that starts strictly inside the cluster.
        # `start == hi` is adjacency, not overlap: the two edits touch different
        # lines and can both be emitted, in order.
        while idx < len(marked) and marked[idx][0] < hi:
            hi = max(hi, marked[idx][1])
            cluster.append(marked[idx])
            idx += 1

        out.extend(b[pos:lo])
        sides = {side for _, _, side, _ in cluster}
        if len(sides) == 1:
            out.extend(_render(b, [(a, e, r) for a, e, _, r in cluster], lo, hi))
        else:
            mine = _render(b, [(a, e, r) for a, e, s, r in cluster if s == 1], lo, hi)
            theirs = _render(b, [(a, e, r) for a, e, s, r in cluster if s == 0], lo, hi)
            # Both sides edited these lines — but if they wrote the SAME thing
            # they agree, and calling that a conflict would send an author to a
            # review surface to arbitrate between two identical paragraphs.
            if mine != theirs:
                contended = True
            out.extend(mine)
        pos = hi

    out.extend(b[pos:])
    return Merge(text="\n".join(out), contended=contended)
