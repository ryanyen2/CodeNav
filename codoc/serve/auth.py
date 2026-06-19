"""auth.py — GitHub-App authorization for the codoc serve hub (U4).

The hub is gated on GitHub identity. A visitor signs in (auth-code + PKCE web
flow), and their REPO-COLLABORATOR permission decides their capability:

    read / triage        → SUGGEST  (suggest, comment, withdraw your own)
    write / maintain / admin → HANDOFF  (also accept, hand off, write verdicts)
    not a collaborator   → NONE     (denied)

The permission check runs with the MAINTAINER / App-installation identity — the
`GET /repos/{owner}/{repo}/collaborators/{user}/permission` endpoint requires the
CALLER to have push access, so it must NOT use the visitor's token (KTD4).

This module is the pure, fully-tested decision layer: the permission→capability
mapping, the `authorize()` gate, and a server-side `SessionStore` (the browser
holds only an opaque HTTP-only cookie — never a GitHub token). The live GitHub
OAuth + REST calls are injected behind :class:`CollaboratorResolver`, so the
trust boundary is mockable here and a real GitHub App (client id/secret,
installation token) is deployment configuration, documented in U6's deploy doc.
"""
from __future__ import annotations

import enum
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

COOKIE_NAME = "codoc_session"
DEFAULT_TTL_SECONDS = 8 * 3600


class Capability(enum.Enum):
    NONE = "none"
    SUGGEST = "suggest"
    HANDOFF = "handoff"

    def can_suggest(self) -> bool:
        return self in (Capability.SUGGEST, Capability.HANDOFF)

    def can_hand_off(self) -> bool:
        return self is Capability.HANDOFF


# GitHub permission level → capability. `maintain` collapses to write, `triage`
# to read (the legacy mapping the REST `permission` field uses).
_WRITE_LEVELS = frozenset({"admin", "write", "maintain"})
_READ_LEVELS = frozenset({"read", "triage"})


def capability_for_permission(permission: str | None) -> Capability:
    """Map a GitHub collaborator permission level to a codoc capability."""
    if not permission:
        return Capability.NONE
    p = permission.strip().lower()
    if p in _WRITE_LEVELS:
        return Capability.HANDOFF
    if p in _READ_LEVELS:
        return Capability.SUGGEST
    return Capability.NONE


class CollaboratorResolver(Protocol):
    """Resolves a GitHub login to its repo-collaborator permission level, using
    the maintainer/App identity. Returns None when the user is not a collaborator
    (or the lookup is unauthorized) so :func:`authorize` denies by default."""

    def permission(self, login: str) -> str | None:
        ...


def authorize(login: str | None, resolver: CollaboratorResolver) -> Capability:
    """The authorization gate: a visitor's capability for the served repo.

    Denies by default — an empty login, a non-collaborator, or a resolver that
    returns None all yield :attr:`Capability.NONE`."""
    if not login:
        return Capability.NONE
    return capability_for_permission(resolver.permission(login))


@dataclass
class Session:
    sid: str
    login: str
    capability: Capability
    created_at: float


@dataclass
class SessionStore:
    """Server-side sessions keyed by an opaque id (the cookie value).

    In-memory for Tier 1 — one always-on hub process owns the repo. The browser
    receives only the opaque ``sid`` as an HTTP-only ``Secure`` ``SameSite`` cookie;
    GitHub tokens never reach the client. ``clock`` is injectable for TTL tests."""

    ttl_seconds: float = DEFAULT_TTL_SECONDS
    clock: Callable[[], float] = time.time
    _sessions: dict[str, Session] = field(default_factory=dict)

    def create(self, login: str, capability: Capability) -> Session:
        sid = secrets.token_urlsafe(32)
        session = Session(sid=sid, login=login, capability=capability,
                          created_at=self.clock())
        self._sessions[sid] = session
        return session

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        session = self._sessions.get(sid)
        if session is None:
            return None
        if self.clock() - session.created_at > self.ttl_seconds:
            self._sessions.pop(sid, None)  # expired → evict
            return None
        return session

    def delete(self, sid: str | None) -> None:
        if sid:
            self._sessions.pop(sid, None)

    def capability_for(self, sid: str | None) -> Capability:
        session = self.get(sid)
        return session.capability if session else Capability.NONE


@dataclass
class AuthContext:
    """What the hub needs to authorize requests: the session store and (live) the
    collaborator resolver. ``resolver`` is None in tests that drive the session
    store directly; deployment supplies a GitHub-App-backed resolver."""

    store: SessionStore
    resolver: CollaboratorResolver | None = None


def capability_from_request(request, store: SessionStore) -> Capability:
    """Capability for an incoming request, read from its session cookie.

    Duck-typed on ``request.cookies`` (a Starlette ``Request``), so this module
    stays free of any web-framework import."""
    return store.capability_for(request.cookies.get(COOKIE_NAME))
