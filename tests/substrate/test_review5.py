"""Review 5 (core): the faults its probes found, as the suite's memory.

Each name says the property, not the probe number; the review itself is in
`docs/substrate/13-substrate-review-5-core.md`.
"""
import shutil
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.derive import phase
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
    w = add_block(ws, "diagram", {"source": "flowchart TB\n  a --> b", "caption": ""}, after="p1", actor="loop")
    assert w.ok
    assert written_by(ws.store, "h1") == "loop"
    c = add_block(ws, "comment", {"thread": "t1", "anchor": "p1", "anchor_text": "Retries", "body": "why once?"}, after="p1", actor=H)
    assert c.ok, c.refused
    assert block_written_by(ws.store, c.bid) == "human"
    assert written_by(ws.store, "h1") == "loop", "a margin comment made the feature read as human-written"
    ws.close()
