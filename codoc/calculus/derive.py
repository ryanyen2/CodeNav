"""Derived views over the store (spec §6). Pure functions; nothing here writes.

Every user-facing question is one of these: what does a feature say, who wrote
which word, what is the person's unsent ink, what did the loop change under
them, which plans are open here, where is the feature in its lifecycle, what
should the agent build, what happened to it over time.
"""
from __future__ import annotations

from dataclasses import dataclass

from codoc.calculus import ops as K
from codoc.calculus.doc import HEADING, PARAGRAPH, Block, TreeNode, fold_tree, in_flow, text_fields
from codoc.calculus.ids import OpId, is_human
from codoc.calculus.inline_md import Span, render
from codoc.calculus.ops import Op, covered, in_frontier
from codoc.calculus.store import Store, Watermarks
from codoc.calculus.text import BOLD, CITE, LINK, PROPOSALS, SUGGEST_DEL, SUGGEST_MARK, Mark, Text

LOOP = "loop"
# A feature with pending intent: the document wins over a machine's repair
# (01 §4 rule 8), whatever its size.
HELD = ("edited", "queued", "planned")
# The fraction of a block a machine may delete on the trunk before the repair
# becomes a proposal instead (01 §4 rule 5); a person's prose is held tighter.
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


def markdown(text: Text, mode: str = "live", store: Store | None = None) -> str:
    """The field as Markdown. ``mode``: ``live`` (what the trunk says, which is
    what the page shows under the proposals drawn over it), ``accepted`` (every
    open plan taken), ``rejected`` (every open plan dropped, which is what a
    directive is rendered from).

    A plan's mark toggle is a record beside the trunk's marks, never a change to
    them (01 §3), so ``live`` and ``rejected`` agree about marks and only
    ``accepted`` has anything to apply. What differs between live and rejected
    is the words: a plan's insertions are on the page and not in the document
    without it."""
    # A field no plan has touched reads the same in all three modes, and most
    # fields in a tree are that field. Say so before cloning it and before
    # scanning the plans — a cold publish renders every field twice.
    if mode == "live" or store is None or not (
            any(s.live and s.plan for s in text.segs) if mode == "rejected" else text.proposals()):
        return render(text.live_text(), live_spans(text))
    view = text.clone()
    open_plans = store.open_plans
    if mode == "rejected":
        # An open plan's words are not the document's; its proposal records are
        # not marks at all, and `live_spans` already leaves them out.
        for s in view.segs:
            if s.live and s.plan in open_plans:
                s.dead = "view"
    elif mode == "accepted":
        for m in view.proposals():
            if m.attrs.get("plan") not in open_plans:
                continue
            if m.kind == SUGGEST_DEL:
                view.delete(m.start, m.end, by="view")
            else:
                view.set_mark(m.attrs.get("mark", ""), {k[2:]: v for k, v in m.attrs.items() if k.startswith("a:")},
                              m.start, m.end, bool(m.attrs.get("on")), actor=m.actor, op=m.op)
            view.marks.remove(m)
    return render(view.live_text(), live_spans(view))



def title(store: Store, fid: str, mode: str = "live") -> str:
    return markdown(store.blocks[fid].text(), mode, store)


def description(store: Store, fid: str, mode: str = "live") -> str:
    parts = []
    for b in store.feature_body(fid)[1:]:
        if b.kind == PARAGRAPH:
            md = markdown(b.text(), mode, store)
            if md.strip():
                parts.append(md)
    return "\n\n".join(parts)


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
    """Who last wrote this block: the actor of the newest live segment across
    its Text fields (a paragraph has one, a diagram has its source and its
    caption), an open plan's words excluded (a pending proposal is nobody's
    prose yet). ``human`` for any person, else the actor, else ``""``."""
    b = store.blocks[bid]
    newest = ("", "")
    for f in text_fields(b.kind):
        for s in b.text(f).segs:
            if not s.live or s.at <= newest[0]:
                continue
            if s.plan in store.open_plans:
                continue
            newest = (s.at, s.actor)
    actor = newest[1]
    return "human" if is_human(actor) else actor


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
    note (01 §6)."""
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
    hers, or one whose ops landed in her.

    Over `store.open_plans`, not every plan the tree ever had: a decided plan
    is answered, and walking all of them per feature made `phases()` cost
    O(features × plans) — 41 → 195 ms as a tree accumulated 3,000 verdicts."""
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


def phase(store: Store, fid: str, realizing: set[str] = frozenset()) -> str:
    """The single lifecycle state of a feature. Doc wins: a person's pending
    intent outranks anything the code side has to say. ``realizing`` is the
    one ephemeral input (a lease outside the store)."""
    w = store.watermarks(fid)
    if any(c.stage == "unsent" for c in ink(store, fid)):
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
    """Whether a view still reads this feature's OPS and not just its state —
    which is what a snapshot may not fold away (01 §7.5).

    ONE does: an open plan's verdict undoes the plan's own ops by identity, and
    a proposal is in flight by definition — a fold that took its ops would be
    folding away the answer to a question nobody has answered yet.

    Everything else reads state, and the rule that got them there is 01 §7.5's:
    put on the THING what the reader needs. A segment carries its minter's
    actor and clock and now its killer's; a mark carries its actor, its placing
    op and its clock; a watermark carries when the code last moved and when a
    realization last settled; a dead heading carries what it owned. So the
    directive's `removed:` and `Focus:`, `ground`'s reds and greens, drift, and
    a phantom's settle are all questions about the document — which they had to
    become, because three of them are asked by a hand-off given AFTER the fold
    and nothing at fold time could know it was coming.
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
    (`bind.refresh` or `bind.detach` — an `attach` is a relocation, not drift),
    when anybody last touched the prose, and when a realization last settled
    it. This used to compare the ORDER of those ops, which is why a feature
    that had drifted kept its whole log alive through every fold."""
    w = store.watermarks(fid)
    if not w.code_at:
        return False
    return w.code_at > max(w.realized_at, touched_at(store, fid))


# ── directive ────────────────────────────────────────────────────────────────

def directive(store: Store, fid: str) -> str:
    """The realize prompt item for a feature: its text in the all-rejected read
    mode (an un-vetted plan never masquerades as the person's intent), what the
    person changed since the last realization, Focus and Consult lines, and
    the person's own words from the hand-off."""
    b = store.blocks.get(fid)
    if b is None:
        return ""
    w = store.watermarks(fid)
    ho = _op(store, w.handoffs[-1]) if w.handoffs else None
    lines = []
    if not b.live:
        lines.append(f"### REMOVE FEATURE {title(store, fid, 'rejected')} ⟨{fid}⟩")
        if ho and ho.payload.get("delete_code"):
            lines.append("Delete the code this feature owned; it was removed from the tree on purpose.")
        else:
            lines.append("Stop tracking this feature's code; do not delete anything.")
    else:
        lines.append(f"### {title(store, fid, 'rejected')} ⟨{fid}⟩")
        lines.append("")
        lines.append(description(store, fid, "rejected") or "(no description)")
    changes = _human_changes(store, fid, w)
    if changes:
        lines.append("")
        lines.append("Changed since the last realization:")
        for c in changes:
            lines.append(f"- {c}")
    focus = _focus(store, fid, w)
    if focus:
        lines.append("")
        lines.append("Focus: " + "; ".join(f'"{t}"' for t in focus))
    consult = _all_links(store, fid)
    if consult:
        lines.append("")
        lines.append("Consult: " + ", ".join(f"{t} <{u}>" for t, u in consult))
    reads, asks = _kind_lines(store, fid, w)
    for line in reads + asks:
        lines.append("")
        lines.append(line)
    if ho and ho.payload.get("asked"):
        lines.append("")
        lines.append(f"Author asked: {ho.payload['asked']}")
    return "\n".join(lines)


def _kind_lines(store: Store, fid: str, w: Watermarks) -> tuple[list[str], list[str]]:
    """What this feature's plugin-kind blocks put in front of the agent (03
    §5.1): every in-flow block's ``consult``, then the directive each block's
    ``lower`` codec derives from what changed since the last realization. An
    out-of-flow block (a comment) is not prose the agent reads — only a
    request, which its own codec words."""
    from codoc import kinds as registry
    reads: list[str] = []
    asks: list[str] = []
    ctx: object | None = None
    for b in store.feature_body(fid):
        kind = registry.get(b.kind)
        if kind is None:
            continue
        new = registry.plain(b, store)
        if in_flow(b.kind) and (said := kind.consult(new)):
            reads.append(said)
        if kind.lower is None:
            continue
        old = {k: (markdown(_at_realized(store, v, w.realized), "rejected", store)
                   if isinstance(v, Text) and k in text_fields(b.kind) else v)
               for k, v in b.content.items()}
        for o in reversed(store.ops_on([b.id])):
            if o.conflict or o.kind != K.BLOCK_SET or in_frontier(o.id, w.realized):
                continue
            prev = o.payload.get("prev")
            old.pop(o.target["path"], None) if prev is None else old.update({o.target["path"]: prev})
        if ctx is None:
            ctx = registry.LowerContext(fid, [x for x in store.bindings.values() if x.feature == fid])
        if ask := kind.lower(old, new, ctx):
            asks.append(ask)
    return reads, asks


def _at_realized(store: Store, text: Text, realized: dict) -> Text:
    """The field without what the AUTHOR changed since the last realization:
    their characters are not there yet and their kills have not happened. By
    identity, no diff. Only the author's, because `lower` speaks for them — a
    diagram the loop lifted from the code is already in the code, and telling
    the agent "the author drew that edge" about it contradicts the `consult`
    line beside it."""
    t = text.clone()
    t.segs = [s for s in t.segs if in_frontier(s.op, realized) or not _authored(store, s.op)]
    for s in t.segs:
        if s.dead and not in_frontier(s.dead, realized) and _authored(store, s.dead):
            s.dead = None
    t.marks = [m for m in t.marks if t.holds(m.start) and t.holds(m.end)]
    return t


def _authored(store: Store, op_id: OpId) -> bool:
    """Whether a reader would call this the author's word: a person wrote it,
    or a person ACCEPTED it. Accepting is how an author adopts an agent's
    words — "a plan is by definition a request to build" (02 §3.3) — so an
    accepted edge has to reach the directive the same way a typed one does."""
    o = store.by_id.get(op_id)
    if o is None:
        return False
    if is_human(o.actor):
        return True
    plan = store.plans.get(o.plan) if o.plan else None
    return plan is not None and plan.status == "accepted"


def _human_changes(store: Store, fid: str, w: Watermarks) -> list[str]:
    """What the person did that the code has not answered, as the directive
    says it. Read from the TEXT, never from the ops: a segment carries who
    minted it and now who killed it, so a fold that takes the ops away leaves
    the request intact — which matters because the hand-off that renders it is
    usually given after the fold, and nothing at fold time could know."""
    out = []
    for c in ink(store, fid):
        if c.stage == "sent":
            out.append(f"added: \"{c.text}\"")
    for b in prose_blocks(store, fid):
        for f in text_fields(b.kind):
            for s in b.text(f).segs:
                if s.dead and is_human(s.dead_actor) and not in_frontier(s.dead, w.realized) \
                        and in_frontier(s.dead, w.handoff):
                    out.append(f"removed: \"{s.chars}\"")
    return out


def _focus(store: Store, fid: str, w: Watermarks) -> list[str]:
    """The words a person bolded and has not had realized — 02 §2's focus
    spans, as the directive names them.

    A mark carries its own actor and placing op, so this reads the MARKS, not
    the ops that placed them: a mark since turned off is simply not here to
    find, and a fold that takes the ops away leaves the focus intact. (It used
    to walk `text.mark` ops and re-find each recorded mark in the text, which
    needed the op to survive and had a matching rule of its own.)"""
    out: list[str] = []
    for b in prose_blocks(store, fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            for m in t.marks:
                if m.kind == BOLD and is_human(m.actor) and m.op and not in_frontier(m.op, w.realized):
                    if words := t.mark_text(m):
                        out.append(words)
    return out


def _all_links(store: Store, fid: str) -> list[tuple[str, str]]:
    out = []
    for b in store.feature_body(fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            lt = t.live_text()
            for sp in live_spans(t):
                if sp.kind == LINK:
                    out.append((lt[sp.start:sp.end], sp.attrs.get("href", "")))
    return out


# ── history ──────────────────────────────────────────────────────────────────

def history(store: Store, fid: str) -> list[Op]:
    """Every op recorded against the heading or a block it currently
    dominates, oldest first, conflicted ones included (their ``conflict`` says
    why they did nothing). Ops on blocks it dominated at the time but no longer
    does are omitted."""
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
    ``(block_head[b.id], plans_head)`` (spec §6, ``projection``).

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
    return {"head": store.pos, "frontier": dict(store.frontier), "blocks": blocks, "phases": phases,
            "plans": [p.to_json() for p in open_plans(store)],
            "watermarks": watermarks}


def open_plans(store: Store) -> list:
    """Plans still awaiting a verdict whose target (when they have one) is
    live: a proposal on a heading the person deleted is not decidable from
    any surface, so it is not offered."""
    return [p for p in store.open_plans.values()
            if p.target is None or (p.target in store.blocks and store.blocks[p.target].live)]
