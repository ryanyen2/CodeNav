"""U3 — the SSE PayloadStream dedup (broadcast-storm guard)."""
from __future__ import annotations

import json
from pathlib import Path

from codoc.model.hlc import HLC
from codoc.serve.push import PayloadStream


def _seed(cd: Path, features: dict) -> None:
    cd.mkdir(parents=True, exist_ok=True)
    (cd / "status.json").write_text(json.dumps(
        {"state": "in_sync", "pending": 0, "at": HLC.now().to_str()}))
    (cd / "tree.bindings.json").write_text(json.dumps(
        {"features": features, "by_feature": {}}))


def test_stream_snapshots_then_suppresses_identical(tmp_path):
    cd = tmp_path / ".codoc"
    _seed(cd, {})
    stream = PayloadStream(str(cd))

    assert stream.next_if_changed() is not None       # cold snapshot
    assert stream.next_if_changed() is None           # identical → suppressed


def test_stream_emits_on_real_change(tmp_path):
    cd = tmp_path / ".codoc"
    _seed(cd, {})
    stream = PayloadStream(str(cd))
    stream.next_if_changed()  # snapshot

    (cd / "tree.bindings.json").write_text(json.dumps(
        {"features": {"f": {"title": "X", "parent_id": None}}, "by_feature": {}}))
    nxt = stream.next_if_changed()
    assert nxt is not None
    assert "f" in nxt["nodes"]
