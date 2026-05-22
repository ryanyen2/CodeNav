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
    no_intent: bool = False,
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
    no_intent:
        If True, skip LLM intent generation during bootstrap (offline/testing
        only).  By default the LLM always generates intent for each feature so
        users start from a meaningful tree they can curate, not a blank one.
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

    with_intent = not no_intent

    match state.stage:
        case Stage.UNINIT:
            _init(root, result)
            _run_bootstrap(root, result, with_intent=with_intent)
            if accept_all:
                _accept_all(root, prune_code, result)
                _finish_bootstrap(root, result)
                _reconcile_health(root, result)
                _render_tree(root, result)

        case Stage.NEEDS_BOOTSTRAP:
            _ensure_hooks(root, result)
            _run_bootstrap(root, result, with_intent=with_intent)
            if accept_all:
                _accept_all(root, prune_code, result)
                _finish_bootstrap(root, result)
                _reconcile_health(root, result)
                _render_tree(root, result)

        case Stage.BOOTSTRAP_REVIEW:
            if accept_all:
                _accept_all(root, prune_code, result)
                _finish_bootstrap(root, result)
                _reconcile_health(root, result)
                _render_tree(root, result)
            else:
                result.actions.append(
                    f"{state.pending_count} bootstrap proposals pending — "
                    "run `codoc proposals` then `codoc accept`, or re-run with --yes"
                )

        case Stage.PROPOSALS_PENDING:
            if accept_all:
                _accept_all(root, prune_code, result)
                _reconcile_health(root, result)
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
                _reconcile_health(root, result)
                _render_tree(root, result)
            elif after_reflect.stage == Stage.STALE_RENDER:
                _render_tree(root, result)
            elif after_reflect.stage == Stage.CLEAN:
                # If the user touched code without producing new proposals
                # (e.g. whitespace-only edits), still refresh resolutions so
                # FeatureState badges keep up.
                if "reflect" in " ".join(result.actions):
                    _reconcile_health(root, result)

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


def _run_bootstrap(root: Path, result: SyncSummary, *, with_intent: bool = False) -> None:
    """Run bootstrap unless pending bootstrap proposals already exist.

    Idempotent: if a prior bootstrap already emitted proposals that are still
    awaiting review, do nothing rather than emit duplicate INTRODUCE proposals.
    The user must accept/reject the pending batch before a new bootstrap runs.
    """
    from codoc.pipelines.bootstrap.runner import run_bootstrap
    from codoc.storage.sqlite_store import SQLiteStore

    codoc_dir = root / ".codoc"
    db_path = codoc_dir / "codoc.db"
    if db_path.exists():
        store = SQLiteStore(str(db_path))
        store.open()
        try:
            pending = store.list_transactions(proposal=True, limit=0)
        finally:
            store.close()
        if pending:
            result.actions.append(
                f"bootstrap: skipped — {len(pending)} pending proposal(s) from prior run"
            )
            return
    try:
        r = run_bootstrap(
            root_dir=str(root),
            codoc_dir=str(codoc_dir),
            repo_name=root.name,
            with_intent=with_intent,
        )
        count = r.get("proposal_count", 0)
        result.actions.append(f"bootstrap: {r.get('chunk_count', 0)} chunks → {count} proposals")
    except Exception as exc:
        result.actions.append(f"bootstrap failed: {exc}")


def _reconcile_health(root: Path, result: SyncSummary) -> None:
    """Run the binding-health sweep so feature_state reflects current reality.

    Without this the binding_resolutions table stays empty after bootstrap-accept
    and every feature derives to SEVERED (state_derivation treats missing
    resolutions as unresolved).  Silent on success; logs the failure on error.
    """
    from codoc.pipelines.health.runner import reconcile_all
    from codoc.storage.sqlite_store import SQLiteStore

    codoc_dir = root / ".codoc"
    db_path = codoc_dir / "codoc.db"
    if not db_path.exists():
        return
    store = SQLiteStore(str(db_path))
    store.open()
    try:
        reconcile_all(store, str(root))
    except Exception as exc:
        result.actions.append(f"health sweep failed: {exc}")
    finally:
        store.close()


def _finish_bootstrap(root: Path, result: SyncSummary) -> None:
    """Mark bootstrap complete by writing unattributed.json. Idempotent."""
    from codoc.pipelines.bootstrap.runner import finish_bootstrap
    codoc_dir = root / ".codoc"
    if (codoc_dir / "unattributed.json").exists():
        return
    try:
        r = finish_bootstrap(codoc_dir=str(codoc_dir))
        result.actions.append(f"bootstrap finished: {r.get('unattributed_count', 0)} unattributed")
    except Exception as exc:
        result.actions.append(f"finish_bootstrap failed: {exc}")


def _run_reflect(root: Path, from_ref: str, to_ref: str, result: SyncSummary) -> None:
    """Reflect committed (from_ref..to_ref) AND uncommitted working-tree changes.

    Order:
      1. Commit-based reflect for any diff between from_ref and to_ref.
      2. Working-tree reflect for files dirty or untracked relative to HEAD.

    Both are no-ops when there's nothing to do.
    """
    from codoc.pipelines.reflective.runner import run_reflect, run_reflect_files
    codoc_dir = root / ".codoc"

    proposals_total = 0
    try:
        r = run_reflect(
            root_dir=str(root),
            codoc_dir=str(codoc_dir),
            from_ref=from_ref,
            to_ref=to_ref,
            repo_name=root.name,
        )
        proposals_total += r.get("proposals_emitted", 0)
    except Exception as exc:
        result.actions.append(f"reflect (committed) failed: {exc}")

    dirty_files = _dirty_working_tree_files(root)
    if dirty_files:
        try:
            r = run_reflect_files(
                root_dir=str(root),
                codoc_dir=str(codoc_dir),
                file_paths=dirty_files,
            )
            proposals_total += r.get("proposals_emitted", 0)
        except Exception as exc:
            result.actions.append(f"reflect (working-tree) failed: {exc}")

    if proposals_total:
        result.actions.append(f"reflect: {proposals_total} new proposal(s)")


def _dirty_working_tree_files(root: Path) -> list[str]:
    """Return repo-relative paths for files changed but not committed.

    Includes: modified-staged, modified-unstaged, new (untracked) files —
    filtered to source extensions the language adapters know about.
    """
    import subprocess

    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain", "-z"],
            cwd=str(root), text=True,
        )
    except Exception:
        return []

    files: list[str] = []
    for entry in out.split("\0"):
        if not entry or len(entry) < 4:
            continue
        # Porcelain v1: "XY path" where X = index, Y = work-tree status.
        path = entry[3:]
        if path.startswith(".codoc/"):
            continue
        if not (path.endswith(".py") or path.endswith(".ts") or path.endswith(".tsx") or
                path.endswith(".js") or path.endswith(".jsx")):
            continue
        if (root / path).exists():
            files.append(path)
    return files


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

        # Seed chunk_fingerprints cache from every (file, symbol_path) now bound.
        # Without this, the very next reflect treats every bound chunk as "added"
        # because the cache is empty, producing spurious ABSORB proposals.
        _seed_fingerprint_cache_from_bindings(store)
    finally:
        store.close()


def _seed_fingerprint_cache_from_bindings(store) -> None:
    """Populate chunk_fingerprints for every binding that lacks a cache entry.

    Uses the fingerprint stored on the Binding itself (set at INTRODUCE time)
    so we never have to re-parse the file.  Idempotent: only writes when the
    cache key isn't already present.
    """
    from codoc.pipelines.reflective.types import chunk_cache_key

    cached = store.get_all_chunk_fingerprints()
    for binding in store.get_all_bindings():
        symbol_path = binding.anchor.symbol_path or ""
        key = chunk_cache_key(binding.anchor.file, symbol_path)
        if key in cached:
            continue
        if not binding.fingerprint:
            continue
        store.upsert_chunk_fingerprint(
            key=key,
            file=binding.anchor.file,
            symbol_path=symbol_path,
            fingerprint=binding.fingerprint,
            commit="",
        )


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
