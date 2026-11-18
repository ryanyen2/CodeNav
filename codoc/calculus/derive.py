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
    HEADING, LAYOUT_KEY, PROSE, SOURCE, Block, TreeNode, fold_tree, is_source_field, layout_entry,
    registered_layouts,
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
    ``accepted``: with every open plan taken. ``loop``: with the loop's own
    open plans taken and every other open plan's words left out, which is what
    the reflect pass reads, so it does not propose again what it already has.
    Returns ``text`` itself when the mode changes nothing, else a clone."""
    if mode == "live" or store is None:
        return text
    open_plans = store.open_plans
    taken = (set(open_plans) if mode == "accepted" else
             {pid for pid, p in open_plans.items() if p.actor == LOOP} if mode == "loop" else set())
    if not (any(s.live and s.plan in open_plans and s.plan not in taken for s in text.segs)
            or any(m.attrs.get("plan") in taken for m in text.proposals())):
        return text
    view = text.clone()
    for s in view.segs:
        if s.live and s.plan in open_plans and s.plan not in taken:
            s.dead = "view"
    for m in view.proposals():
        if m.attrs.get("plan") not in taken:
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
    if not b.layout.export:
        return None
    plain = {k: v for k, v in b.content.items() if k not in b.layout.text}
    if b.kind in SOURCE:
        source = field_text(b, "source", mode, store)
        return BlockSpec(b.kind, text=source, content=plain) if source.strip() else None
    texts = {f: field_text(b, f, mode, store) for f in b.layout.text}
    # The fence says which keys are Text, so a paste rebuilds the same block
    # whether or not the pasting side has the kind's code.
    return BlockSpec(b.kind, content={LAYOUT_KEY: b.layout.to_json(), **plain, **{f: t for f, t in texts.items() if t}},
                     id=b.id)


def kind_words(b: Block, store: Store | None = None) -> str:
    """A plugin block as a model reads it without the kind's code: the kind's
    name and the words of its Text fields in the ``rejected`` mode, or "" when
    they hold none."""
    words = [w for f in b.layout.text if (w := field_text(b, f, "rejected", store).strip())]
    return f"{b.kind}: {'; '.join(words)}" if words else ""


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
    for f in b.layout.text:
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
    stage: str          # unsent | sent | open | added | removed | marked | unmarked
    actor: str
    text: str = ""


def _body_ids(store: Store, fid: str) -> set[str]:
    return {b.id for b in store.feature_body(fid)}


def prose_blocks(store: Store, fid: str) -> list[Block]:
    """The feature's blocks that are the document. A comment hangs off words
    rather than standing among them, so writing one is not editing the prose:
    it must not ink the feature, turn it `edited`, or offer a hand-off of a
    note."""
    return [b for b in store.feature_body(fid) if b.layout.flow]


def ink(store: Store, fid: str) -> list[Claim]:
    """Human live segments beyond the realized frontier: ``unsent`` past the
    hand-off frontier, ``sent`` between the two. Adjacent segments merge only
    when they share actor, plan and stage, so a word typed after the hand-off
    never reads as sent because it touches one typed before."""
    w = store.watermarks(fid)
    out: list[Claim] = []
    for b in prose_blocks(store, fid):
        for f in b.layout.text:
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
        for f in b.layout.text:
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
    """Open plans' insertions (``open``) and suggested deletions (``removed``)
    on this feature, then ``mark_claims``. An accepted plan's words are
    ordinary text and claim nothing; their runs still name the proposer."""
    out: list[Claim] = []
    for b in prose_blocks(store, fid):
        for f in b.layout.text:
            t = b.text(f)
            lt = t.live_text()
            for r in attribution(t):
                if r.plan in store.open_plans:
                    out.append(Claim(b.id, f, r.start, r.end, "plan", "open", r.actor, lt[r.start:r.end]))
            for sp in _suggest_spans(t, store):
                out.append(Claim(b.id, f, sp.start, sp.end, "plan", "removed", store.plans[sp.attrs["plan"]].actor, lt[sp.start:sp.end]))
    return out + mark_claims(store, fid)


def mark_claims(store: Store, fid: str) -> list[Claim]:
    """Open plans' mark toggles on words already there (``marked`` /
    ``unmarked``), each with ``<mark>:<words>`` as its text."""
    out: list[Claim] = []
    for b in prose_blocks(store, fid):
        for f in b.layout.text:
            t = b.text(f)
            lt = t.live_text()
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


def editors(store: Store, fid: str) -> list[str]:
    """Who has edited this feature since its last hand-off: the actors of its
    unsent ink and of the words and blocks they removed that no hand-off has
    carried. A feature with any is `edited`, and the editor's ⌘S hands off
    only the ones that name the viewer.

    A removal is not part of `ink`, which the editor draws as what the person
    wrote and not what they removed. It is pending intent all the same
    (emptying a title by deleting characters flags the feature, and the
    directive carries it as a `removed:` line); otherwise the person would be
    told there is nothing to hand off while there is. A removal a verdict made
    is nobody's edit unless it accepted a person's plan (``answered``); then
    its editor is the one who accepted, who killed the characters."""
    w = store.watermarks(fid)
    out = {c.actor for c in ink(store, fid) if c.stage == "unsent"}
    kills = [sg for b in prose_blocks(store, fid) for f in b.layout.text
             for sg in b.text(f).segs if sg.dead and is_human(sg.dead_actor)
             and not in_frontier(sg.dead, w.handoff) and not in_frontier(sg.dead, w.realized)]
    if kills:
        verdicts = verdict_ops(store)
        out.update(sg.dead_actor for sg in kills if not answered(verdicts, sg))
    out.update(b.dead_actor for b in removed_blocks(store, fid)
               if not in_frontier(b.dead, w.handoff) and not in_frontier(b.dead, w.realized))
    return sorted(out)


def verdict_ops(store: Store) -> dict:
    """The ops that decided a plan, each to its plan. Read from the plans,
    which a fold keeps, so a character a verdict killed is known as one after
    the fold too."""
    return {p.verdict: p for p in store.plans.values() if p.verdict}


def answered(verdicts: dict, sg, *, build: bool = False) -> bool:
    """Whether a removed segment answered a plan and asks nothing: a verdict
    killed it, and that verdict did not accept a person's plan (a person who
    accepts a suggester's strike adopts it as their own request) nor, with
    ``build``, a plan that asks for code. A plan's own words that its verdict
    took back were never the document's."""
    p = verdicts.get(sg.dead)
    return p is not None and (p.status != "accepted" or sg.plan == p.id
                              or not (is_human(p.actor) or build and p.build))


def removed_blocks(store: Store, fid: str) -> list[Block]:
    """Blocks of the feature's description a person deleted whole, that held
    words of the document (not only an open plan's). Deleting a block is a
    change to what the feature says, like deleting its words one by one.
    Changing a block's kind, level or place is layout and is not listed."""
    out = []
    for b in store.removed_body(fid):
        if not (b.layout.flow and is_human(b.dead_actor)):
            continue
        if any(s.live and s.plan not in store.open_plans for f in b.layout.text for s in b.text(f).segs):
            out.append(b)
    return out


def phase(store: Store, fid: str, realizing: set[str] = frozenset()) -> str:
    """The single lifecycle state of a feature. Doc wins: a person's pending
    intent outranks anything the code side has to say. ``realizing`` is the
    one ephemeral input (a lease outside the store)."""
    w = store.watermarks(fid)
    if editors(store, fid):
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
        for f in b.layout.text:
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


def dead_cites(store: Store) -> list[tuple[str, Mark]]:
    out = []
    for b in store.ordered():
        for f in b.layout.text:
            for m in b.text(f).marks:
                if m.kind == CITE and m.attrs.get("symbol", "") not in store.bindings:
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
    plan proposes is drawn over them from ``mark_claims``, and the words it
    inserted are in ``runs``, as before."""
    entry = {"id": b.id, "kind": b.kind, "level": b.level, "rank": b.rank, "head": store.block_head.get(b.id, 0),
             "fields": {}, **layout_entry(b.kind, b.layout)}     # an editor without the kind can still draw it
    for f in b.layout.text:
        t = b.text(f)
        entry["fields"][f] = {
            "text": t.live_text(),
            "runs": [r.__dict__ for r in attribution(t, merge=False)],
            "spans": [{"kind": s.kind, "attrs": s.attrs, "start": s.start, "end": s.end} for s in live_spans(t)],
            "suggest_del": [{"plan": s.attrs["plan"], "start": s.start, "end": s.end} for s in _suggest_spans(t, store)],
        }
    for k, v in b.content.items():
        if k not in b.layout.text:
            entry["fields"][k] = v
    return entry


def feature_claims(store: Store, fid: str) -> list[dict]:
    """The claims the editor cannot read off a block's runs and `suggest_del`:
    the code channel (``ground``) and an open plan's mark toggles. It draws ink
    and a plan's words and cuts from the block entries."""
    return [c.__dict__ for c in ground(store, fid) + mark_claims(store, fid)]


def projection(store: Store, realizing: set[str] = frozenset(), phases: dict[str, str] | None = None,
               entry=block_entry, claims=feature_claims) -> dict:
    """What an editor needs: every live block with its text, runs and spans,
    each feature's phase, who edited each `edited` one (``editors``), bindings
    and claims. ``phases``, ``entry`` and
    ``claims`` may be passed in by a caller that already computed or memoized
    them."""
    blocks = []
    phases = dict(phases) if phases is not None else {}
    for b in store.ordered():
        if b.kind == HEADING and b.id not in phases:
            phases[b.id] = phase(store, b.id, realizing)
        blocks.append(entry(store, b))
    watermarks = {fid: {"handoff": w.handoff, "realized": w.realized}
                  for fid, w in store.watermarks_by_fid.items() if (w.handoff or w.realized) and fid in phases}
    by_feature: dict[str, list[str]] = {}
    for bd in store.bindings.values():
        by_feature.setdefault(bd.feature, []).append(bd.symbol)
    return {"store": store.id, "head": store.pos, "frontier": dict(store.frontier), "blocks": blocks,
            "phases": phases, "editors": {fid: editors(store, fid) for fid, p in phases.items() if p == "edited"},
            "plans": [{**p.to_json(), "handoff": accept_handoffs(store, p)} for p in open_plans(store)],
            "watermarks": watermarks, "bindings": {"by_feature": by_feature},
            "claims": {fid: c for fid in phases if (c := claims(store, fid))},
            # The layouts this checkout's plugin kinds give a new block, so a
            # paste of a fence without `$layout` splits its fields the same way.
            "kinds": {k: layout.to_json() for k, layout in sorted(registered_layouts().items())}}


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
