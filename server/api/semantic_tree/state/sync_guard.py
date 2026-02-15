"""
Direction-locked guard to prevent infinite sync loops.

After inverse sync (tree → code), we only allow forward sync (code → tree) when the
codebase has actually changed (user edited code). Otherwise forward would overwrite
the user's tree with a re-encoding of the same code and create a loop.
"""

from typing import Optional, Tuple

from api.semantic_tree.state.models import SyncState
from api.semantic_tree.state.delta import EntityDelta


def can_run_forward(state: Optional[SyncState], delta: Optional[EntityDelta]) -> Tuple[bool, str]:
    """
    Return (allowed, reason). Forward sync is allowed unless:
    - Last direction was "inverse" AND there is no code change (delta empty).
    In that case we block to avoid overwriting the user's tree with the same code.
    """
    if state is None:
        return True, ""
    if state.last_sync_direction != "inverse":
        return True, ""
    # Last sync was inverse. Allow forward only if code actually changed.
    if delta is None:
        return False, (
            "Last sync was inverse (tree→code). No delta provided; run with force_full=true to re-run forward."
        )
    has_code_change = (
        bool(delta.added) or bool(delta.removed) or bool(delta.modified) or bool(delta.renamed)
    )
    if not has_code_change:
        return False, (
            "Code unchanged after last inverse sync. Edit the codebase and sync again, or use force_full=true."
        )
    return True, ""


def can_run_inverse(state: Optional[SyncState]) -> Tuple[bool, str]:
    """
    Return (allowed, reason). Inverse (apply tree edit) is allowed when we have
    a tree to diff against (state with last_tree_md). No direction lock for inverse:
    user can apply multiple tree edits in a row.
    """
    if state is None:
        return False, "No sync state; run /sync first to produce a tree."
    if not state.last_tree_md or not state.root_dir:
        return False, "No tree in state; run /sync first."
    return True, ""
