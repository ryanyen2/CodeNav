"""Parse ``tree.codoc`` text into a structural view.

Lines:
  * feature       ``  - Title  ⟨f-1a2b⟩`` (or ``~`` retired; ``⟨new⟩``/absent = new)
  * description   indented prose lines beneath a feature
  * proposal      ``? add "Title"  ⟨e-9f01⟩`` — the leading char is the *action*
                  (``?`` pending, ``+`` accept, ``-`` reject); detail lines start ``?``
  * comment       ``# ...`` (ignored)

Indentation depth gives the parent. Identity comes from ``⟨f-id⟩`` — no sidecar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_FEATURE_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>[-~])\s+(?P<rest>.*\S)\s*$")
_ID_RE = re.compile(r"⟨(f-[0-9a-f]+|new)⟩")
_PROPOSAL_RE = re.compile(
    r"^\s*(?P<action>[?+\-])\s+(?:add|amend|move|retire)\b.*⟨(?P<eid>e-[0-9a-f]+)⟩"
)


@dataclass
class ParsedNode:
    id: str | None  # None = newly authored (no ⟨f-id⟩, or ⟨new⟩)
    title: str
    description: str
    parent_id: str | None
    retired: bool


@dataclass
class ParsedTree:
    nodes: list[ParsedNode] = field(default_factory=list)
    proposal_actions: dict[str, str] = field(default_factory=dict)  # event_id → '?'/'+'/'-'
    errors: list[str] = field(default_factory=list)


def parse_text(text: str) -> ParsedTree:
    tree = ParsedTree()
    stack: list[tuple[int, ParsedNode]] = []  # (indent, node)
    desc_owner: ParsedNode | None = None
    desc_buf: list[str] = []

    def flush_desc() -> None:
        nonlocal desc_owner, desc_buf
        if desc_owner is not None:
            desc_owner.description = "\n".join(desc_buf).strip("\n")
        desc_owner = None
        desc_buf = []

    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()

        if not s:
            flush_desc()
            continue
        if s.startswith("#"):
            continue
        if s.startswith("↪ refs:"):
            continue

        mp = _PROPOSAL_RE.match(line)
        if mp:
            flush_desc()
            tree.proposal_actions[mp.group("eid")] = mp.group("action")
            continue

        mf = _FEATURE_RE.match(line)
        if mf and not s.startswith("?"):
            flush_desc()
            indent = len(mf.group("indent"))
            marker = mf.group("marker")
            rest = mf.group("rest").strip()
            mid = _ID_RE.search(rest)
            if mid:
                fid = None if mid.group(1) == "new" else mid.group(1)
                title = rest[: mid.start()].strip()
            else:
                fid = None
                title = rest

            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent_id = stack[-1][1].id if stack else None

            node = ParsedNode(id=fid, title=title, description="",
                              parent_id=parent_id, retired=(marker == "~"))
            tree.nodes.append(node)
            stack.append((indent, node))
            desc_owner, desc_buf = node, []
            continue

        # otherwise: a description line for the current node
        if desc_owner is not None:
            desc_buf.append(s)

    flush_desc()
    return tree


def parse_tree_file(codoc_dir: str | Path) -> ParsedTree:
    from codoc.codoc_file.render import tree_path

    path = tree_path(codoc_dir)
    if not path.exists():
        return ParsedTree()
    return parse_text(path.read_text())
