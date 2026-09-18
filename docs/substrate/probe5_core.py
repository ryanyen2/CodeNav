"""Review-5 probes for the Python core. Every probe asserts the CORRECT behaviour
(spec 01–04), so a failing probe is a confirmed fault.

Run from the worktree:
  PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <this file> -q -p no:cacheprovider
"""
from __future__ import annotations

import copy
import json
import random
import shutil
from pathlib import Path

import pytest

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import block_entry, description, directive, live_spans, markdown, phase, plan_claims
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, CITE, LINK, SUGGEST_DEL, Mark
from codoc.calculus.window import history_window
from codoc.substrate import log as L
from codoc.substrate.publish import Publisher
from codoc.substrate.snapshot import write_snapshot
from codoc.substrate.workspace import Roles, Workspace

H = "human:local"


# ── helpers ──────────────────────────────────────────────────────────────────

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
    """Identities of the live characters under a bold mark."""
    out = set()
    for m in text.marks:
        if m.kind == BOLD:
            out.update(text.mark_refs(m))
    return out


def texts_after_each_op(store: Store) -> list[dict[str, str]]:
    out = []
    fresh = Store()
    for o in store.ops:
        apply(fresh, Op.from_json(copy.deepcopy(o.to_json())))
        out.append({b.id: b.text().live_text() for b in fresh.all_blocks()})
    return out


def write_roles(cd: Path, roles: dict):
    from codoc.doclang import write_config
    write_config(cd, roles=roles)


# ═════════════════════════════════════════════════════════════════════════════
# 1. Authority at arrival, honoured at replay; merges; snapshots
# ═════════════════════════════════════════════════════════════════════════════

def test_P1a_demotion_after_the_fact_does_not_rewrite_history(tmp_path):
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


def test_P1b_recorded_conflict_replays_as_that_conflict_and_fingerprint_holds(tmp_path):
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


def test_P1c_the_hub_has_a_validator_for_foreign_session_files():
    """01 §7.3 and 04 §6 say the door for a stranger's log is the hub's
    validation of a PR's session files. With replay no longer re-asking the
    policy, that validator is the ONLY thing standing between a forged
    `plan.accept` in a merged log and every clone applying it."""
    import codoc.serve as S
    root = Path(S.__file__).parent
    hits = [p for p in root.glob("*.py") if "read_session" in p.read_text() or "validate_logs" in p.read_text()]
    assert hits, "no module under codoc/serve reads or validates a session file"


def test_P1d_a_forged_success_line_in_a_local_log_now_lands(tmp_path):
    """Documents the new attack surface: strip `conflict` from a refused verdict
    in the log and the rebuild applies it. Passes today by construction of the
    design (recorded answer honoured) — kept as a pinned statement of what the
    hub validator (P1c) must catch."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    bad = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:gh:bob")[0]
    ws.close()
    path = L.list_sessions(cd)[0]
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if bad.id in line:
            d = json.loads(line); d.pop("conflict", None); lines[i] = json.dumps(d)
    path.write_text("\n".join(lines) + "\n")
    ws2 = Workspace(cd, readonly=True)
    assert ws2.store.plans[pid].status == "accepted"      # the forged verdict is honoured


def _fork(tmp_path, name: str, base: Path) -> Path:
    cd = tmp_path / name / ".codoc"
    cd.parent.mkdir()
    shutil.copytree(base, cd)
    return cd


def test_P1e_two_clones_appending_after_the_same_block_merge_without_a_rank_collision(tmp_path):
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


def test_P1f_snapshot_keeps_the_fingerprint_when_a_dead_block_exists(tmp_path):
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


def test_P1g_snapshot_keeps_an_accepted_delete_plan(tmp_path):
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


def test_P1h_a_verdict_after_a_snapshot_still_acts_on_the_plans_archived_ops(tmp_path):
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


def test_P1i_accept_after_a_snapshot_strikes_the_words_the_plan_proposed_to_delete(tmp_path):
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


def test_P1j_an_undo_of_a_pre_snapshot_delete_still_restores_the_block(tmp_path):
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


def test_P1k_a_read_only_command_leaves_no_session_file_behind(tmp_path):
    """`codoc rebuild` / `codoc snapshot` open a writing Workspace and write
    nothing; every such run leaves a tracked header-only log file (git noise)."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    n = len(L.list_sessions(cd))
    for _ in range(3):
        w = Workspace(cd); w.close()
    assert len(L.list_sessions(cd)) == n, "a Workspace that wrote no op left a log file"


# ═════════════════════════════════════════════════════════════════════════════
# 2. Plan marks as proposals
# ═════════════════════════════════════════════════════════════════════════════

def test_P2a_two_plans_on_one_span_rejected_in_order_leave_no_phantom_mark(tmp_path):
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


def test_P2b_rejected_read_mode_does_not_resurrect_a_rejected_plans_mark(tmp_path):
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


def test_P2c_typing_at_the_end_of_a_plans_bold_does_not_make_it_unrejectable(tmp_path):
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


def test_P2d_projection_spans_agree_with_rejected_markdown_by_character_identity(tmp_path):
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


# ═════════════════════════════════════════════════════════════════════════════
# 3. The incremental Publisher: a harder property test
# ═════════════════════════════════════════════════════════════════════════════

def _files(cd: Path) -> dict:
    out = {}
    for f in ("projection.json", "tree.codoc", "history.json", "realize.md"):
        p = cd / f
        out[f] = p.read_text() if p.exists() else None
    return out


def test_P3_warm_publisher_equals_cold_under_every_op_kind(tmp_path):
    rng = random.Random(7)
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Server"}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "h2"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "Listens."}}], actor=H)
    warm = Publisher(cd, str(tmp_path))
    plans: list[str] = []
    paras = ["p1", "p2"]
    heads = ["h1", "h2"]
    realizing: set[str] = set()
    dead: list[tuple[str, Op]] = []
    for step in range(200):
        r = rng.random()
        pb = rng.choice([p for p in paras if ws.store.blocks[p].live] or ["p1"])
        t = ws.store.blocks[pb].text()
        live = list(t.live_refs())
        if r < 0.15 and live:
            plan = rng.choice(plans + [None]) if plans else None
            ins(ws, pb, list(rng.choice(live)), f" w{step}", actor=rng.choice([H, "loop"]) if not plan else "loop", plan=plan)
        elif r < 0.22 and len(live) > 3:
            i, j = sorted(rng.sample(range(len(live)), 2))
            plan = rng.choice(plans + [None]) if plans else None
            kind = rng.choice([BOLD, LINK, SUGGEST_DEL]) if plan else rng.choice([BOLD, LINK])
            attrs = {"href": "https://x"} if kind == LINK else {}
            mark(ws, pb, kind, live[i], live[j], on=kind == SUGGEST_DEL or rng.random() < 0.6, actor="loop" if plan else H, plan=plan, attrs=attrs)
        elif r < 0.30:
            pid = ws.open_plan(actor=rng.choice(["loop", "agent:x"]), kind=rng.choice(["edit", "edit", "add"]), target=rng.choice(heads + paras) if rng.random() < 0.7 else None)
            plans.append(pid)
        elif r < 0.38 and plans:
            pid = plans.pop(rng.randrange(len(plans)))
            ws.submit([{"kind": K.PLAN_ACCEPT if rng.random() < 0.5 else K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)
        elif r < 0.45:
            bid = f"b{step}"
            after = rng.choice([b.id for b in ws.store.ordered()])
            kind = HEADING if rng.random() < 0.3 else PARAGRAPH
            plan = rng.choice(plans + [None, None]) if plans else None
            ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": kind, "level": rng.randint(1, 3), "after": after}},
                       {"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": "text"}, "payload": {"after": START, "text": f"blk {step}"}}],
                      actor="loop" if plan else H, plan=plan)
            (heads if kind == HEADING else paras).append(bid)
        elif r < 0.52:
            fid = rng.choice([h for h in heads if ws.store.blocks[h].live] or ["h1"])
            ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": fid}, "payload": {"asked": f"do {step}"}}], actor=H)
        elif r < 0.58:
            fid = rng.choice(heads)
            ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": fid}, "payload": {"sha": "abc"}}], actor="loop")
        elif r < 0.66:
            fid = rng.choice([h for h in heads if ws.store.blocks[h].live] or ["h1"])
            kind = rng.choice([K.BIND_ATTACH, K.BIND_REFRESH, K.BIND_DETACH])
            sym = rng.choice(["c.py::send", "c.py::recv", "d.py::x"])
            ws.submit([{"kind": kind, "target": {"fid": fid}, "payload": {"file": sym.split("::")[0], "symbol": sym, "fingerprint": f"f{step}"}}], actor="loop")
        elif r < 0.72:
            bid = rng.choice([b.id for b in ws.store.ordered()])
            others = [b.id for b in ws.store.ordered() if b.id not in ws.store.dominated_ids(bid)]
            if others:
                plan = rng.choice(plans + [None, None]) if plans else None
                ws.submit([{"kind": K.BLOCK_MOVE, "target": {"bid": bid}, "payload": {"after": rng.choice(others + ["START"]), "level_delta": rng.choice([-1, 0, 1])}}],
                          actor="loop" if plan else H, plan=plan)
        elif r < 0.78:
            bid = rng.choice([b.id for b in ws.store.ordered() if b.id != "h1"] or ["p1"])
            op = ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": bid}, "payload": {}}], actor=H)[0]
            if op.conflict is None:
                dead.append((bid, op))
        elif r < 0.82 and dead:
            bid, op = dead.pop(rng.randrange(len(dead)))
            ws.submit([dict(b) for b in op.inverse], actor=H)
        elif r < 0.87 and len(live) > 2:
            i = rng.randrange(1, len(live))
            sp = ws.submit([{"kind": K.BLOCK_SPLIT, "target": {"bid": pb, "path": "text"}, "payload": {"new": f"s{step}", "at": list(live[i])}}], actor=H)[0]
            if sp.conflict is None:
                paras.append(f"s{step}")
        elif r < 0.91:
            prev, nxt = ws.store.live_neighbours_of(pb)
            other = rng.choice([x for x in (prev, nxt) if x and ws.store.blocks[x].kind == PARAGRAPH] or [None])
            if other:
                ws.submit([{"kind": K.BLOCK_MERGE, "target": {"into": pb, "bid": other, "path": "text"}, "payload": {}}], actor=H)
        elif r < 0.95:
            hid = rng.choice(heads)
            ws.submit([{"kind": K.BLOCK_SET, "target": {"bid": hid, "path": "level"}, "payload": {"value": rng.randint(1, 4)}}], actor=H)
        else:
            # toggle the ephemeral realizing set
            fid = rng.choice(heads)
            realizing ^= {fid}
        warm.publish(ws.store, realizing=realizing, force=True)
        got = _files(cd)
        cold = Publisher(cd, str(tmp_path))
        cold.publish(ws.store, realizing=realizing, force=True)
        want = _files(cd)
        for f in got:
            if f == "projection.json":
                assert json.loads(got[f]) == json.loads(want[f]), (step, f)
            else:
                assert got[f] == want[f], (step, f)
    ws.close()


# ═════════════════════════════════════════════════════════════════════════════
# 4. The timeline window
# ═════════════════════════════════════════════════════════════════════════════

def _check_window(store: Store, limit: int = 1000):
    truth = texts_after_each_op(store)
    w = history_window(store, limit=limit)
    start = w["start"]
    for e in w["ops"]:
        snap = truth[e["pos"]]
        for bid, text in e["blocks"].items():
            assert text == snap[bid], (e["kind"], e["pos"], bid, text, snap[bid])
    if start > 0:
        before = truth[start - 1]
        for bid, text in w["before"].items():
            assert bid in before, (bid, "in before but not born yet")
            assert text == before[bid], ("before", bid, text, before[bid])
        for bid in w["born"]:
            assert bid not in before, (bid, "born inside the window but existed before it")
    return w


def test_P4a_a_segment_delete_spanning_two_blocks_un_applies_in_both(tmp_path):
    """insert 'abcdef'; delete it; restore (undo); split after 'c'; undo the
    restore → text.delete{segments} whose characters now sit in two blocks.
    The window must revive both halves at earlier positions."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    i = ins(ws, "p1", list(refs(ws, "p1")[-1]), "abcdef")
    d = ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"}, "payload": {"from": [i.id, 0], "to": [i.id, 5]}}], actor=H)[0]
    r = ws.submit([dict(b) for b in d.inverse], actor=H)[0]
    sp = ws.submit([{"kind": K.BLOCK_SPLIT, "target": {"bid": "p1", "path": "text"}, "payload": {"new": "p2", "at": [i.id, 2]}}], actor=H)[0]
    assert sp.conflict is None
    u = ws.submit([dict(b) for b in r.inverse], actor=H)[0]
    assert u.conflict is None and T(ws.store, "p1") == "Retries once." and T(ws.store, "p2") == ""
    _check_window(ws.store)


def test_P4b_window_equals_prefix_replay_under_every_kind_fuzzed(tmp_path):
    fails = []
    for seed_n in range(40):
        rng = random.Random(seed_n)
        ws = Workspace(tmp_path / f"w{seed_n}" / ".codoc"); seed(ws)
        paras = ["p1"]
        plans: list[str] = []
        undoable: list[Op] = []
        for step in range(45):
            pb = rng.choice([p for p in paras if ws.store.blocks[p].live] or ["p1"])
            t = ws.store.blocks[pb].text()
            live = list(t.live_refs())
            r = rng.random()
            op = None
            if r < 0.2 and live:
                plan = rng.choice(plans + [None]) if plans else None
                op = ins(ws, pb, list(rng.choice(live + ["START"])) if rng.random() < 0.9 else "START", f"w{step} ", actor="loop" if plan else H, plan=plan)
            elif r < 0.3 and len(live) > 2:
                i, j = sorted(rng.sample(range(len(live)), 2))
                op = ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": pb, "path": "text"}, "payload": {"from": list(live[i]), "to": list(live[j])}}], actor=H)[0]
            elif r < 0.38 and plans and len(live) > 2:
                i, j = sorted(rng.sample(range(len(live)), 2))
                mark(ws, pb, SUGGEST_DEL, live[i], live[j], actor="loop", plan=plans[-1])
            elif r < 0.45:
                plans.append(ws.open_plan(actor="loop", kind="edit", target=pb))
            elif r < 0.55 and plans:
                pid = plans.pop(rng.randrange(len(plans)))
                op = ws.submit([{"kind": K.PLAN_ACCEPT if rng.random() < 0.5 else K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
            elif r < 0.65 and len(live) > 1:
                op = ws.submit([{"kind": K.BLOCK_SPLIT, "target": {"bid": pb, "path": "text"}, "payload": {"new": f"s{step}", "at": list(rng.choice(live)) if rng.random() < 0.8 else "START"}}], actor=H)[0]
                if op.conflict is None:
                    paras.append(f"s{step}")
            elif r < 0.75:
                prev, nxt = ws.store.live_neighbours_of(pb)
                other = rng.choice([x for x in (prev, nxt) if x and ws.store.blocks[x].kind == PARAGRAPH] or [None])
                if other:
                    op = ws.submit([{"kind": K.BLOCK_MERGE, "target": {"into": pb, "bid": other, "path": "text"}, "payload": {}}], actor=H)[0]
            elif r < 0.82:
                bid = rng.choice(paras)
                op = ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": bid}, "payload": {}}], actor=H)[0]
            elif undoable:
                prev_op = undoable.pop(rng.randrange(len(undoable)))
                for b in prev_op.inverse:
                    op = ws.submit([dict(b)], actor=H)[0]
            if op is not None and op.conflict is None and op.inverse:
                undoable.append(op)
        try:
            _check_window(ws.store, limit=1000)
            # a bounded window that cuts batches and splits in half
            _check_window(ws.store, limit=rng.randint(3, 25))
        except AssertionError as e:
            fails.append((seed_n, str(e)[:200]))
        ws.close()
    assert not fails, fails


def test_P4c_ranks_and_batch_first_and_born_are_right(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "two"}}], actor=H, batch="t-add")
    m = ws.submit([{"kind": K.BLOCK_MERGE, "target": {"into": "p1", "bid": "p2", "path": "text"}, "payload": {}}], actor=H)[0]
    ws.submit([dict(b) for b in m.inverse], actor=H)          # split with new_takes: p2 revived, not born
    w = history_window(ws.store, limit=3)
    assert "p2" not in w["born"], w["born"]          # born before the window
    w = history_window(ws.store, limit=100)
    assert "p2" in w["born"]
    e = next(e for e in w["ops"] if e["kind"] == K.TEXT_INSERT and e["batch"] == "t-add")
    assert e["batch_first"] == ws.store.batch_first["t-add"]
    # a window that starts inside the batch still reports where the batch began
    w3 = history_window(ws.store, limit=len(ws.store.ops) - ws.store.batch_first["t-add"] - 1)
    e3 = next(x for x in w3["ops"] if x["batch"] == "t-add")
    assert e3["batch_first"] < w3["start"]


# ═════════════════════════════════════════════════════════════════════════════
# 5. Loops
# ═════════════════════════════════════════════════════════════════════════════

def test_P5a_translate_never_leaves_an_empty_open_plan(tmp_path):
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


def test_P5b_bootstrap_does_not_silently_drop_a_title_or_paragraph_ingest_refuses(tmp_path):
    from codoc.loops.bootstrap import bootstrap
    from codoc.graph.graph import Graph

    class Row:
        def __init__(self, file, sym):
            self.file, self.symbol_path, self.source, self.tokens_hash, self.types_hash = file, sym, "def f(): ...", "tk", "th"

    rows = [Row("a.py", "a.py::f")]
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)

    def propose_file(file, chunks, edges, snapshot, **kw):
        return [{"kind": "add_node", "id": "n1", "title": "Two line\ntitle",
                 "description": "A hand-wrapped\nparagraph.", "bindings": [["a.py", "a.py::f"]]}]

    res = bootstrap(ws, rows, Graph(), root_dir=str(tmp_path), organize=False, propose_file=propose_file,
                    propose_the_brief=lambda *a, **k: {})
    heads = ws.store.headings()
    assert len(heads) == 1
    assert T(ws.store, heads[0].id).strip(), "the heading was minted with an EMPTY title"
    assert description(ws.store, heads[0].id).strip(), "the paragraph block exists but is empty"
    ws.close()


def test_P5c_a_deleted_bound_feature_with_delete_code_can_be_settled(tmp_path):
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


def test_P5d_done_with_nothing_handed_off_is_refused_not_a_silent_success(tmp_path):
    """`codoc done` on a feature that was never handed off records
    `feature.realized covers={}` and reports 'marked done' (01 §3: realization
    is monotone and settles a REQUEST; here there is none)."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    op = ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {"sha": "abc"}}], actor=H)[0]
    assert op.conflict is not None, op.payload


def test_P5e_emptied_covers_a_feature_whose_last_binding_was_displaced(tmp_path):
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


# ═════════════════════════════════════════════════════════════════════════════
# 6. Workflow coherence
# ═════════════════════════════════════════════════════════════════════════════

def test_P6a_ingest_of_identical_text_reports_the_same_thing_on_both_paths(tmp_path):
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


def test_P6b_a_paragraph_moved_into_another_feature_does_not_read_as_that_features_unsent_ink(tmp_path):
    """The person wrote p1 under Client and handed Client off (realized). They
    then move p1 under Server. Server shows as `edited` with p1's old words as
    unsent ink, and a hand-off of Server lists them as 'Changed since the last
    realization', though nobody typed anything."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Server"}}], actor="loop")
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h2"}, "payload": {"file": "s.py", "symbol": "s.py::run", "fingerprint": "f"}}], actor="loop")
    assert phase(ws.store, "h2") == "synced"
    mv = ws.submit([{"kind": K.BLOCK_MOVE, "target": {"bid": "p1"}, "payload": {"after": "h2", "level_delta": 0}}], actor=H)[0]
    assert mv.conflict is None and ws.store.feature_of("p1") == "h2"
    # the move is the person's op on h2's body; the words themselves are old and were realized under Client
    assert phase(ws.store, "h2") != "edited" or "Retries once." not in directive(ws.store, "h2"), directive(ws.store, "h2")
    ws.close()


def test_P6c_a_kind_only_feature_reaches_the_directive(tmp_path):
    """03 §5.1: `consult` is what the realizing agent reads; `lower` runs when a
    hand-off renders. A feature whose only body block is a diagram must put
    its diagram in front of the agent."""
    from codoc.kinds.write import add_block
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "p1"}, "payload": {}}], actor=H)
    w = add_block(ws, "diagram", {"source": "flowchart TB\n  send --> recv"}, after="h1", actor=H)
    assert w.ok, w.refused
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    from codoc.loops.realize import render_queue
    q = render_queue(ws.store, str(tmp_path))
    # The diagram's source does reach the prompt — but only as raw human INK in
    # "Changed since the last realization", never through the kind's `consult`
    # (03 §5.1); `Kind.lower` / `consult` / `text_view` / `write_back` have no caller.
    assert "Diagram of this feature's code" in q, q[q.find("### "):q.find("### ") + 300]
    ws.close()


def test_P6d_a_heading_deleted_under_a_paragraph_plan_and_restored_keeps_the_plan_decidable(tmp_path):
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


def test_P5f_amend_lands_one_plan_when_the_second_block_flips_the_gate(tmp_path):
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


def test_P5g_written_by_treats_a_loop_lifted_diagram_as_machine_prose(tmp_path):
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
