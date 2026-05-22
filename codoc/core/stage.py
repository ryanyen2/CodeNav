"""Repo-stage detection: what state is a codoc-managed repo in right now?

``repo_stage`` is the single source of truth for the sync dispatcher, the CLI,
and the FastAPI ``GET /state`` endpoint.  It reads only filesystem and SQLite
signals — no LLM calls, no network.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path


class Stage(str, Enum):
    UNINIT = "uninit"               # .codoc/ doesn't exist
    NEEDS_BOOTSTRAP = "needs-bootstrap"  # .codoc/ exists but no features yet
    BOOTSTRAP_REVIEW = "bootstrap-review"  # bootstrap proposals pending, not finished
    PROPOSALS_PENDING = "proposals-pending"  # reflective/planning proposals awaiting review
    STALE_RENDER = "stale-render"   # DB moved past last tree render
    CLEAN = "clean"                 # everything in sync


class RepoState:
    """Snapshot of the repo's current state signals."""

    def __init__(
        self,
        stage: Stage,
        pending_count: int = 0,
        feature_count: int = 0,
        head_hlc: str = "",
        base_hlc: str = "",
        hooks_installed: bool = False,
        has_index: bool = False,
    ) -> None:
        self.stage = stage
        self.pending_count = pending_count
        self.feature_count = feature_count
        self.head_hlc = head_hlc
        self.base_hlc = base_hlc
        self.hooks_installed = hooks_installed
        self.has_index = has_index

    @property
    def next_action(self) -> str:
        match self.stage:
            case Stage.UNINIT:
                return "Run `codoc sync` to initialize and bootstrap"
            case Stage.NEEDS_BOOTSTRAP:
                return "Run `codoc sync` to bootstrap the feature tree"
            case Stage.BOOTSTRAP_REVIEW:
                return f"Review {self.pending_count} bootstrap proposals with `codoc proposals`, then `codoc accept`"
            case Stage.PROPOSALS_PENDING:
                return f"Review {self.pending_count} proposal(s) with `codoc proposals` or `codoc sync --yes`"
            case Stage.STALE_RENDER:
                return "Run `codoc sync` to re-render the feature tree"
            case Stage.CLEAN:
                return "Nothing to do — repo is in sync"
            case _:
                return ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage.value,
            "pending_count": self.pending_count,
            "feature_count": self.feature_count,
            "head_hlc": self.head_hlc,
            "base_hlc": self.base_hlc,
            "hooks_installed": self.hooks_installed,
            "has_index": self.has_index,
            "next_action": self.next_action,
        }


def repo_stage(root_dir: str | Path) -> RepoState:
    """Compute the current stage of a codoc-managed repo.

    Decision tree (checked in priority order):
    1. .codoc/ missing                      → UNINIT
    2. codoc.db missing or zero features    → NEEDS_BOOTSTRAP
    3. pending proposals + no unattributed.json → BOOTSTRAP_REVIEW
    4. pending proposals                    → PROPOSALS_PENDING
    5. tree render behind DB head           → STALE_RENDER
    6. everything in sync                   → CLEAN

    Hooks-installed is always computed and returned alongside; it is orthogonal
    to the stage (a CLEAN repo might still lack hooks if someone deleted them).
    """
    root = Path(root_dir).resolve()
    codoc_dir = root / ".codoc"

    hooks_installed = (root / ".git" / "hooks" / "post-commit").exists()

    if not codoc_dir.is_dir():
        return RepoState(Stage.UNINIT, hooks_installed=hooks_installed)

    db_path = codoc_dir / "codoc.db"
    if not db_path.exists():
        return RepoState(Stage.NEEDS_BOOTSTRAP, hooks_installed=hooks_installed)

    from codoc.storage.sqlite_store import SQLiteStore
    from codoc.projection.meta import read_meta

    store = SQLiteStore(str(db_path))
    try:
        store.open()

        features = store.list_features()
        feature_count = len(features)

        if feature_count == 0:
            return RepoState(
                Stage.NEEDS_BOOTSTRAP,
                feature_count=0,
                hooks_installed=hooks_installed,
            )

        pending = store.list_transactions(proposal=True, limit=0)
        pending_count = len(pending)

        all_accepted = store.list_transactions(proposal=False, limit=0)
        head_hlc = max((t.hlc for t in all_accepted), default=None)
        head_str = head_hlc.to_str() if head_hlc else ""

        bootstrap_done = (codoc_dir / "unattributed.json").exists()

        if pending_count > 0 and not bootstrap_done:
            return RepoState(
                Stage.BOOTSTRAP_REVIEW,
                pending_count=pending_count,
                feature_count=feature_count,
                head_hlc=head_str,
                hooks_installed=hooks_installed,
            )

        if pending_count > 0:
            return RepoState(
                Stage.PROPOSALS_PENDING,
                pending_count=pending_count,
                feature_count=feature_count,
                head_hlc=head_str,
                hooks_installed=hooks_installed,
            )

        meta = read_meta(str(codoc_dir))
        base_str = meta.base_hlc if meta else ""
        has_index = (codoc_dir / "tree" / "_index.codoc").exists()

        if not meta or (head_str and base_str and base_str < head_str):
            return RepoState(
                Stage.STALE_RENDER,
                feature_count=feature_count,
                head_hlc=head_str,
                base_hlc=base_str,
                hooks_installed=hooks_installed,
                has_index=has_index,
            )

        return RepoState(
            Stage.CLEAN,
            feature_count=feature_count,
            head_hlc=head_str,
            base_hlc=base_str,
            hooks_installed=hooks_installed,
            has_index=has_index,
        )
    finally:
        store.close()
