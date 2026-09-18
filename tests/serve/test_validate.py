"""The hub's door for a foreign log (spec 01 §7.3, 04 §6).

Replay honours the answer a log records, which makes the file a capability:
this is what reads a pull request's session logs before they are merged.
"""
import json

from codoc.calculus import ops as K
from codoc.serve.validate import Finding, refused_verdicts, validate_logs
from codoc.substrate import log as L
from codoc.substrate.log import Submission
from codoc.substrate.snapshot import write_snapshot
from codoc.substrate.workspace import Roles, Workspace
from tests.substrate.test_review5 import H, seed, write_roles


def _forge(path, op_id: str) -> None:
    """Strip the recorded conflict from one op, the way a hostile PR would."""
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        d = json.loads(line)
        if d.get("id") == op_id:
            d.pop("conflict", None)
            lines[i] = json.dumps(d)
    path.write_text("\n".join(lines) + "\n")


def test_a_forged_verdict_in_a_pull_requests_log_is_refused(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    write_roles(cd, {"human:gh:bob": "suggest"})
    ws.roles = Roles.load(cd)
    ws.store.policy = ws.roles.policy
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    bad = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:gh:bob")[0]
    assert bad.conflict == "authority"
    ws.close()
    path = L.list_sessions(cd)[0]
    roles = Roles.load(cd)
    assert validate_logs([path], roles=roles) == []          # as written, it is honest
    _forge(path, bad.id)
    found = validate_logs([path], roles=roles)
    assert len(found) == 1 and "refuses it" in found[0].reason, found
    # and the rebuild would have taken it, which is why the door exists
    assert Workspace(cd, readonly=True).store.plans[pid].status == "accepted"


def test_a_suggesters_op_on_a_plan_the_pull_request_opens_is_fine(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    write_roles(cd, {"human:gh:bob": "suggest"})
    ws.roles = Roles.load(cd)
    ws.store.policy = ws.roles.policy
    seed(ws)
    own = ws.open_plan(actor="human:gh:bob", kind="edit", target="p1")
    ok = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                     "payload": {"after": "START", "text": "Mine. "}}], actor="human:gh:bob", plan=own)[0]
    assert ok.conflict is None
    ws.close()
    assert validate_logs(L.list_sessions(cd), roles=Roles.load(cd)) == []


def test_a_snapshot_and_a_far_future_stamp_are_refused(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.close()
    ws = Workspace(cd)
    write_snapshot(cd, ws.store, ws.clock)
    ws.close()
    snap = L.list_snapshots(cd)[0]
    found = validate_logs([snap], roles=Roles.load(cd))
    assert len(found) == 1 and "only the workspace that folds its own log" in found[0].reason
    # a stamp a year out drags every clock that observes it
    ws2 = Workspace(cd)
    seed(ws2)
    ws2.close()
    path = L.list_sessions(cd)[0]
    ahead = validate_logs([path], roles=Roles.load(cd), now_ms=0)
    assert ahead and all("ahead of this clock" in f.reason for f in ahead)


def test_rebuild_counts_what_the_table_would_refuse_today(tmp_path):
    """A demotion never deletes what somebody wrote, so this is a signal the
    maintainer reads, never a gate the rebuild enforces."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    write_roles(cd, {"human:gh:alice": "maintainer"})
    ws.roles = Roles.load(cd)
    ws.store.policy = ws.roles.policy
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:gh:alice")[0].conflict is None
    ws.close()
    write_roles(cd, {"human:gh:alice": "suggest"})
    ws2 = Workspace(cd, readonly=True)
    assert refused_verdicts(ws2.store, Roles.load(cd)) == {"human:gh:alice": 1}


def test_a_finding_reads_as_a_place_and_a_reason():
    assert str(Finding("log/s-1.jsonl", 4, "why")) == "log/s-1.jsonl:4: why"
    assert str(Finding("log/snapshot-x.jsonl", 0, "why")) == "log/snapshot-x.jsonl: why"


def test_the_door_accepts_a_contributors_own_words_delete_and_their_own_comment(tmp_path):
    """Review 8: the policy's own-words exceptions read `by_id` and `ops_on`,
    which the door's view did not carry — so every pull request from a
    contributor who corrected a word was refused at merge. The delete rides in
    their plan now (the policy reads plans, which is what a door can have) and
    the comment rule reads the blocks the files themselves add."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    r = list(ws.store.blocks["p1"].text().live_refs())
    L.submit(cd, "hub-alice", [Submission(cid="i1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                          payload={"after": list(r[-1]), "text": " More."}, batch="b1")])
    ins = [o for o in ws.drain_inbox() if o.kind == K.TEXT_INSERT][0]
    L.submit(cd, "hub-alice", [
        Submission(cid="d1", kind=K.TEXT_DELETE, target={"bid": "p1", "path": "text"},
                   payload={"segments": [[ins.id, 0, 6]]}, batch="b2"),
        Submission(cid="c1", kind=K.BLOCK_ADD, target={"bid": "cx"},
                   payload={"kind": "comment", "after": "p1",
                            "content": {"thread": "cx", "anchor": "p1", "anchor_text": "Retries",
                                        "scope": "code", "status": "open"}}, batch="b3"),
        Submission(cid="c2", kind=K.BLOCK_SET, target={"bid": "cx", "path": "status"},
                   payload={"value": "resolved"}, batch="b3"),
    ])
    assert all(o.conflict is None for o in ws.drain_inbox())
    session = [p for p in L.list_sessions(cd) if p.stem == ws.session][0]
    ws.close()
    roles = Roles(table={"human:gh:alice": "suggest", H: "owner"}, default="suggest")
    assert validate_logs([session], roles=roles, store=Workspace(cd, readonly=True).store) == []
