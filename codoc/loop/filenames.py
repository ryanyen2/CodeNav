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
HOST_OPS_FILENAME = "edits.host.jsonl"      # IDE→daemon append-only op log; merged into edits.json under lock
DRIFT_FILENAME = "drift.json"               # loop-computed per-feature drift/trust signal (sidecar re-emits)
INTENT_FILENAME = "intent.jsonl"            # captured author prompts (UserPromptSubmit hook → Loop A context)
REALIZED_LOG_FILENAME = "realized.jsonl"    # durable directive outcomes (queue drained → what happened)
RESOLUTION_FILENAME = "resolution.json"     # loop-computed realize-divergence per target feature (U5)
CONFIG_FILENAME = "config.json"             # authored workspace settings (doc_language) — TRACKED in git,
                                            # unlike every other file here: the authoring language must
                                            # travel with the repo or a contributor's daemon writes English
                                            # prose into a Chinese tree. Schema + resolution: codoc.doclang.
