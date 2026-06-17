"""U5 — realize-divergence: did the agent's realization match the human's intent?

Three layers, all deterministic (no LLM, no index):

1. The pure classifier (``loop/divergence.py``): FAITHFUL / SCOPE / INTENT.
2. Loop A wiring (``apply_changeset``): a proposed intent op produced under a
   realize directive, on a feature OTHER than the directive's target, is recorded
   as a scope divergence and persisted to ``resolution.json``; on-target work is
   suppressed (held) → faithful → nothing recorded.
3. The sidecar re-emit (``render.write_sidecar``): ``feature_resolution`` mirrors
   the persisted map, filtered to features whose surfaced proposal is still pending.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codoc.codoc_file.render import BINDINGS_FILENAME, write_sidecar
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.divergence import (
    Divergence, Realization, classify_realization, divergent_targets, text_overlap,
)
from codoc.loop.apply import apply_op
from codoc.loop.edits import read_resolution, resolution_path, write_resolution
from codoc.loop.loop_a import apply_changeset
from codoc.model.binding import Binding
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


# ─── 1. the pure classifier ──────────────────────────────────────────────────

def test_faithful_when_only_the_target_was_touched():
    r = Realization(target_feature_id="f-x", touched_feature_ids={"f-x"})
    assert classify_realization(r) is Divergence.FAITHFUL


def test_scope_when_a_feature_beyond_the_target_was_touched():
    r = Realization(target_feature_id="f-x", touched_feature_ids={"f-x", "f-y"})
    assert classify_realization(r) is Divergence.SCOPE


def test_scope_when_a_new_feature_was_added():
    r = Realization(target_feature_id="f-x", added_feature=True)
    assert classify_realization(r) is Divergence.SCOPE


def test_intent_is_off_by_default_even_on_wildly_different_text():
    """The INTENT signal is fuzzy (OQ1) so it is OFF unless an intent_ratio is given."""
    r = Realization(target_feature_id="f-x", intent_text="add rate limiting",
                    realized_text="completely unrelated prose about colors")
    assert classify_realization(r) is Divergence.FAITHFUL          # default ratio 0.0
    assert classify_realization(r, intent_ratio=0.5) is Divergence.INTENT


def test_intent_tolerates_imperative_to_descriptive_rewrite():
    """An imperative intent realized as descriptive prose of the SAME thing keeps a
    high token overlap → NOT flagged, even with the intent signal on."""
    r = Realization(target_feature_id="f-x",
                    intent_text="Add validation for empty input",
                    realized_text="Validates empty input")
    assert classify_realization(r, intent_ratio=0.3) is Divergence.FAITHFUL


def test_scope_wins_over_intent():
    r = Realization(target_feature_id="f-x", touched_feature_ids={"f-y"},
                    intent_text="a", realized_text="z")
    assert classify_realization(r, intent_ratio=0.9) is Divergence.SCOPE


def test_text_overlap_bounds():
    assert text_overlap("", "") == 1.0
    assert text_overlap("abc", "") == 0.0
    assert text_overlap("add validation", "add validation") == 1.0
    assert 0.0 < text_overlap("add validation here", "validation removed") < 1.0


def test_divergent_targets_keys_by_target_for_divergent_only():
    rs = {
        "d-1": Realization(target_feature_id="f-x", touched_feature_ids={"f-y"}),  # scope
        "d-2": Realization(target_feature_id="f-z", touched_feature_ids={"f-z"}),  # faithful
    }
    assert divergent_targets(rs) == {"f-x": "scope"}


# ─── 2. Loop A wiring ────────────────────────────────────────────────────────

@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    return str(d)


@pytest.fixture
def store(codoc_dir):
    s = open_store(codoc_dir)
    yield s
    s.close()


def _added(file, sym):
    return ChangeSet(added=[ChunkRef(file, sym, "h", source="def f(): ...")])


def test_realize_epoch_records_scope_divergence(store, codoc_dir):
    """A directive targets f-x (held). The realization proposes a MOVE on f-y — a
    feature beyond the target → recorded as a scope divergence on f-y, persisted."""
    fx = Feature(title="Auth", description="Login.")
    fy = Feature(title="Data", description="Persistence.")
    store.upsert_feature(fx)
    store.upsert_feature(fy)

    move_fy = NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=fy.id, parent_id=None,
                     rationale="agent moved it while realizing the f-x directive")

    result = apply_changeset(
        _added("n.py", "n.py::new"), store,
        propose=lambda *a, **k: [move_fy],
        held={fx.id},                                   # f-x is the held directive target
        caused_by_map={fx.id: "d-1"}, default_caused_by="d-1",
        codoc_dir=codoc_dir,
    )

    assert result.realize_outcomes == {fy.id: "scope"}
    assert read_resolution(codoc_dir) == {fy.id: "scope"}


def test_faithful_realization_records_nothing(store, codoc_dir):
    """On-target work is suppressed by the doc-wins hold (quiet while in-flight) →
    no proposed op beyond the target → faithful → resolution.json empty."""
    fx = Feature(title="Auth", description="Login.")
    store.upsert_feature(fx)

    # The realization proposes a (large) AMEND on the TARGET itself — suppressed by
    # the hold (row 13), so it never surfaces and is never a divergence.
    amend_fx = NodeOp(kind=NodeOpKind.AMEND, feature_id=fx.id,
                      description="A completely different and much longer description "
                                  "that would not auto-apply as a small amend at all.")

    result = apply_changeset(
        _added("n.py", "n.py::new"), store,
        propose=lambda *a, **k: [amend_fx],
        held={fx.id}, caused_by_map={fx.id: "d-1"}, default_caused_by="d-1",
        codoc_dir=codoc_dir,
    )

    assert result.realize_outcomes == {}
    assert result.held_back == 1            # the on-target amend was suppressed
    assert read_resolution(codoc_dir) == {}


def test_resolution_self_clears_when_no_proposal_pending(store, codoc_dir):
    """A later pass with no realize ops prunes a stale resolution entry once its
    surfaced proposal is gone (accepted/rejected)."""
    fy = Feature(title="Data", description="Persistence.")
    store.upsert_feature(fy)
    write_resolution(codoc_dir, {fy.id: "scope"})  # a prior divergence, no pending proposal now

    apply_changeset(ChangeSet(), store, propose=lambda *a, **k: [], codoc_dir=codoc_dir)

    assert read_resolution(codoc_dir) == {}  # pruned — nothing left to review


# ─── 3. sidecar re-emit + live filter ────────────────────────────────────────

def _sidecar(codoc_dir) -> dict:
    return json.loads((Path(codoc_dir) / BINDINGS_FILENAME).read_text())


def test_write_sidecar_reemits_resolution_for_pending_proposal(store, codoc_dir):
    """feature_resolution mirrors resolution.json — but only while the divergent
    feature still has a pending proposal to review."""
    fy = Feature(title="Data", description="Persistence.")
    store.upsert_feature(fy)
    # A pending proposal on f-y (the surfaced divergent change).
    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=fy.id, parent_id=None),
             store, source="loop_a", applied=False)
    write_resolution(codoc_dir, {fy.id: "scope"})

    write_sidecar(store, codoc_dir)

    assert _sidecar(codoc_dir)["feature_resolution"] == {fy.id: "scope"}


def test_write_sidecar_drops_resolution_when_no_pending_proposal(store, codoc_dir):
    """Once the divergent proposal is resolved (no pending event for the feature),
    the interactive re-emit drops the review flag."""
    fy = Feature(title="Data", description="Persistence.")
    store.upsert_feature(fy)
    write_resolution(codoc_dir, {fy.id: "scope"})  # flag, but no pending proposal

    write_sidecar(store, codoc_dir)

    assert _sidecar(codoc_dir)["feature_resolution"] == {}  # nothing pending → dropped


def test_resolution_round_trip(codoc_dir):
    write_resolution(codoc_dir, {"f-a": "scope", "f-b": "intent"})
    assert read_resolution(codoc_dir) == {"f-a": "scope", "f-b": "intent"}
    assert resolution_path(codoc_dir).exists()


def test_read_resolution_tolerant_of_missing(codoc_dir):
    assert read_resolution(codoc_dir) == {}  # never written → empty, no crash
