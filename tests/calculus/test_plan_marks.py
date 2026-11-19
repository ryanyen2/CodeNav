"""A plan's mark changes are proposals: `suggest_mark` records beside the
document's own marks, applied on accept and removed on reject."""
import json
import random

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import block_entry, live_spans, markdown, plan_claims
from codoc.calculus.derive import export_markdown
from codoc.calculus.inline_md import parse
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, CITE, COMMENT, LINK, PROPOSALS, SUGGEST_DEL
from codoc.workspace import Workspace
from tests.helpers import (
    H,
    Mint,
    T,
    apply_mark,
    bold_chars,
    heading,
    ins,
    mark,
    paragraph,
    refs,
    seed,
    seed_loop,
)


def test_two_plans_on_one_span_rejected_in_order_leave_no_phantom_mark(tmp_path):
    """Plan A bolds a span; plan B (another proposal) unbolds it. Reject A, then
    B: the document had no bold before either plan, so it has none after."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], actor="loop", plan=a)
    b = ws.open_plan(actor="agent:x", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], on=False, actor="agent:x", plan=b)
    assert not bold_chars(ws.store.blocks["p1"].text())
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": b}, "payload": {}}], actor=H)
    t = ws.store.blocks["p1"].text()
    assert not bold_chars(t), [m.to_json() for m in t.marks]
    assert "**" not in markdown(t, "rejected", ws.store)


def test_rejected_read_mode_does_not_resurrect_a_rejected_plans_mark(tmp_path):
    """A rejected, B open. The export (rejected mode) is the document without
    any open plan, which never held A's bold."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], actor="loop", plan=a)
    b = ws.open_plan(actor="agent:x", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], on=False, actor="agent:x", plan=b)
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)
    t = ws.store.blocks["p1"].text()
    assert not bold_chars(t)
    assert "**" not in markdown(t, "rejected", ws.store), markdown(t, "rejected", ws.store)
    e = block_entry(ws.store, ws.store.blocks["p1"])
    assert not [s for s in e["fields"]["text"]["spans"] if s["kind"] == BOLD]


def test_a_proposed_bold_does_not_grow_over_what_the_person_types_at_its_edge(tmp_path):
    """I2's growth is a rule about the document's own marks. A proposal is a
    record beside them, so typing at its edge does not silently widen what the
    person is being asked to answer — and accepting it afterwards bolds what was
    proposed, not what was proposed plus what they typed."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], actor="loop", plan=a)
    typed = ins(ws, "p1", list(r[6]), "!!")
    t = ws.store.blocks["p1"].text()
    assert not bold_chars(t), "a proposal is not a mark on the document"
    assert "**" not in markdown(t, "rejected", ws.store), markdown(t, "rejected", ws.store)
    claims = [c for c in plan_claims(ws.store, "h1") if c.stage == "marked"]
    assert [c.text for c in claims] == ["bold:Retries"], claims
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": a}, "payload": {}}], actor=H)
    t = ws.store.blocks["p1"].text()
    assert bold_chars(t) == set(r[0:7]) and (typed.id, 0) not in bold_chars(t)
    assert markdown(t) == "**Retries**!! once."


def test_rejecting_a_proposed_bold_leaves_the_document_untouched(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], actor="loop", plan=a)
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)
    t = ws.store.blocks["p1"].text()
    assert not bold_chars(t) and not t.proposals(), [m.to_json() for m in t.marks]


def test_projection_spans_agree_with_rejected_markdown_by_character_identity(tmp_path):
    """The editor's spans (block_entry) and the export (markdown rejected) are
    two renderings of one set of marks. Fuzz: random person/plan toggles,
    inserts and verdicts; compare the marked character identities."""
    rng = random.Random(11)
    for seed_n in range(12):
        rng.seed(seed_n)
        ws = Workspace(tmp_path / f"w{seed_n}" / ".codoc"); seed(ws)
        plans: list[str] = []
        for step in range(30):
            t = ws.store.blocks["p1"].text()
            live = list(t.live_refs())
            r = rng.random()
            if r < 0.25 and len(live) > 3:
                i, j = sorted(rng.sample(range(len(live)), 2))
                kind = rng.choice([BOLD, BOLD, LINK, CITE])
                attrs = {"href": "https://x"} if kind == LINK else {"symbol": "s"} if kind == CITE else {}
                plan = rng.choice(plans + [None, None]) if plans else None
                mark(ws, "p1", kind, live[i], live[j], on=rng.random() < 0.7, actor="loop" if plan else H, plan=plan, attrs=attrs)
            elif r < 0.45 and live:
                plan = rng.choice(plans + [None]) if plans else None
                ins(ws, "p1", list(rng.choice(live)), f" w{step}", actor="loop" if plan else H, plan=plan)
            elif r < 0.6:
                plans.append(ws.open_plan(actor="loop", kind="edit", target="p1"))
            elif r < 0.7 and plans:
                pid = plans.pop(rng.randrange(len(plans)))
                ws.submit([{"kind": K.PLAN_ACCEPT if rng.random() < 0.5 else K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)
            elif len(live) > 4:
                i, j = sorted(rng.sample(range(len(live)), 2))
                ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"}, "payload": {"from": list(live[i]), "to": list(live[j])}}], actor=H)
            # compare
            t = ws.store.blocks["p1"].text()
            e = block_entry(ws.store, ws.store.blocks["p1"])
            offs = {i: ref for ref, i in t.live_offsets().items()}
            proj = {(s["kind"], json.dumps(s["attrs"], sort_keys=True), offs[i]) for s in e["fields"]["text"]["spans"] for i in range(s["start"], s["end"])}
            # rejected mode over identities: recompute on a clone the way markdown() does
            view = t.clone()
            open_plans = {p.id for p in ws.store.plans.values() if p.status == "open"}
            for s in view.segs:
                if s.live and s.plan in open_plans:
                    s.dead = "view"
            rej = set()
            for m in view.marks:
                if m.kind in PROPOSALS:
                    continue
                for ref in view.mark_refs(m):
                    rej.add((m.kind, json.dumps(m.attrs, sort_keys=True), ref))
            # the projection's spans cover the live text (plan words included); restrict both to rejected-live chars
            rej_live = set(view.live_refs())
            assert {p for p in proj if p[2] in rej_live} == rej, (seed_n, step)
        ws.close()


def marks_of_kind(text, kind):
    return sorted(((m.start, m.end, m.op) for m in text.marks if m.kind == kind))


def test_a_plan_bold_that_widens_a_human_bold_is_undone_by_reject(tmp_path):
    """A proposal is a record beside the document, so a plan bold that widens
    the person's bold leaves it exactly as it was until a verdict. Reject keeps
    it; accept widens it to one mark over the whole range."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])                                  # the person bolds "Retries"
    before = bold_chars(ws.store.blocks["p1"].text())
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[11], actor="loop", plan=a)            # the plan widens it over "Retries once"
    assert bold_chars(ws.store.blocks["p1"].text()) == before, "a proposal did not wait for its verdict"
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None
    t = ws.store.blocks["p1"].text()
    assert bold_chars(t) == before, marks_of_kind(t, BOLD)
    assert markdown(t) == "**Retries** once."
    # …and the same widening, accepted, is one mark over the whole range.
    ws2 = Workspace(tmp_path / "b" / ".codoc"); seed(ws2)
    r2 = refs(ws2, "p1")
    mark(ws2, "p1", BOLD, r2[0], r2[6])
    b = ws2.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws2, "p1", BOLD, r2[0], r2[11], actor="loop", plan=b)
    ws2.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": b}, "payload": {}}], actor=H)
    assert markdown(ws2.store.blocks["p1"].text()) == "**Retries once**."
    ws2.close()


def test_rejected_read_mode_hides_a_plan_bold_that_widened_a_human_bold(tmp_path):
    """The `rejected` read mode is the document without any open plan."""
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


def test_two_open_plans_with_adjacent_bolds_each_reject_cleanly(tmp_path):
    """Two open plans propose adjacent bolds. Each is its own record, so
    rejecting B leaves the document's bold marks as they were with A open."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], actor="loop", plan=a)             # "Retries"
    only_a = bold_chars(ws.store.blocks["p1"].text())
    b = ws.open_plan(actor="agent:x", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[7], r[11], actor="agent:x", plan=b)         # " once", adjacent to "Retries"
    merged = bold_chars(ws.store.blocks["p1"].text())
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": b}, "payload": {}}], actor=H)
    t = ws.store.blocks["p1"].text()
    assert bold_chars(t) == only_a, (marks_of_kind(t, BOLD), merged)


def test_a_plan_cite_that_displaced_a_human_link_is_undone_whole_by_reject(tmp_path):
    """A plan cite inside a person's link would displace the link if
    accepted. While the plan is open the link is untouched, and after a reject
    the marks are exactly what they were and the Markdown round-trips."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", LINK, r[0], r[11], attrs={"href": "https://x.test/"})     # [Retries once](…)
    before = [m.to_json() for m in ws.store.blocks["p1"].text().marks]
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", CITE, r[3], r[5], actor="loop", plan=a, attrs={"symbol": "send"})
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None
    t = ws.store.blocks["p1"].text()
    assert [m.to_json() for m in t.marks] == before, [m.to_json() for m in t.marks]
    md = markdown(t)
    assert parse(md) == (t.live_text(), live_spans(t)) or md == "[Retries once](https://x.test/).", md


def test_rejected_read_mode_while_the_displacing_plan_is_open(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", LINK, r[0], r[11], attrs={"href": "https://x.test/"})
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", CITE, r[3], r[5], actor="loop", plan=a, attrs={"symbol": "send"})
    t = ws.store.blocks["p1"].text()
    assert markdown(t, "rejected", ws.store) == "[Retries once](https://x.test/).", markdown(t, "rejected", ws.store)


def test_a_plan_that_unbolds_the_middle_of_a_bold_is_undone_by_reject(tmp_path):
    """A plan that proposes unbolding the middle of a bold leaves the bold
    whole while it is open, in the export, and after a reject."""
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


def test_a_plan_bold_that_widened_a_human_bold_is_undone_after_the_person_split_the_paragraph(tmp_path):
    """A plan bold widens the person's bold, then the person presses Enter
    inside the widened part. After a reject the tail block has no bold and the
    head keeps exactly the person's bold."""
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
    assert markdown(tail) == "nce.", (markdown(tail), marks_of_kind(tail, BOLD))
    assert bold_chars(head) == set(r[0:7]), (markdown(head), marks_of_kind(head, BOLD))


def test_the_rejected_read_mode_agrees_after_the_split_while_the_plan_is_open(tmp_path):
    """The `rejected` read mode is the document without any open plan, and the
    export is rendered from it. After the split, the tail block shows no bold
    in that mode or in the projection."""
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


def test_rejecting_one_of_two_plans_that_bold_the_same_words_keeps_the_other_proposal(tmp_path):
    """Two plans propose the same bold. Rejecting A leaves B open, and
    accepting B bolds the words."""
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


def test_accepting_a_strike_kills_what_it_proposed_and_not_what_was_typed_inside(tmp_path):
    """A `suggest_del` records the characters it strikes when it is placed,
    and the accept kills exactly those, so a word the person typed inside the
    struck range survives."""
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    r = refs(ws, "p1")
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", SUGGEST_DEL, r[8], r[11], actor="loop", plan=pid)     # "once"
    ins(ws, "p1", list(r[9]), "X", actor=H)                              # the person answers inside it
    assert T(ws.store, "p1") == "Retries onXce."
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)
    assert T(ws.store, "p1") == "Retries X."


def test_a_verdict_on_a_mark_the_person_already_removed_leaves_the_plan_decided(tmp_path):
    """A proposal to turn off a whole mark the person already removed is
    satisfied: the accept applies, its strike lands, and the plan is decided."""
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    r = refs(ws, "p1")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "c1"},
                "payload": {"kind": "comment", "after": "p1",
                            "content": {"thread": "c1", "anchor": "p1", "anchor_text": "Retries",
                                        "scope": "code", "status": "open"}}},
               {"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": COMMENT, "attrs": {"block": "c1"},
                            "start": list(r[0]), "end": list(r[6]), "on": True}}], actor=H)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", COMMENT, r[0], r[6], on=False, actor="loop", plan=pid, attrs={"block": "c1"})   # proposes closing the thread
    mark(ws, "p1", SUGGEST_DEL, r[8], r[11], actor="loop", plan=pid)        # …and striking "once"
    # The person resolves the thread themselves before answering.
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": COMMENT, "attrs": {"block": "c1"},
                            "start": list(r[0]), "end": list(r[6]), "on": False}}], actor=H)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert acc.conflict is None, acc.conflict
    assert ws.store.plans[pid].status == "accepted"
    assert T(ws.store, "p1") == "Retries ."


def test_a_plan_bold_on_trunk_words_stays_out_of_the_export():
    m, s = Mint(), Store()
    heading(m, s, "b-000000000001", "T")
    ins = paragraph(m, s, "b-000000000002", "alpha beta", after="b-000000000001")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p-1"}, {"kind": "edit"}, actor="loop"))
    apply_mark(m, s, "b-000000000002", ins, 0, 4, actor="loop", plan="p-1")
    assert "**alpha**" not in export_markdown(s)
    assert markdown(s.blocks["b-000000000002"].text(), "accepted", s) == "**alpha** beta"


def test_the_page_keeps_a_mark_a_plan_proposes_to_remove(tmp_path):
    """A proposed unbold leaves the word bold and says so beside it, the way a
    proposed deletion leaves the words struck rather than gone."""
    from codoc.calculus.derive import block_entry, plan_claims
    from codoc.calculus.text import BOLD
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
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
