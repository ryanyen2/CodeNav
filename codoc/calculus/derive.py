"""Derived views over the store (docs/model.md, Derived views). Pure functions;
nothing here writes.

Every user-facing question is one of these: what does a feature say, who wrote
which word, what is the person's unsent ink, what did the loop change under
them, which plans are open here, where is the feature in its lifecycle, what
happened to it over time.
"""
from __future__ import annotations

from dataclasses import dataclass

from codoc.calculus import block_md
from codoc.calculus.block_md import BlockSpec
from codoc.calculus.doc import (
    HEADING, PROSE, SOURCE, Block, TreeNode, fold_tree, in_flow, is_exported, is_source_field, text_fields,
)
from codoc.calculus.ids import OpId, is_human
from codoc.calculus.inline_md import Span, render
from codoc.calculus.ops import Op, covered, in_frontier
from codoc.calculus.store import Store
from codoc.calculus.text import CITE, PROPOSALS, SUGGEST_DEL, SUGGEST_MARK, Mark, Text, struck_chars

LOOP = "loop"
# A feature with pending intent: the document wins over a machine's repair,
# whatever its size (docs/concurrency.md, The machine gate).
HELD = ("edited", "queued", "planned")
# The fraction of a block a machine may delete on the trunk before the repair
# becomes a proposal instead; a person's prose is held tighter.
GATE_MACHINE = 0.5
GATE_HUMAN = 0.15


# ── text views ───────────────────────────────────────────────────────────────

def live_spans(text: Text, kinds: frozenset | None = None) -> list[Span]:
    """Marks as offset spans over the live text (a mark whose characters are
    all dead yields nothing). Default: every kind Markdown renders."""
    offsets = text.live_offsets()
    out: list[Span] = []
    for m in text.marks:
        if (kinds is None and m.kind in PROPOSALS) or (kinds is not None and m.kind not in kinds):
            continue
        span = text.live_span(m, offsets)
        if span:
            out.append(Span(m.kind, dict(m.attrs), span[0], span[1]))
    return out


def _in_mode(text: Text, mode: str, store: Store | None) -> Text:
    """``text`` as it reads in a read mode. ``live``: the field as stored, an
    open plan's words included. ``rejected``: without any open plan's words.
    ``accepted``: with every open plan taken. Returns ``text`` itself when the
    mode changes nothing, else a clone."""
    if mode == "live" or store is None or not (
            any(s.live and s.plan for s in text.segs) if mode == "rejected" else text.proposals()):
        return text
    view = text.clone()
    open_plans = store.open_plans
    if mode == "rejected":
        for s in view.segs:
            if s.live and s.plan in open_plans:
                s.dead = "view"
        return view
    for m in view.proposals():
        if m.attrs.get("plan") not in open_plans:
            continue
        if m.kind == SUGGEST_DEL:
            view.kill(struck_chars(m), by="view")      # what accept would kill, not the whole range
        else:
            view.set_mark(m.attrs.get("mark", ""), {k[2:]: v for k, v in m.attrs.items() if k.startswith("a:")},
                          m.start, m.end, bool(m.attrs.get("on")), actor=m.actor, op=m.op)
        view.marks.remove(m)
    return view


def markdown(text: Text, mode: str = "live", store: Store | None = None) -> str:
    """An inline field as Markdown in a read mode."""
    view = _in_mode(text, mode, store)
    return render(view.live_text(), live_spans(view))


def text_string(kind: str, path: str, text: Text, mode: str = "live", store: Store | None = None) -> str:
    """A Text field as a string: the raw text of a source field, inline
    Markdown for any other."""
    if is_source_field(kind, path):
        return _in_mode(text, mode, store).live_text()
    return markdown(text, mode, store)


def field_text(block: Block, path: str, mode: str = "live", store: Store | None = None) -> str:
    return text_string(block.kind, path, block.text(path), mode, store)


def title(store: Store, fid: str, mode: str = "live") -> str:
    return markdown(store.blocks[fid].text(), mode, store)


def block_spec(store: Store, b: Block, mode: str = "live") -> BlockSpec | None:
    """``b`` as Markdown says it, or None when it says nothing in this mode: an
    empty paragraph or item, an empty source, a block holding only an open
    plan's words under ``rejected``, a kind left out of the export."""
    if b.kind == HEADING:
        md = markdown(b.text(), mode, store)
        if b.text().live_len() and not md.strip():
            return None            # a proposed node: its title is an open plan's words
        return BlockSpec(HEADING, b.level, md, id=b.id)
    if b.kind in PROSE:
        md = markdown(b.text(), mode, store)
        return BlockSpec(b.kind, b.level, md) if md.strip() else None
    if not is_exported(b.kind):
        return None
    plain = {k: v for k, v in b.content.items() if k not in text_fields(b.kind)}
    if b.kind in SOURCE:
        source = field_text(b, "source", mode, store)
        return BlockSpec(b.kind, text=source, content=plain) if source.strip() else None
    texts = {f: field_text(b, f, mode, store) for f in text_fields(b.kind)}
    return BlockSpec(b.kind, content={**plain, **{f: t for f, t in texts.items() if t}}, id=b.id)


def description(store: Store, fid: str, mode: str = "live") -> str:
    """The feature's own body as Markdown: its paragraphs, list items, tables,
    diagrams and code, up to its first child heading. Other kinds (an image, a
    reference, a comment) are not part of what the feature says."""
    specs = [s for b in store.feature_body(fid)[1:]
             if b.kind in PROSE or b.kind in SOURCE
             if (s := block_spec(store, b, mode)) is not None]
    return block_md.render(specs).rstrip("\n")


def tree(store: Store) -> list[TreeNode]:
    return fold_tree(store.headings())


# ── attribution ──────────────────────────────────────────────────────────────

@dataclass
class Run:
    start: int          # live offsets, half-open
    end: int
    actor: str
    at: str
    plan: str | None = None
    op: OpId = ""       # the minting op of the run's first character
    k0: int = 0         # …and that character's index within the op, so (op, k0 + i) is character i


def attribution(text: Text, merge: bool = True) -> list[Run]:
    """Live segments as offset runs. With ``merge`` adjacent runs by the same
    actor and plan join (for display); without it every run is one segment and
    ``(op, k0 + i)`` is the identity of its i-th character (for the editor)."""
    out: list[Run] = []
    i = 0
    for s in text.segs:
        if not s.live:
            continue
        r = Run(i, i + len(s.chars), s.actor, s.at, s.plan, s.op, s.k0)
        i += len(s.chars)
        if merge and out and out[-1].actor == r.actor and out[-1].plan == r.plan and out[-1].end == r.start:
            out[-1].end = r.end
            out[-1].at = max(out[-1].at, r.at)
        else:
            out.append(r)
    return out


def block_written_by(store: Store, bid: str) -> str:
    """``human`` when any live character of the block was written by a person,
    else the actor of the newest live segment, else ``""``. An open plan's
    words are left out: a pending proposal is nobody's prose yet.

    Any person's word makes the block a person's, so a machine appending a
    clause to it does not loosen the gate on rewriting the rest."""
    b = store.blocks[bid]
    newest = ("", "")
    for f in text_fields(b.kind):
        for s in b.text(f).segs:
            if not s.live or s.plan in store.open_plans:
                continue
            if is_human(s.actor):
                return "human"
            if s.at > newest[0]:
                newest = (s.at, s.actor)
    return newest[1]


# ── the three channels ───────────────────────────────────────────────────────

@dataclass
class Claim:
    bid: str
    path: str
    start: int
    end: int
    channel: str        # human | plan | code
    stage: str          # unsent | sent | open | accepted | added | removed
    actor: str
    text: str = ""


def _body_ids(store: Store, fid: str) -> set[str]:
    return {b.id for b in store.feature_body(fid)}


def prose_blocks(store: Store, fid: str) -> list[Block]:
    """The feature's blocks that are the document. A comment hangs off words
    rather than standing among them, so writing one is not editing the prose:
    it must not ink the feature, turn it `edited`, or offer a hand-off of a
    note."""
    return [b for b in store.feature_body(fid) if in_flow(b.kind)]


def ink(store: Store, fid: str) -> list[Claim]:
    """Human live segments beyond the realized frontier: ``unsent`` past the
    hand-off frontier, ``sent`` between the two. Adjacent segments merge only
    when they share actor, plan and stage, so a word typed after the hand-off
    never reads as sent because it touches one typed before."""
    w = store.watermarks(fid)
    out: list[Claim] = []
    for b in prose_blocks(store, fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            lt = t.live_text()
            last: Claim | None = None
            for r in attribution(t, merge=False):
                if not is_human(r.actor) or r.plan or in_frontier(r.op, w.realized):
                    last = None
                    continue
                stage = "sent" if in_frontier(r.op, w.handoff) else "unsent"
                if last and last.actor == r.actor and last.stage == stage and last.end == r.start:
                    last.end = r.end
                    last.text = lt[last.start:last.end]
                    continue
                last = Claim(b.id, f, r.start, r.end, "human", stage, r.actor, lt[r.start:r.end])
                out.append(last)
    return out


def ground(store: Store, fid: str) -> list[Claim]:
    """What the loop did to this feature since the person last engaged with it
    (their latest hand-off, or the newest character or mark of theirs on its
    blocks, whichever is later by the clock): live loop segments as ``added``,
    loop-killed dead segments as ``removed`` (their text carried, positioned at
    the live offset where they were). A feature the person never touched paints
    nothing: there is no "since" to measure from."""
    w = store.watermarks(fid)
    since = touched_at(store, fid, human_only=True)
    if not w.handoff and not since:
        return []

    def new(op_id: str, at: str) -> bool:
        return at > since and not in_frontier(op_id, w.handoff)

    out: list[Claim] = []
    for b in prose_blocks(store, fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            i = 0
            for s in t.segs:
                if s.live:
                    if s.actor == LOOP and not s.plan and new(s.op, s.at):
                        out.append(Claim(b.id, f, i, i + len(s.chars), "code", "added", s.actor, s.chars))
                    i += len(s.chars)
                elif s.dead and s.dead_actor == LOOP and new(s.dead, s.dead_at):
                    out.append(Claim(b.id, f, i, i, "code", "removed", s.dead_actor, s.chars))
    return out


def plan_claims(store: Store, fid: str) -> list[Claim]:
    """Open plans' insertions (``open``), suggested deletions (``removed``) and
    mark toggles (``marked`` / ``unmarked``) on this feature, plus accepted
    plans' words (``accepted``)."""
    out: list[Claim] = []
    for b in prose_blocks(store, fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            lt = t.live_text()
            for r in attribution(t):
                if not r.plan or r.plan not in store.plans:
                    continue
                st = store.plans[r.plan].status
                if st == "rejected":
                    continue
                out.append(Claim(b.id, f, r.start, r.end, "plan", "open" if st == "open" else "accepted", r.actor, lt[r.start:r.end]))
            for sp in _suggest_spans(t, store):
                out.append(Claim(b.id, f, sp.start, sp.end, "plan", "removed", store.plans[sp.attrs["plan"]].actor, lt[sp.start:sp.end]))
            offsets = t.live_offsets()
            for m in t.proposals(kind=SUGGEST_MARK):
                plan = store.plans.get(str(m.attrs.get("plan") or ""))
                if plan is None or plan.status != "open":
                    continue
                span = t.live_span(m, offsets)
                if span:
                    out.append(Claim(b.id, f, span[0], span[1], "plan",
                                     "marked" if m.attrs.get("on") else "unmarked", plan.actor,
                                     f"{m.attrs.get('mark', '')}:{lt[span[0]:span[1]]}"))
    return out


def _suggest_spans(t: Text, store: Store) -> list[Span]:
    return [sp for sp in live_spans(t, frozenset({SUGGEST_DEL}))
            if sp.attrs.get("plan") in store.open_plans]


def open_plans_on(store: Store, fid: str) -> list[str]:
    """The open plans this feature is waiting on: one targeting a block of
    its body, or one whose ops landed in it.

    Over `store.open_plans`, not every plan the tree ever had: a decided plan
    is answered, and walking all of them per feature would make `phases()`
    cost O(features × plans)."""
    ids = _body_ids(store, fid)
    out = {pid for pid, p in store.open_plans.items() if p.target in ids}
    out.update(o.plan for o in store.ops_on(ids)
               if o.plan and not o.conflict and o.plan in store.open_plans)
    return sorted(out)


def _op(store: Store, op_id: OpId) -> Op | None:
    return store.by_id.get(op_id)


# ── phase ────────────────────────────────────────────────────────────────────

# Doc-wins order: a person's unsent edit shows even while an agent holds the
# realizing lease, which is what lets them keep typing during a build.
PHASES = ("edited", "realizing", "queued", "planned", "proposed", "drifted", "synced")


def unsent_removal(store: Store, fid: str) -> bool:
    """Whether the person has removed words here that no hand-off has carried.

    Not part of `ink`, which the editor draws as what the person wrote and
    not what they removed. A removal is pending intent all the same (emptying
    a title by deleting characters flags the feature, and the directive
    carries it as a `removed:` line), so the phase sees it; otherwise the
    person would be told there is nothing to hand off while there is."""
    w = store.watermarks(fid)
    for b in prose_blocks(store, fid):
        for f in text_fields(b.kind):
            for sg in b.text(f).segs:
                if sg.dead and is_human(sg.dead_actor) and not in_frontier(sg.dead, w.handoff) \
                        and not in_frontier(sg.dead, w.realized):
                    return True
    return any(not in_frontier(b.dead, w.handoff) and not in_frontier(b.dead, w.realized)
               for b in removed_blocks(store, fid))


def removed_blocks(store: Store, fid: str) -> list[Block]:
    """Blocks of the feature's description a person deleted whole, that held
    words of the document (not only an open plan's). Deleting a block is a
    change to what the feature says, like deleting its words one by one.
    Changing a block's kind, level or place is layout and is not listed."""
    out = []
    for b in store.removed_body(fid):
        if not (in_flow(b.kind) and is_human(b.dead_actor)):
            continue
        if any(s.live and s.plan not in store.open_plans for f in text_fields(b.kind) for s in b.text(f).segs):
            out.append(b)
    return out


def phase(store: Store, fid: str, realizing: set[str] = frozenset()) -> str:
    """The single lifecycle state of a feature. Doc wins: a person's pending
    intent outranks anything the code side has to say. ``realizing`` is the
    one ephemeral input (a lease outside the store)."""
    w = store.watermarks(fid)
    if any(c.stage == "unsent" for c in ink(store, fid)) or unsent_removal(store, fid):
        return "edited"
    if fid in realizing:
        return "realizing"
    queued = bool(w.handoff) and not covered(w.handoff, w.realized)
    if queued:
        return "queued" if any(b.feature == fid for b in store.bindings.values()) else "planned"
    if open_plans_on(store, fid):
        return "proposed"
    if _drifted(store, fid):
        return "drifted"
    return "synced"


def reads_its_ops(store: Store, fid: str) -> bool:
    """Whether a view still reads this feature's OPS and not only its state,
    which is what a snapshot may not fold away.

    One does: an open plan's verdict undoes the plan's own ops by identity, so
    a fold may not take them.

    Everything else reads state. A segment carries its minter's actor and clock
    and its killer's; a mark carries its actor, its placing op and its clock; a
    watermark carries when the code last moved and when a realization last
    settled; a dead heading carries what it owned. So the directive's
    `removed:` and `Focus:`, `ground`'s reds and greens, drift, and a deleted
    heading's settle read the document, and a hand-off given after a fold
    still finds what it needs.
    """
    b = store.blocks.get(fid)
    return b is not None and b.live and bool(open_plans_on(store, fid))


def touched_at(store: Store, fid: str, *, human_only: bool = False) -> str:
    """The clock of the newest thing anyone did to this feature's PROSE: a
    character written or removed, a mark placed. Read from the text, because
    that is where a segment's and a mark's clocks live; ``human_only`` asks the
    same question about the person alone, which is what ``ground`` measures
    "since" from."""
    newest = ""
    for b in prose_blocks(store, fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            for sg in t.segs:
                if not human_only or is_human(sg.actor):
                    newest = max(newest, sg.at)
                if sg.dead and (not human_only or is_human(sg.dead_actor)):
                    newest = max(newest, sg.dead_at)
            for m in t.marks:
                if not human_only or is_human(m.actor):
                    newest = max(newest, m.at)
    return newest


def _drifted(store: Store, fid: str) -> bool:
    """Bound code moved and nobody has looked at the prose since.

    Three clocks, all on state: when the code under this feature last moved
    (`bind.refresh` or `bind.detach`; an `attach` is a relocation, not drift),
    when anybody last touched the prose, and when a realization last settled
    it."""
    w = store.watermarks(fid)
    if not w.code_at:
        return False
    return w.code_at > max(w.realized_at, touched_at(store, fid))


# ── history ──────────────────────────────────────────────────────────────────

def history(store: Store, fid: str) -> list[Op]:
    """Every op recorded against the heading or a block it currently
    dominates, oldest first, conflicted ones included (their ``conflict`` says
    why they did nothing). Ops on blocks it dominated at the time but does not
    dominate now are omitted."""
    return store.ops_on(_body_ids(store, fid))


def dead_cites(store: Store, known: set[tuple[str, str]]) -> list[tuple[str, Mark]]:
    out = []
    for b in store.ordered():
        for f in text_fields(b.kind):
            for m in b.text(f).marks:
                if m.kind == CITE and (m.attrs.get("file", ""), m.attrs.get("symbol", "")) not in known:
                    out.append((b.id, m))
    return out


# ── projection ───────────────────────────────────────────────────────────────

def block_entry(store: Store, b: Block) -> dict:
    """One block as the editor reads it. Pure, and a function of the block's
    own state plus which plans are open, so a caller may memoize it on
    ``(block_head[b.id], plans_head)`` (docs/model.md, Cache keys).

    The spans are the document's own marks, and nothing else: a plan never
    changes them, so a proposal to unbold a word leaves the word bold, the way
    a proposal to delete words leaves them struck rather than gone. What the
    plan proposes is drawn over them from ``plan_claims`` (``marked`` /
    ``unmarked``), and the words it inserted are in ``runs``, as before."""
    entry = {"id": b.id, "kind": b.kind, "level": b.level, "rank": b.rank, "head": store.block_head.get(b.id, 0),
             "fields": {}}
    for f in text_fields(b.kind):
        t = b.text(f)
        entry["fields"][f] = {
            "text": t.live_text(),
            "runs": [r.__dict__ for r in attribution(t, merge=False)],
            "spans": [{"kind": s.kind, "attrs": s.attrs, "start": s.start, "end": s.end} for s in live_spans(t)],
            "suggest_del": [{"plan": s.attrs["plan"], "start": s.start, "end": s.end} for s in _suggest_spans(t, store)],
        }
    for k, v in b.content.items():
        if k not in text_fields(b.kind):
            entry["fields"][k] = v
    return entry


def projection(store: Store, realizing: set[str] = frozenset(), phases: dict[str, str] | None = None,
               entry=block_entry) -> dict:
    """What an editor needs: every live block with its text, runs, spans, plan
    claims and its feature's phase. Dead segments are omitted; the editor asks
    for ``ground`` per feature when History is on. ``phases`` and ``entry`` may
    be passed in by a caller that already computed or memoized them."""
    blocks = []
    phases = dict(phases) if phases is not None else {}
    for b in store.ordered():
        if b.kind == HEADING and b.id not in phases:
            phases[b.id] = phase(store, b.id, realizing)
        blocks.append(entry(store, b))
    watermarks = {fid: {"handoff": w.handoff, "realized": w.realized}
                  for fid, w in store.watermarks_by_fid.items() if (w.handoff or w.realized) and fid in phases}
    return {"store": store.id, "head": store.pos, "frontier": dict(store.frontier), "blocks": blocks,
            "phases": phases, "plans": [{**p.to_json(), "handoff": accept_handoffs(store, p)} for p in open_plans(store)],
            "watermarks": watermarks}


def plan_features(store: Store, plan) -> list[str]:
    """Every live feature a plan is about: the one it names, and the one each
    of its ops lands in. A plan that adds several headings names none of them."""
    out: list[str] = []
    for bid in [plan.target] + [(store.by_id[i].target.get("bid") or store.by_id[i].target.get("fid"))
                                for i in plan.ops if i in store.by_id]:
        fid = store.feature_of(bid) if bid and bid in store.blocks else None
        if fid and fid not in out:
            out.append(fid)
    return out


def accept_handoffs(store: Store, plan) -> list[str]:
    """The features that accepting ``plan`` hands off, in the same batch.

    A plan opened with ``build`` asks for code: ``codoc_propose`` opens one
    before the code is written, so accepting it queues the work. Any other
    plan (the loop's, a translator's, a suggester's, or an agent's
    ``codoc_reflect`` of code it already wrote) is about words, and accepting
    it takes the words only. A delete plan never hands off: removing code is
    its own decision (`codoc handoff --delete-code`)."""
    if plan.kind == "delete" or not plan.build:
        return []
    return plan_features(store, plan)


def open_plans(store: Store) -> list:
    """Plans still awaiting a verdict whose target (when they have one) is
    live: a proposal on a heading the person deleted is not decidable from
    any surface, so it is not offered."""
    return [p for p in store.open_plans.values()
            if p.target is None or (p.target in store.blocks and store.blocks[p.target].live)]
