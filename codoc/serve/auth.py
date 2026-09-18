"""Sessions and roles for the hub.

A visitor signs in with GitHub; their collaborator permission on the served
repository is their role in the calculus's ``Roles`` table:

    admin / write / maintain   → maintainer   (trunk, verdicts, hand-offs)
    read / triage              → suggest      (plans of their own)
    not a collaborator         → none         (denied)

The permission check runs with the maintainer's token (the endpoint requires
push access), never the visitor's. The browser holds only an opaque cookie.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

COOKIE_NAME = "codoc_session"
DEFAULT_TTL_SECONDS = 8 * 3600
# How often a live session's collaborator permission is asked of GitHub again.
# A role is decided at sign-in and a sign-in lasts a working day, so without
# this a collaborator removed from the repository keeps the authority they
# arrived with until the cookie expires.
DEFAULT_RECHECK_SECONDS = 600.0
MAINTAINER, SUGGEST, NONE = "maintainer", "suggest", "none"

_WRITE = frozenset({"admin", "write", "maintain"})
_READ = frozenset({"read", "triage"})


def role_for_permission(permission: str | None) -> str:
    p = (permission or "").strip().lower()
    return MAINTAINER if p in _WRITE else SUGGEST if p in _READ else NONE


class CollaboratorResolver(Protocol):
    def permission(self, login: str) -> str | None: ...


def authorize(login: str | None, resolver: CollaboratorResolver) -> str:
    """Deny by default: no login, no collaborator, no answer → ``none``."""
    return role_for_permission(resolver.permission(login)) if login else NONE


@dataclass
class Session:
    sid: str
    login: str
    role: str
    created_at: float
    checked_at: float = 0.0        # when the role was last re-asked of GitHub


@dataclass
class SessionStore:
    """Server-side sessions keyed by the cookie value; in memory (one hub
    process owns the repo). ``clock`` is injectable for TTL tests."""
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    clock: Callable[[], float] = time.time
    _sessions: dict[str, Session] = field(default_factory=dict)

    def create(self, login: str, role: str) -> Session:
        now = self.clock()
        s = Session(secrets.token_urlsafe(32), login, role, now, now)
        self._sessions[s.sid] = s
        return s

    def get(self, sid: str | None) -> Session | None:
        s = self._sessions.get(sid or "")
        if s is None:
            return None
        if self.clock() - s.created_at > self.ttl_seconds:
            self._sessions.pop(s.sid, None)
            return None
        return s

    def delete(self, sid: str | None) -> None:
        self._sessions.pop(sid or "", None)


@dataclass
class AuthContext:
    store: SessionStore
    resolver: CollaboratorResolver | None = None
    oauth: object | None = None           # github_auth.GithubOAuth in deployment
    recheck_seconds: float = DEFAULT_RECHECK_SECONDS

    def session(self, sid: str | None) -> Session | None:
        """The session behind this cookie, with its role re-asked of GitHub on a
        cadence. Revoking access has to reach a tab that is already open, and
        the role it signed in with is a decision somebody has since changed."""
        s = self.store.get(sid)
        if s is None:
            return None
        now = self.store.clock()
        if self.resolver is None or now - s.checked_at < self.recheck_seconds:
            return s
        s.checked_at = now
        role = authorize(s.login, self.resolver)
        if role == NONE:
            self.store.delete(s.sid)
            return None
        s.role = role
        return s


def record_role(codoc_dir: str | Path, login: str, role: str) -> None:
    """Remember a login's role where every clone reads it: the tracked
    ``config.json`` (``roles: {actor: role}``, spec §7.3), so a rebuild on any
    machine applies that contributor's ops under the same authority. The actor
    is ``log.hub_actor``, never a bare ``human:<login>``, so no GitHub login can
    name the maintainer's own identity."""
    from codoc.doclang import read_config, write_config
    from codoc.substrate.log import hub_actor
    roles = dict(read_config(codoc_dir).get("roles") or {})
    roles[hub_actor(login)] = role
    write_config(codoc_dir, roles=roles)


def read_roles(codoc_dir: str | Path) -> dict[str, str]:
    from codoc.doclang import read_config
    d = read_config(codoc_dir).get("roles")
    return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
