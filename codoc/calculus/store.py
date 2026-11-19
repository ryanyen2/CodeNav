"""The in-memory state the applier mutates (docs/model.md, Definitions).

Everything here is a cache of the op log: ``replay(log)`` rebuilds it exactly
(I6). Persistence lives in ``codoc.workspace``; this module knows no files.

Indexes the applier and the views need, all maintained in ``record`` or by the
mutation that changes them:

- ``by_id``           op id → recorded op
- ``char_index``      minting op → the ``(block, field)`` holding its characters
- ``mark_index``      placing op → the ``(block, field)`` holding its marks
- ``order``           ``(rank, id)`` sorted, live and dead: a dead block keeps its
                      rank so "after a deleted block" still names a place
- ``ops_by_block``    block → positions of the ops recorded against it (the
                      canonical holders of an applied op; the client's hint
                      for a conflicted one), so every "ops on this feature"
                      view is O(ops on the feature), not O(log)
- ``block_head`` / ``structural_head`` / ``plans_head``   cache keys for the
                      derived views: a block's own head advances whenever an op
                      changes it, the structural head on any add/delete/move/
                      level change, the plans head on any plan or verdict. A
                      view keyed on these may be memoized (``daemon.publish``)
"""
from __future__ import annotations

import bisect
import copy
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from codoc.calculus import ops as K
from codoc.calculus import rank as R
from codoc.calculus.doc import HEADING, Block
from codoc.calculus.ids import CharRef, OpId, is_human
from codoc.calculus.ops import Op, covered, frontier_union
from codoc.calculus.text import AnchorError, Mark, Text


def file_of(symbol: str) -> str:
    """The file a symbol names: ``f.py`` for ``f.py::g`` and for ``f.py``."""
    return symbol.split("::", 1)[0]


@dataclass
class Binding:
    feature: str
    symbol: str             # the key: ``file::symbol_path``, as the index names a chunk
    fingerprint: str = ""
    types_hash: str = ""
    # The actor whose op set these hashes. The reflect pass leaves a chunk
    # alone only when the loop set the hashes it now has: then the loop has
    # already described that code. An agent's or a person's attach says the code
    # is theirs, not that the prose fits it.
    set_by: str = ""

    @property
    def file(self) -> str:
        return file_of(self.symbol)

    def to_json(self) -> dict:
        return {"feature": self.feature, "symbol": self.symbol,
                "fingerprint": self.fingerprint, "types_hash": self.types_hash, "set_by": self.set_by}


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
    # Accepting it asks for the code (``codoc_propose`` opens such a plan
    # before the code is written). Kept here, not read from the opening op,
    # so a fold that archives that op changes nothing.
    build: bool = False
    # The actor of the current verdict. A fold archives the verdict op, so
    # without this it would re-emit the verdict as the importer.
    decided_by: str = ""
    # The op of the current verdict, the one a reopen may undo. A fold keeps it
    # as it keeps who decided, so a reopen asks the same question either side.
    verdict: OpId = ""

    @property
    def asks_for_code(self) -> bool:
        """Accepting it hands its features off: it was opened with ``build``.
        A delete plan never does; removing code is its own decision
        (``codoc handoff --delete-code``)."""
        return self.build and self.kind != "delete"

    @property
    def adopted(self) -> bool:
        """Accepted, so what it proposed is now a request of the person who
        accepted it: it is a person's plan, or it asks for code. Accepting any
        other plan (the loop's, a translator's, an agent's reflect of code
        already written) takes words that describe what is already there."""
        return self.status == "accepted" and (is_human(self.actor) or self.asks_for_code)

    def to_json(self) -> dict:
        return {"id": self.id, "actor": self.actor, "kind": self.kind, "target": self.target,
                "note": self.note, "status": self.status, "ops": list(self.ops), "opened_by": self.opened_by,
                "build": self.build, "decided_by": self.decided_by, "verdict": self.verdict}


@dataclass
class Watermarks:
    handoff: dict = field(default_factory=dict)     # Frontier
    realized: dict = field(default_factory=dict)    # Frontier
    handoffs: list[OpId] = field(default_factory=list)   # hand-off op ids, oldest first
    # When the code under this feature last moved (a detach, or new hashes on a
    # binding it already had: `ops.moved_code`), and when a realization last settled it. Drift is
    # "the code moved and nobody has looked at the prose since": these two
    # clocks and the ones on the segments and marks make it a comparison of
    # state, so a snapshot can fold the ops away.
    code_at: str = ""
    realized_at: str = ""

    @property
    def queued(self) -> bool:
        """Handed off and not yet realized (docs/model.md, Watermarks)."""
        return bool(self.handoff) and not covered(self.handoff, self.realized)


Policy = Callable[["Store", Op], str | None]   # (store, op) → denial reason, or None


class _CopyOnFetch(dict):
    """A fork's blocks while it is written: each is copied from the parent the
    first time it is fetched by key (``[bid]`` or ``get``), which is how the
    applier reaches every block it writes. Iterating gives what is there,
    copied or not."""

    def __init__(self, parent: dict) -> None:
        super().__init__(parent)
        self.mine: set[str] = set()

    def __getitem__(self, bid: str) -> Block:
        b = dict.__getitem__(self, bid)
        if bid not in self.mine:
            b = copy.deepcopy(b)
            dict.__setitem__(self, bid, b)
            self.mine.add(bid)
        return b

    def get(self, bid, default=None):
        return self[bid] if bid in self else default


class Store:
    def __init__(self, policy: Policy | None = None) -> None:
        # Names this store instance. A rebuild makes a new store, and positions
        # (`pos`, `block_head`) restart with it, so a reader comparing positions
        # must compare them only under the same id.
        self.id = uuid.uuid4().hex[:12]
        self.blocks: dict[str, Block] = {}
        self.order: list[tuple[str, str]] = []
        # Feature id -> the blocks that recorded it when they died (``Block.dead_in``).
        # A revived block stays listed and is filtered out on read.
        self.removed: dict[str, set[str]] = {}
        self.bindings: dict[str, Binding] = {}   # symbol -> its one owner (I8)
        self.plans: dict[str, Plan] = {}
        # The open ones, kept as they open and close. Every reader that asks
        # "is anything in flight" asks per feature, and a tree accumulates
        # decided plans for ever.
        self.open_plans: dict[str, Plan] = {}
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
        self.structural_head: int = 0
        self.plans_head: int = 0
        self.policy: Policy | None = policy
        # Features that have ever had code bound to them. A feature that had code
        # and has none now is proposed for deletion; after a fold there is no
        # `bind.attach` op left to read, so it is state and not history.
        self.bound_once: set[str] = set()
        # A fork's log is its parent's, which it never appends to (`fork`).
        self.forked = False

    @property
    def pos(self) -> int:
        return len(self.ops)

    def fork(self, write: Callable[[Store], object]) -> Store:
        """This store as ``write`` leaves a throwaway copy of it, for a view
        that applies ops it does not keep (``realize.rejected_view``). The log
        and its indexes are shared and never appended to: a fork records
        nothing and indexes nothing, so ``write`` may not move characters
        between fields (a verdict kills, revives and drops marks in place).
        What an op writes is copied: each block the first time ``write``
        fetches it, the order, the bindings, the open plans, the removal lists,
        the watermark table, the plan table. So a fork costs the blocks its ops
        reach and a few copies of tables the size of the tree, never the log's
        length. The fork has an id of its own, so no cache keyed on
        the store mistakes it for this one, and no policy: it judges no one.
        Read it, never write it again: its untouched blocks are this store's."""
        f = copy.copy(self)
        f.id, f.forked, f.policy = uuid.uuid4().hex[:12], True, None
        f.blocks = _CopyOnFetch(self.blocks)
        f.order = list(self.order)
        f.removed = {k: set(v) for k, v in self.removed.items()}
        f.bindings = dict(self.bindings)
        f.open_plans = {k: copy.copy(p) for k, p in self.open_plans.items()}
        f.plans = {**self.plans, **f.open_plans}
        f.watermarks_by_fid = dict(self.watermarks_by_fid)
        f.bound_once = set(self.bound_once)
        write(f)
        f.blocks = dict(f.blocks)               # written: a read copies nothing
        return f

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
        if block.dead_in:                    # a snapshot restoring a removed block
            self.removed.setdefault(block.dead_in, set()).add(block.id)

    def set_rank(self, bid: str, rank: str, by: OpId) -> str:
        """Re-rank a block, recording ``by`` as the op that ranked it. A rank
        already held by another block is not reused: the nearest free rank
        below it is taken, since a rank is derived and the position is the
        intent (a restored rank may have been handed out again meanwhile).
        Returns the rank actually set."""
        b = self.blocks[bid]
        b.ranked_by = by
        i = bisect.bisect_left(self.order, (b.rank, bid))
        if i < len(self.order) and self.order[i] == (b.rank, bid):
            del self.order[i]
        j = bisect.bisect_left(self.order, (rank, ""))
        if j < len(self.order) and self.order[j][0] == rank:
            lo = self.order[j - 1][0] if j > 0 else ""
            rank = R.between(lo, rank)
        b.rank = rank
        bisect.insort(self.order, (rank, bid))
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

    def range(self, bid: str) -> list[Block]:
        """``range(h)`` (docs/model.md, Features): a live heading and every
        following live block up to the next live heading at its level or
        shallower. What ``block.move`` moves. Any other block, and a dead
        heading, is its own range. O(range plus interleaved dead blocks)."""
        return self._following(bid, lambda n, h: n.kind == HEADING and n.level <= h.level)

    def body(self, bid: str) -> list[Block]:
        """``body(h)``: a live heading and the following live non-heading
        blocks, up to the next live heading of any level. What a feature says,
        and what ``block.delete`` kills with its heading. Any other block, and a
        dead heading, is its own body."""
        return self._following(bid, lambda n, h: n.kind == HEADING)

    def _following(self, bid: str, stop) -> list[Block]:
        b = self.blocks[bid]
        out = [b]
        if b.kind != HEADING or not b.live:
            return out
        for j in range(self._index_of(bid) + 1, len(self.order)):
            n = self.blocks[self.order[j][1]]
            if not n.live:
                continue
            if stop(n, b):
                break
            out.append(n)
        return out

    def removed_body(self, fid: str) -> list[Block]:
        """What was deleted from a live heading's description, in document
        order: the dead non-heading blocks that recorded ``fid`` when they
        died. Where they sit now does not matter, so a heading added or
        deleted among them later moves none of them to another feature."""
        if not self.blocks[fid].live:
            return []
        out = [self.blocks[i] for i in self.removed.get(fid, ())]
        return sorted((b for b in out if not b.live and b.kind != HEADING and b.dead_in == fid),
                      key=lambda b: (b.rank, b.id))

    def kill(self, ids: list[str], op: OpId, actor: str, at: str) -> None:
        """Kill ``ids``, each recording the feature it was in. Read for all of
        them first: a heading's body was in that heading, not in the feature
        above it once the heading is dead."""
        features = {i: self.feature_of(i) or "" for i in ids}
        for i in ids:
            self.blocks[i].kill(op, actor, at, features[i])
            if features[i]:
                self.removed.setdefault(features[i], set()).add(i)

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
        # that into O(n²): 450 ms of it alone at 10,000 features.
        i = self._index_of(bid)
        flows = lambda j: (b := self.blocks[self.order[j][1]]).live and b.layout.flow
        prev = next((self.order[j][1] for j in range(i - 1, -1, -1) if flows(j)), None)
        nxt = next((self.order[j][1] for j in range(i + 1, len(self.order)) if flows(j)), None)
        return prev, nxt

    # ── bindings ───────────────────────────────────────────────────────────
    def unbind(self, fid: str) -> list[dict]:
        """Drop ``fid``'s bindings and return them, each with ``fid``, as
        ``reattach`` reads them."""
        mine = [{"fid": fid, **b.to_json()} for b in self.bindings.values() if b.feature == fid]
        for e in mine:
            del self.bindings[e["symbol"]]
        return mine

    def reattach(self, entries: list[dict]) -> set[str]:
        """Re-attach what a delete or a kind change took off, unless the symbol
        has an owner now (their attach stands) or the block is no longer a
        heading. Returns the features that got a binding back."""
        got: set[str] = set()
        for e in entries:
            key = e["symbol"]
            owner = self.blocks.get(e["fid"])
            if owner is None or owner.kind != HEADING:
                continue
            if key not in self.bindings:
                self.bindings[key] = Binding(e["fid"], key, e.get("fingerprint", ""), e.get("types_hash", ""),
                                             e.get("set_by", ""))
                got.add(e["fid"])
            owner.detached = [d for d in owner.detached if d["symbol"] != key or d.get("set")]
        return got

    def detach_dead(self, fids: list[str]) -> list[dict]:
        """A dead heading owns no code: drop its bindings and keep them on the
        block (the deleted heading's settle and the undo read them there, so a
        fold may take the op away). Returns them. What a kind change recorded
        there (``set``) stays."""
        out = []
        for fid in fids:
            mine = self.unbind(fid)
            if mine:
                b = self.blocks[fid]
                b.detached = [*mine, *(e for e in b.detached if e.get("set"))]
            out.extend(mine)
        return out

    def refeature(self, b: Block, prev_kind: str, symbols) -> tuple[list[dict], list[dict]]:
        """The one rule for a kind change, by ``block.set`` or a verdict's
        ``set`` step. A heading made another kind leaves as a delete does: its
        bindings come off and are kept on the block, marked ``set``, replacing
        an older entry for the same symbol. A live block made a heading takes
        back the named ``symbols`` it kept that way, each whose symbol nobody
        owns now, with the hashes and setter it kept: the undo names which
        symbols, never their values, so no payload sets them. A dead block made
        a heading (a reopen) owns no code, so it keeps them, as a delete keeps
        what it detached, and its restore re-attaches them. Returns
        ``(detached, attached)``."""
        if prev_kind == HEADING and b.kind != HEADING:
            gone = self.unbind(b.id)
            fresh = {e["symbol"]: {**e, "set": True} for e in gone}
            # In place, so a reopen that takes the same code off again leaves the list as it was.
            b.detached = [fresh.pop(d["symbol"]) if d.get("set") and d["symbol"] in fresh else d for d in b.detached] \
                + list(fresh.values())
            return gone, []
        if b.kind == HEADING and prev_kind != HEADING:
            want = set(symbols or ())
            back = [{k: v for k, v in e.items() if k != "set"} for e in b.detached
                    if e.get("set") and e["symbol"] in want and e["symbol"] not in self.bindings]
            if b.live:
                self.reattach(back)
            else:
                kept = {e["symbol"] for e in b.detached if not e.get("set")}
                b.detached = [*b.detached, *(e for e in back if e["symbol"] not in kept)]
            return [], back
        return [], []

    # ── characters and marks ───────────────────────────────────────────────
    def index_text(self, bid: str, path: str, text: Text) -> None:
        if self.forked:
            return
        for s in text.segs:
            self.char_index.setdefault(s.op, set()).add((bid, path))
        for m in text.marks:
            if m.op:
                self.mark_index.setdefault(m.op, set()).add((bid, path))

    def prune_index(self, bid: str, path: str, text: Text, was: set[OpId]) -> None:
        """After a split or merge moved characters out of ``(bid, path)``, drop
        the holders left holding nothing of an op. ``was`` is what the
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

    def placed_by(self, op: OpId) -> list[tuple[Block, str, Mark]]:
        """Every piece of every mark ``op`` placed, with the block and field
        holding it, wherever a split or a merge has taken it since."""
        return [(self.blocks[b], p, m) for b, p in sorted(self.mark_index.get(op, ())) if b in self.blocks
                for m in self.blocks[b].text(p).marks if m.op == op]

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
        if self.forked:
            return
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
    def state_fingerprint(self) -> dict:
        """The observable state: blocks with text and marks (marks in sequence
        order, so two logs applied in different orders compare equal), bindings,
        plans (ops as a set), watermarks. Positions and caches are excluded."""
        blocks = []
        for b in self.all_blocks():
            j = b.to_json()
            for f in b.layout.text:
                j["content"][f]["marks"] = b.text(f).sorted_marks_json()
            blocks.append(j)
        return {
            "blocks": blocks,
            "bindings": sorted((k, v.to_json()) for k, v in self.bindings.items()),
            "plans": sorted((p.id, {**p.to_json(), "ops": sorted(p.ops)}) for p in self.plans.values()),
            "watermarks": {fid: {"handoff": w.handoff, "realized": w.realized}
                           for fid, w in self.watermarks_by_fid.items() if w.handoff or w.realized},
        }
