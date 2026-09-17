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
from codoc.calculus.ids import OpId
from codoc.calculus.inline_md import Span, render
from codoc.calculus.ops import Op, in_frontier
from codoc.calculus.store import Store, Watermarks
from codoc.calculus.text import BOLD, CITE, LINK, SUGGEST_DEL, Mark, Text

HUMAN = "human:"
LOOP = "loop"


# ── text views ───────────────────────────────────────────────────────────────

def live_spans(text: Text) -> list[Span]:
    """Marks as offset spans over the live text (a mark whose characters are
    all dead yields nothing)."""
    offsets: dict[tuple, int] = {}
    i = 0
    for s in text.segs:
        if s.live:
            for k in range(len(s.chars)):
                offsets[s.ref(k)] = i
                i += 1
    out: list[Span] = []
    for m in text.marks:
        if m.kind == SUGGEST_DEL:
            continue
        lo = _first_live(text, m, offsets, forward=True)
        hi = _first_live(text, m, offsets, forward=False)
        if lo is None or hi is None or hi < lo:
            continue
        out.append(Span(m.kind, dict(m.attrs), lo, hi + 1))
    return out


def _first_live(text: Text, m: Mark, offsets: dict, forward: bool) -> int | None:
    si, so = text.locate(m.start)
    ei, eo = text.locate(m.end)
    rng = range(si, ei + 1) if forward else range(ei, si - 1, -1)
    for i in rng:
        seg = text.segs[i]
        lo = so if i == si else 0
        hi = eo if i == ei else len(seg.chars) - 1
        ks = range(lo, hi + 1) if forward else range(hi, lo - 1, -1)
        if not seg.live:
            continue
        for k in ks:
            return offsets[seg.ref(k)]
    return None


def markdown(text: Text, mode: str = "live", store: Store | None = None) -> str:
    """The field as Markdown. ``mode``: ``live`` (what is on screen),
    ``accepted`` (every open plan taken), ``rejected`` (every open plan
    dropped, which is what a directive is rendered from)."""
    if mode == "live" or store is None:
        return render(text.live_text(), live_spans(text))
    view = Text([s for s in text.segs], list(text.marks)).clone()
    open_plans = {p.id for p in store.plans.values() if p.status == "open"}
    if mode == "rejected":
        for s in view.segs:
            if s.live and s.plan in open_plans:
                s.dead = "view"
        view.marks = [m for m in view.marks if not (m.kind == SUGGEST_DEL and m.attrs.get("plan") in open_plans)]
    elif mode == "accepted":
        for m in list(view.marks):
            if m.kind == SUGGEST_DEL and m.attrs.get("plan") in open_plans:
                view.delete(m.start, m.end, by="view")
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
    op: OpId = ""


def attribution(text: Text, merge: bool = True) -> list[Run]:
    """Live segments as offset runs; adjacent runs with the same actor and plan
    merge. Word snapping for display is the renderer's business."""
    out: list[Run] = []
    i = 0
    for s in text.segs:
        if not s.live:
            continue
        r = Run(i, i + len(s.chars), s.actor, s.at, s.plan, s.op)
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


def _blocks_of(store: Store, fid: str) -> list[Block]:
    return store.feature_body(fid)


def ink(store: Store, fid: str) -> list[Claim]:
    """Human live segments beyond the realized frontier: ``unsent`` past the
    hand-off frontier, ``sent`` between the two."""
    w = store.watermarks(fid)
    out: list[Claim] = []
    for b in _blocks_of(store, fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            for r in attribution(t):
                if not r.actor.startswith(HUMAN) or r.plan:
                    continue
                if in_frontier(r.op, w.realized):
                    continue
                stage = "sent" if in_frontier(r.op, w.handoff) else "unsent"
                out.append(Claim(b.id, f, r.start, r.end, "human", stage, r.actor, t.live_text()[r.start:r.end]))
    return out


def ground(store: Store, fid: str, since: dict | None = None) -> list[Claim]:
    """What the loop did to this feature since ``since`` (default: the latest
    hand-off frontier): live loop segments as ``added``, loop-killed dead
    segments as ``removed`` (their text carried, positioned at the live offset
    where they were)."""
    w = store.watermarks(fid)
    since = since if since is not None else w.handoff
    out: list[Claim] = []
    for b in _blocks_of(store, fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            i = 0
            for s in t.segs:
                if s.live:
                    if s.actor == LOOP and not s.plan and not in_frontier(s.op, since):
                        out.append(Claim(b.id, f, i, i + len(s.chars), "code", "added", s.actor, s.chars))
                    i += len(s.chars)
                elif s.dead and s.dead != "view":
                    killer = _op(store, s.dead)
                    if killer is not None and killer.actor == LOOP and not in_frontier(s.dead, since):
                        out.append(Claim(b.id, f, i, i, "code", "removed", killer.actor, s.chars))
    return out


def plan_claims(store: Store, fid: str) -> list[Claim]:
    """Open plans' insertions (``open``) and suggested deletions (``removed``)
    on this feature, plus accepted plans' words (``accepted``)."""
    out: list[Claim] = []
    for b in _blocks_of(store, fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            for r in attribution(t):
                if not r.plan or r.plan not in store.plans:
                    continue
                st = store.plans[r.plan].status
                if st == "rejected":
                    continue
                out.append(Claim(b.id, f, r.start, r.end, "plan", "open" if st == "open" else "accepted", r.actor,
                                 t.live_text()[r.start:r.end]))
            for sp in _suggest_spans(t, store):
                out.append(Claim(b.id, f, sp.start, sp.end, "plan", "removed", store.plans[sp.attrs["plan"]].actor,
                                 t.live_text()[sp.start:sp.end]))
    return out


def _suggest_spans(t: Text, store: Store) -> list[Span]:
    offsets: dict[tuple, int] = {}
    i = 0
    for s in t.segs:
        if s.live:
            for k in range(len(s.chars)):
                offsets[s.ref(k)] = i
                i += 1
    out = []
    for m in t.marks:
        if m.kind != SUGGEST_DEL or store.plans.get(m.attrs.get("plan"), None) is None:
            continue
        if store.plans[m.attrs["plan"]].status != "open":
            continue
        lo = _first_live(t, m, offsets, True)
        hi = _first_live(t, m, offsets, False)
        if lo is not None and hi is not None and hi >= lo:
            out.append(Span(SUGGEST_DEL, dict(m.attrs), lo, hi + 1))
    return out


def open_plans_on(store: Store, fid: str) -> list[str]:
    ids = {b.id for b in _blocks_of(store, fid)}
    out = []
    for p in store.plans.values():
        if p.status != "open":
            continue
        if p.target in ids or any(_op_touches(store, i, ids) for i in p.ops):
            out.append(p.id)
    return out


def _op(store: Store, op_id: OpId) -> Op | None:
    return store.by_id.get(op_id)


def _op_touches(store: Store, op_id: OpId, ids: set[str]) -> bool:
    o = _op(store, op_id)
    if o is None:
        return False
    return o.target.get("bid") in ids or o.payload.get("bid") in ids


# ── phase ────────────────────────────────────────────────────────────────────

PHASES = ("realizing", "edited", "queued", "proposed", "planned", "drifted", "synced")


def phase(store: Store, fid: str, realizing: set[str] = frozenset()) -> str:
    """The single lifecycle state of a feature. Doc wins: a person's pending
    intent outranks anything the code side has to say. ``realizing`` is the
    one ephemeral input (a lease outside the store)."""
    if fid in realizing:
        return "realizing"
    w = store.watermarks(fid)
    claims = ink(store, fid)
    if any(c.stage == "unsent" for c in claims):
        return "edited"
    queued = _queued(store, fid, w)
    bound = any(b.feature == fid for b in store.bindings.values())
    if queued and not bound:
        return "planned"
    if queued:
        return "queued"
    if open_plans_on(store, fid):
        return "proposed"
    if _drifted(store, fid, w):
        return "drifted"
    return "synced"


def _queued(store: Store, fid: str, w: Watermarks) -> bool:
    if not w.handoff:
        return False
    return any(not in_frontier(op, w.realized) for op in [_max_op(w.handoff)])


def _max_op(frontier: dict) -> OpId:
    session, seq = max(frontier.items(), key=lambda kv: kv[1]) if frontier else ("", -1)
    return f"{session}:{seq}"


def _drifted(store: Store, fid: str, w: Watermarks) -> bool:
    """Bound code moved and nobody has looked at the prose since: the newest
    op among binding changes, prose edits (any actor) and realizations on this
    feature is a binding change."""
    ids = {b.id for b in _blocks_of(store, fid)}
    last_bind = last_prose = last_realized = -1
    for i, o in enumerate(store.ops):
        if o.conflict:
            continue
        if o.kind in (K.BIND_REFRESH, K.BIND_DETACH) and o.target.get("fid") == fid:
            last_bind = i
        elif o.kind.startswith("text.") and (o.payload.get("bid") in ids or o.target.get("bid") in ids):
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
    focus = [m for m in _new_marks(store, fid, w) if m.kind == BOLD]
    if focus:
        lines.append("")
        lines.append("Focus: " + "; ".join(f'"{_mark_text(store, fid, m)}"' for m in focus))
    consult = _all_links(store, fid)
    if consult:
        lines.append("")
        lines.append("Consult: " + ", ".join(f"{t} <{u}>" for t, u in consult))
    scope = sorted({f"{b2}#{s}" if s else b2 for b2, s in _cites(store, fid)})
    if scope:
        lines.append("")
        lines.append("Edit only: " + ", ".join(scope))
    if ho and ho.payload.get("asked"):
        lines.append("")
        lines.append(f"Author asked: {ho.payload['asked']}")
    return "\n".join(lines)


def _human_changes(store: Store, fid: str, w: Watermarks) -> list[str]:
    out = []
    for c in ink(store, fid):
        if c.stage == "sent":
            out.append(f"added: \"{c.text}\"")
    for b in _blocks_of(store, fid):
        for f in text_fields(b.kind):
            for s in b.text(f).segs:
                if s.dead and s.dead != "view":
                    killer = _op(store, s.dead)
                    if killer and killer.actor.startswith(HUMAN) and not in_frontier(s.dead, w.realized) \
                            and in_frontier(s.dead, w.handoff):
                        out.append(f"removed: \"{s.chars}\"")
    return out


def _new_marks(store: Store, fid: str, w: Watermarks) -> list[Mark]:
    ids = {b.id for b in _blocks_of(store, fid)}
    out: list[Mark] = []
    for o in store.ops:
        if o.conflict or o.kind != K.TEXT_MARK or not o.actor.startswith(HUMAN):
            continue
        if o.payload.get("bid") not in ids or in_frontier(o.id, w.realized):
            continue
        for mj in o.payload.get("added", []):
            out.append(Mark.from_json(mj))
    return out


def _mark_text(store: Store, fid: str, m: Mark) -> str:
    for b in _blocks_of(store, fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            if t.holds(m.start):
                spans = live_spans(t)
                lt = t.live_text()
                for sp in spans:
                    if sp.kind == m.kind and sp.attrs == m.attrs:
                        return lt[sp.start:sp.end]
    return ""


def _all_links(store: Store, fid: str) -> list[tuple[str, str]]:
    out = []
    for b in _blocks_of(store, fid):
        for f in text_fields(b.kind):
            t = b.text(f)
            lt = t.live_text()
            for sp in live_spans(t):
                if sp.kind == LINK:
                    out.append((lt[sp.start:sp.end], sp.attrs.get("href", "")))
    return out


def _cites(store: Store, fid: str) -> list[tuple[str, str]]:
    out = []
    for b in _blocks_of(store, fid):
        for f in text_fields(b.kind):
            for m in b.text(f).marks:
                if m.kind == CITE:
                    out.append((m.attrs.get("file", ""), m.attrs.get("symbol", "")))
    return out


# ── history ──────────────────────────────────────────────────────────────────

def history(store: Store, fid: str) -> list[Op]:
    """Structural ops on the heading plus text ops on the blocks the feature
    currently dominates, oldest first. Ops on blocks it dominated at the time
    but no longer does are omitted."""
    ids = {b.id for b in _blocks_of(store, fid)}
    out = []
    for o in store.ops:
        if o.conflict:
            continue
        tb = o.target.get("bid") or o.target.get("fid") or o.payload.get("bid")
        if tb in ids or o.target.get("into") in ids:
            out.append(o)
    return out


def dead_cites(store: Store, known: set[tuple[str, str]]) -> list[tuple[str, Mark]]:
    out = []
    for b in store.ordered():
        for f in text_fields(b.kind):
            for m in b.text(f).marks:
                if m.kind == CITE and (m.attrs.get("file", ""), m.attrs.get("symbol", "")) not in known:
                    out.append((b.id, m))
    return out


# ── projection ───────────────────────────────────────────────────────────────

def projection(store: Store, realizing: set[str] = frozenset()) -> dict:
    """What an editor needs: every live block with its text, runs, spans, plan
    claims and its feature's phase. Dead segments are omitted; the editor asks
    for ``ground`` per feature when History is on."""
    blocks = []
    phases: dict[str, str] = {}
    for b in store.ordered():
        if b.kind == HEADING:
            phases[b.id] = phase(store, b.id, realizing)
        entry = {"id": b.id, "kind": b.kind, "level": b.level, "rank": b.rank, "head": store.block_head.get(b.id, 0),
                 "fields": {}}
        for f in text_fields(b.kind):
            t = b.text(f)
            entry["fields"][f] = {
                "text": t.live_text(),
                "runs": [r.__dict__ for r in attribution(t)],
                "spans": [{"kind": s.kind, "attrs": s.attrs, "start": s.start, "end": s.end} for s in live_spans(t)],
                "suggest_del": [{"plan": s.attrs["plan"], "start": s.start, "end": s.end} for s in _suggest_spans(t, store)],
            }
        for k, v in b.content.items():
            if k not in text_fields(b.kind):
                entry["fields"][k] = v
        blocks.append(entry)
    return {"head": store.pos, "frontier": dict(store.frontier), "blocks": blocks, "phases": phases,
            "plans": [p.to_json() for p in store.plans.values() if p.status == "open"]}
