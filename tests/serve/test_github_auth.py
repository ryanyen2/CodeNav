"""The live GitHub edge for hub auth (codoc/serve/github_auth.py).

HTTP is injected, so these verify URL-building, response parsing, the OAuth
exchange→login flow, and the collaborator resolver's deny-by-default behavior
without touching the network.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from codoc.serve.auth import Capability, authorize
from codoc.serve.github_auth import (
    GithubAuthConfig,
    GithubCollaboratorResolver,
    GithubOAuth,
    build_auth_context,
    parse_permission_response,
    parse_token_response,
)


def _config():
    return GithubAuthConfig(client_id="cid", client_secret="secret", token="app-tok",
                            owner="acme", repo="widget")


def test_from_env_requires_all_fields():
    assert GithubAuthConfig.from_env({}) is None
    partial = {"CODOC_GITHUB_CLIENT_ID": "a", "CODOC_GITHUB_CLIENT_SECRET": "b"}
    assert GithubAuthConfig.from_env(partial) is None
    full = {"CODOC_GITHUB_CLIENT_ID": "a", "CODOC_GITHUB_CLIENT_SECRET": "b",
            "CODOC_GITHUB_TOKEN": "t", "CODOC_SERVE_REPO": "acme/widget"}
    cfg = GithubAuthConfig.from_env(full)
    assert cfg is not None and cfg.owner == "acme" and cfg.repo == "widget"


def test_authorize_url_carries_state_and_redirect():
    url = GithubOAuth(_config()).authorize_url(state="xyz", redirect_uri="https://h/auth/callback")
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert parsed.path.endswith("/login/oauth/authorize")
    assert q["state"] == ["xyz"]
    assert q["redirect_uri"] == ["https://h/auth/callback"]
    assert q["client_id"] == ["cid"]


def test_parse_helpers():
    assert parse_token_response({"access_token": "tok"}) == "tok"
    assert parse_token_response({"error": "bad_verification_code"}) is None
    assert parse_permission_response({"permission": "write"}) == "write"
    assert parse_permission_response({}) is None


def test_exchange_code_then_fetch_login():
    posts, gets = [], []

    def http_post(url, data, headers):
        posts.append((url, data))
        return 200, {"access_token": "user-tok"}

    def http_get(url, headers):
        gets.append((url, headers))
        return 200, {"login": "maya"}

    oauth = GithubOAuth(_config(), http_get=http_get, http_post=http_post)
    token = oauth.exchange_code("the-code", redirect_uri="https://h/auth/callback")
    assert token == "user-tok"
    assert oauth.fetch_login(token) == "maya"
    # the exchange used the code + redirect; the login call used the USER token
    assert posts[0][1]["code"] == "the-code"
    assert "user-tok" in gets[0][1]["Authorization"]


def test_exchange_code_denies_on_error_status():
    oauth = GithubOAuth(_config(), http_post=lambda *a: (401, {}))
    assert oauth.exchange_code("x", redirect_uri="https://h/cb") is None


def test_collaborator_resolver_uses_app_token_and_denies_non_collaborators():
    seen = {}

    def http_get(url, headers):
        seen["url"] = url
        seen["auth"] = headers["Authorization"]
        # a write collaborator
        if "/maya/" in url:
            return 200, {"permission": "write"}
        return 404, {"message": "Not Found"}  # not a collaborator

    resolver = GithubCollaboratorResolver(_config(), http_get=http_get)
    assert resolver.permission("maya") == "write"
    assert resolver.permission("stranger") is None
    # the permission check runs with the APP token, never a visitor token (KTD4)
    assert "app-tok" in seen["auth"]
    assert "/repos/acme/widget/collaborators/" in seen["url"]


def test_end_to_end_authorize_maps_permission_to_capability():
    resolver = GithubCollaboratorResolver(
        _config(), http_get=lambda url, h: (200, {"permission": "read"}))
    assert authorize("maya", resolver) is Capability.SUGGEST


def test_build_auth_context_wires_store_resolver_oauth():
    ctx = build_auth_context(_config())
    assert ctx.store is not None
    assert ctx.resolver is not None
    assert ctx.oauth is not None
