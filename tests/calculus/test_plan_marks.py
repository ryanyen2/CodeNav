"""A plan's marks are proposals: placed by an op, undone by that op (spec 01 §3).

Review 5's A4: matching a mark by value let a second plan's toggle, or a bold
grown over text typed at its edge, survive the plan that placed it.
"""
import json
import random

from codoc.calculus import ops as K
from codoc.calculus.derive import block_entry, markdown
from codoc.calculus.derive import live_spans
from codoc.calculus.inline_md import parse
from codoc.calculus.text import BOLD, CITE, LINK, SUGGEST_DEL
from codoc.substrate.workspace import Workspace
from tests.substrate.test_review5 import H, bold_chars, ins, mark, refs, seed


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


def test_typing_at_the_end_of_a_plans_bold_does_not_make_it_unrejectable(tmp_path):
    """A plan bolds 'Retries'; the person types at its end (bold grows, I2).
    Rejecting the plan must remove the plan's bold; the person's characters
    were never bold in the document without the plan."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    r = refs(ws, "p1")
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mk = mark(ws, "p1", BOLD, r[0], r[6], actor="loop", plan=a)
    typed = ins(ws, "p1", list(r[6]), "!!")
    t = ws.store.blocks["p1"].text()
    assert bold_chars(t) >= {(typed.id, 0), (typed.id, 1)}          # grew, as I2 says
    # rejected read mode while open: no bold at all
    assert "**" not in markdown(t, "rejected", ws.store), markdown(t, "rejected", ws.store)
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)
    t = ws.store.blocks["p1"].text()
    assert not bold_chars(t), [m.to_json() for m in t.marks]


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
            from codoc.calculus.derive import _unapply_plan_marks
            view.marks = [m for m in view.marks if not (m.kind == SUGGEST_DEL and m.attrs.get("plan") in open_plans)]
            _unapply_plan_marks(view, ws.store, open_plans)
            rej = set()
            for m in view.marks:
                if m.kind == SUGGEST_DEL:
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
