"""Review-7 probes for the Python core. Every probe asserts the CORRECT
behaviour (specs 01–04), so a failing probe is a confirmed fault. Section 8
holds confirmations expected to pass.

Run from the worktree:
  PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <this file> -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import block_entry, directive, ground, ink, markdown, phase
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, SUGGEST_DEL
from codoc.calculus.window import history_window
from codoc.substrate import log as L
from codoc.substrate.snapshot import write_snapshot
from codoc.substrate.workspace import Roles, Workspace
from tests.calculus.test_apply import Mint, heading, paragraph
from tests.substrate.test_review5 import H, T, _sub, bold_chars, fold, ins, mark, refs, seed


def bolds(text):
    return sorted(((m.start, m.end, m.op) for m in text.marks if m.kind == BOLD))


def open_plans_of(store, actor=None):
    return [p for p in store.plans.values() if p.status == "open" and (actor is None or p.actor == actor)]


# ═════════════════════════════════════════════════════════════════════════════
# 1. Plan marks matched by (kind, attrs, start, placer): what a split, and a
#    second plan on the same span, do to that key
# ═════════════════════════════════════════════════════════════════════════════

def test_P1a_a_plan_bold_that_widened_a_human_bold_is_undone_after_the_person_split_the_paragraph(tmp_path):
    """01 §3: reject removes 'what the op RECORDED putting there — its own
    `added` entries, matched on kind, attrs, start and placer'. The recorded
    mark is (bold, r0..r11, placer=the person). An Enter inside the widened
    part cuts it into two pieces; the tail piece starts at the cut, not at r0,
    and carries the person's placer, so no key finds it. The extension the
    plan proposed stays bold after the plan is rejected."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])                                   # the person: **Retries**
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[11], actor="loop", plan=a)             # the plan: **Retries once**
    sp = ws.submit([{"kind": K.BLOCK_SPLIT, "target": {"bid": "p1", "path": "text"},
                     "payload": {"new": "p1b", "at": list(r[8])}}], actor=H)[0]   # Enter after "Retries o"
    assert sp.conflict is None and T(ws.store, "p1b") == "nce."
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None
    head, tail = ws.store.blocks["p1"].text(), ws.store.blocks["p1b"].text()
    assert markdown(tail) == "nce.", (markdown(tail), bolds(tail))
    assert bold_chars(head) == set(r[0:7]), (markdown(head), bolds(head))


def test_P1b_the_rejected_read_mode_agrees_after_the_split_while_the_plan_is_open(tmp_path):
    """01 §6: `rejected` is the document without any open plan; the export is
    rendered from it. The tail block's bold has the person's placer and a
    start no `added` entry names."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[11], actor="loop", plan=a)
    ws.submit([{"kind": K.BLOCK_SPLIT, "target": {"bid": "p1", "path": "text"},
                "payload": {"new": "p1b", "at": list(r[8])}}], actor=H)
    tail = ws.store.blocks["p1b"].text()
    assert markdown(tail, "rejected", ws.store) == "nce.", markdown(tail, "rejected", ws.store)
    e = block_entry(ws.store, ws.store.blocks["p1b"])
    assert [s for s in e["fields"]["text"]["spans"] if s["kind"] == BOLD] == [], e["fields"]["text"]["spans"]


def test_P1c_rejecting_one_of_two_plans_that_bold_the_same_words_keeps_the_other_proposal(tmp_path):
    """Two plans propose the same bold. `set_mark` merges the second into the
    first and keeps the first's placer, so the one live mark is recorded under
    plan A's op by BOTH plans. Rejecting A removes it whole; B is still open,
    still offered, and now proposes nothing — accepting it bolds nothing."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], actor="loop", plan=a)
    b = ws.open_plan(actor="agent:x", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], actor="agent:x", plan=b)
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)
    assert ws.store.plans[b].status == "open"
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": b}, "payload": {}}], actor=H)[0]
    assert acc.conflict is None
    t = ws.store.blocks["p1"].text()
    assert bold_chars(t) == set(r[0:7]), ("plan B was accepted and its bold is gone:", markdown(t))


# ═════════════════════════════════════════════════════════════════════════════
# 2. Snapshots: the ops are remembered, their ORDER is not
# ═════════════════════════════════════════════════════════════════════════════

def test_P2a_the_greens_a_person_has_not_caught_up_on_survive_a_fold(tmp_path):
    """01 §6 `ground(f)`: 'what the loop did since the person last engaged'.
    A realized feature the loop then repaired is `synced`, so `_unrealized`
    archives none of its ops; after the fold `ground` finds no loop op newer
    than the person's last touch and the green the person had not yet read is
    gone. 02 §2 gives the person 'Catch up / Keep' to clear it; the fold does
    it for them."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "x"}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {"sha": "s"}}], actor="loop")
    res, ops = ws.ingest("p1", "text", base_text="Retries once.", new_md="Retries once, then stops.", actor="loop")
    assert ops and all(o.plan is None for o in ops), (res.refused, [o.plan for o in ops])
    before = [(c.stage, c.text) for c in ground(ws.store, "h1")]
    assert before == [("added", ", then stops")], before
    assert phase(ws.store, "h1") == "synced"
    ws.close()
    fold(cd)
    after = [(c.stage, c.text) for c in ground(Workspace(cd, readonly=True).store, "h1")]
    assert after == before, f"\nBEFORE {before}\nAFTER  {after}"


def test_P2b_ground_on_an_unsettled_feature_is_the_same_after_a_fold(tmp_path):
    """01 §7.5 (rev after review 6): 'the fold also archives the ops of every
    feature that is not settled: ... `ground`'s reds and greens ... are read
    from the ops, and a fold that dropped them would answer differently'.
    Here the feature is `drifted` (a refresh after the repair), so its ops ARE
    archived — and `ground` still paints nothing, because it measures 'since'
    by apply POSITION and `Store.remember` puts every archived op at −1."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "x"}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {"sha": "s"}}], actor="loop")
    res, ops = ws.ingest("p1", "text", base_text="Retries once.", new_md="Retries once, then stops.", actor="loop")
    assert ops and all(o.plan is None for o in ops)
    ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": "f2"}}], actor="loop")
    before = [(c.stage, c.text) for c in ground(ws.store, "h1")]
    assert before == [("added", ", then stops")] and phase(ws.store, "h1") == "drifted"
    ws.close()
    fold(cd)
    st = Workspace(cd, readonly=True).store
    assert any(o.kind == K.TEXT_INSERT and o.actor == "loop" for o in st.ops_on({"p1"})), "the repair IS archived"
    after = [(c.stage, c.text) for c in ground(st, "h1")]
    assert after == before, f"\nBEFORE {before}\nAFTER  {after}"


def test_P2c_a_second_fold_keeps_the_archived_ops_in_apply_order(tmp_path):
    """`snapshot_ops` sorts the archive by `store.position`, and a remembered
    op's position is −1, so on the second fold every op archived by the first
    sorts to the front in the order the open-plan loop and `ops_on` happened to
    visit them. `_drifted` decides by that order."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": HEADING, "level": 1, "after": START}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "Client"}}], actor="loop")
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": "f1"}}], actor="loop")
    ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": "f2"}}], actor="loop")
    a = ws.open_plan(actor="loop", kind="edit", target="h1")
    ins(ws, "h1", list(refs(ws, "h1")[-1]), " v2", actor="loop", plan=a)      # prose AFTER the refresh
    truth_order = [o.id for o in ws.store.ops]
    ws.close()
    twin = tmp_path / "twin" / ".codoc"; twin.parent.mkdir(); shutil.copytree(cd, twin)
    fold(cd); fold(cd)
    snap = L.read_session(L.list_snapshots(cd)[-1])
    archived = [o.id for o in snap.archived]
    foreign = [i for i in archived if i not in truth_order]
    assert not foreign, f"the second fold archived the FIRST fold's own ops (they accumulate per fold): {foreign}"
    pos = {i: n for n, i in enumerate(truth_order)}
    assert archived == sorted(archived, key=pos.__getitem__), f"archived out of apply order: {[pos[i] for i in archived]}"
    # and the phase it decides: with the plan rejected, the newest op on h1 is prose, not a binding change
    for d in (cd, twin):
        w = Workspace(d)
        w.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)
        w.close()
    assert phase(Workspace(cd, readonly=True).store, "h1") == phase(Workspace(twin, readonly=True).store, "h1") == "synced"


def test_P2g_ops_on_returns_the_archived_ops_of_a_feature_in_apply_order(tmp_path):
    """`Store.ops_on` returns 'the recorded ops on any of `bids`, oldest first'.
    Remembered ops carry no order (`remember` puts every one at −1), so `ops_on`
    concatenates each block's archived list in the iteration order of the SET it
    was handed. A heading's ops and its paragraph's ops interleave in the truth;
    after a fold they come out grouped by block, in an order that depends on
    the hash of two block ids."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws, actor="loop")                        # add h1, ins h1, add p1, ins p1, attach
    ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": "f2"}}], actor="loop")
    truth = [o.id for o in ws.store.ops_on({"h1", "p1"})]
    assert truth == [o.id for o in ws.store.ops]
    assert phase(ws.store, "h1") == "drifted"
    ws.close(); fold(cd)
    st = Workspace(cd, readonly=True).store
    got = [o.id for o in st.ops_on({"h1", "p1"}) if o.id in truth]
    assert got == truth, f"\ntruth {[truth.index(i) for i in truth]}\ngot   {[truth.index(i) for i in got]}"


def test_P2h_a_fold_does_not_change_the_phase_of_a_drifted_feature(tmp_path):
    """`_drifted` decides by the order `ops_on` returns: a refresh newer than
    every prose op is drift. After a fold the paragraph's insert may come after
    the heading's refresh (P2g), and the feature reads `synced`. With forty
    features, some flip and some do not, by block-id hash."""
    from collections import Counter
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    last = START
    for i in range(40):
        h, p = f"h{i}", f"p{i}"
        ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": h}, "payload": {"kind": HEADING, "level": 1, "after": last}},
                   {"kind": K.TEXT_INSERT, "target": {"bid": h, "path": "text"}, "payload": {"after": START, "text": f"F{i}"}},
                   {"kind": K.BLOCK_ADD, "target": {"bid": p}, "payload": {"kind": PARAGRAPH, "after": h}},
                   {"kind": K.TEXT_INSERT, "target": {"bid": p, "path": "text"}, "payload": {"after": START, "text": f"P{i}."}},
                   {"kind": K.BIND_ATTACH, "target": {"fid": h}, "payload": {"file": "c.py", "symbol": f"c.py::s{i}", "fingerprint": "a"}}], actor="loop")
        last = p
    for i in range(40):
        ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": f"h{i}"}, "payload": {"file": "c.py", "symbol": f"c.py::s{i}", "fingerprint": "b"}}], actor="loop")
    before = Counter(phase(ws.store, h.id) for h in ws.store.headings())
    assert before == {"drifted": 40}
    ws.close(); fold(cd)
    after = Counter(phase(Workspace(cd, readonly=True).store, h.id) for h in ws.store.headings())
    assert after == before, f"before {dict(before)} after {dict(after)}"


def test_P2d_two_clones_that_each_snapshot_do_not_lose_a_feature_on_merge(tmp_path):
    """04 §5: 'a branch forked before the snapshot merges correctly'. Two
    clones that each fold: the rebuild takes the newest snapshot file, ignores
    the other, and every session the other folded is in `log/archive/`, which
    nothing reads. One clone's work vanishes with no problem reported."""
    base = tmp_path / "base" / ".codoc"
    ws = Workspace(base); seed(ws); ws.close()
    a = tmp_path / "a" / ".codoc"; a.parent.mkdir(); shutil.copytree(base, a)
    b = tmp_path / "b" / ".codoc"; b.parent.mkdir(); shutil.copytree(base, b)
    for d, fid, title in ((a, "h2", "Alpha"), (b, "h3", "Beta")):
        w = Workspace(d)
        w.submit([{"kind": K.BLOCK_ADD, "target": {"bid": fid}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
                  {"kind": K.TEXT_INSERT, "target": {"bid": fid, "path": "text"}, "payload": {"after": START, "text": title}}], actor=H)
        w.close()
        fold(d)
    # git merge: both snapshot files, both archives, no conflicts
    for src in (b / "log").rglob("*.jsonl"):
        dst = a / "log" / src.relative_to(b / "log")
        dst.parent.mkdir(exist_ok=True)
        if not dst.exists():
            shutil.copy(src, dst)
    m = Workspace(a, readonly=True)
    titles = {markdown(h.text()) for h in m.store.headings()}
    assert {"Alpha", "Beta"} <= titles or m.problems, (titles, m.problems)


def test_P2e_rebuilds_signal_does_not_count_the_snapshots_own_verdicts(tmp_path):
    """`codoc rebuild` prints 'N applied op(s) by <actor> the roles table would
    refuse today' as the forged-log signal (01 §7.3). A snapshot's
    feature.handoff / plan verdicts are stamped `actor=import`, a machine, so
    after every fold the signal fires on the fold itself."""
    from codoc.serve.validate import refused_verdicts
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "x"}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {"sha": "s"}}], actor="loop")
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)
    assert refused_verdicts(ws.store, ws.roles) == {}
    ws.close()
    fold(cd)
    ws = Workspace(cd, readonly=True)
    assert refused_verdicts(ws.store, ws.roles) == {}, refused_verdicts(ws.store, ws.roles)


def test_P2f_the_fingerprint_survives_a_fold_after_a_realized_handoff_was_undone(tmp_path):
    """The one sanctioned regression of I9 (01 §3): a person undoes their
    hand-off. If it had already been realized the feature keeps a realized
    frontier with no hand-off; the snapshot then emits feature.realized alone,
    which `nothing_queued` refuses, and the frontier is dropped by the fold."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ho = ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "x"}}], actor=H)[0]
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {"sha": "s"}}], actor="loop")
    ws.submit([dict(b) for b in ho.inverse], actor=H)          # undo the hand-off
    fp = ws.store.state_fingerprint(); ws.close()
    fold(cd)
    ws = Workspace(cd, readonly=True)
    assert not [o for o in ws.store.ops if o.conflict], [(o.kind, o.conflict) for o in ws.store.ops if o.conflict]
    assert ws.store.state_fingerprint() == fp


# ═════════════════════════════════════════════════════════════════════════════
# 3. The suggester's plan meets the suggester's own undo
# ═════════════════════════════════════════════════════════════════════════════

def test_P3a_a_contributors_backspace_over_words_they_just_proposed_removes_them(tmp_path):
    """Review 6 surfaces A6 let a suggester take back their own plan's words —
    for the `segments` form an undo sends. The editor's Backspace sends a range
    (`recorder.ts:83`), and round 6 turns a suggester's range delete into
    `suggest_del`, so a contributor who types ' More.' and backspaces over it
    is left with their own words struck under their own proposal, and the
    maintainer is asked to accept a deletion of a suggestion."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [_sub("i1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " More."})])
    ins_op = [o for o in ws.drain_inbox() if o.kind == K.TEXT_INSERT][0]
    assert ins_op.conflict is None and ins_op.plan
    L.submit(cd, "hub-alice", [_sub("d1", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                                    {"from": [ins_op.id, 0], "to": [ins_op.id, 5]}, batch="b2")])
    ops = ws.drain_inbox()
    assert all(o.conflict is None for o in ops), [(o.kind, o.conflict) for o in ops]
    t = ws.store.blocks["p1"].text()
    assert T(ws.store, "p1") == "Retries once.", (T(ws.store, "p1"), [m.to_json() for m in t.marks])
    ws.close()


def test_P3b_a_contributor_cannot_revive_their_words_after_the_maintainer_deleted_them(tmp_path):
    """`_own_plan_text` allows a `text.restore` naming only characters an open
    plan of the actor's minted. The maintainer deleting a plan's words is 'an
    ordinary delete: the plan sees fewer of its words survive' (01 §3); a
    contributor restoring them overrides the maintainer's trunk op, which 01
    §7.3 gives a suggester no right to."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [_sub("i1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " More."})])
    ins_op = [o for o in ws.drain_inbox() if o.kind == K.TEXT_INSERT][0]
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"},
                "payload": {"from": [ins_op.id, 0], "to": [ins_op.id, 5]}}], actor=H)       # the maintainer removes them
    assert T(ws.store, "p1") == "Retries once."
    L.submit(cd, "hub-alice", [_sub("u1", K.TEXT_RESTORE, {"bid": "p1", "path": "text"},
                                    {"segments": [[ins_op.id, 0, 6]]}, batch="b2")])
    ops = ws.drain_inbox()
    assert T(ws.store, "p1") == "Retries once.", (T(ws.store, "p1"), [(o.kind, o.conflict) for o in ops])
    ws.close()


# ═════════════════════════════════════════════════════════════════════════════
# 4. The machine gate meets the other machine writers
# ═════════════════════════════════════════════════════════════════════════════

def _graph(edges):
    from codoc.graph.graph import Graph
    return Graph([{"src_symbol": s, "dst_name": d.split("::")[-1], "dst_symbol": d, "kind": "call", "src_file": "c.py", "internal": True}
                  for s, d in edges])


def test_P4a_the_loops_lift_of_a_diagram_lands_on_the_trunk_however_much_changed(tmp_path):
    """03 §5.1: `lift_pass` 'writes the delta with actor=loop on the trunk'; a
    lift 'is attribution, like a binding refresh, never intent'. Round 6 put
    the amend gate in `Workspace.ingest` for every machine, and the lift writes
    through it: when the code graph changes enough, the re-derived diagram
    deletes more than half its source and becomes a proposal from the loop
    that the person has to accept — a branch for the always-on channel."""
    from codoc.kinds.lift import lift_pass
    from codoc.kinds.write import add_block
    ws = Workspace(tmp_path / ".codoc"); seed(ws, actor="loop")
    for sym in ("retry", "backoff", "jitter"):
        ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": f"c.py::{sym}"}}], actor="loop")
    d = add_block(ws, "diagram", {"source": ""}, after="p1", actor="loop")
    assert d.ok and lift_pass(ws, _graph([])) == 1
    first = ws.store.blocks[d.bid].text("source").live_text()
    assert "send" in first and "jitter" in first
    # the code moves: everything the feature owned is gone, one new symbol arrives
    for sym in ("send", "retry", "backoff", "jitter"):
        ws.submit([{"kind": K.BIND_DETACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": f"c.py::{sym}"}}], actor="loop")
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::alpha"}}], actor="loop")
    assert phase(ws.store, "h1") not in ("edited", "queued", "planned")
    assert lift_pass(ws, _graph([])) == 1
    assert open_plans_of(ws.store, "loop") == [], [(p.kind, p.note) for p in open_plans_of(ws.store, "loop")]
    src = ws.store.blocks[d.bid].text("source")
    assert markdown(src, "rejected", ws.store) == src.live_text(), "the lifted source is only a proposal"
    ws.close()


def test_P4b_an_accepted_agent_plan_on_a_diagram_reaches_the_directive(tmp_path):
    """02 §3.3: 'a plan is by definition a request to build'. Round 6 made
    `_at_realized` keep every machine segment, so an agent's ACCEPTED diagram
    edge is already in `old` when `lower` runs, and the directive never asks
    for the edge the person accepted."""
    from codoc.kinds.lift import lift_pass
    from codoc.kinds.write import add_block
    ws = Workspace(tmp_path / ".codoc"); seed(ws, actor="loop")
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::retry"}}], actor="loop")
    d = add_block(ws, "diagram", {"source": ""}, after="p1", actor="loop")
    assert d.ok and lift_pass(ws, _graph([])) == 1                    # no edges in the code yet
    base = ws.store.blocks[d.bid].text("source").live_text()
    pid = ws.open_plan(actor="agent:claude-code", kind="edit", target=d.bid, note="wire the retry")
    res, ops = ws.ingest(d.bid, "source", base_text=base, new_md=base + "\n  send --> retry", actor="agent:claude-code", plan=pid)
    assert not res.refused and ops
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}},
               {"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": f"Build the plan {pid}."}}], actor=H)
    out = directive(ws.store, "h1")
    assert "send --> retry" in out                                    # the accepted words are the document
    assert "Add a dependency or call from `send` to `retry`" in out, out
    ws.close()


def test_P4c_an_agents_amend_of_two_paragraphs_on_a_queued_feature_is_one_plan(tmp_path):
    """01 §4 rule 8: 'One amend is one plan: every block it repairs ... rides
    under the same plan id, so one repair gets one verdict.' Review 6 Applied:
    'an agent's multi-block amend is one proposal too'. `tools.reflect` sends
    one ingest per block with no batch, so `_plan_for` keys on the block id
    and opens a plan per paragraph."""
    from codoc.mcp import tools
    from codoc.substrate import client
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "Backs off."}}], actor=H)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "x"}}], actor=H)
    ws.close()
    try:
        out = tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1", "blocks": [
            {"id": "p1", "text": "Retries twice."}, {"id": "p2", "text": "Backs off exponentially."}]}])
        assert out["ok"], out
        st = client.read(cd).store
        plans = open_plans_of(st, "agent:claude-code")
        assert len(plans) == 1, [(p.id, p.target, p.ops) for p in plans]
    finally:
        client.release(cd)


def test_P4d_a_gated_amend_never_rides_in_the_actors_open_delete_plan(tmp_path):
    """`_plan_for` reuses 'an open plan of theirs on the same block' whatever
    its kind. An agent that proposed deleting a feature and then repairs its
    title (gated: the person wrote it) has the repair filed under the DELETE
    proposal: accepting the deletion accepts the repair, rejecting it drops it."""
    from codoc.mcp import tools
    from codoc.substrate import client
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    try:
        prop = tools.propose(str(cd), ops=[{"kind": "retire_node", "feature_id": "h1"}], note="dead code")
        assert prop["ok"]
        tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1", "title": "Server client"}])
        st = client.read(cd).store
        riders = {st.plans[o.plan].kind for o in st.ops if o.kind == K.TEXT_INSERT and o.plan and "Server" in o.payload.get("text", "")}
        assert riders and "delete" not in riders, riders
    finally:
        client.release(cd)


# ═════════════════════════════════════════════════════════════════════════════
# 5. Applier and window edges
# ═════════════════════════════════════════════════════════════════════════════

def test_P5a_the_window_reports_the_rank_and_shape_a_reopen_displaced_not_the_one_it_set():
    """window.py: 'ranks ... the rank each op displaced, from a move or from a
    verdict that undid one'. A reject records in `moved` the ranks IT displaced;
    `plan.reopen` copies that record and applies it, so its `moved` rows are the
    ranks it SET. `_rank_rows` reads both the same way."""
    m, s = Mint(), Store()
    heading(m, s, "h1", "A"); paragraph(m, s, "p1", "a", after="h1")
    heading(m, s, "h2", "B", after="p1"); paragraph(m, s, "p2", "b", after="h2")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "move", "target": "h2"}))
    apply(s, m.op(K.BLOCK_MOVE, {"bid": "h2"}, {"after": START, "level_delta": 0}, plan="P"))
    rej = apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    assert rej.conflict is None and [b.id for b in s.ordered()] == ["h1", "p1", "h2", "p2"]
    before_reopen = s.blocks["h2"].rank
    re = apply(s, m.op(K.PLAN_REOPEN, {"pid": "P"}, dict(rej.inverse[0]["payload"])))
    assert re.conflict is None and [b.id for b in s.ordered()] == ["h2", "p2", "h1", "p1"]
    w = history_window(s, limit=1)                                    # the window holds only the reopen
    assert w["ops"][0]["kind"] == K.PLAN_REOPEN
    assert w["ranks"].get("h2") == before_reopen, ("before the reopen h2 was last; window says", w["ranks"], before_reopen)


def test_P5b_a_bind_attach_that_conflicted_does_not_make_the_feature_once_bound(tmp_path):
    """`_h_bind_attach` adds to `bound_once` before reading the payload it will
    conflict on, so a malformed attach leaves a feature that never had code
    proposed for deletion as one that 'lost all of its code'."""
    from codoc.loops.reflect import _emptied
    ws = Workspace(tmp_path / ".codoc"); seed(ws, bind=False)
    bad = ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "d.py"}}], actor="loop")[0]
    assert bad.conflict == "missing:symbol"
    assert _emptied(ws.store) == [], ("never had code, proposed for deletion:", ws.store.bound_once)


# ═════════════════════════════════════════════════════════════════════════════
# 6. One applier
# ═════════════════════════════════════════════════════════════════════════════

def test_P6a_two_daemonless_writers_do_not_publish_two_different_trees(tmp_path):
    """01 §1: 'One applier, one order, one truth.' `client.py` makes any
    process the applier when no daemon runs and keeps that workspace for the
    life of the process (the MCP server), while a second process (`codoc
    handoff`, `codoc accept`) does the same. Each publishes from its own store,
    so the derived files describe whichever wrote last."""
    from codoc.substrate.publish import PROJECTION_FILE, publish
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    a, b = Workspace(cd), Workspace(cd)                                # two processes, no daemon
    ins(a, "p1", list(refs(a, "p1")[-1]), " A."); publish(cd, a.store)
    ins(b, "p1", list(refs(b, "p1")[-1]), " B."); publish(cd, b.store)
    a.close(); b.close()
    proj = json.loads((cd / PROJECTION_FILE).read_text())
    text = next(e["fields"]["text"]["text"] for e in proj["blocks"] if e["id"] == "p1")
    assert " A." in text and " B." in text, text


# ═════════════════════════════════════════════════════════════════════════════
# 8. Confirmations (expected to PASS)
# ═════════════════════════════════════════════════════════════════════════════

def test_P8a_replay_equals_store_after_a_suggesters_rewritten_backspace(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [_sub("d1", K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": list(r[0]), "to": list(r[6])})])
    ws.drain_inbox()
    assert [m.kind for m in ws.store.blocks["p1"].text().marks] == [SUGGEST_DEL]
    fp = ws.store.state_fingerprint(); ws.close()
    assert Workspace(cd, readonly=True).store.state_fingerprint() == fp


def test_P8b_a_plan_bold_grown_by_typing_at_its_edge_is_still_rejected_whole(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], actor="loop", plan=a)
    ins(ws, "p1", list(r[6]), "!!", actor=H)                           # grows the plan's bold (I2's one growing kind)
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)
    t = ws.store.blocks["p1"].text()
    assert bold_chars(t) == set() and T(ws.store, "p1") == "Retries!! once."


def test_P8c_reject_then_undo_restores_a_widened_human_bold_exactly(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[11], actor="loop", plan=a)
    widened = bolds(ws.store.blocks["p1"].text())
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)[0]
    assert bold_chars(ws.store.blocks["p1"].text()) == set(r[0:7])
    re = ws.submit([dict(b) for b in rej.inverse], actor=H)[0]
    assert re.conflict is None and bolds(ws.store.blocks["p1"].text()) == widened


def test_P8d_a_plan_open_in_one_clone_and_typing_in_another_merge_and_the_plan_stays_rejectable(tmp_path):
    base = tmp_path / "base" / ".codoc"
    ws = Workspace(base); seed(ws); ws.close()
    a = tmp_path / "a" / ".codoc"; a.parent.mkdir(); shutil.copytree(base, a)
    b = tmp_path / "b" / ".codoc"; b.parent.mkdir(); shutil.copytree(base, b)
    wa, wb = Workspace(a), Workspace(b)
    pid = wa.open_plan(actor="loop", kind="edit", target="p1")
    ins(wa, "p1", list(refs(wa, "p1")[-1]), " Proposed.", actor="loop", plan=pid)
    ins(wb, "p1", list(refs(wb, "p1")[-1]), " Mine.")
    wa.close(); wb.close()
    for f in L.list_sessions(b):
        if not (a / "log" / f.name).exists():
            shutil.copy(f, a / "log" / f.name)
    m = Workspace(a)
    assert not [o for o in m.store.ops if o.conflict]
    assert " Proposed." in T(m.store, "p1") and " Mine." in T(m.store, "p1")
    assert markdown(m.store.blocks["p1"].text(), "rejected", m.store) == "Retries once. Mine."
    rej = m.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None and T(m.store, "p1") == "Retries once. Mine."
    m.close()


def test_P8e_a_snapshot_keeps_a_widened_bold_rejectable(tmp_path):
    """Round 6's by-record match on the archived op after a fold."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[11], actor="loop", plan=a)
    ws.close(); fold(cd)
    ws = Workspace(cd)
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None and bold_chars(ws.store.blocks["p1"].text()) == set(r[0:7])
    ws.close()


def test_P8f_the_gate_turns_a_large_agent_rewrite_into_a_plan_and_a_small_one_lands(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws, actor="loop")
    res, ops = ws.ingest("p1", "text", base_text="Retries once.", new_md="Retries twice.", actor="agent:claude-code")
    assert ops and all(o.plan is None for o in ops)
    res, ops = ws.ingest("p1", "text", base_text="Retries twice.", new_md="Something else entirely, nothing kept.", actor="agent:claude-code")
    assert ops and all(o.plan for o in ops) and ws.store.plans[ops[0].plan].actor == "agent:claude-code"
    ws.close()
