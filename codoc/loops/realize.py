"""Document → code: the realize queue and the realization watermark.

A person's edits are already ops on the trunk, and their hand-off
(``feature.handoff``) is written by the surface they used: the editor,
``codoc handoff``, an accept. Nothing here infers either.

- ``directive``: the realize prompt item for one feature.
- ``write_queue``: render every queued directive into ``realize.md``, the
  prompt the realizing session or ``codoc realize`` reads. Regenerable; the
  log is the truth.
- ``settle``: record ``feature.realized`` for each queued feature once a
  binding op on any feature citing its latest hand-off in ``caused_by`` has
  landed, from any writer (under a plan, once accepted), and for each phantom
  whose code a citing reflect pass saw go. Every applier runs it after
  applying (the daemon, a daemonless writer), never the agent, through
  ``settle_if_idle``, which waits while an agent epoch is open. ``codoc done``
  writes it by hand for a request whose implementation moved no bound symbol.
"""
from __future__ import annotations

from pathlib import Path

from codoc import kinds as registry
from codoc.calculus import ops as K
from codoc.calculus.derive import (
    description, ink, kind_words, live_spans, prose_blocks, queued, realized_content, removals, title, unvetted_block,
)
from codoc.calculus.doc import BUILTIN
from codoc.calculus.ids import LOOP, is_human
from codoc.calculus.ops import Op, in_frontier
from codoc.calculus.store import Store, Watermarks, file_of
from codoc.calculus.text import BOLD, LINK
from codoc.llm.prompt import format_prompt, load_prompt
from codoc.workspace import Workspace
from codoc.workspace.epoch import epoch_alive
from codoc.workspace.layout import REALIZE, atomic_write


def render_queue(store: Store, root_dir: str = ".", doc_language=None) -> str:
    items = []
    for fid in queued(store):
        w = store.watermarks(fid)
        latest_handoff = w.handoffs[-1] if w.handoffs else ""
        body = directive(store, fid)
        items.append(body.replace("### ", f"### ⟨d:{latest_handoff}⟩ ", 1) if latest_handoff else body)
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
    ``caused_by``, whoever wrote it (an agent's ``codoc_reflect``, or a
    reflect pass that cited it), on a symbol a build touched since the
    hand-off (``ops.built_code``: its code moved or it was newly bound, by
    that op or an earlier one, since a reflect pass may take the change
    before the agent reports it). A citing op on code nothing built records a
    review, not the build. The op may bind
    any feature: a request built
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
        latest_handoff = w.handoffs[-1]
        since = [o for o in store.ops[store.position.get(latest_handoff, 0):]
                 if o.conflict is None and o.kind.startswith("bind.") and _taken(store, o)]
        moved = {o.payload.get("symbol") for o in since if K.built_code(o.kind, o.payload)}
        cites = [o for o in since if o.caused_by == latest_handoff and o.payload.get("symbol") in moved]
        if not cites and not (res and res.caused.get(fid) == latest_handoff
                              and _phantom_done(store, fid, latest_handoff, res.removed)):
            continue
        out.extend(ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": fid},
                               "payload": {"covers": dict(w.handoff), "sha": sha,
                                           "files": sorted({file_of(o.payload["symbol"]) for o in cites})}}],
                             actor=LOOP, caused_by=latest_handoff))
    return out


def settle_if_idle(ws: Workspace, res=None, *, sha: str, now: float | None = None) -> list[Op]:
    """``settle``, unless an agent epoch is open: its build is in progress, so
    the feature stays queued and the agent's amends land as plans. The batch,
    or the process, that sees the epoch close runs the same check."""
    if epoch_alive(ws.codoc_dir, now=now):
        return []
    return settle(ws, res, sha=sha)


def _taken(store: Store, o: Op) -> bool:
    """A plan's op is evidence only once a verdict accepted it: an open plan
    may still be rejected, and a rejected one, or an op a subset accept left
    out, was undone."""
    if not o.plan:
        return True
    plan = store.plans.get(o.plan)
    verdict = store.by_id.get(plan.verdict) if plan and plan.status == "accepted" else None
    return verdict is not None and o.id in (verdict.payload.get("accepted") or [])


def _phantom_done(store: Store, fid: str, latest_handoff: str, removed: set[str]) -> bool:
    """A deleted feature settles on its code going away, never on a binding op:
    the delete already detached its bindings, so no ``bind.*`` op can
    cite the hand-off and the phantom would queue forever. What it owned is
    what the delete recorded as detached. A hand-off that did not ask for the
    code to go has nothing to build, so the pass that ran under the epoch
    settles it."""
    b = store.blocks.get(fid)
    ho = store.by_id.get(latest_handoff)
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
    changes = _human_changes(store, fid)
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
    says its Text fields' words under its name. A block an open plan wrote
    says nothing (``unvetted_block``)."""
    reads: list[str] = []
    asks: list[str] = []
    ctx: object | None = None
    for b in store.body(fid):
        if unvetted_block(store, b):
            continue
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
        old = realized_content(store, b, w.realized)
        if ctx is None:
            ctx = registry.LowerContext(fid, [x for x in store.bindings.values() if x.feature == fid])
        if ask := kind.lower(old, new, ctx):
            asks.append(ask)
    return reads, asks


def _human_changes(store: Store, fid: str) -> list[str]:
    """What the person handed off that the code has not answered: their sent
    ink, then their sent removals (``derive.removals``). Read from the text,
    so a fold that takes the ops away leaves the request intact."""
    return [f"added: \"{c.text}\"" for c in ink(store, fid) if c.stage == "sent"] \
        + [f"removed: \"{c.text}\"" for c in removals(store, fid) if c.stage == "sent"]


def _focus(store: Store, fid: str, w: Watermarks) -> list[str]:
    """The words a person bolded and has not had realized: the directive's
    ``Focus:`` lines.

    A mark carries its own actor and placing op, so this reads the marks, not
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
    for b in store.body(fid):
        for f in b.layout.text:
            t = b.text(f)
            lt = t.live_text()
            for sp in live_spans(t):
                if sp.kind == LINK:
                    out.append((lt[sp.start:sp.end], sp.attrs.get("href", "")))
    return out
