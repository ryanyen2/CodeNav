"""Canonical `.codoc/` control-file names.

A dependency-free leaf module so every layer (loop_b, status, agent.hook,
autorealize) shares one definition without import cycles — `status` imports
`loop_b` indirectly, so the constant cannot live in `loop_b`.
"""
from __future__ import annotations

DOC_FILENAME = "tree.doc.json"             # webview's authored rich doc (U2b Loop B input)
REALIZE_FILENAME = "realize.md"
REALIZE_MANIFEST_FILENAME = "realize.json"  # machine-readable directive manifest (ids + targets)
EDITS_FILENAME = "edits.json"               # IDE→loop provenance annotations + live doc-ahead intents
DRIFT_FILENAME = "drift.json"               # loop-computed per-feature drift/trust signal (sidecar re-emits)
RESOLUTION_FILENAME = "resolution.json"     # loop-computed realize-divergence per target feature (U5)
