"""Serve a locally-attached media file (image/pdf block, screenshot) to the hub's
browser clients — the read-side counterpart to the webview's `asWebviewUri`
translation (`vscode-codoc/src/providers/tree-editor.ts:mediaSrc`). Both hosts
resolve the SAME repo-relative `.codoc/media/...` ref to a URL their own
transport can load; neither the webview nor the hub's browser client ever
resolves a raw filesystem path itself.

``resolve_media_file`` is deliberately narrow: it only ever serves a bare
filename (a single path segment — FastAPI's ``{name}`` route param already can't
carry a ``/``, but a literal ``..`` segment still needs rejecting) from exactly
``<codoc_dir>/media/``, restricted to the small set of extensions codoc's own
attachment writers produce. This route has no auth gate (matching `/api/payload`
— read access mirrors what the payload already exposes), so keeping the surface
narrow is the only defense available.
"""
from __future__ import annotations

import base64
from pathlib import Path

_ALLOWED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"})
_ALLOWED_MIME_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "pdf"})
_MAX_ATTACHMENT_BYTES = 10_000_000  # a remote suggester's upload lands on the
# maintainer's own disk (unauthenticated-ish, SUGGEST-capability) — cap it so a
# repeated large upload isn't a free disk-exhaustion vector.


def resolve_media_file(codoc_dir: str, name: str) -> Path | None:
    """Resolve ``name`` (a bare filename) to a file under ``<codoc_dir>/media/``,
    or ``None`` if it's unsafe, has a disallowed extension, or doesn't exist."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    if Path(name).suffix.lower() not in _ALLOWED_EXTENSIONS:
        return None
    media_dir = (Path(codoc_dir) / "media").resolve()
    candidate = (media_dir / name).resolve()
    if candidate.parent != media_dir or not candidate.is_file():
        return None
    return candidate


def save_media_attachment(codoc_dir: str, key: str, data_b64: str, mime: str = "") -> str | None:
    """Decode + write a base64 attachment (an image/pdf block's `add`, submitted
    through the hub — mirrors the webview host's `writeMediaAttachment` in
    tree-editor.ts) under ``<codoc_dir>/media/``, keyed by ``key`` (the block id)
    so two attachments never collide. Returns the repo-relative
    ``.codoc/media/...`` ref Loop A/B and both hosts already expect, or ``None``
    on any invalid input (bad base64, disallowed/oversized, unwritable) — a
    failed attachment must not crash the edit, just land with no image."""
    ext = (mime.split("/")[-1] if mime else "png").lower()
    ext = "".join(c for c in ext if c.isalnum())
    if ext not in _ALLOWED_MIME_EXTENSIONS:
        return None
    safe = "".join(c for c in key if c.isalnum() or c in "_-") or "attachment"
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:
        return None
    if not raw or len(raw) > _MAX_ATTACHMENT_BYTES:
        return None
    media_dir = Path(codoc_dir) / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    name = f"{safe}.{ext}"
    (media_dir / name).write_bytes(raw)
    return f".codoc/media/{name}"


def media_url_for(ref: str) -> str | None:
    """The hub-facing URL for a block's media ``ref``, or ``None`` if ``ref``
    isn't a resolvable local attachment (an already-absolute ``http(s)://``
    reference is returned unchanged; the webview does the equivalent pass-through
    in ``mediaSrc``)."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if ref.startswith(("http://", "https://")):
        return ref
    if not ref.startswith(".codoc/media/"):
        return None
    name = ref.rsplit("/", 1)[-1]
    return f"/api/media/{name}" if name else None
