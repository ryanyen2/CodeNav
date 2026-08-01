"""github_auth.py — the live GitHub edge for the hub's authorization (U4/U6 wiring).

``auth.py`` is the pure decision layer (permission→capability, sessions, the
``authorize`` gate). This module is the thin LIVE edge that talks to GitHub and
plugs into that layer:

  • the OAuth authorization-code web flow — a visitor signs in on github.com and
    the hub exchanges the returned code for a short-lived user token just long
    enough to read their login;
  • the collaborator-permission check — run with the MAINTAINER / App-installation
    token (NEVER the visitor's), because ``GET /repos/{owner}/{repo}/collaborators/
    {login}/permission`` requires the caller to have push access (KTD4).

HTTP is injected (``http_get`` / ``http_post`` callables), so URL-building and
response-parsing are unit-testable without network; the live default uses ``httpx``
(the ``serve`` extra). Config comes from the environment — see
``docs/serve-deployment.md``:

    CODOC_GITHUB_CLIENT_ID / CODOC_GITHUB_CLIENT_SECRET  the OAuth App
    CODOC_GITHUB_TOKEN     a token WITH PUSH ACCESS to the repo (App installation
                           token or a maintainer PAT) — used ONLY for the
                           collaborator-permission call, never handed to any agent
    CODOC_SERVE_REPO       owner/repo being served
    CODOC_GITHUB_API       API base (default https://api.github.com; set for GHE)
    CODOC_GITHUB_WEB       OAuth base (default https://github.com; set for GHE)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

# http_get(url, headers) -> (status_code, parsed_json_or_None)
# http_post(url, data, headers) -> (status_code, parsed_json_or_None)
HttpGet = Callable[[str, dict], "tuple[int, object]"]
HttpPost = Callable[[str, dict, dict], "tuple[int, object]"]

_DEFAULT_API = "https://api.github.com"
_DEFAULT_WEB = "https://github.com"
_OAUTH_SCOPE = "read:user"  # only the login is needed; no repo scope for the visitor


@dataclass(frozen=True)
class GithubAuthConfig:
    client_id: str
    client_secret: str
    token: str          # maintainer/installation token for the permission check
    owner: str
    repo: str
    api_base: str = _DEFAULT_API
    web_base: str = _DEFAULT_WEB

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "GithubAuthConfig | None":
        """Build the config from the environment, or ``None`` when the hub auth is
        not fully configured (so the caller can keep the hub localhost-only)."""
        env = environ if environ is not None else os.environ
        client_id = (env.get("CODOC_GITHUB_CLIENT_ID") or "").strip()
        client_secret = (env.get("CODOC_GITHUB_CLIENT_SECRET") or "").strip()
        token = (env.get("CODOC_GITHUB_TOKEN") or "").strip()
        repo_slug = (env.get("CODOC_SERVE_REPO") or "").strip()
        if not (client_id and client_secret and token and "/" in repo_slug):
            return None
        owner, _, repo = repo_slug.partition("/")
        if not (owner and repo):
            return None
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            token=token,
            owner=owner,
            repo=repo,
            api_base=(env.get("CODOC_GITHUB_API") or _DEFAULT_API).rstrip("/"),
            web_base=(env.get("CODOC_GITHUB_WEB") or _DEFAULT_WEB).rstrip("/"),
        )


def authorize_url(config: GithubAuthConfig, *, state: str, redirect_uri: str) -> str:
    """The github.com URL a visitor is redirected to, to sign in and consent."""
    query = urlencode({
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "scope": _OAUTH_SCOPE,
        "state": state,
        "allow_signup": "false",
    })
    return f"{config.web_base}/login/oauth/authorize?{query}"


def parse_token_response(payload: object) -> str | None:
    """The user access token from the exchange response, or ``None`` on failure
    (GitHub returns ``{"error": …}`` with HTTP 200 on a bad/expired code)."""
    if not isinstance(payload, dict):
        return None
    token = payload.get("access_token")
    return token if isinstance(token, str) and token else None


def parse_permission_response(payload: object) -> str | None:
    """The collaborator ``permission`` level (admin/write/read/none) or ``None``."""
    if not isinstance(payload, dict):
        return None
    perm = payload.get("permission")
    return perm if isinstance(perm, str) and perm else None


class GithubOAuth:
    """The visitor-facing OAuth flow: authorize URL → code → user token → login."""

    def __init__(self, config: GithubAuthConfig, *,
                 http_get: HttpGet | None = None, http_post: HttpPost | None = None):
        self.config = config
        self._get = http_get or _httpx_get
        self._post = http_post or _httpx_post

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        return authorize_url(self.config, state=state, redirect_uri=redirect_uri)

    def exchange_code(self, code: str, *, redirect_uri: str) -> str | None:
        """Exchange the callback ``code`` for a short-lived user access token."""
        url = f"{self.config.web_base}/login/oauth/access_token"
        data = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        try:
            status, payload = self._post(url, data, {"Accept": "application/json"})
        except Exception:  # noqa: BLE001 — a network failure denies by default
            return None
        if status != 200:
            return None
        return parse_token_response(payload)

    def fetch_login(self, user_token: str) -> str | None:
        """Read the signed-in user's GitHub login from their (short-lived) token."""
        url = f"{self.config.api_base}/user"
        try:
            status, payload = self._get(url, _bearer(user_token))
        except Exception:  # noqa: BLE001
            return None
        if status != 200 or not isinstance(payload, dict):
            return None
        login = payload.get("login")
        return login if isinstance(login, str) and login else None


class GithubCollaboratorResolver:
    """Implements the ``CollaboratorResolver`` protocol against the live GitHub REST
    API, using the MAINTAINER/App token (which has push access) — never a visitor
    token. A non-collaborator (404) or any error resolves to ``None`` so
    ``auth.authorize`` denies by default."""

    def __init__(self, config: GithubAuthConfig, *, http_get: HttpGet | None = None):
        self.config = config
        self._get = http_get or _httpx_get

    def permission(self, login: str) -> str | None:
        if not login:
            return None
        url = (f"{self.config.api_base}/repos/{self.config.owner}/{self.config.repo}"
               f"/collaborators/{login}/permission")
        try:
            status, payload = self._get(url, _bearer(self.config.token))
        except Exception:  # noqa: BLE001 — deny by default on any failure
            return None
        if status != 200:
            return None  # 404 = not a collaborator; 403 = our token lacks push access
        return parse_permission_response(payload)


def build_auth_context(config: GithubAuthConfig, *, ttl_seconds: float | None = None):
    """Assemble an :class:`codoc.serve.auth.AuthContext` backed by live GitHub."""
    from codoc.serve.auth import AuthContext, DEFAULT_TTL_SECONDS, SessionStore

    store = SessionStore(ttl_seconds=ttl_seconds or DEFAULT_TTL_SECONDS)
    resolver = GithubCollaboratorResolver(config)
    # The OAuth helper rides on the context so the app's login/callback routes reach
    # it without reconstructing config.
    return AuthContext(store=store, resolver=resolver, oauth=GithubOAuth(config))


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _httpx_get(url: str, headers: dict) -> "tuple[int, object]":
    import httpx

    resp = httpx.get(url, headers=headers, timeout=10.0, follow_redirects=False)
    return resp.status_code, _json_or_none(resp)


def _httpx_post(url: str, data: dict, headers: dict) -> "tuple[int, object]":
    import httpx

    resp = httpx.post(url, data=data, headers=headers, timeout=10.0, follow_redirects=False)
    return resp.status_code, _json_or_none(resp)


def _json_or_none(resp) -> object:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 — non-JSON body
        return None
