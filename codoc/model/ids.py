"""Short, prefixed, human-glanceable ids for tree entities.

Features render their id into the ``.codoc`` file as ``⟨f-id⟩`` markers, so the
id needs to be short enough to sit at the end of a title line yet collision-safe
for a single-repo feature tree. 8 hex chars (32 bits) is ample.
"""
from __future__ import annotations

import uuid


def _short(prefix: str, n: int = 8) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:n]}"


def new_feature_id() -> str:
    return _short("f")


def new_binding_id() -> str:
    return _short("b")


def new_event_id() -> str:
    return _short("e")


def new_directive_id() -> str:
    return _short("d")
