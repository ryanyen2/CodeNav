"""Review-3 probes. Every test asserts the CORRECT behaviour, so a failure is a
confirmed fault. Run from the substrate worktree:

    ../CodeNav/.venv/bin/python -m pytest <this file> -q -p no:cacheprovider
"""
import json
import random
import shutil
import time
from pathlib import Path

import pytest

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import (_drifted, directive, ground, history, ink, markdown, open_plans_on, phase,
                                   plan_claims, projection)
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.export import export_markdown, import_markdown
from codoc.calculus.ids import START
from codoc.calculus.ingest import ingest
from codoc.calculus.inline_md import Span, parse, render
from codoc.calculus.ops import Op, covered
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, CITE, LINK, Mark
from codoc.graph.graph import Graph
from codoc.loop.diff import ChangeSet
from codoc.loops.realize import handoff, settle
from codoc.loops.reflect import reflect
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


def mark(m, s, bid, ins, lo, hi, kind=BOLD, attrs=None, on=True, actor=None):
    return apply(s, m.op(K.TEXT_MARK, {"bid": bid, "path": "text"},
                         {"kind": kind, "attrs": attrs or {}, "start": [ins.id, lo], "end": [ins.id, hi], "on": on}, actor=actor))


# ── A. ingest is not a fixed point when an insert lands INSIDE a mark ─────────

def test_P1_insert_inside_a_live_bold_is_not_unbolded():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "alpha beta", after="h1")
    mark(m, s, "p1", ins, 0, 9)                                  # **alpha beta**
    res, applied = run_ingest(s, Mint("h", "human:a"), "p1", "**alpha** gamma **beta**", actor="human:a")
    assert res.refused is None and all(o.conflict is None for o in applied)
    assert md_of(s, "p1") == "**alpha** gamma **beta**"


def test_P1b_insert_inside_a_live_link_is_not_unlinked():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "alpha beta", after="h1")
    mark(m, s, "p1", ins, 0, 9, LINK, {"href": "https://u"})
    res, applied = run_ingest(s, Mint("h", "human:a"), "p1", "[alpha](https://u) gamma [beta](https://u)", actor="human:a")
    assert res.refused is None and all(o.conflict is None for o in applied)
    assert md_of(s, "p1") == "[alpha](https://u) gamma [beta](https://u)"


def test_P1c_append_after_a_bold_whose_end_char_is_dead_is_bolded():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "alpha beta", after="h1")
    mark(m, s, "p1", ins, 0, 9)                                  # **alpha beta**
    apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 5], "to": [ins.id, 9]}))
    assert md_of(s, "p1") == "**alpha**"
    res, applied = run_ingest(s, Mint("l", "loop"), "p1", "**alpha** gamma")
    assert res.refused is None and all(o.conflict is None for o in applied)
    assert md_of(s, "p1") == "**alpha** gamma"


def test_P1d_fuzz_multiword_marks_with_inner_inserts():
    rng = random.Random(7)
    words = ["alpha", "beta", "gamma", "delta", "once", "twice"]
    bad = []
    for trial in range(300):
        m, s = Mint(), Store()
        heading(m, s, "h1", "T")
        base = [rng.choice(words) for _ in range(rng.randint(2, 6))]
        ins = paragraph(m, s, "p1", " ".join(base), after="h1")
        t = s.blocks["p1"].text()
        lt = t.live_text()
        # one multi-word mark of a random kind
        i, j = sorted(rng.sample(range(len(base)), 2))
        lo = len(" ".join(base[:i])) + (1 if i else 0)
        hi = len(" ".join(base[:j + 1])) - 1
        kind, attrs = rng.choice([(BOLD, {}), (LINK, {"href": "https://u"}), (CITE, {"file": "c.py", "symbol": "C"})])
        apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                      {"kind": kind, "attrs": attrs, "start": list(t.ref_at(lo)), "end": list(t.ref_at(hi)), "on": True}))
        # sometimes kill the tail of the marked span first (dead mark end)
        if rng.random() < 0.3 and hi > lo + 2:
            apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": list(t.ref_at(hi - 1)), "to": list(t.ref_at(hi))}))
        # new text: same words with one new word inserted somewhere, marks re-drawn per word at random
        cur_md = md_of(s, "p1")
        plain, spans = parse(cur_md)
        toks = plain.split(" ")
        k = rng.randrange(len(toks) + 1)
        toks.insert(k, rng.choice(words))
        # keep existing per-word marking by re-deriving from the old markdown per word
        old_marked = {}
        for sp in spans:
            for w_i, w in enumerate(plain.split(" ")):
                off = len(" ".join(plain.split(" ")[:w_i])) + (1 if w_i else 0)
                if sp.start <= off < sp.end:
                    old_marked.setdefault(w_i, []).append(sp)
        parts = []
        for w_i, w in enumerate(toks):
            src = w_i if w_i < k else w_i - 1
            if w_i == k:
                parts.append(w)      # the new word is plain
                continue
            sps = old_marked.get(src, [])
            txt = w
            for sp in sps:
                if sp.kind == BOLD:
                    txt = f"**{txt}**"
                elif sp.kind == LINK:
                    txt = f"[{txt}]({sp.attrs['href']})"
                else:
                    txt = f"[{txt}](codoc:{sp.attrs['file']}#{sp.attrs['symbol']})"
            parts.append(txt)
        new_md = " ".join(parts)
        res, applied = run_ingest(s, Mint("h", "human:a"), "p1", new_md, actor="human:a")
        if res.refused:
            continue
        got = md_of(s, "p1")
        if got != new_md:
            bad.append((trial, cur_md, new_md, got))
    assert bad == [], bad[:5]


# ── B. inline_md render/parse is not a fixed point on literal stars next to bold, or ] in labels ──

def test_P2a_literal_star_before_a_bold():
    text, spans = "*a", [Span(BOLD, {}, 1, 2)]
    assert parse(render(text, spans)) == (text, spans)


def test_P2b_literal_star_before_a_bold_mid_text():
    text, spans = "a*b", [Span(BOLD, {}, 2, 3)]
    assert parse(render(text, spans)) == (text, spans)


def test_P2c_bracket_inside_a_link_label():
    text, spans = "a]b", [Span(LINK, {"href": "https://u"}, 0, 3)]
    assert parse(render(text, spans)) == (text, spans)


def test_P2d_fuzz_render_parse():
    rng = random.Random(5)
    alphabet = "ab*[]()#`\\ "
    bad = []
    for trial in range(2000):
        n = rng.randint(1, 8)
        text = "".join(rng.choice(alphabet) for _ in range(n))
        spans = []
        if rng.random() < 0.7:
            i, j = sorted(rng.sample(range(n + 1), 2)) if n else (0, 1)
            kind = rng.choice([BOLD, LINK, CITE])
            attrs = {} if kind == BOLD else ({"href": "https://u"} if kind == LINK else {"file": "c.py", "symbol": "C"})
            spans.append(Span(kind, attrs, i, j))
        try:
            got = parse(render(text, spans))
        except Exception as e:  # noqa: BLE001
            bad.append((trial, text, spans, repr(e)))
            continue
        if got != (text, spans):
            bad.append((trial, text, spans, render(text, spans), got))
    assert bad == [], (len(bad), bad[:8])


# ── C. a plan-tagged mark change is applied to the document, not proposed ───

def test_P3_held_amend_drops_a_human_bold_from_the_live_doc_and_the_export(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    t = ws.store.blocks["p1"].text()
    ins = [o for o in ws.store.ops if o.kind == K.TEXT_INSERT and o.payload["bid"] == "p1"][0]
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}}], actor="human:local")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")   # unsent ink → held
    assert md_of(ws.store, "p1") == "Retries **once** before giving up. Mine."
    unbold = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries once before giving up. Mine."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=unbold)
    assert len(r.plans) == 1 and ws.store.plans[r.plans[0]].status == "open"
    # A proposal must not change what the person sees or what git holds until a verdict.
    assert md_of(ws.store, "p1") == "Retries **once** before giving up. Mine."
    assert "**once**" in export_markdown(ws.store)


def test_P3b_a_plan_bold_on_trunk_words_enters_the_export():
    m, s = Mint(), Store()
    heading(m, s, "b-000000000001", "T")
    ins = paragraph(m, s, "b-000000000002", "alpha beta", after="b-000000000001")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p-1"}, {"kind": "edit"}, actor="loop"))
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"},
                  {"kind": BOLD, "attrs": {}, "start": [ins.id, 0], "end": [ins.id, 4], "on": True}, actor="loop", plan="p-1"))
    assert "**alpha**" not in export_markdown(s)           # rejected read mode: the plan is not the document


# ── D. settlement skipped when the person kept typing during the build ──────

def test_P4_request_built_while_the_person_kept_typing_never_settles(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    t = ws.store.blocks["p1"].text()
    a = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                    "payload": {"after": list(t.last_live_ref()), "text": " Do A."}}], actor="human:local")[0]
    handoff(ws, "h1", actor="human:local", asked="do A")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": [a.id, 5], "text": " And B later."}}], actor="human:local")     # unsent, typed during the build
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=lambda *a, **k: [],
                under_epoch=True)
    done = settle(ws, r, sha="built")
    w = ws.store.watermarks("h1")
    assert len(done) == 1 and covered(w.handoff, w.realized)          # A was built; " And B later." stays unsent
    assert phase(ws.store, "h1") == "edited"


# ── E. I6 depends on an input the log does not carry: the roles table ───────

def test_P5_replay_with_a_different_roles_table_changes_the_store(tmp_path):
    ws = Workspace(tmp_path / ".codoc", roles=Roles({"human:local": "owner", "human:alice": "maintainer"}))
    seed(ws)
    pid = ws.open_plan(actor="loop")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Proposed."}}], actor="loop", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:alice")[0]
    assert acc.conflict is None
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")                               # a clone with the shipped default table
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()


# ── F. ops_by_block: live vs rebuilt, and vs brute force ────────────────────

def test_P6a_an_undo_that_kills_nothing_is_indexed_live_but_not_after_rebuild(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ins = ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}},
                     {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "abc"}}],
                    actor="human:local")[1]
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"}, "payload": {"from": [ins.id, 0], "to": [ins.id, 2]}}],
              actor="loop")                                            # someone else deleted the words
    undo = ws.submit([dict(b) for b in ins.inverse], actor="human:local")[0]   # the person's undo of their insert: kills nothing
    assert undo.conflict is None and undo.payload["segments"] == []
    live_index = {k: list(v) for k, v in ws.store.ops_by_block.items()}
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert {k: list(v) for k, v in ws2.store.ops_by_block.items()} == live_index
    assert [o.id for o in history(ws2.store, "p1")] == [o.id for o in history(ws.store, "p1")]


def _brute_history(store: Store, fid: str) -> list[str]:
    """Ops whose canonical block set (the blocks apply touched) meets the body."""
    ids = set(store.body_ids(fid))
    out = []
    for o in store.ops:
        if o.conflict:
            hint = o.target.get("bid") or o.target.get("fid") or o.target.get("into")
            if hint in ids:
                out.append(o.id)
            continue
        if o.id in TOUCHED and TOUCHED[o.id] & ids:
            out.append(o.id)
    return out


TOUCHED: dict = {}


def test_P6b_views_agree_with_brute_force_over_a_random_log(tmp_path, monkeypatch):
    import codoc.calculus.store as S
    real = S.Store.record

    def rec(self, op, touched, structural):
        TOUCHED[op.id] = set(touched)
        return real(self, op, touched, structural)
    monkeypatch.setattr(S.Store, "record", rec)
    from tests.substrate.test_review2 import _random_body
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    rng = random.Random(9)
    for step in range(300):
        body = _random_body(rng, ws.store, step)
        if body["kind"] == K.TEXT_MARK and body["payload"]["kind"] == LINK and not body["payload"]["attrs"]:
            body["payload"]["attrs"] = {"href": "https://y"}
        got = ws.submit([body], actor=rng.choice(["human:local", "loop"]))[0]
        if got.conflict is None and got.inverse and rng.random() < 0.2:
            ws.submit([dict(b) for b in got.inverse], actor="human:local")
        if step % 37 == 0:
            ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": f"f{step}"}}], actor="loop")
        if step % 53 == 0:
            handoff(ws, "h1", actor="human:local")
    for fid in [h.id for h in ws.store.headings()]:
        assert [o.id for o in history(ws.store, fid)] == _brute_history(ws.store, fid), fid
    # and the index survives a rebuild
    live_index = {k: list(v) for k, v in ws.store.ops_by_block.items()}
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert {k: list(v) for k, v in ws2.store.ops_by_block.items()} == live_index


def test_P6c_an_accept_that_only_confirms_inserts_is_in_nobodys_history(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Proposed."}}], actor="loop", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:local")[0]
    assert acc.conflict is None
    assert acc in history(ws.store, "h1")


# ── G. a torn trailing line drops the whole session ─────────────────────────

def test_P7_torn_last_line_loses_every_op_of_the_session(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.close()
    p = L.list_sessions(tmp_path / ".codoc")[0]
    data = p.read_bytes()
    p.write_bytes(data + b'{"id": "' + ws.session.encode() + b':6", "at": "0000')     # a crash mid-write of op 6
    ws2 = Workspace(tmp_path / ".codoc")
    assert "h1" in ws2.store.blocks                                    # the five complete ops must survive
    assert ws2.problems and "unreadable" in ws2.problems[0].reason


# ── H. provisional anchors do not survive a drain boundary ──────────────────

def test_P8_provisional_anchor_across_two_drains(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    L.submit(tmp_path / ".codoc", "ide", [Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                                     payload={"after": START, "text": "ab"})])
    assert ws.drain_inbox({"ide": "human:local"})[0].conflict is None
    L.submit(tmp_path / ".codoc", "ide", [Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                                     payload={"after": ["$cid", "c1", 1], "text": "c"})])
    got = ws.drain_inbox({"ide": "human:local"})[0]
    assert got.conflict is None and T(ws.store, "p1") == "abc"


# ── I. an ingest submission without a bid poisons the inbox ─────────────────

def test_P9_ingest_submission_without_bid_poisons_every_later_line(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    L.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="c1", kind="ingest", target={}, payload={"base_text": "", "new_md": "x"}),
        Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "ok"}),
    ])
    ws.drain_inbox({"ide": "human:local"})                             # must not raise
    assert T(ws.store, "p1") == "ok"
    acks = [json.loads(l)["cid"] for l in (L.inbox_dir(tmp_path / ".codoc") / "ide.ack.jsonl").read_text().splitlines()]
    assert acks == ["c1", "c2"]


# ── J. publish-skip key misses a rebuild that changed content but not pos ───

def test_P10_rebuild_with_equal_pos_publishes_stale_files(tmp_path):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    seed(d.ws)
    d.process_batch([])
    assert "Retries once" in json.loads((d.codoc_dir / "projection.json").read_text())["blocks"][1]["fields"]["text"]["text"]
    # A checkout swaps the log set for another of the same length (a branch with a different tree).
    other = tmp_path / "other" / ".codoc"
    ws = Workspace(other)
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": HEADING, "level": 1, "after": START}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "Server"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": "h1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Listens forever."}},
        {"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "s.py", "symbol": "s.py::run"}},
    ], actor="loop")
    ws.close()
    d.ws.close()
    shutil.rmtree(d.codoc_dir / "log")
    shutil.copytree(other / "log", d.codoc_dir / "log")
    d.state.head = "other-branch"
    d.process_batch([])
    assert T(d.ws.store, "p1") == "Listens forever."
    proj = json.loads((d.codoc_dir / "projection.json").read_text())
    assert "Listens forever." in proj["blocks"][1]["fields"]["text"]["text"]
    assert "Listens forever." in (d.codoc_dir / "tree.codoc").read_text()


# ── K. rejecting a plan whose attach re-bound the feature's own symbol ──────

def test_P11_plan_reject_deletes_a_binding_the_plan_only_refreshed():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C", "fingerprint": "f1"}))
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p-1"}, {"kind": "edit", "target": "h1"}, actor="agent:x"))
    at = apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C", "fingerprint": "f2"}, actor="agent:x", plan="p-1"))
    assert at.conflict is None and at.payload["displaced"] is None and at.payload["prev_fingerprint"] == "f1"
    apply(s, m.op(K.PLAN_REJECT, {"pid": "p-1"}, {}))
    assert ("c.py", "C") in s.bindings and s.bindings[("c.py", "C")].fingerprint == "f1"


# ── L. a heading deleted while a plan is open on it: a zombie proposal ──────

def test_P15_deleting_a_heading_under_an_open_plan_leaves_a_zombie_proposal(tmp_path):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    seed(d.ws)
    pid = d.ws.open_plan(actor="loop", kind="edit", target="h1")
    t = d.ws.store.blocks["p1"].text()
    d.ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                  "payload": {"after": list(t.last_live_ref()), "text": " Maybe."}}], actor="loop", plan=pid)
    d.ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor="human:local")
    d.process_batch([])
    st = json.loads((d.codoc_dir / "status.json").read_text())
    proj = json.loads((d.codoc_dir / "projection.json").read_text())
    assert proj["blocks"] == []                                        # nothing to see
    assert st["state"] != "proposed", st                               # …yet the status asks for a verdict on it
    assert all(p["id"] != pid for p in proj["plans"])


# ── M. a translator run over a feature with an open plan kills the plan's words on the trunk ──

def test_P17_translate_tombstones_an_open_plans_words(tmp_path):
    from codoc.doclang import DocLanguage, profile
    from codoc.loops.translate import translate
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Maybe."}}], actor="loop", plan=pid)
    lang = profile("zh-Hans")
    propose = lambda payload, lang, **kw: {p["id"]: ("客户端", "放弃前重试一次。") for p in payload}
    translate(ws, lang, propose=propose, say=lambda *_: None)
    plan_words = [s for s in ws.store.blocks["p1"].text().segs if s.plan == pid]
    assert plan_words and all(s.live for s in plan_words), [(s.chars, s.dead) for s in plan_words]
    assert ws.store.plans[pid].status == "open"


# ── N. set_mark: the merged mark loses its first placer when an exclusive displacement precedes ──

def test_P13_merged_link_keeps_the_existing_placer_even_when_it_also_displaces():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "alpha beta gamma", after="h1")
    human = mark(m, s, "p1", ins, 0, 4, LINK, {"href": "https://u"}, actor="human:a")          # [alpha](u)
    mark(m, s, "p1", ins, 11, 15, CITE, {"file": "c.py", "symbol": "C"}, actor="human:a")     # [gamma](codoc:)
    ext = mark(m, s, "p1", ins, 5, 15, LINK, {"href": "https://u"}, actor="loop")             # loop widens u over beta gamma
    links = [mk for mk in s.blocks["p1"].text().marks if mk.kind == LINK]
    assert [(mk.actor, mk.op) for mk in links] == [("human:a", human.id)]
