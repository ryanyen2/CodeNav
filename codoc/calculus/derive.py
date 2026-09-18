"""Derived views over the store (spec §6). Pure functions; nothing here writes.

Every user-facing question is one of these: what does a feature say, who wrote
which word, what is the person's unsent ink, what did the loop change under
them, which plans are open here, where is the feature in its lifecycle, what
should the agent build, what happened to it over time.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codoc.calculus import ops as K
from codoc.calculus.doc import HEADING, PARAGRAPH, Block, TreeNode, fold_tree, text_fields
from codoc.calculus.ids import OpId, is_human
from codoc.calculus.inline_md import Span, render
from codoc.calculus.ops import Op, covered, in_frontier
from codoc.calculus.store import Store, Watermarks
from codoc.calculus.text import BOLD, CITE, LINK, SUGGEST_DEL, Mark, Text

LOOP = "loop"


# ── text views ───────────────────────────────────────────────────────────────

def live_spans(text: Text, kinds: frozenset | None = None) -> list[Span]:
    """Marks as offset spans over the live text (a mark whose characters are
    all dead yields nothing). Default: every kind Markdown renders."""
    offsets = text.live_offsets()
    out: list[Span] = []
    for m in text.marks:
        if (kinds is None and m.kind == SUGGEST_DEL) or (kinds is not None and m.kind not in kinds):
            continue
        span = text.live_span(m, offsets)
        if span:
            out.append(Span(m.kind, dict(m.attrs), span[0], span[1]))
    return out


def markdown(text: Text, mode: str = "live", store: Store | None = None) -> str:
    """The field as Markdown. ``mode``: ``live`` (what is on screen),
    ``accepted`` (every open plan taken), ``rejected`` (every open plan
    dropped, which is what a directive is rendered from)."""
    if mode == "live" or store is None:
        return render(text.live_text(), live_spans(text))
    view = text.clone()
    open_plans = {p.id for p in store.plans.values() if p.status == "open"}
    if mode == "rejected":
        # Open plans undone on the clone: their segments hidden (killed by a
        # marker no op owns, so `_op` finds no killer), their suggest_del marks
        # dropped, their mark toggles reversed (a proposed unbold is not an unbold).
        for s in view.segs:
            if s.live and s.plan in open_plans:
                s.dead = "view"
        view.marks = [m for m in view.marks if not (m.kind == SUGGEST_DEL and m.attrs.get("plan") in open_plans)]
        _unapply_plan_marks(view, store, open_plans)
    elif mode == "accepted":
        for m in list(view.marks):
            if m.kind == SUGGEST_DEL and m.attrs.get("plan") in open_plans:
                view.delete(m.start, m.end, by="view")
                view.marks.remove(m)
    return render(view.live_text(), live_spans(view))


def plan_mark_ops(store: Store, plans: set[str]) -> list[Op]:
    """The applied ``text.mark`` ops of ``plans``, in apply order."""
    out: list[Op] = []
    for pid in plans:
        p = store.plans.get(pid)
        if p is None:
            continue
        out.extend(o for i in p.ops if (o := store.by_id.get(i)) is not None and o.kind == K.TEXT_MARK and o.conflict is None)
    out.sort(key=lambda o: store.position[o.id])
    return out


def _unapply_plan_marks(view: Text, store: Store, plans: set[str]) -> None:
    for o in reversed(plan_mark_ops(store, plans)):
        for mj in o.payload.get("added", []):
            mk = Mark.from_json(mj)
            if mk in view.marks:
                view.marks.remove(mk)
        for mj in o.payload.get("removed", []):
            mk = Mark.from_json(mj)
            if view.holds(mk.start) and view.holds(mk.end) and mk not in view.marks:
                view.marks.append(mk)


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


def blame_summary(text: Text) -> dict[str, int]:
    """Characters per actor over the live text."""
    out: dict[str, int] = {}
    for s in text.segs:
        if s.live:
            out[s.actor] = out.get(s.actor, 0) + len(s.chars)
    return out


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


def ink(store: Store, fid: str) -> list[Claim]:
    """Human live segments beyond the realized frontier: ``unsent`` past the
    hand-off frontier, ``sent`` between the two. Adjacent segments merge only
    when they share actor, plan and stage, so a word typed after the hand-off
    never reads as sent because it touches one typed before."""
    w = store.watermarks(fid)
    out: list[Claim] = []
    for b in store.feature_body(fid):
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
    (their latest hand-off, or their newest op on its blocks, whichever is
    later in this store's apply order): live loop segments as ``added``,
    loop-killed dead segments as ``removed`` (their text carried, positioned at
    the live offset where they were). A feature the person never touched paints
    nothing: there is no "since" to measure from."""
    w = store.watermarks(fid)
    since = max((store.position[o.id] for o in store.ops_on(_body_ids(store, fid))
                 if is_human(o.actor) and not o.conflict), default=-1)
    if not w.handoff and since < 0:
        return []

    def new(op_id: OpId) -> bool:
        return not in_frontier(op_id, w.handoff) and store.position.get(op_id, -1) > since

    out: list[Claim] = []
    for b in store.feature_body(fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            i = 0
            for s in t.segs:
                if s.live:
                    if s.actor == LOOP and not s.plan and new(s.op):
                        out.append(Claim(b.id, f, i, i + len(s.chars), "code", "added", s.actor, s.chars))
                    i += len(s.chars)
                elif s.dead:
                    killer = _op(store, s.dead)
                    if killer is not None and killer.actor == LOOP and new(s.dead):
                        out.append(Claim(b.id, f, i, i, "code", "removed", killer.actor, s.chars))
    return out


def plan_claims(store: Store, fid: str) -> list[Claim]:
    """Open plans' insertions (``open``), suggested deletions (``removed``) and
    mark toggles (``marked`` / ``unmarked``) on this feature, plus accepted
    plans' words (``accepted``)."""
    out: list[Claim] = []
    open_plans = {p.id for p in store.plans.values() if p.status == "open"}
    for b in store.feature_body(fid):
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
            for o in plan_mark_ops(store, open_plans):
                if o.payload.get("bid") != b.id or o.payload.get("path", "text") != f or o.payload.get("kind") == SUGGEST_DEL:
                    continue
                stage = "marked" if o.payload.get("on", True) else "unmarked"
                for mj in (o.payload.get("added") if stage == "marked" else o.payload.get("removed")) or []:
                    mk = Mark.from_json(mj)
                    if not (t.holds(mk.start) and t.holds(mk.end)):
                        continue
                    span = t.live_span(mk, offsets)
                    if span:
                        out.append(Claim(b.id, f, span[0], span[1], "plan", stage, o.actor, f"{mk.kind}:{lt[span[0]:span[1]]}"))
    return out


def _suggest_spans(t: Text, store: Store) -> list[Span]:
    open_plans = {p.id for p in store.plans.values() if p.status == "open"}
    return [sp for sp in live_spans(t, frozenset({SUGGEST_DEL})) if sp.attrs.get("plan") in open_plans]


def open_plans_on(store: Store, fid: str) -> list[str]:
    ids = _body_ids(store, fid)
    out = {p.id for p in store.plans.values() if p.status == "open" and p.target in ids}
    out.update(o.plan for o in store.ops_on(ids) if o.plan and not o.conflict
               and store.plans.get(o.plan) is not None and store.plans[o.plan].status == "open")
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


def _drifted(store: Store, fid: str) -> bool:
    """Bound code moved and nobody has looked at the prose since: the newest
    op among binding changes, prose edits (any actor) and realizations on this
    feature is a binding change (``bind.attach`` is a relocation, not drift)."""
    last_bind = last_prose = last_realized = -1
    for i, o in enumerate(store.ops_on(_body_ids(store, fid))):
        if o.conflict:
            continue
        if o.kind in (K.BIND_REFRESH, K.BIND_DETACH) and o.target.get("fid") == fid:
            last_bind = i
        elif o.kind.startswith("text."):
            last_prose = i
        elif o.kind == K.FEATURE_REALIZED and o.target.get("fid") == fid:
            last_realized = i
    return last_bind >= 0 and last_bind > max(last_prose, last_realized)


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
    focus = [t for m in _new_marks(store, fid, w) if m.kind == BOLD and (t := _mark_text(store, fid, m))]
    if focus:
        lines.append("")
        lines.append("Focus: " + "; ".join(f'"{t}"' for t in focus))
    consult = _all_links(store, fid)
    if consult:
        lines.append("")
        lines.append("Consult: " + ", ".join(f"{t} <{u}>" for t, u in consult))
    if ho and ho.payload.get("asked"):
        lines.append("")
        lines.append(f"Author asked: {ho.payload['asked']}")
    return "\n".join(lines)


def _human_changes(store: Store, fid: str, w: Watermarks) -> list[str]:
    out = []
    for c in ink(store, fid):
        if c.stage == "sent":
            out.append(f"added: \"{c.text}\"")
    for b in store.feature_body(fid):
        for f in text_fields(b.kind):
            for s in b.text(f).segs:
                if s.dead:
                    killer = _op(store, s.dead)
                    if killer and is_human(killer.actor) and not in_frontier(s.dead, w.realized) \
                            and in_frontier(s.dead, w.handoff):
                        out.append(f"removed: \"{s.chars}\"")
    return out


def _new_marks(store: Store, fid: str, w: Watermarks) -> list[Mark]:
    """Marks a person turned on since the last realization."""
    out: list[Mark] = []
    for o in store.ops_on(_body_ids(store, fid)):
        if o.conflict or o.kind != K.TEXT_MARK or not is_human(o.actor) or not o.payload.get("on", True):
            continue
        if in_frontier(o.id, w.realized):
            continue
        out.extend(Mark.from_json(mj) for mj in o.payload.get("added", []))
    return out


def _mark_text(store: Store, fid: str, m: Mark) -> str:
    """The live words under the mark ``m``'s placing op left on the page (a
    merge keeps the first placer; a mark since turned off yields nothing)."""
    for block, f in store.mark_holders(m.op):
        if store.feature_of(block.id) == fid:
            for cur in block.text(f).marks:
                if cur.op == m.op and cur.kind == m.kind:
                    return block.text(f).mark_text(cur)
    return ""


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

    The spans are the marks the document has *without* the open plans' toggles:
    a proposal to unbold a word leaves the word bold on the page, the way a
    proposal to delete words leaves them struck rather than gone, and the
    proposal itself is drawn over it from ``plan_claims``. The words a plan
    inserted are in ``runs`` and shown as the plan's, as before."""
    open_ids = {p.id for p in store.plans.values() if p.status == "open"}
    entry = {"id": b.id, "kind": b.kind, "level": b.level, "rank": b.rank, "head": store.block_head.get(b.id, 0),
             "fields": {}}
    for f in text_fields(b.kind):
        t = b.text(f)
        marked = t
        if open_ids:
            marked = t.clone()                      # same characters, the plans' mark toggles undone
            _unapply_plan_marks(marked, store, open_ids)
        entry["fields"][f] = {
            "text": t.live_text(),
            "runs": [r.__dict__ for r in attribution(t, merge=False)],
            "spans": [{"kind": s.kind, "attrs": s.attrs, "start": s.start, "end": s.end} for s in live_spans(marked)],
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
    return [p for p in store.plans.values()
            if p.status == "open" and (p.target is None or (p.target in store.blocks and store.blocks[p.target].live))]
