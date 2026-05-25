"""Binding — attribution of one code chunk to one feature.

The anchor is inlined as ``(file, symbol_path)`` — the exact key the cocoindex
``code_chunks`` index uses — so resolving a binding against current code is a
dict lookup, not a tree-sitter re-resolve. ``fingerprint`` is the chunk's
``tokens_hash``; a mismatch against the live index means the bound code drifted.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from codoc.model.hlc import HLC
from codoc.model.ids import new_binding_id


class Binding(BaseModel):
    id: str = Field(default_factory=new_binding_id)
    feature_id: str
    file: str  # repo-relative posix path
    symbol_path: str  # "pkg/mod.py::Class.method" — joins to the index
    fingerprint: str  # tokens_hash at attribution time; staleness signal
    updated_at: HLC = Field(default_factory=HLC.now)
