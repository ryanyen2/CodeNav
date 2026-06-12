"""Parse ``tree.codoc`` text into a structural view.

Lines:
  * feature      ``  - Title  ⟨f-1a2b⟩`` (``~`` marker = retired; absent id = new)
  * description  indented prose lines beneath a feature; blank lines are kept as
                 paragraph breaks. A description ends only at the next feature
                 line, the pending-changes sentinel, or EOF — never at a blank.
  * steering     ``> …`` blockquote lines inside a description are notes TO THE
                 AGENT, not prose: collected per-node into
                 :attr:`ParsedNode.comments` and EXCLUDED from ``description``.
                 A contiguous run of ``>`` lines is one comment; Loop B turns
                 each into a realize directive and the next render (store-driven)
                 consumes it from the text.
  * comment      ``# …`` (ignored, except the legacy pending-changes sentinel)
  * proposals    in-situ diff hunks — a col-0 op char (``+``/``-``/``~``) then a
                 node carrying a hidden ``⟨e-id⟩`` (live nodes carry ``⟨f-id⟩``),
                 terminated by a blank line. They are display-only and skipped
                 here (verdicts arrive via ``.codoc/inbox.json``).

Indentation depth gives the parent. Identity comes from ``⟨f-id⟩``; the IDE hides
it from the human but it stays on disk so renames never lose attribution.
Inline ``[label](codoc:file#symbol)`` refs stay verbatim in the description (so
the round-trip is exact); :func:`extract_refs` pulls them out as metadata.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from codoc.codoc_file.render import PENDING_SENTINEL

_FEATURE_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>[-~])\s+(?P<rest>.*\S)\s*$")
_ID_RE = re.compile(r"⟨(f-[0-9a-f]+|new)⟩")
# Detects a line that looks like a feature line (indented non-space + space + text)
# but uses an unrecognized marker — e.g. "    * Title ⟨f-id⟩". `>` is excluded:
# it is the steering-comment marker, never a mangled feature line.
_BAD_MARKER_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>[^\s\-~#>])[ \t]+\S")
# An in-situ proposal title: col-0 op char, space, optional tree indent, a
# feature marker, space. Combined with an ``⟨e-id⟩`` (live nodes carry ``⟨f-id⟩``)
# this is unambiguous against a live feature line like ``- Title``.
_PROPOSAL_TITLE_RE = re.compile(r"^[+\-~] \s*[-~] ")
_EVENT_ID_RE = re.compile(r"⟨e-[0-9a-f]+⟩")
# Legacy depth-0 hunk (``- ~ Title``) for trees written before in-situ proposals.
_DIFF_HUNK_RE = re.compile(r"^[+\-~] [-~] ")
# Inline code citation: [label](codoc:file.py#symbol)  — symbol part optional.
_REF_RE = re.compile(r"\[(?P<label>[^\]]*)\]\(codoc:(?P<file>[^)#]+)(?:#(?P<symbol>[^)]+))?\)")
# External markdown link: [label](https://…) — a page the realizing agent should
# consult (WebFetch) before implementing. codoc: links are refs, not links.
_LINK_RE = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<url>https?://[^)\s]+)\)")
# **bold** span — the author's emphasis. Newly-bolded spans in an edit signal
# "focus here" and ride into realize directives as `Focus:` lines.
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")


@dataclass
class Ref:
    label: str
    file: str
    symbol: str | None


@dataclass
class Link:
    label: str
    url: str


@dataclass
class ParsedNode:
    id: str | None  # None = newly authored (no ⟨f-id⟩, or ⟨new⟩)
    title: str
    description: str
    parent_id: str | None
    retired: bool
    refs: list[Ref] = field(default_factory=list)
    # Steering comments (`> …` runs) — notes to the agent, not part of the prose.
    comments: list[str] = field(default_factory=list)


@dataclass
class ParsedTree:
    nodes: list[ParsedNode] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def extract_refs(text: str) -> list[Ref]:
    """Pull inline ``[label](codoc:file#symbol)`` citations out of prose."""
    return [
        Ref(label=m.group("label"), file=m.group("file"), symbol=m.group("symbol"))
        for m in _REF_RE.finditer(text)
    ]


def extract_links(text: str) -> list[Link]:
    """Pull external ``[label](https://…)`` links out of prose — pages the
    realizing agent should consult before implementing."""
    return [Link(label=m.group("label"), url=m.group("url"))
            for m in _LINK_RE.finditer(text or "")]


def extract_bold(text: str) -> list[str]:
    """Pull ``**bold**`` spans out of prose, in document order, deduped."""
    seen: dict[str, None] = {}
    for m in _BOLD_RE.finditer(text or ""):
        span = m.group(1).strip()
        if span:
            seen.setdefault(span, None)
    return list(seen.keys())


def parse_text(text: str) -> ParsedTree:
    tree = ParsedTree()
    stack: list[tuple[int, ParsedNode]] = []  # (indent, node)
    desc_owner: ParsedNode | None = None
    desc_buf: list[str] = []
    in_pending = False
    in_proposal = False  # inside an in-situ proposal block (until the next blank)
    skip_desc = False  # True after a bad-marker line; cleared on next valid feature

    comment_buf: list[str] = []  # current contiguous `>` run

    def flush_comment() -> None:
        nonlocal comment_buf
        if desc_owner is not None and comment_buf:
            text = "\n".join(comment_buf).strip()
            if text:
                desc_owner.comments.append(text)
        comment_buf = []

    def flush_desc() -> None:
        nonlocal desc_owner, desc_buf
        flush_comment()
        if desc_owner is not None:
            lines = [dl.strip() for dl in desc_buf]
            while lines and not lines[0]:
                lines.pop(0)
            while lines and not lines[-1]:
                lines.pop()
            desc_owner.description = "\n".join(lines)
            desc_owner.refs = extract_refs(desc_owner.description)
        desc_owner = None
        desc_buf = []

    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()

        # Everything past the legacy sentinel is display-only proposal diff.
        if in_pending:
            continue
        if s.startswith(PENDING_SENTINEL):
            flush_desc()
            in_pending = True
            continue

        # In-situ proposal block: skip its lines until the terminating blank.
        if in_proposal:
            if not s:
                in_proposal = False
            continue
        if _PROPOSAL_TITLE_RE.match(line) and _EVENT_ID_RE.search(line):
            flush_desc()
            in_proposal = True
            continue

        if not s:
            # Blank line: a paragraph break inside the current description (kept),
            # or filler between nodes (dropped when the description is flushed).
            if desc_owner is not None:
                if comment_buf:
                    # The blank ends a steering-comment run. The comment "owns"
                    # one paragraph break — don't double it, or a comment-only
                    # edit would read as a prose change.
                    flush_comment()
                    if desc_buf and desc_buf[-1] != "":
                        desc_buf.append("")
                else:
                    desc_buf.append("")
            continue
        if _DIFF_HUNK_RE.match(line):
            continue  # stray proposal hunk outside the pending block
        if s.startswith("#"):
            continue

        mf = _FEATURE_RE.match(line)
        if mf:
            skip_desc = False
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

        # Detect mangled feature lines (wrong marker, e.g. `*` or `+`).
        # They contain a feature id or look structurally like a feature line.
        # Don't bleed their text (or their following description) into a previous node.
        if _BAD_MARKER_RE.match(line) and _ID_RE.search(s):
            tree.errors.append(f"Unrecognized feature marker on line: {s!r}")
            skip_desc = True
            continue

        # otherwise: a description line for the current node — `> …` lines are
        # steering comments (notes to the agent), everything else is prose.
        if skip_desc:
            continue
        if desc_owner is not None:
            if s.startswith(">"):
                comment_buf.append(s[1:].lstrip())
            else:
                flush_comment()
                desc_buf.append(s)

    flush_desc()
    return tree


def parse_tree_file(codoc_dir: str | Path) -> ParsedTree:
    from codoc.codoc_file.render import tree_path

    path = tree_path(codoc_dir)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return ParsedTree()
    except OSError as exc:
        # Unreadable (permissions, transient FS error) — degrade to "no edits"
        # with a surfaced error rather than crashing the loop pass.
        return ParsedTree(errors=[f"tree.codoc unreadable: {exc}"])
    return parse_text(text)
