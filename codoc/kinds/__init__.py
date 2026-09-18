"""Block kinds: a schema plus the arrows a kind supports (`03-extensions.md`).

The calculus knows ``heading`` and ``paragraph``. Every other kind registers
here: which content fields are attributed ``Text`` (identity, per-word
attribution, plans, translation and History come with that for free), a
schema for the rest, and up to five codecs:

    lift(ctx)                  code → content; read-only on code; idempotent; run by the loop
    lower(old, new, ctx)       content → a directive string, or None; run when a hand-off renders
    consult(content)           what the realizing agent reads before implementing
    text_view(content)         what an LLM reads when the block is context (default: consult)
    write_back(content, reply) how an agent's reply becomes content (default: the reply itself)

A kind never touches ops. ``write.py`` is the one boundary that turns content
into ops: ``block.set`` for a non-Text field, ``ingest`` for a Text field, both
with the caller's actor and, when the caller proposes, a plan tag.
``export.py`` fences every plugin kind as JSON; a kind with ``exported=False``
(a comment) is left out of ``tree.codoc``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from codoc.calculus.doc import Block, register_kind, text_fields
from codoc.calculus.store import Binding, Store
from codoc.calculus.text import Text


@dataclass(frozen=True)
class LiftContext:
    store: Store
    block: Block
    feature: str | None
    bindings: list[Binding]
    graph: object | None = None          # codoc.graph.graph.Graph when the loop has one
    codoc_dir: str | None = None


@dataclass(frozen=True)
class LowerContext:
    feature: str
    bindings: list[Binding]


@dataclass(frozen=True)
class Kind:
    name: str
    text_fields: tuple[str, ...] = ()
    in_flow: bool = True                              # a comment hangs off words, it does not sit between them
    schema: dict = field(default_factory=dict)        # field → {"type": "string"|"number"|"boolean"|"object"|"array", "enum"?: [...], "required"?: bool}
    lift: Callable[[LiftContext], dict | None] | None = None
    lower: Callable[[dict, dict, LowerContext], str | None] | None = None
    consult: Callable[[dict], str] = lambda c: ""
    text_view: Callable[[dict], str] | None = None
    write_back: Callable[[dict, dict], dict] | None = None
    exported: bool = True

    def view(self, content: dict) -> str:
        return (self.text_view or self.consult)(content)

    def validate(self, content: dict, *, partial: bool = False) -> str | None:
        """Why ``content`` does not fit the schema, or None. Text fields are
        strings (Markdown) on the way in; unknown fields are refused; a
        ``partial`` write (fields not named are left alone) skips ``required``."""
        for k, v in content.items():
            if k in self.text_fields:
                if not isinstance(v, str):
                    return f"{k} must be a string"
                continue
            spec = self.schema.get(k)
            if spec is None:
                return f"unknown field {k!r}"
            if not isinstance(v, _PY_TYPES[spec.get("type", "string")]) or isinstance(v, bool) and spec.get("type") != "boolean":
                return f"{k} must be {spec.get('type', 'string')}"
            if "enum" in spec and v not in spec["enum"]:
                return f"{k} must be one of {spec['enum']}"
        if not partial:
            for k, spec in self.schema.items():
                if spec.get("required") and k not in content:
                    return f"{k} is required"
        return None


_PY_TYPES: dict[str, type | tuple] = {"string": str, "number": (int, float), "boolean": bool, "object": dict, "array": list}

_REGISTRY: dict[str, Kind] = {}


def register(kind: Kind) -> Kind:
    if kind.name in ("heading", "paragraph"):
        raise ValueError(f"{kind.name} is built into the calculus")
    _REGISTRY[kind.name] = kind
    register_kind(kind.name, kind.text_fields, exported=kind.exported, in_flow=kind.in_flow)
    return kind


def get(name: str) -> Kind | None:
    return _REGISTRY.get(name)


def kinds() -> list[Kind]:
    return list(_REGISTRY.values())


def plain(block: Block, store: Store | None = None, mode: str = "rejected") -> dict:
    """A block's content with its Text fields rendered as Markdown: what a
    codec sees. ``mode`` is the read mode (an open plan's words are not the
    content until accepted)."""
    from codoc.calculus.derive import markdown
    out: dict = {}
    for k, v in block.content.items():
        out[k] = markdown(v, mode, store) if isinstance(v, Text) and k in text_fields(block.kind) else v
    return out


from codoc.kinds import builtins as _builtins  # noqa: E402  (registers the shipped kinds)

_builtins.register_all()
