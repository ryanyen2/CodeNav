"""U4 — GitHub authorization: the permission→capability gate + sessions.

Five flows: read collaborator → suggest, write/admin → hand-off, non-collaborator
→ denied, empty login → denied, and an expired session → denied. The live GitHub
OAuth/REST is mocked behind CollaboratorResolver; this verifies the decision
layer that gates the whole hub."""
from __future__ import annotations

from codoc.serve.auth import (
    Capability,
    SessionStore,
    authorize,
    capability_for_permission,
)


class FakeResolver:
    def __init__(self, perms: dict[str, str]):
        self._perms = perms

    def permission(self, login: str) -> str | None:
        return self._perms.get(login)


def test_permission_level_mapping():
    assert capability_for_permission("read") is Capability.SUGGEST
    assert capability_for_permission("triage") is Capability.SUGGEST
    assert capability_for_permission("write") is Capability.HANDOFF
    assert capability_for_permission("maintain") is Capability.HANDOFF
    assert capability_for_permission("admin") is Capability.HANDOFF
    assert capability_for_permission("none") is Capability.NONE
    assert capability_for_permission(None) is Capability.NONE
    assert capability_for_permission("nonsense") is Capability.NONE


# Flow 1 — read collaborator → suggest.
def test_read_collaborator_can_only_suggest():
    cap = authorize("maya", FakeResolver({"maya": "read"}))
    assert cap is Capability.SUGGEST
    assert cap.can_suggest()
    assert not cap.can_hand_off()


# Flow 2 — write/admin collaborator → hand-off (and suggest).
def test_write_collaborator_can_hand_off():
    cap = authorize("sam", FakeResolver({"sam": "write"}))
    assert cap.can_hand_off()
    assert cap.can_suggest()


# Flow 3 — signed-in non-collaborator → denied.
def test_non_collaborator_denied():
    assert authorize("stranger", FakeResolver({})) is Capability.NONE


# Flow 4 — empty/absent login → denied (deny by default).
def test_empty_login_denied():
    assert authorize("", FakeResolver({"x": "admin"})) is Capability.NONE
    assert authorize(None, FakeResolver({"x": "admin"})) is Capability.NONE


def test_session_store_roundtrip_and_capability_lookup():
    store = SessionStore()
    session = store.create("maya", Capability.SUGGEST)
    assert store.get(session.sid).login == "maya"
    assert store.capability_for(session.sid) is Capability.SUGGEST
    assert store.capability_for(None) is Capability.NONE
    assert store.capability_for("bogus") is Capability.NONE


# Flow 5 — an expired session is denied and evicted.
def test_session_expires_after_ttl():
    clock = {"now": 1000.0}
    store = SessionStore(ttl_seconds=100, clock=lambda: clock["now"])
    session = store.create("sam", Capability.HANDOFF)
    clock["now"] = 1099
    assert store.get(session.sid) is not None
    clock["now"] = 1101
    assert store.get(session.sid) is None
    assert store.capability_for(session.sid) is Capability.NONE


def test_session_delete_revokes():
    store = SessionStore()
    session = store.create("x", Capability.HANDOFF)
    store.delete(session.sid)
    assert store.get(session.sid) is None
