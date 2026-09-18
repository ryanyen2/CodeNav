"""The in-memory state the applier mutates (spec §5).

Everything here is a cache of the op log: ``replay(log)`` rebuilds it exactly
(I6). Persistence lives in ``codoc.substrate``; this module knows no files.

Indexes the applier and the views need, all maintained in ``record`` or by the
mutation that changes them:

- ``by_id``           op id → recorded op
- ``char_index``      minting op → the ``(block, field)`` holding its characters
- ``mark_index``      placing op → the ``(block, field)`` holding its marks
- ``order``           ``(rank, id)`` sorted, live and dead: a dead block keeps its
                      rank so "after a deleted block" still names a place
- ``rank_head``       block → pos of its last re-rank (a plan's move is not
                      undone over a later re-rank by someone else)
- ``ops_by_block``    block → positions of the ops recorded against it (the
                      canonical holders of an applied op; the client's hint
                      for a conflicted one), so every "ops on this feature"
                      view is O(ops on the feature), not O(log)
- ``block_head`` / ``structural_head`` / ``plans_head``   cache keys for the
                      derived views: a block's own head advances whenever an op
                      changes it, the structural head on any add/delete/move/
                      level change, the plans head on any plan or verdict. A
                      view keyed on these may be memoized (``substrate.publish``)
"""
from __future__ import annotations

import bisect
from collections.abc import Callable
from dataclasses import dataclass, field

from codoc.calculus import ops as K
from codoc.calculus import rank as R
from codoc.calculus.doc import HEADING, Block, in_flow, text_fields
from codoc.calculus.ids import CharRef, OpId
from codoc.calculus.ops import Op, frontier_union
from codoc.calculus.text import AnchorError, Text


@dataclass
class Binding:
    feature: str
    file: str
    symbol: str
    fingerprint: str = ""
    types_hash: str = ""

    def key(self) -> tuple[str, str]:
        return (self.file, self.symbol)

    def to_json(self) -> dict:
        return {"feature": self.feature, "file": self.file, "symbol": self.symbol,
                "fingerprint": self.fingerprint, "types_hash": self.types_hash}


@dataclass
class Plan:
    id: str
    actor: str
    kind: str = "edit"            # edit | add | move | delete
    target: str | None = None
    note: str = ""
    status: str = "open"          # open | accepted | rejected
    ops: list[OpId] = field(default_factory=list)   # the plan's ops in apply order
    opened_by: OpId | None = None

    def to_json(self) -> dict:
        return {"id": self.id, "actor": self.actor, "kind": self.kind, "target": self.target,
                "note": self.note, "status": self.status, "ops": list(self.ops), "opened_by": self.opened_by}


@dataclass
class Watermarks:
    handoff: dict = field(default_factory=dict)     # Frontier
    realized: dict = field(default_factory=dict)    # Frontier
    handoffs: list[OpId] = field(default_factory=list)   # hand-off op ids, oldest first


Policy = Callable[["Store", Op], str | None]   # (store, op) → denial reason, or None


class Store:
    def __init__(self, policy: Policy | None = None) -> None:
        self.blocks: dict[str, Block] = {}
        self.order: list[tuple[str, str]] = []
        self.bindings: dict[tuple[str, str], Binding] = {}
        self.plans: dict[str, Plan] = {}
        self.watermarks_by_fid: dict[str, Watermarks] = {}
        self.ops: list[Op] = []
        self.by_id: dict[OpId, Op] = {}
        self.position: dict[OpId, int] = {}       # op id → index in ``ops`` (apply order)
        self.batch_first: dict[str, int] = {}     # batch → the position of its first op
        self.frontier: dict[str, int] = {}
        self.char_index: dict[OpId, set[tuple[str, str]]] = {}
        self.mark_index: dict[OpId, set[tuple[str, str]]] = {}
        self.block_head: dict[str, int] = {}
        self.ops_by_block: dict[str, list[int]] = {}
        self.rank_head: dict[str, int] = {}
        self.structural_head: int = 0
        self.plans_head: int = 0
        self.policy: Policy | None = policy
        # Features that have ever had code bound to them. A feature that HAD code
        # and has none now is proposed for deletion; after a fold there is no
        # `bind.attach` op left to read, so it is state and not history.
        self.bound_once: set[str] = set()

    @property
    def pos(self) -> int:
        return len(self.ops)

    # ── blocks and order ───────────────────────────────────────────────────
    def add_block(self, block: Block) -> None:
        """Place a new block. A rank another block already holds is not reused:
        two clones that each appended after the same paragraph compute the same
        key, and the merge must still give them an order (a rank is derived;
        the position is the intent)."""
        if block.id in self.blocks:
            raise KeyError(block.id)
        i = bisect.bisect_left(self.order, (block.rank, ""))
        if i < len(self.order) and self.order[i][0] == block.rank:
            lo = self.order[i - 1][0] if i > 0 else ""
            block.rank = R.between(lo, block.rank)
        self.blocks[block.id] = block
        bisect.insort(self.order, (block.rank, block.id))
        self.rank_head[block.id] = self.pos + 1

    def set_rank(self, bid: str, rank: str) -> str:
        """Re-rank a block. A rank already held by another block is not
        reused: the nearest free rank below it is taken, since a rank is derived
        and the position is the intent (a restored rank may have been handed
        out again meanwhile). Returns the rank actually set."""
        b = self.blocks[bid]
        i = bisect.bisect_left(self.order, (b.rank, bid))
        if i < len(self.order) and self.order[i] == (b.rank, bid):
            del self.order[i]
        j = bisect.bisect_left(self.order, (rank, ""))
        if j < len(self.order) and self.order[j][0] == rank:
            lo = self.order[j - 1][0] if j > 0 else ""
            rank = R.between(lo, rank)
        b.rank = rank
        bisect.insort(self.order, (rank, bid))
        self.rank_head[bid] = self.pos + 1
        return rank

    def rank_after(self, after: str | None) -> str:
        """A fresh rank right after block ``after`` (live or dead), or at the
        start when ``None``. Appending at the very end uses ``append_after`` so
        tail keys stay short."""
        if after is None:
            hi = self.order[0][0] if self.order else ""
            return R.between("", hi) if hi else R.START
        b = self.blocks[after]
        i = bisect.bisect_right(self.order, (b.rank, after))
        if i >= len(self.order):
            return R.append_after(b.rank)
        return R.between(b.rank, self.order[i][0])

    def rank_before(self, bid: str) -> str:
        b = self.blocks[bid]
        i = bisect.bisect_left(self.order, (b.rank, bid))
        lo = self.order[i - 1][0] if i > 0 else ""
        return R.between(lo, b.rank)

    def ranks_after(self, after: str | None, n: int, exclude: set[str]) -> list[str]:
        """``n`` fresh ascending ranks after ``after`` (``None`` = the start),
        ignoring ``exclude`` (the blocks being moved) when finding the next
        neighbour."""
        lo = "" if after is None else self.blocks[after].rank
        i = 0 if after is None else bisect.bisect_right(self.order, (lo, after))
        while i < len(self.order) and self.order[i][1] in exclude:
            i += 1
        hi = self.order[i][0] if i < len(self.order) else ""
        out: list[str] = []
        cur = lo
        for _ in range(n):
            cur = R.between(cur, hi) if hi else R.append_after(cur)
            out.append(cur)
        return out

    def ordered(self) -> list[Block]:
        return [self.blocks[i] for _, i in self.order if self.blocks[i].live]

    def all_blocks(self) -> list[Block]:
        return [self.blocks[i] for _, i in self.order]

    def _index_of(self, bid: str) -> int:
        b = self.blocks[bid]
        return bisect.bisect_left(self.order, (b.rank, bid))

    def dominated_ids(self, bid: str) -> list[str]:
        """The live blocks ``bid`` dominates: itself, then for a heading every
        live block until the next live heading at the same or a shallower
        level. O(range plus interleaved dead blocks)."""
        b = self.blocks[bid]
        out = [bid]
        if b.kind != HEADING:
            return out
        for j in range(self._index_of(bid) + 1, len(self.order)):
            n = self.blocks[self.order[j][1]]
            if not n.live:
                continue
            if n.kind == HEADING and n.level <= b.level:
                break
            out.append(n.id)
        return out

    def body_ids(self, bid: str) -> list[str]:
        """A heading's own body: itself and the following live non-heading
        blocks. A paragraph's body is itself."""
        b = self.blocks[bid]
        out = [bid]
        if b.kind != HEADING:
            return out
        for j in range(self._index_of(bid) + 1, len(self.order)):
            n = self.blocks[self.order[j][1]]
            if not n.live:
                continue
            if n.kind == HEADING:
                break
            out.append(n.id)
        return out

    def feature_range(self, fid: str) -> list[Block]:
        h = self.blocks[fid]
        return [self.blocks[i] for i in self.dominated_ids(fid)] if h.live else [h]

    def feature_body(self, fid: str) -> list[Block]:
        h = self.blocks[fid]
        return [self.blocks[i] for i in self.body_ids(fid)] if h.live else [h]

    def feature_of(self, bid: str) -> str | None:
        """The live heading dominating ``bid`` (itself for a heading): the
        nearest preceding live heading, walking backwards."""
        b = self.blocks.get(bid)
        if b is None:
            return None
        if b.kind == HEADING:
            return bid if b.live else None
        for j in range(self._index_of(bid) - 1, -1, -1):
            n = self.blocks[self.order[j][1]]
            if n.live and n.kind == HEADING:
                return n.id
        return None

    def headings(self) -> list[Block]:
        return [b for b in self.ordered() if b.kind == HEADING]

    def live_neighbours_of(self, bid: str) -> tuple[str | None, str | None]:
        """The live blocks immediately before and after ``bid`` in the reading
        flow: a comment thread ranked between two paragraphs does not separate
        them, because nobody reads it there."""
        # By index, never by slicing `self.order`: these four walks are called
        # once per block by a cold publish, and an O(n) list copy each turns
        # that into O(n²) — 450 ms of it alone at 10,000 features.
        i = self._index_of(bid)
        flows = lambda j: (b := self.blocks[self.order[j][1]]).live and in_flow(b.kind)
        prev = next((self.order[j][1] for j in range(i - 1, -1, -1) if flows(j)), None)
        nxt = next((self.order[j][1] for j in range(i + 1, len(self.order)) if flows(j)), None)
        return prev, nxt

    # ── characters and marks ───────────────────────────────────────────────
    def index_text(self, bid: str, path: str, text: Text) -> None:
        for s in text.segs:
            self.char_index.setdefault(s.op, set()).add((bid, path))
        for m in text.marks:
            if m.op:
                self.mark_index.setdefault(m.op, set()).add((bid, path))

    def prune_index(self, bid: str, path: str, text: Text, was: set[OpId]) -> None:
        """After a split or merge moved characters out of ``(bid, path)``, drop
        the holders that no longer hold anything of an op. ``was`` is what the
        field held before the cut (``Text.ops()``), which is what keeps this
        O(the ops of one field) and not O(every op ever recorded)."""
        holder = (bid, path)
        chars = {s.op for s in text.segs}
        marks = {m.op for m in text.marks if m.op}
        for op in was:
            if op not in chars and (idx := self.char_index.get(op)) is not None:
                idx.discard(holder)
            if op not in marks and (idx := self.mark_index.get(op)) is not None:
                idx.discard(holder)

    def holders(self, op: OpId) -> list[tuple[Block, str]]:
        """Every ``(block, field)`` holding characters minted by ``op``."""
        return [(self.blocks[b], p) for b, p in sorted(self.char_index.get(op, ())) if b in self.blocks]

    def mark_holders(self, op: OpId) -> list[tuple[Block, str]]:
        return [(self.blocks[b], p) for b, p in sorted(self.mark_index.get(op, ())) if b in self.blocks]

    def locate_char(self, ref: CharRef, hint: tuple[str, str] | None = None) -> tuple[Block, str]:
        """The block and field holding ``ref`` (live or dead). A live holder is
        preferred over a dead one; the client's hint breaks ties. Raises
        ``AnchorError``."""
        cands: list[tuple[str, str]] = []
        if hint:
            cands.append(hint)
        cands.extend(c for c in sorted(self.char_index.get(ref[0], ())) if c != hint)
        found: list[tuple[Block, str]] = []
        for bid, path in cands:
            b = self.blocks.get(bid)
            if b is None:
                continue
            t = b.content.get(path)
            if isinstance(t, Text) and t.holds(ref):
                found.append((b, path))
        if not found:
            raise AnchorError(ref)
        live = [f for f in found if f[0].live]
        return (live or found)[0]

    # ── watermarks ─────────────────────────────────────────────────────────
    def watermarks(self, fid: str) -> Watermarks:
        return self.watermarks_by_fid.setdefault(fid, Watermarks())

    # ── record keeping ─────────────────────────────────────────────────────
    def record(self, op: Op, touched: set[str], structural: bool) -> None:
        self.ops.append(op)
        self.by_id[op.id] = op
        self.position[op.id] = len(self.ops) - 1
        if op.batch:
            self.batch_first.setdefault(op.batch, len(self.ops) - 1)
        session, _, seq = op.id.rpartition(":")
        self.frontier = frontier_union(self.frontier, {session: int(seq)})
        pos = len(self.ops) - 1
        for bid in touched:
            self.block_head[bid] = pos + 1
            self.ops_by_block.setdefault(bid, []).append(pos)
        if not touched:
            # An op that changed no block (a refused op, a verdict that only
            # confirmed) is still history for the block or plan target it named.
            pid = op.target.get("pid")
            plan_target = self.plans[pid].target if pid in self.plans else None
            for hint in (op.target.get("bid"), op.target.get("fid"), op.target.get("into"), plan_target):
                if hint:
                    self.ops_by_block.setdefault(hint, []).append(pos)
                    break
        if structural:
            self.structural_head = pos + 1
        if op.conflict is None and op.kind in K.PLAN_KINDS:
            # A plan's existence or status changes what the rejected read mode
            # hides and which proposals are offered, on blocks it never touched.
            # Open, accept, reject and reopen all move it; a `plan.open` that
            # conflicted does not, because nothing about the plans changed.
            self.plans_head = pos + 1

    def ops_on(self, bids) -> list[Op]:
        """The recorded ops on any of ``bids``, oldest first, each once."""
        seen: set[int] = set()
        for b in bids:
            seen.update(self.ops_by_block.get(b, ()))
        return [self.ops[i] for i in sorted(seen)]

    # ── equality for tests and snapshots ───────────────────────────────────
    def to_json(self) -> dict:
        return {
            "blocks": [b.to_json() for b in self.all_blocks()],
            "bindings": [b.to_json() for b in self.bindings.values()],
            "plans": [p.to_json() for p in self.plans.values()],
            "watermarks": {fid: {"handoff": w.handoff, "realized": w.realized, "handoffs": w.handoffs}
                           for fid, w in self.watermarks_by_fid.items()},
            "frontier": dict(self.frontier),
        }

    def state_fingerprint(self) -> dict:
        """The observable state: blocks with text and marks (marks in sequence
        order, so two logs applied in different orders compare equal), bindings,
        plans (ops as a set), watermarks. Positions and caches are excluded."""
        blocks = []
        for b in self.all_blocks():
            j = b.to_json()
            for f in text_fields(b.kind):
                j["content"][f]["marks"] = b.text(f).sorted_marks_json()
            blocks.append(j)
        return {
            "blocks": blocks,
            "bindings": sorted((list(k), v.to_json()) for k, v in self.bindings.items()),
            "plans": sorted((p.id, {**p.to_json(), "ops": sorted(p.ops)}) for p in self.plans.values()),
            "watermarks": {fid: {"handoff": w.handoff, "realized": w.realized}
                           for fid, w in self.watermarks_by_fid.items() if w.handoff or w.realized},
        }
