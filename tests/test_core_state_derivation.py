"""Tests for FeatureState derivation: STUB, DRAFTING, STABLE, STRAINED, SEVERED, DEPRECATED."""

from __future__ import annotations

import time

from codoc.core.state_derivation import BindingResolution, compute_feature_state
from codoc.model.hlc import HLC
from codoc.model.obligation import Obligation, ObligationKind
from codoc.model.state import FeatureState


def _resolution(uuid: str, resolved: bool, fingerprint_matches: bool = True) -> BindingResolution:
    return BindingResolution(binding_uuid=uuid, resolved=resolved, fingerprint_matches=fingerprint_matches)


def _obligation(feature_uuid: str, hlc: HLC, status: str = "pending") -> Obligation:
    return Obligation(
        uuid="obligation-1",
        kind=ObligationKind.RECONCILE_PROSE,
        feature_uuid=feature_uuid,
        triggered_by_tx_hlc=hlc,
        context_hash="0" * 64,
        expected_output_schema="prose_patch",
        context={},
        status=status,
    )


def test_stub_state_when_no_bindings(make_feature) -> None:
    feature = make_feature(intent="Has prose but no bindings")
    state = compute_feature_state(feature, bindings=[], resolutions=[], open_obligations=[])
    assert state == FeatureState.STUB


def test_deprecated_state_when_retired(make_feature, make_binding) -> None:
    feature = make_feature(retired=True)
    binding = make_binding(feature.uuid)
    res = _resolution(binding.uuid, resolved=True, fingerprint_matches=True)
    state = compute_feature_state(feature, [binding], [res], [])
    assert state == FeatureState.DEPRECATED


def test_severed_state_when_all_bindings_unresolved(make_feature, make_binding) -> None:
    feature = make_feature()
    binding_a = make_binding(feature.uuid)
    binding_b = make_binding(feature.uuid)
    res_a = _resolution(binding_a.uuid, resolved=False)
    res_b = _resolution(binding_b.uuid, resolved=False)
    state = compute_feature_state(feature, [binding_a, binding_b], [res_a, res_b], [])
    assert state == FeatureState.SEVERED


def test_strained_state_on_fingerprint_divergence(make_feature, make_binding) -> None:
    # Force "old" feature so DRAFTING window doesn't trigger.
    old_hlc = HLC(logical_time=0, wall_clock=1, node_id="old")
    feature = make_feature(hlc=old_hlc)
    binding = make_binding(feature.uuid)
    res = _resolution(binding.uuid, resolved=True, fingerprint_matches=False)
    state = compute_feature_state(feature, [binding], [res], [])
    assert state == FeatureState.STRAINED


def test_strained_state_on_open_obligation(make_feature, make_binding) -> None:
    old_hlc = HLC(logical_time=0, wall_clock=1, node_id="old")
    feature = make_feature(hlc=old_hlc)
    binding = make_binding(feature.uuid)
    res = _resolution(binding.uuid, resolved=True, fingerprint_matches=True)
    obligation = _obligation(feature.uuid, old_hlc)
    state = compute_feature_state(feature, [binding], [res], [obligation])
    assert state == FeatureState.STRAINED


def test_drafting_state_on_recent_creation(make_feature, make_binding) -> None:
    # Default HLC.now → recently created → DRAFTING.
    feature = make_feature()
    binding = make_binding(feature.uuid)
    res = _resolution(binding.uuid, resolved=True, fingerprint_matches=True)
    state = compute_feature_state(feature, [binding], [res], [])
    assert state == FeatureState.DRAFTING


def test_drafting_state_when_intent_empty(make_feature, make_binding) -> None:
    old_hlc = HLC(logical_time=0, wall_clock=1, node_id="old")
    feature = make_feature(intent="", hlc=old_hlc)
    binding = make_binding(feature.uuid)
    res = _resolution(binding.uuid, resolved=True, fingerprint_matches=True)
    state = compute_feature_state(feature, [binding], [res], [])
    assert state == FeatureState.DRAFTING


def test_stable_state_when_all_clear(make_feature, make_binding) -> None:
    old_hlc = HLC(logical_time=0, wall_clock=1, node_id="old")
    feature = make_feature(intent="A meaningful description.", hlc=old_hlc)
    binding = make_binding(feature.uuid)
    res = _resolution(binding.uuid, resolved=True, fingerprint_matches=True)
    state = compute_feature_state(feature, [binding], [res], [])
    assert state == FeatureState.STABLE


def test_priority_severed_over_strained(make_feature, make_binding) -> None:
    # All bindings unresolved AND fingerprint divergence (not relevant when unresolved)
    # AND open obligations → SEVERED still wins because resolved_count == 0.
    old_hlc = HLC(logical_time=0, wall_clock=1, node_id="old")
    feature = make_feature(hlc=old_hlc)
    binding = make_binding(feature.uuid)
    res = _resolution(binding.uuid, resolved=False, fingerprint_matches=False)
    obligation = _obligation(feature.uuid, old_hlc)
    state = compute_feature_state(feature, [binding], [res], [obligation])
    assert state == FeatureState.SEVERED


def test_priority_deprecated_over_severed(make_feature, make_binding) -> None:
    feature = make_feature(retired=True)
    binding = make_binding(feature.uuid)
    res = _resolution(binding.uuid, resolved=False)
    state = compute_feature_state(feature, [binding], [res], [])
    assert state == FeatureState.DEPRECATED
