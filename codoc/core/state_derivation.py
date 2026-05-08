"""Pure function that computes FeatureState from a Feature record, its Bindings,
resolution outcomes, and open Obligations.

Priority (highest → lowest):
  Severed > Deprecated > Strained > Drafting > Stable > Stub
"""

import time
from dataclasses import dataclass

from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.model.obligation import Obligation
from codoc.model.state import FeatureState

# "Recently created" threshold: features created within this many milliseconds
# of the derivation call are considered still in the Drafting window.
_DRAFTING_RECENTLY_CREATED_MS: int = 60 * 60 * 1000  # 1 hour in milliseconds


@dataclass
class BindingResolution:
    binding_uuid: str
    resolved: bool          # anchor resolved to a byte range
    fingerprint_matches: bool  # current fingerprint == stored fingerprint


def compute_feature_state(
    feature: Feature,
    bindings: list[Binding],
    resolutions: list[BindingResolution],
    open_obligations: list[Obligation],
) -> FeatureState:
    """Derive the current FeatureState for a feature.

    Parameters
    ----------
    feature:
        The Feature record.
    bindings:
        All Binding records associated with this feature.
    resolutions:
        Resolution outcomes, one per binding (keyed by binding_uuid).
    open_obligations:
        Obligations whose status is "pending" or "in_flight" for this feature.
    """
    resolution_map: dict[str, BindingResolution] = {
        r.binding_uuid: r for r in resolutions
    }

    # --- DEPRECATED: retired flag supersedes almost everything ---
    if feature.retired:
        return FeatureState.DEPRECATED

    # --- STUB: zero bindings (regardless of intent prose) ---
    if not bindings:
        return FeatureState.STUB

    # --- SEVERED: ALL bindings fail to resolve ---
    all_resolved = [
        resolution_map[b.uuid].resolved
        for b in bindings
        if b.uuid in resolution_map
    ]
    # If resolution data is missing for some bindings treat them as unresolved.
    resolved_count = sum(1 for r in all_resolved if r)
    if resolved_count == 0:
        return FeatureState.SEVERED

    # --- STRAINED: ≥1 fingerprint diverged OR ≥1 open obligation ---
    has_fingerprint_divergence = any(
        not resolution_map[b.uuid].fingerprint_matches
        for b in bindings
        if b.uuid in resolution_map and resolution_map[b.uuid].resolved
    )
    has_open_obligation = bool(open_obligations)
    if has_fingerprint_divergence or has_open_obligation:
        return FeatureState.STRAINED

    # --- DRAFTING: any binding unresolved OR intent empty OR recently created ---
    any_unresolved = resolved_count < len(bindings)
    intent_empty = not feature.intent.strip()
    now_ms = int(time.time() * 1000)
    recently_created = (
        now_ms - feature.created_at_hlc.wall_clock
    ) < _DRAFTING_RECENTLY_CREATED_MS
    if any_unresolved or intent_empty or recently_created:
        return FeatureState.DRAFTING

    # --- STABLE: all clear ---
    return FeatureState.STABLE
