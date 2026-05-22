"""Live feature view: bundles a Feature with its current health data.

``resolve_feature(store, feature)`` is the single helper every caller should use
to compute ``FeatureState``.  It reads real ``binding_resolutions`` rows from the
store rather than passing ``resolutions=[]``, so the state machine is no longer
inert in the CLI, projection renderer, and API severed-features endpoint.

Usage
-----
    from codoc.core.feature_view import resolve_feature

    view = resolve_feature(store, feature)
    view.state          # FeatureState
    view.bindings       # list[Binding]
    view.obligations    # list[Obligation]
    view.resolutions    # list[BindingResolution] (real data, not empty)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codoc.model.binding import Binding
    from codoc.model.feature import Feature
    from codoc.model.obligation import Obligation
    from codoc.model.state import FeatureState
    from codoc.core.state_derivation import BindingResolution
    from codoc.storage.sqlite_store import SQLiteStore


@dataclass
class FeatureView:
    """Bundled feature record with live health data and computed state."""

    feature: "Feature"
    bindings: "list[Binding]" = field(default_factory=list)
    resolutions: "list[BindingResolution]" = field(default_factory=list)
    obligations: "list[Obligation]" = field(default_factory=list)
    state: "FeatureState | None" = None


def resolve_feature(store: "SQLiteStore", feature: "Feature") -> FeatureView:
    """Bundle a Feature with live resolutions and compute its FeatureState.

    Reads real ``binding_resolutions`` rows from *store* so the state machine
    reflects actual health data rather than assuming all bindings are aligned.

    Parameters
    ----------
    store:
        An open ``SQLiteStore`` instance.
    feature:
        The ``Feature`` record to resolve.

    Returns
    -------
    FeatureView
        Contains ``feature``, ``bindings``, ``resolutions``, ``obligations``,
        and the derived ``state``.
    """
    from codoc.core.state_derivation import compute_feature_state, BindingResolution

    bindings = store.list_bindings(feature.uuid)
    obligations = store.list_obligations(feature_uuid=feature.uuid, status="pending")

    # Read real resolution rows from the store.
    raw_resolutions = store.get_latest_resolutions_for_feature(feature.uuid)
    resolutions: list[BindingResolution] = [
        BindingResolution(
            binding_uuid=r["binding_uuid"],
            resolved=bool(r["resolved"]),
            fingerprint_matches=bool(r["fingerprint_matches"]),
        )
        for r in raw_resolutions
    ]

    state = compute_feature_state(feature, bindings, resolutions, obligations)

    return FeatureView(
        feature=feature,
        bindings=bindings,
        resolutions=resolutions,
        obligations=obligations,
        state=state,
    )
