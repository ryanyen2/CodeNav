"""``Kind``: the declaration of a source or plugin block kind, and the registry.

A kind declares which content fields are attributed ``Text``, a schema for the
rest, and up to three codecs:

    lift(ctx)              code → content; read-only on code; idempotent; run by the loop
    lower(old, new, ctx)   content → a directive line, or None; run when a hand-off renders
    consult(content)       what the realizing agent reads before implementing

A kind never produces ops; ``kinds.write`` turns content into ops.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from codoc.calculus.derive import field_text
from codoc.calculus.doc import Block, is_one_line, register_kind, text_fields
from codoc.calculus.store import Binding, Store
from codoc.calculus.text import Text


@dataclass(frozen=True)
class LiftContext:
    store: Store
    block: Block
    feature: str | None
    bindings: list[Binding]
    graph: object | None = None          # codoc.index.graph.Graph when the loop has one
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
    exported: bool = True

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
    if is_one_line(kind.name):
        raise ValueError(f"{kind.name} is a prose kind of the calculus")
    _REGISTRY[kind.name] = kind
    register_kind(kind.name, kind.text_fields, exported=kind.exported, in_flow=kind.in_flow)
    return kind


def get(name: str) -> Kind | None:
    return _REGISTRY.get(name)


def kinds() -> list[Kind]:
    return list(_REGISTRY.values())


def plain(block: Block, store: Store | None = None, mode: str = "rejected") -> dict:
    """A block's content as a codec sees it: each Text field as a string (raw
    for a source field, inline Markdown otherwise) in the read mode ``mode``."""
    fields = text_fields(block.kind)
    return {k: field_text(block, k, mode, store) if k in fields and isinstance(v, Text) else v
            for k, v in block.content.items()}
