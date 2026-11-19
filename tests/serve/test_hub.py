"""The hub: sessions and roles, submissions to a per-login inbox, the SSE feed."""
import json

from fastapi.testclient import TestClient

from codoc.calculus import ops as K
from codoc.daemon.publish import publish
from codoc.serve.app import build_app, client_name
from codoc.serve.auth import (
    COOKIE_NAME,
    MAINTAINER,
    SUGGEST,
    AuthContext,
    SessionStore,
    role_for_permission,
)
from codoc.serve.push import Feed
from codoc.workspace import Roles, Workspace
from codoc.workspace import inbox, log as L
from codoc.workspace.inbox import Submission
from tests.helpers import seed_loop


class FakeOAuth:
    def authorize_url(self, *, state, redirect_uri):
        return f"https://github.test/login?state={state}&redirect_uri={redirect_uri}"

    def exchange_code(self, code, *, redirect_uri):
        return "tok" if code == "good" else None

    def fetch_login(self, token):
        return "alice" if token == "tok" else None


class FakeResolver:
    def __init__(self, perms):
        self.perms = perms

    def permission(self, login):
        return self.perms.get(login)


def workspace(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed_loop(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    return cd


def test_roles_from_permissions():
    assert role_for_permission("admin") == role_for_permission("write") == MAINTAINER
    assert role_for_permission("read") == role_for_permission("triage") == SUGGEST
    assert role_for_permission(None) == role_for_permission("weird") == "none"


def test_local_hub_treats_its_viewer_as_the_owner_and_routes_submissions(tmp_path):
    cd = workspace(tmp_path)
    c = TestClient(build_app(str(cd)))
    assert c.get("/api/whoami").json() == {"authenticated": True, "login": "local", "role": MAINTAINER}
    assert c.post("/api/message", json={"kind": "submit", "subs": []}).status_code == 403     # no CSRF header
    r = c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "submit", "subs": [
        {"cid": "c1", "kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": "START", "text": "Hi "}, "batch": "b1"}]})
    assert r.json() == {"ok": True, "submitted": 1}
    lines = inbox.read_inbox(cd, "cli")      # the local hub's viewer is the person at the machine
    assert len(lines) == 1 and lines[0].sub.cid == "c1" and lines[0].sub.kind == K.TEXT_INSERT
    assert c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "set-language", "tag": "ja"}).json() == {"ok": True}
    assert json.loads((cd / "config.json").read_text())["doc_language"] == "ja"
    for kind, says in (("realize", "no terminal"), ("open-cite", "code opens in the editor")):
        r = c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": kind})
        assert r.status_code == 202 and says in r.json()["error"], (kind, r.json())
    assert c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "sync"}).status_code == 400
    h = c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "history"}).json()
    assert h["kind"] == "history" and h["history"]["head"] == len(L.read_session(L.list_sessions(cd)[0]).ops)


def test_auth_gate_records_roles_and_a_suggester_cannot_set_the_language(tmp_path):
    cd = workspace(tmp_path)
    auth = AuthContext(SessionStore(), resolver=FakeResolver({"alice": "read", "bob": "admin"}), oauth=FakeOAuth())
    c = TestClient(build_app(str(cd), auth=auth), follow_redirects=False)
    assert c.get("/api/whoami").json()["authenticated"] is False
    assert c.get("/api/events").status_code == 401
    assert "Sign in" in c.get("/").text
    r = c.get("/auth/login")
    assert r.status_code == 302 and "github.test" in r.headers["location"]
    state = r.headers["location"].split("state=")[1].split("&")[0]
    assert c.get("/auth/callback", params={"code": "good", "state": "wrong"}).status_code == 400
    r = c.get("/auth/callback", params={"code": "good", "state": state})
    assert r.status_code == 302 and COOKIE_NAME in r.cookies
    c.cookies.set(COOKIE_NAME, r.cookies[COOKIE_NAME])
    assert c.get("/api/whoami").json() == {"authenticated": True, "login": "alice", "role": SUGGEST}
    # The actor is namespaced: a GitHub login called "local" can never arrive
    # as the maintainer's own identity, here or in the roles table.
    assert Roles.load(cd).table == {"human:gh:alice": SUGGEST}
    assert json.loads((cd / "config.json").read_text())["roles"] == {"human:gh:alice": SUGGEST}
    assert c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "set-language", "tag": "ja"}).status_code == 403
    r = c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "submit", "subs": [
        {"cid": "s1", "kind": K.PLAN_OPEN, "target": {"pid": "p-1"}, "payload": {"kind": "edit"}}]})
    assert r.json()["ok"] and inbox.read_inbox(cd, client_name("alice"))[0].sub.kind == K.PLAN_OPEN
    # A maintainer signing in later gets the maintainer role beside alice's.
    s = auth.store.create("bob", role_for_permission("admin"))
    from codoc.serve.auth import record_role
    record_role(cd, "bob", s.role)
    assert Roles.load(cd).table == {"human:gh:alice": SUGGEST, "human:gh:bob": MAINTAINER}
    assert inbox.actor_for(client_name("local")) == "human:gh:local" != "human:local"


def test_feed_sends_a_snapshot_then_only_changes_and_this_viewers_acks(tmp_path):
    cd = workspace(tmp_path)
    feed = Feed(cd, "cli")
    first = feed.pending()
    assert [m["kind"] for m in first] == ["projection", "status"]
    assert first[1]["status"]["doc_language"] == "en" and first[1]["status"]["actor"] == "human:local"
    assert first[1]["status"]["terminal"] is False         # the page offers no Build now on the hub
    assert feed.pending() == []
    inbox.ack(cd, "cli", "c1", None, error="unknown_anchor")
    inbox.ack(cd, "hub-other", "c9", None)
    got = feed.pending()
    assert got == [{"kind": "acks", "acks": [{"cid": "c1", "op": "", "conflict": "unknown_anchor"}]}]
    (cd / "config.json").write_text(json.dumps({"doc_language": "ja"}))
    got = feed.pending()
    assert [m["kind"] for m in got] == ["status"] and got[0]["status"]["doc_language"] == "ja"


def test_a_viewer_keeps_receiving_acks_after_the_daemon_trims_the_ack_file(tmp_path):
    """`rotate_acks` rewrites the ack file to its newest lines at every daemon
    start, and both tails (this one and `provider.ts`'s `readFrom`) follow it
    by byte offset. A file shorter than where a tail stopped is read again from
    the start, so acks keep arriving and held blocks are released."""
    cd = workspace(tmp_path)
    for i in range(2100):
        inbox.ack(cd, "hub-alice", f"c{i}", None)
    feed = Feed(cd, "hub-alice")
    feed.pending()
    inbox.rotate_acks(cd, keep=2000)                        # the daemon restarted
    inbox.ack(cd, "hub-alice", "fresh", None)
    got = [a["cid"] for m in feed.pending() if m["kind"] == "acks" for a in m["acks"]]
    assert "fresh" in got, got


def test_the_feed_sends_acks_before_the_projection_that_followed_them(tmp_path):
    """Disk order: the daemon acks during the drain and publishes afterwards.
    The editor skips a held block when it adopts, and the ack is what lifts the
    hold, so the feed sends the acks first and the block is free when its
    projection lands."""
    cd = workspace(tmp_path)
    feed = Feed(cd, "ide")
    feed.pending()
    ws = Workspace(cd)
    t = ws.store.blocks["p1"].text()
    inbox.submit(cd, "ide", [Submission(cid="k1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={"after": list(t.last_live_ref()), "text": " More."}, batch="b1")])
    ws.drain_inbox()
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    kinds = [m["kind"] for m in feed.pending()]
    assert kinds.index("acks") < kinds.index("projection"), kinds


def test_a_contributor_and_the_person_edit_one_checkout_at_once(tmp_path):
    """The workflow the hub exists for: a suggester proposes from the browser
    while the person types in VS Code, and only a maintainer's verdict lands it."""
    from codoc.calculus.derive import markdown, phase, plan_claims
    from codoc.daemon.publish import Publisher
    from codoc.workspace.config import write_config

    cd = tmp_path / ".codoc"
    cd.mkdir()
    write_config(cd, roles={"human:gh:alice": SUGGEST, "human:gh:bob": MAINTAINER})
    ws = Workspace(cd)
    seed_loop(ws)
    t = ws.store.blocks["p1"].text()
    inbox.submit(cd, "ide", [Submission(cid="i1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={"after": list(t.last_live_ref()), "text": " Mine."}, batch="b-ide")])
    pid = "p-alice"
    inbox.submit(cd, client_name("alice"), [
        Submission(cid="a1", kind=K.PLAN_OPEN, target={"pid": pid}, payload={"kind": "edit", "target": "p1"}, batch=pid),
        Submission(cid="a2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                   payload={"after": list(t.ref_at(7)), "text": "twice not "}, plan=pid, batch=pid),
        Submission(cid="a3", kind=K.PLAN_ACCEPT, target={"pid": pid}, payload={}, batch=pid)])
    ops = ws.drain_inbox()
    assert [(o.actor, o.kind, o.conflict) for o in ops] == [
        ("human:local", K.TEXT_INSERT, None),                 # appended first, applied first
        ("human:gh:alice", K.PLAN_OPEN, None), ("human:gh:alice", K.TEXT_INSERT, None),
        ("human:gh:alice", K.PLAN_ACCEPT, "authority")]       # a suggester may not decide
    assert markdown(ws.store.blocks["p1"].text(), "rejected", ws.store) == "Retries once before giving up. Mine."
    assert [(c.stage, c.text) for c in plan_claims(ws.store, "h1")] == [("open", "twice not ")]
    assert phase(ws.store, "h1") == "edited"                   # the person's unsent words win the badge
    acks = [json.loads(l) for l in (inbox.inbox_dir(cd) / f"{client_name('alice')}.ack.jsonl").read_text().splitlines()]
    assert [a["conflict"] for a in acks] == [None, None, "authority"]
    inbox.submit(cd, client_name("bob"), [Submission(cid="b1", kind=K.PLAN_ACCEPT, target={"pid": pid}, payload={}, batch="b-bob")])
    ws.drain_inbox()
    assert ws.store.plans[pid].status == "accepted"
    assert ("human:gh:alice", "twice not ") in [(s.actor, s.chars) for s in ws.store.blocks["p1"].text().segs if s.live]
    assert Publisher(cd, str(tmp_path)).publish(ws.store, force=True)["state"] == "edited"
    ws.close()


def test_a_revoked_collaborator_loses_the_session_at_the_next_recheck(tmp_path):
    """A role is decided at sign-in and a sign-in lasts a working day, so
    revoking somebody's access has to reach a tab that is already open."""
    cd = workspace(tmp_path)
    perms = {"alice": "admin"}
    auth = AuthContext(SessionStore(), resolver=FakeResolver(perms), oauth=FakeOAuth(), recheck_seconds=0)
    c = TestClient(build_app(str(cd), auth=auth), follow_redirects=False)
    state = c.get("/auth/login").headers["location"].split("state=")[1].split("&")[0]
    r = c.get("/auth/callback", params={"code": "good", "state": state})
    c.cookies.set(COOKIE_NAME, r.cookies[COOKIE_NAME])
    assert c.get("/api/whoami").json()["role"] == MAINTAINER
    perms["alice"] = "read"                       # demoted
    assert c.get("/api/whoami").json()["role"] == SUGGEST
    perms.pop("alice")                            # removed from the repository
    assert c.get("/api/whoami").json()["authenticated"] is False
    assert c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "submit", "subs": [
        {"cid": "x1", "kind": K.PLAN_ACCEPT, "target": {"pid": "p-1"}, "payload": {}, "batch": "b"}]}).status_code == 401


def test_a_live_session_is_not_re_asked_on_every_request(tmp_path):
    """The check costs a GitHub call, and a person types many times a minute."""
    cd = workspace(tmp_path)
    calls = []

    class Counting(FakeResolver):
        def permission(self, login):
            calls.append(login)
            return super().permission(login)

    auth = AuthContext(SessionStore(), resolver=Counting({"alice": "admin"}), oauth=FakeOAuth())
    c = TestClient(build_app(str(cd), auth=auth), follow_redirects=False)
    state = c.get("/auth/login").headers["location"].split("state=")[1].split("&")[0]
    r = c.get("/auth/callback", params={"code": "good", "state": state})
    c.cookies.set(COOKIE_NAME, r.cookies[COOKIE_NAME])
    for _ in range(5):
        c.get("/api/whoami")
    assert len(calls) == 1, calls


def test_set_language_takes_a_tag_and_not_a_string(tmp_path):
    """The tree's language is written into a tracked file every clone reads."""
    cd = workspace(tmp_path)
    c = TestClient(build_app(str(cd)))
    bad = c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "set-language", "tag": "x" * 5000})
    assert bad.status_code == 400 and "config.json" not in [p.name for p in cd.iterdir()]
    assert c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "set-language", "tag": "zh"}).json() == {"ok": True}
    assert json.loads((cd / "config.json").read_text())["doc_language"] == "zh-Hans"     # as `codoc lang zh` writes it



def hub_ws(tmp_path):
    """A workspace whose tracked table names a contributor and a maintainer."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    (cd / "config.json").write_text(json.dumps({"roles": {"human:gh:alice": SUGGEST, "human:gh:bob": MAINTAINER}}))
    ws = Workspace(cd)
    seed_loop(ws)
    return cd, ws


def test_a_contributor_cannot_resolve_or_rewrite_somebody_elses_comment_thread(tmp_path):
    """A comment is writable untagged by anyone, because a thread is
    something to answer rather than accept. Inside somebody else's thread a
    `suggest` login may only reply: it may not resolve the thread or rewrite
    the words of the request."""
    cd, ws = hub_ws(tmp_path)
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "c1"},
         "payload": {"kind": "comment", "after": "p1", "content": {"thread": "c1", "anchor": "p1",
                                                                   "anchor_text": "Retries", "scope": "code", "status": "open"}}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "c1", "path": "body"}, "payload": {"after": "START", "text": "make it retry twice"}},
    ], actor="human:gh:bob")
    body = ws.store.blocks["c1"].content["body"]
    inbox.submit(cd, client_name("alice"), [
        Submission(cid="r1", kind=K.BLOCK_SET, target={"bid": "c1", "path": "status"}, payload={"value": "resolved"}, batch="b"),
        Submission(cid="r2", kind=K.TEXT_DELETE, target={"bid": "c1", "path": "body"},
                   payload={"from": list(body.ref_at(0)), "to": list(body.last_live_ref())}, batch="b"),
    ])
    assert [o.conflict for o in ws.drain_inbox()] == ["authority", "authority"]
    assert ws.store.blocks["c1"].content.get("status") == "open"
    # …while a reply of their own, and closing a thread they started, both stand.
    inbox.submit(cd, client_name("alice"), [
        Submission(cid="r3", kind=K.BLOCK_ADD, target={"bid": "c2"},
                   payload={"kind": "comment", "after": "c1", "content": {"thread": "c1", "anchor": "p1",
                                                                          "anchor_text": "Retries", "scope": "code", "status": "open"}}, batch="b2"),
        Submission(cid="r4", kind=K.TEXT_INSERT, target={"bid": "c2", "path": "body"},
                   payload={"after": "START", "text": "on it"}, batch="b2"),
        Submission(cid="r5", kind=K.BLOCK_ADD, target={"bid": "c3"},
                   payload={"kind": "comment", "after": "p1", "content": {"thread": "c3", "anchor": "p1",
                                                                          "anchor_text": "Retries", "scope": "code", "status": "open"}}, batch="b3"),
        Submission(cid="r6", kind=K.BLOCK_SET, target={"bid": "c3", "path": "status"}, payload={"value": "resolved"}, batch="b3"),
    ])
    assert [o.conflict for o in ws.drain_inbox()] == [None, None, None, None]
    ws.close()


def test_a_revoked_collaborator_recorded_as_none_is_not_a_suggester(tmp_path):
    """`auth.session` writes `none` to the tracked table on revocation, and
    `none` may write nothing, not even what a suggester may. Their session is
    gone, but an inbox line drained after the revocation, and a clone
    validating their pull request's logs, both go through the policy."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    (cd / "config.json").write_text(json.dumps({"roles": {"human:gh:eve": "none"}}))
    ws = Workspace(cd)
    seed_loop(ws)
    inbox.submit(cd, client_name("eve"), [
        Submission(cid="e1", kind=K.PLAN_OPEN, target={"pid": "pl-eve"},
                   payload={"kind": "edit", "target": "p1", "note": "hi"}, batch="pl-eve"),
        Submission(cid="e2", kind=K.BLOCK_ADD, target={"bid": "ce"},
                   payload={"kind": "comment", "after": "p1", "content": {"thread": "ce", "anchor": "p1",
                                                                          "anchor_text": "", "scope": "code", "status": "open"}}, batch="b"),
    ])
    assert all(o.conflict == "authority" for o in ws.drain_inbox())
    ws.close()


def test_a_revoked_collaborators_open_stream_stops_carrying_the_tree(tmp_path):
    """A stream is a long-lived read of the whole tree, so `/api/events`
    re-checks the role while it runs: a collaborator taken off the repository
    stops receiving projections, although the browser would reopen the tab's
    stream by itself."""
    import asyncio

    from codoc.serve.auth import NONE, record_role
    from codoc.serve.push import event_source
    cd = workspace(tmp_path)
    (cd / "config.json").write_text(json.dumps({"roles": {"human:gh:eve": SUGGEST}}))

    async def run():
        async def connected():
            return False
        gen = event_source(cd, client_name("eve"), is_disconnected=connected, poll_s=0.01)
        assert [(await asyncio.wait_for(gen.__anext__(), 2))["event"] for _ in range(2)] == ["projection", "status"]
        record_role(cd, "eve", NONE)                      # the recheck found them removed
        ws = Workspace(cd)
        t = ws.store.blocks["p1"].text()
        ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                    "payload": {"after": list(t.last_live_ref()), "text": " More."}}], actor="human:local")
        publish(cd, ws.store, root_dir=str(tmp_path))
        ws.close()
        try:
            return (await asyncio.wait_for(gen.__anext__(), 2))["event"]
        except StopAsyncIteration:
            return None

    assert asyncio.run(run()) is None


def test_one_message_may_not_carry_an_unbounded_number_of_submissions(tmp_path):
    """The rate limit counts requests, and one POST is one request however
    much it carries, so a POST may carry at most `MAX_SUBS` submissions."""
    from codoc.serve.app import MAX_SUBS
    cd = workspace(tmp_path)
    c = TestClient(build_app(str(cd)))
    one = {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": "START", "text": "x"}}
    ok = c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "submit", "subs": [dict(one, cid=f"c{i}") for i in range(MAX_SUBS)]})
    assert ok.status_code == 200
    too_many = c.post("/api/message", headers={"x-codoc-csrf": "1"},
                      json={"kind": "submit", "subs": [dict(one, cid=f"d{i}") for i in range(MAX_SUBS + 1)]})
    assert too_many.status_code == 413
