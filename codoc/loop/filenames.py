"""Canonical `.codoc/` control-file names.

A dependency-free leaf module so every layer (loop_b, status, agent.hook,
autorealize) shares one definition without import cycles — `status` imports
`loop_b` indirectly, so the constant cannot live in `loop_b`.
"""
from __future__ import annotations

REALIZE_FILENAME = "realize.md"
REALIZE_MANIFEST_FILENAME = "realize.json"  # machine-readable directive manifest (ids + targets)
EDITS_FILENAME = "edits.json"               # IDE→loop provenance annotations + live doc-ahead intents
