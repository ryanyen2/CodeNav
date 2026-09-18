"""The hub: sessions and roles, submissions to a per-login inbox, the SSE feed."""
import json

from fastapi.testclient import TestClient

from codoc.calculus import ops as K
from codoc.serve.app import build_app, client_name
from codoc.serve.auth import COOKIE_NAME, MAINTAINER, SUGGEST, AuthContext, SessionStore, read_roles, role_for_permission
from codoc.serve.push import Feed
from codoc.substrate import log as L
from codoc.substrate.log import Submission
from codoc.substrate.publish import publish
from codoc.substrate.workspace import Workspace
from tests.loops.test_reflect import seed


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
    seed(ws)
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
    lines = L.read_inbox(cd, "cli")      # the local hub's viewer is the person at the machine
    assert len(lines) == 1 and lines[0].sub.cid == "c1" and lines[0].sub.kind == K.TEXT_INSERT
    assert c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "set-language", "tag": "ja"}).json() == {"ok": True}
    assert json.loads((cd / "config.json").read_text())["doc_language"] == "ja"
    assert c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "realize"}).status_code == 202
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
    assert read_roles(cd) == {"human:gh:alice": SUGGEST}
    assert json.loads((cd / "config.json").read_text())["roles"] == {"human:gh:alice": SUGGEST}
    assert c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "set-language", "tag": "ja"}).status_code == 403
    r = c.post("/api/message", headers={"x-codoc-csrf": "1"}, json={"kind": "submit", "subs": [
        {"cid": "s1", "kind": K.PLAN_OPEN, "target": {"pid": "p-1"}, "payload": {"kind": "edit"}}]})
    assert r.json()["ok"] and L.read_inbox(cd, client_name("alice"))[0].sub.kind == K.PLAN_OPEN
    # A maintainer signing in later gets the maintainer role beside alice's.
    s = auth.store.create("bob", role_for_permission("admin"))
    from codoc.serve.auth import record_role
    record_role(cd, "bob", s.role)
    assert read_roles(cd) == {"human:gh:alice": SUGGEST, "human:gh:bob": MAINTAINER}
    assert L.actor_for(client_name("local")) == "human:gh:local" != "human:local"


def test_feed_sends_a_snapshot_then_only_changes_and_this_viewers_acks(tmp_path):
    cd = workspace(tmp_path)
    feed = Feed(cd, "cli")
    first = feed.pending()
    assert [m["kind"] for m in first] == ["projection", "status"]
    assert first[1]["status"]["doc_language"] == "en" and first[1]["status"]["actor"] == "human:local"
    assert feed.pending() == []
    L.ack(cd, "cli", "c1", None, error="unknown_anchor")
    L.ack(cd, "hub-other", "c9", None)
    got = feed.pending()
    assert got == [{"kind": "acks", "acks": [{"cid": "c1", "op": "", "conflict": "unknown_anchor"}]}]
    (cd / "config.json").write_text(json.dumps({"doc_language": "ja"}))
    got = feed.pending()
    assert [m["kind"] for m in got] == ["status"] and got[0]["status"]["doc_language"] == "ja"


def test_a_contributor_and_the_person_edit_one_checkout_at_once(tmp_path):
    """The workflow the hub exists for: a suggester proposes from the browser
    while the person types in VS Code, and only a maintainer's verdict lands it."""
    from codoc.calculus.derive import markdown, phase, plan_claims
    from codoc.calculus.ids import START
    from codoc.doclang import write_config
    from codoc.substrate.publish import Publisher

    cd = tmp_path / ".codoc"
    cd.mkdir()
    write_config(cd, roles={"human:gh:alice": SUGGEST, "human:gh:bob": MAINTAINER})
    ws = Workspace(cd)
    seed(ws)
    t = ws.store.blocks["p1"].text()
    L.submit(cd, "ide", [Submission(cid="i1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={"after": list(t.last_live_ref()), "text": " Mine."}, batch="b-ide")])
    pid = "p-alice"
    L.submit(cd, client_name("alice"), [
        Submission(cid="a1", kind=K.PLAN_OPEN, target={"pid": pid}, payload={"kind": "edit", "target": "p1"}, batch=pid),
        Submission(cid="a2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                   payload={"after": list(t.ref_at(7)), "text": "twice not "}, plan=pid, batch=pid),
        Submission(cid="a3", kind=K.PLAN_ACCEPT, target={"pid": pid}, payload={}, batch=pid)])
    ops = ws.drain_inbox()
    assert [(o.actor, o.kind, o.conflict) for o in ops] == [
        ("human:gh:alice", K.PLAN_OPEN, None), ("human:gh:alice", K.TEXT_INSERT, None),
        ("human:gh:alice", K.PLAN_ACCEPT, "authority"),       # a suggester may not decide
        ("human:local", K.TEXT_INSERT, None)]
    assert markdown(ws.store.blocks["p1"].text(), "rejected", ws.store) == "Retries once before giving up. Mine."
    assert [(c.stage, c.text) for c in plan_claims(ws.store, "h1")] == [("open", "twice not ")]
    assert phase(ws.store, "h1") == "edited"                   # the person's unsent words win the badge
    acks = [json.loads(l) for l in (L.inbox_dir(cd) / f"{client_name('alice')}.ack.jsonl").read_text().splitlines()]
    assert [a["conflict"] for a in acks] == [None, None, "authority"]
    L.submit(cd, client_name("bob"), [Submission(cid="b1", kind=K.PLAN_ACCEPT, target={"pid": pid}, payload={}, batch="b-bob")])
    ws.drain_inbox()
    assert ws.store.plans[pid].status == "accepted"
    assert ("human:gh:alice", "twice not ") in [(s.actor, s.chars) for s in ws.store.blocks["p1"].text().segs if s.live]
    assert Publisher(cd, str(tmp_path)).publish(ws.store, force=True)["state"] == "edited"
    ws.close()
