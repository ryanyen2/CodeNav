"""The hub tells each viewer what it may do — per connection, never shared.

The hub has always enforced capabilities, but the browser client had no way to
learn its own role: it drew the maintainer's affordances for everyone, a read
collaborator's settle came back 403, and the client's outbox dropped it (a
capability you lack never succeeds on retry) with nobody told.
"""
from __future__ import annotations

from codoc.serve.auth import Capability
from codoc.serve.payload import viewer_block


def test_each_capability_states_what_it_may_do():
    assert viewer_block(Capability.HANDOFF, "grace") == {
        "capability": "handoff", "login": "grace", "canSuggest": True, "canHandOff": True,
    }
    assert viewer_block(Capability.SUGGEST, "ada") == {
        "capability": "suggest", "login": "ada", "canSuggest": True, "canHandOff": False,
    }
    assert viewer_block(Capability.NONE) == {
        "capability": "none", "login": "", "canSuggest": False, "canHandOff": False,
    }


def test_the_block_derives_from_the_capability_not_a_parallel_table():
    """The two booleans must be the capability's own answers. A second table
    would drift from the one the routes enforce with, and the client would draw
    an affordance the hub then refuses — the exact failure this exists to end."""
    for cap in Capability:
        block = viewer_block(cap)
        assert block["canSuggest"] is cap.can_suggest()
        assert block["canHandOff"] is cap.can_hand_off()


def test_the_shared_payload_carries_no_viewer(tmp_path):
    """The load-bearing separation. ``PayloadStream`` computes one payload and
    hands it to every connected viewer, so a capability built into it would be
    whatever the first viewer happened to have — telling a contributor they can
    hand off, unlocking a button the server refuses. Worse than not knowing."""
    from codoc.serve.payload import build_browser_payload

    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    payload = build_browser_payload(codoc_dir)

    assert "viewer" not in payload


# ── route level: the block each real connection actually receives ──────────────
#
# The unit tests above pin `viewer_block`; these pin the WIRING, which is where the
# failure would be invisible. `_viewer` reads the request's own session, so every
# branch has to be exercised through a request — a payload that carried the wrong
# viewer would draw affordances the routes then refuse, which is the whole reason
# the field exists.

def _codoc_dir(tmp_path):
    import json

    from codoc.model.hlc import HLC

    cd = tmp_path / ".codoc"
    cd.mkdir(parents=True)
    (cd / "tree.bindings.json").write_text(json.dumps(
        {"features": {"f": {"title": "X", "parent_id": None}}, "by_feature": {}}))
    (cd / "status.json").write_text(json.dumps(
        {"state": "in_sync", "pending": 0, "at": HLC.now().to_str()}))
    return cd


def test_payload_reports_handoff_when_no_auth_is_configured(tmp_path):
    """Localhost-only posture: the single local viewer IS the maintainer, and the
    routes will grant handoff, so the block must say so or the UI hides its own
    hand-off button on the maintainer's own machine."""
    from fastapi.testclient import TestClient

    from codoc.serve.app import build_app

    client = TestClient(build_app(str(_codoc_dir(tmp_path))))
    viewer = client.get("/api/payload").json()["viewer"]
    assert viewer == {"capability": "handoff", "login": "",
                      "canSuggest": True, "canHandOff": True}


def test_payload_reports_each_session_its_own_capability(tmp_path):
    """Two clients, one hub, one shared payload — and different answers. A viewer
    block computed anywhere but per-request would give both whatever the first
    connection happened to have."""
    from fastapi.testclient import TestClient

    from codoc.serve.app import build_app
    from codoc.serve.auth import COOKIE_NAME, AuthContext, SessionStore

    store = SessionStore()
    app = build_app(str(_codoc_dir(tmp_path)), auth=AuthContext(store=store))

    reader = TestClient(app)
    reader.cookies.set(COOKIE_NAME, store.create("ada", Capability.SUGGEST).sid)
    assert reader.get("/api/payload").json()["viewer"] == {
        "capability": "suggest", "login": "ada", "canSuggest": True, "canHandOff": False,
    }

    maintainer = TestClient(app)
    maintainer.cookies.set(COOKIE_NAME, store.create("grace", Capability.HANDOFF).sid)
    assert maintainer.get("/api/payload").json()["viewer"] == {
        "capability": "handoff", "login": "grace", "canSuggest": True, "canHandOff": True,
    }


def test_the_sse_route_hands_the_stream_this_connection_s_viewer(tmp_path, monkeypatch):
    """The browser learns its role from the SSE snapshot too, not just the initial GET.
    The generator is watch-driven and never ends on its own, so this asserts what the
    ROUTE passes into it — which is the part that could be wrong."""
    from fastapi.testclient import TestClient

    from codoc.serve import push
    from codoc.serve.app import build_app
    from codoc.serve.auth import COOKIE_NAME, AuthContext, SessionStore

    seen: list[dict] = []

    async def fake_event_source(codoc_dir, *, is_disconnected=None, viewer=None):
        seen.append(viewer)
        yield {"data": "{}"}

    monkeypatch.setattr(push, "event_source", fake_event_source)

    store = SessionStore()
    client = TestClient(build_app(str(_codoc_dir(tmp_path)), auth=AuthContext(store=store)))
    client.cookies.set(COOKIE_NAME, store.create("ada", Capability.SUGGEST).sid)

    with client.stream("GET", "/api/events") as r:
        assert r.status_code == 200
        r.read()

    assert seen == [{"capability": "suggest", "login": "ada",
                     "canSuggest": True, "canHandOff": False}]


def test_an_unauthenticated_read_is_refused_before_any_viewer_is_computed(tmp_path):
    """The block is advisory; the gate is the enforcement. A caller with no session
    gets 401 rather than a NONE-capability payload it could render from."""
    from fastapi.testclient import TestClient

    from codoc.serve.app import build_app
    from codoc.serve.auth import AuthContext, SessionStore

    client = TestClient(build_app(str(_codoc_dir(tmp_path)),
                                  auth=AuthContext(store=SessionStore())))
    assert client.get("/api/payload").status_code == 401
    assert client.get("/api/events").status_code == 401
