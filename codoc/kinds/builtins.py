"""The shipped kinds.

    table        source: pipe-table rows
    code         source: a snippet; lang
    diagram      source: mermaid; derived (redrawn from the code graph when true)
    url, pdf     a reference, fetched or extracted once, then consulted
    image, screenshot   a file under .codoc/media the agent is told about
    comment      one message of a thread, anchored by a comment mark

Each is a declaration. ``lift`` reads code (or a local file, or a guarded
fetch) and returns new content or None; ``lower`` maps a person's edit to a
directive string or None; ``consult`` is passive. None of them knows about
ops, actors or plans.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from codoc.calculus.derive import one_line
from codoc.calculus.doc import Layout
from codoc.kinds.kind import Kind, LiftContext, LowerContext, register
from codoc.workspace.layout import CODOC_DIR, MEDIA_DIR

EXCERPT_MAX = 2000
RETRY_COOLDOWN_S = 300

# ── table, code: authored source. Both are part of the feature's description,
#    so the agent reads them there and neither needs a `consult`.

TABLE = Kind(name="table", layout=Layout(("source",)))

CODE = Kind(name="code", layout=Layout(("source",)), schema={"lang": {"type": "string"}})

# ── diagram: mermaid. Authored by default. `derived` marks a flowchart of the
#    feature's bound code: the loop redraws it, and an edit to it is a dependency request.

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
    if not ctx.block.content.get("derived") or ctx.graph is None or not symbols:
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
    """An edit to a derived diagram is a dependency request. An authored
    diagram is explanation; the agent reads it through ``consult``."""
    if not new.get("derived") or not ctx.bindings:
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
    name="diagram", layout=Layout(("source",)), schema={"derived": {"type": "boolean"}},
    lift=diagram_lift, lower=diagram_lower,
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
        url = one_line(c.get("url") or "")
        return f"Consult this reference ({url}, \"{one_line(c.get('title') or '') or url}\"):\n{c['excerpt']}"
    return f"Consult: {one_line(c.get('url') or '')}"


URL = Kind(
    name="url", layout=Layout(("caption",)),
    schema={"url": {"type": "string", "required": True}, "status": {"type": "string"}, "fetched": {"type": "string"},
            "title": {"type": "string"}, "excerpt": {"type": "string"}, "attempted_at": {"type": "number"}},
    lift=url_lift, consult=url_consult, lifted=("status", "fetched", "title", "excerpt", "attempted_at"),
)


def media_path(codoc_dir: str, ref: str) -> Path | None:
    """A ``.codoc/media/…`` reference as an absolute path, or None when it
    escapes the media directory or does not exist."""
    root = Path(codoc_dir).parent.resolve()
    media = (root / CODOC_DIR / MEDIA_DIR).resolve()
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
        except Exception:  # noqa: BLE001 (a bad file is "nothing to lift")
            text, pages = "", 0
    return {"status": "ok" if text else "error", "extracted": ref, "pages": pages, "excerpt": _truncate(text), "attempted_at": now}


PDF = Kind(
    name="pdf", layout=Layout(("caption",)),
    schema={"ref": {"type": "string", "required": True}, "status": {"type": "string"}, "extracted": {"type": "string"},
            "pages": {"type": "number"}, "excerpt": {"type": "string"}, "attempted_at": {"type": "number"}},
    lift=pdf_lift, lifted=("status", "extracted", "pages", "excerpt", "attempted_at"),
    consult=lambda c: (f"Consult this reference document ({one_line(c.get('ref') or '')}):\n{c['excerpt']}"
                       if c.get("excerpt") else f"Consult this reference document: {one_line(c.get('ref') or '')}"),
)

# ── image / screenshot: pixels the agent is told about ──────────────────────

IMAGE = Kind(
    name="image", layout=Layout(("alt", "caption")),
    schema={"asset": {"type": "string", "required": True}, "source": {"type": "string", "enum": ["screenshot", "upload", "agent"]}},
    consult=lambda c: one_line(f"Image {c.get('alt') or ''}. " + (f"Caption: {c['caption']}. " if c.get("caption") else "")
                               + f"File: {c.get('asset', '')}"),
)

SCREENSHOT = Kind(
    name="screenshot", layout=Layout(("caption",)),
    schema={"asset": {"type": "string", "required": True}},
    consult=lambda c: one_line(f"Consult this screenshot of the problem before implementing: {c.get('asset', '')}"
                               + (f" ({c['caption']})" if c.get("caption") else "")),
)

# ── comment: one message of a thread, anchored by a comment mark ────────────

def _comment_line(c: dict) -> str:
    """A comment as one directive line. Its words are a client's, so they are
    ``one_line``'d, the body as well as ``anchor_text``: collapsed rather than
    kept on lines of their own under a prefix, because the ``Comment on`` line
    is what voices them as a comment, and a model that meets a line of their
    words on its own may read it as the directive's (a ``Consult:`` or an
    ``Author asked:``) whatever marks it. A comment is a sentence or two, so
    its line breaks cost little."""
    return f"Comment on \"{one_line(c.get('anchor_text') or '')}\": {one_line((c.get('body') or '').strip())}"


def comment_lower(old: dict, new: dict, ctx: LowerContext) -> str | None:
    """An open thread is a request. ``scope=code`` asks for the implementation
    only; ``both`` also asks for the description to follow, which the agent
    does through the tree, not by editing prose behind the person's back.
    ``status``, ``scope`` and ``anchor_text`` are the thread's (its first
    message's, ``realize._kind_lines``), so a reply cannot ask for more than
    the margin shows."""
    if new.get("status") == "resolved":
        return None
    if not (new.get("body") or "").strip():
        return None
    line = _comment_line(new)
    if new.get("scope") == "both":
        line += "\nWhen the code lands, amend this feature's description to match, through codoc_reflect."
    return line


COMMENT = Kind(
    name="comment", layout=Layout(("body",), flow=False, export=False),   # hangs off words, not between them
    schema={"thread": {"type": "string", "required": True}, "anchor": {"type": "string", "required": True},
            "anchor_text": {"type": "string"}, "scope": {"type": "string", "enum": ["code", "both"]},
            "status": {"type": "string", "enum": ["open", "resolved"]}},
    lower=comment_lower,
)

ALL = (TABLE, CODE, DIAGRAM, URL, PDF, IMAGE, SCREENSHOT, COMMENT)


def register_all() -> None:
    for k in ALL:
        register(k)
