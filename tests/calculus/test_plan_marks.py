"""A plan's marks are proposals: placed by an op, undone by that op (spec 01 §3).

Review 5's A4: matching a mark by value let a second plan's toggle, or a bold
grown over text typed at its edge, survive the plan that placed it.
"""
import json
import random

from codoc.calculus import ops as K
from codoc.calculus.derive import block_entry, markdown
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
