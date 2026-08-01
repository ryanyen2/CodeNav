"""The codoc serve HTTP app.

Builds the ASGI app for the home-hub: the health endpoint, the SPA catch-all, the
read endpoints (payload / media / events), the GitHub sign-in flow, and the
capability-gated command endpoint.

Authorization posture (see ``auth.py`` / ``github_auth.py``):

  • With an :class:`AuthContext` present, the hub is GATED — the read endpoints
    (`/api/payload`, `/api/media`, `/api/events`) AND `/api/command` require a valid
    GitHub-backed session, and `/auth/login` → `/auth/callback` establish it. This is
    the posture for any off-machine exposure.
  • With no ``AuthContext`` (``auth=None``), the hub is UNGATED and intended for
    localhost only — ``codoc serve`` refuses to expose it off-machine in that state
    (see ``codoc.cli.main.serve``).

The web framework is imported lazily inside :func:`build_app` so the base ``codoc``
CLI stays light and does not require the ``serve`` extra for the other commands.
"""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

_PLACEHOLDER = (
    "<!doctype html><meta charset=utf-8><title>codoc</title>"
    "<body style='font:14px/1.5 system-ui;margin:3rem;max-width:40rem'>"
    "<h1>codoc serve</h1>"
    "<p>The hub is running and supervising the daemon. The standalone editor "
    "bundle is not built yet (plan unit U2). Pass <code>--static-dir</code> "
    "once it exists.</p></body>"
)

_OAUTH_STATE_COOKIE = "codoc_oauth_state"


def _is_https(request: Request) -> bool:
    """Whether the visitor's connection is https — honouring ``X-Forwarded-Proto``
    (cloudflared/other reverse proxies terminate TLS and forward http to the origin)."""
    fwd = request.headers.get("x-forwarded-proto")
    if fwd:
        return fwd.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _callback_uri(request: Request) -> str:
    """The PUBLIC ``/auth/callback`` URL GitHub must redirect back to. Behind a tunnel
    the origin sees localhost, so prefer the forwarded host/proto — this must match the
    callback URL registered on the GitHub OAuth App."""
    host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    if host:
        scheme = "https" if _is_https(request) else "http"
        return f"{scheme}://{host}/auth/callback"
    return str(request.base_url).rstrip("/") + "/auth/callback"


def build_app(codoc_dir: str, *, static_dir: str | None = None, auth=None, rate_limiter=None):
    """Build the FastAPI app for ``codoc serve``.

    ``static_dir`` is the built standalone SPA; when absent the catch-all serves a
    placeholder so the hub is runnable before the SPA exists. ``auth`` is an optional
    :class:`codoc.serve.auth.AuthContext`; when present EVERY ``/api/*`` route (reads
    included) is gated on a valid session and the ``/auth/*`` sign-in routes register.
    API/SSE/auth routes register before the catch-all so it never shadows them."""
    from codoc.serve.auth import COOKIE_NAME, Capability

    app = FastAPI(title="codoc serve", docs_url=None, redoc_url=None)
    app.state.codoc_dir = codoc_dir
    app.state.auth = auth

    def _session(request: Request):
        return auth.store.get(request.cookies.get(COOKIE_NAME)) if auth else None

    def _gate(request: Request):
        """When auth is configured, require a valid (non-NONE) session to view the
        tree. Returns a 401 JSONResponse to short-circuit, or None to proceed. With
        no auth (localhost-only mode) there is no gate."""
        if auth is None:
            return None
        session = _session(request)
        if session is None or session.capability is Capability.NONE:
            return JSONResponse(
                {"error": "authentication required", "login": "/auth/login"},
                status_code=401,
            )
        return None

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "service": "codoc-serve"})

    if auth is not None:
        @app.get("/api/whoami")
        def whoami(request: Request) -> JSONResponse:
            session = _session(request)
            return JSONResponse({
                "authenticated": session is not None,
                "login": session.login if session else None,
                "capability": session.capability.value if session else "none",
            })

        @app.get("/auth/login")
        def auth_login(request: Request):
            oauth = getattr(auth, "oauth", None)
            if oauth is None:
                return JSONResponse(
                    {"error": "GitHub sign-in is not configured on this hub"},
                    status_code=503,
                )
            state = secrets.token_urlsafe(24)
            redirect_uri = _callback_uri(request)
            resp = RedirectResponse(
                oauth.authorize_url(state=state, redirect_uri=redirect_uri),
                status_code=302,
            )
            resp.set_cookie(_OAUTH_STATE_COOKIE, state, max_age=600, httponly=True,
                            secure=_is_https(request), samesite="lax", path="/auth")
            return resp

        @app.get("/auth/callback")
        def auth_callback(request: Request, code: str = "", state: str = ""):
            from codoc.serve.auth import authorize

            oauth = getattr(auth, "oauth", None)
            if oauth is None or auth.resolver is None:
                return JSONResponse({"error": "sign-in is not configured"}, status_code=503)
            expected = request.cookies.get(_OAUTH_STATE_COOKIE)
            if not code or not state or not expected or not secrets.compare_digest(state, expected):
                return JSONResponse({"error": "invalid or expired sign-in state"}, status_code=400)
            redirect_uri = _callback_uri(request)
            token = oauth.exchange_code(code, redirect_uri=redirect_uri)
            login = oauth.fetch_login(token) if token else None
            if not login:
                return JSONResponse({"error": "GitHub sign-in failed"}, status_code=401)
            capability = authorize(login, auth.resolver)
            if capability is Capability.NONE:
                return JSONResponse(
                    {"error": f"'{login}' is not a collaborator on this repository"},
                    status_code=403,
                )
            session = auth.store.create(login, capability)
            resp = RedirectResponse("/", status_code=302)
            resp.set_cookie(COOKIE_NAME, session.sid, httponly=True,
                            secure=_is_https(request), samesite="lax", path="/")
            resp.delete_cookie(_OAUTH_STATE_COOKIE, path="/auth")
            return resp

        @app.post("/api/logout")
        def api_logout(request: Request) -> JSONResponse:
            auth.store.delete(request.cookies.get(COOKIE_NAME))
            resp = JSONResponse({"ok": True})
            resp.delete_cookie(COOKIE_NAME, path="/")
            return resp

        @app.post("/api/command")
        async def api_command(request: Request) -> JSONResponse:
            from codoc.serve.dispatch import CommandError, dispatch

            # CSRF: state-changing requests must carry a custom header a cross-site
            # form cannot set (it forces a CORS preflight). The network HostBridge
            # sends it on every command.
            if request.headers.get("x-codoc-csrf") is None:
                return JSONResponse({"error": "missing CSRF header"}, status_code=403)
            session = _session(request)
            key = session.login if session else "anon"
            if rate_limiter is not None and not rate_limiter.allow(key):
                return JSONResponse({"error": "rate limited"}, status_code=429)
            capability = session.capability if session else Capability.NONE
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"error": "invalid JSON"}, status_code=400)
            try:
                result = dispatch(body, capability, codoc_dir)
            except CommandError as exc:
                return JSONResponse({"error": str(exc)}, status_code=exc.status)
            return JSONResponse(result)

    @app.get("/api/payload")
    def api_payload(request: Request) -> JSONResponse:
        blocked = _gate(request)
        if blocked is not None:
            return blocked
        from codoc.serve.payload import build_browser_payload

        return JSONResponse(build_browser_payload(codoc_dir))

    @app.get("/api/media/{name}")
    def api_media(name: str, request: Request):
        blocked = _gate(request)
        if blocked is not None:
            return blocked
        from starlette.responses import FileResponse

        from codoc.serve.media import resolve_media_file

        path = resolve_media_file(codoc_dir, name)
        if path is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(path))

    @app.get("/api/events")
    async def api_events(request: Request):
        blocked = _gate(request)
        if blocked is not None:
            return blocked
        from sse_starlette.sse import EventSourceResponse

        from codoc.serve.push import event_source

        return EventSourceResponse(
            event_source(codoc_dir, is_disconnected=request.is_disconnected)
        )

    spa = Path(static_dir) if static_dir else None
    index = (spa / "index.html") if spa else None
    if spa and spa.is_dir():
        app.mount("/assets", StaticFiles(directory=str(spa)), name="assets")

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    def spa_catch_all(full_path: str) -> HTMLResponse:
        if index is not None and index.is_file():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        return HTMLResponse(_PLACEHOLDER)

    return app
