"""Derived views over the store (docs/model.md, Derived views). Pure functions;
nothing here writes.

Every user-facing question is one of these: what does a feature say, who wrote
which word, what is the person's unsent ink, what did the loop change under
them, which plans are open here, where is the feature in its lifecycle, what
happened to it over time, what the document exports as.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from codoc.calculus import block_md
from codoc.calculus.block_md import BlockSpec
from codoc.calculus.doc import (
    HEADING, LAYOUT_KEY, PROSE, SOURCE, Block, TreeNode, fold_tree, is_source_field, layout_entry,
    registered_layouts,
)
from codoc.calculus.ids import LOOP, OpId, is_human
from codoc.calculus.inline_md import Span, render
from codoc.calculus.ops import BLOCK_SET, Op, in_frontier
from codoc.calculus.store import Store
from codoc.calculus.text import CITE, COMMENT, PROPOSALS, SUGGEST_DEL, SUGGEST_MARK, Mark, Seg, Text, struck_chars

# A feature with pending intent: the document wins over a machine's repair,
# whatever its size (docs/concurrency.md, The machine gate).
HELD = ("edited", "queued", "planned")
_SPACE = re.compile(r"\s+")


def one_line(s) -> str:
    """Free text as it may stand inside one line of a directive: every run of
    whitespace, each kind of line break included, as one space. The directive
    is read line by line (a ``Consult:`` line is a page to fetch,
    ``Author asked:`` is the person's request), so words that broke their line
    would write lines of their own. Every client's text the directive quotes
    inside a line of its own goes through here."""
    return _SPACE.sub(" ", str(s))


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


def in_mode(text: Text, mode: str, store: Store | None) -> Text:
    """``text`` as it reads in a read mode. ``live``: the field as stored, an
    open plan's words included. ``rejected``: without any open plan's words.
    ``accepted``: with every open plan taken. ``loop``: what the reflect pass
    reads, with the loop's own open plans taken and every open plan that
    answers a hand-off (an op of it cites one in ``caused_by``) and is not a
    build plan, and every other open plan's words left out. Either kind
    describes code the loop would otherwise describe a second time; a build
    plan comes before the code.
    Returns ``text`` itself when the mode changes nothing, else a clone."""
    if mode == "live" or store is None:
        return text
    open_plans = store.open_plans
    taken = (set(open_plans) if mode == "accepted" else
             {pid for pid, p in open_plans.items()
              if p.actor == LOOP or not p.build and any((o := store.by_id.get(i)) is not None and o.caused_by
                                                        for i in p.ops)}
             if mode == "loop" else set())
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
    view = in_mode(text, mode, store)
    return render(view.live_text(), live_spans(view))


def text_string(kind: str, path: str, text: Text, mode: str = "live", store: Store | None = None) -> str:
    """A Text field as a string: the raw text of a source field, inline
    Markdown for any other."""
    if is_source_field(kind, path):
        return in_mode(text, mode, store).live_text()
    return markdown(text, mode, store)


def field_text(block: Block, path: str, mode: str = "live", store: Store | None = None) -> str:
    return text_string(block.kind, path, block.text(path), mode, store)


def title(store: Store, fid: str, mode: str = "live") -> str:
    return markdown(store.blocks[fid].text(), mode, store)


def unvetted_block(store: Store, b: Block) -> bool:
    """Whether a still-open plan added ``b``. Until the verdict such a block is
    the plan's: it says nothing in the ``rejected`` mode, the directive leaves
    it out, and the lift pass leaves it alone, since its fetch would run on a
    stranger's address."""
    return b.added_plan in store.open_plans


def set_value(payload: dict) -> tuple[bool, object]:
    """What a content ``block.set`` wrote, as (absent, value)."""
    return (True, None) if payload.get("unset") else (False, payload.get("value"))


_SHAPE = ("kind", "level")      # the block's own attributes a `block.set` writes; any other path is a content field


def _field(b: Block, path: str) -> tuple[bool, object]:
    """Field ``path`` of ``b`` as it is, as (absent, value)."""
    return (False, getattr(b, path)) if path in _SHAPE else (path not in b.content, b.content.get(path))


def _wrote(op: Op, path: str) -> tuple[bool, object]:
    """What ``block.set`` ``op`` wrote to ``path``, as (absent, value). A kind
    set writes the level too."""
    if path == "level" and op.target.get("path") == "kind":
        return False, op.payload.get("level")
    return (False, op.payload.get("value")) if path in _SHAPE else set_value(op.payload)


def _replaced(op: Op, path: str):
    return op.payload.get("prev_level" if path == "level" and op.target.get("path") == "kind" else "prev")


def _sets_on(op: Op, b: Block, path: str) -> bool:
    """Whether ``op`` is a ``block.set`` on ``path`` of ``b``: a kind set is
    one on the level too, since it writes both."""
    return op.kind == BLOCK_SET and op.target.get("bid") == b.id \
        and op.target.get("path") in (path, "kind" if path == "level" else path)


def _sets(store: Store, b: Block, path: str) -> list:
    """The sets on ``path`` of ``b``, oldest first, as [op, the value it
    replaced, plan]. A content field keeps them in ``Block.prior``; ``kind``
    and ``level`` are read from the block's own ops."""
    if path not in _SHAPE:
        return b.prior.get(path) or []
    return [[o.id, _replaced(o, path), o.plan or ""] for o in store.ops_on([b.id])
            if o.conflict is None and _sets_on(o, b, path)]


def without_plans(store: Store, b: Block, path: str, past: tuple[str, ...],
                  undoing: list[Op] | tuple = ()) -> tuple[bool, object, Op | None]:
    """Field ``path`` of ``b`` with plans' sets taken out, as (absent, value,
    by): the one rule for the value from before the plans (docs/model.md,
    Read modes), for a content field and for ``kind`` and ``level`` alike.
    ``by`` is the oldest set taken out, whose replaced value this is, or None.
    ``Block.prior`` keeps each content set with the value it replaced; a kind
    or level set carries it in its payload. The walk goes from the newest set
    back, starting from the field as it is. A set whose plan's status is in
    ``past``, or that is one of ``undoing`` (the ops a verdict is taking back,
    whatever their plan's status), is taken out: when it wrote the value in
    hand, the value it replaced is the one in hand now; otherwise a verdict
    already took it back (its own reject, or the reject of a plan that set the
    field twice), and it is passed. Anything else ends the walk: a person's
    set, an accepted plan's, one whose plan's status ``past`` leaves out, and a
    set whose op a fold archived. So a value someone set since stands. A value
    a lift wrote since matches no set, so it stays."""
    sets = _sets(store, b, path)
    taking = {o.id: o for o in undoing if _sets_on(o, b, path)}
    # A set a realization covered has left `prior`; what it replaced is in its payload.
    have = {e[0] for e in sets}
    sets = [[i, _replaced(o, path), o.plan or ""] for i, o in taking.items() if i not in have] + sets
    now, by = _field(b, path), None
    for op_id, prev, pid in reversed(sets):
        plan = store.plans.get(pid) if pid else None
        if op_id not in taking and (plan is None or plan.status not in past):
            break
        was = (prev is None, prev)
        if was == now:
            continue
        op = taking.get(op_id) or store.by_id.get(op_id)
        if op is None:
            break
        if _wrote(op, path) != now:
            continue        # a verdict already took this set back
        now, by = was, op
    return now[0], now[1], by


def rejected_content(store: Store, b: Block) -> dict:
    """``b.content`` as the ``rejected`` mode reads its JSON fields. A Text
    field carries its plan on every character, so the read modes hide an open
    plan's words; a JSON field (a url block's ``url``, a code block's ``lang``)
    carries none. While an open plan has a set on the field it reads as
    ``without_plans`` takes every open and rejected plan's set out of it: the
    owner's value. With none open it is the field as it is, which every reject
    has already put back. The export, the directive, the kind codecs and the
    lift read this, so a value a suggester proposed is neither shown, nor put in
    front of the agent, nor fetched before a verdict, and the owner's block is
    not hidden meanwhile."""
    out = dict(b.content)
    for k, sets in b.prior.items():
        if any(e[2] in store.open_plans for e in sets):
            absent, value, _ = without_plans(store, b, k, ("open", "rejected"))
            if absent:
                out.pop(k, None)
            else:
                out[k] = value
    return out


def block_spec(store: Store, b: Block, mode: str = "live") -> BlockSpec | None:
    """``b`` as Markdown says it, or None when it says nothing in this mode: an
    empty paragraph or item, an empty source, a block holding only an open
    plan's words under ``rejected`` or one an open plan added
    (``unvetted_block``), a kind left out of the export. Under ``rejected`` its
    JSON fields are ``rejected_content``'s."""
    if mode == "rejected" and unvetted_block(store, b):
        return None
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
    content = rejected_content(store, b) if mode == "rejected" else b.content
    plain = {k: v for k, v in content.items() if k not in b.layout.text}
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
    return one_line(f"{b.kind}: {'; '.join(words)}") if words else ""


def thread_id(b: Block) -> str:
    """The thread a comment block is a message of: its ``thread`` when that is
    a non-empty string, else its own id. The margin reads it the same way
    (``comments.ts``, ``threadIdOf``); ``thread-cases.json`` pins both."""
    t = b.content.get("thread")
    return t if isinstance(t, str) and t else b.id


def first_message(store: Store, b: Block) -> Block | None:
    """The message that speaks for ``b``'s thread, whose ``status``, ``scope``
    and ``anchor_text`` are the thread's: the block the thread id names, when
    that is a live comment of this thread, else the thread's earliest live
    message in document order. A reply's own fields decide nothing, wherever
    it was placed. The margin (``threadsOf``), the directive and the policy
    on who may resolve a thread all read this. Ordered by rank, not
    ``store.ordered()``, so the policy can answer from a view without the
    document's order (``validate._policy_view``)."""
    tid = thread_id(b)
    named = store.blocks.get(tid)
    if named is not None and named.live and named.kind == COMMENT and thread_id(named) == tid:
        return named
    return min((x for x in store.blocks.values() if x.live and x.kind == COMMENT and thread_id(x) == tid),
               key=lambda x: (x.rank, x.id), default=None)


def description(store: Store, fid: str, mode: str = "live") -> str:
    """The feature's own body as Markdown: its paragraphs, list items, tables,
    diagrams and code, up to its first child heading. Other kinds (an image, a
    reference, a comment) are not part of what the feature says."""
    specs = [s for b in store.body(fid)[1:]
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
    path: str           # "" for a whole block
    start: int          # live offsets, half-open; a removal is zero-width at its place
    end: int
    channel: str        # human | plan | code
    stage: str          # human: unsent | sent; plan: open | removed | marked | unmarked; code: added | removed
    actor: str
    text: str = ""
    plan: str = ""      # the plan, on the plan channel


def prose_blocks(store: Store, fid: str) -> list[Block]:
    """The feature's blocks that are the document. A comment hangs off words
    rather than standing among them, so writing one is not editing the prose:
    it must not ink the feature, turn it `edited`, or offer a hand-off of a
    note."""
    return [b for b in store.body(fid) if b.layout.flow]


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
                    out.append(Claim(b.id, f, r.start, r.end, "plan", "open", r.actor, lt[r.start:r.end], r.plan))
            for sp in _suggest_spans(t, store.open_plans):
                pid = sp.attrs["plan"]
                out.append(Claim(b.id, f, sp.start, sp.end, "plan", "removed", store.plans[pid].actor,
                                 lt[sp.start:sp.end], pid))
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
                                     f"{m.attrs.get('mark', '')}:{lt[span[0]:span[1]]}", plan.id))
    return out


def _suggest_spans(t: Text, plans) -> list[Span]:
    """The characters each ``suggest_del`` of a plan in ``plans`` lists that
    are still live here, as offset spans: what accepting it kills. A word typed
    inside the struck range since is not among them."""
    offsets = t.live_offsets()
    out: list[Span] = []
    for m in t.proposals(kind=SUGGEST_DEL):
        pid = m.attrs.get("plan")
        if pid not in plans:
            continue
        runs: list[list[int]] = []
        for i in sorted(offsets[(o, k)] for o, k, _ in struck_chars(m) if (o, k) in offsets):
            if runs and runs[-1][1] == i:
                runs[-1][1] = i + 1
            else:
                runs.append([i, i + 1])
        out.extend(Span(SUGGEST_DEL, {"plan": pid}, a, b) for a, b in runs)
    return out


def open_plans_on(store: Store, fid: str) -> list[str]:
    """The plans offered for a verdict (``offered_plans``) that this feature
    is waiting on: one targeting a block of its body, or one whose ops landed
    in it. A plan whose target is dead stays open but cannot be decided, so it
    does not make the feature ``proposed``."""
    offered = {p.id for p in offered_plans(store)}
    if not offered:
        return []
    ids = {b.id for b in store.body(fid)}
    out = {pid for pid in offered if store.plans[pid].target in ids}
    out.update(o.plan for o in store.ops_on(ids) if o.plan in offered and not o.conflict)
    return sorted(out)


# ── phase ────────────────────────────────────────────────────────────────────

def editors(store: Store, fid: str) -> list[str]:
    """Who has edited this feature since its last hand-off: the actors of its
    unsent ink and unsent removals. A feature with any is `edited`, and the
    editor's ⌘S hands off only the ones that name the viewer."""
    return sorted({c.actor for c in ink(store, fid) + removals(store, fid) if c.stage == "unsent"})


def removals(store: Store, fid: str) -> list[Claim]:
    """What a person removed from this feature that no realization has
    settled: each run of characters they killed, zero-width at the live offset
    where it was, then each whole block of the description they deleted
    (``removed_blocks``, path ""). ``unsent`` outside the hand-off frontier,
    ``sent`` inside.

    A removal is pending intent as much as a typed word (emptying a title
    makes the feature `edited`, and the directive carries it as a `removed:`
    line), but it is not ink, which the editor draws as what the person wrote.
    A removal a verdict made counts only when the verdict adopted its plan
    (``Plan.adopted``) and the words were not the plan's own; it is then the
    removal of the person who accepted. A plan taking back its own words is
    the plan getting smaller, not a removal (``_answered``)."""
    w = store.watermarks(fid)
    verdicts: dict | None = None

    def stage(op_id: str) -> str:
        return "" if in_frontier(op_id, w.realized) else "sent" if in_frontier(op_id, w.handoff) else "unsent"

    out: list[Claim] = []
    for b in prose_blocks(store, fid):
        for f in b.layout.text:
            i = 0
            for sg in b.text(f).segs:
                if sg.live:
                    i += len(sg.chars)
                elif is_human(sg.dead_actor) and (st := stage(sg.dead)):
                    verdicts = verdicts if verdicts is not None else _verdict_ops(store)
                    if not _answered(store, verdicts, sg):
                        out.append(Claim(b.id, f, i, i, "human", st, sg.dead_actor, sg.chars))
    for b in removed_blocks(store, fid):
        if st := stage(b.dead):
            words = " ".join(b.text(f).live_text() for f in b.layout.text).strip()
            out.append(Claim(b.id, "", 0, 0, "human", st, b.dead_actor, words))
    return out


def _verdict_ops(store: Store) -> dict:
    """The ops that decided a plan, each to its plan. Read from the plans,
    which a fold keeps, so a character a verdict killed is known as one after
    the fold too."""
    return {p.verdict: p for p in store.plans.values() if p.verdict}


def _answered(store: Store, verdicts: dict, sg: Seg) -> bool:
    """Whether this dead segment's removal is nobody's request: the plan that
    minted the words took them back itself (one of its own ops killed them: a
    contributor correcting their own proposal, docs/concurrency.md section 9),
    or a verdict killed them and its plan was not adopted or the words were
    that plan's own."""
    own = store.plans.get(sg.plan) if sg.plan else None
    if own is not None and sg.dead in own.ops:
        return True
    p = verdicts.get(sg.dead)
    return p is not None and (sg.plan == p.id or not p.adopted)


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
    if editors(store, fid):
        return "edited"
    if fid in realizing:
        return "realizing"
    if store.watermarks(fid).queued:
        return "queued" if any(b.feature == fid for b in store.bindings.values()) else "planned"
    if open_plans_on(store, fid):
        return "proposed"
    if _drifted(store, fid):
        return "drifted"
    return "synced"


def queued(store: Store) -> list[str]:
    """Features with a hand-off not yet realized, in document order, then the
    dead headings whose removal is still waiting. A watermark fact: a feature
    being edited or built right now is still queued."""
    pending = {fid for fid, w in store.watermarks_by_fid.items() if w.queued}
    out = [h.id for h in store.headings() if h.id in pending]
    out.extend(sorted(fid for fid in pending if fid in store.blocks and not store.blocks[fid].live))
    return out


def in_flight(store: Store) -> list[str]:
    """Features with work under way whatever their phase shows: queued, or
    one a plan offered for a verdict is about (``plan_features``). ``phase``
    shows `edited` over both, and the editor still keeps such a heading a
    heading."""
    out = set(queued(store))
    for p in offered_plans(store):
        out.update(plan_features(store, p))
    return sorted(out)


def reads_its_ops(store: Store, fid: str) -> bool:
    """Whether a view still reads this feature's ops and not only its state,
    which is what a snapshot may not fold away.

    One does: an open plan's verdict undoes the plan's own ops by identity, so
    a fold may not take them.

    Everything else reads state. A segment carries its minter's actor and clock
    and its killer's; a mark carries its actor, its placing op and its clock; a
    block carries who added it and the values the author's unrealized sets
    replaced; a watermark carries when the code last moved and when a
    realization last settled; a dead heading carries what it owned. So the
    directive's `removed:`, `Focus:` and kind lines, `ground`'s reds and
    greens, drift, a comment's owner and a deleted heading's settle read the
    document, and a hand-off given after a fold still finds what it needs.
    """
    b = store.blocks.get(fid)
    return b is not None and b.live and bool(open_plans_on(store, fid))


def touched_at(store: Store, fid: str, *, human_only: bool = False) -> str:
    """The clock of the newest thing anyone did to this feature's prose: a
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
    (`ops.moved_code`: a detach, or new hashes on a binding the feature
    already had; an attach of a new symbol is not drift),
    when anybody last touched the prose, and when a realization last settled
    it."""
    w = store.watermarks(fid)
    if not w.code_at:
        return False
    return w.code_at > max(w.realized_at, touched_at(store, fid))


# ── as last realized ─────────────────────────────────────────────────────────

def realized_content(store: Store, b: Block, realized: dict) -> dict:
    """``b``'s content as its author last had it realized, as a kind's codec
    reads content (``kinds.plain``): each Text field without what the author
    wrote or removed since ``realized``, by identity, in the ``rejected`` mode;
    each other field as it was before the author's first set since
    (``Block.prior``), else as ``rejected_content`` reads it. Only the
    author's, because ``lower`` speaks for them: a diagram the loop lifted from
    the code is already in the code, and an open plan's value is nobody's yet."""
    out = {k: text_string(b.kind, k, _at_realized(store, v, realized), "rejected", store)
           if k in b.layout.text and isinstance(v, Text) else v for k, v in rejected_content(store, b).items()}
    for k, sets in b.prior.items():
        first = next((e for e in sets if not in_frontier(e[0], realized)
                      and (not e[2] or (p := store.plans.get(e[2])) is not None and p.adopted)), None)
        if first is None:
            continue
        if first[1] is None:
            out.pop(k, None)
        else:
            out[k] = first[1]
    return out


def _at_realized(store: Store, text: Text, realized: dict) -> Text:
    t = text.clone()
    t.segs = [s for s in t.segs if in_frontier(s.op, realized) or not _authored(store, s)]
    verdicts = _verdict_ops(store)
    for s in t.segs:
        if s.dead and not in_frontier(s.dead, realized) and is_human(s.dead_actor) and not _answered(store, verdicts, s):
            s.dead = None
    t.marks = [m for m in t.marks if t.holds(m.start) and t.holds(m.end)]
    return t


def _authored(store: Store, sg: Seg) -> bool:
    """Whether these characters are the author's words: a person minted them,
    or a plan the author adopted did (``Plan.adopted``)."""
    p = store.plans.get(sg.plan) if sg.plan else None
    return is_human(sg.actor) or p is not None and p.adopted


# ── history ──────────────────────────────────────────────────────────────────

def history(store: Store, fid: str) -> list[Op]:
    """Every op recorded against a block of the feature's body, oldest first,
    conflicted ones included (their ``conflict`` says why they did nothing).
    Ops on blocks that were in its body then and are not now are omitted."""
    return store.ops_on([b.id for b in store.body(fid)])


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
    own state, so a caller may memoize it on ``block_head[b.id]``
    (docs/model.md, Cache keys).

    The spans are the document's own marks, and nothing else: a plan never
    changes them, so a proposal to unbold a word leaves the word bold, the way
    a proposal to delete words leaves them struck rather than gone. What a
    plan proposes is in the feature's claims (``plan_claims``)."""
    entry = {"id": b.id, "kind": b.kind, "level": b.level, "rank": b.rank, "head": store.block_head.get(b.id, 0),
             "fields": {}, **layout_entry(b.kind, b.layout)}     # an editor without the kind can still draw it
    for f in b.layout.text:
        t = b.text(f)
        entry["fields"][f] = {
            "text": t.live_text(),
            "runs": [r.__dict__ for r in attribution(t, merge=False)],
            "spans": [{"kind": s.kind, "attrs": s.attrs, "start": s.start, "end": s.end} for s in live_spans(t)],
        }
    for k, v in b.content.items():
        if k not in b.layout.text:
            entry["fields"][k] = v
    return entry


def feature_claims(store: Store, fid: str) -> list[dict]:
    """Everything the editor draws on a feature's prose: the person's ink, the
    open plans' words, cuts and mark toggles, and the code's ground."""
    return [c.__dict__ for c in ink(store, fid) + plan_claims(store, fid) + ground(store, fid)]


def projection(store: Store, realizing: set[str] = frozenset(), phases: dict[str, str] | None = None,
               entry=block_entry, claims=feature_claims) -> dict:
    """What an editor needs: every live block with its text, runs and spans,
    each feature's phase and claims, who edited each `edited` one
    (``editors``), the plans offered for a verdict, the features with work in
    flight, and bindings. ``phases``, ``entry`` and ``claims`` may be passed in
    by a caller that already computed or memoized them."""
    blocks = []
    phases = dict(phases) if phases is not None else {}
    for b in store.ordered():
        if b.kind == HEADING and b.id not in phases:
            phases[b.id] = phase(store, b.id, realizing)
        blocks.append(entry(store, b))
    by_feature: dict[str, list[str]] = {}
    for bd in store.bindings.values():
        by_feature.setdefault(bd.feature, []).append(bd.symbol)
    return {"store": store.id, "head": store.pos, "frontier": dict(store.frontier), "blocks": blocks,
            "phases": phases, "editors": {fid: editors(store, fid) for fid, p in phases.items() if p == "edited"},
            "plans": [{**p.to_json(), "hands_off": accept_handoffs(store, p)} for p in offered_plans(store)],
            "in_flight": in_flight(store), "bindings": {"by_feature": by_feature},
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
    """The features that accepting ``plan`` hands off, in the same batch: a
    plan that asks for code (``Plan.asks_for_code``) queues the work. Any other
    plan is about words, and accepting it takes the words only."""
    return plan_features(store, plan) if plan.asks_for_code else []


def offered_plans(store: Store) -> list:
    """Open plans a person can see, which are the ones offered for a verdict:
    one with no target or a live one, and one whose target was deleted but
    whose words or proposal records are still live on a live block (it wrote
    into another feature). Every plan ``plan_claims`` draws is one of these. A
    plan with nothing left on the page is not offered; it comes back with its
    target."""
    return [p for p in store.open_plans.values()
            if p.target is None or (p.target in store.blocks and store.blocks[p.target].live) or _on_page(store, p)]


def _on_page(store: Store, plan) -> bool:
    for op_id in plan.ops:
        for b, f in store.holders(op_id):
            if b.live and any(s.live and s.op == op_id for s in b.text(f).segs):
                return True
        for b, f, m in store.placed_by(op_id):
            if b.live and m.kind in PROPOSALS and b.text(f).mark_refs(m):
                return True
    return False


# ── export ───────────────────────────────────────────────────────────────────

def export_spec(store: Store, b: Block) -> BlockSpec | None:
    """One block of ``tree.codoc``: ``block_spec`` in the ``rejected`` mode, so
    an open plan's words are left out and a block holding nothing else is left
    out whole. A function of the block and of which plans are open, so a
    caller may memoize it on ``(block_head[b.id], plans_head)``."""
    return block_spec(store, b, "rejected")


def export_markdown(store: Store, spec=export_spec) -> str:
    """``tree.codoc``: every live block's ``export_spec``, headings with their
    ids. The file is one-way: nothing reads it back into a store, and its
    first line (``block_md.GENERATED``) says a hand edit is overwritten."""
    return block_md.render([s for b in store.ordered() if (s := spec(store, b)) is not None], ids=True, generated=True)
