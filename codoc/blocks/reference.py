"""Reference media: url, pdf, image, latex — persistent, mostly-ambient blocks that
enrich realization context but never imply a code change (none declare ``LOWER``).

``url`` and ``pdf`` additionally declare ``LIFT``: a DETERMINISTIC pass (Loop A,
the maintainer's own daemon — see ``codoc/blocks/fetch_guard.py`` for the trust
boundary) that fetches/extracts real content ONCE and caches it, so both a human
reading the tree and the realizing agent's ``consult`` get an actual excerpt
instead of a bare reference. ``content`` is plugin-opaque (per
``codoc/model/block.py``) — url/pdf encode a small JSON envelope
(``{"url"|"ref", "title"?, "excerpt"?, "status", "fetched_at"?, "attempted_at"}``)
so the raw reference and the derived excerpt are both recoverable, and a failed
fetch is cached with a cooldown rather than retried every single Loop A pass (a
network call inside a loop that can run on every file save must not become a
per-pass stall — see ``_RETRY_COOLDOWN_S``).

``image`` stays consult-only re: code (a lift/regeneration path is future work,
per the original plan) — it is visual, not text to extract. ``latex`` is
CONSULT-only + BOUND: a formula the realizing agent should see as context for the
feature it's attached to.
"""
from __future__ import annotations

import json
import time

from codoc.blocks.base import (
    BindingMode,
    BlockPlugin,
    Capability,
    Dispatch,
    LiftContext,
    LiftResult,
)
from codoc.blocks.fetch_guard import safe_get
from codoc.model.block import Block, BlockLifecycle

_EXCERPT_MAX_CHARS = 2000
_RETRY_COOLDOWN_S = 300  # don't re-attempt a failed fetch on every Loop A pass


def _parse_envelope(content: str) -> dict | None:
    """``content`` is either a bare reference (fresh, human-authored) or a
    previously-cached JSON envelope. Returns the envelope dict, or ``None`` if
    ``content`` is not (yet) one — the caller then treats it as a bare ref."""
    text = (content or "").strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _truncate(text: str, limit: int = _EXCERPT_MAX_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _within_cooldown(envelope: dict | None) -> bool:
    if not envelope:
        return False
    attempted = envelope.get("attempted_at")
    return isinstance(attempted, (int, float)) and (time.time() - attempted) < _RETRY_COOLDOWN_S


class UrlPlugin(BlockPlugin):
    kind = "url"
    capabilities = frozenset({Capability.LIFT, Capability.CONSULT})
    binding_mode = BindingMode.AMBIENT
    lifecycle = BlockLifecycle.PERSISTENT
    lift_dispatch = Dispatch.DETERMINISTIC

    def lift(self, ctx: LiftContext) -> LiftResult:
        prior = ctx.block.content if ctx.block else ""
        envelope = _parse_envelope(prior)
        url = envelope["url"] if envelope else prior.strip()
        if not url or not url.startswith(("http://", "https://")):
            return LiftResult.no_change()
        if envelope and envelope.get("url") == url and envelope.get("status") == "ok":
            return LiftResult.no_change()  # already fetched this exact url
        if _within_cooldown(envelope):
            return LiftResult.no_change()  # recently failed — don't hammer every pass

        raw = safe_get(url)
        now = time.time()
        if raw is None:
            new_envelope = {"url": url, "status": "blocked_or_unreachable", "attempted_at": now}
            return LiftResult.refresh(json.dumps(new_envelope, ensure_ascii=False))

        title, excerpt = _extract_article(raw)
        new_envelope = {
            "url": url, "title": title, "excerpt": _truncate(excerpt),
            "status": "ok", "fetched_at": now, "attempted_at": now,
        }
        return LiftResult.refresh(json.dumps(new_envelope, ensure_ascii=False))

    def consult(self, block: Block) -> str:
        envelope = _parse_envelope(block.content)
        if envelope and envelope.get("status") == "ok" and envelope.get("excerpt"):
            title = envelope.get("title") or envelope["url"]
            return f"Consult this reference ({envelope['url']} — \"{title}\"):\n{envelope['excerpt']}"
        url = envelope["url"] if envelope else (block.content or "").strip()
        return f"Consult: {url}"


def _extract_article(raw: bytes) -> tuple[str, str]:
    """Title + main-text extraction via trafilatura (optional `media` extra).
    Absent the library, return an empty excerpt — the envelope still records a
    successful fetch attempt (avoids re-fetching), just with no derived text."""
    try:
        import trafilatura
    except ImportError:
        return "", ""
    html = raw.decode("utf-8", errors="replace")
    text = trafilatura.extract(html) or ""
    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else "") or ""
    return title, text


class PdfPlugin(BlockPlugin):
    """A locally-attached PDF (``.codoc/media/*.pdf`` — the same attachment path
    as a screenshot). ``lift`` extracts text from the LOCAL FILE ONLY — never a
    network fetch, so it carries none of ``fetch_guard``'s concerns."""

    kind = "pdf"
    capabilities = frozenset({Capability.LIFT, Capability.CONSULT})
    binding_mode = BindingMode.AMBIENT
    lifecycle = BlockLifecycle.PERSISTENT
    lift_dispatch = Dispatch.DETERMINISTIC

    def lift(self, ctx: LiftContext) -> LiftResult:
        prior = ctx.block.content if ctx.block else ""
        envelope = _parse_envelope(prior)
        ref = envelope["ref"] if envelope else prior.strip()
        if not ref:
            return LiftResult.no_change()
        if envelope and envelope.get("ref") == ref and envelope.get("status") == "ok":
            return LiftResult.no_change()  # already extracted this exact attachment
        if _within_cooldown(envelope):
            return LiftResult.no_change()  # recently failed — don't retry every pass
        if ctx.codoc_dir is None:
            return LiftResult.no_change()  # no workspace root to resolve against

        text, pages = _extract_pdf_text(ctx.codoc_dir, ref)
        now = time.time()
        new_envelope = {
            "ref": ref, "pages": pages, "excerpt": _truncate(text),
            "status": "ok" if text else "error", "fetched_at": now, "attempted_at": now,
        }
        return LiftResult.refresh(json.dumps(new_envelope, ensure_ascii=False))

    def consult(self, block: Block) -> str:
        envelope = _parse_envelope(block.content)
        if envelope and envelope.get("excerpt"):
            return f"Consult this reference document ({envelope['ref']}):\n{envelope['excerpt']}"
        ref = envelope["ref"] if envelope else (block.content or "").strip()
        return f"Consult this reference document: {ref}"


def _resolve_media_ref(codoc_dir: str, ref: str):
    """Resolve a repo-relative ``.codoc/media/...`` ref to an absolute path,
    rejecting anything that escapes the media directory (defence in depth against
    a malformed/adversarial ref — the same shape of guard the hub's media route
    needs)."""
    from pathlib import Path

    repo_root = Path(codoc_dir).parent.resolve()
    media_dir = (repo_root / ".codoc" / "media").resolve()
    candidate = (repo_root / ref).resolve()
    if media_dir not in candidate.parents and candidate != media_dir:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _extract_pdf_text(codoc_dir: str, ref: str) -> tuple[str, int]:
    path = _resolve_media_ref(codoc_dir, ref)
    if path is None:
        return "", 0
    try:
        import pypdf
    except ImportError:
        return "", 0
    try:
        reader = pypdf.PdfReader(str(path))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
            if sum(len(p) for p in parts) >= _EXCERPT_MAX_CHARS:
                break
        return "\n".join(parts), len(reader.pages)
    except Exception:
        return "", 0


class ImagePlugin(BlockPlugin):
    """A reference image (e.g. a UI mock). Consult-only for v1 — a `lift`
    re-render path (regenerating from a live UI) is future work, per the
    original plan; this is unaffected by the fetch/extraction work above."""

    kind = "image"
    capabilities = frozenset({Capability.CONSULT})
    binding_mode = BindingMode.BOUND
    lifecycle = BlockLifecycle.PERSISTENT

    def consult(self, block: Block) -> str:
        ref = (block.content or "").strip() or "(image)"
        return f"Reference image for this feature: {ref}"


class LatexPlugin(BlockPlugin):
    """A formula attached to a feature (e.g. the algorithm a function
    implements). CONSULT-only + BOUND: the realizing agent should see it as
    context for the feature it's attached to; there is nothing to lift (a human
    authors it directly, like prose) and no code-implying `lower` (a formula edit
    is a clarification, not a structural directive — unlike diagram's edge
    delta, there's no deterministic mapping from a LaTeX diff to a code change)."""

    kind = "latex"
    capabilities = frozenset({Capability.CONSULT})
    binding_mode = BindingMode.BOUND
    lifecycle = BlockLifecycle.PERSISTENT

    def consult(self, block: Block) -> str:
        formula = (block.content or "").strip()
        return f"Consult this formula for the feature: {formula}" if formula else ""
