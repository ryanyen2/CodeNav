"""The hub's HTTP app.

Routes: ``/healthz``; ``/auth/login`` → GitHub → ``/auth/callback``;
``/api/whoami``; ``/api/events`` (SSE: projection, status, this viewer's acks);
``/api/message`` (POST, the webview's ``WebviewToHost`` messages: ``submit``
appends to ``inbox/hub-<login>.jsonl``; ``set-language`` writes ``config.json``
for maintainers; the terminal-bound ones are refused with a reason);
``/api/media/<name>``; the SPA shell and its assets.

With an ``AuthContext`` every ``/api`` route needs a session whose role is not
``none``. Without one the hub is for localhost and its single viewer is the
owner (``hub-local`` → ``human:local``).
"""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from codoc.serve.auth import COOKIE_NAME, MAINTAINER, NONE, AuthContext, Session, authorize, record_role
from codoc.workspace import log as L
from codoc.workspace.log import Submission

STATE_COOKIE = "codoc_oauth_state"
MAX_SUBS = 200                     # one settle is a few ops; the rate limit counts requests
LOCAL = Session("local", "local", MAINTAINER, 0.0)


def client_name(login: str) -> str:
    """The inbox file for a GitHub login; the daemon stamps ``log.hub_actor``."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in login)[:64] or "anon"
    return f"hub-{safe}"


def inbox_of(s: Session) -> str:
    """Which inbox this viewer writes to. With no auth the hub serves the
    person at the machine, who is the local writer like the IDE and the CLI,
    not a remote contributor."""
    return "cli" if s is LOCAL else client_name(s.login)


def _https(request: Request) -> bool:
    fwd = request.headers.get("x-forwarded-proto")
    return fwd.split(",")[0].strip().lower() == "https" if fwd else request.url.scheme == "https"


def _callback(request: Request) -> str:
    host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    if host:
        return f"{'https' if _https(request) else 'http'}://{host}/auth/callback"
    return str(request.base_url).rstrip("/") + "/auth/callback"


def build_app(codoc_dir: str, *, static_dir: str | None = None, auth: AuthContext | None = None, rate_limiter=None):
    cd = Path(codoc_dir)
    app = FastAPI(title="codoc", docs_url=None, redoc_url=None)

    def session(request: Request) -> Session | None:
        if auth is None:
            return LOCAL
        s = auth.session(request.cookies.get(COOKIE_NAME), cd)
        return s if s and s.role != NONE else None

    def denied() -> JSONResponse:
        return JSONResponse({"error": "authentication required", "login": "/auth/login"}, status_code=401)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "service": "codoc"})

    @app.get("/api/whoami")
    def whoami(request: Request) -> JSONResponse:
        s = session(request)
        return JSONResponse({"authenticated": s is not None, "login": s.login if s else None, "role": s.role if s else NONE})

    if auth is not None:
        @app.get("/auth/login")
        def login(request: Request):
            if auth.oauth is None:
                return JSONResponse({"error": "GitHub sign-in is not configured on this hub"}, status_code=503)
            state = secrets.token_urlsafe(24)
            resp = RedirectResponse(auth.oauth.authorize_url(state=state, redirect_uri=_callback(request)), status_code=302)
            resp.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, secure=_https(request), samesite="lax", path="/auth")
            return resp

        @app.get("/auth/callback")
        def callback(request: Request, code: str = "", state: str = ""):
            if auth.oauth is None or auth.resolver is None:
                return JSONResponse({"error": "sign-in is not configured"}, status_code=503)
            expected = request.cookies.get(STATE_COOKIE)
            if not code or not state or not expected or not secrets.compare_digest(state, expected):
                return JSONResponse({"error": "invalid or expired sign-in state"}, status_code=400)
            token = auth.oauth.exchange_code(code, redirect_uri=_callback(request))
            login_name = auth.oauth.fetch_login(token) if token else None
            if not login_name:
                return JSONResponse({"error": "GitHub sign-in failed"}, status_code=401)
            role = authorize(login_name, auth.resolver)
            if role == NONE:
                return JSONResponse({"error": f"'{login_name}' is not a collaborator on this repository"}, status_code=403)
            record_role(cd, login_name, role)
            s = auth.store.create(login_name, role)
            resp = RedirectResponse("/", status_code=302)
            resp.set_cookie(COOKIE_NAME, s.sid, httponly=True, secure=_https(request), samesite="lax", path="/")
            resp.delete_cookie(STATE_COOKIE, path="/auth")
            return resp

        @app.post("/api/logout")
        def logout(request: Request) -> JSONResponse:
            auth.store.delete(request.cookies.get(COOKIE_NAME))
            resp = JSONResponse({"ok": True})
            resp.delete_cookie(COOKIE_NAME, path="/")
            return resp

    @app.get("/api/events")
    async def events(request: Request):
        s = session(request)
        if s is None:
            return denied()
        # Each connection polls the control files twice a second for as long as
        # it is held, so opening them is a write of its own kind.
        if rate_limiter is not None and not rate_limiter.allow(f"events:{s.login}"):
            return JSONResponse({"error": "rate limited"}, status_code=429)
        from sse_starlette.sse import EventSourceResponse
        from codoc.serve.push import event_source
        return EventSourceResponse(event_source(cd, inbox_of(s), is_disconnected=request.is_disconnected,
                                                still_allowed=lambda: session(request) is not None))

    @app.post("/api/message")
    async def message(request: Request) -> JSONResponse:
        s = session(request)
        if s is None:
            return denied()
        if request.headers.get("x-codoc-csrf") is None:
            return JSONResponse({"error": "missing CSRF header"}, status_code=403)
        # Every write wakes the daemon and fans out to every connected browser,
        # so a flood is both a daemon DoS and a broadcast amplifier.
        if rate_limiter is not None and not rate_limiter.allow(s.login):
            return JSONResponse({"error": "rate limited"}, status_code=429)
        try:
            m = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        return handle_message(cd, s, m)

    @app.get("/api/media/{name}")
    def media(name: str, request: Request):
        if session(request) is None:
            return denied()
        from starlette.responses import FileResponse
        p = (cd / "media" / name).resolve()
        if (cd / "media").resolve() not in p.parents or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(p))

    spa = Path(static_dir) if static_dir else None
    if spa and spa.is_dir():
        app.mount("/assets", StaticFiles(directory=str(spa)), name="assets")

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    def shell(full_path: str, request: Request):
        if auth is not None and session(request) is None:
            return HTMLResponse(SIGN_IN)
        return HTMLResponse(SHELL if spa and spa.is_dir() else PLACEHOLDER)

    return app


def handle_message(cd: Path, s: Session, m: dict) -> JSONResponse:
    """One ``WebviewToHost`` message from this viewer."""
    kind = m.get("kind") if isinstance(m, dict) else None
    if kind == "ready":
        return JSONResponse({"ok": True})
    if kind == "history":
        from codoc.serve.push import history_message
        return JSONResponse(history_message(cd) or {"ok": False, "error": "no history yet"})
    if kind == "submit":
        subs = m.get("subs") or []
        if not isinstance(subs, list) or not all(isinstance(x, dict) for x in subs):
            return JSONResponse({"error": "subs must be a list of submissions"}, status_code=400)
        if len(subs) > MAX_SUBS:
            # The rate limit counts REQUESTS, so one POST is one request however
            # much it carries. A settle is a few ops; anything of this size is
            # not a person typing.
            return JSONResponse({"error": f"at most {MAX_SUBS} submissions per message"}, status_code=413)
        L.submit(cd, inbox_of(s), [Submission(
            cid=str(x.get("cid") or ""), kind=str(x.get("kind") or ""), target=dict(x.get("target") or {}),
            payload=dict(x.get("payload") or {}), batch=str(x.get("batch") or ""), plan=x.get("plan") or None,
            caused_by=x.get("caused_by") or None) for x in subs])
        return JSONResponse({"ok": True, "submitted": len(subs)})
    if kind == "set-language":
        if s.role != MAINTAINER:
            return JSONResponse({"error": "only a maintainer changes the tree's language"}, status_code=403)
        tag = str(m.get("tag") or "").strip()
        from codoc.doclang import is_language_tag
        from codoc.workspace.config import write_config
        if not is_language_tag(tag):
            return JSONResponse({"error": "a BCP 47 language tag is required"}, status_code=400)
        # Atomically, like every other writer of the tracked config: the daemon
        # reads it on each inbox pass, and a torn read would drop every role.
        write_config(cd, doc_language=tag)
        return JSONResponse({"ok": True})
    if kind in ("realize", "sync", "open-cite", "open-file", "find"):
        return JSONResponse({"ok": False, "error": "the hub has no terminal; a maintainer's daemon builds hand-offs"}, status_code=202)
    return JSONResponse({"error": f"unknown message kind {kind!r}"}, status_code=400)


SHELL = """<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none';"/>
<link rel="stylesheet" href="/assets/app.css"/><title>codoc</title></head>
<body><div id="app"></div><script src="/assets/hub.js"></script><script src="/assets/app.js"></script></body></html>"""

SIGN_IN = """<!doctype html><meta charset="utf-8"><title>codoc</title>
<body style="font:14px/1.6 system-ui;margin:3rem;max-width:40rem"><h1>codoc</h1>
<p>This tree is shared with the repository's collaborators. <a href="/auth/login">Sign in with GitHub</a>.</p></body>"""

PLACEHOLDER = """<!doctype html><meta charset="utf-8"><title>codoc</title>
<body style="font:14px/1.6 system-ui;margin:3rem;max-width:40rem"><h1>codoc</h1>
<p>The hub is running. The editor bundle is not built: run <code>node esbuild.config.mjs</code> in <code>vscode-codoc/</code> or pass <code>--static-dir</code>.</p></body>"""
