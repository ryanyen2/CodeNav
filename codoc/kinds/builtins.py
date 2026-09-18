"""The shipped kinds: diagram, image, url, pdf, screenshot, comment.

Each is a declaration. ``lift`` reads code (or a local file, or a guarded
fetch) and returns new content or None; ``lower`` maps a person's edit to a
directive string or None; ``consult`` is passive. None of them knows about
ops, actors or plans.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from codoc.kinds import Kind, LiftContext, LowerContext, register

EXCERPT_MAX = 2000
RETRY_COOLDOWN_S = 300

# ── diagram: a mermaid flowchart of the feature's bound code ────────────────

_EDGE = re.compile(r"^\s*([\w.:]+)\s*-->\s*(?:\|[^|]*\|\s*)?([\w.:]+)\s*$")


def _node(symbol: str) -> str:
    leaf = symbol.split("::", 1)[-1].replace(".", "_")
    return leaf or symbol


def edges_of(source: str) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for line in source.splitlines():
        m = _EDGE.match(line)
        if m:
            out.add((m.group(1), m.group(2)))
    return out


def _freeform(source: str) -> bool:
    for line in source.splitlines():
        s = line.strip()
        if not s or s.startswith(("flowchart", "graph", "%%")) or _EDGE.match(line):
            continue
        return True
    return False


def diagram_lift(ctx: LiftContext) -> dict | None:
    symbols = [b.symbol for b in ctx.bindings]
    if ctx.graph is None or not symbols:
        return None
    lines = ["flowchart TB"]
    seen: set[tuple[str, str]] = set()
    for sym in symbols:
        for e in ctx.graph.edges_out(sym):   # type: ignore[attr-defined]
            dst = e.get("dst_symbol") or e.get("dst_name")
            if not dst:
                continue
            edge = (_node(sym), _node(dst))
            if edge in seen or edge[0] == edge[1]:
                continue
            seen.add(edge)
            lines.append(f"  {edge[0]} --> {edge[1]}")
    for sym in symbols:
        n = _node(sym)
        if not any(n in e for e in seen):
            lines.append(f"  {n}")
    return {"source": "\n".join(lines)}


def diagram_lower(old: dict, new: dict, ctx: LowerContext) -> str | None:
    if not ctx.bindings:
        return None
    a, b = edges_of(old.get("source", "")), edges_of(new.get("source", ""))
    added, removed = b - a, a - b
    if not added and not removed:
        if new.get("source") != old.get("source") and _freeform(new.get("source", "")):
            return ("The diagram changed in a way that is not a dependency edit (a freeform node or a "
                    "relabel). Ask the author what code change they mean before implementing.")
        return None
    parts = [f"Remove the dependency from `{s}` to `{d}` (the author deleted that edge)." for s, d in sorted(removed)]
    parts += [f"Add a dependency or call from `{s}` to `{d}` (the author drew that edge)." for s, d in sorted(added)]
    return "\n".join(parts)


DIAGRAM = Kind(
    name="diagram", text_fields=("source", "caption"),
    lift=diagram_lift, lower=diagram_lower,
    consult=lambda c: f"Diagram of this feature's code (mermaid):\n{c.get('source', '')}" + (f"\nCaption: {c['caption']}" if c.get("caption") else ""),
)

# ── url / pdf: a reference fetched or extracted once, then consulted ────────

def _truncate(text: str) -> str:
    text = text.strip()
    return text if len(text) <= EXCERPT_MAX else text[:EXCERPT_MAX].rstrip() + "…"


def _cooling(content: dict) -> bool:
    at = content.get("attempted_at")
    return isinstance(at, (int, float)) and time.time() - at < RETRY_COOLDOWN_S


def url_lift(ctx: LiftContext) -> dict | None:
    c = ctx.block.content
    url = str(c.get("url") or "").strip()
    if not url.startswith(("http://", "https://")) or c.get("status") == "ok" and c.get("fetched") == url or _cooling(c):
        return None
    from codoc.kinds.fetch_guard import safe_get
    now = time.time()
    raw = safe_get(url)
    if raw is None:
        return {"status": "unreachable", "attempted_at": now}
    title, text = _article(raw)
    return {"status": "ok", "fetched": url, "title": title, "excerpt": _truncate(text), "attempted_at": now}


def _article(raw: bytes) -> tuple[str, str]:
    try:
        import trafilatura
    except ImportError:
        return "", ""
    html = raw.decode("utf-8", errors="replace")
    meta = trafilatura.extract_metadata(html)
    return (meta.title if meta and meta.title else "") or "", trafilatura.extract(html) or ""


def url_consult(c: dict) -> str:
    if c.get("status") == "ok" and c.get("excerpt"):
        return f"Consult this reference ({c.get('url')} — \"{c.get('title') or c.get('url')}\"):\n{c['excerpt']}"
    return f"Consult: {c.get('url', '')}"


URL = Kind(
    name="url", text_fields=("caption",),
    schema={"url": {"type": "string", "required": True}, "status": {"type": "string"}, "fetched": {"type": "string"},
            "title": {"type": "string"}, "excerpt": {"type": "string"}, "attempted_at": {"type": "number"}},
    lift=url_lift, consult=url_consult,
)


def media_path(codoc_dir: str, ref: str) -> Path | None:
    """A ``.codoc/media/…`` reference as an absolute path, or None when it
    escapes the media directory or does not exist."""
    root = Path(codoc_dir).parent.resolve()
    media = (root / ".codoc" / "media").resolve()
    p = (root / ref).resolve()
    return p if media in p.parents and p.is_file() else None


def pdf_lift(ctx: LiftContext) -> dict | None:
    c = ctx.block.content
    ref = str(c.get("ref") or "").strip()
    if not ref or ctx.codoc_dir is None or c.get("status") == "ok" and c.get("extracted") == ref or _cooling(c):
        return None
    now = time.time()
    path = media_path(ctx.codoc_dir, ref)
    text, pages = "", 0
    if path is not None:
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            parts: list[str] = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
                if sum(map(len, parts)) >= EXCERPT_MAX:
                    break
            text, pages = "\n".join(parts), len(reader.pages)
        except Exception:  # noqa: BLE001 — a bad file is "nothing to lift"
            text, pages = "", 0
    return {"status": "ok" if text else "error", "extracted": ref, "pages": pages, "excerpt": _truncate(text), "attempted_at": now}


PDF = Kind(
    name="pdf", text_fields=("caption",),
    schema={"ref": {"type": "string", "required": True}, "status": {"type": "string"}, "extracted": {"type": "string"},
            "pages": {"type": "number"}, "excerpt": {"type": "string"}, "attempted_at": {"type": "number"}},
    lift=pdf_lift,
    consult=lambda c: (f"Consult this reference document ({c.get('ref')}):\n{c['excerpt']}" if c.get("excerpt")
                       else f"Consult this reference document: {c.get('ref', '')}"),
)

# ── image / screenshot: pixels the agent is told about ──────────────────────

IMAGE = Kind(
    name="image", text_fields=("alt", "caption"),
    schema={"asset": {"type": "string", "required": True}, "source": {"type": "string", "enum": ["screenshot", "upload", "agent"]}},
    consult=lambda c: f"Image {c.get('alt') or ''}. " + (f"Caption: {c['caption']}. " if c.get("caption") else "") + f"File: {c.get('asset', '')}",
    # An agent cannot make pixels from text: its reply names an asset it captured or drew.
    write_back=lambda content, reply: {**content, **{k: reply[k] for k in ("asset", "alt", "caption") if k in reply}, "source": "agent"},
)

SCREENSHOT = Kind(
    name="screenshot", text_fields=("caption",),
    schema={"asset": {"type": "string", "required": True}},
    consult=lambda c: f"Consult this screenshot of the problem before implementing: {c.get('asset', '')}"
    + (f" ({c['caption']})" if c.get("caption") else ""),
)

# ── comment: one message of a thread, anchored by a comment mark ────────────

def comment_lower(old: dict, new: dict, ctx: LowerContext) -> str | None:
    """An open thread is a request. ``scope=code`` asks for the implementation
    only; ``both`` also asks for the description to follow, which the agent
    does through the tree, not by editing prose behind the person's back."""
    if new.get("status") == "resolved":
        return None
    body = (new.get("body") or "").strip()
    if not body:
        return None
    line = f"Comment on \"{new.get('anchor_text', '')}\": {body}"
    if new.get("scope") == "both":
        line += "\nWhen the code lands, amend this feature's description to match, through codoc_reflect."
    return line


COMMENT = Kind(
    name="comment", text_fields=("body",),
    schema={"thread": {"type": "string", "required": True}, "anchor": {"type": "string", "required": True},
            "anchor_text": {"type": "string"}, "scope": {"type": "string", "enum": ["code", "both"]},
            "status": {"type": "string", "enum": ["open", "resolved"]}},
    lower=comment_lower,
    consult=lambda c: f"Comment on \"{c.get('anchor_text', '')}\": {c.get('body', '')}",
    exported=False, in_flow=False,
)

ALL = (DIAGRAM, URL, PDF, IMAGE, SCREENSHOT, COMMENT)


def register_all() -> None:
    for k in ALL:
        register(k)
