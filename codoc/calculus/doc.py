"""Blocks, kinds, and the heading fold (spec §2, §6 ``tree``).

A block is a kind, a rank, a level (headings only), and a content mapping. A
kind declares which content fields are ``Text``; those fields are attributed
by the calculus. The two built-in kinds, ``heading`` and ``paragraph``, have
one field, ``text``. Plugin kinds register their Text fields with
``register_kind``; a field not listed is an opaque JSON value replaced whole by
``block.set``.

Features are a view: a live heading and every following live block until the
next live heading at the same or a shallower level. Gaps in levels are
tolerated (an H1 followed by an H3 is a parent and child).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codoc.calculus.ids import BlockId, OpId
from codoc.calculus.text import Text

HEADING = "heading"
PARAGRAPH = "paragraph"

_TEXT_FIELDS: dict[str, tuple[str, ...]] = {HEADING: ("text",), PARAGRAPH: ("text",)}
_UNEXPORTED: set[str] = set()
MAX_LEVEL = 6


def register_kind(kind: str, text_fields: tuple[str, ...], *, exported: bool = True) -> None:
    """Declare a plugin kind's attributed fields, and whether its blocks appear
    in the Markdown export (a comment thread does not)."""
    _TEXT_FIELDS[kind] = tuple(text_fields)
    (_UNEXPORTED.discard if exported else _UNEXPORTED.add)(kind)


def is_exported(kind: str) -> bool:
    return kind not in _UNEXPORTED


def text_fields(kind: str) -> tuple[str, ...]:
    return _TEXT_FIELDS.get(kind, ())


def is_text_field(kind: str, path: str) -> bool:
    return path in text_fields(kind)


@dataclass
class Block:
    id: BlockId
    kind: str
    rank: str
    level: int = 0
    content: dict = field(default_factory=dict)   # field -> Text | JSON
    dead: OpId | None = None

    @property
    def live(self) -> bool:
        return self.dead is None

    @property
    def is_heading(self) -> bool:
        return self.kind == HEADING

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
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Block":
        kind = d["kind"]
        content = {}
        for k, v in (d.get("content") or {}).items():
            content[k] = Text.from_json(v) if k in text_fields(kind) else v
        return cls(d["id"], kind, d["rank"], int(d.get("level") or 0), content, d.get("dead"))


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


def dominated(blocks: list[Block], start: int) -> int:
    """Given the ordered live blocks and the index of a block, the exclusive end
    index of the range that block owns: itself, or for a heading, itself plus
    everything until the next heading at the same or a shallower level."""
    b = blocks[start]
    if not b.is_heading:
        return start + 1
    end = start + 1
    while end < len(blocks):
        n = blocks[end]
        if n.is_heading and n.level <= b.level:
            break
        end += 1
    return end


def body_range(blocks: list[Block], start: int) -> int:
    """A heading's *own* body: itself plus the following non-heading blocks,
    stopping at any heading. This is what ``block.delete`` on a heading covers
    (descendant headings are not deleted; a gesture promotes them)."""
    b = blocks[start]
    if not b.is_heading:
        return start + 1
    end = start + 1
    while end < len(blocks) and not blocks[end].is_heading:
        end += 1
    return end


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
