"""U1 — the hub HTTP app skeleton: health endpoint + SPA catch-all."""
from __future__ import annotations

from fastapi.testclient import TestClient

from codoc.serve.app import build_app


def test_healthz_ok(tmp_path):
    client = TestClient(build_app(str(tmp_path)))
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "codoc-serve"


def test_catch_all_serves_placeholder_when_no_spa(tmp_path):
    client = TestClient(build_app(str(tmp_path)))
    r = client.get("/some/deep/spa/route")
    assert r.status_code == 200
    assert "codoc serve" in r.text


def test_catch_all_serves_index_when_spa_present(tmp_path):
    spa = tmp_path / "web"
    spa.mkdir()
    (spa / "index.html").write_text("<html><body>EDITOR BUNDLE</body></html>")
    client = TestClient(build_app(str(tmp_path), static_dir=str(spa)))
    r = client.get("/anything")
    assert r.status_code == 200
    assert "EDITOR BUNDLE" in r.text


def test_healthz_not_shadowed_by_catch_all(tmp_path):
    spa = tmp_path / "web"
    spa.mkdir()
    (spa / "index.html").write_text("INDEX")
    client = TestClient(build_app(str(tmp_path), static_dir=str(spa)))
    assert client.get("/healthz").json()["ok"] is True


def test_api_payload_returns_snapshot(tmp_path):
    import json

    from codoc.model.hlc import HLC

    cd = tmp_path / ".codoc"
    cd.mkdir(parents=True)
    (cd / "tree.bindings.json").write_text(json.dumps(
        {"features": {"f": {"title": "X", "parent_id": None}}, "by_feature": {}}))
    (cd / "status.json").write_text(json.dumps(
        {"state": "in_sync", "pending": 0, "at": HLC.now().to_str()}))
    client = TestClient(build_app(str(cd)))
    r = client.get("/api/payload")
    assert r.status_code == 200
    body = r.json()
    assert "f" in body["nodes"]
    assert body["rev"] > 0


def test_whoami_reflects_session_capability(tmp_path):
    from codoc.serve.auth import AuthContext, COOKIE_NAME, Capability, SessionStore

    store = SessionStore()
    session = store.create("maya", Capability.SUGGEST)
    client = TestClient(build_app(str(tmp_path), auth=AuthContext(store=store)))

    assert client.get("/api/whoami").json()["authenticated"] is False

    client.cookies.set(COOKIE_NAME, session.sid)
    body = client.get("/api/whoami").json()
    assert body == {"authenticated": True, "login": "maya", "capability": "suggest"}


def test_whoami_absent_without_auth(tmp_path):
    # No AuthContext → the route isn't registered (catch-all serves the SPA).
    client = TestClient(build_app(str(tmp_path)))
    r = client.get("/api/whoami")
    assert "codoc serve" in r.text  # placeholder, not a JSON whoami


def test_command_route_enforces_csrf_and_capability(tmp_path):
    from codoc.loop import inbox
    from codoc.serve.auth import COOKIE_NAME, AuthContext, Capability, SessionStore

    store = SessionStore()
    suggester = store.create("maya", Capability.SUGGEST)
    maintainer = store.create("ryan", Capability.HANDOFF)
    cd = tmp_path / ".codoc"
    cd.mkdir(parents=True)
    client = TestClient(build_app(str(cd), auth=AuthContext(store=store)))

    # missing CSRF header → 403 even with a valid maintainer session
    client.cookies.set(COOKIE_NAME, maintainer.sid)
    assert client.post("/api/command", json={"kind": "hand-off"}).status_code == 403

    # suggester + csrf + verdict → 403 (capability gate)
    client.cookies.set(COOKIE_NAME, suggester.sid)
    r = client.post("/api/command",
                    json={"kind": "verdict", "eventIds": ["e-1"], "accept": True},
                    headers={"x-codoc-csrf": "1"})
    assert r.status_code == 403
    assert inbox.read_verdicts(str(cd)) == []

    # maintainer + csrf + verdict → 200, written
    client.cookies.set(COOKIE_NAME, maintainer.sid)
    r = client.post("/api/command",
                    json={"kind": "verdict", "eventIds": ["e-9"], "accept": True},
                    headers={"x-codoc-csrf": "1"})
    assert r.status_code == 200
    assert {v.event_id for v in inbox.read_verdicts(str(cd))} == {"e-9"}


def test_command_route_rate_limits(tmp_path):
    from codoc.serve.auth import COOKIE_NAME, AuthContext, Capability, SessionStore
    from codoc.serve.ratelimit import RateLimiter

    store = SessionStore()
    session = store.create("maya", Capability.SUGGEST)
    limiter = RateLimiter(capacity=2, refill_per_sec=0.0001)  # ~no refill in-test
    cd = tmp_path / ".codoc"
    cd.mkdir(parents=True)
    client = TestClient(build_app(str(cd), auth=AuthContext(store=store), rate_limiter=limiter))
    client.cookies.set(COOKIE_NAME, session.sid)

    body = {"kind": "comment-create", "thread": {"featureId": "f", "body": "x", "id": "c"}}
    headers = {"x-codoc-csrf": "1"}
    assert client.post("/api/command", json=body, headers=headers).status_code == 200
    assert client.post("/api/command", json=body, headers=headers).status_code == 200
    assert client.post("/api/command", json=body, headers=headers).status_code == 429
