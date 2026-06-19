"""The codoc serve HTTP app (Tier 1 skeleton).

Builds the ASGI app for the home-hub. U1 lands the health endpoint and the
SPA-serving catch-all; SSE live-status (U3), command endpoints (U5), and the
GitHub auth edge (U4) register on this same app in later units.

The web framework is imported lazily inside :func:`build_app` so the base
``codoc`` CLI stays light and does not require the ``serve`` extra to be
installed for the other commands.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.staticfiles import StaticFiles

_PLACEHOLDER = (
    "<!doctype html><meta charset=utf-8><title>codoc</title>"
    "<body style='font:14px/1.5 system-ui;margin:3rem;max-width:40rem'>"
    "<h1>codoc serve</h1>"
    "<p>The hub is running and supervising the daemon. The standalone editor "
    "bundle is not built yet (plan unit U2). Pass <code>--static-dir</code> "
    "once it exists.</p></body>"
)


def build_app(codoc_dir: str, *, static_dir: str | None = None, auth=None):
    """Build the FastAPI app for ``codoc serve``.

    ``static_dir`` is the built standalone SPA (U2); when absent the catch-all
    serves a placeholder so the hub is runnable before the SPA exists. ``auth`` is
    an optional :class:`codoc.serve.auth.AuthContext`; when present the hub exposes
    ``/api/whoami`` and (in later units) gates state-changing routes on capability.
    API and SSE routes are registered before the catch-all so it never shadows them."""
    app = FastAPI(title="codoc serve", docs_url=None, redoc_url=None)
    app.state.codoc_dir = codoc_dir
    app.state.auth = auth

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "service": "codoc-serve"})

    if auth is not None:
        @app.get("/api/whoami")
        def whoami(request: Request) -> JSONResponse:
            from codoc.serve.auth import COOKIE_NAME

            session = auth.store.get(request.cookies.get(COOKIE_NAME))
            return JSONResponse({
                "authenticated": session is not None,
                "login": session.login if session else None,
                "capability": session.capability.value if session else "none",
            })

        @app.post("/api/command")
        async def api_command(request: Request) -> JSONResponse:
            from codoc.serve.auth import capability_from_request
            from codoc.serve.dispatch import CommandError, dispatch

            # CSRF: state-changing requests must carry a custom header a cross-site
            # form cannot set (it forces a CORS preflight). The network HostBridge
            # sends it on every command.
            if request.headers.get("x-codoc-csrf") is None:
                return JSONResponse({"error": "missing CSRF header"}, status_code=403)
            capability = capability_from_request(request, auth.store)
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
    def api_payload() -> JSONResponse:
        from codoc.serve.payload import build_browser_payload

        return JSONResponse(build_browser_payload(codoc_dir))

    @app.get("/api/events")
    async def api_events(request: "Request"):
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
