"""The hub's authorization GATE on the read endpoints + the sign-in flow (app.py).

The security fix under test: with an AuthContext present, /api/payload, /api/media,
and /api/events require a valid session — they are NOT public. Without auth (the
localhost-only posture) they stay open, which the existing test_app.py covers.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from codoc.serve.app import build_app
from codoc.serve.auth import AuthContext, COOKIE_NAME, Capability, SessionStore


def _cd(tmp_path):
    cd = tmp_path / ".codoc"
    (cd / "media").mkdir(parents=True)
    (cd / "media" / "m.png").write_bytes(b"\x89PNG")
    (cd / "tree.bindings.json").write_text(json.dumps(
        {"features": {"f": {"title": "X", "parent_id": None}}, "by_feature": {}}))
    from codoc.model.hlc import HLC
    (cd / "status.json").write_text(json.dumps(
        {"state": "in_sync", "pending": 0, "at": HLC.now().to_str()}))
    return cd


def test_reads_require_a_session_when_auth_is_configured(tmp_path):
    cd = _cd(tmp_path)
    store = SessionStore()
    client = TestClient(build_app(str(cd), auth=AuthContext(store=store)))

    # No cookie → every read endpoint is refused (401), NOT public.
    assert client.get("/api/payload").status_code == 401
    assert client.get("/api/media/m.png").status_code == 401
    # media bytes never leak to an unauthenticated caller
    assert b"PNG" not in client.get("/api/media/m.png").content

    # A valid collaborator session → reads succeed.
    session = store.create("maya", Capability.SUGGEST)
    client.cookies.set(COOKIE_NAME, session.sid)
    assert client.get("/api/payload").status_code == 200
    assert client.get("/api/media/m.png").status_code == 200


def test_none_capability_session_is_still_refused(tmp_path):
    cd = _cd(tmp_path)
    store = SessionStore()
    session = store.create("stranger", Capability.NONE)
    client = TestClient(build_app(str(cd), auth=AuthContext(store=store)))
    client.cookies.set(COOKIE_NAME, session.sid)
    assert client.get("/api/payload").status_code == 401


def test_reads_open_without_auth_localhost_mode(tmp_path):
    # Regression: the localhost-only posture (auth=None) keeps reads open.
    cd = _cd(tmp_path)
    client = TestClient(build_app(str(cd)))
    assert client.get("/api/payload").status_code == 200
    assert client.get("/api/media/m.png").status_code == 200


class _FakeOAuth:
    def authorize_url(self, *, state, redirect_uri):
        return f"https://github.test/login/oauth/authorize?state={state}&redirect_uri={redirect_uri}"

    def exchange_code(self, code, *, redirect_uri):
        return "user-token" if code == "good-code" else None

    def fetch_login(self, token):
        return "maya" if token == "user-token" else None


class _FakeResolver:
    def __init__(self, perm):
        self._perm = perm

    def permission(self, login):
        return self._perm


def test_login_redirects_and_sets_state_cookie(tmp_path):
    ctx = AuthContext(store=SessionStore(), resolver=_FakeResolver("read"), oauth=_FakeOAuth())
    client = TestClient(build_app(str(_cd(tmp_path)), auth=ctx))
    r = client.get("/auth/login", follow_redirects=False)
    assert r.status_code == 302
    assert "github.test/login/oauth/authorize" in r.headers["location"]
    assert "codoc_oauth_state" in r.cookies


def test_callback_establishes_a_session_for_a_collaborator(tmp_path):
    store = SessionStore()
    ctx = AuthContext(store=store, resolver=_FakeResolver("write"), oauth=_FakeOAuth())
    client = TestClient(build_app(str(_cd(tmp_path)), auth=ctx))

    # Seed the state cookie the callback validates (mirrors what /auth/login set).
    client.cookies.set("codoc_oauth_state", "s1")
    r = client.get("/auth/callback?code=good-code&state=s1", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    sid = r.cookies.get(COOKIE_NAME)
    assert sid and store.get(sid).login == "maya"
    assert store.get(sid).capability is Capability.HANDOFF


def test_callback_rejects_state_mismatch_csrf(tmp_path):
    ctx = AuthContext(store=SessionStore(), resolver=_FakeResolver("write"), oauth=_FakeOAuth())
    client = TestClient(build_app(str(_cd(tmp_path)), auth=ctx))
    client.cookies.set("codoc_oauth_state", "expected")
    r = client.get("/auth/callback?code=good-code&state=ATTACKER", follow_redirects=False)
    assert r.status_code == 400


def test_callback_denies_non_collaborator(tmp_path):
    ctx = AuthContext(store=SessionStore(), resolver=_FakeResolver(None), oauth=_FakeOAuth())
    client = TestClient(build_app(str(_cd(tmp_path)), auth=ctx))
    client.cookies.set("codoc_oauth_state", "s1")
    r = client.get("/auth/callback?code=good-code&state=s1", follow_redirects=False)
    assert r.status_code == 403


def test_logout_revokes_the_session(tmp_path):
    store = SessionStore()
    session = store.create("maya", Capability.SUGGEST)
    ctx = AuthContext(store=store, resolver=_FakeResolver("read"), oauth=_FakeOAuth())
    client = TestClient(build_app(str(_cd(tmp_path)), auth=ctx))
    client.cookies.set(COOKIE_NAME, session.sid)
    assert client.post("/api/logout").status_code == 200
    assert store.get(session.sid) is None
