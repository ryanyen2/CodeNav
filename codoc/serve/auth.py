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
ROLES_FILE = "cache/roles.json"
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


@dataclass
class SessionStore:
    """Server-side sessions keyed by the cookie value; in memory (one hub
    process owns the repo). ``clock`` is injectable for TTL tests."""
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    clock: Callable[[], float] = time.time
    _sessions: dict[str, Session] = field(default_factory=dict)

    def create(self, login: str, role: str) -> Session:
        s = Session(secrets.token_urlsafe(32), login, role, self.clock())
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


def record_role(codoc_dir: str | Path, login: str, role: str) -> None:
    """Remember a login's role where the daemon reads it: the actor
    ``human:<login>`` gets that role in ``Roles`` (``cache/roles.json``)."""
    path = Path(codoc_dir) / ROLES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        table = {}
    table[f"human:{login}"] = role
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(table, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def read_roles(codoc_dir: str | Path) -> dict[str, str]:
    try:
        d = json.loads((Path(codoc_dir) / ROLES_FILE).read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}
