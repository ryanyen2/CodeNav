"""``tree.codoc`` as Markdown, and its import (spec §6 ``export``, `04` §3).

    ## Title {#b-1a2b3c4d5e6f}

    A paragraph with **focus**, a [citation](codoc:file.py#Sym) and a
    [link](https://example.org).

    ```codoc:diagram {#b-…}
    {"source": "graph TD; A-->B"}
    ```

Headings carry their level as the number of hashes and their id as a
pandoc-style attribute; paragraphs carry no id (an agent that needs to cite
one asks the projection). Plugin kinds are fenced with the kind name and
their non-Text content as JSON; their Text fields are rendered as Markdown
strings inside that JSON. Comments and dead blocks are omitted.

``import_markdown`` is the inverse for a fresh store: it yields op bodies
(``block.add``, ``text.insert``, ``text.mark``) that rebuild the same export.
It is used by ``codoc init`` on a hand-written tree and by the fixed-point
test, never to reconcile an edited export against a live store.
"""
from __future__ import annotations

import json
import re

from codoc.calculus import ops as K
from codoc.calculus.derive import markdown
from codoc.calculus.doc import HEADING, PARAGRAPH, text_fields
from codoc.calculus.ids import START, new_block_id
from codoc.calculus.inline_md import parse
from codoc.calculus.store import Store

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*\{#(b-[0-9a-f]+)\}\s*$")
_FENCE = re.compile(r"^```codoc:([a-z_]+)\s*\{#(b-[0-9a-f]+)\}\s*$")


def export_markdown(store: Store) -> str:
    out: list[str] = []
    for b in store.ordered():
        if b.kind == HEADING:
            out.append(f"{'#' * max(1, b.level)} {markdown(b.text())} {{#{b.id}}}")
            out.append("")
        elif b.kind == PARAGRAPH:
            md = markdown(b.text())
            if md.strip():
                out.append(md)
                out.append("")
        else:
            content = {}
            for k, v in b.content.items():
                content[k] = markdown(v) if k in text_fields(b.kind) else v
            out.append(f"```codoc:{b.kind} {{#{b.id}}}")
            out.append(json.dumps(content, ensure_ascii=False, sort_keys=True))
            out.append("```")
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n" if out else ""


def import_markdown(md: str, *, keep_ids: bool = True) -> list[dict]:
    """Op bodies that build the document ``md`` describes in a fresh store.
    Paragraphs get fresh ids; headings keep theirs when ``keep_ids``."""
    bodies: list[dict] = []
    last: str = START
    lines = md.splitlines()
    i = 0
    para: list[str] = []

    def flush_para() -> None:
        nonlocal last
        text = " ".join(l.strip() for l in para).strip()
        para.clear()
        if not text:
            return
        bid = new_block_id()
        bodies.append({"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": PARAGRAPH, "after": last}})
        bodies.extend(_text_bodies(bid, text))
        last = bid

    while i < len(lines):
        line = lines[i]
        h = _HEADING.match(line)
        f = _FENCE.match(line)
        if h:
            flush_para()
            level, title_md, bid = len(h.group(1)), h.group(2), h.group(3)
            if not keep_ids:
                bid = new_block_id()
            bodies.append({"kind": K.BLOCK_ADD, "target": {"bid": bid},
                           "payload": {"kind": HEADING, "level": level, "after": last}})
            bodies.extend(_text_bodies(bid, title_md))
            last = bid
        elif f:
            flush_para()
            kind, bid = f.group(1), f.group(2)
            if not keep_ids:
                bid = new_block_id()
            body_lines = []
            i += 1
            while i < len(lines) and lines[i].strip() != "```":
                body_lines.append(lines[i])
                i += 1
            content = json.loads("\n".join(body_lines) or "{}")
            texts = {k: content.pop(k) for k in list(content) if k in text_fields(kind)}
            bodies.append({"kind": K.BLOCK_ADD, "target": {"bid": bid},
                           "payload": {"kind": kind, "after": last, "content": content}})
            for k, v in texts.items():
                bodies.extend(_text_bodies(bid, v, path=k))
            last = bid
        elif not line.strip():
            flush_para()
        else:
            para.append(line)
        i += 1
    flush_para()
    return bodies


def _text_bodies(bid: str, md: str, path: str = "text") -> list[dict]:
    text, spans = parse(md)
    if not text:
        return []
    out = [{"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": path}, "payload": {"after": START, "text": text}}]
    for sp in spans:
        out.append({"kind": K.TEXT_MARK, "target": {"bid": bid, "path": path},
                    "payload": {"kind": sp.kind, "attrs": sp.attrs, "start": ["$ins", 0, sp.start],
                                "end": ["$ins", 0, sp.end - 1], "on": True}})
    return out
