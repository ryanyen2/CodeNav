"""codoc FastAPI application factory and lifespan."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: read CODOC_ROOT_DIR from env, defaulting to cwd.
    root_dir = os.environ.get("CODOC_ROOT_DIR", os.getcwd())
    app.state.root_dir = root_dir
    yield
    # Cleanup: stores are opened per-request; nothing to tear down here.


def create_app() -> FastAPI:
    app = FastAPI(
        title="codoc",
        description="Feature-tree synchronization service",
        version="0.1.0",
        lifespan=lifespan,
    )
    from codoc.api.routes import router
    app.include_router(router)
    return app


app = create_app()
