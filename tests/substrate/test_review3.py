"""Regressions from `11-substrate-review-3.md` (A1–A16)."""
import json
import random
import shutil

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import directive, history, markdown, phase, plan_claims
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.export import export_markdown
from codoc.calculus.ids import START
from codoc.calculus.inline_md import Span, parse, render
from codoc.calculus.ops import covered
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, CITE, LINK
from codoc.doclang import resolve
from codoc.graph.graph import Graph
from codoc.loop.diff import ChangeSet
from codoc.loops.realize import handoff, settle
from codoc.loops.reflect import reflect
from codoc.loops.translate import translate_tree
from codoc.substrate import log as L
from codoc.substrate.daemon import Daemon
from codoc.substrate.log import Submission
from codoc.substrate.workspace import Roles, Workspace
from tests.calculus.test_apply import Mint, heading, paragraph
from tests.calculus.test_ingest import run_ingest
from tests.loops.test_reflect import chunk, seed


def T(s, bid):
    return s.blocks[bid].text().live_text()


def md_of(s, bid):
    return markdown(s.blocks[bid].text())


def mark(m, s, bid, ins, lo, hi, kind=BOLD, attrs=None, actor=None, plan=None):
    return apply(s, m.op(K.TEXT_MARK, {"bid": bid, "path": "text"},
                         {"kind": kind, "attrs": attrs or {}, "start": [ins.id, lo], "end": [ins.id, hi], "on": True},
                         actor=actor, plan=plan))


# ── A1: an insert inside any mark ────────────────────────────────────────────

def test_insert_inside_a_live_mark_is_not_captured_by_it():
    for kind, attrs, new in ((BOLD, {}, "**alpha** gamma **beta**"),
                             (LINK, {"href": "https://u"}, "[alpha](https://u) gamma [beta](https://u)")):
        m, s = Mint(), Store()
        heading(m, s, "h1", "C")
        ins = paragraph(m, s, "p1", "alpha beta", after="h1")
        mark(m, s, "p1", ins, 0, 9, kind, attrs)
        res, applied = run_ingest(s, Mint("h", "human:a"), "p1", new, actor="human:a")
        assert res.refused is None and all(o.conflict is None for o in applied)
        assert md_of(s, "p1") == new


def test_append_after_a_bold_whose_end_char_is_dead_is_not_bolded():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "alpha beta", after="h1")
    mark(m, s, "p1", ins, 0, 9)
    apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 5], "to": [ins.id, 9]}))
    assert md_of(s, "p1") == "**alpha**"
    run_ingest(s, Mint("l", "loop"), "p1", "**alpha** gamma")
    assert md_of(s, "p1") == "**alpha** gamma"


def test_ingest_fixed_point_with_multi_word_marks_and_inner_inserts_fuzzed():
    rng = random.Random(7)
    words = ["alpha", "beta", "gamma", "delta", "once", "twice"]
    for trial in range(300):
        m, s = Mint(), Store()
        heading(m, s, "h1", "T")
        base = [rng.choice(words) for _ in range(rng.randint(2, 6))]
        ins = paragraph(m, s, "p1", " ".join(base), after="h1")
        t = s.blocks["p1"].text()
        lt = t.live_text()
        kind, attrs = rng.choice([(BOLD, {}), (LINK, {"href": "https://u"}), (CITE, {"file": "c.py", "symbol": "C"})])
        i, j = sorted(rng.sample(range(len(base)), 2))
        lo = lt.index(base[i]) if i == 0 else len(" ".join(base[:i])) + 1
        hi = len(" ".join(base[:j + 1])) - 1
        apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                      {"kind": kind, "attrs": attrs, "start": list(t.ref_at(lo)), "end": list(t.ref_at(hi)), "on": True}))
        marked = set(range(i, j + 1))
        if rng.random() < 0.4:                        # a dead tail on the mark
            apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                          {"from": list(t.ref_at(hi - len(base[j]) + 1)), "to": list(t.ref_at(hi))}))
            base_now = base[:j] + base[j + 1:]
            marked = {k for k in marked if k != j}
            marked = {k if k < j else k - 1 for k in marked}
        else:
            base_now = list(base)
        k = rng.randint(0, len(base_now))
        new_words = base_now[:k] + [rng.choice(words)] + base_now[k:]
        keep = {(w if idx < k else idx - 1) for idx, w in enumerate(range(len(new_words))) if idx != k}
        wrap = lambda w, on: (f"**{w}**" if kind == BOLD else f"[{w}](https://u)" if kind == LINK else f"[{w}](codoc:c.py#C)") if on else w
        parts = []
        for idx, w in enumerate(new_words):
            orig = idx if idx < k else idx - 1
            on = idx != k and orig in marked and rng.random() < 0.9
            parts.append(wrap(w, on))
        new_md = " ".join(parts)
        res, applied = run_ingest(s, Mint("i", "human:a"), "p1", new_md, actor="human:a")
        assert res.refused is None and all(o.conflict is None for o in applied), (trial, new_md, [o.conflict for o in applied])
        got = md_of(s, "p1")
        # Adjacent same-kind spans render merged; compare on the parsed form.
        assert parse(got) == parse(new_md), (trial, base, new_md, got)


# ── A2: plan marks are proposals ─────────────────────────────────────────────

def test_held_amend_that_unbolds_keeps_the_bold_in_the_rejected_view_and_export(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ins = [o for o in ws.store.ops if o.kind == K.TEXT_INSERT and o.payload["bid"] == "p1"][0]
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}}], actor="human:local")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")
    unbold = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries once before giving up. Mine."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=unbold)
    assert len(r.plans) == 1 and ws.store.plans[r.plans[0]].status == "open"
    assert markdown(ws.store.blocks["p1"].text(), "rejected", ws.store) == "Retries **once** before giving up. Mine."
    assert "**once**" in export_markdown(ws.store)
    assert [(c.stage, c.text) for c in plan_claims(ws.store, "h1") if c.stage in ("marked", "unmarked")] == [("unmarked", "bold:once")]
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": r.plans[0]}, "payload": {}}], actor="human:local")
    assert md_of(ws.store, "p1") == "Retries **once** before giving up. Mine."


def test_a_plan_bold_on_trunk_words_stays_out_of_the_export():
    m, s = Mint(), Store()
    heading(m, s, "b-000000000001", "T")
    ins = paragraph(m, s, "b-000000000002", "alpha beta", after="b-000000000001")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p-1"}, {"kind": "edit"}, actor="loop"))
    mark(m, s, "b-000000000002", ins, 0, 4, actor="loop", plan="p-1")
    assert "**alpha**" not in export_markdown(s)
    assert markdown(s.blocks["b-000000000002"].text(), "accepted", s) == "**alpha** beta"


# ── A3: settle while the person kept typing ──────────────────────────────────

def test_request_built_while_the_person_kept_typing_settles(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    t = ws.store.blocks["p1"].text()
    a = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Do A."}}], actor="human:local")[0]
    handoff(ws, "h1", actor="human:local", asked="do A")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [a.id, 5], "text": " And B later."}}], actor="human:local")
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=lambda *a, **k: [], under_epoch=True)
    done = settle(ws, r, sha="built")
    w = ws.store.watermarks("h1")
    assert len(done) == 1 and covered(w.handoff, w.realized) and phase(ws.store, "h1") == "edited"


# ── A4: inline markdown edges ────────────────────────────────────────────────

def test_render_parse_edges():
    for text, spans in (("*a", [Span(BOLD, {}, 1, 2)]), ("a*b", [Span(BOLD, {}, 2, 3)]),
                        ("a]b", [Span(LINK, {"href": "https://u"}, 0, 3)]), ("\\a]", [Span(CITE, {"file": "c.py", "symbol": "C"}, 1, 3)]),
                        ("**x", []), ("[a](https://b)", [Span(BOLD, {}, 0, 3)])):
        assert parse(render(text, spans)) == (text, spans), (text, spans, render(text, spans))
    rng = random.Random(5)
    alphabet = "ab*[]()#`\\ "
    for _ in range(2000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8)))
        spans = []
        if rng.random() < 0.8:
            i, j = sorted(rng.sample(range(len(text) + 1), 2)) if len(text) > 1 else (0, 1)
            kind = rng.choice([BOLD, LINK, CITE])
            spans = [Span(kind, {} if kind == BOLD else {"href": "https://u"} if kind == LINK else {"file": "c.py", "symbol": "C"}, i, j)]
        md = render(text, spans)
        assert parse(md) == (text, spans), (text, spans, md)


# ── A5 / A9: bindings die with their heading; zombie plans are not offered ───

def test_deleting_a_bound_heading_detaches_and_undo_reattaches(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    d = ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor="human:local")[0]
    assert ("c.py", "c.py::send") not in ws.store.bindings and d.payload["detached"][0]["symbol"] == "c.py::send"
    ws.submit([dict(b) for b in d.inverse], actor="human:local")
    assert ws.store.bindings[("c.py", "c.py::send")].feature == "h1"
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()


def test_accepting_a_retire_plan_detaches_and_reopen_restores(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    retire = lambda c, s, t, **kw: [{"kind": "retire_node", "feature_id": "h1", "rationale": "gone"}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=retire)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": r.plans[0]}, "payload": {}}], actor="human:local")[0]
    assert not ws.store.blocks["h1"].live and ("c.py", "c.py::send") not in ws.store.bindings
    ws.submit([dict(b) for b in acc.inverse], actor="human:local")
    assert ws.store.blocks["h1"].live and ws.store.bindings[("c.py", "c.py::send")].feature == "h1"


def test_a_plan_on_a_deleted_heading_is_not_offered(tmp_path):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    seed(d.ws)
    pid = d.ws.open_plan(actor="loop", kind="edit", target="h1")
    t = d.ws.store.blocks["p1"].text()
    d.ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Maybe."}}], actor="loop", plan=pid)
    d.ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor="human:local")
    d.process_batch([])
    st = json.loads((d.codoc_dir / "status.json").read_text())
    proj = json.loads((d.codoc_dir / "projection.json").read_text())
    assert proj["blocks"] == [] and proj["plans"] == [] and st["state"] == "synced"


# ── A6: translate leaves open plans alone ────────────────────────────────────

def test_translate_skips_a_feature_with_an_open_plan(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Maybe."}}], actor="loop", plan=pid)
    propose = lambda payload, lang, **kw: {p["id"]: ("客户端", "放弃前重试一次。") for p in payload}
    res = translate_tree(ws, tmp_path / ".codoc", language=resolve("zh-Hans"), propose=propose)
    assert res.translated == 0
    assert all(s.live for s in ws.store.blocks["p1"].text().segs if s.plan == pid)


# ── A7 / A11: the inbox is total; provisional anchors outlive a drain ────────

def test_a_malformed_ingest_submission_is_acked_and_the_next_line_applies(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    L.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="c1", kind="ingest", target={}, payload={"base_text": "", "new_md": "x"}),
        Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "ok"})])
    ws.drain_inbox()
    assert T(ws.store, "p1") == "ok"
    acks = [json.loads(l) for l in (L.inbox_dir(tmp_path / ".codoc") / "ide.ack.jsonl").read_text().splitlines()]
    assert acks[0]["cid"] == "c1" and acks[0]["conflict"].startswith("ingest target.bid")
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.drain_inbox() == []
    # A provisional anchor from a previous drain still resolves.
    L.submit(tmp_path / ".codoc", "ide", [Submission(cid="c3", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": ["$cid", "c2", 1], "text": "!"})])
    got = ws2.drain_inbox()
    assert got[0].conflict is None and T(ws2.store, "p1") == "ok!"


# ── A8: a rebuild with equal pos still publishes ─────────────────────────────

def test_rebuild_with_equal_pos_publishes_fresh_files(tmp_path):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    seed(d.ws)
    d.process_batch([])
    other = tmp_path / "other" / ".codoc"
    ws = Workspace(other)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": "heading", "level": 1, "after": START}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "Server"}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": "h1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Listens forever."}},
               {"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "s.py", "symbol": "s.py::serve"}}], actor="loop")
    ws.close()
    d.ws.close()
    shutil.rmtree(d.codoc_dir / "log")
    shutil.copytree(other / "log", d.codoc_dir / "log")
    d.state.head = "other-branch"
    d.process_batch([])
    assert T(d.ws.store, "p1") == "Listens forever." and "Listens forever." in (d.codoc_dir / "tree.codoc").read_text()


# ── A10: roles live in the tracked config ────────────────────────────────────

def test_roles_from_config_make_every_clone_agree(tmp_path):
    from codoc.doclang import write_config
    cd = tmp_path / ".codoc"
    cd.mkdir()
    write_config(cd, roles={"human:alice": "maintainer"})
    ws = Workspace(cd)
    seed(ws)
    pid = ws.open_plan(actor="loop")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "P "}}], actor="loop", plan=pid)
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:alice")[0].conflict is None
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:bob")[0].conflict == "authority"
    ws.close()
    ws2 = Workspace(cd)
    assert ws2.roles.role("human:alice") == "maintainer" and ws2.store.state_fingerprint() == ws.store.state_fingerprint()
    assert Roles.load(cd).role("human:local") == "owner"


# ── A12: a torn tail keeps the prefix ────────────────────────────────────────

def test_torn_last_line_keeps_the_prefix_and_the_daemon_cuts_it(tmp_path):
    root = tmp_path / "repo"
    cd = root / ".codoc"
    cd.mkdir(parents=True)
    ws = Workspace(cd)
    seed(ws)
    ws.close()
    p = L.list_sessions(cd)[0]
    p.write_bytes(p.read_bytes() + b'{"id": "' + ws.session.encode() + b':6", "at": "0000')
    ws2 = Workspace(cd)
    assert "h1" in ws2.store.blocks and len(ws2.problems) == 1 and ws2.torn
    ws2.close()
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    assert L.read_session(p).valid and L.read_session(p).torn_tail is None and "h1" in d.ws.store.blocks
    d.ws.close()


# ── A13 / A14 / A15 / A16 ────────────────────────────────────────────────────

def test_verdicts_are_in_the_features_history(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "P "}}], actor="loop", plan=pid)
    bad = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="agent:x")[0]
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:local")[0]
    h = history(ws.store, "h1")
    assert bad.conflict == "authority" and bad in h and acc in h


def test_an_undo_that_kills_nothing_is_indexed_the_same_after_rebuild(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ins = ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}},
                     {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "abc"}}], actor="human:local")[1]
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"}, "payload": {"from": [ins.id, 0], "to": [ins.id, 2]}}], actor="loop")
    ws.submit([dict(b) for b in ins.inverse], actor="human:local")
    live = {k: list(v) for k, v in ws.store.ops_by_block.items()}
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert {k: list(v) for k, v in ws2.store.ops_by_block.items()} == live


def test_plan_reject_restores_a_binding_the_plan_only_refreshed():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C", "fingerprint": "f1"}))
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p-1"}, {"kind": "edit", "target": "h1"}, actor="agent:x"))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C", "fingerprint": "f2"}, actor="agent:x", plan="p-1"))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "p-1"}, {}))
    assert s.bindings[("c.py", "C")].fingerprint == "f1"


def test_merged_link_keeps_the_existing_placer_even_when_it_also_displaces():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "read the client docs", after="h1")
    human = mark(m, s, "p1", ins, 0, 3, LINK, {"href": "https://u"})
    mark(m, s, "p1", ins, 9, 14, CITE, {"file": "c.py", "symbol": "C"})
    mark(m, s, "p1", ins, 4, 14, LINK, {"href": "https://u"}, actor="loop")
    links = [mk for mk in s.blocks["p1"].text().marks if mk.kind == LINK]
    assert [(mk.actor, mk.op) for mk in links] == [("human:a", human.id)]


# ── review 4: the window, the inbox and the hand-off ─────────────────────────

def test_window_unapplies_verdicts_and_omits_blocks_born_inside_it():
    from codoc.calculus.text import SUGGEST_DEL
    from codoc.calculus.window import history_window
    from tests.calculus.test_window import texts_after_each_op
    m, s = Mint(), Store()
    heading(m, s, "h1", "T")
    ins = paragraph(m, s, "p1", "keep this maybe", after="h1")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit", "target": "h1"}, actor="loop"))
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                  {"kind": SUGGEST_DEL, "attrs": {}, "start": [ins.id, 5], "end": [ins.id, 9], "on": True}, actor="loop", plan="pl"))
    apply(s, m.op(K.PLAN_ACCEPT, {"pid": "pl"}, {}))
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p2"}, {"kind": "edit", "target": "h1"}, actor="loop"))
    pi = apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 14], "text": " later"}, actor="loop", plan="p2"))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "p2"}, {}))
    truth = texts_after_each_op(s)
    w = history_window(s, limit=500)
    for e in w["ops"]:
        for bid, text in e["blocks"].items():
            assert text == truth[e["pos"]][bid], (e["kind"], bid, text, truth[e["pos"]][bid])
    assert next(e for e in w["ops"] if e["id"] == pi.id)["blocks"]["p1"] == "keep maybe later"
    short = history_window(s, limit=2)
    assert "p1" in short["before"]
    m2, s2 = Mint(), Store()
    heading(m2, s2, "h1", "T")
    paragraph(m2, s2, "p1", "one", after="h1")
    assert "p1" not in history_window(s2, limit=2)["before"]     # born inside the window


def test_a_clients_own_scalar_index_survives_normalisation(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    L.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "é"}),
        Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": ["$cid", "c1", 1], "text": "x"})])
    ops = ws.drain_inbox()
    assert [o.conflict for o in ops] == [None, None]
    assert T(ws.store, "p1") == "éx"


def test_a_handoff_covers_what_arrived_with_it(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    last = ws.store.blocks["p1"].text().last_live_ref()
    L.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                   payload={"after": list(last), "text": " Twice."}, batch="t1"),
        Submission(cid="c2", kind=K.FEATURE_HANDOFF, target={"fid": "h1"}, payload={"asked": "twice"}, batch="t2")])
    ws.drain_inbox()
    assert phase(ws.store, "h1") == "queued" and "Twice." in directive(ws.store, "h1")


def test_a_later_demotion_does_not_rewrite_what_was_written(tmp_path):
    from codoc.doclang import write_config
    cd = tmp_path / ".codoc"
    cd.mkdir()
    write_config(cd, roles={"human:alice": "maintainer"})
    ws = Workspace(cd)
    seed(ws)
    pid = ws.open_plan(actor="loop")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "P "}}], actor="loop", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:alice")[0]
    bad = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:bob")[0]
    assert acc.conflict is None and bad.conflict == "authority"
    fp = ws.store.state_fingerprint()
    ws.close()
    write_config(cd, roles={"human:alice": "suggest"})       # alice's access is reduced later
    ws2 = Workspace(cd)
    assert ws2.store.by_id[acc.id].conflict is None and ws2.store.by_id[bad.id].conflict == "authority"
    assert ws2.store.state_fingerprint() == fp
    # …and the new table is what judges the next op she writes.
    assert ws2.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:alice")[0].conflict == "authority"


def test_the_page_keeps_a_mark_a_plan_proposes_to_remove(tmp_path):
    """A proposed unbold leaves the word bold and says so beside it, the way a
    proposed deletion leaves the words struck rather than gone."""
    from codoc.calculus.derive import block_entry, plan_claims
    from codoc.calculus.text import BOLD
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ins = [o for o in ws.store.ops if o.kind == K.TEXT_INSERT and o.payload["bid"] == "p1"][0]
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}}], actor="human:local")
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": False}}], actor="loop", plan=pid)
    spans = block_entry(ws.store, ws.store.blocks["p1"])["fields"]["text"]["spans"]
    assert [(s["kind"], s["start"], s["end"]) for s in spans] == [(BOLD, 8, 12)]
    assert [(c.stage, c.text) for c in plan_claims(ws.store, "h1") if c.stage == "unmarked"] == [("unmarked", "bold:once")]
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
    assert block_entry(ws.store, ws.store.blocks["p1"])["fields"]["text"]["spans"] == []
