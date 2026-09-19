"""Review-8 probes for the Python core, written against `18855c0`.

Every probe asserts the CORRECT behaviour (specs 01–04 and the round-7 Applied
note), so a failing probe is a confirmed fault and a passing one a confirmed
property. Section H holds confirmations expected to pass.

Run from the worktree:

  cd /Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate && \
  PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest \
    /private/tmp/claude-501/-Users-r4yen-Desktop-Research-CoDoc-repos-CodeNav/d28f4a40-c80f-4f4e-b0c9-17b9d836ed30/scratchpad/probe8_core.py \
    -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codoc.calculus import ops as K
from codoc.calculus.derive import directive, history, markdown, phase
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.text import BOLD, SUGGEST_DEL, SUGGEST_MARK
from codoc.calculus.window import history_window
from codoc.substrate import client
from codoc.substrate import log as L
from codoc.substrate.snapshot import settled_prefix, write_snapshot
from codoc.substrate.workspace import Roles, Workspace
from tests.substrate.test_review5 import H, T, _sub, bold_chars, fold, ins, mark, refs, seed, write_roles

WORKTREE = Path(__file__).resolve()
for _p in Path(__file__).resolve().parents:
    if (_p / "codoc" / "calculus").is_dir():
        WORKTREE = _p
        break
else:
    WORKTREE = Path("/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate")

A = "human:gh:alice"


def suggester(cd: Path, ws: Workspace, actor: str = A) -> None:
    write_roles(cd, {actor: "suggest"})
    ws.roles = Roles.load(cd)
    ws.store.policy = ws.roles.policy


def second_feature_in_flight(ws: Workspace) -> str:
    """A second feature created AFTER everything on the first, with an open
    plan on it: the fold's cut lands at h2's creation, so the first feature's
    whole history is in the folded prefix."""
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Server"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "h2"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "Listens."}},
    ], actor="loop")
    pid = ws.open_plan(actor="loop", kind="edit", target="p2")
    ins(ws, "p2", refs(ws, "p2")[-1], " Proposed.", actor="loop", plan=pid)
    return pid


def sub_ins(cd, bid, after, text, cid="c1", batch="b1"):
    return _sub(cid, K.TEXT_INSERT, {"bid": bid, "path": "text"}, {"after": list(after) if not isinstance(after, str) else after, "text": text}, batch=batch)


def open_plans_of(store, actor=None):
    return [p for p in store.plans.values() if p.status == "open" and (actor is None or p.actor == actor)]


@pytest.fixture(autouse=True)
def _release_client():
    yield
    client.release()
    client._SEEN.clear()


# ═════════════════════════════════════════════════════════════════════════════
# A. The fold (round 7 item 1): what `state_at` re-applies, and what a later
#    reader still needs from the folded prefix
# ═════════════════════════════════════════════════════════════════════════════

def test_A1_state_at_does_not_reapply_an_op_the_applier_refused(tmp_path):
    """01 §7.3: authority is checked once, when the op arrives, and 'replay
    honours what the record says'. `Workspace._replay_one` does (a conflicted
    op is recorded, not applied); `snapshot.state_at` builds the folded prefix
    with a bare `apply` on a policy-less Store, so a refused op inside the
    prefix is applied into the state the snapshot then asserts."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    suggester(cd, ws)
    seed(ws)
    bad = ins(ws, "p1", refs(ws, "p1")[-1], " Alice was here.", actor=A)      # untagged trunk write
    assert bad.conflict == "authority"
    second_feature_in_flight(ws)                                             # partial fold: cut at h2's creation
    ws.close()
    fold(cd)
    ws2 = Workspace(cd)
    assert "Alice" not in T(ws2.store, "p1"), T(ws2.store, "p1")


def test_A2_state_fingerprint_is_equal_across_a_partial_fold(tmp_path):
    """01 §7.5: `state_fingerprint` is equal across a fold. Confirmation for the
    round-7 fold when the cut is inside the log (an open plan on a later
    feature keeps the tail as ordinary log)."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"}, "payload": {"from": list(r[8]), "to": list(r[11])}}], actor=H)
    second_feature_in_flight(ws)
    before = ws.store.state_fingerprint()
    cut = settled_prefix(ws.store)
    assert 0 < cut < len(ws.store.ops)
    ws.close()
    fold(cd)
    ws2 = Workspace(cd)
    assert ws2.store.state_fingerprint() == before


def test_A3_a_removal_the_person_made_before_a_fold_still_reaches_the_directive(tmp_path):
    """01 §6 `directive`: 'what the person changed since the last realization',
    with `removed:` from the op that killed each word. A feature nobody has
    handed off is `synced` at fold time, so its ops are folded; the segment
    keeps `dead=<op id>` but the killer's actor lived only on the op.
    `_human_changes` resolves the killer through `by_id`, finds nothing, and
    the removal is silently gone from the request the person sends next."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    r = refs(ws, "p1")
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"}, "payload": {"from": list(r[8]), "to": list(r[11])}}], actor=H)
    assert T(ws.store, "p1") == "Retries ."
    ws.close()
    fold(cd)
    ws2 = Workspace(cd)
    ws2.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "drop the retry"}}], actor=H)
    d = directive(ws2.store, "h1")
    assert 'removed: "once"' in d, d


def test_A4_a_bold_the_person_placed_before_a_fold_is_still_a_focus_line(tmp_path):
    """Same seam as A3 for `Focus:`: `_new_marks` reads the person's `text.mark`
    OPS beyond the realized frontier. The mark itself carries actor and op and
    survives the fold; the op does not, and the directive reads the op."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])
    ws.close()
    fold(cd)
    ws2 = Workspace(cd)
    ws2.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "make retries the focus"}}], actor=H)
    d = directive(ws2.store, "h1")
    assert 'Focus: "Retries"' in d, d


def test_A5_history_is_a_derived_view_and_01_7_5_says_every_view_is_equal_across_a_fold(tmp_path):
    """01 §7.5: '`state_fingerprint` is equal across a fold, and so is every
    derived view'. `history(f)` (01 §6) is a derived view: before the fold it
    is the person's ops; after, one `block.add` by `import`. The sentence
    overstates; either the spec names the views that survive or the fold keeps
    what History reads."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    before = [(o.kind, o.actor) for o in history(ws.store, "h1")]
    ws.close()
    fold(cd)
    ws2 = Workspace(cd)
    after = [(o.kind, o.actor) for o in history(ws2.store, "h1")]
    assert after == before, (before, after)


def test_A6_codoc_snapshot_says_so_when_there_is_nothing_to_fold(tmp_path):
    """04 §5: 'the fold writes nothing (it returns None rather than a file)'.
    `cli.snapshot` prints `path.name` on that None."""
    from typer.testing import CliRunner
    from codoc.cli.main import app
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")       # in flight from the first feature: cut = 0
    ins(ws, "p1", refs(ws, "p1")[-1], " Plan.", actor="loop", plan=pid)
    ws.close()
    res = CliRunner().invoke(app, ["snapshot", "--root", str(tmp_path)])
    assert res.exit_code == 0 and res.exception is None, (res.exit_code, repr(res.exception), res.output)


def test_A7_a_fold_does_not_orphan_the_session_a_daemonless_applier_is_still_writing(tmp_path):
    """01 §7.5 archives 'a session file the fold covers WHOLE'. A held
    daemonless applier (the MCP server between calls) has an open session file
    whose every op so far is settled; `codoc snapshot` (no daemon, so allowed)
    moves that file under `log/archive/`; the applier's writer keeps the fd and
    appends the next op to the archived file, which nothing replays."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.close()
    rr = list(client.read(cd).store.blocks["p1"].text().live_refs())
    client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                             "payload": {"after": list(rr[-1]), "text": " A."}}])
    held = client._OWNED[cd]
    assert " A." in T(held.store, "p1")
    # what `codoc snapshot` does: a raw Workspace, no lock, archive what is covered
    w = Workspace(cd)
    assert write_snapshot(cd, w.store, w.clock) is not None
    w.close()
    rr = list(held.store.blocks["p1"].text().live_refs())
    client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                             "payload": {"after": list(rr[-1]), "text": " B."}}])
    fresh = Workspace(cd, readonly=True)
    assert " B." in T(fresh.store, "p1"), (T(fresh.store, "p1"), [p.name for p in L.list_sessions(cd)],
                                           [p.name for p in L.list_archived(cd)])


# ═════════════════════════════════════════════════════════════════════════════
# B. One applier (round 7 item 7): what a held process reads after another
#    process wrote
# ═════════════════════════════════════════════════════════════════════════════

def test_B1_client_read_sees_what_another_process_applied_since_our_last_turn(tmp_path):
    """client.py: `applying()` rebuilds 'when the log grew by another hand
    since our last turn'. `client.read()` returns the held workspace with no
    such check, so the MCP server's next `codoc_read_*` (and the `base_text`
    its `reflect` computes) describes the tree before the person's
    `codoc accept` in the terminal."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.close()
    rr = list(client.read(cd).store.blocks["p1"].text().live_refs())
    client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                             "payload": {"after": list(rr[-1]), "text": " A."}}])
    code = (
        "from pathlib import Path\n"
        "from codoc.substrate import client\n"
        f"cd = Path({str(cd)!r})\n"
        "rr = list(client.read(cd).store.blocks['p1'].text().live_refs())\n"
        "client.write(cd, client='cli', bodies=[{'kind': 'text.insert', 'target': {'bid': 'p1', 'path': 'text'},"
        " 'payload': {'after': list(rr[-1]), 'text': ' B.'}}])\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=str(WORKTREE),
                   env={**os.environ, "PYTHONPATH": str(WORKTREE)}, timeout=60)
    assert " B." in T(Workspace(cd, readonly=True).store, "p1")          # the log has it
    assert " B." in T(client.read(cd).store, "p1")                       # the held read should too


# ═════════════════════════════════════════════════════════════════════════════
# C. Plans: the verdict's two phases, the strike's range, redo, a plan's attach
# ═════════════════════════════════════════════════════════════════════════════

def test_C1_a_plan_accept_that_conflicts_leaves_the_state_untouched(tmp_path):
    """apply.py:4-8: handlers 'RESOLVE every anchor, block and mark they will
    touch before the first mutation, so a conflict discovered midway never
    leaves a partial change behind'. `_h_plan_accept` strikes first and applies
    the mark proposals after; a `suggest_mark` that proposes turning a WHOLE
    mark off whose trunk mark the person has since removed raises MarkError
    there, the accept is recorded as `mark_partial`, and the strike stands."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", "comment", r[0], r[6], attrs={"block": "c1"})                       # the person's comment
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", "comment", r[0], r[6], on=False, actor="loop", plan=pid, attrs={"block": "c1"})   # proposes resolving it
    mark(ws, "p1", SUGGEST_DEL, r[8], r[11], actor="loop", plan=pid)                    # and striking "once"
    mark(ws, "p1", "comment", r[0], r[6], on=False, attrs={"block": "c1"})              # the person resolves it first
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    if acc.conflict:
        assert T(ws.store, "p1") == "Retries once.", (acc.conflict, T(ws.store, "p1"))
        assert ws.store.plans[pid].status == "open"
    else:
        assert T(ws.store, "p1") == "Retries ."


def test_C2_accepting_a_strike_kills_the_proposals_characters_not_the_persons_words_typed_inside(tmp_path):
    """01 §3: `plan.accept` 'tombstones the characters under its suggest_del
    marks'; I2 keeps a person's typing outside a suggestion. A `suggest_del`
    is stored as a range and struck with a range delete, so a word the person
    typed INSIDE the struck span (blue inside gray) dies with the proposal,
    although the proposal never named it."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    r = refs(ws, "p1")
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", SUGGEST_DEL, r[8], r[11], actor="loop", plan=pid)       # strike "once"
    ins(ws, "p1", r[9], "X")                                               # the person: "onXce"
    assert T(ws.store, "p1") == "Retries onXce."
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert acc.conflict is None
    assert T(ws.store, "p1") == "Retries X.", T(ws.store, "p1")


def test_C3_the_inverse_of_a_plan_marks_inverse_applies(tmp_path):
    """apply.py:9: 'The inverse of an inverse is the original'; 01 §9: `apply ∘
    inverse = identity` for every op kind. A plan-tagged bold records a
    `suggest_mark`; its inverse (`suggest_mark off`, untagged) applies; the
    inverse of THAT (`suggest_mark on`, untagged) is refused with
    `suggest_needs_plan`, so a redo cannot be expressed."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    r = refs(ws, "p1")
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    m = mark(ws, "p1", BOLD, r[0], r[6], actor="loop", plan=pid)
    undo = ws.submit([{"kind": m.inverse[0]["kind"], "target": m.inverse[0]["target"], "payload": m.inverse[0]["payload"]}], actor=H)[0]
    assert undo.conflict is None and not ws.store.blocks["p1"].text().proposals(pid)
    redo = ws.submit([{"kind": undo.inverse[0]["kind"], "target": undo.inverse[0]["target"], "payload": undo.inverse[0]["payload"]}], actor=H)[0]
    assert redo.conflict is None, redo.conflict
    assert ws.store.blocks["p1"].text().proposals(pid, SUGGEST_MARK)


def test_C4_a_binding_an_open_plan_borrowed_does_not_make_its_owner_look_emptied(tmp_path):
    """01 §3: a plan's `bind.attach` applies to the live document, tagged, and
    I8 displaces the prior owner. `reflect._emptied` then lists that owner —
    'once had code and has none now' — and the next pass opens a DELETE plan
    for a feature whose code a proposal has only borrowed."""
    from codoc.loops.reflect import _emptied
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)                                                             # h1 owns c.py::send
    pid = ws.open_plan(actor="agent:claude-code", kind="add", target="h2")
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Sender"}},
        {"kind": K.BIND_ATTACH, "target": {"fid": "h2"}, "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": "f1", "types_hash": "t1"}},
    ], actor="agent:claude-code", plan=pid)
    assert ws.store.bindings[("c.py", "c.py::send")].feature == "h2"       # borrowed while the plan is open (spec)
    assert "h1" not in _emptied(ws.store), _emptied(ws.store)


# ═════════════════════════════════════════════════════════════════════════════
# D. Authority (round 7 item 3): a contributor's own words, and the drain
# ═════════════════════════════════════════════════════════════════════════════

def test_D1_a_suggester_cannot_revive_withdrawn_words_after_the_plan_was_accepted(tmp_path):
    """01 §7.3: a suggester's `text.restore` is allowed when it undoes 'a
    `text.delete` of their own'. `_own_plan_text` checks only the delete's
    actor. Withdraw your proposed words, wait for the verdict, restore: the
    words come back live, tagged with an ACCEPTED plan, as trunk prose nobody
    accepted."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    suggester(cd, ws)
    seed(ws)
    pid = ws.open_plan(actor=A, kind="edit", target="p1")
    i = ins(ws, "p1", refs(ws, "p1")[-1], " More.", actor=A, plan=pid)
    d = ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"}, "payload": {"segments": [[i.id, 0, 6]]}}], actor=A)[0]
    assert d.conflict is None and T(ws.store, "p1") == "Retries once."
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert acc.conflict is None
    res = ws.submit([{"kind": K.TEXT_RESTORE, "target": {"bid": "p1", "path": "text"}, "payload": {"segments": [[i.id, 0, 6]], "by": d.id}}], actor=A)[0]
    assert res.conflict == "authority" or " More." not in T(ws.store, "p1"), (res.conflict, T(ws.store, "p1"))


def test_D2_a_suggester_cannot_revive_withdrawn_words_after_the_plan_was_rejected(tmp_path):
    """The same door after a REJECT: the revived segments carry a rejected
    plan's tag, which the `rejected` read mode does not hide (it hides OPEN
    plans' words), so they appear in the export as the document's prose."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    suggester(cd, ws)
    seed(ws)
    pid = ws.open_plan(actor=A, kind="edit", target="p1")
    i = ins(ws, "p1", refs(ws, "p1")[-1], " More.", actor=A, plan=pid)
    d = ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"}, "payload": {"segments": [[i.id, 0, 6]]}}], actor=A)[0]
    assert d.conflict is None
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None
    res = ws.submit([{"kind": K.TEXT_RESTORE, "target": {"bid": "p1", "path": "text"}, "payload": {"segments": [[i.id, 0, 6]], "by": d.id}}], actor=A)[0]
    exported = markdown(ws.store.blocks["p1"].text(), "rejected", ws.store)
    assert res.conflict == "authority" or " More." not in exported, (res.conflict, exported)


def test_D3_a_suggesters_typo_corrected_within_one_flush_is_not_struck_under_their_own_proposal(tmp_path):
    """Round 7 A3/P3a: 'a contributor who types a word and backspaces over it'
    gets a real delete. The drain decides that BEFORE it resolves provisional
    `$cid` anchors (`_as_proposal` at workspace.py:567, `_resolve_provisional`
    at :580), so when the insert and the backspace arrive in one flush the
    range resolves to nothing, the own-words check fails closed, and the
    correction becomes a `suggest_del` over their own just-typed characters."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    suggester(cd, ws)
    seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [
        sub_ins(cd, "p1", r[-1], " Moer", cid="c1"),
        _sub("c2", K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": ["$cid", "c1", 0], "to": ["$cid", "c1", 4]}),
    ])
    ops = ws.drain_inbox()
    assert all(o.conflict is None for o in ops), [(o.kind, o.conflict) for o in ops]
    t = ws.store.blocks["p1"].text()
    assert T(ws.store, "p1") == "Retries once." and not t.proposals(kind=SUGGEST_DEL), (T(ws.store, "p1"), [m.kind for m in t.marks])


def test_D4_the_hub_gate_accepts_a_log_that_holds_a_suggesters_own_words_delete(tmp_path):
    """01 §7.3 / 04 §6: the hub validates a pull request's session files
    against the roles table. The three authority exceptions (`_own_plan_text`,
    `_own_comment`) read `by_id` and `ops_on`, which `validate._plans_view`
    does not carry, so a contributor who typed and backspaced has a log the
    door refuses as 'recorded as applied, but the roles table refuses it'."""
    from codoc.serve.validate import validate_logs
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    suggester(cd, ws)
    seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [sub_ins(cd, "p1", r[-1], " More.", cid="c1")])
    i = ws.drain_inbox()[0]
    assert i.conflict is None and i.plan
    L.submit(cd, "hub-alice", [_sub("c2", K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [i.id, 0], "to": [i.id, 5]}, batch="b2")])
    d = ws.drain_inbox()[0]
    assert d.kind == K.TEXT_DELETE and d.conflict is None and T(ws.store, "p1") == "Retries once."
    ws.close()
    findings = validate_logs(L.list_sessions(cd), roles=Roles.load(cd), store=Workspace(cd, readonly=True).store)
    assert findings == [], [str(f) for f in findings]


# ═════════════════════════════════════════════════════════════════════════════
# E. Two clones (I6 and 01 §7.2 at a merge)
# ═════════════════════════════════════════════════════════════════════════════

def test_E1_an_op_one_clone_applied_that_conflicts_at_merge_is_reported_not_silent(tmp_path):
    """01 §7.2: after a merge the order is `(at, session, seq)`. Clone A
    accepts plan P; clone B, not yet knowing, writes more words into P a
    moment later. At merge B's op replays after the accept and is recorded
    `plan_not_open`; B's own log says it applied. The merged tree drops B's
    words and `Workspace.problems` says nothing — Grove's 'arbitrarily losing
    content is not acceptable' with no report."""
    a = tmp_path / "A" / ".codoc"
    wa = Workspace(a)
    seed(wa)
    pid = wa.open_plan(actor="loop", kind="edit", target="p1")
    ins(wa, "p1", refs(wa, "p1")[-1], " Plan.", actor="loop", plan=pid)
    wa.close()
    b = tmp_path / "B" / ".codoc"
    shutil.copytree(a / "log", b / "log")
    wa = Workspace(a)
    acc = wa.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert acc.conflict is None
    wa.close()
    time.sleep(0.005)
    wb = Workspace(b)
    more = ins(wb, "p1", refs(wb, "p1")[-1], " More.", actor=H, plan=pid)
    assert more.conflict is None and " More." in T(wb.store, "p1")
    wb.close()
    for p in L.list_sessions(b):
        if not (a / "log" / p.name).exists():
            shutil.copy(p, a / "log" / p.name)
    merged = Workspace(a)
    assert " More." in T(merged.store, "p1") or merged.problems, \
        (T(merged.store, "p1"), merged.store.by_id[more.id].conflict, merged.problems)


# ═════════════════════════════════════════════════════════════════════════════
# F. Phase (a spec question, filed under §E of the report)
# ═════════════════════════════════════════════════════════════════════════════

def test_F1_a_person_who_only_deleted_words_has_something_to_hand_off(tmp_path):
    """02 §2: 'empty a title by deleting characters → a feature with an empty
    title, flagged'. 01 §6 defines `ink` as human LIVE segments, so a
    delete-only edit is not ink, the phase stays `synced`, the status bar says
    nothing, and yet a hand-off would carry `removed:` lines. One of the two
    documents should move."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws, actor="loop")
    assert phase(ws.store, "h1") == "synced"
    r = refs(ws, "p1")
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"}, "payload": {"from": list(r[8]), "to": list(r[11])}}], actor=H)
    assert phase(ws.store, "h1") == "edited", phase(ws.store, "h1")


# ═════════════════════════════════════════════════════════════════════════════
# G. Measured: where the fold cuts (04 §5 says this; the number is for §C)
# ═════════════════════════════════════════════════════════════════════════════

def test_G1_the_cut_is_the_creation_of_the_oldest_unsettled_feature(tmp_path):
    """04 §5: the fold covers 'everything before the oldest op any unfinished
    feature was written by'. That op is the feature's own `block.add`, so one
    drifted feature bootstrapped first keeps the entire log; one bootstrapped
    last lets everything before it fold. Documented; confirmed; measured."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    last = START
    adds = {}
    for i in range(1, 4):
        h, p = f"h{i}", f"p{i}"
        ops = ws.submit([
            {"kind": K.BLOCK_ADD, "target": {"bid": h}, "payload": {"kind": HEADING, "level": 1, "after": last}},
            {"kind": K.TEXT_INSERT, "target": {"bid": h, "path": "text"}, "payload": {"after": START, "text": f"F{i}"}},
            {"kind": K.BLOCK_ADD, "target": {"bid": p}, "payload": {"kind": PARAGRAPH, "after": h}},
            {"kind": K.TEXT_INSERT, "target": {"bid": p, "path": "text"}, "payload": {"after": START, "text": "Body."}},
            {"kind": K.BIND_ATTACH, "target": {"fid": h}, "payload": {"file": f"f{i}.py", "symbol": f"f{i}.py::s", "fingerprint": "a"}},
        ], actor="loop")
        adds[h] = ws.store.position[ops[0].id]
        last = p
    assert settled_prefix(ws.store) == len(ws.store.ops)
    ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": "h3"}, "payload": {"file": "f3.py", "symbol": "f3.py::s", "fingerprint": "b"}}], actor="loop")
    assert settled_prefix(ws.store) == adds["h3"]
    ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": "h1"}, "payload": {"file": "f1.py", "symbol": "f1.py::s", "fingerprint": "b"}}], actor="loop")
    assert settled_prefix(ws.store) == 0


# ═════════════════════════════════════════════════════════════════════════════
# H. Confirmations (expected to PASS): the round-7 items that hold
# ═════════════════════════════════════════════════════════════════════════════

def test_H1_two_plans_proposing_one_bold_are_decided_separately(tmp_path):
    """Round 7 item 2 (A2/P1c): a plan's mark toggle is a record beside the
    trunk's marks, so rejecting A does not empty B."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    r = refs(ws, "p1")
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], actor="loop", plan=a)
    b = ws.open_plan(actor="agent:claude-code", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[6], actor="agent:claude-code", plan=b)
    assert ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)[0].conflict is None
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": b}, "payload": {}}], actor=H)[0].conflict is None
    assert bold_chars(ws.store.blocks["p1"].text()) == set(r[0:7])


def test_H2_a_plan_bold_split_by_the_person_is_gone_from_the_export_after_the_reject(tmp_path):
    """Round 7 item 2 (A2/P1a-b): a split moves no record the reject has to
    find; the export shows no proposal."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])
    a = ws.open_plan(actor="loop", kind="edit", target="p1")
    mark(ws, "p1", BOLD, r[0], r[11], actor="loop", plan=a)
    assert ws.submit([{"kind": K.BLOCK_SPLIT, "target": {"bid": "p1", "path": "text"}, "payload": {"new": "p1b", "at": list(r[8])}}], actor=H)[0].conflict is None
    assert markdown(ws.store.blocks["p1b"].text(), "rejected", ws.store) == "nce."
    assert ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor=H)[0].conflict is None
    assert markdown(ws.store.blocks["p1"].text()) == "**Retries** o" and markdown(ws.store.blocks["p1b"].text()) == "nce."


def test_H3_a_suggesters_range_delete_over_their_own_acked_words_is_a_real_delete(tmp_path):
    """Round 7 item 3 (A3/P3a) holds once the insert is acked."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    suggester(cd, ws)
    seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [sub_ins(cd, "p1", r[-1], " More.", cid="c1")])
    i = ws.drain_inbox()[0]
    L.submit(cd, "hub-alice", [_sub("c2", K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [i.id, 0], "to": [i.id, 5]}, batch="b2")])
    d = ws.drain_inbox()[0]
    assert d.kind == K.TEXT_DELETE and d.conflict is None and T(ws.store, "p1") == "Retries once."
    assert not ws.store.blocks["p1"].text().proposals(kind=SUGGEST_DEL)


def test_H4_a_suggester_cannot_restore_a_maintainers_delete(tmp_path):
    """Round 7 item 3 (A3/P3b) holds: the restore is judged by the delete."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    suggester(cd, ws)
    seed(ws)
    pid = ws.open_plan(actor=A, kind="edit", target="p1")
    i = ins(ws, "p1", refs(ws, "p1")[-1], " More.", actor=A, plan=pid)
    d = ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"}, "payload": {"segments": [[i.id, 0, 6]]}}], actor=H)[0]
    assert d.conflict is None
    res = ws.submit([{"kind": K.TEXT_RESTORE, "target": {"bid": "p1", "path": "text"}, "payload": {"segments": [[i.id, 0, 6]], "by": d.id}}], actor=A)[0]
    assert res.conflict == "authority" and T(ws.store, "p1") == "Retries once."


def test_H5_the_lift_stays_on_the_trunk_however_much_changed(tmp_path):
    """Round 7 item 4: `derived=True` skips the gate for the lift."""
    from codoc.kinds.write import add_block, write_block
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws, actor="loop")                                   # nothing pending: the add itself lands on the trunk
    w = add_block(ws, "diagram", {"source": "flowchart TB\n  a --> b\n  c --> d\n  e --> f\n  g"}, after="p1", actor="loop")
    assert w.ok
    w2 = write_block(ws, w.bid, {"source": "flowchart TB\n  z"}, actor="loop", derived=True)
    assert w2.ok and open_plans_of(ws.store, "loop") == []
    assert ws.store.blocks[w.bid].text("source").live_text() == "flowchart TB\n  z"


def test_H6_a_reopen_reports_what_it_displaced_to_the_window(tmp_path):
    """Round 7 item 5: `plan.reopen` records `undid` and the window reads it."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Second"}},
    ], actor=H)
    pid = ws.open_plan(actor="loop", kind="move", target="h2")
    mv = ws.submit([{"kind": K.BLOCK_MOVE, "target": {"bid": "h2"}, "payload": {"after": START, "level_delta": 0}}], actor="loop", plan=pid)[0]
    assert mv.conflict is None and ws.store.ordered()[0].id == "h2"
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None and ws.store.ordered()[0].id == "h1"
    restored = ws.store.blocks["h2"].rank
    ro = ws.submit([{"kind": rej.inverse[0]["kind"], "target": rej.inverse[0]["target"], "payload": rej.inverse[0]["payload"]}], actor=H)[0]
    assert ro.kind == K.PLAN_REOPEN and ro.conflict is None and ws.store.ordered()[0].id == "h2"
    # An entry's `ranks` are the ranks right AFTER its op (like its `blocks`);
    # the walk back through the reopen uses `undid`, so the reject's entry
    # still reads the rank the reject set, not the one the reopen re-applied.
    entries = {e["id"]: e for e in history_window(ws.store)["ops"]}
    assert entries[ro.id]["ranks"].get("h2") == ws.store.blocks["h2"].rank
    assert entries[rej.id]["ranks"].get("h2") == restored


def test_H7_a_malformed_attach_does_not_remember_the_feature_as_once_bound(tmp_path):
    """Round 7 item 5 (A8/P5b): `bound_once` after the payload is read."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws, bind=False)
    bad = ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py"}}], actor="loop")[0]
    assert bad.conflict == "missing:symbol" and "h1" not in ws.store.bound_once


def test_H8_status_counts_only_answerable_proposals(tmp_path):
    """Round 7 item 6: a plan whose target died is not offered and not counted."""
    from codoc.mcp.tools import read_status
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    ins(ws, "p1", refs(ws, "p1")[-1], " Plan.", actor="loop", plan=pid)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor=H)
    ws.close()
    assert read_status(str(cd))["proposals"] == 0


def test_H9_the_fold_keeps_who_decided(tmp_path):
    """Round 7 item 8: `Plan.decided_by` survives a fold."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    ins(ws, "p1", refs(ws, "p1")[-1], " Plan.", actor="loop", plan=pid)
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)[0].conflict is None
    ws.close()
    fold(cd)
    ws2 = Workspace(cd)
    assert ws2.store.plans[pid].decided_by == H and ws2.store.plans[pid].status == "accepted"
