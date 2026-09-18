"""Probes for substrate-review-2. Each test asserts the OBSERVED (buggy) behaviour,
so a passing probe CONFIRMS the bug. Run from the CodeNav-substrate worktree:
    ../CodeNav/.venv/bin/python -m pytest <this file> -q -p no:cacheprovider
"""
import copy
import json

import pytest

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import (
    directive, description, ground, history, ink, markdown, phase, plan_claims, title,
)
from codoc.calculus.export import export_markdown, import_markdown
from codoc.calculus.ids import START
from codoc.calculus.ingest import Refused, ingest, tokenize
from codoc.calculus.inline_md import Span, parse, render
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, CITE, LINK
from codoc.substrate import log as L
from codoc.substrate.log import Submission
from codoc.substrate.workspace import Workspace, _wire

from tests.calculus.test_apply import Mint, heading, paragraph, replay
from tests.calculus.test_ingest import run_ingest
from tests.calculus.test_derive_export import build


def T(s, bid):
    return s.blocks[bid].text().live_text()


def wire_replay(ws: Workspace) -> Store:
    """Rebuild exactly as Workspace.rebuild does: through _wire()."""
    fresh = Store(policy=ws.store.policy)
    for o in ws.store.ops:
        apply(fresh, _wire(Op.from_json(copy.deepcopy(o.to_json()))))
    return fresh


# ── A1. ink merges runs across the hand-off frontier ──────────────────────────

def test_A1_ink_reports_unsent_words_as_sent_when_adjacent_to_sent_ones():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client", actor="loop")
    ins = paragraph(m, s, "p1", "Retries once.", after="h1", actor="loop")
    a = apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 12], "text": " A"}))
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C"}))
    assert phase(s, "h1") == "queued"
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [a.id, 1], "text": " B"}))  # unsent
    claims = ink(s, "h1")
    assert [(c.stage, c.text) for c in claims] == [("sent", " A B")]      # BUG: " B" is unsent
    assert phase(s, "h1") == "queued"                                      # BUG: should be edited
    assert 'added: " A B"' in directive(s, "h1")                           # BUG: the queue shows unsent words as sent


# ── A2. _queued picks the frontier entry with the largest seq, not coverage ──

def test_A2_queued_misses_a_handoff_whose_new_ops_are_on_a_low_seq_session():
    a, b, s = Mint("a", "human:a"), Mint("b", "human:a"), Store()
    heading(a, s, "h1", "Client", actor="loop")
    ins = paragraph(a, s, "p1", "Retries once.", after="h1", actor="loop")
    apply(s, a.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C"}))
    for i in range(20):   # session a runs to a high seq
        apply(s, a.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 0], "end": [ins.id, 1], "on": bool(i % 2 == 0)}))
    apply(s, a.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    apply(s, a.op(K.FEATURE_REALIZED, {"fid": "h1"}, {}))
    assert phase(s, "h1") == "synced"
    # A new session (low seq) types, then hands off.
    apply(s, b.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 12], "text": " Now twice."}))
    assert phase(s, "h1") == "edited"
    ho = apply(s, b.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    w = s.watermarks("h1")
    assert w.handoff["b"] == 1 and w.realized.get("b") is None             # b:1 is handed off and unrealized
    assert [c.stage for c in ink(s, "h1")] == ["sent"]
    assert phase(s, "h1") == "synced"                                      # BUG: must be queued
    from codoc.loops.realize import queued
    assert queued(s) == []                                                 # BUG: no directive is rendered


# ── A3. plan.accept subset is not replayable through _wire (I6) ──────────────

def test_A3_subset_accept_replays_as_accept_all(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": "heading", "level": 1, "after": START}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "T"}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": "h1"}}], actor="human:local")
    ins = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "base"}}], actor="human:local")[0]
    pid = ws.open_plan(actor="agent:x")
    i1 = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [ins.id, 3], "text": " A"}}], actor="agent:x", plan=pid)[0]
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [i1.id, 1], "text": " B"}}], actor="agent:x", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {"subset": [i1.id]}}], actor="human:local")[0]
    assert acc.conflict is None and T(ws.store, "p1") == "base A"
    assert "subset" not in acc.payload                                     # canonical dropped the wire subset
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")                                   # a real rebuild from the log
    assert T(ws2.store, "p1") == "base A B"                                # BUG: replay accepted everything


# ── A4. an undo that restores a displaced mark loses actor/op on replay (I6) ─

def test_A4_restored_mark_actor_is_not_replayable(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    ins = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "the client"}}], actor="human:local")[0]
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": LINK, "attrs": {"href": "https://a"}, "start": [ins.id, 0], "end": [ins.id, 9], "on": True}}], actor="human:local")
    disp = ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                       "payload": {"kind": LINK, "attrs": {"href": "https://b"}, "start": [ins.id, 4], "end": [ins.id, 9], "on": True}}], actor="loop")[0]
    # The person undoes the loop's displacement (inverse bodies as their own op).
    ws.submit([dict(b) for b in disp.inverse], actor="human:local")
    live = sorted((m.attrs["href"], m.actor) for m in ws.store.blocks["p1"].text().marks)
    assert live == [("https://a", "human:local")]
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    replayed = sorted((m.attrs["href"], m.actor) for m in ws2.store.blocks["p1"].text().marks)
    assert replayed == [("https://a", "human:local")]                      # actor happens to coincide here…
    # …but the placing op does not: the canonical payload dropped `op`.
    live_ops = sorted(m.op for m in ws.store.blocks["p1"].text().marks)
    rep_ops = sorted(m.op for m in ws2.store.blocks["p1"].text().marks)
    assert live_ops != rep_ops                                             # BUG: I6 broken on the placing op
    assert ws.store.state_fingerprint() != ws2.store.state_fingerprint()


# ── A5. ingest is not a fixed point on marks ─────────────────────────────────

def test_A5a_ingest_keeps_a_bold_the_new_text_no_longer_has():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "alpha beta", after="h1")
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 0], "end": [ins.id, 4], "on": True}))
    assert markdown(s.blocks["p1"].text()) == "**alpha** beta"
    res, applied = run_ingest(s, Mint("h", "human:a"), "p1", "alpha **beta**", actor="human:a")
    assert res.refused is None and all(o.conflict is None for o in applied)
    assert markdown(s.blocks["p1"].text()) == "**alpha** **beta**"         # BUG: expected "alpha **beta**"


def test_A5b_ingest_does_not_widen_a_bold_that_survives_by_identity():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "Retries once via x.", after="h1")
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}))
    res, applied = run_ingest(s, Mint("h", "human:a"), "p1", "**Retries once** via x.", actor="human:a")
    assert res.refused is None
    assert markdown(s.blocks["p1"].text()) == "Retries **once** via x."   # BUG: expected "**Retries once** via x."


def test_A5c_appending_after_a_bolded_last_word_is_refused_for_the_loop():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "Retries once", after="h1")
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}))
    res, _ = run_ingest(s, Mint("l", "loop"), "p1", "Retries **once** more")
    assert res.refused == "would_drop_human_bold"                          # BUG: nothing was dropped; "once" ≠ "once "
    # And without marks, the same tokenization re-authors the person's last word.
    m2, s2 = Mint(), Store()
    heading(m2, s2, "h1", "C")
    paragraph(m2, s2, "p1", "one two", after="h1")
    res2, _ = run_ingest(s2, Mint("l", "loop"), "p1", "one two three")
    assert res2.deleted == 3 and res2.inserted == len("two three")         # BUG: "two" deleted and re-minted as loop's


# ── A6. inline_md: bold overlapping a link/cite is neither renderable nor parseable ──

def test_A6_bold_over_link_breaks_the_round_trip():
    out = render("abc", [Span(LINK, {"href": "u"}, 0, 2), Span(BOLD, {}, 1, 3)])
    assert out == "[a**b****](u)c**"                                       # BUG: unbalanced; the re-opened bold is never re-pushed
    # Nested (well-formed) forms cannot be read back either.
    text, spans = parse("**[x](https://u)**")
    assert text == "[x](https://u)" and [sp.kind for sp in spans] == [BOLD]  # BUG: the link became literal text
    text, spans = parse("[**x**](https://u)")
    assert text == "**x**" and [sp.kind for sp in spans] == [LINK]           # BUG: the bold became literal text
    # So a store state the editor can produce does not survive export → import.
    m, s = Mint(), Store()
    heading(m, s, "b-000000000001", "T")
    ins = paragraph(m, s, "b-000000000002", "see the client now", after="b-000000000001")
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"},
                  {"kind": CITE, "attrs": {"file": "c.py", "symbol": "C"}, "start": [ins.id, 4], "end": [ins.id, 13], "on": True}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"},
                  {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 13], "on": True}))
    md1 = export_markdown(s)
    s2 = Store()
    build(s2, Mint("t"), import_markdown(md1))
    assert export_markdown(s2) != md1                                      # BUG: not a fixed point


def test_A6b_literal_markdown_in_prose_is_not_escaped_on_export():
    m, s = Mint(), Store()
    heading(m, s, "b-000000000001", "T")
    paragraph(m, s, "b-000000000002", "# Not a heading {#b-000000000009}", after="b-000000000001")
    paragraph(m, s, "b-000000000003", "type **stars** to bold", after="b-000000000002")
    md1 = export_markdown(s)
    bodies = import_markdown(md1)
    kinds = [(b["kind"], b["payload"].get("kind")) for b in bodies if b["kind"] == K.BLOCK_ADD]
    assert kinds[1] == (K.BLOCK_ADD, "heading")                            # BUG: a paragraph came back as a heading
    s2 = Store()
    build(s2, Mint("t"), bodies)
    assert T(s2, "b-000000000009") == "Not a heading"
    assert any(mk.kind == BOLD for mk in s2.blocks[[b for b in s2.blocks if s2.blocks[b].kind == "paragraph"][-1]].text().marks)  # BUG: literal ** became a mark


# ── A7. Roles.policy lets agents write verdicts, hand-offs and realized ──────

def test_A7_agent_can_accept_plans_and_mark_features_realized(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": "heading", "level": 1, "after": START}}], actor="human:local")
    pid = ws.open_plan(actor="loop")
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="agent:claude-code")[0]
    assert acc.conflict is None                                            # BUG: an agent accepted a plan
    ho = ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor="agent:claude-code")[0]
    rz = ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="agent:claude-code")[0]
    assert ho.conflict is None and rz.conflict is None                     # BUG: spec §7.3 / ops.VERDICTS comment


def test_A7b_suggest_role_can_ride_someone_elses_open_plan(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    pid = ws.open_plan(actor="loop", note="the loop's repair")
    op = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "smuggled"}}],
                   actor="human:contrib", plan=pid)[0]
    assert op.conflict is None and op.id in ws.store.plans[pid].ops        # BUG: accepting the loop's plan lands the contributor's words


# ── A8. read_inbox loses a torn trailing line ────────────────────────────────

def test_A8_torn_inbox_line_is_skipped_and_never_reread(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    d = L.inbox_dir(tmp_path / ".codoc"); d.mkdir(parents=True, exist_ok=True)
    line = json.dumps(Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "X"}).__dict__)
    (d / "ide.jsonl").write_text(line[:20])                                # writer mid-line
    assert ws.drain_inbox({"ide": "human:local"}) == []
    with (d / "ide.jsonl").open("a") as fh:
        fh.write(line[20:] + "\n")                                         # writer finishes
    assert ws.drain_inbox({"ide": "human:local"}) == []                    # BUG: offset already past it; op lost, never acked
    assert T(ws.store, "p1") == ""


def test_A8b_malformed_submission_crashes_the_drain_after_advancing_the_offset(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    d = L.inbox_dir(tmp_path / ".codoc"); d.mkdir(parents=True, exist_ok=True)
    good = json.dumps(Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "X"}).__dict__)
    (d / "ide.jsonl").write_text(json.dumps({"cid": "c1", "kind": K.TEXT_INSERT}) + "\n" + good + "\n")
    with pytest.raises(TypeError):
        ws.drain_inbox({"ide": "human:local"})                             # BUG: dict(None)
    assert ws.drain_inbox({"ide": "human:local"}) == []                    # BUG: c2 was behind the crash and is gone
    assert T(ws.store, "p1") == ""


# ── A9. rejected add-plan leaves bindings on a dead heading ──────────────────

def test_A9_rejecting_an_add_plan_orphans_its_trunk_bindings(tmp_path):
    from codoc.graph.graph import Graph
    from codoc.loop.diff import ChangeSet
    from codoc.loops.reflect import reflect
    from tests.loops.test_reflect import chunk, seed
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    propose = lambda c, s, t, **kw: [{"kind": "add_node", "parent_id": "h1", "title": "Storage", "description": "Saves.",
                                     "bindings": [["s.py", "s.py::save"]]}]
    r = reflect(ws, ChangeSet(added=[chunk("s.py", "s.py::save", "f9", "t9")]), Graph(), propose=propose,
                index_keys={("s.py", "s.py::save")})
    pid = r.plans[0]
    fid = ws.store.plans[pid].target
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
    assert not ws.store.blocks[fid].live
    assert ws.store.bindings[("s.py", "s.py::save")].feature == fid        # BUG: a dead heading owns live code
    # The next pass sees the chunk as bound and never re-places it; the model is told a dead current_feature_id.
    seen = {}
    def spy(changes, subtree, titles, **kw):
        seen["mod"] = changes["modified"]; return []
    reflect(ws, ChangeSet(added=[], modified=[chunk("s.py", "s.py::save", "fA", "t9")]), Graph(), propose=spy)
    assert seen["mod"][0]["current_feature_id"] == fid                     # BUG


# ── A10. a held feature + no-op amend opens an empty plan ────────────────────

def test_A10_amend_with_unchanged_text_on_a_held_feature_opens_an_empty_plan(tmp_path):
    from codoc.graph.graph import Graph
    from codoc.loop.diff import ChangeSet
    from codoc.loops.reflect import reflect
    from tests.loops.test_reflect import chunk, seed
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")
    same = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": T(ws.store, "p1")}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=same)
    assert len(r.plans) == 1
    p = ws.store.plans[r.plans[0]]
    assert p.status == "open" and p.ops == []                              # BUG: an empty plan awaits a verdict forever


def test_A10b_amend_listing_only_the_changed_block_strikes_every_other_paragraph(tmp_path):
    from codoc.graph.graph import Graph
    from codoc.loop.diff import ChangeSet
    from codoc.loops.reflect import reflect
    from tests.loops.test_reflect import chunk, seed
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": "paragraph", "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "Still true."}}], actor="loop")
    one = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries twice before giving up."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=one)
    assert len(r.plans) == 1 and ws.store.plans[r.plans[0]].note == "paragraph no longer true"
    assert [c.stage for c in plan_claims(ws.store, "h1")] == ["removed"]   # p2 struck although the model said nothing about it


# ── A11. _mark_text returns the first same-kind span, so two Focus bolds read alike ──

def test_A11_two_human_bolds_yield_the_same_focus_text():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "Retries once and logs.", after="h1")
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 17], "end": [ins.id, 20], "on": True}))
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    d = directive(s, "h1")
    assert 'Focus: "once"; "once"' in d                                    # BUG: expected "once"; "logs"


# ── A12. context renders open-plan nodes in rejected mode → blank titles ─────

def test_A12_llm_context_shows_a_proposed_node_with_an_empty_title(tmp_path):
    from codoc.graph.graph import Graph
    from codoc.loops.context import select_context
    from tests.loops.test_reflect import seed
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="add", target="hX")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "hX"}, "payload": {"kind": "heading", "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "hX", "path": "text"}, "payload": {"after": START, "text": "Storage"}}], actor="loop", plan=pid)
    _, titles, _ = select_context(ws.store, Graph(), {"c.py"})
    assert {t["id"]: t["title"] for t in titles}["hX"] == ""                # BUG: the model cannot attach to it by name


# ── A13. quarantine at rebuild silently diverges from the log ────────────────

def test_A13_rebuild_quarantines_a_committed_op_and_then_writes_on_top(tmp_path):
    from codoc.substrate.clock import fmt
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    far = fmt(ws.clock.wall + 60 * 60 * 1000, 0, "peer")
    ws.clock.wall = ws.clock.wall + 60 * 60 * 1000 - 1                     # a collaborator's fast clock stamped this
    ins = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "hello"}}], actor="human:local")[0]
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert [o.id for o in ws2.quarantined] == [ins.id]
    assert T(ws2.store, "p1") == ""                                        # the truth on disk says "hello"
    nxt = ws2.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [ins.id, 4], "text": "!"}}], actor="human:local")[0]
    assert nxt.conflict == "unknown_anchor"                                # BUG: every later op anchored there conflicts


# ── A14. history attributes by the client's hint block, not the actual one ───

def test_A14_history_follows_the_hint_block():
    m, s = Mint(), Store()
    heading(m, s, "h1", "One"); a = paragraph(m, s, "a", "aaa", after="h1")
    heading(m, s, "h2", "Two", after="a"); b = paragraph(m, s, "b", "bbb", after="h2")
    apply(s, m.op(K.BLOCK_MOVE, {"bid": "b"}, {"after": "a"}))             # b now under h1
    apply(s, m.op(K.BLOCK_MERGE, {"into": "a", "bid": "b"}, {}))           # b's chars now in a
    ins = apply(s, m.op(K.TEXT_INSERT, {"bid": "b", "path": "text"}, {"after": [b.id, 2], "text": "!"}))  # stale hint
    assert ins.conflict is None and ins.payload["bid"] == "a"
    assert ins not in history(s, "h1") and ins in history(s, "h2")         # BUG: the edit landed in h1's paragraph


# ── A15. settle realizes on ANY reflect touching the feature ─────────────────

def test_A15_a_hand_edit_to_code_marks_a_queued_request_realized(tmp_path):
    from codoc.graph.graph import Graph
    from codoc.loop.diff import ChangeSet
    from codoc.loops.realize import handoff, settle
    from codoc.loops.reflect import reflect
    from tests.loops.test_reflect import chunk, seed
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Retry five times."}}], actor="human:local")
    handoff(ws, "h1", actor="human:local", asked="retry five times")
    # Nobody realized anything: the person fixed a typo in c.py by hand.
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=lambda *a, **k: [])
    settle(ws, r.caused, sha="typo")
    assert phase(ws.store, "h1") == "synced"                               # BUG: the request was never implemented


# ── A16. _valid_binds lets the model attach a REMOVED symbol ─────────────────

def test_A16_model_may_attach_a_symbol_that_no_longer_exists(tmp_path):
    from codoc.graph.graph import Graph
    from codoc.loop.diff import ChangeSet
    from codoc.loops.reflect import reflect
    from tests.loops.test_reflect import chunk, seed
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": "heading", "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Other"}}], actor="loop")
    propose = lambda c, s, t, **kw: [{"kind": "attach", "feature_id": "h2", "bindings": [["c.py", "c.py::send"]]}]
    cs = ChangeSet(removed=[chunk("c.py", "c.py::send", "f1", "t1")], added=[chunk("n.py", "n.py::x", "fz", "tz")], rows=[])
    r = reflect(ws, cs, Graph(), propose=propose, allow_delete=False)
    assert ws.store.bindings[("c.py", "c.py::send")].feature == "h2"      # BUG: bound to code the index just removed


# ── A17. ground with no hand-off paints every loop character green ───────────

def test_A17_bootstrap_prose_is_all_ground_added():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client", actor="loop")
    paragraph(m, s, "p1", "Retries once.", after="h1", actor="loop")
    g = ground(s, "h1")
    assert [(c.stage, c.text) for c in g] == [("added", "Client"), ("added", "Retries once.")]   # every bootstrap char is a "code added" claim


# ── B. the `_ref_for` Refused path and as_plan_bodies ───────────────────────

def test_B_refused_is_unreachable_but_uncaught():
    import inspect
    from codoc.calculus import ingest as I
    src = inspect.getsource(I.ingest)
    assert "except Refused" not in src                                     # nothing maps it to res.refused


# ── C. costs ─────────────────────────────────────────────────────────────────

def test_C_phase_and_publish_scan_all_ops(tmp_path):
    import time
    from codoc.substrate.publish import publish
    m, s = Mint(), Store()
    prev = START
    for i in range(300):
        h = f"h{i}"; heading(m, s, h, f"Feature {i}", after=prev)
        p = f"p{i}"; paragraph(m, s, p, "Some prose about it.", after=h); prev = p
        apply(s, m.op(K.BIND_ATTACH, {"fid": h}, {"file": f"f{i}.py", "symbol": f"S{i}"}))
    for i in range(300):
        apply(s, m.op(K.BIND_REFRESH, {"fid": f"h{i}"}, {"file": f"f{i}.py", "symbol": f"S{i}", "fingerprint": "x"}))
    n = len(s.ops)
    t0 = time.perf_counter(); [phase(s, f"h{i}") for i in range(300)]; t1 = time.perf_counter()
    t2 = time.perf_counter(); publish(tmp_path, s); t3 = time.perf_counter()
    print(f"\nops={n} features=300 phase×300={1000*(t1-t0):.0f}ms publish={1000*(t3-t2):.0f}ms")


# ── corrected / additional probes ─────────────────────────────────────────────

def test_A2fix_queued_and_covered_disagree_on_a_partial_realization():
    from codoc.calculus.derive import _queued
    from codoc.loops.realize import _covered
    a, b, s = Mint("a", "human:a"), Mint("b", "human:a"), Store()
    heading(a, s, "h1", "C", actor="loop"); ins = paragraph(a, s, "p1", "x", after="h1", actor="loop")
    apply(s, a.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C"}))
    apply(s, b.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 0], "text": "y"}))
    apply(s, a.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))                    # covers {a:5, b:1}
    w = s.watermarks("h1")
    apply(s, a.op(K.FEATURE_REALIZED, {"fid": "h1"}, {"covers": {"a": 5}}))  # a client-chosen partial cover
    assert _covered(w.handoff, w.realized) is False
    assert _queued(s, "h1", w) is False                                    # BUG: two definitions; this one checks one session
    assert phase(s, "h1") == "synced" and [c.stage for c in ink(s, "h1")] == ["sent"]


def test_A6fix_bold_inside_cite_survives_export_only_as_literal_stars():
    m, s = Mint(), Store()
    heading(m, s, "b-000000000001", "T")
    ins = paragraph(m, s, "b-000000000002", "see the client now", after="b-000000000001")
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"},
                  {"kind": CITE, "attrs": {"file": "c.py", "symbol": "C"}, "start": [ins.id, 4], "end": [ins.id, 13], "on": True}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"},
                  {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 13], "on": True}))
    md1 = export_markdown(s)
    assert "[the **client**](codoc:c.py#C)" in md1
    s2 = Store(); build(s2, Mint("t"), import_markdown(md1))
    assert export_markdown(s2) == md1                                      # textually a fixed point…
    p2 = [b for b in s2.ordered() if b.kind == "paragraph"][0]
    assert p2.text().live_text() == "see the **client** now"               # BUG: …but the stars are now characters
    assert [mk.kind for mk in p2.text().marks] == [CITE]                    # BUG: and the bold is gone


def test_A8bfix_null_target_submission_crashes_the_drain_and_loses_the_batch(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    d = L.inbox_dir(tmp_path / ".codoc"); d.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"cid": "c2", "kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "X"}})
    (d / "ide.jsonl").write_text(json.dumps({"cid": "c1", "kind": K.TEXT_INSERT, "target": None, "payload": {}}) + "\n" + good + "\n")
    with pytest.raises(TypeError):
        ws.drain_inbox({"ide": "human:local"})                             # BUG: dict(None) in _mint
    assert ws.drain_inbox({"ide": "human:local"}) == [] and T(ws.store, "p1") == ""   # BUG: c2 was behind it; gone


def test_A14fix_history_loses_an_op_whose_hint_block_is_dead():
    m, s = Mint(), Store()
    heading(m, s, "h1", "One"); paragraph(m, s, "a", "aaa", after="h1")
    heading(m, s, "h2", "Two", after="a"); b = paragraph(m, s, "b", "bbb", after="h2")
    apply(s, m.op(K.BLOCK_MOVE, {"bid": "b"}, {"after": "a"}))
    apply(s, m.op(K.BLOCK_MERGE, {"into": "a", "bid": "b"}, {}))
    ins = apply(s, m.op(K.TEXT_INSERT, {"bid": "b", "path": "text"}, {"after": [b.id, 2], "text": "!"}))   # stale hint
    assert ins.conflict is None and ins.payload["bid"] == "a" and T(s, "a") == "aaabbb!"
    assert ins not in history(s, "h1") and ins not in history(s, "h2")   # BUG: the op is in no feature's history


def test_A18_one_amend_opens_a_plan_per_new_paragraph(tmp_path):
    from codoc.graph.graph import Graph
    from codoc.loop.diff import ChangeSet
    from codoc.loops.reflect import reflect
    from tests.loops.test_reflect import chunk, seed
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    two = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1",
                                  "blocks": [{"id": "p1", "text": T(ws.store, "p1")}, {"text": "New one."}, {"text": "New two."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=two)
    assert len(r.plans) == 2 and len({ws.store.plans[p].target for p in r.plans}) == 1   # two verdicts for one repair


def test_A19_written_by_is_per_feature_not_per_block(tmp_path):
    from codoc.loops.context import written_by
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": "heading", "level": 1, "after": START}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "T"}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": "h1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Loop prose."}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": "paragraph", "after": "p1"}}], actor="loop")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "."}}], actor="human:local")
    assert written_by(ws.store, "h1") == "human"                           # one human period in p2 makes p1's gate the strict one


def test_A20_export_commits_open_plan_text_as_prose(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "b-000000000001"}, "payload": {"kind": "heading", "level": 1, "after": START}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "b-000000000001", "path": "text"}, "payload": {"after": START, "text": "T"}}], actor="human:local")
    pid = ws.open_plan(actor="loop")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "b-000000000002"}, "payload": {"kind": "paragraph", "after": "b-000000000001"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "b-000000000002", "path": "text"}, "payload": {"after": START, "text": "Unvetted claim."}}], actor="loop", plan=pid)
    assert "Unvetted claim." in export_markdown(ws.store)                  # tree.codoc (tracked) carries a proposal as fact


def test_A21_projection_never_says_realizing(tmp_path):
    from codoc.substrate.publish import publish, STATUS_FILE
    import inspect
    from codoc.substrate import daemon
    src = inspect.getsource(daemon.Daemon.process_batch)
    assert "publish(self.codoc_dir, self.ws.store, root_dir=str(self.root))" in src   # no `realizing=` ever passed


def test_A22_ingest_on_a_dead_block_logs_conflict_ops(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "old"}},
               {"kind": K.BLOCK_DELETE, "target": {"bid": "p1"}, "payload": {}}], actor="human:local")
    res, ops = ws.ingest("p1", "text", base_text="old", new_md="new", actor="loop")
    assert res.refused is None and [o.conflict for o in ops] == ["dead_block", "dead_block"]   # two conflict ops in the truth


def test_C2_scan_costs_at_scale(tmp_path):
    import time
    from codoc.calculus.derive import _drifted
    from codoc.substrate.publish import publish
    m, s = Mint(), Store()
    prev = START
    F = 1500
    for i in range(F):
        h = f"h{i}"; heading(m, s, h, f"Feature {i}", after=prev)
        p = f"p{i}"; paragraph(m, s, p, "Some prose about it that is a sentence long.", after=h); prev = p
        apply(s, m.op(K.BIND_ATTACH, {"fid": h}, {"file": f"f{i}.py", "symbol": f"S{i}"}))
    for rnd in range(4):
        for i in range(F):
            apply(s, m.op(K.BIND_REFRESH, {"fid": f"h{i}"}, {"file": f"f{i}.py", "symbol": f"S{i}", "fingerprint": f"x{rnd}"}))
    n = len(s.ops)
    t0 = time.perf_counter(); _drifted(s, "h7", s.watermarks("h7")); t1 = time.perf_counter()
    t2 = time.perf_counter(); [phase(s, f"h{i}") for i in range(F)]; t3 = time.perf_counter()
    t4 = time.perf_counter(); history(s, "h7"); t5 = time.perf_counter()
    t6 = time.perf_counter(); publish(tmp_path, s); t7 = time.perf_counter()
    print(f"\nops={n} features={F}: one _drifted={1000*(t1-t0):.1f}ms  phase×{F}={1000*(t3-t2):.0f}ms  history={1000*(t5-t4):.1f}ms  publish={1000*(t7-t6):.0f}ms")
