"""Regressions and properties from `10-substrate-review-2.md` (A1–A19, T1–T3)."""
import json
import random

import pytest

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import ground, history, ink, phase, plan_claims
from codoc.loops.realize import directive
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.export import export_markdown
from codoc.calculus.ids import START
from codoc.calculus.inline_md import Span, parse, render
from codoc.calculus.ops import covered
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, CITE, LINK
from codoc.index.graph import Graph
from codoc.daemon.epoch import read_activity, write_activity
from codoc.index.changes import ChangeSet
from codoc.loops.realize import handoff, settle
from codoc.loops.reflect import reflect
from codoc.workspace import log as L
from codoc.daemon.daemon import Daemon
from codoc.workspace.log import Submission
from codoc.workspace import Roles, Workspace
from tests.calculus.test_apply import Mint, heading, paragraph
from tests.calculus.test_derive_export import build, import_markdown
from tests.calculus.test_ingest import run_ingest
from tests.loops.test_reflect import chunk, seed


def T(s, bid):
    return s.blocks[bid].text().live_text()


def md_of(s, bid):
    from codoc.calculus.derive import markdown
    return markdown(s.blocks[bid].text())


# ── A1 / T3: watermarks under interleaved actors ────────────────────────────

def test_ink_keeps_unsent_words_apart_from_sent_neighbours():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client", actor="loop")
    ins = paragraph(m, s, "p1", "Retries once.", after="h1", actor="loop")
    a = apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 12], "text": " A"}))
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C"}))
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [a.id, 1], "text": " B"}))
    assert [(c.stage, c.text) for c in ink(s, "h1")] == [("sent", " A"), ("unsent", " B")]
    assert phase(s, "h1") == "edited"
    assert phase(s, "h1", realizing={"h1"}) == "edited"          # doc wins over the lease (B3)
    d = directive(s, "h1")
    assert 'added: " A"' in d and '" A B"' not in d


def test_a_hand_edit_never_settles_a_request_but_a_closed_epoch_does(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Retry five times."}}], actor="human:local")
    handoff(ws, "h1", actor="human:local", asked="retry five times")
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=lambda *a, **k: [])
    assert r.caused == {} and settle(ws, r, sha="typo") == []
    assert phase(ws.store, "h1") == "queued"
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=lambda *a, **k: [],
                under_epoch=True)
    assert len(settle(ws, r, sha="built")) == 1
    w = ws.store.watermarks("h1")
    assert covered(w.handoff, w.realized) and phase(ws.store, "h1") == "synced"


def test_partial_realization_is_one_fact():
    a, b, s = Mint("a", "human:a"), Mint("b", "human:a"), Store()
    heading(a, s, "h1", "C", actor="loop")
    ins = paragraph(a, s, "p1", "x", after="h1", actor="loop")
    apply(s, a.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C"}))
    apply(s, b.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 0], "text": "y"}))
    apply(s, a.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    w = s.watermarks("h1")
    apply(s, a.op(K.FEATURE_REALIZED, {"fid": "h1"}, {"covers": {"a": 5}}))
    assert not covered(w.handoff, w.realized) and phase(s, "h1") == "queued"
    assert [c.stage for c in ink(s, "h1")] == ["sent"]


# ── A2 / A3 / A4 / T2: rebuild is the store ─────────────────────────────────

def _para(ws, bid, text, after=START, actor="human:local"):
    return ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": PARAGRAPH, "after": after}},
                      {"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": "text"}, "payload": {"after": START, "text": text}}],
                     actor=actor)[1]


def test_subset_accept_replays_as_the_same_subset(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ins = _para(ws, "p1", "base")
    pid = ws.open_plan(actor="agent:x")
    i1 = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [ins.id, 3], "text": " A"}}], actor="agent:x", plan=pid)[0]
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [i1.id, 1], "text": " B"}}], actor="agent:x", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {"subset": [i1.id]}}], actor="human:local")[0]
    assert T(ws.store, "p1") == "base A" and acc.payload["subset"] == [i1.id]
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert T(ws2.store, "p1") == "base A"
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()


def test_restored_mark_keeps_its_placer_through_rebuild(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ins = _para(ws, "p1", "the client")
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": LINK, "attrs": {"href": "https://a"}, "start": [ins.id, 0], "end": [ins.id, 9], "on": True}}], actor="human:local")
    disp = ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                       "payload": {"kind": LINK, "attrs": {"href": "https://b"}, "start": [ins.id, 4], "end": [ins.id, 9], "on": True}}], actor="loop")[0]
    ws.submit([dict(b) for b in disp.inverse], actor="human:local")           # undo, as the person
    marks = ws.store.blocks["p1"].text().marks
    assert [(m.attrs["href"], m.actor) for m in marks] == [("https://a", "human:local")]
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()


def _random_body(rng, store, step):
    live = [b for b in store.ordered() if b.kind == PARAGRAPH and b.text().live_len()]
    if not live or rng.random() < 0.1:
        return {"kind": K.BLOCK_ADD, "target": {"bid": f"b{step}"}, "payload": {"kind": PARAGRAPH, "after": START}}
    b = rng.choice(live)
    t = b.text()
    n = t.live_len()
    r = rng.random()
    if r < 0.45:
        return {"kind": K.TEXT_INSERT, "target": {"bid": b.id, "path": "text"},
                "payload": {"after": list(t.ref_at(rng.randrange(n))), "text": rng.choice([" a", " bb", "c "])}}
    if r < 0.65:
        i, j = sorted(rng.sample(range(n), 2)) if n > 1 else (0, 0)
        return {"kind": K.TEXT_DELETE, "target": {"bid": b.id, "path": "text"},
                "payload": {"from": list(t.ref_at(i)), "to": list(t.ref_at(j))}}
    if r < 0.85:
        i, j = sorted(rng.sample(range(n), 2)) if n > 1 else (0, 0)
        return {"kind": K.TEXT_MARK, "target": {"bid": b.id, "path": "text"},
                "payload": {"kind": rng.choice([BOLD, LINK]), "attrs": {} if rng.random() < 0.5 else {"href": "https://x"},
                            "start": list(t.ref_at(i)), "end": list(t.ref_at(j)), "on": rng.random() < 0.7}}
    if r < 0.93 and n > 1:
        return {"kind": K.BLOCK_SPLIT, "target": {"bid": b.id, "path": "text"},
                "payload": {"new": f"s{step}", "at": list(t.ref_at(rng.randrange(n - 1)))}}
    return {"kind": K.BLOCK_MOVE, "target": {"bid": b.id}, "payload": {"after": START}}


def test_workspace_rebuild_equals_live_store_through_verdicts_and_undo(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    rng = random.Random(3)
    ws.clock.wall += 5 * 60 * 1000
    for step in range(250):
        body = _random_body(rng, ws.store, step)
        actor = rng.choice(["human:local", "loop", "agent:x"])
        if body["kind"] == K.TEXT_MARK and body["payload"]["kind"] == LINK and not body["payload"]["attrs"]:
            body["payload"]["attrs"] = {"href": "https://y"}
        got = ws.submit([body], actor=actor)[0]
        if got.conflict is None and got.inverse and rng.random() < 0.2:
            ws.submit([dict(b) for b in got.inverse], actor="human:local")
        if step % 40 == 0:
            live = [b for b in ws.store.ordered() if b.kind == PARAGRAPH and b.text().live_len()]
            if live:
                b = live[0]
                pid = ws.open_plan(actor="agent:x")
                ids = [o.id for o in ws.submit([
                    {"kind": K.TEXT_INSERT, "target": {"bid": b.id, "path": "text"}, "payload": {"after": list(b.text().ref_at(0)), "text": "P"}},
                    {"kind": K.TEXT_INSERT, "target": {"bid": b.id, "path": "text"}, "payload": {"after": list(b.text().ref_at(0)), "text": "Q"}},
                ], actor="agent:x", plan=pid)]
                verdict = {"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {"subset": ids[:1]}} if rng.random() < 0.6 \
                    else {"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}
                v = ws.submit([verdict], actor="human:local")[0]
                if rng.random() < 0.5 and v.inverse:
                    ws.submit([dict(b) for b in v.inverse], actor="human:local")
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.problems == []
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()
    assert export_markdown(ws2.store) == export_markdown(ws.store)


# ── A5 / T1: ingest is a fixed point ────────────────────────────────────────

def test_ingest_turns_a_bold_off_widens_one_and_appends_without_reauthoring():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "alpha beta", after="h1")
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 0], "end": [ins.id, 4], "on": True}))
    res, _ = run_ingest(s, Mint("h", "human:a"), "p1", "alpha **beta**", actor="human:a")
    assert res.refused is None and md_of(s, "p1") == "alpha **beta**"
    m2, s2 = Mint(), Store()
    heading(m2, s2, "h1", "C")
    ins2 = paragraph(m2, s2, "p1", "Retries once via x.", after="h1")
    apply(s2, m2.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins2.id, 8], "end": [ins2.id, 11], "on": True}))
    res, _ = run_ingest(s2, Mint("h", "human:a"), "p1", "**Retries once** via x.", actor="human:a")
    assert md_of(s2, "p1") == "**Retries once** via x."
    assert s2.blocks["p1"].text().marks[0].actor == "human:a"
    m3, s3 = Mint(), Store()
    heading(m3, s3, "h1", "C")
    ins3 = paragraph(m3, s3, "p1", "Retries once", after="h1")
    apply(s3, m3.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins3.id, 8], "end": [ins3.id, 11], "on": True}))
    res, _ = run_ingest(s3, Mint("l", "loop"), "p1", "Retries **once** more")
    assert res.refused is None and res.deleted == 0 and md_of(s3, "p1") == "Retries **once** more"
    assert [(seg.actor, seg.chars) for seg in s3.blocks["p1"].text().segs if seg.live] == [("human:a", "Retries once"), ("loop", " more")]


def test_ingest_is_a_fixed_point_on_text_and_marks_fuzzed():
    rng = random.Random(11)
    words = ["alpha", "beta", "gamma", "delta", "once", "twice", "client", "retries"]
    seen_ok = 0
    for trial in range(200):
        m, s = Mint(), Store()
        heading(m, s, "h1", "T")
        base_words = [rng.choice(words) for _ in range(rng.randint(1, 8))]
        ins = paragraph(m, s, "p1", " ".join(base_words), after="h1")
        t = s.blocks["p1"].text()
        lt = t.live_text()
        cited = None
        for kind, attrs in ((BOLD, {}), (CITE, {"file": "c.py", "symbol": "C"})):
            w = rng.choice(base_words)
            lo = lt.index(w)
            apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                          {"kind": kind, "attrs": attrs, "start": list(t.ref_at(lo)), "end": list(t.ref_at(lo + len(w) - 1)), "on": True}))
            if kind == CITE:
                cited = w
        new_words = [w for w in base_words if rng.random() > 0.3] + [rng.choice(words) for _ in range(rng.randint(0, 3))]
        rng.shuffle(new_words)
        actor = rng.choice(["human:a", "loop"])
        parts = []
        for w in new_words:
            if w == cited and (actor == "loop" or rng.random() < 0.5):
                parts.append(f"[{w}](codoc:c.py#C)")
            elif rng.random() < 0.3:
                parts.append(f"**{w}**")
            else:
                parts.append(w)
        new_md = " ".join(parts) or "x"
        res, applied = run_ingest(s, Mint("i", actor), "p1", new_md, actor=actor)
        if res.refused:
            assert actor == "loop" and res.refused.startswith("would_drop_human_"), (trial, res.refused, new_md)
            continue
        assert all(o.conflict is None for o in applied), (trial, new_md, [(o.kind, o.conflict) for o in applied])
        t2 = s.blocks["p1"].text()
        assert md_of(s, "p1") == new_md, (trial, base_words, new_md, md_of(s, "p1"))
        for seg in t2.segs:
            if seg.live and seg.op == ins.id:
                assert seg.chars in " ".join(base_words)
        seen_ok += 1
    assert seen_ok > 100


# ── A6 / A7: inline markdown and export are fixed points ────────────────────

def test_bold_across_a_cite_edge_and_literal_markup_round_trip():
    assert render("abc", [Span(LINK, {"href": "https://u"}, 0, 2), Span(BOLD, {}, 1, 3)]) == "[a**b**](https://u)**c**"
    m, s = Mint(), Store()
    heading(m, s, "b-000000000001", "T")
    ins = paragraph(m, s, "b-000000000002", "see the client now", after="b-000000000001")
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"},
                  {"kind": CITE, "attrs": {"file": "c.py", "symbol": "C"}, "start": [ins.id, 4], "end": [ins.id, 13], "on": True}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 13], "on": True}))
    paragraph(m, s, "b-000000000003", "# Not a heading {#b-000000000009}", after="b-000000000002")
    paragraph(m, s, "b-000000000004", "type **stars** to bold and [x](https://y) too", after="b-000000000003")
    md1 = export_markdown(s)
    s2 = Store()
    build(s2, Mint("t"), import_markdown(md1))
    assert export_markdown(s2) == md1
    paras = [b for b in s2.ordered() if b.kind == PARAGRAPH]
    assert [p.text().live_text() for p in paras] == ["see the client now", "# Not a heading {#b-000000000009}",
                                                      "type **stars** to bold and [x](https://y) too"]
    assert sorted(mk.kind for mk in paras[0].text().marks) == [BOLD, CITE] and paras[1].text().marks == []
    assert len([b for b in s2.ordered() if b.kind == HEADING]) == 1


def test_export_leaves_open_plans_out(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Maybe."}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p9"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p9", "path": "text"}, "payload": {"after": START, "text": "Whole new paragraph."}}],
              actor="loop", plan=pid)
    md = export_markdown(ws.store)
    assert "Maybe" not in md and "Whole new" not in md and "Retries once before giving up." in md


# ── A8: authority ───────────────────────────────────────────────────────────

def test_agents_write_no_verdicts_and_suggesters_ride_only_their_own_plans(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": HEADING, "level": 1, "after": START}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": "h1"}}], actor="human:local")
    pid = ws.open_plan(actor="loop", note="the loop's repair")
    denied = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}},
                        {"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}},
                        {"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor="agent:claude-code")
    assert [o.conflict for o in denied] == ["authority"] * 3
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor="human:local")
    assert ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")[0].conflict is None
    smuggled = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "x"}}],
                         actor="human:contrib", plan=pid)[0]
    assert smuggled.conflict == "authority" and smuggled.id not in ws.store.plans[pid].ops
    own = ws.open_plan(actor="human:contrib")
    ok = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "x"}}],
                   actor="human:contrib", plan=own)[0]
    assert ok.conflict is None


def test_roles_table_over_every_kind():
    roles = Roles({"human:local": "owner", "human:m": "maintainer"})
    from codoc.calculus.ops import Op
    store = Store()
    store.plans["p-mine"] = __import__("codoc.calculus.store", fromlist=["Plan"]).Plan("p-mine", "human:s")
    store.plans["p-theirs"] = __import__("codoc.calculus.store", fromlist=["Plan"]).Plan("p-theirs", "loop")

    def allowed(actor, kind, plan=None):
        return roles.policy(store, Op(id="x:1", at="", actor=actor, batch="", kind=kind, plan=plan)) is None

    for kind in K.KINDS:
        assert allowed("human:local", kind) and allowed("human:m", kind)
        assert allowed("agent:a", kind) == (kind not in K.VERDICTS)
        assert allowed("loop", kind) == (kind not in K.VERDICTS or kind == K.FEATURE_REALIZED)
        assert allowed("human:s", kind) == (kind == K.PLAN_OPEN)
        assert allowed("human:s", kind, "p-mine")            # the policy allows; apply's not_plannable decides the rest
        assert not allowed("human:s", kind, "p-theirs") or kind == K.PLAN_OPEN


# ── A9: inbox durability ────────────────────────────────────────────────────

def test_torn_and_malformed_inbox_lines(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    d = L.inbox_dir(tmp_path / ".codoc")
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps(Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "X"}).__dict__)
    (d / "ide.jsonl").write_text(line[:20])
    assert ws.drain_inbox({"ide": "human:local"}) == []
    with (d / "ide.jsonl").open("a") as fh:
        fh.write(line[20:] + "\n")
    assert len(ws.drain_inbox({"ide": "human:local"})) == 1 and T(ws.store, "p1") == "X"
    bad = json.dumps({"cid": "c2", "kind": K.TEXT_INSERT, "target": None, "payload": {}})
    good = json.dumps({"cid": "c3", "kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Y"}})
    with (d / "ide.jsonl").open("a") as fh:
        fh.write(bad + "\n" + good + "\n")
    assert len(ws.drain_inbox({"ide": "human:local"})) == 1 and T(ws.store, "p1") == "YX"
    acks = [json.loads(l) for l in (d / "ide.ack.jsonl").read_text().splitlines()]
    assert [(a["cid"], a["op"] != "", a["conflict"]) for a in acks] == [("c1", True, None), ("c2", False, "target must be an object"), ("c3", True, None)]
    assert ws.drain_inbox({"ide": "human:local"}) == []          # nothing re-applied


# ── A10 / A14 / A15 / A16 / A17: plan hygiene in reflect ────────────────────

def test_rejecting_an_add_plan_detaches_its_bindings(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    propose = lambda c, s, t, **kw: [{"kind": "add_node", "parent_id": "h1", "title": "Storage", "description": "Saves.", "bindings": [["s.py", "s.py::save"]]}]
    r = reflect(ws, ChangeSet(added=[chunk("s.py", "s.py::save", "f9", "t9")]), Graph(), propose=propose)
    pid = r.plans[0]
    fid = ws.store.plans[pid].target
    assert ws.store.bindings[("s.py", "s.py::save")].feature == fid
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:local")[0]
    assert not ws.store.blocks[fid].live and ("s.py", "s.py::save") not in ws.store.bindings
    ws.submit([dict(b) for b in rej.inverse], actor="human:local")           # reopen restores the binding
    assert ws.store.blocks[fid].live and ws.store.bindings[("s.py", "s.py::save")].feature == fid


def test_amend_is_one_unit_and_omission_means_unchanged(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "Still true."}}], actor="loop")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")
    same = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": T(ws.store, "p1")}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=same)
    assert r.plans == []                                                   # nothing to propose, no empty plan
    two = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1",
                                  "blocks": [{"id": "p1", "text": T(ws.store, "p1").replace("once", "twice")}, {"text": "New one."}, {"text": "New two."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f4", "t1")]), Graph(), propose=two)
    assert len(r.plans) == 1
    assert [c.text for c in plan_claims(ws.store, "h1") if c.stage == "removed"] == ["once"]   # p2 untouched
    assert len([b for b in ws.store.feature_body("h1") if b.kind == PARAGRAPH]) == 4
    strike = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p2", "delete": True}]}]
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": r.plans[0]}, "payload": {}}], actor="human:local")
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f5", "t1")]), Graph(), propose=strike)
    assert len(r.plans) == 1 and [c.text for c in plan_claims(ws.store, "h1") if c.stage == "removed"] == ["Still true."]


def test_model_may_not_attach_a_symbol_the_index_removed_and_bad_shapes_are_refused(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Other"}}], actor="loop")
    propose = lambda c, s, t, **kw: [{"kind": "attach", "feature_id": "h2", "bindings": ["c.py::send"]},
                                     {"kind": "amend", "feature_id": "h1", "blocks": "not a list"},
                                     {"kind": "add_node"}, "junk"]
    cs = ChangeSet(removed=[chunk("c.py", "c.py::send", "f1", "t1")], added=[chunk("n.py", "n.py::x", "fz", "tz")])
    r = reflect(ws, cs, Graph(), propose=propose, allow_delete=False)
    assert ("c.py", "c.py::send") not in ws.store.bindings
    assert r.refused == [("attach", "no bindable symbol"), ("amend", "amend.blocks must be a list of objects"),
                         ("add_node", "add_node.title must be a non-empty string"), ("", "not an object")]
    assert len(r.plans) == 1                                               # the coverage net still placed n.py::x


def test_ingest_refuses_a_dead_block_and_a_non_text_path(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "old"}},
               {"kind": K.BLOCK_DELETE, "target": {"bid": "p1"}, "payload": {}}], actor="human:local")
    res, ops = ws.ingest("p1", "text", base_text="old", new_md="new", actor="loop")
    assert res.refused == "dead_block" and ops == []
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    res, ops = ws.ingest("p2", "rank", base_text="", new_md="new", actor="loop")
    assert res.refused == "not_text" and ops == []
    assert all(o.conflict is None for o in ws.store.ops)                   # nothing refused reached the log


# ── A12 / A13 / A19: views read the canonical block ─────────────────────────

def test_history_follows_an_op_to_the_block_it_landed_in_and_lists_conflicts():
    m, s = Mint(), Store()
    heading(m, s, "h1", "One")
    paragraph(m, s, "a", "aaa", after="h1")
    heading(m, s, "h2", "Two", after="a")
    b = paragraph(m, s, "b", "bbb", after="h2")
    apply(s, m.op(K.BLOCK_MOVE, {"bid": "b"}, {"after": "a"}))
    apply(s, m.op(K.BLOCK_MERGE, {"into": "a", "bid": "b"}, {}))
    ins = apply(s, m.op(K.TEXT_INSERT, {"bid": "b", "path": "text"}, {"after": [b.id, 2], "text": "!"}))
    bad = apply(s, m.op(K.TEXT_INSERT, {"bid": "a", "path": "text"}, {"after": ["nope:1", 0], "text": "?"}))
    assert ins.payload["bid"] == "a" and bad.conflict == "unknown_anchor"
    h = history(s, "h1")
    assert ins in h and bad in h and ins not in history(s, "h2")


def test_two_human_bolds_are_two_focus_items():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "Retries once and logs.", after="h1")
    for lo, hi in ((8, 11), (17, 20)):
        apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, lo], "end": [ins.id, hi], "on": True}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 17], "end": [ins.id, 20], "on": False}))
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    assert 'Focus: "once"' in directive(s, "h1") and '"logs"' not in directive(s, "h1")   # an un-bold is not a focus


def test_ground_paints_nothing_on_a_tree_nobody_touched_and_shows_repairs_after():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client", actor="loop")
    ins = paragraph(m, s, "p1", "Retries once.", after="h1", actor="loop")
    assert ground(s, "h1") == []
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 12], "text": " Mine."}))
    loop = Mint("l", "loop")
    apply(s, loop.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 8], "to": [ins.id, 11]}))
    apply(s, loop.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 11], "text": "twice"}))
    assert [(c.stage, c.text) for c in ground(s, "h1")] == [("removed", "once"), ("added", "twice")]


def test_projection_carries_the_realizing_lease_and_publish_skips_unchanged(tmp_path):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    seed(d.ws)
    t = d.ws.store.blocks["p1"].text()
    d.ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Do it."}}], actor="human:local")
    handoff(d.ws, "h1", actor="human:local")
    d.process_batch([])
    assert json.loads((d.codoc_dir / "status.json").read_text())["state"] == "queued"
    (d.codoc_dir / "status.json").write_text(json.dumps({"state": "realizing", "version": 2}))
    d.process_batch([])
    proj = json.loads((d.codoc_dir / "projection.json").read_text())
    assert proj["phases"] == {"h1": "realizing"}
    stamp = (d.codoc_dir / "projection.json").stat().st_mtime_ns
    assert d.process_batch([]) == "no-op" and (d.codoc_dir / "projection.json").stat().st_mtime_ns == stamp


def test_head_change_rebuilds_from_the_logs_on_disk(tmp_path):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    seed(d.ws)
    d.process_batch([])
    # A peer's session file arrives (a pull) and HEAD moves.
    other = Workspace(d.codoc_dir, session="s-peer")
    t = other.store.blocks["p1"].text()
    other.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " From a peer."}}], actor="human:local")
    other.close()
    d.state.head = "moved"
    note = d.process_batch([])
    assert "rotated" in note and T(d.ws.store, "p1").endswith(" From a peer.")
