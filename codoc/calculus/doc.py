"""Blocks and the heading fold.

    Block := { id, kind, rank, level, content, dead? }

Kinds, by what their content is:

    heading    {text: Text}     a feature's title
    prose      paragraph | bullet | numbered
               {text: Text}     one line of inline text (bold, code, link, cite)
    source     table | diagram | code
               {source: Text}   raw multi-line text with no inline marks: pipe-table
                                rows, mermaid, or a snippet. diagram may carry
                                ``derived``; code may carry ``lang``.
    plugin     image | url | pdf | screenshot | comment | …   (codoc.kinds)
               a declared schema; its Text fields are inline text

Heading and the prose kinds have the same field, so ``block.set(kind)`` turns
one into another, ``block.split`` splits any of them, and ``block.merge`` joins
two (a heading is never merged away). A field that is not Text is a JSON value
replaced whole by ``block.set``.

``level`` is the outline depth of a heading (1..6), the indent depth of a list
item (1..6), and 0 for every other kind.

A feature is a view: a live heading and every following live block until the
next live heading at the same or a shallower level. Level gaps are tolerated
(an H1 followed by an H3 is parent and child).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codoc.calculus.ids import BlockId, OpId
from codoc.calculus.text import Text

HEADING = "heading"
PARAGRAPH = "paragraph"
BULLET = "bullet"
NUMBERED = "numbered"
TABLE = "table"
DIAGRAM = "diagram"
CODE_BLOCK = "code"

ITEMS = frozenset({BULLET, NUMBERED})
PROSE = frozenset({PARAGRAPH, BULLET, NUMBERED})     # body kinds holding one line of inline text
LEVELLED = frozenset({HEADING, BULLET, NUMBERED})    # kinds whose ``level`` means something
SOURCE = frozenset({TABLE, DIAGRAM, CODE_BLOCK})     # kinds whose ``source`` Text is raw and multi-line

_TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    **{k: ("text",) for k in (HEADING, PARAGRAPH, BULLET, NUMBERED)},
    **{k: ("source",) for k in SOURCE},
}
_UNEXPORTED: set[str] = set()
_OUT_OF_FLOW: set[str] = set()
MAX_LEVEL = 6


def register_kind(kind: str, text_fields: tuple[str, ...], *, exported: bool = True, in_flow: bool = True) -> None:
    """Declare a medium or note kind: its Text fields, whether it appears in
    the Markdown export, and whether it sits in the reading flow."""
    _TEXT_FIELDS[kind] = tuple(text_fields)
    (_UNEXPORTED.discard if exported else _UNEXPORTED.add)(kind)
    (_OUT_OF_FLOW.discard if in_flow else _OUT_OF_FLOW.add)(kind)


def is_exported(kind: str) -> bool:
    return kind not in _UNEXPORTED


def in_flow(kind: str) -> bool:
    return kind not in _OUT_OF_FLOW


def text_fields(kind: str) -> tuple[str, ...]:
    return _TEXT_FIELDS.get(kind, ())


def is_text_field(kind: str, path: str) -> bool:
    return path in text_fields(kind)


def is_source_field(kind: str, path: str) -> bool:
    """A raw field: its text is taken as written, never parsed as inline Markdown."""
    return kind in SOURCE and path == "source"


def is_one_line(kind: str) -> bool:
    """Kinds whose text cannot hold a newline: a line break there is a new block."""
    return kind == HEADING or kind in PROSE


def level_for(kind: str, level: int | None) -> int:
    """The level a block of ``kind`` takes: clamped to 1..MAX_LEVEL for a
    heading or a list item (1 when not given), 0 for anything else."""
    return clamp_level(level or 1) if kind in LEVELLED else 0


@dataclass
class Block:
    id: BlockId
    kind: str
    rank: str
    level: int = 0
    content: dict = field(default_factory=dict)   # field -> Text | JSON
    dead: OpId | None = None
    # A dead heading owns no code, so its bindings leave the store when it
    # dies. They are kept here as `[{file, symbol, fingerprint, types_hash}]`:
    # the removal directive names the symbols to delete, and an undo re-attaches them.
    detached: list = field(default_factory=list)

    @property
    def live(self) -> bool:
        return self.dead is None

    def text(self, path: str = "text") -> Text:
        t = self.content.get(path)
        if not isinstance(t, Text):
            raise KeyError(f"{self.id}.{path} is not a Text field")
        return t

    def to_json(self) -> dict:
        content = {k: (v.to_json() if isinstance(v, Text) else v) for k, v in self.content.items()}
        d = {"id": self.id, "kind": self.kind, "rank": self.rank, "level": self.level, "content": content}
        if self.dead:
            d["dead"] = self.dead
        if self.detached:
            d["detached"] = [dict(e) for e in self.detached]
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Block":
        kind = d["kind"]
        content = {}
        for k, v in (d.get("content") or {}).items():
            content[k] = Text.from_json(v) if k in text_fields(kind) else v
        return cls(d["id"], kind, d["rank"], int(d.get("level") or 0), content, d.get("dead"),
                   [dict(e) for e in (d.get("detached") or [])])


def empty_content(kind: str, content: dict | None = None) -> dict:
    """Content for a new block: every Text field starts empty; other fields are
    taken from ``content`` as given."""
    out: dict = {}
    for k, v in (content or {}).items():
        if k not in text_fields(kind):
            out[k] = v
    for f in text_fields(kind):
        out[f] = Text()
    return out


def clamp_level(level: int) -> int:
    return max(1, min(MAX_LEVEL, int(level)))


@dataclass
class TreeNode:
    id: BlockId
    level: int
    children: list["TreeNode"] = field(default_factory=list)


def fold_tree(headings: list[Block]) -> list[TreeNode]:
    """Fold live headings in rank order into a forest by level: a heading
    deeper than the preceding one is its child, whatever the numeric gap."""
    roots: list[TreeNode] = []
    stack: list[TreeNode] = []
    for h in headings:
        node = TreeNode(h.id, h.level)
        while stack and stack[-1].level >= h.level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
    return roots


def parent_of(headings: list[Block], index: int) -> BlockId | None:
    """The parent heading id of ``headings[index]``: the nearest preceding
    heading with a strictly shallower level."""
    level = headings[index].level
    for j in range(index - 1, -1, -1):
        if headings[j].level < level:
            return headings[j].id
    return None
