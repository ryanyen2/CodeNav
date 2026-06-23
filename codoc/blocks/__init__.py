"""Typed-medium blocks and their plugin codecs.

A block is a typed projection of a feature's intent (diagram / image / latex /
code / url); a :class:`~codoc.blocks.base.BlockPlugin` declares which of three
capabilities (``lift`` / ``lower`` / ``consult``) the medium supports and how
each direction dispatches. The loops dispatch through the
:mod:`~codoc.blocks.registry`. See ``docs/plans/2026-06-22-001-...`` for the
contract (KTD5/KTD8).
"""
from __future__ import annotations

from codoc.blocks.base import (
    BindingMode,
    BlockPlugin,
    Capability,
    Dispatch,
    LiftContext,
    LiftResult,
    LowerContext,
    LowerResult,
)
from codoc.blocks.registry import (
    BlockRegistry,
    PluginContractError,
    UnknownBlockKind,
    default_registry,
)

__all__ = [
    "BindingMode",
    "BlockPlugin",
    "Capability",
    "Dispatch",
    "LiftContext",
    "LiftResult",
    "LowerContext",
    "LowerResult",
    "BlockRegistry",
    "PluginContractError",
    "UnknownBlockKind",
    "default_registry",
]
