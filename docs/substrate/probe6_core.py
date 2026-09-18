"""Review-6 probes for the Python core. Every probe asserts the CORRECT
behaviour (spec 01–04), so a failing probe is a confirmed fault.

Run from the worktree:
  PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <this file> -q -p no:cacheprovider
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import block_entry, directive, live_spans, markdown
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.inline_md import parse
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, CITE, LINK, SUGGEST_DEL
from codoc.calculus.window import history_window
from codoc.substrate import log as L
from codoc.substrate.snapshot import write_snapshot
from codoc.substrate.workspace import Workspace
from tests.calculus.test_apply import Mint, heading, paragraph
from tests.substrate.test_review5 import H, T, bold_chars, ins, mark, refs, seed, write_roles


def marks_of_kind(text, kind):
    return sorted(((m.start, m.end, m.op) for m in text.marks if m.kind == kind))


def fold(cd: Path) -> None:
    ws = Workspace(cd)
    write_snapshot(cd, ws.store, ws.clock)
    ws.close()


# ═════════════════════════════════════════════════════════════════════════════
# 1. Plan marks: un-apply by placer misses what set_mark records under
#    another placer (merge keeps the neighbour's op; displacement pieces keep
#    the displaced mark's op)
# ═════════════════════════════════════════════════════════════════════════════

def test_P1a_a_plan_bold_that_widens_a_human_bold_is_undone_by_reject(tmp_path):
    """01 §3 plan.reject: 'Marks are removed by the op that placed them'.
    Text.set_mark(on) merging into an adjacent/overlapping same-kind mark keeps
    THAT mark's placer, so the plan's op never becomes the placer of the mark it
    widened. Reject must still take the widening back."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])                                  # the person bolds "Retries"
    before = bold_chars(ws.store.blocks["p1"].text())
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[11], actor="loop", plan=a)            # the plan widens it over "Retries once"
    assert bold_chars(ws.store.blocks["p1"].text()) > before           # the plan's toggle applied live
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None
    t = ws.store.blocks["p1"].text()
    assert bold_chars(t) == before, marks_of_kind(t, BOLD)
    assert markdown(t) == "**Retries** once."


def test_P1b_rejected_read_mode_hides_a_plan_bold_that_widened_a_human_bold(tmp_path):
    """01 §6 read modes: `rejected` is the document without any open plan."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[11], actor="loop", plan=a)
    t = ws.store.blocks["p1"].text()
    assert markdown(t, "rejected", ws.store) == "**Retries** once.", markdown(t, "rejected", ws.store)
    e = block_entry(ws.store, ws.store.blocks["p1"])
    spans = [(s["start"], s["end"]) for s in e["fields"]["text"]["spans"] if s["kind"] == BOLD]
    assert spans == [(0, 7)], spans


def test_P1c_two_open_plans_with_adjacent_bolds_each_reject_cleanly(tmp_path):
    """Plan B's bold merged into plan A's adjacent bold; the merged mark keeps
    A's op. Rejecting B alone must remove B's part and leave A's."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], actor="loop", plan=a)             # "Retries"
    only_a = bold_chars(ws.store.blocks["p1"].text())
    b = ws.open_plan(actor="agent:x", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[7], r[11], actor="agent:x", plan=b)         # " once", adjacent to "Retries" so set_mark merges
    merged = bold_chars(ws.store.blocks["p1"].text())
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": b}, "payload": {}}], actor=H)
    t = ws.store.blocks["p1"].text()
    assert bold_chars(t) == only_a, (marks_of_kind(t, BOLD), merged)


def test_P1d_a_plan_cite_that_displaced_a_human_link_is_undone_whole_by_reject(tmp_path):
    """EXCLUSIVE displacement records the displaced link in `removed` and its
    outer pieces (with the LINK's placer) in `added`. Reject must remove the
    pieces AND the cite and put the one link back; otherwise the text holds
    three overlapping links and the Markdown no longer round-trips."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", LINK, r[0], r[11], attrs={"href": "https://x.test/"})     # [Retries once](…)
    before = [m.to_json() for m in ws.store.blocks["p1"].text().marks]
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", CITE, r[3], r[5], actor="loop", plan=a, attrs={"file": "c.py", "symbol": "send"})
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None
    t = ws.store.blocks["p1"].text()
    assert [m.to_json() for m in t.marks] == before, [m.to_json() for m in t.marks]
    md = markdown(t)
    assert parse(md) == (t.live_text(), live_spans(t)) or md == "[Retries once](https://x.test/).", md


def test_P1e_rejected_read_mode_while_the_displacing_plan_is_open(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", LINK, r[0], r[11], attrs={"href": "https://x.test/"})
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", CITE, r[3], r[5], actor="loop", plan=a, attrs={"file": "c.py", "symbol": "send"})
    t = ws.store.blocks["p1"].text()
    assert markdown(t, "rejected", ws.store) == "[Retries once](https://x.test/).", markdown(t, "rejected", ws.store)


def test_P1f_a_plan_that_unbolds_the_middle_of_a_bold_is_undone_by_reject(tmp_path):
    """A partial `off` records the bold in `removed` and its two outer pieces
    (same placer) in `added`. Reject must remove the pieces and restore the
    whole; otherwise the text holds a whole bold plus two overlapping pieces."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[11])                                 # **Retries once**
    before = marks_of_kind(ws.store.blocks["p1"].text(), BOLD)
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[3], r[5], on=False, actor="loop", plan=a)  # propose unbolding "rie"
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None
    t = ws.store.blocks["p1"].text()
    assert marks_of_kind(t, BOLD) == before, marks_of_kind(t, BOLD)
    assert markdown(t) == "**Retries once**."
    # and the export while it was open said the same
    ws2 = Workspace(tmp_path / "b" / ".codoc"); seed(ws2)
    r = refs(ws2, "p1"); mark(ws2, "p1", BOLD, r[0], r[11])
    a = ws2.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws2, "p1", BOLD, r[3], r[5], on=False, actor="loop", plan=a)
    t2 = ws2.store.blocks["p1"].text()
    assert markdown(t2, "rejected", ws2.store) == "**Retries once**.", markdown(t2, "rejected", ws2.store)


# ═════════════════════════════════════════════════════════════════════════════
# 2. Snapshots: state survives the fold, the views that read the log do not
# ═════════════════════════════════════════════════════════════════════════════

def test_P2a_a_phantom_with_delete_code_can_still_settle_after_a_fold(tmp_path):
    """02 §2: a bound feature's removal is held until a hand-off with
    delete_code, then the agent removes the code and the pass settles it. The
    settle reads the delete's `detached` from the log; a fold between the
    hand-off and the build discards that record."""
    from codoc.graph.graph import Graph
    from codoc.loop.diff import ChangeSet, ChunkRef
    from codoc.loops import realize as R
    from codoc.loops.reflect import reflect
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"delete_code": True}}], actor=H)
    ws.close()
    fold(cd)
    ws = Workspace(cd)
    assert R.queued(ws.store) == ["h1"]
    cs = ChangeSet(removed=[ChunkRef("c.py", "c.py::send", "f1", "t1")])
    res = reflect(ws, cs, Graph(), propose=lambda *a, **k: [], under_epoch=True)
    R.settle(ws, res, sha="abc", files=["c.py"])
    assert R.queued(ws.store) == [], "the phantom's code went and it is still queued"
    ws.close()


def test_P2b_the_directive_of_a_queued_feature_is_the_same_across_a_fold(tmp_path):
    """01 §7.5 folds 'the frontiers (with the words the person asked for)'; a
    queued feature's directive (Focus from the person's bold, `removed:` from
    the person's deletion) is a function of the same state and must not lose
    lines because the ops that carried them were archived."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])                                              # Focus: "Retries"
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"},
                "payload": {"from": list(r[8]), "to": list(r[11])}}], actor=H)     # removed: "once"
    ins(ws, "p1", list(r[12]), " Twice.", actor=H)                                # added: " Twice."
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "please"}}], actor=H)
    d1 = directive(ws.store, "h1")
    assert 'Focus: "Retries"' in d1 and 'removed: "once"' in d1 and "Author asked: please" in d1, d1
    ws.close()
    fold(cd)
    ws = Workspace(cd, readonly=True)
    d2 = directive(ws.store, "h1")
    assert d2 == d1, f"\nBEFORE:\n{d1}\n\nAFTER:\n{d2}"


def test_P2c_emptied_is_the_same_state_before_and_after_a_fold(tmp_path):
    """reflect step 2 is 'state-based: any live feature that once had code and
    has none now'. Whether it once had code is read from the log; a fold keeps
    only the bindings it has now."""
    from codoc.loops.reflect import _emptied
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BIND_DETACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send"}}], actor="loop")
    assert _emptied(ws.store) == ["h1"]
    ws.close()
    fold(cd)
    ws = Workspace(cd, readonly=True)
    assert _emptied(ws.store) == ["h1"], _emptied(ws.store)


# ═════════════════════════════════════════════════════════════════════════════
# 3. The suggester's plan (02 §3.6) and the agent's gate (tools.reflect)
# ═════════════════════════════════════════════════════════════════════════════

def _sub(cid, kind, target, payload, batch="b1", **kw):
    return L.Submission(cid=cid, kind=kind, target=target, payload=payload, batch=batch, **kw)


def test_P3a_a_suggesters_backspace_becomes_a_suggest_del_under_their_plan(tmp_path):
    """02 §3.6: 'a contributor's edit on the trunk is applied as a plan with
    that actor'. A plan proposes and never kills, so their text.delete is the
    plan's suggest_del over the range — as_plan_bodies already does this for an
    ingest. Today the delete is refused `authority` and the editor reloads the
    paragraph on every Backspace."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [_sub("d1", K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": list(r[0]), "to": list(r[6])})])
    ops = ws.drain_inbox()
    assert [o.conflict for o in ops if o.kind != K.PLAN_OPEN] == [None], [(o.kind, o.conflict) for o in ops]
    assert T(ws.store, "p1") == "Retries once."                       # proposed, not destroyed
    t = ws.store.blocks["p1"].text()
    sd = [m for m in t.marks if m.kind == SUGGEST_DEL]
    assert sd and ws.store.plans[sd[0].attrs["plan"]].actor == "human:gh:alice", [m.to_json() for m in t.marks]
    ws.close()


def test_P3b_a_suggesters_no_op_ingest_leaves_no_empty_open_plan(tmp_path):
    """The drain opens the suggester's plan before it knows whether anything
    will land under it (review 5 A9 for translate; same shape here)."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    L.submit(cd, "hub-alice", [_sub("i1", "ingest", {"bid": "p1", "path": "text"},
                                    {"base_text": "Retries once.", "new_md": "Retries once."})])
    ws.drain_inbox()
    empty = [p for p in ws.store.plans.values() if p.status == "open" and not p.ops]
    assert not empty, [(p.id, p.actor, p.ops) for p in empty]
    # and a batch whose only op conflicts (unknown anchor)
    L.submit(cd, "hub-alice", [_sub("x1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": ["s-nope:1", 0], "text": "x"}, batch="b2")])
    ws.drain_inbox()
    empty = [p for p in ws.store.plans.values() if p.status == "open" and not p.ops]
    assert not empty, [(p.id, p.actor, p.ops) for p in empty]
    ws.close()


def test_P3c_an_agents_amend_that_rewrites_a_persons_paragraph_is_a_plan_not_a_trunk_write(tmp_path):
    """mcp/tools.py: 'block-keyed prose repairs (through ingest under the
    machine gate). Safe ops land; anything that would rewrite a person's words
    becomes a plan.' server.py codoc_reflect: 'small repairs land, rewrites and
    repairs on a feature with pending intent become a plan'. 01 §4 rule 8: a
    queued feature never receives a trunk write from a machine (doc wins)."""
    from codoc.mcp import tools
    from codoc.substrate import client
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "x"}}], actor=H)   # queued
    ws.close()
    try:
        out = tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1",
                                           "blocks": [{"id": "p1", "text": "Completely different words, nothing kept."}]}])
        assert out["ok"], out
        st = client.read(cd).store
        trunk = [s for s in st.blocks["p1"].text().segs if s.live and s.actor.startswith("agent:") and not s.plan]
        assert not trunk, ("an agent replaced the person's paragraph on the trunk of a QUEUED feature: " +
                           repr(st.blocks["p1"].text().live_text()))
        assert T(st, "p1") == "Retries once." or any(p.status == "open" for p in st.plans.values())
    finally:
        client.release(cd)


def test_P3d_an_agents_amend_that_drops_a_persons_bold_is_refused_or_planned(tmp_path):
    """01 §4 rule 5 protects a person's bold/link/cite from a trunk rewrite by
    the loop or a translator; ingest.REWRITERS leaves the agent out, so an
    agent's `codoc_reflect amend` drops it silently."""
    from codoc.mcp import tools
    from codoc.substrate import client
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1"); mark(ws, "p1", BOLD, r[0], r[6])
    ws.close()
    try:
        tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries twice."}]}])
        st = client.read(cd).store
        t = st.blocks["p1"].text()
        assert bold_chars(t) or any(p.status == "open" for p in st.plans.values()), markdown(t)
    finally:
        client.release(cd)


# ═════════════════════════════════════════════════════════════════════════════
# 4. Applier edges
# ═════════════════════════════════════════════════════════════════════════════

def test_P4a_reopen_never_binds_code_to_a_dead_heading(tmp_path):
    """01 §3 block.delete: 'a dead heading owns no code'. plan.reopen restores
    the bindings a reject removed without asking whether the heading is still
    alive."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    a = ws.open_plan(actor="loop", kind="edit", target="h1")
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "d.py", "symbol": "d.py::x"}}], actor="loop", plan=a)
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)[0]
    assert ("d.py", "d.py::x") not in ws.store.bindings
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor=H)
    assert not ws.store.blocks["h1"].live and not [b for b in ws.store.bindings.values() if b.feature == "h1"]
    re = ws.submit([dict(b) for b in rej.inverse], actor=H)[0]        # the person undoes the reject
    owners = [b for b in ws.store.bindings.values() if b.feature == "h1"]
    assert not owners or ws.store.blocks["h1"].live, (re.conflict, [b.to_json() for b in owners])


def test_P4b_a_reopen_naming_an_unknown_block_in_marks_back_is_a_conflict_not_a_crash():
    """01 §5: apply is total. `_h_plan_reopen` checks every block it will touch
    except the ones under `marks_back`; a KeyError there escapes apply (and a
    rebuild that meets it in a merged log dies)."""
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client")
    paragraph(m, s, "p1", "Retries once.", after="h1")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit", "target": "p1"}))
    rej = apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    assert rej.conflict is None
    bad = m.op(K.PLAN_REOPEN, {"pid": "P"}, {"from": "rejected", "by": rej.id,
                                             "marks_back": [{"bid": "no-such-block", "path": "text",
                                                             "mark": {"kind": BOLD, "attrs": {}, "start": ["s:1", 0], "end": ["s:1", 0]}}]})
    try:
        out = apply(s, bad)
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"apply raised {e!r} instead of recording a conflict")
    assert out.conflict is not None
    assert s.plans["P"].status == "rejected"


def test_P4c_the_timeline_reports_the_order_a_verdict_restored(tmp_path):
    """window.py: 'ranks and each entry's ranks: the order the page had'. A
    plan.reject that undoes a plan's move re-ranks blocks; the window records
    displaced ranks for block.move only, so the page just before the reject
    renders in the order the reject produced."""
    m, s = Mint(), Store()
    heading(m, s, "h1", "A"); paragraph(m, s, "p1", "a", after="h1")
    heading(m, s, "h2", "B", after="p1"); paragraph(m, s, "p2", "b", after="h2")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "move", "target": "h2"}))
    mv = apply(s, m.op(K.BLOCK_MOVE, {"bid": "h2"}, {"after": START, "level_delta": 0}, plan="P"))
    assert mv.conflict is None and [b.id for b in s.ordered()] == ["h2", "p2", "h1", "p1"]
    rej = apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    assert rej.conflict is None and [b.id for b in s.ordered()] == ["h1", "p1", "h2", "p2"]
    w = history_window(s, limit=1)                                    # the window holds only the reject
    assert "h2" in w["ranks"] and w["ranks"]["h2"] < w["ranks"]["h1"], ("before the reject h2 was first; window says", w["ranks"])


# ═════════════════════════════════════════════════════════════════════════════
# 5. The lazy session file, ack rotation, and the codecs' `old`
# ═════════════════════════════════════════════════════════════════════════════

def test_P5a_a_workspace_that_wrote_nothing_is_still_a_workspace(tmp_path):
    """`codoc init` on a repository with no indexable files opens a Workspace,
    writes no op, publishes and exits 0. Every later command then says 'No
    codoc workspace — run codoc init first' because `_require` looks for
    `log/`, which the lazy session file no longer creates."""
    import typer
    from codoc.cli.main import _require, _write_gitignore
    root = tmp_path
    cd = root / ".codoc"
    cd.mkdir(); _write_gitignore(cd)
    ws = Workspace(cd); ws.close()
    try:
        _require(str(root))
    except typer.Exit:
        pytest.fail("a freshly initialised (empty) workspace is not recognised as one")


def test_P5b_a_resubmitted_client_id_is_not_applied_twice_after_ack_rotation(tmp_path):
    """01 §7.4: 'A resubmitted client id is acked with the same OpId and not
    applied twice.' Ack rotation forgets cids older than ACK_KEEP; a client
    that resubmits one of those after a daemon restart lands it again."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "ide", [_sub("c1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " X"}),
                         _sub("c2", K.TEXT_INSERT, {"bid": "h1", "path": "text"}, {"after": START, "text": "The "})])
    ws.drain_inbox(); ws.close()
    L.rotate_acks(cd, keep=1)                                         # the bound, made small
    L.submit(cd, "ide", [_sub("c1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " X"})])
    ws = Workspace(cd); ws.drain_inbox()
    assert T(ws.store, "p1") == "Retries once. X", T(ws.store, "p1")
    ws.close()


def test_P5c_a_loop_lifted_diagram_on_a_handed_off_feature_is_not_reported_as_the_authors_drawing(tmp_path):
    """03 §5.1: `lower` speaks for 'the change since the last realization' with
    `old` reconstructed by identity. A diagram the LOOP lifted from the code
    after the frontier is then all 'new', and the directive tells the agent the
    author drew every edge the code already has."""
    from codoc.graph.graph import Graph
    from codoc.kinds.lift import lift_pass
    from codoc.kinds.write import add_block
    ws = Workspace(tmp_path / ".codoc"); seed(ws, actor="loop")
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::retry"}}], actor="loop")
    g = Graph([{"src_symbol": "c.py::send", "dst_name": "retry", "dst_symbol": "c.py::retry", "kind": "call", "src_file": "c.py", "internal": True}])
    d = add_block(ws, "diagram", {"source": ""}, after="p1", actor="loop")
    assert d.ok and lift_pass(ws, g) == 1
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "make it retry twice"}}], actor=H)
    out = directive(ws.store, "h1")
    assert "Diagram of this feature's code" in out, out
    assert "the author drew that edge" not in out, out
    ws.close()


# ═════════════════════════════════════════════════════════════════════════════
# 6. Confirmations (expected to PASS): what review 5 changed and what holds
# ═════════════════════════════════════════════════════════════════════════════

def test_P6a_a_realized_feature_survives_a_fold_with_no_problems(tmp_path):
    """`nothing_queued` must not refuse the snapshot's own feature.realized,
    which is emitted after the snapshot's feature.handoff."""
    from codoc.loops import realize as R
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "x"}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {"sha": "s"}}], actor="loop")
    assert R.queued(ws.store) == []
    fp = ws.store.state_fingerprint(); ws.close()
    fold(cd)
    ws = Workspace(cd, readonly=True)
    assert not ws.problems and ws.store.state_fingerprint() == fp and R.queued(ws.store) == []
    assert not [o for o in ws.store.ops if o.conflict], [(o.kind, o.conflict) for o in ws.store.ops if o.conflict]


def test_P6b_a_suggesters_insert_rides_in_their_plan_and_the_rebuild_agrees(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [_sub("i1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " More."})])
    ops = ws.drain_inbox()
    ins_op = [o for o in ops if o.kind == K.TEXT_INSERT][0]
    assert ins_op.conflict is None and ins_op.plan and ws.store.plans[ins_op.plan].actor == "human:gh:alice"
    assert markdown(ws.store.blocks["p1"].text(), "rejected", ws.store) == "Retries once."
    fp = ws.store.state_fingerprint(); ws.close()
    assert Workspace(cd, readonly=True).store.state_fingerprint() == fp


def test_P6c_two_clones_inserting_after_the_same_character_both_land(tmp_path):
    import shutil
    base = tmp_path / "base" / ".codoc"
    ws = Workspace(base); seed(ws); ws.close()
    a = tmp_path / "a" / ".codoc"; a.parent.mkdir(); shutil.copytree(base, a)
    b = tmp_path / "b" / ".codoc"; b.parent.mkdir(); shutil.copytree(base, b)
    wa, wb = Workspace(a), Workspace(b)
    ra, rb = refs(wa, "p1"), refs(wb, "p1")
    ins(wa, "p1", list(ra[-1]), " A."); ins(wb, "p1", list(rb[-1]), " B.")
    wa.close(); wb.close()
    for f in L.list_sessions(b):
        if not (a / "log" / f.name).exists():
            shutil.copy(f, a / "log" / f.name)
    m = Workspace(a, readonly=True)
    assert T(m.store, "p1") in ("Retries once. A. B.", "Retries once. B. A.")
    assert not [o for o in m.store.ops if o.conflict]


def test_P6d_a_client_op_missing_a_key_is_a_recorded_conflict():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client")
    paragraph(m, s, "p1", "Retries once.", after="h1")
    o = apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"text": "x"}))
    assert o.conflict == "missing:after"
    o = apply(s, m.op(K.BLOCK_ADD, {"bid": "p2"}, {"after": "p1"}))
    assert o.conflict == "missing:kind"


def test_P6e_doc_wins_a_person_typing_during_a_build_reads_edited(tmp_path):
    from codoc.calculus.derive import phase
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    assert phase(ws.store, "h1", realizing={"h1"}) == "realizing"
    ins(ws, "p1", list(refs(ws, "p1")[-1]), " More.")
    assert phase(ws.store, "h1", realizing={"h1"}) == "edited"


def test_P6f_the_window_reads_verdict_kills_by_identity_after_a_split(tmp_path):
    """Review 5 A5 fix: a segment op whose characters a split moved is
    un-applied in the clone that holds them."""
    from tests.calculus.test_window import texts_after_each_op
    m, s = Mint(), Store()
    heading(m, s, "h1", "T")
    i = paragraph(m, s, "p1", "abcdef", after="h1")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit", "target": "p1"}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": SUGGEST_DEL, "attrs": {}, "start": [i.id, 3], "end": [i.id, 5], "on": True}, plan="P"))
    sp = apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"new": "p2", "at": [i.id, 1]}))
    assert sp.conflict is None
    acc = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {}))
    assert acc.conflict is None and s.blocks["p2"].text().live_text() == "c"
    truth = texts_after_each_op(s)
    for e, snap in zip(history_window(s, limit=100)["ops"], truth):
        for bid, text in e["blocks"].items():
            assert text == snap[bid], (e["kind"], bid, text, snap[bid])


# ═════════════════════════════════════════════════════════════════════════════
# 7. ink() and the reading flow
# ═════════════════════════════════════════════════════════════════════════════

def test_P7a_a_margin_comment_is_not_the_features_unsent_ink(tmp_path):
    """01 §6 ink(f): 'Only blocks in the reading flow count — a margin comment
    is a request about the prose, not the prose (written_by and the directive
    agree)'. `ink()` walks feature_body without the in_flow filter, so a
    person's comment on a loop-written, synced feature turns its phase to
    `edited` and paints blue ink on the card."""
    from codoc.calculus.derive import ink, phase
    from codoc.kinds.write import add_block
    ws = Workspace(tmp_path / ".codoc"); seed(ws, actor="loop")
    assert phase(ws.store, "h1") == "synced"
    c = add_block(ws, "comment", {"thread": "t1", "anchor": "p1", "anchor_text": "Retries", "body": "why once?",
                                  "scope": "code"}, after="p1", actor=H)
    assert c.ok, c.refused
    claims = ink(ws.store, "h1")
    assert not [k for k in claims if k.bid == c.bid], [k.__dict__ for k in claims]
    assert phase(ws.store, "h1") != "edited", phase(ws.store, "h1")
    ws.close()
