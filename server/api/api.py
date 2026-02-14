"""
CodeNav API — semantic tree pipeline: analyze (extract + index + RAG), search, status.
Uses adalflow for embeddings and LLM completion.
"""

import os
import logging
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.logging_config import setup_logging
from api.semantic_tree.routes import router as semantic_tree_router

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="CodeNav API",
    description="API for semantic tree: analyze (extract + RAG index + LLM pipeline), search, status.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(semantic_tree_router)


@app.get("/health")
async def health_check():
    """Health check for Docker and monitoring."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "codenav-api",
    }


@app.get("/")
async def root():
    """Root endpoint: list available route groups."""
    endpoints = {}
    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            if route.path in ["/openapi.json", "/docs", "/redoc", "/favicon.ico"]:
                continue
            path_parts = route.path.strip("/").split("/")
            group = path_parts[0].capitalize() if path_parts[0] else "Root"
            method_list = list(route.methods - {"HEAD", "OPTIONS"})
            for method in method_list:
                endpoints.setdefault(group, []).append(f"{method} {route.path}")
    for group in endpoints:
        endpoints[group].sort()
    return {
        "message": "CodeNav API",
        "version": "0.1.0",
        "endpoints": endpoints,
    }
