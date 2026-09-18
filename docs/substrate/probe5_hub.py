"""Review-5 probes for the hub (`codoc/serve/*`) and the daemon facts the editor
surfaces depend on. Every test asserts the CORRECT behaviour, so a failing
test is a confirmed fault. Also writes the timeline fixtures the vitest probes
in `probe5_editor.test.ts` §D read.

    cd <worktree> && PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <this file> -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codoc.calculus import ops as K
from codoc.calculus.window import history_window
from codoc.doclang import write_config
from codoc.serve import realize_agent, worker
from codoc.serve.app import build_app, client_name
from codoc.serve.auth import COOKIE_NAME, MAINTAINER, SUGGEST, AuthContext, SessionStore
from codoc.serve.push import Feed
from codoc.serve.realize_agent import make_sandboxed_agent, realize_tool_policy
from codoc.serve.sandbox import edit_allowed
from codoc.serve.worker import Worker, process_ready, read_ledger, realize_one, Request
from codoc.substrate import log as L
from codoc.substrate.log import Submission
from codoc.substrate.publish import publish
from codoc.substrate.workspace import Workspace
from tests.loops.test_reflect import seed
from tests.serve.test_hub import FakeOAuth, FakeResolver
from tests.serve.test_worker import ready

FIX = Path("/private/tmp/claude-501/-Users-r4yen-Desktop-Research-CoDoc-repos-CodeNav/d28f4a40-c80f-4f4e-b0c9-17b9d836ed30/scratchpad/probe5_fixtures")


# ── A. startup and the sandbox ────────────────────────────────────────────────

def test_H1_the_rate_limiter_the_cli_constructs_can_be_constructed():
    """`codoc serve` (cli/main.py:419) builds `RateLimiter()` with no arguments
    whenever GitHub auth is configured."""
    from codoc.serve.ratelimit import RateLimiter
    RateLimiter()


def test_H2_the_sandbox_admits_the_worktree_the_worker_creates(tmp_path):
    """`process_ready` puts the worktree under `<codoc_dir>/worktrees/…`; the
    agent's tools send absolute paths inside it; the policy must admit an
    in-scope file there (and let the agent read it)."""
    cd, ws, ho = ready(tmp_path)
    ws.close()
    calls = []
    run = lambda argv, cwd=None: calls.append(argv) or 0
    process_ready(str(tmp_path), str(cd), run=run, agent=lambda d, w, scope=None: ["c.py"], printer=lambda *_: None)
    wt = [a for a in calls if a[:3] == ["git", "worktree", "add"]][0][-1]
    assert wt.startswith(str(cd))                      # documents where it lives: inside .codoc/
    policy = realize_tool_policy(["c.py"], consult_allowlist=frozenset(), resolve=lambda h: [], root=wt)
    assert policy("Read", {"file_path": f"{wt}/c.py"}) == (True, "")
    assert policy("Edit", {"file_path": f"{wt}/c.py"}) == (True, "")


def test_H3_reads_are_confined_to_the_worktree():
    """A Read/Glob/Grep outside the worktree is how a directive exfiltrates the
    maintainer's tokens (gh's own hosts.yml, ~/.netrc, ~/.pypirc, another
    checkout's .codoc) into an in-scope file and so into the PR diff."""
    wt = "/srv/repo/.codoc/worktrees/codoc-realize-s-9"
    policy = realize_tool_policy(["c.py"], consult_allowlist=frozenset(), resolve=lambda h: [], root=wt)
    for p in [os.path.expanduser("~/.config/gh/hosts.yml"), os.path.expanduser("~/.netrc"), os.path.expanduser("~/.pypirc"),
              "/proc/1234/environ", "/etc/passwd", "/srv/other-repo/.codoc/inbox/ide.jsonl"]:
        assert policy("Read", {"file_path": p})[0] is False, p
    assert policy("Grep", {"pattern": "token", "path": os.path.expanduser("~")})[0] is False
    assert policy("Glob", {"pattern": "**/hosts.yml", "path": "/"})[0] is False


def test_H4_a_transient_failure_is_retried_on_the_next_interval(monkeypatch):
    """serve-deployment.md: 'a git or gh failure is not a refusal and is
    retried'. The worker only calls process_ready when status.json's head
    moved, so nothing retries until somebody else edits the tree."""
    calls = []
    monkeypatch.setattr(worker, "_published_head", lambda cd: 5)
    monkeypatch.setattr(worker, "process_ready", lambda *a, **k: calls.append(time.time()) or [])
    w = Worker("/r", "/r/.codoc", interval=0.01, printer=lambda *_: None).start()
    time.sleep(0.15)
    w.stop()
    assert len(calls) >= 2, f"process_ready ran {len(calls)} time(s) over ~15 intervals with a constant head"


def test_H5_a_sandbox_that_could_not_run_is_not_recorded_as_a_refusal(tmp_path, monkeypatch):
    """`make_sandboxed_agent` fails safe by returning [] when the SDK is missing;
    `realize_one` reads [] as 'no changes produced' and process_ready buries the
    hand-off in the ledger for ever. A hub without the SDK refuses every
    hand-off it will ever see, silently, with the wrong reason."""
    cd, ws, ho = ready(tmp_path)
    ws.close()
    monkeypatch.setattr(realize_agent, "_run_sandboxed_sdk", lambda **k: False)
    agent = make_sandboxed_agent(run_capture=lambda argv, cwd: "")
    out = []
    process_ready(str(tmp_path), str(cd), run=lambda a, cwd=None: 0, agent=agent, printer=out.append)
    ledger = read_ledger(cd)
    assert ho.id not in ledger, f"buried as {ledger.get(ho.id)!r}; printed {out}"


def test_H6_a_source_file_named_like_a_secret_is_editable_when_in_scope():
    assert edit_allowed("tokenizer.py", ["tokenizer.py"])
    assert edit_allowed("auth/password_reset.py", ["auth/password_reset.py"])


# ── B. the contributor's flow (02-workflows §3.6) ─────────────────────────────

def hub_ws(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    write_config(cd, roles={"human:gh:alice": SUGGEST, "human:gh:bob": MAINTAINER})
    ws = Workspace(cd)
    seed(ws)
    return cd, ws


def test_H7a_a_suggesters_typing_is_applied_as_a_plan_of_theirs(tmp_path):
    """02-workflows §3.6: 'a contributor's edit on the trunk is applied as a plan
    with that actor'. The shipped editor never opens a plan and the daemon does
    not wrap one, so every keystroke from the browser is an `authority`
    conflict and the paragraph is reloaded under the person's hands."""
    cd, ws = hub_ws(tmp_path)
    t = ws.store.blocks["p1"].text()
    L.submit(cd, client_name("alice"), [Submission(cid="a1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                                   payload={"after": list(t.last_live_ref()), "text": " More."}, batch="b1")])
    ops = ws.drain_inbox()
    assert [o.conflict for o in ops] == [None]
    assert ops[0].plan is not None                                # …as a plan, not on the trunk


def test_H7b_a_suggester_can_comment(tmp_path):
    """02-workflows §3.6: a `suggest` role 'may only write plan-tagged ops and
    comments'. The policy has no comment clause."""
    cd, ws = hub_ws(tmp_path)
    t = ws.store.blocks["p1"].text()
    L.submit(cd, client_name("alice"), [
        Submission(cid="c1", kind=K.BLOCK_ADD, target={"bid": "cm1"}, payload={"kind": "comment", "after": "p1",
                   "content": {"thread": "cm1", "anchor": "p1", "anchor_text": "Retries", "scope": "code", "status": "open"}}, batch="b2"),
        Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "cm1", "path": "body"}, payload={"after": "START", "text": "why once?"}, batch="b2"),
        Submission(cid="c3", kind=K.TEXT_MARK, target={"bid": "p1", "path": "text"},
                   payload={"kind": "comment", "attrs": {"block": "cm1"}, "start": list(t.ref_at(0)), "end": list(t.ref_at(6)), "on": True}, batch="b2")])
    ops = ws.drain_inbox()
    assert [o.conflict for o in ops] == [None, None, None]


def test_H8_a_revoked_collaborator_cannot_keep_writing(tmp_path):
    """The role is fixed in the session at sign-in and never re-checked; a
    collaborator removed from the repository keeps maintainer authority for the
    rest of the 8-hour session."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    perms = {"alice": "admin"}
    auth = AuthContext(SessionStore(), resolver=FakeResolver(perms), oauth=FakeOAuth())
    c = TestClient(build_app(str(cd), auth=auth), follow_redirects=False)
    state = c.get("/auth/login").headers["location"].split("state=")[1].split("&")[0]
    r = c.get("/auth/callback", params={"code": "good", "state": state})
    c.cookies.set(COOKIE_NAME, r.cookies[COOKIE_NAME])
    perms.pop("alice")                                            # removed from the repo
    r = c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "submit", "subs": [
        {"cid": "x1", "kind": K.PLAN_ACCEPT, "target": {"pid": "p-1"}, "payload": {}, "batch": "b"}]})
    assert r.status_code in (401, 403), r.json()


def test_H9_set_language_refuses_a_tag_that_is_not_a_language(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    c = TestClient(build_app(str(cd)))
    r = c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "set-language", "tag": "x" * 5000})
    assert r.status_code == 400, r.json()
    assert len(json.loads((cd / "config.json").read_text())["doc_language"]) < 40


# ── C. review-12 fixes, re-verified ──────────────────────────────────────────

def test_H10_realize_one_checks_every_exit_code_and_cleans_the_branch():
    req = Request("h1", "s:9", "Client", "Retry twice.", ["c.py"])
    calls = []
    agent_calls = []

    def run(argv, cwd=None):
        calls.append(argv)
        return 1 if argv[:3] == ["git", "worktree", "add"] else 0
    res = realize_one(req, "/tmp/wt", run=run, agent=lambda d, w, scope=None: agent_calls.append(1) or ["c.py"])
    assert not res.ok and res.transient and not agent_calls          # no agent in a worktree git refused
    calls.clear()
    run2 = lambda argv, cwd=None: calls.append(argv) or (1 if argv[0] == "gh" else 0)
    res = realize_one(req, "/tmp/wt", run=run2, agent=lambda d, w, scope=None: ["c.py"])
    assert not res.ok and res.transient
    # cleanup removes the branch too
    calls.clear()
    worker._cleanup("/tmp/wt", "codoc/realize-s-9", lambda argv, cwd=None: calls.append(argv) or 0, "/r")
    assert ["git", "branch", "-D", "codoc/realize-s-9"] in calls


def test_H11_feed_replays_acks_written_before_a_reconnect(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    for i in range(3):
        L.ack(cd, "hub-alice", f"c{i}", None)
    feed = Feed(cd, "hub-alice")                                    # a fresh connection
    acks = [m for m in feed.pending() if m["kind"] == "acks"]
    assert acks and [a["cid"] for a in acks[0]["acks"]] == ["c0", "c1", "c2"]


def test_H12_media_route_is_gated_and_stays_inside_media(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    (cd / "media").mkdir()
    (cd / "media" / "ok.png").write_bytes(b"png")
    (tmp_path / "secret.txt").write_text("s")
    os.symlink(tmp_path / "secret.txt", cd / "media" / "link.txt")
    auth = AuthContext(SessionStore(), resolver=FakeResolver({}), oauth=FakeOAuth())
    c = TestClient(build_app(str(cd), auth=auth))
    assert c.get("/api/media/ok.png").status_code == 401
    c2 = TestClient(build_app(str(cd)))
    assert c2.get("/api/media/ok.png").status_code == 200
    assert c2.get("/api/media/link.txt").status_code == 404
    r = c2.get("/api/media/..%2Fconfig.json")
    assert "doc_language" not in r.text and "roles" not in r.text     # a 200 here is the SPA shell (httpx normalised the path), not the file


# ── D. fixtures for the timeline reader (vitest §D) ──────────────────────────

def _fixture(tmp_path, name, mutate):
    cd = tmp_path / f"{name}.codoc"
    ws = Workspace(cd)
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": "heading", "level": 1, "after": "START"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": "START", "text": "A"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": "h1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": "START", "text": "a-body"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": "paragraph", "after": "p1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": "START", "text": "Gone paragraph"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": "heading", "level": 1, "after": "p2"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": "START", "text": "B"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p3"}, "payload": {"kind": "paragraph", "after": "h2"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p3", "path": "text"}, "payload": {"after": "START", "text": "b-body"}},
    ], actor="human:local")
    mutate(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    FIX.mkdir(exist_ok=True)
    shutil.copy(cd / "history.json", FIX / f"{name}.history.json")
    shutil.copy(cd / "projection.json", FIX / f"{name}.projection.json")
    w = history_window(ws.store)
    ws.close()
    return w


def test_H13_fixture_delete(tmp_path):
    w = _fixture(tmp_path, "delete", lambda ws: (
        ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "p2"}, "payload": {}}], actor="human:local"),
        ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p3", "path": "text"}, "payload": {"after": list(ws.store.blocks["p3"].text().last_live_ref()), "text": " later"}}], actor="human:local")))
    dele = [e for e in w["ops"] if e["kind"] == "block.delete"][0]
    # What the window says a deleted block said "right after the op": its whole text.
    # A reader that trusts this shows the paragraph on every page after its deletion.
    assert dele["blocks"].get("p2") in (None, ""), dele["blocks"]


def test_H14_fixture_merge(tmp_path):
    w = _fixture(tmp_path, "merge", lambda ws: (
        ws.submit([{"kind": K.BLOCK_MERGE, "target": {"into": "p1", "bid": "p2", "path": "text"}, "payload": {}}], actor="human:local"),
        ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p3", "path": "text"}, "payload": {"after": list(ws.store.blocks["p3"].text().last_live_ref()), "text": " later"}}], actor="human:local")))
    assert any(e["kind"] == "block.merge" for e in w["ops"])


def test_H15_fixture_move(tmp_path):
    w = _fixture(tmp_path, "move", lambda ws: (
        ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "p2"}, "payload": {}}], actor="human:local"),
        ws.submit([{"kind": K.BLOCK_MOVE, "target": {"bid": "h2"}, "payload": {"after": "START"}}], actor="human:local"),
        ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p3", "path": "text"}, "payload": {"after": list(ws.store.blocks["p3"].text().last_live_ref()), "text": " later"}}], actor="human:local")))
    assert any(e["kind"] == "block.move" for e in w["ops"])
    # `ranks` is today's rank per block, so a page before the move is in today's order (09-editor §7 wants the order it had).
    assert set(w["ranks"]) >= {"h1", "p3"}


def test_H16_a_batch_the_daemon_refused_whole_is_visible_to_the_client_in_some_channel(tmp_path):
    """Context for vitest E11: app.ts waits for every submitted batch to show in
    the window before it will undo. The window drops conflicted ops, so a batch
    refused whole never arrives and the person's ⌘Z is dead for the session."""
    cd, ws = hub_ws(tmp_path)
    t = ws.store.blocks["p1"].text()
    L.submit(cd, client_name("alice"), [Submission(cid="z1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                                   payload={"after": list(t.last_live_ref()), "text": " More."}, batch="b-refused")])
    ops = ws.drain_inbox()
    assert ops[0].conflict == "authority"
    w = history_window(ws.store)
    assert "b-refused" not in {e["batch"] for e in w["ops"]}      # a fact, not a fault: the client must not wait on it
