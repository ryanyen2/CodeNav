"""The single mid-flight phase projection (Proposal B + D5).

``compute_phases`` is the one place "where is this feature mid-flight?" is
decided. These tests pin the priority order (doc-wins: a held feature is never
also badged drifted/divergent), the thin-view fields (holds / hold_detail /
feature_drift / feature_resolution reproduced exactly), and the ``is_held``
predicate the loops share.
"""
from __future__ import annotations

from codoc.loop.edits import DRIFT_BINDING_LOST, DRIFT_QUESTIONED, Directive
from codoc.loop.phase import (
    Phase,
    PhaseInputs,
    compute_phases,
    is_held,
    project_from_store,
)
from codoc.model.feature import Feature
from codoc.store.db import open_store


def _inp(features, **kw) -> PhaseInputs:
    base = dict(
        features=features,
        bound_ids=set(),
        pending_feature_ids=set(),
        held=set(),
        directives=[],
        drift={},
        resolution={},
    )
    base.update(kw)
    return PhaseInputs(**base)


# ─── is_held — the single doc-wins predicate (D5) ────────────────────────────

def test_is_held_predicate():
    assert is_held("f-1", {"f-1"}) is True
    assert is_held("f-2", {"f-1"}) is False
    assert is_held("", {"f-1"}) is False
    assert is_held(None, {"f-1"}) is False


# ─── primary phase priority ──────────────────────────────────────────────────

def test_synced_is_absent_from_the_slice():
    f = Feature(title="A", description="Does a thing.")
    proj = compute_phases(_inp([f], bound_ids={f.id}))
    assert proj.phase == {}  # synced → no dot


def test_retired_wins_over_everything():
    f = Feature(title="A", retired=True)
    proj = compute_phases(_inp(
        [f], held={f.id}, drift={f.id: DRIFT_QUESTIONED},
        resolution={f.id: "scope"}))
    assert proj.phase[f.id] == Phase.RETIRED.value


def test_planned_when_unrealized_and_unbound():
    f = Feature(title="Plan", realized=False)
    proj = compute_phases(_inp([f]))
    assert proj.phase[f.id] == Phase.PLANNED.value


def test_drifted_when_questioned_and_not_held():
    f = Feature(title="A", description="x")
    proj = compute_phases(_inp([f], bound_ids={f.id}, drift={f.id: DRIFT_QUESTIONED}))
    assert proj.phase[f.id] == Phase.DRIFTED.value


def test_held_wins_over_drift_doc_wins():
    """A held feature with a drift signal is DRAFTING/QUEUED, never DRIFTED — the
    doc-wins rule, applied in ONE place (the priority order)."""
    f = Feature(title="A", description="x")
    proj = compute_phases(_inp(
        [f], bound_ids={f.id}, held={f.id}, drift={f.id: DRIFT_QUESTIONED}))
    assert proj.phase[f.id] == Phase.QUEUED.value
    # The drift *slice* preserves the former _live_drift exactly (it does not
    # exclude held — the loop never writes a held drift entry); only the primary
    # `phase` applies doc-wins. So the dot is QUEUED while the raw slice is intact.
    assert proj.drift == {f.id: DRIFT_QUESTIONED}


def test_held_wins_over_divergence():
    f = Feature(title="A", description="x")
    proj = compute_phases(_inp(
        [f], bound_ids={f.id}, held={f.id}, pending_feature_ids={f.id},
        resolution={f.id: "scope"}))
    assert proj.phase[f.id] == Phase.QUEUED.value


def test_divergent_outranks_drift():
    f = Feature(title="A", description="x")
    proj = compute_phases(_inp(
        [f], bound_ids={f.id}, pending_feature_ids={f.id},
        drift={f.id: DRIFT_QUESTIONED}, resolution={f.id: "scope"}))
    assert proj.phase[f.id] == Phase.DIVERGENT.value


def test_drafting_vs_queued_by_handoff():
    f = Feature(title="A", description="x")
    draft = Directive(id="d-1", feature_id=f.id, kind="amend", handed_off=False)
    proj = compute_phases(_inp([f], bound_ids={f.id}, held={f.id}, directives=[draft]))
    assert proj.phase[f.id] == Phase.DRAFTING.value

    handed = Directive(id="d-2", feature_id=f.id, kind="amend", handed_off=True)
    proj2 = compute_phases(_inp([f], bound_ids={f.id}, held={f.id}, directives=[handed]))
    assert proj2.phase[f.id] == Phase.QUEUED.value


def test_held_by_live_intent_only_is_queued():
    """A hold with no directive (a live suggestion) reads QUEUED — pending the
    loop's apply."""
    f = Feature(title="A", description="x")
    proj = compute_phases(_inp([f], bound_ids={f.id}, held={f.id}))
    assert proj.phase[f.id] == Phase.QUEUED.value


# ─── thin-view fields reproduce the former _live_* filters exactly ───────────

def test_drift_view_drops_binding_lost_for_rebound():
    f = Feature(title="A", description="x")
    proj = compute_phases(_inp(
        [f], bound_ids={f.id}, drift={f.id: DRIFT_BINDING_LOST}))
    assert f.id not in proj.drift  # re-bound → contradictory badge dropped


def test_drift_view_keeps_questioned_for_live_bound():
    f = Feature(title="A", description="x")
    proj = compute_phases(_inp([f], bound_ids={f.id}, drift={f.id: DRIFT_QUESTIONED}))
    assert proj.drift == {f.id: DRIFT_QUESTIONED}


def test_drift_view_drops_retired():
    f = Feature(title="A", retired=True)
    proj = compute_phases(_inp([f], drift={f.id: DRIFT_QUESTIONED}))
    assert proj.drift == {}


def test_resolution_view_requires_pending():
    f = Feature(title="A", description="x")
    # No pending proposal → flag dropped.
    assert compute_phases(_inp([f], resolution={f.id: "scope"})).resolution == {}
    # Pending → kept.
    assert compute_phases(_inp(
        [f], pending_feature_ids={f.id}, resolution={f.id: "scope"})
    ).resolution == {f.id: "scope"}


def test_hold_detail_first_directive_per_feature_wins():
    f = Feature(title="A", description="x")
    d1 = Directive(id="d-1", feature_id=f.id, kind="amend", baseline="Old.")
    d2 = Directive(id="d-2", feature_id=f.id, kind="retire")
    proj = compute_phases(_inp([f], held={f.id}, directives=[d1, d2]))
    assert proj.hold_detail[f.id]["kind"] == "amend"
    assert proj.hold_detail[f.id]["baseline"] == "Old."
    assert proj.hold_detail[f.id]["intent"]  # non-empty gloss


def test_holds_is_sorted_hold_set():
    fa = Feature(title="A")
    fb = Feature(title="B")
    proj = compute_phases(_inp([fa, fb], held={fb.id, fa.id}))
    assert proj.holds == sorted({fa.id, fb.id})


# ─── project_from_store wiring ───────────────────────────────────────────────

def test_project_from_store_reads_store_and_controls(tmp_path):
    codoc_dir = str(tmp_path / ".codoc")
    (tmp_path / ".codoc").mkdir()
    with open_store(codoc_dir) as store:
        f = Feature(title="Plan", realized=False)
        store.upsert_feature(f)
        proj = project_from_store(store, codoc_dir)
    assert proj.phase[f.id] == Phase.PLANNED.value
