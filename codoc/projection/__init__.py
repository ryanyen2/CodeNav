"""codoc.projection — Phase 1.5 .codoc/tree/ projection layer.

Renders SQLite state to multi-file `.codoc` text format and parses edits
back into IntentOps applied via the IntentionalRunner.
"""

from codoc.projection.differ import (
    AcceptOp,
    AmendOp,
    DiffError,
    IntentOp,
    RejectOp,
    RenameOp,
    RestructureOp,
    RetireOp,
    diff_tree,
)
from codoc.projection.meta import TreeMeta, read_meta, write_meta
from codoc.projection.parser import (
    ParsedFeature,
    ParsedProposal,
    ParsedTree,
    parse_tree_dir,
)
from codoc.projection.sync import SyncResult, sync_from_dir
from codoc.projection.tree_codoc import render_tree, write_tree

__all__ = [
    "AcceptOp",
    "AmendOp",
    "DiffError",
    "IntentOp",
    "ParsedFeature",
    "ParsedProposal",
    "ParsedTree",
    "RejectOp",
    "RenameOp",
    "RestructureOp",
    "RetireOp",
    "SyncResult",
    "TreeMeta",
    "diff_tree",
    "parse_tree_dir",
    "read_meta",
    "render_tree",
    "sync_from_dir",
    "write_meta",
    "write_tree",
]
