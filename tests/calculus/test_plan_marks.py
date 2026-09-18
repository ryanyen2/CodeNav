"""A plan's marks are proposals: placed by an op, undone by that op (spec 01 §3).

Review 5's A4: matching a mark by value let a second plan's toggle, or a bold
grown over text typed at its edge, survive the plan that placed it.
"""
import json
import random

from codoc.calculus import ops as K
from codoc.calculus.derive import block_entry, live_spans, markdown, plan_claims
from codoc.calculus.inline_md import parse
from codoc.calculus.text import BOLD, CITE, LINK, PROPOSALS, SUGGEST_DEL
from codoc.substrate.workspace import Workspace
from tests.substrate.test_review5 import H, T, bold_chars, ins, mark, refs, seed


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
    """A rejected, B open (B's toggle removed A's mark). The export (rejected
    mode) is 'the document without any open plan' — which never held A's bold."""
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
                attrs = {"href": "https://x"} if kind == LINK else {"file": "f", "symbol": "s"} if kind == CITE else {}
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
            lt = t.live_text()
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
    """01 §3 plan.reject: 'Marks are removed by the op that placed them'.
    A widening used to merge into the person's mark and keep THEIR placer, so
    nothing could find what the plan had placed. A proposal is a record beside
    the document, so the person's bold is exactly what it was until a verdict,
    and accepting is the one thing that widens it."""
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


def test_two_open_plans_with_adjacent_bolds_each_reject_cleanly(tmp_path):
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


def test_a_plan_cite_that_displaced_a_human_link_is_undone_whole_by_reject(tmp_path):
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


def test_rejected_read_mode_while_the_displacing_plan_is_open(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", LINK, r[0], r[11], attrs={"href": "https://x.test/"})
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", CITE, r[3], r[5], actor="loop", plan=a, attrs={"file": "c.py", "symbol": "send"})
    t = ws.store.blocks["p1"].text()
    assert markdown(t, "rejected", ws.store) == "[Retries once](https://x.test/).", markdown(t, "rejected", ws.store)


def test_a_plan_that_unbolds_the_middle_of_a_bold_is_undone_by_reject(tmp_path):
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


def test_a_plan_bold_that_widened_a_human_bold_is_undone_after_the_person_split_the_paragraph(tmp_path):
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


def test_the_rejected_read_mode_agrees_after_the_split_while_the_plan_is_open(tmp_path):
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


def test_rejecting_one_of_two_plans_that_bold_the_same_words_keeps_the_other_proposal(tmp_path):
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
