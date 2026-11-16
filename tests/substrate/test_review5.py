"""Review 5 (core): the faults its probes found, as the suite's memory.

Each name says the property, not the probe number; the review itself is in
`docs/substrate/13-substrate-review-5-core.md`.
"""
import json
import shutil
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.derive import directive, markdown, phase
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, SUGGEST_DEL
from codoc.substrate import log as L
from codoc.substrate.snapshot import write_snapshot
from codoc.substrate.workspace import Roles, Workspace

H = "human:local"


def T(store: Store, bid: str) -> str:
    return store.blocks[bid].text().live_text()


def seed(ws: Workspace, actor=H, bind=True):
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": HEADING, "level": 1, "after": START}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "Client"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": "h1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Retries once."}},
    ], actor=actor)
    if bind:
        ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"},
                    "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": "f1", "types_hash": "t1"}}], actor="loop")


def ins(ws, bid, after, text, actor=H, plan=None):
    return ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": "text"},
                       "payload": {"after": after, "text": text}}], actor=actor, plan=plan)[0]


def mark(ws, bid, kind, start, end, on=True, actor=H, plan=None, attrs=None):
    return ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": bid, "path": "text"},
                       "payload": {"kind": kind, "attrs": attrs or {}, "start": list(start), "end": list(end), "on": on}}],
                     actor=actor, plan=plan)[0]


def refs(ws, bid):
    return list(ws.store.blocks[bid].text().live_refs())


def bold_chars(text) -> set:
    out = set()
    for m in text.marks:
        if m.kind == BOLD:
            out.update(text.mark_refs(m))
    return out


def write_roles(cd: Path, roles: dict):
    from codoc.doclang import write_config
    write_config(cd, roles=roles)



def fold(cd) -> None:
    """Snapshot the workspace and reopen it: what a long-lived tree does, and
    what every view has to survive."""
    ws = Workspace(cd)
    write_snapshot(cd, ws.store, ws.clock)
    ws.close()


def _sub(cid, kind, target, payload, batch="b1", **kw):
    return L.Submission(cid=cid, kind=kind, target=target, payload=payload, batch=batch, **kw)


def test_demotion_after_the_fact_does_not_rewrite_history(tmp_path):
    """Spec §7.3 / 04 §6: a recorded success replays as a success whatever the
    table says today."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    write_roles(cd, {"human:gh:alice": "maintainer"})
    ws.roles = Roles.load(cd); ws.store.policy = ws.roles.policy
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    ins(ws, "p1", list(refs(ws, "p1")[-1]), " Proposed.", actor="loop", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:gh:alice")[0]
    assert acc.conflict is None
    fp = ws.store.state_fingerprint()
    ws.close()
    write_roles(cd, {"human:gh:alice": "suggest"})
    ws2 = Workspace(cd, readonly=True)
    assert ws2.store.plans[pid].status == "accepted"
    assert ws2.store.state_fingerprint() == fp


def test_recorded_conflict_replays_as_that_conflict_and_fingerprint_holds(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    bad = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:gh:bob")[0]
    assert bad.conflict == "authority"
    # an unknown-anchor conflict too
    ua = ins(ws, "p1", ["s-nope:1", 0], "x")
    assert ua.conflict == "unknown_anchor"
    fp = ws.store.state_fingerprint()
    ws.close()
    write_roles(cd, {"human:gh:bob": "owner"})       # promoted later: history does not change
    ws2 = Workspace(cd, readonly=True)
    assert ws2.store.by_id[bad.id].conflict == "authority"
    assert ws2.store.by_id[ua.id].conflict == "unknown_anchor"
    assert ws2.store.plans[pid].status == "open"
    assert ws2.store.state_fingerprint() == fp


def _fork(tmp_path, name: str, base: Path) -> Path:
    cd = tmp_path / name / ".codoc"
    cd.parent.mkdir()
    shutil.copytree(base, cd)
    return cd


def test_two_clones_appending_after_the_same_block_merge_without_a_rank_collision(tmp_path):
    """04 §2: given the same set of log files the rebuild is deterministic and
    every later op still applies. Two clones each append a paragraph after
    `p1`; both compute the same fresh rank. After the merge the next append
    after either must not fail."""
    base = tmp_path / "base" / ".codoc"
    ws = Workspace(base); seed(ws); ws.close()
    a, b = _fork(tmp_path, "a", base), _fork(tmp_path, "b", base)
    wa, wb = Workspace(a), Workspace(b)
    wa.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "pa"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "pa", "path": "text"}, "payload": {"after": START, "text": "from a"}}], actor=H)
    wb.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "pb"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "pb", "path": "text"}, "payload": {"after": START, "text": "from b"}}], actor=H)
    wa.close(); wb.close()
    # git merge: the union of session files
    for f in L.list_sessions(b):
        if not (a / "log" / f.name).exists():
            shutil.copy(f, a / "log" / f.name)
    m = Workspace(a)
    assert {x.id for x in m.store.ordered()} == {"h1", "p1", "pa", "pb"}
    ranks = [m.store.blocks[x].rank for x in ("pa", "pb")]
    assert len(set(ranks)) == 2, f"two live blocks share one rank: {ranks}"
    first = m.store.ordered()[2].id
    op = m.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "pc"}, "payload": {"kind": PARAGRAPH, "after": first}}], actor=H)[0]
    assert op.conflict is None, op.conflict
    m.close()


def test_snapshot_keeps_the_fingerprint_when_a_dead_block_exists(tmp_path):
    """01 §7.5: `state_fingerprint` is equal across a fold. A deleted paragraph
    is the ordinary case."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "gone soon"}}], actor=H)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "p2"}, "payload": {}}], actor=H)
    fp = ws.store.state_fingerprint()
    ws.close()
    ws = Workspace(cd)
    write_snapshot(cd, ws.store, ws.clock)
    ws.close()
    ws2 = Workspace(cd, readonly=True)
    assert ws2.store.state_fingerprint() == fp


def test_snapshot_keeps_an_accepted_delete_plan(tmp_path):
    """The fold carries plans with their status (01 §7.5); an accepted
    `kind=delete` plan whose target is dead must come back accepted, not as a
    conflict that leaves the plan behind."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Old"}}], actor=H)
    pid = ws.open_plan(actor="loop", kind="delete", target="h2")
    r = refs(ws, "h2")
    mark(ws, "h2", SUGGEST_DEL, r[0], r[-1], actor="loop", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert acc.conflict is None and not ws.store.blocks["h2"].live
    fp = ws.store.state_fingerprint()
    ws.close()
    ws = Workspace(cd); write_snapshot(cd, ws.store, ws.clock); ws.close()
    ws2 = Workspace(cd, readonly=True)
    assert pid in ws2.store.plans and ws2.store.plans[pid].status == "accepted"
    assert not ws2.store.blocks["h2"].live
    assert ws2.store.state_fingerprint() == fp
    assert not [p for p in ws2.problems], ws2.problems


def test_a_verdict_after_a_snapshot_still_acts_on_the_plans_archived_ops(tmp_path):
    """01 §7.5: 'the plans (with the ops they owned, now archived, and their
    verdicts)'. A plan open across a fold must still be rejectable: its
    inserted words go, its struck words come back unstruck."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    r = refs(ws, "p1")
    ins(ws, "p1", list(r[-1]), " Proposed words.", actor="loop", plan=pid)
    mark(ws, "p1", SUGGEST_DEL, r[0], r[6], actor="loop", plan=pid)       # strike "Retries"
    ws.close()
    ws = Workspace(cd); write_snapshot(cd, ws.store, ws.clock); ws.close()
    ws2 = Workspace(cd)
    assert ws2.store.plans[pid].status == "open"
    rej = ws2.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None
    assert T(ws2.store, "p1") == "Retries once.", T(ws2.store, "p1")
    assert not [m for m in ws2.store.blocks["p1"].text().marks if m.kind == SUGGEST_DEL]
    ws2.close()


def test_accept_after_a_snapshot_strikes_the_words_the_plan_proposed_to_delete(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    r = refs(ws, "p1")
    mark(ws, "p1", SUGGEST_DEL, r[0], r[6], actor="loop", plan=pid)       # strike "Retries"
    ws.close()
    ws = Workspace(cd); write_snapshot(cd, ws.store, ws.clock); ws.close()
    ws2 = Workspace(cd)
    acc = ws2.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert acc.conflict is None
    assert T(ws2.store, "p1") == " once.", T(ws2.store, "p1")
    ws2.close()


def test_an_undo_of_a_pre_snapshot_delete_still_restores_the_block(tmp_path):
    """04 §5: a branch forked before the snapshot merges correctly. Its undo of
    a delete the snapshot folded is `block.restore {ids, by: <the delete>}`."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "keep me"}}], actor=H)
    d = ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "p2"}, "payload": {}}], actor=H)[0]
    inverse = [dict(b) for b in d.inverse]
    ws.close()
    ws = Workspace(cd); write_snapshot(cd, ws.store, ws.clock); ws.close()
    ws2 = Workspace(cd)
    undo = ws2.submit(inverse, actor=H)[0]
    assert undo.conflict is None
    assert ws2.store.blocks["p2"].live, undo.payload
    ws2.close()


def test_a_read_only_command_leaves_no_session_file_behind(tmp_path):
    """`codoc rebuild` / `codoc snapshot` open a writing Workspace and write
    nothing; every such run leaves a tracked header-only log file (git noise)."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    n = len(L.list_sessions(cd))
    for _ in range(3):
        w = Workspace(cd); w.close()
    assert len(L.list_sessions(cd)) == n, "a Workspace that wrote no op left a log file"


def test_translate_never_leaves_an_empty_open_plan(tmp_path):
    """A human-written feature whose translation is refused (paragraph count
    changed / stale) must not be left with an open, empty plan the editor
    offers for a verdict."""
    from codoc.doclang import DocLanguage
    from codoc.loops.translate import translate_tree
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)     # human-written
    from codoc.doclang import resolve
    lang = resolve("zh-Hans")

    def propose(payload, lang, **kw):
        # two paragraphs where the feature has one → "paragraph count changed"
        return {p["id"]: ("客户端", "重试一次。\n\n然后放弃。") for p in payload}

    res = translate_tree(ws, cd, language=lang, propose=propose)
    assert res.skipped and "paragraph count" in res.skipped[0].reason
    open_ = [p for p in ws.store.plans.values() if p.status == "open"]
    assert not open_, [(p.id, p.ops) for p in open_]
    ws.close()


def test_a_deleted_bound_feature_with_delete_code_can_be_settled(tmp_path):
    """02 §2: a bound feature stays in the queue as a phantom until a hand-off
    with delete_code; then the agent deletes its code. The reflect pass that
    follows sees `removed` chunks whose bindings the delete already detached,
    writes no `bind.*` op for the phantom, and `settle` never fires: the phantom
    is queued forever, and `codoc done` cannot name a dead heading by title."""
    from codoc.loop.diff import ChangeSet, ChunkRef
    from codoc.graph.graph import Graph
    from codoc.loops import realize as R
    from codoc.loops.reflect import reflect
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"delete_code": True}}], actor=H)
    assert R.queued(ws.store) == ["h1"]
    # the agent deletes send(); the epoch closes; the reflect pass runs under it
    cs = ChangeSet(removed=[ChunkRef("c.py", "c.py::send", "f1", "t1")])
    res = reflect(ws, cs, Graph(), propose=lambda *a, **k: [], under_epoch=True)
    R.settle(ws, res, sha="abc", files=["c.py"])
    assert R.queued(ws.store) == [], "the phantom's removal was implemented and it is still queued"
    ws.close()


def test_marking_done_with_nothing_handed_off_is_refused_not_a_silent_success(tmp_path):
    """`codoc done` on a feature that was never handed off records
    `feature.realized covers={}` and reports 'marked done' (01 §3: realization
    is monotone and settles a REQUEST; here there is none)."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    op = ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {"sha": "abc"}}], actor=H)[0]
    assert op.conflict is not None, op.payload


def test_emptied_covers_a_feature_whose_last_binding_was_displaced(tmp_path):
    """reflect step 2 is 'state-based: any live feature that once had code and
    has none now'. A feature whose only symbol was re-attached to another
    feature (I8 displacement) has none now but never saw a `bind.detach`."""
    from codoc.loops.reflect import _emptied
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Server"}}], actor=H)
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h2"}, "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": "f1"}}], actor="loop")
    assert not any(b.feature == "h1" for b in ws.store.bindings.values())
    assert "h1" in _emptied(ws.store)
    ws.close()


def test_ingest_of_identical_text_reports_the_same_thing_on_both_paths(tmp_path):
    """Daemonless: {refused: None, ops: []}. Through the inbox the ack carries
    conflict='unchanged' and client.ingest returns {refused: 'unchanged'}. One
    fact, two answers to the agent."""
    from codoc.substrate import client
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    direct = client.ingest(cd, client="mcp", bid="p1", path="text", base_text="Retries once.", new_md="Retries once.")
    client.release(cd)
    # simulate the daemon path: submit + drain + read the ack the way client.ingest does
    ws = Workspace(cd)
    L.submit(cd, "mcp", [L.Submission(cid="c1", kind="ingest", target={"bid": "p1", "path": "text"},
                                      payload={"base_text": "Retries once.", "new_md": "Retries once."})])
    ws.drain_inbox()
    ack = client.wait_for_acks(cd, "mcp", ["c1"], 0.2)[0]
    via_daemon = {"refused": ack.get("conflict"), "ops": [ack["op"]] if ack.get("op") else []}
    ws.close()
    assert direct["refused"] == via_daemon["refused"], (direct, via_daemon)


def test_a_heading_deleted_under_a_paragraph_plan_and_restored_keeps_the_plan_decidable(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws, actor="loop")
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    ins(ws, "p1", list(refs(ws, "p1")[-1]), " Proposed.", actor="loop", plan=pid)
    from codoc.calculus.derive import open_plans, projection
    assert [p.id for p in open_plans(ws.store)] == [pid]
    d = ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor=H)[0]
    assert open_plans(ws.store) == []                      # not offered while its words are dead
    projection(ws.store)                                   # renders without error
    ws.submit([dict(b) for b in d.inverse], actor=H)
    assert [p.id for p in open_plans(ws.store)] == [pid]   # back with the heading
    assert phase(ws.store, "h1") == "proposed"
    ws.close()


def test_amend_lands_one_plan_when_the_second_block_flips_the_gate(tmp_path):
    """reflect §3: 'one amend is one plan'. Block 1 is a small repair (trunk-
    sized), block 2 rewrites a person's paragraph past the gate: both must ride
    under ONE plan, and nothing lands on the trunk."""
    from codoc.loops.reflect import _Pass, ReflectResult, _amend
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "Then it gives up for good."}}], actor=H)
    res = ReflectResult()
    p = _Pass(ws, res, {})
    _amend(p, {"kind": "amend", "feature_id": "h1", "blocks": [
        {"id": "p1", "text": "Retries twice."},
        {"id": "p2", "text": "Completely different words here now."}]})
    plans = [pl for pl in ws.store.plans.values() if pl.status == "open"]
    assert len(plans) == 1, [pl.id for pl in plans]
    assert all(o.plan == plans[0].id for o in res.ops if o.kind.startswith("text.")), [(o.kind, o.plan) for o in res.ops]
    ws.close()


def test_written_by_treats_a_loop_lifted_diagram_as_machine_prose(tmp_path):
    """context.written_by feeds translate's plan-or-trunk choice and the
    prompt's written_by. A feature whose paragraph the loop wrote and whose
    diagram the loop lifted is machine-written; a person's comment thread on it
    (out of flow, never exported) must not turn the whole feature human."""
    from codoc.kinds.write import add_block
    from codoc.loops.context import block_written_by, written_by
    ws = Workspace(tmp_path / ".codoc"); seed(ws, actor="loop")
    w = add_block(ws, "diagram", {"source": "flowchart TB\n  a --> b"}, after="p1", actor="loop")
    assert w.ok
    assert written_by(ws.store, "h1") == "loop"
    c = add_block(ws, "comment", {"thread": "t1", "anchor": "p1", "anchor_text": "Retries", "body": "why once?"}, after="p1", actor=H)
    assert c.ok, c.refused
    assert block_written_by(ws.store, c.bid) == "human"
    assert written_by(ws.store, "h1") == "loop", "a margin comment made the feature read as human-written"
    ws.close()


def test_a_phantom_with_delete_code_can_still_settle_after_a_fold(tmp_path):
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


def test_the_directive_of_a_queued_feature_is_the_same_across_a_fold(tmp_path):
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


def test_emptied_is_the_same_state_before_and_after_a_fold(tmp_path):
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


def test_a_suggesters_backspace_becomes_a_suggest_del_under_their_plan(tmp_path):
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


def test_a_contributor_backspacing_over_their_own_proposal_removes_it(tmp_path):
    """Review 7: the rule above turns EVERY range delete of theirs into a
    suggest_del, so typing ' More.' and backspacing over it left the maintainer
    asked to accept the deletion of a suggestion. Their own proposed words are
    theirs to take back."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [_sub("i1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " More."})])
    ins = [o for o in ws.drain_inbox() if o.kind == K.TEXT_INSERT][0]
    assert ins.conflict is None and ins.plan and T(ws.store, "p1") == "Retries once. More."
    L.submit(cd, "hub-alice", [_sub("d1", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                                    {"from": [ins.id, 0], "to": [ins.id, 5]}, batch="b2")])
    ops = ws.drain_inbox()
    assert all(o.conflict is None for o in ops), [(o.kind, o.conflict) for o in ops]
    assert T(ws.store, "p1") == "Retries once."
    assert not [m for m in ws.store.blocks["p1"].text().marks if m.kind == SUGGEST_DEL]
    # …but a range that also covers a word of the trunk's is still a proposal.
    L.submit(cd, "hub-alice", [_sub("d2", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                                    {"from": list(r[0]), "to": list(r[6])}, batch="b3")])
    ws.drain_inbox()
    assert T(ws.store, "p1") == "Retries once."
    assert [m.kind for m in ws.store.blocks["p1"].text().marks] == [SUGGEST_DEL]
    ws.close()


def test_a_contributor_cannot_revive_words_the_maintainer_deleted(tmp_path):
    """The other half: a restore is judged by the delete it undoes, not by who
    minted the characters. A maintainer removing a plan's words is an ordinary
    trunk delete (01 §3) and a proposal does not get to answer it.

    Review 8 moved the rule from the POLICY to the applier, which is why the
    refusal reads `not_this_plans_delete` rather than `authority`: the policy
    had to read `by_id` to answer, and the hub's door — which judges a
    stranger's log with only the plans in hand — could not. Everything a
    contributor writes is tagged with their plan now, and the applier holds it
    to that plan's own characters."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [_sub("i1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " More."})])
    ins = [o for o in ws.drain_inbox() if o.kind == K.TEXT_INSERT][0]
    gone = ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"},
                       "payload": {"from": [ins.id, 0], "to": [ins.id, 5]}}], actor=H)[0]
    assert T(ws.store, "p1") == "Retries once."
    L.submit(cd, "hub-alice", [_sub("u1", K.TEXT_RESTORE, {"bid": "p1", "path": "text"},
                                    {"segments": [[ins.id, 0, 6]], "by": gone.id}, batch="b2")])
    assert [o.conflict for o in ws.drain_inbox()] == ["not_this_plans_delete"]
    assert T(ws.store, "p1") == "Retries once."
    # …while undoing a delete of THEIR own stands, which is what it has to keep.
    L.submit(cd, "hub-alice", [_sub("i2", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " Twice."}, batch="b3")])
    second = [o for o in ws.drain_inbox() if o.kind == K.TEXT_INSERT][0]
    L.submit(cd, "hub-alice", [_sub("d2", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                                    {"from": [second.id, 0], "to": [second.id, 6]}, batch="b4")])
    theirs = [o for o in ws.drain_inbox() if o.kind == K.TEXT_DELETE][0]
    assert theirs.conflict is None and T(ws.store, "p1") == "Retries once."
    undo = theirs.inverse[0]                            # exactly what the editor's undo sends
    L.submit(cd, "hub-alice", [_sub("u2", undo["kind"], undo["target"], undo["payload"], batch="b5")])
    assert [o.conflict for o in ws.drain_inbox()] == [None]
    assert T(ws.store, "p1") == "Retries once. Twice."
    ws.close()


def test_a_suggesters_no_op_ingest_leaves_no_empty_open_plan(tmp_path):
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


def test_a_contributor_typing_over_several_settles_leaves_one_proposal(tmp_path):
    """The editor flushes every 350 ms. A plan per flush would leave the
    maintainer four verdicts to give on one sentence."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    from codoc.doclang import write_config
    write_config(cd, roles={"human:gh:alice": "suggest"})
    ws.roles = Roles.load(cd)
    ws.store.policy = ws.roles.policy
    seed(ws)
    for i, word in enumerate(["One ", "two ", "three "]):
        last = ws.store.blocks["p1"].text().last_live_ref()
        L.submit(cd, "hub-alice", [_sub(f"c{i}", K.TEXT_INSERT, {"bid": "p1", "path": "text"},
                                        {"after": list(last), "text": word}, batch=f"t{i}")])
        assert all(o.conflict is None for o in ws.drain_inbox() if o.kind != K.PLAN_OPEN)
    open_plans = [p for p in ws.store.plans.values() if p.status == "open"]
    assert len(open_plans) == 1, [(p.id, p.ops) for p in open_plans]
    assert len(open_plans[0].ops) == 3
    ws.close()


def _folded(cd) -> tuple[int, int]:
    """(ops still in the log, ops the snapshot folded)."""
    return (sum(len(L.read_session(p).ops) for p in L.list_sessions(cd)),
            sum(len(L.read_session(p).ops) for p in L.list_snapshots(cd)))


def test_a_settled_tree_folds_whole_and_its_log_is_archived(tmp_path):
    """The fold is Eg-walker's critical version: a point with nothing in flight
    across it. A tree nobody is waiting on is entirely behind such a point."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")
    fp = ws.store.state_fingerprint()
    ws.close()
    fold(cd)
    assert not L.list_sessions(cd), "a settled tree's log was not archived"
    ws2 = Workspace(cd, readonly=True)
    assert ws2.store.state_fingerprint() == fp and not ws2.problems


def test_the_fold_stops_where_the_work_in_flight_begins(tmp_path):
    """A queued feature's directive, an open plan's verdict and drift all read
    the ops. The fold folds what is behind them and leaves the rest as log,
    rather than persisting a derived copy of it."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")
    # A second feature, and a proposal on it that nobody has answered.
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Server"}}], actor=H)
    pid = ws.open_plan(actor="loop", kind="edit", target="h2")
    ins(ws, "h2", list(refs(ws, "h2")[-1]), " (proposed)", actor="loop", plan=pid)
    before = directive(ws.store, "h1")
    ws.close()
    fold(cd)
    log, snap = _folded(cd)
    assert log and snap, f"folded {snap} ops, left {log} — one of the two halves is empty"
    ws2 = Workspace(cd)
    assert not ws2.problems
    # The proposal is still answerable, and answering it takes its own ops.
    assert ws2.store.plans[pid].status == "open"
    rej = ws2.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None and "(proposed)" not in T(ws2.store, "h2")
    assert directive(ws2.store, "h1") == before
    ws2.close()


def test_a_fold_keeps_who_answered_and_who_asked(tmp_path):
    """Review 7: the fold re-emits a decided plan's verdict and a watermark's
    hand-off, and emitted both as `import` — so after a fold the tree could not
    say who accepted a proposal or who asked for the work. `Plan` records no
    verdict actor, so it had to: `decided_by`. WHEN is deliberately not kept,
    because every op a fold emits carries the fold's own clock."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws, actor="loop")
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    ins(ws, "p1", list(refs(ws, "p1")[-1]), " Proposed.", actor="loop", plan=pid)
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "build it"}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"},
                "payload": {"covers": dict(ws.store.frontier)}}], actor="loop")
    assert ws.store.plans[pid].decided_by == H
    ws.close()
    fold(cd)
    st = Workspace(cd, readonly=True).store
    assert st.plans[pid].status == "accepted" and st.plans[pid].decided_by == H
    verdicts = [o for o in st.ops if o.kind == K.PLAN_ACCEPT]
    handoffs = [o for o in st.ops if o.kind == K.FEATURE_HANDOFF]
    assert [o.actor for o in verdicts] == [H], [(o.actor, o.kind) for o in verdicts]
    assert [o.actor for o in handoffs] == [H], [(o.actor, o.kind) for o in handoffs]


def test_a_fold_never_folds_a_fold(tmp_path):
    """Two folds in a row must not carry the first fold's own ops into the
    second: an archive that grows by a copy of the state per fold is not a
    compaction."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")
    ws.close()
    fold(cd)
    first = _folded(cd)[1]
    fold(cd)
    assert _folded(cd)[1] == first, "the second fold grew"
    assert len(L.list_snapshots(cd)) == 1


def test_drift_survives_a_fold_that_takes_every_op_away(tmp_path):
    """Drift is "the code moved and nobody has looked at the prose since". It
    used to compare the POSITION of the newest binding op with the newest prose
    op, so the whole log of every drifted feature had to survive every fold —
    which, after any reflect pass, is every feature (review 8 §C: 0% foldable).
    It is three clocks on state now: the watermark's `code_at`, the newest
    segment or mark on the prose, and the watermark's `realized_at`. Forty
    features, so an accident cannot pass for the property."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    for i in range(40):
        ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": f"h{i}"},
                    "payload": {"kind": HEADING, "level": 1, "after": f"p{i - 1}" if i else START}},
                   {"kind": K.TEXT_INSERT, "target": {"bid": f"h{i}", "path": "text"},
                    "payload": {"after": START, "text": f"Feature {i}"}},
                   {"kind": K.BLOCK_ADD, "target": {"bid": f"p{i}"}, "payload": {"kind": PARAGRAPH, "after": f"h{i}"}},
                   {"kind": K.TEXT_INSERT, "target": {"bid": f"p{i}", "path": "text"},
                    "payload": {"after": START, "text": f"Body {i}."}}], actor="loop")
        ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": f"h{i}"},
                    "payload": {"file": f"m{i}.py", "symbol": f"m{i}.py::f", "fingerprint": "a"}}], actor="loop")
        ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": f"h{i}"},
                    "payload": {"file": f"m{i}.py", "symbol": f"m{i}.py::f", "fingerprint": "b"}}], actor="loop")
    before = {h.id: phase(ws.store, h.id) for h in ws.store.headings()}
    assert set(before.values()) == {"drifted"}, before
    ws.close()
    fold(cd)
    ws2 = Workspace(cd, readonly=True)
    assert {h.id: phase(ws2.store, h.id) for h in ws2.store.headings()} == before
    # …and the fold really did take the ops: this is the state the old rule
    # could not fold at all.
    assert not [o for o in ws2.store.ops if o.kind.startswith("bind.")
                and o.actor != "import"], "the fold kept the binding ops"
    ws2.close()


def test_two_clones_that_each_folded_do_not_lose_one_clones_work(tmp_path):
    """04 §5: a branch forked before a snapshot merges correctly. Two clones
    that each fold is not that case — only one state can be the state — so the
    rebuild says so and reads the archive the losing fold made, instead of
    dropping a feature with no word about it."""
    base = tmp_path / "base" / ".codoc"
    ws = Workspace(base)
    seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")
    ws.close()
    import shutil
    a, b = tmp_path / "a" / ".codoc", tmp_path / "b" / ".codoc"
    for d in (a, b):
        d.parent.mkdir()
        shutil.copytree(base, d)
    for d, bid, title in ((a, "h2", "Alpha"), (b, "h3", "Beta")):
        w = Workspace(d)
        w.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
                  {"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": "text"}, "payload": {"after": START, "text": title}},
                  {"kind": K.FEATURE_HANDOFF, "target": {"fid": bid}, "payload": {}}], actor=H)
        w.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": bid}, "payload": {}}], actor="loop")
        w.close()
        fold(d)
    for src in (b / "log").rglob("*.jsonl"):                 # the git merge: both folds arrive
        dst = a / "log" / src.relative_to(b / "log")
        dst.parent.mkdir(exist_ok=True)
        if not dst.exists():
            shutil.copy(src, dst)
    m = Workspace(a, readonly=True)
    titles = {T(m.store, h.id) for h in m.store.headings()}
    assert {"Alpha", "Beta"} <= titles, (titles, m.problems)
    assert any("another clone" in p.reason for p in m.problems), m.problems
    m.close()


def test_a_second_daemonless_writer_does_not_lose_the_firsts_work(tmp_path):
    """Review 7: 01 §1 is "one applier, one order, one truth", and with no
    daemon any process becomes it and KEEPS its workspace (the MCP server
    does). A second — `codoc accept`, the hub — did the same, so each
    published from a store the other had moved on from and the derived files
    described whichever wrote last. `client.applying` takes an exclusive lock
    for the turn and rebuilds when the log grew by a hand that is not ours."""
    import subprocess
    import sys

    from codoc.substrate import client
    from codoc.substrate.publish import PROJECTION_FILE
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()

    def here(text):
        r = refs(Workspace(cd, readonly=True), "p1")
        return client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                                       "payload": {"after": list(r[-1]), "text": text}}],
                            root_dir=str(tmp_path))

    assert here(" A.")[0]["conflict"] is None                  # this process is now the applier, and holds it
    other = subprocess.run([sys.executable, "-c", f"""
import sys; sys.path.insert(0, {str(Path.cwd())!r})
from codoc.substrate import client
from codoc.substrate.workspace import Workspace
ws = Workspace({str(cd)!r}, readonly=True)
r = list(ws.store.blocks["p1"].text().live_refs())
print(client.write({str(cd)!r}, client="cli", bodies=[{{"kind": "text.insert", "target": {{"bid": "p1", "path": "text"}},
      "payload": {{"after": list(r[-1]), "text": " B."}}}}], root_dir={str(tmp_path)!r}))
"""], capture_output=True, text=True, timeout=120)
    assert other.returncode == 0, other.stderr
    assert here(" C.")[0]["conflict"] is None                  # …and our next turn sees theirs
    proj = json.loads((cd / PROJECTION_FILE).read_text())
    text = next(e["fields"]["text"]["text"] for e in proj["blocks"] if e["id"] == "p1")
    assert " A." in text and " B." in text and " C." in text, text
    client.release(cd)


def test_a_fold_does_not_reapply_an_op_the_applier_refused(tmp_path):
    """Review 8: `state_at` built the folded prefix with a bare `apply` on a
    policy-less store, so an op recorded REFUSED was re-run against the state
    it would meet the second time — and any that now succeeded was folded in.
    A stranger's refused op in a merged log became trunk text at the next
    `codoc snapshot`. Replay honours the record, everywhere (01 §7.3)."""
    cd = tmp_path / ".codoc"
    roles = Roles(table={"human:gh:alice": "suggest", H: "owner"}, default="suggest")
    ws = Workspace(cd, roles=roles)
    seed(ws)
    r = refs(ws, "p1")
    # Straight into the log, as a merged foreign session arrives: no daemon
    # drain to wrap it in a plan of theirs.
    refused = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                          "payload": {"after": list(r[-1]), "text": " Alice was here."}}],
                        actor="human:gh:alice")[0]
    assert refused.conflict == "authority" and "Alice" not in T(ws.store, "p1")
    ws.close()
    fold(cd)
    ws2 = Workspace(cd, roles=roles, readonly=True)
    assert "Alice" not in T(ws2.store, "p1"), T(ws2.store, "p1")


def test_a_hand_off_given_after_a_fold_still_carries_what_the_person_changed(tmp_path):
    """01 §6: the directive says what the person changed since the last
    realization. It read the op that killed each word and the op that placed
    each mark — and a feature nobody has handed off is settled, so the fold
    took both. The hand-off that renders them is given AFTERWARDS, which is
    why nothing at fold time could know. A segment carries its killer now, and
    a mark its own clock, so both questions are about the document."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    r = refs(ws, "p1")
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"},
                "payload": {"from": list(r[8]), "to": list(r[11])}},
               {"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": BOLD, "attrs": {}, "start": list(r[0]), "end": list(r[6]), "on": True}}], actor=H)
    assert T(ws.store, "p1") == "Retries ."
    ws.close()
    fold(cd)
    ws2 = Workspace(cd)
    ws2.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "drop the retry"}}], actor=H)
    d = directive(ws2.store, "h1")
    assert 'removed: "once"' in d and 'Focus: "Retries"' in d, d
    ws2.close()


def test_a_fold_does_not_orphan_the_session_a_held_applier_is_still_writing(tmp_path):
    """Review 8: a held daemonless applier (the MCP server between calls) has
    an open session file whose every op so far is settled, so the fold archived
    it — and the applier's next op went to `log/archive/`, which nothing
    replays. A file says when its writer CLOSED it now, and only a closed file
    is archived: the question "did my file move" is deleted rather than
    answered."""
    from codoc.substrate import client
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    r = list(client.read(cd).store.blocks["p1"].text().live_refs())
    client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                            "payload": {"after": list(r[-1]), "text": " A."}}])
    held = client._OWNED[cd]
    with client.applying(cd) as w:                     # `codoc snapshot`'s own path
        assert write_snapshot(cd, w.store, w.clock) is not None
    r = list(held.store.blocks["p1"].text().live_refs())
    client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                            "payload": {"after": list(r[-1]), "text": " B."}}])
    client.release(cd)
    fresh = Workspace(cd, readonly=True)
    assert T(fresh.store, "p1") == "Retries once. A. B.", (
        T(fresh.store, "p1"), [p.name for p in L.list_sessions(cd)], [p.name for p in L.list_archived(cd)])
    fresh.close()


def test_a_held_appliers_reads_see_what_another_process_wrote(tmp_path):
    """`applying()` catches a held workspace up when the log grew by another
    hand; `read()` did not, and every MCP read and the `base_text` its
    `reflect` computes go through it. The agent's next amend was `stale_base`
    against a tree the person had already changed with `codoc accept`."""
    import subprocess
    import sys

    from codoc.substrate import client
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    r = list(client.read(cd).store.blocks["p1"].text().live_refs())
    client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                            "payload": {"after": list(r[-1]), "text": " A."}}])
    other = subprocess.run([sys.executable, "-c", f"""
import sys; sys.path.insert(0, {str(Path.cwd())!r})
from codoc.substrate import client
ws = client.read({str(cd)!r})
r = list(ws.store.blocks["p1"].text().live_refs())
client.write({str(cd)!r}, client="cli", bodies=[{{"kind": "text.insert", "target": {{"bid": "p1", "path": "text"}},
      "payload": {{"after": list(r[-1]), "text": " B."}}}}])
"""], capture_output=True, text=True, timeout=120)
    assert other.returncode == 0, other.stderr
    assert " B." in T(client.read(cd).store, "p1"), T(client.read(cd).store, "p1")
    client.release(cd)


def test_a_contributor_cannot_revive_words_they_withdrew_once_the_plan_is_decided(tmp_path):
    """Review 8: propose, withdraw, wait for the verdict, restore. The restore
    used to be judged by "was the delete yours", which it was, so the words
    came back live carrying a DECIDED plan's tag — prose nobody accepted after
    an accept, and after a reject a state the calculus assumes cannot exist
    (the `rejected` read mode hides an OPEN plan's words, so the export printed
    them). Everything a contributor writes rides in their plan now, so a
    decided plan simply has no room left in it."""
    for verdict in (K.PLAN_ACCEPT, K.PLAN_REJECT):
        cd = tmp_path / verdict / ".codoc"
        ws = Workspace(cd); seed(ws)
        r = refs(ws, "p1")
        L.submit(cd, "hub-alice", [_sub("i1", K.TEXT_INSERT, {"bid": "p1", "path": "text"},
                                        {"after": list(r[-1]), "text": " More."})])
        ins = [o for o in ws.drain_inbox() if o.kind == K.TEXT_INSERT][0]
        L.submit(cd, "hub-alice", [_sub("d1", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                                        {"segments": [[ins.id, 0, 6]]}, batch="b2")])
        gone = [o for o in ws.drain_inbox() if o.kind == K.TEXT_DELETE][0]
        assert gone.conflict is None and gone.plan == ins.plan   # their own words, in their own plan
        ws.submit([{"kind": verdict, "target": {"pid": ins.plan}, "payload": {}}], actor=H)
        L.submit(cd, "hub-alice", [_sub("u1", K.TEXT_RESTORE, {"bid": "p1", "path": "text"},
                                        {"segments": [[ins.id, 0, 6]], "by": gone.id}, batch="b3")])
        after = ws.drain_inbox()
        assert all(o.conflict for o in after), [(o.kind, o.conflict) for o in after]
        assert T(ws.store, "p1") == "Retries once."
        assert markdown(ws.store.blocks["p1"].text(), "rejected", ws.store) == "Retries once."
        ws.close()


def test_a_contributors_typo_corrected_within_one_flush_is_not_struck_as_a_proposal(tmp_path):
    """The editor batches a flush, so an insert and the Backspace over it
    arrive together exactly when somebody corrects a typo — with `$cid` refs
    that name an op the daemon has not minted yet. The drain decided whether
    the range was their own words BEFORE resolving them, so the answer was
    always no and the correction became a strike-through under their own
    proposal."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [
        _sub("c1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " Moer"}),
        _sub("c2", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
             {"from": ["$cid", "c1", 0], "to": ["$cid", "c1", 4]}),
    ])
    ops = ws.drain_inbox()
    assert all(o.conflict is None for o in ops), [(o.kind, o.conflict) for o in ops]
    assert T(ws.store, "p1") == "Retries once."
    assert not ws.store.blocks["p1"].text().proposals(kind=SUGGEST_DEL)
    ws.close()
