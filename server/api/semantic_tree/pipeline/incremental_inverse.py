"""
Incremental inverse sync: tree edit → operations → LLM code generation → apply to files.

Orchestrates: get operations (from caller), dispatch each to CodeChange[], apply via
code_applicator, post_check, then update SyncState. Supports dry_run (plan only).
After apply we re-fingerprint modified files so the next forward sync sees no spurious delta.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from api.semantic_tree.models import CodebaseSnapshot
from api.semantic_tree.state.models import SyncState, EntityFingerprint
from api.semantic_tree.state.persistence import save_sync_state
from api.semantic_tree.state.fingerprint import (
    compute_entity_fingerprint,
    compute_file_fingerprint,
    get_entity_content_sample,
)
from api.semantic_tree.extraction.python_extractor import extract_python_file
from api.semantic_tree.pipeline.code_dispatch import dispatch_operation_to_changes
from api.semantic_tree.pipeline.code_applicator import CodeChange, apply_changes
from api.semantic_tree.pipeline.post_check import post_check, DriftReportItem

logger = logging.getLogger(__name__)


def _entity_key(fpath: str, name: str) -> str:
    return f"{fpath}::{name}"


def _merge_fingerprints_after_apply(
    root_dir: str,
    modified_fpaths: List[str],
    old_entity_fps: Dict[str, EntityFingerprint],
    old_file_fps: Dict[str, str],
) -> Tuple[Dict[str, EntityFingerprint], Dict[str, str]]:
    """
    Re-fingerprint modified files and merge into state so the next forward sync
    does not treat applied entities as modified (incremental consistency).
    Returns (merged_entity_fingerprints, merged_file_fingerprints).
    """
    root = Path(root_dir).resolve()
    entity_out = {k: v for k, v in old_entity_fps.items()
                  if not any(k.startswith(f"{fp}::") for fp in modified_fpaths)}
    file_out = dict(old_file_fps)
    for fpath in modified_fpaths:
        path = root / fpath
        if not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("Re-fingerprint: could not read %s: %s", fpath, e)
            continue
        file_out[fpath] = compute_file_fingerprint(source)
        try:
            file_info = extract_python_file(fpath, source, str(root), include_imports=False)
        except Exception as e:
            logger.warning("Re-fingerprint: extract failed for %s: %s", fpath, e)
            continue
        for e in file_info.entities:
            ch, sh = compute_entity_fingerprint(e)
            sample = get_entity_content_sample(e)
            entity_out[_entity_key(e.fpath, e.name)] = EntityFingerprint(
                content_hash=ch, signature_hash=sh, content_sample=sample or None
            )
    return entity_out, file_out


def incremental_inverse(
    operations: List[Dict[str, Any]],
    snapshot: CodebaseSnapshot,
    state: SyncState,
    provider: str = "openai",
    model: Optional[str] = None,
    dry_run: bool = False,
) -> Tuple[
    List[str],
    List[CodeChange],
    List[Dict[str, Any]],
    Optional[SyncState],
]:
    """
    Run inverse sync: tree edit operations → code changes → apply (unless dry_run).

    Args:
        operations: List of op dicts from tree_edit (op, target, params, targets).
        snapshot: Current codebase snapshot (for dispatch context and file reads).
        state: Current sync state (base tree already in state.last_tree_md).
        provider, model: LLM for AddNode / EditFeature.
        dry_run: If True, do not write files and do not update state; return planned changes.

    Returns:
        (modified_fpaths, all_changes_planned_or_applied, drift_report_dicts, new_state or None).
        If dry_run, modified_fpaths is [], new_state is None.
    """
    root_dir = state.root_dir or snapshot.root_dir
    all_changes: List[CodeChange] = []
    for op in operations:
        changes = dispatch_operation_to_changes(
            op, snapshot, root_dir, provider=provider, model=model
        )
        all_changes.extend(changes)

    if dry_run:
        return [], all_changes, [], None

    if not all_changes:
        # No code changes; still update state to persist edited tree (caller passes edited_md)
        return [], [], [], None

    modified = apply_changes(root_dir, all_changes)
    drift_items: List[DriftReportItem] = post_check(root_dir, modified)
    drift_report = [
        {"fpath": item.fpath, "entities": [e.model_dump() for e in item.entities]}
        for item in drift_items
    ]

    # Caller will pass edited_tree_md and update state; we don't have it here.
    # So we return new_state as None and let the route update state after we return.
    return modified, all_changes, drift_report, None


def apply_inverse_and_update_state(
    operations: List[Dict[str, Any]],
    snapshot: CodebaseSnapshot,
    state: SyncState,
    edited_tree_md: str,
    provider: str = "openai",
    model: Optional[str] = None,
    dry_run: bool = False,
) -> Tuple[
    List[str],
    List[CodeChange],
    List[Dict[str, Any]],
    Optional[SyncState],
]:
    """
    Full inverse: run incremental_inverse then update state with edited tree.
    If dry_run, state is not updated. Returns same 4-tuple as incremental_inverse.
    """
    modified, changes, drift_report, _ = incremental_inverse(
        operations, snapshot, state, provider=provider, model=model, dry_run=dry_run
    )
    if dry_run or not modified:
        # If no file changes, we still want to persist the edited tree when not dry_run
        new_state = None
        if not dry_run and edited_tree_md and state.root_dir:
            new_state = state.model_copy(update={
                "last_tree_md": edited_tree_md,
                "last_sync_direction": "inverse",
                "tree_version": state.tree_version + 1,
            })
            save_sync_state(new_state, state.root_dir)
        return modified, changes, drift_report, new_state

    # Re-fingerprint modified files so next forward sync sees no spurious delta
    root_dir = state.root_dir or snapshot.root_dir
    merged_entity_fps, merged_file_fps = _merge_fingerprints_after_apply(
        root_dir,
        modified,
        state.entity_fingerprints,
        state.file_fingerprints,
    )
    new_state = state.model_copy(update={
        "entity_fingerprints": merged_entity_fps,
        "file_fingerprints": merged_file_fps,
        "last_tree_md": edited_tree_md,
        "last_sync_direction": "inverse",
        "tree_version": state.tree_version + 1,
    })
    save_sync_state(new_state, state.root_dir)
    return modified, changes, drift_report, new_state
