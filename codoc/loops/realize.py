"""Document → code: hand-offs, the realize queue, and the realization watermark.

A person's edits are already ops on the trunk; nothing here infers them.

- ``handoff``: record ``feature.handoff`` for a feature, with the frontier it
  drains, the commit the work starts from, the person's own words, and
  whether a deletion may remove code. This is the one gesture that turns
  documentation into a request (I7).
- ``queued``: which features have handed-off work not yet realized, including
  deleted headings whose removal is still pending (phantoms).
- ``directive``: the realize prompt item for one feature.
- ``write_queue``: render every queued directive into ``realize.md``, the
  prompt the realizing session or ``codoc realize`` reads. Regenerable; the
  log is the truth.
- ``settle``: record ``feature.realized`` for each queued feature once a
  binding op on any feature citing its latest hand-off in ``caused_by`` has landed, from
  any writer (under a plan, once accepted), and for each phantom whose code a citing reflect pass saw go. Every
  applier runs it after applying (the daemon, a daemonless writer), never the
  agent; the owner may also write it by hand (``codoc done``) for a request
  whose implementation moved no bound symbol.
"""
from __future__ import annotations

from pathlib import Path

from codoc import kinds as registry
from codoc.calculus import ops as K
from codoc.calculus.derive import (
    description, ink, kind_words, live_spans, prose_blocks, removed_blocks, text_string, title,
)
from codoc.calculus.doc import BUILTIN
from codoc.calculus.ids import OpId, is_human
from codoc.calculus.ops import Op, covered, in_frontier
from codoc.calculus.store import Store, Watermarks, file_of
from codoc.calculus.text import BOLD, LINK, Text
from codoc.llm.prompt import format_prompt, load_prompt
from codoc.workspace import Workspace
from codoc.workspace.layout import REALIZE, atomic_write


def queued(store: Store) -> list[str]:
    """Features whose hand-off frontier is not covered by their realized
    frontier, in document order; dead headings with a pending removal come
    last. A watermark fact: a feature being edited or built right now is
    still queued (the agent is reading its directive)."""
    pending = {fid for fid, w in store.watermarks_by_fid.items() if w.handoff and not covered(w.handoff, w.realized)}
    out = [h.id for h in store.headings() if h.id in pending]
    out.extend(fid for fid in pending if fid in store.blocks and not store.blocks[fid].live)
    return out


def render_queue(store: Store, root_dir: str = ".", doc_language=None) -> str:
    items = []
    for fid in queued(store):
        w = store.watermarks(fid)
        did = w.handoffs[-1] if w.handoffs else ""
        body = directive(store, fid)
        items.append(body.replace("### ", f"### ⟨d:{did}⟩ ", 1) if did else body)
    if not items:
        return ""
    template = load_prompt("realize", doc_language=doc_language)
    return format_prompt(template, root_dir=root_dir, directives="\n\n".join(items))


def write_queue(codoc_dir: str | Path, store: Store, root_dir: str = ".", doc_language=None) -> Path | None:
    path = Path(codoc_dir) / REALIZE
    text = render_queue(store, root_dir, doc_language)
    if not text:
        if path.exists():
            path.unlink()
        return None
    atomic_write(path, text)
    return path


def settle(ws: Workspace, res=None, *, sha: str = "") -> list[Op]:
    """Record ``feature.realized`` for each queued feature once an applied
    binding op since its latest hand-off cites that hand-off in
    ``caused_by``, whoever wrote it: an agent's ``codoc_reflect``, or a
    reflect pass that cited it. The op may bind any feature: a request built
    as a sub-feature binds the new node, and one that changed a symbol another
    feature owns (realize.txt allows it) binds that owner. An op under a plan
    counts once a verdict accepted it. A phantom has no bindings left to cite,
    so it settles on the reflect pass ``res`` that cited it, when that pass
    saw its code go."""
    out = []
    store = ws.store
    for fid in queued(store):
        w = store.watermarks_by_fid[fid]
        if not w.handoffs:
            continue
        did = w.handoffs[-1]
        cites = [o for o in store.ops[store.position.get(did, 0):]
                 if o.caused_by == did and o.kind.startswith("bind.") and o.conflict is None and _taken(store, o)]
        if not cites and not (res and res.caused.get(fid) == did and _phantom_done(store, fid, did, res.removed)):
            continue
        out.extend(ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": fid},
                               "payload": {"covers": dict(w.handoff), "sha": sha,
                                           "files": sorted({file_of(o.payload["symbol"]) for o in cites})}}],
                             actor="loop", caused_by=did))
    return out


def _taken(store: Store, o: Op) -> bool:
    """A plan's op is evidence only once a verdict accepted it: an open plan
    may still be rejected, and a rejected one, or an op a subset accept left
    out, was undone."""
    if not o.plan:
        return True
    plan = store.plans.get(o.plan)
    verdict = store.by_id.get(plan.verdict) if plan and plan.status == "accepted" else None
    return verdict is not None and o.id in (verdict.payload.get("accepted") or [])


def _phantom_done(store: Store, fid: str, did: str, removed: set[str]) -> bool:
    """A deleted feature settles on its code going away, never on a binding op:
    the delete already detached its bindings, so no ``bind.*`` op can
    cite the hand-off and the phantom would queue forever. What it owned is
    what the delete recorded as detached. A hand-off that did not ask for the
    code to go has nothing to build, so the pass that ran under the epoch
    settles it."""
    b = store.blocks.get(fid)
    ho = store.by_id.get(did)
    if b is None or b.live or ho is None:
        return False
    if not ho.payload.get("delete_code"):
        return True
    owned = {e["symbol"] for e in b.detached if not e.get("set")}     # `set`: a kind change's, not the delete's
    return bool(owned) and owned <= removed


# ── the directive ────────────────────────────────────────────────────────────

def directive(store: Store, fid: str) -> str:
    """The realize prompt item for a feature: its text in the all-rejected read
    mode (an un-vetted plan never masquerades as the person's intent), what the
    person changed since the last realization, Focus and Consult lines, and
    the person's own words from the hand-off."""
    b = store.blocks.get(fid)
    if b is None:
        return ""
    w = store.watermarks(fid)
    ho = store.by_id.get(w.handoffs[-1]) if w.handoffs else None
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
    """What this feature's kind blocks put in front of the agent: every
    in-flow block's ``consult``, then the directive each block's
    ``lower`` codec derives from what changed since the last realization. An
    out-of-flow block (a comment) is not prose the agent reads, only a
    request, which its own codec words. A kind this process has no code for
    says its Text fields' words under its name."""
    reads: list[str] = []
    asks: list[str] = []
    ctx: object | None = None
    for b in store.feature_body(fid):
        kind = registry.get(b.kind)
        if kind is None:
            if b.kind not in BUILTIN and b.layout.flow and (said := kind_words(b, store)):
                reads.append(said)
            continue
        new = registry.plain(b, store)
        if b.layout.flow and (said := kind.consult(new)):
            reads.append(said)
        if kind.lower is None:
            continue
        old = {k: (text_string(b.kind, k, _at_realized(store, v, w.realized), "rejected", store)
                   if isinstance(v, Text) and k in b.layout.text else v)
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
    identity, no diff. Only the author's, because `lower` speaks for them. A
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
    or a person ACCEPTED a plan that asks for code (``plan.build``, the rule
    ``derive.accept_handoffs`` uses). Accepting an agent's proposal is how an
    author adopts its words as a request, so an accepted edge reaches the
    directive the same way a typed one does. An accepted reflect plan
    describes code that is already there and asks for nothing."""
    o = store.by_id.get(op_id)
    if o is None:
        return False
    if is_human(o.actor):
        return True
    plan = store.plans.get(o.plan) if o.plan else None
    return plan is not None and plan.status == "accepted" and plan.build


def _human_changes(store: Store, fid: str, w: Watermarks) -> list[str]:
    """What the person did that the code has not answered, as the directive
    says it. Read from the TEXT, never from the ops: a segment carries who
    minted it and now who killed it, so a fold that takes the ops away leaves
    the request intact. That matters because the hand-off that renders it is
    usually given after the fold, and nothing at fold time could know. What a
    verdict removed answered a plan and asks nothing of the code, unless it
    accepted a plan that asks for code (``_authored``)."""
    out = []
    verdicts = {p.verdict for p in store.plans.values() if p.verdict and not (p.build and p.status == "accepted")}
    for c in ink(store, fid):
        if c.stage == "sent":
            out.append(f"added: \"{c.text}\"")
    for b in prose_blocks(store, fid):
        for f in b.layout.text:
            for s in b.text(f).segs:
                if s.dead and is_human(s.dead_actor) and s.dead not in verdicts \
                        and not in_frontier(s.dead, w.realized) and in_frontier(s.dead, w.handoff):
                    out.append(f"removed: \"{s.chars}\"")
    for b in removed_blocks(store, fid):
        if in_frontier(b.dead, w.handoff) and not in_frontier(b.dead, w.realized):
            words = " ".join(b.text(f).live_text() for f in b.layout.text).strip()
            out.append(f"removed: \"{words}\"")
    return out


def _focus(store: Store, fid: str, w: Watermarks) -> list[str]:
    """The words a person bolded and has not had realized: the directive's
    ``Focus:`` lines.

    A mark carries its own actor and placing op, so this reads the MARKS, not
    the ops that placed them: a mark since turned off is not here to
    find, and a fold that takes the ops away leaves the focus intact."""
    out: list[str] = []
    for b in prose_blocks(store, fid):
        for f in b.layout.text:
            t = b.text(f)
            for m in t.marks:
                if m.kind == BOLD and is_human(m.actor) and m.op and not in_frontier(m.op, w.realized):
                    if words := t.mark_text(m):
                        out.append(words)
    return out


def _all_links(store: Store, fid: str) -> list[tuple[str, str]]:
    out = []
    for b in store.feature_body(fid):
        for f in b.layout.text:
            t = b.text(f)
            lt = t.live_text()
            for sp in live_spans(t):
                if sp.kind == LINK:
                    out.append((lt[sp.start:sp.end], sp.attrs.get("href", "")))
    return out
