"""State-aware sync dispatcher — shared by CLI and FastAPI.

``dispatch`` reads the current ``Stage`` and performs the minimum set of
operations needed to advance the repo toward ``CLEAN``.  The function is
deterministic and idempotent: running it multiple times on a CLEAN repo is
a no-op.

Each pipeline runner (bootstrap, reflect) is imported lazily so importing
this module itself never triggers LLM-related imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from codoc.core.stage import Stage, repo_stage, RepoState


@dataclass
class SyncSummary:
    stage_before: str = ""
    stage_after: str = ""
    actions: list[str] = field(default_factory=list)
    pending_count: int = 0
    feature_count: int = 0

    @property
    def summary(self) -> str:
        if not self.actions:
            return f"codoc: {self.stage_after} — nothing to do"
        return "; ".join(self.actions)


def dispatch(
    root_dir: str | Path,
    *,
    accept_all: bool = False,
    prune_code: bool = False,
    from_ref: str = "HEAD~1",
    to_ref: str = "HEAD",
    post_commit: bool = False,
) -> SyncSummary:
    """Run the appropriate pipeline(s) for the repo's current stage.

    Parameters
    ----------
    root_dir:
        Absolute root of the codebase (contains .codoc/).
    accept_all:
        Auto-accept all pending proposals instead of pausing for review.
    prune_code:
        Passed through to accept when RETIRE_REFLECTIVE proposals are accepted.
    from_ref / to_ref:
        Git refs forwarded to the reflective pipeline when catching up on commits.
    post_commit:
        Internal flag set by the post-commit hook.  When True, runs reflect for
        the just-landed commit and writes the SNAPSHOT transaction.
    """
    root = Path(root_dir).resolve()
    state = repo_stage(root)
    result = SyncSummary(stage_before=state.stage.value)

    if post_commit:
        result = _run_post_commit(root, from_ref, to_ref, result)
        after = repo_stage(root)
        result.stage_after = after.stage.value
        result.pending_count = after.pending_count
        result.feature_count = after.feature_count
        return result

    match state.stage:
        case Stage.UNINIT:
            _init(root, result)
            _run_bootstrap(root, result)

        case Stage.NEEDS_BOOTSTRAP:
            _ensure_hooks(root, result)
            _run_bootstrap(root, result)

        case Stage.BOOTSTRAP_REVIEW:
            if accept_all:
                _accept_all(root, prune_code, result)
                _render_tree(root, result)
            else:
                result.actions.append(
                    f"{state.pending_count} bootstrap proposals pending — "
                    "run `codoc proposals` then `codoc accept`, or re-run with --yes"
                )

        case Stage.PROPOSALS_PENDING:
            if accept_all:
                _accept_all(root, prune_code, result)
                _render_tree(root, result)
            else:
                result.actions.append(
                    f"{state.pending_count} proposal(s) pending — "
                    "run `codoc proposals` to review, or re-run with --yes"
                )

        case Stage.STALE_RENDER:
            _render_tree(root, result)

        case Stage.CLEAN:
            _run_reflect(root, from_ref, to_ref, result)
            after_reflect = repo_stage(root)
            if after_reflect.stage == Stage.PROPOSALS_PENDING and accept_all:
                _accept_all(root, prune_code, result)
                _render_tree(root, result)
            elif after_reflect.stage == Stage.STALE_RENDER:
                _render_tree(root, result)

    after = repo_stage(root)
    result.stage_after = after.stage.value
    result.pending_count = after.pending_count
    result.feature_count = after.feature_count
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _init(root: Path, result: SyncSummary) -> None:
    codoc_dir = root / ".codoc"
    codoc_dir.mkdir(exist_ok=True)
    from codoc.storage.sqlite_store import SQLiteStore
    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    store.close()
    result.actions.append("initialized .codoc/")
    _ensure_hooks(root, result)


def _ensure_hooks(root: Path, result: SyncSummary) -> None:
    hooks_dir = root / ".git" / "hooks"
    if not hooks_dir.exists():
        return
    post_commit = hooks_dir / "post-commit"
    post_script = (
        '#!/bin/sh\n'
        'codoc sync --root-dir "$(git rev-parse --show-toplevel)" --post-commit\n'
    )
    post_commit.write_text(post_script, encoding="utf-8")
    post_commit.chmod(0o755)

    pre_commit = hooks_dir / "pre-commit"
    pre_script = (
        '#!/bin/sh\n'
        'STAGED="$(git diff --cached --name-only 2>/dev/null)"\n'
        'if [ -n "$STAGED" ]; then\n'
        '  ROOT="$(git rev-parse --show-toplevel)"\n'
        '  # Write snapshot-pending for post-commit to fill in the git SHA.\n'
        '  codoc sync --root-dir "$ROOT" --write-snapshot-pending\n'
        '  codoc commit-preflight --staged "$STAGED" --root-dir "$ROOT"\n'
        'fi\n'
        'exit 0\n'
    )
    pre_commit.write_text(pre_script, encoding="utf-8")
    pre_commit.chmod(0o755)
    result.actions.append("installed git hooks")


def _run_bootstrap(root: Path, result: SyncSummary) -> None:
    from codoc.pipelines.bootstrap.runner import run_bootstrap
    codoc_dir = root / ".codoc"
    try:
        r = run_bootstrap(
            root_dir=str(root),
            codoc_dir=str(codoc_dir),
            repo_name=root.name,
        )
        count = r.get("proposal_count", 0)
        result.actions.append(f"bootstrap: {r.get('chunk_count', 0)} chunks → {count} proposals")
    except Exception as exc:
        result.actions.append(f"bootstrap failed: {exc}")


def _run_reflect(root: Path, from_ref: str, to_ref: str, result: SyncSummary) -> None:
    from codoc.pipelines.reflective.runner import run_reflect
    codoc_dir = root / ".codoc"
    try:
        r = run_reflect(
            root_dir=str(root),
            codoc_dir=str(codoc_dir),
            from_ref=from_ref,
            to_ref=to_ref,
            repo_name=root.name,
        )
        proposals = r.get("proposal_count", 0)
        if proposals:
            result.actions.append(f"reflect: {proposals} new proposal(s)")
    except Exception as exc:
        result.actions.append(f"reflect failed: {exc}")


def _accept_all(root: Path, prune_code: bool, result: SyncSummary) -> None:
    from codoc.storage.sqlite_store import SQLiteStore
    from codoc.storage.jsonl_log import JSONLLog
    from codoc.core.log import TransactionLog
    from codoc.core.apply import apply_accepted_transaction

    codoc_dir = root / ".codoc"
    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    try:
        jsonl_log = JSONLLog(str(codoc_dir / "log.jsonl"))
        tx_log = TransactionLog(store)
        pending = store.list_transactions(proposal=True, limit=0)
        accepted = 0
        for tx in pending:
            apply_accepted_transaction(tx, store, root_dir=root, prune_code=prune_code)
            accepted_tx = tx_log.accept_proposal(tx.hlc.to_str())
            jsonl_log.append(accepted_tx)
            accepted += 1
        result.actions.append(f"accepted {accepted} proposal(s)")
    finally:
        store.close()


def _render_tree(root: Path, result: SyncSummary) -> None:
    from codoc.storage.sqlite_store import SQLiteStore
    from codoc.core.log import TransactionLog
    from codoc.projection.tree_codoc import write_tree

    codoc_dir = root / ".codoc"
    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    try:
        tx_log = TransactionLog(store)
        write_tree(str(codoc_dir), store, tx_log)
        result.actions.append("rendered .codoc/tree/_index.codoc")
    finally:
        store.close()


def _run_post_commit(root: Path, from_ref: str, to_ref: str, result: SyncSummary) -> SyncSummary:
    """Run reflect for the just-landed commit + write the SNAPSHOT tx."""
    _run_reflect(root, from_ref, to_ref, result)
    _write_snapshot(root, result)
    return result


def _write_snapshot(root: Path, result: SyncSummary) -> None:
    """Read .codoc/.snapshot-pending.json, fill in the git SHA, write SNAPSHOT tx."""
    import json
    import subprocess

    codoc_dir = root / ".codoc"
    pending_path = codoc_dir / ".snapshot-pending.json"

    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(root), text=True
        ).strip()
    except Exception:
        return

    pending_data: dict = {}
    if pending_path.exists():
        try:
            pending_data = json.loads(pending_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        pending_path.unlink(missing_ok=True)

    from codoc.storage.sqlite_store import SQLiteStore
    from codoc.storage.jsonl_log import JSONLLog
    from codoc.core.log import TransactionLog
    from codoc.model.transaction import Transaction, TransactionKind
    from codoc.model.hlc import HLC

    store = SQLiteStore(str(codoc_dir / "codoc.db"))
    store.open()
    try:
        jsonl_log = JSONLLog(str(codoc_dir / "log.jsonl"))
        tx_log = TransactionLog(store)

        # Detect which files were committed.
        try:
            files_output = subprocess.check_output(
                ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD"],
                cwd=str(root), text=True,
            )
            wrote_files = [f for f in files_output.splitlines() if f]
        except Exception:
            wrote_files = []

        snap_tx = Transaction(
            hlc=HLC.now(),
            parent_hlcs=[],
            kind=TransactionKind.SNAPSHOT,
            payload={
                "git_sha": sha,
                "head_hlc": pending_data.get("head_hlc", ""),
                "wrote_files": wrote_files,
            },
            author="codoc-hook",
            proposal=False,
        )
        tx_log.append(snap_tx)
        jsonl_log.append(snap_tx)
        result.actions.append(f"snapshot: {sha[:8]}")
    finally:
        store.close()
