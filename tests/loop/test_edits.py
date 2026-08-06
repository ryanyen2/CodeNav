"""The provenance/intent channel (loop/edits.py) + its Loop B integration:
edits.json annotation stamping, realize.json directive manifest, hold_set."""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop import edits
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    return str(root), str(codoc_dir)


# ─── the channel file itself ─────────────────────────────────────────────────

def test_annotations_roundtrip_and_drain_keeps_intents(dirs):
    _, codoc_dir = dirs
    edits.append_annotation(codoc_dir, edits.EditAnnotation(
        feature_id="f-1", fields=["description"], actor="claude-code", mode="suggest"))
    # host-maintained intent rides in the same file
    data = json.loads(edits.edits_path(codoc_dir).read_text())
    data["intents"] = [{"id": "d-f1", "feature_id": "f-2", "actor": "human", "ts": 0}]
    edits.edits_path(codoc_dir).write_text(json.dumps(data))

    anns = edits.drain_annotations(codoc_dir)
    assert anns["f-1"].actor == "claude-code"
    assert anns["f-1"].mode == "suggest"
    # edits consumed, intents kept
    assert edits.read_annotations(codoc_dir) == {}
    assert [i.feature_id for i in edits.read_intents(codoc_dir)] == ["f-2"]


def test_manifest_requires_realize_md(dirs):
    _, codoc_dir = dirs
    edits.write_manifest(codoc_dir, [edits.Directive(id="d-1", feature_id="f-1", kind="amend")])
    # no realize.md beside it → stale, ignored AND cleaned up
    assert edits.read_manifest(codoc_dir) == []
    assert not edits.manifest_path(codoc_dir).exists()

    realize_path(codoc_dir).write_text("### 1. ⟨d-2⟩ UPDATE FEATURE …")
    edits.write_manifest(codoc_dir, [edits.Directive(id="d-2", feature_id="f-1", kind="amend",
                                                     caused_by="e-9")])
    got = edits.read_manifest(codoc_dir)
    assert [(d.id, d.feature_id, d.caused_by) for d in got] == [("d-2", "f-1", "e-9")]


def test_hold_set_unions_intents_and_directives_with_staleness(dirs):
    _, codoc_dir = dirs
    now = 1_000_000_000_000
    fresh = {"id": "d-fa", "feature_id": "f-fresh", "actor": "human", "ts": now - 1000}
    stale = {"id": "d-fb", "feature_id": "f-stale", "actor": "human",
             "ts": now - edits.INTENT_STALE_MS - 1}
    edits._write_edits_file(codoc_dir, edits=[], intents=[fresh, stale])
    realize_path(codoc_dir).write_text("### 1. ⟨d-1⟩ …")
    edits.write_manifest(codoc_dir, [edits.Directive(id="d-1", feature_id="f-queued", kind="amend")])

    held = edits.hold_set(codoc_dir, now_ms=now)
    assert held == {"f-fresh", "f-queued"}  # stale intent ignored — no forever-hold


# ─── Loop B integration ──────────────────────────────────────────────────────

def _seed_feature(codoc_dir, *, title="Validator", desc="Validates input.") -> str:
    s = open_store(codoc_dir)
    f = Feature(title=title, description=desc)
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file="v.py", symbol_path="v.py::check",
                             fingerprint="x"))
    write_tree(s, codoc_dir)
    s.close()
    return f.id


def test_loop_b_stamps_user_ops_from_annotations(dirs):
    """The edit arrives as a `set_description` COMMAND (U3/U4); the edits.json
    annotation stamps the resulting AMEND event with the declared author (the command
    apply path consults annotations, U7). dry_run=False because commands apply only on
    a real pass."""
    root, codoc_dir = dirs
    fid = _seed_feature(codoc_dir)
    # an agent settle annotated by the IDE host
    edits.append_annotation(codoc_dir, edits.EditAnnotation(
        feature_id=fid, fields=["description"], actor="claude-code", mode="suggest"))
    edits.append_command(codoc_dir, edits.Command(
        id="cmd-ann-1", kind="set_description", feature_id=fid,
        payload={"description": "Validates and sanitizes input."}))

    run_loop_b(root, codoc_dir, dry_run=False)

    s = open_store(codoc_dir)
    evs = [e for e in s.recent_events(10) if e.op.kind.value == "amend"]
    assert evs and evs[0].actor == "claude-code" and evs[0].mode == "suggest"
    s.close()
    # annotation consumed
    assert edits.read_annotations(codoc_dir) == {}


def test_loop_b_defaults_to_human_pen_without_annotation(dirs):
    """A command with no annotation defaults the AMEND's authorship to human/pen."""
    root, codoc_dir = dirs
    fid = _seed_feature(codoc_dir)
    edits.append_command(codoc_dir, edits.Command(
        id="cmd-noann-1", kind="set_description", feature_id=fid,
        payload={"description": "Validates trimmed input."}))

    run_loop_b(root, codoc_dir, dry_run=False)

    s = open_store(codoc_dir)
    evs = [e for e in s.recent_events(10) if e.op.kind.value == "amend"]
    assert evs and (evs[0].actor, evs[0].mode) == ("human", "pen")
    s.close()


def test_directives_get_ids_in_manifest_and_heading_after_handoff(dirs):
    root, codoc_dir = dirs
    fid = _seed_feature(codoc_dir)
    edits.append_annotation(codoc_dir, edits.EditAnnotation(
        feature_id=fid, fields=["description"], actor="human", mode="pen",
        suggestion_id="d-sugg1"))
    edits.append_command(codoc_dir, edits.Command(
        id="cmd-did-1", kind="set_description", feature_id=fid,
        payload={"description": "Rewrite the validator; it should reject tabs."}))

    # Held-draft model: the AMEND mints a directive with a stable id + caused_by, held
    # in the manifest (handed_off=False) — NOT yet in realize.md.
    res = run_loop_b(root, codoc_dir, dry_run=False)
    assert res.queued is False and len(res.directive_ids) == 1
    did = res.directive_ids[0]
    assert did.startswith("d-")
    manifest = edits.read_manifest(codoc_dir)
    assert [(d.id, d.feature_id, d.kind, d.caused_by, d.handed_off) for d in manifest] == \
        [(did, fid, "amend", "d-sugg1", False)]
    assert not realize_path(codoc_dir).exists()
    # the held feature is in the hold set (doc wins) even while held
    assert edits.hold_set(codoc_dir) == {fid}

    # Hand off → the directive's ⟨id⟩ appears in the realize.md heading.
    edits.append_handoffs(codoc_dir, [fid])
    run_loop_b(root, codoc_dir, dry_run=False)
    body = realize_path(codoc_dir).read_text()
    assert f"⟨{did}⟩" in body and "UPDATE FEATURE" in body


# ─── the intent drain — Loop B applies doc-ahead suggestions (row 9) ──────────

def _write_intents(codoc_dir, intents):
    edits._write_edits_file(codoc_dir, edits=[], intents=intents)


def test_read_intents_payload_fields(dirs):
    _, codoc_dir = dirs
    _write_intents(codoc_dir, [
        {"id": "d-a", "feature_id": "f-1", "actor": "human", "ts": 0,
         "description": "New prose."},
        {"id": "d-b", "feature_id": "f-2", "actor": "human", "ts": 0,
         "title": "Renamed", "description": ""},
        {"id": "d-c", "feature_id": "f-3", "actor": "human", "ts": 0},  # hold-only
    ])
    got = {i.id: i for i in edits.read_intents(codoc_dir)}
    assert got["d-a"].title is None and got["d-a"].description == "New prose."
    assert got["d-b"].title == "Renamed" and got["d-b"].description == ""  # "" = clear, not absent
    assert got["d-c"].title is None and got["d-c"].description is None


def test_intent_drain_applies_and_holds_draft_with_causality(dirs):
    """A payload intent (doc-ahead suggestion) is applied by Loop B (the agent-side
    apply): store updated, event stamped human/suggest/caused_by=suggestion id, a
    HELD-draft directive minted with the same caused_by, and tree.codoc re-rendered so
    the text catches up. Held (not realized) — an applied suggestion is a doc edit, not
    surprise code; the maintainer hands it off to realize."""
    root, codoc_dir = dirs
    fid = _seed_feature(codoc_dir)
    _write_intents(codoc_dir, [{"id": "d-sugg9", "feature_id": fid, "actor": "human",
                                "ts": 0, "description": "Should reject empty payloads."}])

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.user_edits == 1 and res.queued is False
    s = open_store(codoc_dir)
    assert s.get_feature(fid).description == "Should reject empty payloads."
    ev = [e for e in s.recent_events(10) if e.op.kind.value == "amend"][0]
    assert (ev.actor, ev.mode, ev.caused_by) == ("human", "suggest", "d-sugg9")
    s.close()
    manifest = edits.read_manifest(codoc_dir)
    assert [(d.caused_by, d.handed_off) for d in manifest] == [("d-sugg9", False)]
    assert "Should reject empty payloads." in tree_path(codoc_dir).read_text()


def test_intent_drain_skips_satisfied_holdonly_and_stale(dirs):
    root, codoc_dir = dirs
    fid = _seed_feature(codoc_dir)
    import time as _t
    now = int(_t.time() * 1000)
    _write_intents(codoc_dir, [
        # satisfied — payload equals the store already
        {"id": "d-same", "feature_id": fid, "actor": "human", "ts": now,
         "description": "Validates input."},
        # hold-only — no payload
        {"id": "d-hold", "feature_id": fid, "actor": "human", "ts": now},
        # stale — older than the backstop
        {"id": "d-old", "feature_id": fid, "actor": "human",
         "ts": now - edits.INTENT_STALE_MS - 1, "description": "Should do things."},
    ])

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.user_edits == 0 and not res.queued
    s = open_store(codoc_dir)
    assert s.get_feature(fid).description == "Validates input."
    s.close()
    # the drain is read-only: intents stay host-owned
    assert [i.id for i in edits.read_intents(codoc_dir)] == ["d-same", "d-hold", "d-old"]


def test_intent_drain_descriptive_applies_without_directive(dirs):
    root, codoc_dir = dirs
    fid = _seed_feature(codoc_dir)
    _write_intents(codoc_dir, [{"id": "d-doc", "feature_id": fid, "actor": "human",
                                "ts": 0, "description": "Validates and trims input."}])

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.user_edits == 1 and not res.queued  # row 7: documenting ≠ building
    s = open_store(codoc_dir)
    assert s.get_feature(fid).description == "Validates and trims input."
    s.close()


def test_accepted_amend_survives_the_stale_text(dirs):
    """Regression: a verdict-accepted AMEND must not be reverted by the same
    pass diffing the (necessarily stale) on-disk text against the already-
    mutated store. The diff snapshots BEFORE verdicts; the pass re-renders."""
    from codoc.loop import inbox
    from codoc.loop.apply import apply_op
    from codoc.model.event import NodeOp, NodeOpKind
    import json as _json
    from pathlib import Path

    root, codoc_dir = dirs
    fid = _seed_feature(codoc_dir)
    s = open_store(codoc_dir)
    ev = apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid,
                         description="Validates input against the schema."),
                  s, source="loop_a_agent", applied=False)
    s.close()
    Path(inbox.inbox_path(codoc_dir)).write_text(
        _json.dumps({"version": 1, "verdicts": [{"event_id": ev.id, "accept": True}]}))

    res = run_loop_b(root, codoc_dir, dry_run=False)
    assert res.accepted == 1

    s = open_store(codoc_dir)
    assert s.get_feature(fid).description == "Validates input against the schema."
    s.close()
    # text re-rendered → caught up → the NEXT pass sees no phantom user edit
    assert "against the schema" in tree_path(codoc_dir).read_text()
    res2 = run_loop_b(root, codoc_dir, dry_run=False)
    assert res2.user_edits == 0
    s = open_store(codoc_dir)
    assert s.get_feature(fid).description == "Validates input against the schema."
    s.close()


# ─── realized.jsonl — durable directive outcomes ──────────────────────────────

def test_drained_manifest_logs_outcomes(dirs):
    """When the queue drains (realize.md deleted), the handed-off directives
    must land in realized.jsonl before the manifest entry vanishes."""
    _, codoc_dir = dirs
    edits.write_manifest(codoc_dir, [
        edits.Directive(id="d-1", feature_id="f-1", kind="amend",
                        caused_by="e-9", text="UPDATE FEATURE: …")])
    assert edits.read_manifest(codoc_dir) == []  # stale → cleared

    (outcome,) = edits.read_realized(codoc_dir)
    assert outcome["id"] == "d-1"
    assert outcome["feature_id"] == "f-1"
    assert outcome["caused_by"] == "e-9"
    assert outcome["completed_at"]


def test_drain_with_held_drafts_logs_only_completed(dirs):
    """Held drafts survive; completed handed-off entries are logged and dropped
    from the manifest exactly once."""
    _, codoc_dir = dirs
    edits.write_manifest(codoc_dir, [
        edits.Directive(id="d-done", feature_id="f-1", kind="amend", handed_off=True),
        edits.Directive(id="d-draft", feature_id="f-2", kind="amend", handed_off=False),
    ])

    got = edits.read_manifest(codoc_dir)
    assert [d.id for d in got] == ["d-draft"]
    assert [o["id"] for o in edits.read_realized(codoc_dir)] == ["d-done"]

    # Re-reads must not duplicate the outcome.
    edits.read_manifest(codoc_dir)
    edits.read_manifest(codoc_dir)
    assert [o["id"] for o in edits.read_realized(codoc_dir)] == ["d-done"]


def test_read_realized_missing_file(dirs):
    _, codoc_dir = dirs
    assert edits.read_realized(codoc_dir) == []
