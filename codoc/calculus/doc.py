"""Blocks and the heading fold.

    Block := { id, kind, rank, level, content, layout, dead? }

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

Which fields are Text is the block's ``layout``: fixed by the kind for the
kinds above it, and recorded by ``block.add`` for a plugin kind.

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

MAX_LEVEL = 6


@dataclass(frozen=True)
class Layout:
    """Which content fields of a block are Text, whether it sits in the
    reading flow, and whether the export writes it. Recorded on the block when
    it is added and never changed after, so every clone reads the block the same
    way whether or not it has the kind's code."""
    text: tuple[str, ...] = ()
    flow: bool = True
    export: bool = True

    def to_json(self) -> dict:
        return {"text": list(self.text), "flow": self.flow, "export": self.export}

    @classmethod
    def from_json(cls, d) -> "Layout":
        text = d.get("text", []) if isinstance(d, dict) else None
        if not isinstance(text, list) or not all(isinstance(f, str) and f != LAYOUT_KEY for f in text):
            raise TypeError("layout")
        return cls(tuple(dict.fromkeys(text)), bool(d.get("flow", True)), bool(d.get("export", True)))


OPAQUE = Layout()       # a kind nobody declared: every field is JSON
LAYOUT_KEY = "$layout"  # where a plugin block's `codoc:` fence carries its layout; no field may be named so
BUILTIN: dict[str, Layout] = {
    **{k: Layout(("text",)) for k in (HEADING, PARAGRAPH, BULLET, NUMBERED)},
    **{k: Layout(("source",)) for k in SOURCE},
}
_REGISTERED: dict[str, Layout] = {}


def register_kind(kind: str, layout: Layout) -> None:
    """Declare the layout a writer gives a new block of a plugin kind. A
    builtin kind's layout is fixed and is not changed."""
    if kind not in BUILTIN:
        _REGISTERED[kind] = layout


def layout_of(kind: str) -> Layout:
    """The layout this process gives a new block of ``kind``. Asked only by a
    writer building a ``block.add`` and by the applier for an add that does not
    carry one; every other question about a block asks ``Block.layout``."""
    return BUILTIN.get(kind) or _REGISTERED.get(kind) or OPAQUE


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

    # Who removed the block and when, kept on the block as a segment keeps its
    # killer: a person deleting a paragraph is pending intent, and the
    # directive says so after the op that did it has been folded away.
    dead_actor: str = ""
    dead_at: str = ""
    # The feature whose description the block was removed from, read when it
    # died. "" when no words were removed: a block merged into its neighbour,
    # or one a rejected plan had added.
    dead_in: str = ""
    # Fixed by the kind for a builtin kind; for any other kind, what its
    # `block.add` recorded.
    layout: Layout = OPAQUE

    def __post_init__(self) -> None:
        self.layout = BUILTIN.get(self.kind) or self.layout

    @property
    def live(self) -> bool:
        return self.dead is None

    def kill(self, op: OpId, actor: str = "", at: str = "", feature: str = "") -> None:
        self.dead, self.dead_actor, self.dead_at, self.dead_in = op, actor, at, feature

    def revive(self) -> None:
        self.dead, self.dead_actor, self.dead_at, self.dead_in = None, "", "", ""

    def text(self, path: str = "text") -> Text:
        t = self.content.get(path)
        if not isinstance(t, Text):
            raise KeyError(f"{self.id}.{path} is not a Text field")
        return t

    def to_json(self) -> dict:
        content = {k: (v.to_json() if isinstance(v, Text) else v) for k, v in self.content.items()}
        d = {"id": self.id, "kind": self.kind, "rank": self.rank, "level": self.level, "content": content}
        if self.kind not in BUILTIN:
            d["layout"] = self.layout.to_json()
        if self.dead:
            d["dead"] = self.dead
            if self.dead_actor:
                d["dead_actor"], d["dead_at"] = self.dead_actor, self.dead_at
            if self.dead_in:
                d["dead_in"] = self.dead_in
        if self.detached:
            d["detached"] = [dict(e) for e in self.detached]
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Block":
        layout = BUILTIN.get(d["kind"]) or (Layout.from_json(d["layout"]) if "layout" in d else OPAQUE)
        content = {k: Text.from_json(v) if k in layout.text else v for k, v in (d.get("content") or {}).items()}
        return cls(d["id"], d["kind"], d["rank"], int(d.get("level") or 0), content, d.get("dead"),
                   [dict(e) for e in (d.get("detached") or [])], d.get("dead_actor", ""), d.get("dead_at", ""),
                   d.get("dead_in", ""), layout)


def empty_content(layout: Layout, content: dict | None = None) -> dict:
    """Content for a new block: every Text field starts empty; other fields are
    taken from ``content`` as given."""
    out = {k: v for k, v in (content or {}).items() if k not in layout.text}
    out.update({f: Text() for f in layout.text})
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
