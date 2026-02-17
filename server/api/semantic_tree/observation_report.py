"""
Semantic tree observation report: code-gen overflow, conflict/merge detection.

Builds a report from apply response (drift_report, operations) and optional
sync response (merge_summary). Logs observations for tests and debugging.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _expected_entity_names_from_operations(operations: List[Any], fpath: str) -> set:
    """Entity names in the tree edit that target this file (from operation targets)."""
    names = set()
    for op in operations:
        op_dict = op.model_dump() if hasattr(op, "model_dump") else op
        for t in op_dict.get("targets") or []:
            if (t.get("fpath") or "") == fpath and t.get("entity_name"):
                names.add(t["entity_name"])
    return names


def _actual_entity_names_from_drift(drift_report: List[dict], fpath: str) -> set:
    """Entity names re-extracted from code after apply (for this file)."""
    for item in drift_report:
        if (item.get("fpath") or "") == fpath:
            return {e.get("name") for e in (item.get("entities") or []) if e.get("name")}
    return set()


def build_apply_observations(
    modified_fpaths: List[str],
    drift_report: List[dict],
    operations: List[Any],
    completion_mode: Optional[str],
    generated_artifact_count: Optional[int],
) -> Dict[str, Any]:
    """
    Compare drift_report (actual code entities) vs operations (expected targets).
    Returns observations: over_generation, mergeable, by_file.
    """
    observations: Dict[str, Any] = {
        "over_generation": [],
        "by_file": {},
        "completion_mode": completion_mode,
        "generated_artifact_count": generated_artifact_count,
    }
    for fpath in modified_fpaths:
        expected_names = _expected_entity_names_from_operations(operations, fpath)
        actual_names = _actual_entity_names_from_drift(drift_report, fpath)
        extra = actual_names - expected_names
        missing = expected_names - actual_names
        observations["by_file"][fpath] = {
            "expected_entities": sorted(expected_names),
            "actual_entities": sorted(actual_names),
            "extra_in_code": sorted(extra),
            "missing_from_code": sorted(missing),
        }
        if extra:
            observations["over_generation"].append(
                {"fpath": fpath, "extra_entities": sorted(extra)}
            )
    return observations


def log_observations(
    step: str,
    apply_obs: Optional[Dict[str, Any]] = None,
    merge_obs: Optional[Dict[str, Any]] = None,
) -> None:
    """Log one-line observation summary for a step."""
    if apply_obs and apply_obs.get("over_generation"):
        logger.info(
            "[CODENAV] OBSERVATION | step=%s | over_generation=%s",
            step,
            apply_obs["over_generation"],
        )
    if merge_obs and merge_obs.get("surfaced_added"):
        logger.info(
            "[CODENAV] OBSERVATION | step=%s | merge surfaced_added=%s overwritten_grounded=%s",
            step,
            merge_obs.get("surfaced_added"),
            merge_obs.get("overwritten_grounded_nodes"),
        )


