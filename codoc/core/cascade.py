"""codoc.core.cascade — cascade enumeration for Phase 2 structural transactions.

Cascade scope (per plan Q8):
  - Binding-graph: 1-hop only — neighbouring features whose bindings reference
    (or are referenced by) the bindings of the affected feature.
  - Tree-structural: recurse through the subtree when SPLIT/MERGE/RESTRUCTURE
    affects descendants' parent pointers, inherited constraint context, or prose.

CascadeEnumerator does NOT call the LLM.  It produces ``Obligation`` records
(status="pending") that downstream agents (prose_reconciliation,
binding_reconciliation) consume.
"""

from __future__ import annotations

import hashlib
import json
import uuid as _uuid

from codoc.core.binding_graph import neighbors_1hop
from codoc.model.hlc import HLC
from codoc.model.obligation import Obligation, ObligationKind
from codoc.storage.sqlite_store import SQLiteStore


def _new_uuid() -> str:
    try:
        import uuid_utils  # type: ignore[import]
        return str(uuid_utils.uuid7())
    except ImportError:
        return str(_uuid.uuid4())


def _hash_context(context: dict) -> str:
    canonical = json.dumps(context, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CascadeEnumerator:
    """Enumerate cascade obligations for structural transactions."""

    def __init__(
        self,
        store: SQLiteStore,
        binding_graph: dict[str, set[str]] | None = None,
    ) -> None:
        self._store = store
        self._binding_graph = binding_graph or {}

    # ------------------------------------------------------------------
    # SPLIT
    # ------------------------------------------------------------------

    def enumerate_for_split(
        self,
        feature_uuid: str,
        child_a_uuid: str,
        child_b_uuid: str,
        triggered_by_hlc: HLC,
    ) -> list[Obligation]:
        """A SPLIT replaces one feature with two children.

        Cascade scope:
          - 1-hop binding-graph neighbours need ``RECONCILE_BINDING`` because
            their references previously targeted the original feature; we must
            decide which child each surviving inbound reference should point at.
          - Each child gets ``RECONCILE_PROSE`` so the agent re-evaluates the
            child's intent against the structural change.
        """
        obligations: list[Obligation] = []

        # Children prose reconciliation.
        for child_uuid in (child_a_uuid, child_b_uuid):
            ctx = {
                "trigger": "split",
                "parent_feature_uuid": feature_uuid,
                "child_feature_uuid": child_uuid,
                "sibling_feature_uuid": child_b_uuid if child_uuid == child_a_uuid else child_a_uuid,
            }
            obligations.append(
                Obligation(
                    uuid=_new_uuid(),
                    kind=ObligationKind.RECONCILE_PROSE,
                    feature_uuid=child_uuid,
                    triggered_by_tx_hlc=triggered_by_hlc,
                    context_hash=_hash_context(ctx),
                    expected_output_schema="prose_patch",
                    context=ctx,
                    status="pending",
                )
            )

        # 1-hop binding-graph neighbours.
        for neighbour_uuid in neighbors_1hop(feature_uuid, self._binding_graph):
            if neighbour_uuid in (child_a_uuid, child_b_uuid):
                continue
            ctx = {
                "trigger": "split",
                "split_feature_uuid": feature_uuid,
                "neighbour_feature_uuid": neighbour_uuid,
                "candidate_target_uuids": [child_a_uuid, child_b_uuid],
            }
            obligations.append(
                Obligation(
                    uuid=_new_uuid(),
                    kind=ObligationKind.RECONCILE_BINDING,
                    feature_uuid=neighbour_uuid,
                    triggered_by_tx_hlc=triggered_by_hlc,
                    context_hash=_hash_context(ctx),
                    expected_output_schema="binding_patch",
                    context=ctx,
                    status="pending",
                )
            )

        return obligations

    # ------------------------------------------------------------------
    # MERGE
    # ------------------------------------------------------------------

    def enumerate_for_merge(
        self,
        source_uuids: list[str],
        target_uuid: str,
        triggered_by_hlc: HLC,
    ) -> list[Obligation]:
        """A MERGE folds multiple sources into one target.

        Cascade scope:
          - Target gets ``RECONCILE_PROSE`` so its intent reflects the union.
          - 1-hop binding-graph neighbours of any source get
            ``RECONCILE_BINDING`` since their references must re-target.
        """
        obligations: list[Obligation] = []

        ctx_target = {
            "trigger": "merge",
            "source_feature_uuids": list(source_uuids),
            "target_feature_uuid": target_uuid,
        }
        obligations.append(
            Obligation(
                uuid=_new_uuid(),
                kind=ObligationKind.RECONCILE_PROSE,
                feature_uuid=target_uuid,
                triggered_by_tx_hlc=triggered_by_hlc,
                context_hash=_hash_context(ctx_target),
                expected_output_schema="prose_patch",
                context=ctx_target,
                status="pending",
            )
        )

        seen_neighbours: set[str] = set()
        for source_uuid in source_uuids:
            for neighbour_uuid in neighbors_1hop(source_uuid, self._binding_graph):
                if neighbour_uuid in source_uuids or neighbour_uuid == target_uuid:
                    continue
                if neighbour_uuid in seen_neighbours:
                    continue
                seen_neighbours.add(neighbour_uuid)
                ctx = {
                    "trigger": "merge",
                    "merged_source_uuids": list(source_uuids),
                    "merged_target_uuid": target_uuid,
                    "neighbour_feature_uuid": neighbour_uuid,
                }
                obligations.append(
                    Obligation(
                        uuid=_new_uuid(),
                        kind=ObligationKind.RECONCILE_BINDING,
                        feature_uuid=neighbour_uuid,
                        triggered_by_tx_hlc=triggered_by_hlc,
                        context_hash=_hash_context(ctx),
                        expected_output_schema="binding_patch",
                        context=ctx,
                        status="pending",
                    )
                )

        return obligations

    # ------------------------------------------------------------------
    # RESTRUCTURE
    # ------------------------------------------------------------------

    def enumerate_for_restructure(
        self,
        feature_uuid: str,
        new_parent_uuid: str | None,
        triggered_by_hlc: HLC,
    ) -> list[Obligation]:
        """A RESTRUCTURE moves a feature (and its subtree) to a new parent.

        Cascade scope:
          - Tree-structural recursion: every descendant's prose context shifts;
            emit ``RECONCILE_PROSE`` for the moved feature and each descendant.
          - 1-hop binding-graph neighbours of the moved feature get
            ``RECONCILE_BINDING`` for completeness (parent context can change
            inherited constraints).
        """
        obligations: list[Obligation] = []

        # Walk the subtree rooted at feature_uuid (deterministic recursion).
        descendants = self._collect_descendants(feature_uuid)
        all_affected = [feature_uuid, *descendants]

        for affected_uuid in all_affected:
            ctx = {
                "trigger": "restructure",
                "moved_feature_uuid": feature_uuid,
                "affected_feature_uuid": affected_uuid,
                "new_parent_uuid": new_parent_uuid,
            }
            obligations.append(
                Obligation(
                    uuid=_new_uuid(),
                    kind=ObligationKind.RECONCILE_PROSE,
                    feature_uuid=affected_uuid,
                    triggered_by_tx_hlc=triggered_by_hlc,
                    context_hash=_hash_context(ctx),
                    expected_output_schema="prose_patch",
                    context=ctx,
                    status="pending",
                )
            )

        for neighbour_uuid in neighbors_1hop(feature_uuid, self._binding_graph):
            if neighbour_uuid in all_affected:
                continue
            ctx = {
                "trigger": "restructure",
                "moved_feature_uuid": feature_uuid,
                "neighbour_feature_uuid": neighbour_uuid,
                "new_parent_uuid": new_parent_uuid,
            }
            obligations.append(
                Obligation(
                    uuid=_new_uuid(),
                    kind=ObligationKind.RECONCILE_BINDING,
                    feature_uuid=neighbour_uuid,
                    triggered_by_tx_hlc=triggered_by_hlc,
                    context_hash=_hash_context(ctx),
                    expected_output_schema="binding_patch",
                    context=ctx,
                    status="pending",
                )
            )

        return obligations

    # ------------------------------------------------------------------
    # REWIND
    # ------------------------------------------------------------------

    def enumerate_for_rewind(
        self,
        feature_uuid: str,
        target_hlc: HLC,
        triggered_by_hlc: HLC,
    ) -> list[Obligation]:
        """A REWIND restores a feature's intent/slug to a prior state.

        Cascade scope is intentionally narrow: only the rewound feature and
        its 1-hop binding-graph neighbours need ``RECONCILE_BINDING`` since
        their inbound references may now point at stale prose.
        """
        obligations: list[Obligation] = []

        ctx_self = {
            "trigger": "rewind",
            "feature_uuid": feature_uuid,
            "target_hlc": target_hlc.to_str(),
        }
        obligations.append(
            Obligation(
                uuid=_new_uuid(),
                kind=ObligationKind.RECONCILE_PROSE,
                feature_uuid=feature_uuid,
                triggered_by_tx_hlc=triggered_by_hlc,
                context_hash=_hash_context(ctx_self),
                expected_output_schema="prose_patch",
                context=ctx_self,
                status="pending",
            )
        )

        for neighbour_uuid in neighbors_1hop(feature_uuid, self._binding_graph):
            ctx = {
                "trigger": "rewind",
                "rewound_feature_uuid": feature_uuid,
                "neighbour_feature_uuid": neighbour_uuid,
                "target_hlc": target_hlc.to_str(),
            }
            obligations.append(
                Obligation(
                    uuid=_new_uuid(),
                    kind=ObligationKind.RECONCILE_BINDING,
                    feature_uuid=neighbour_uuid,
                    triggered_by_tx_hlc=triggered_by_hlc,
                    context_hash=_hash_context(ctx),
                    expected_output_schema="binding_patch",
                    context=ctx,
                    status="pending",
                )
            )

        return obligations

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _collect_descendants(self, feature_uuid: str) -> list[str]:
        """Return all descendant uuids of *feature_uuid* in deterministic order."""
        out: list[str] = []
        frontier: list[str] = [feature_uuid]
        seen: set[str] = {feature_uuid}
        while frontier:
            current = frontier.pop(0)
            children = self._store.list_features(parent_uuid=current)
            for child in children:
                if child.uuid in seen:
                    continue
                seen.add(child.uuid)
                out.append(child.uuid)
                frontier.append(child.uuid)
        return out
