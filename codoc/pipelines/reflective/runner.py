"""Top-level reflective pipeline runner.

Called by the post-commit hook or the ``codoc reflect`` CLI command.
Orchestrates git diff detection, fingerprint comparison, heuristic gating,
LLM escalation, and proposal emission.  Updates the chunk fingerprint cache
at the end so subsequent runs only process genuinely new changes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from codoc.storage.sqlite_store import SQLiteStore
from codoc.storage.jsonl_log import JSONLLog
from codoc.core.log import TransactionLog
from codoc.pipelines.reflective.commit_diff import (
    get_changed_files,
    extract_chunks_for_files,
    get_file_source,
)
from codoc.pipelines.reflective.fingerprint_compare import (
    compare_chunk_fingerprints,
    ChunkChange,
    _chunk_cache_key,
)
from codoc.pipelines.reflective.escalate import (
    should_escalate,
    emit_evict_proposal,
    is_cheap_absorb,
)
from codoc.pipelines.reflective.propose import escalate_to_llm
from codoc.model.transaction import Transaction, TransactionKind
from codoc.model.hlc import HLC


def run_reflect(
    root_dir: str,
    codoc_dir: str,
    from_ref: str = "HEAD~1",
    to_ref: str = "HEAD",
    repo_name: str = "codebase",
    node_id: str = "default",
) -> dict:
    """Run the reflective pipeline for commits *from_ref*…*to_ref*.

    Parameters
    ----------
    root_dir:
        Absolute path to the repository root.
    codoc_dir:
        Absolute path to the ``.codoc/`` directory where the SQLite store and
        JSONL log live.
    from_ref:
        Git ref to diff from (usually ``"HEAD~1"``).
    to_ref:
        Git ref to diff to (usually ``"HEAD"``).
    repo_name:
        Human-readable name passed to the LLM attribution prompt.
    node_id:
        HLC node identifier for this machine (used in transaction timestamps).

    Returns
    -------
    dict
        Summary with keys:
        ``changed_files``, ``changed_chunks``, ``skipped_unchanged``,
        ``evicted_directly``, ``escalated_to_llm``, ``proposals_emitted``,
        ``proposals`` (list of ``{hlc, kind, symbol_path}`` dicts).
    """
    codoc_path = Path(codoc_dir)
    db_path = str(codoc_path / "codoc.db")
    log_path = str(codoc_path / "log.jsonl")

    with SQLiteStore(db_path) as store:
        jsonl_log = JSONLLog(log_path)
        tx_log = TransactionLog(store, node_id=node_id)

        # --- 1. Detect changed files ---
        changed_files = get_changed_files(root_dir, from_ref=from_ref, to_ref=to_ref)

        # Determine which files were deleted (no longer on disk).
        deleted_files: list[str] = []
        surviving_files: list[str] = []
        for f in changed_files:
            source = get_file_source(root_dir, f)
            if source is None:
                deleted_files.append(f)
            else:
                surviving_files.append(f)

        # --- 2. Extract chunks for surviving files ---
        chunks_by_file = extract_chunks_for_files(root_dir, surviving_files)

        # Build language adapter cache so fingerprinting doesn't re-instantiate
        # adapters per chunk.
        from codoc.lang import detect_language, get_adapter
        language_adapters: dict = {}
        for file in chunks_by_file:
            lang = detect_language(file)
            if lang and lang not in language_adapters:
                try:
                    language_adapters[lang] = get_adapter(lang)
                except ValueError:
                    pass

        # --- 3. Compare fingerprints ---
        all_bindings = store.get_all_bindings()

        changes: list[ChunkChange] = compare_chunk_fingerprints(
            chunks_by_file=chunks_by_file,
            deleted_files=deleted_files,
            store=store,
            language_adapters=language_adapters,
        )

        # Count chunks that were unchanged (not in changes list).
        total_current_chunks = sum(len(c) for c in chunks_by_file.values())
        changed_chunk_count = len(changes)
        skipped_unchanged = total_current_chunks - sum(
            1 for ch in changes if ch.change_kind in ("added", "modified")
        )

        # --- 4. Route each change through heuristics / LLM ---
        evicted_directly = 0
        escalated_to_llm_count = 0
        proposals_emitted = 0
        proposal_summaries: list[dict] = []

        for change in changes:
            if change.change_kind == "removed":
                if change.existing_binding_uuid is not None:
                    # Emit EVICT proposal directly — no LLM needed.
                    tx = emit_evict_proposal(change, tx_log, author="reflective")
                    evicted_directly += 1
                    proposals_emitted += 1
                    jsonl_log.append(tx)
                    proposal_summaries.append(
                        {
                            "hlc": tx.hlc.to_str(),
                            "kind": tx.kind.value,
                            "symbol_path": change.symbol_path,
                        }
                    )
                # else: unattributed removal → silent no-op.
                continue

            # Check cheap absorb shortcut for "added" + unattributed chunks.
            if change.change_kind == "added" and change.existing_binding_uuid is None:
                if is_cheap_absorb(change, all_bindings):
                    tx = _emit_cheap_absorb(change, all_bindings, tx_log, author="reflective")
                    if tx is not None:
                        proposals_emitted += 1
                        jsonl_log.append(tx)
                        proposal_summaries.append(
                            {
                                "hlc": tx.hlc.to_str(),
                                "kind": tx.kind.value,
                                "symbol_path": change.symbol_path,
                            }
                        )
                    continue

            if not should_escalate(change, all_bindings):
                # Heuristic decided no action needed (e.g. unattributed modified chunk).
                continue

            # Escalate to LLM.
            escalated_to_llm_count += 1
            tx = escalate_to_llm(
                change,
                store=store,
                tx_log=tx_log,
                repo_name=repo_name,
                author="reflective",
            )
            if tx is not None:
                proposals_emitted += 1
                jsonl_log.append(tx)
                proposal_summaries.append(
                    {
                        "hlc": tx.hlc.to_str(),
                        "kind": tx.kind.value,
                        "symbol_path": change.symbol_path,
                    }
                )

        # --- 5. Resolve the current commit hash and update fingerprint cache ---
        commit_sha = _get_current_commit(root_dir)
        update_fingerprint_cache(store, chunks_by_file, language_adapters, commit_sha)

    # Re-render so proposals appear in .codoc/tree/ immediately after commit.
    # (run_reflect_files already does this; run_reflect was missing it.)
    if proposals_emitted > 0:
        from codoc.pipelines.intentional.runner import open_stores
        from codoc.projection.tree_codoc import write_tree

        store2, _, tx_log2 = open_stores(codoc_dir)
        try:
            write_tree(codoc_dir, store2, tx_log2)
        finally:
            store2.close()

    return {
        "changed_files": len(changed_files),
        "changed_chunks": changed_chunk_count,
        "skipped_unchanged": max(skipped_unchanged, 0),
        "evicted_directly": evicted_directly,
        "escalated_to_llm": escalated_to_llm_count,
        "proposals_emitted": proposals_emitted,
        "proposals": proposal_summaries,
    }


def _get_pending_plan_slugs(store) -> dict[str, str]:
    """Return {slug: plan_session_id} for pending plan proposals."""
    from codoc.model.transaction import TransactionKind
    pending = store.list_transactions(proposal=True, limit=0)
    result: dict[str, str] = {}
    for tx in pending:
        if tx.payload.get("source") != "plan":
            continue
        session_id = tx.payload.get("plan_session_id", "")
        slug = tx.payload.get("slug", "")
        if slug:
            result[slug] = session_id
    return result


def _maybe_tag_plan_aligned(tx_summary: dict, store, pending_plan_slugs: dict) -> None:
    """Tag a proposal summary as plan_aligned if it matches a pending plan op."""
    slug = tx_summary.get("slug", "") or tx_summary.get("symbol_path", "")
    if not slug:
        return
    # Check if any feature bound to this symbol has a pending plan op.
    feature_uuid = tx_summary.get("feature_uuid", "")
    if feature_uuid:
        feature = store.get_feature(feature_uuid)
        if feature and feature.slug in pending_plan_slugs:
            tx_summary["plan_aligned"] = True
            tx_summary["plan_session_id"] = pending_plan_slugs[feature.slug]


def _get_changed_chunks_for_file(
    root_dir: str,
    file_path: str,
    abs_path: str,
    store,
) -> tuple[list, dict, dict]:
    """Extract changed chunks for a single file by comparing fingerprints.

    Returns
    -------
    tuple of (changes, chunks_by_file, language_adapters)
    """
    try:
        from codoc.lang import detect_language, get_adapter
        from codoc.pipelines.reflective.commit_diff import extract_chunks_for_files
        from codoc.pipelines.reflective.fingerprint_compare import compare_chunk_fingerprints

        chunks_by_file = extract_chunks_for_files(root_dir=root_dir, file_paths=[file_path])

        # Build language adapter cache.
        language_adapters: dict = {}
        for file in chunks_by_file:
            lang = detect_language(file)
            if lang and lang not in language_adapters:
                try:
                    language_adapters[lang] = get_adapter(lang)
                except ValueError:
                    pass

        changes = compare_chunk_fingerprints(
            chunks_by_file=chunks_by_file,
            deleted_files=[],
            store=store,
            language_adapters=language_adapters,
        )
        return changes, chunks_by_file, language_adapters
    except Exception:
        return [], {}, {}


def run_reflect_files(
    root_dir: str,
    codoc_dir: str,
    file_paths: list[str],
    node_id: str = "default",
    session_id: str | None = None,
    author: str = "reflective",
) -> dict:
    """Incremental reflect for specific files (no git refs required).

    Used by on-save hooks and the API for Flow 2 (bottom-up real-time reflection).
    Skips the git diff step and processes the given files directly.

    Args:
        root_dir: Root directory of the codebase.
        codoc_dir: Path to .codoc/ directory.
        file_paths: List of repo-relative file paths to process.

    Returns:
        dict with processed_files, changed_chunks, proposals_emitted, proposals.
    """
    from codoc.pipelines.intentional.runner import open_stores
    from codoc.projection.tree_codoc import write_tree

    store, jsonl_log, tx_log = open_stores(codoc_dir)
    result: dict = {
        "processed_files": len(file_paths),
        "changed_chunks": 0,
        "skipped_unchanged": 0,
        "evicted_directly": 0,
        "escalated_to_llm": 0,
        "proposals_emitted": 0,
        "proposals": [],
    }
    # Declared before try so they're always accessible after finally.
    all_chunks_by_file: dict = {}
    all_language_adapters: dict = {}

    try:
        pending_plan_slugs = _get_pending_plan_slugs(store)

        # Accumulate changes across all requested files.
        all_changed: list = []
        for file_path in file_paths:
            abs_path = str(Path(root_dir) / file_path)
            if not Path(abs_path).exists():
                continue
            try:
                changed, chunks_by_file, language_adapters = _get_changed_chunks_for_file(
                    root_dir=root_dir,
                    file_path=file_path,
                    abs_path=abs_path,
                    store=store,
                )
                all_changed.extend(changed)
                all_chunks_by_file.update(chunks_by_file)
                all_language_adapters.update(language_adapters)
            except Exception:
                continue

        result["changed_chunks"] = len(all_changed)

        if all_changed:
            # Re-use the per-change routing logic from run_reflect.
            all_bindings = store.get_all_bindings()
            evicted_directly = 0
            escalated_to_llm_count = 0
            proposals_emitted = 0
            proposal_summaries: list[dict] = []

            for change in all_changed:
                if change.change_kind == "removed":
                    if change.existing_binding_uuid is not None:
                        tx = emit_evict_proposal(change, tx_log, author=author)
                        evicted_directly += 1
                        proposals_emitted += 1
                        jsonl_log.append(tx)
                        summary = {
                            "hlc": tx.hlc.to_str(),
                            "kind": tx.kind.value,
                            "symbol_path": change.symbol_path,
                        }
                        _maybe_tag_plan_aligned(summary, store, pending_plan_slugs)
                        proposal_summaries.append(summary)
                    continue

                if change.change_kind == "added" and change.existing_binding_uuid is None:
                    if is_cheap_absorb(change, all_bindings):
                        tx = _emit_cheap_absorb(change, all_bindings, tx_log, author=author)
                        if tx is not None:
                            proposals_emitted += 1
                            jsonl_log.append(tx)
                            summary = {
                                "hlc": tx.hlc.to_str(),
                                "kind": tx.kind.value,
                                "symbol_path": change.symbol_path,
                            }
                            _maybe_tag_plan_aligned(summary, store, pending_plan_slugs)
                            proposal_summaries.append(summary)
                        continue

                if not should_escalate(change, all_bindings):
                    continue

                escalated_to_llm_count += 1
                tx = escalate_to_llm(
                    change,
                    store=store,
                    tx_log=tx_log,
                    repo_name="codebase",
                    author=author,
                )
                if tx is not None:
                    proposals_emitted += 1
                    jsonl_log.append(tx)
                    summary = {
                        "hlc": tx.hlc.to_str(),
                        "kind": tx.kind.value,
                        "symbol_path": change.symbol_path,
                    }
                    _maybe_tag_plan_aligned(summary, store, pending_plan_slugs)
                    proposal_summaries.append(summary)

            result["skipped_unchanged"] = max(result["changed_chunks"] - sum(
                1 for ch in all_changed if ch.change_kind in ("added", "modified")
            ), 0)
            result["evicted_directly"] = evicted_directly
            result["escalated_to_llm"] = escalated_to_llm_count
            result["proposals_emitted"] = proposals_emitted
            result["proposals"] = proposal_summaries

    finally:
        store.close()

    # Update fingerprint cache so subsequent runs only process new changes.
    if all_chunks_by_file:
        commit_sha = _get_current_commit(root_dir)
        store3 = SQLiteStore(str(Path(codoc_dir) / "codoc.db"))
        store3.open()
        try:
            update_fingerprint_cache(store3, all_chunks_by_file, all_language_adapters, commit_sha)
        finally:
            store3.close()

    # Re-render .codoc files if any proposals were emitted.
    if result["proposals_emitted"] > 0 or result["changed_chunks"] > 0:
        store2, _, tx_log2 = open_stores(codoc_dir)
        try:
            write_tree(codoc_dir, store2, tx_log2)
        finally:
            store2.close()

    return result


def update_fingerprint_cache(
    store: SQLiteStore,
    chunks_by_file: dict,
    language_adapters: dict,
    commit: str,
) -> None:
    """Update the chunk fingerprint cache in SQLite for all currently-visible chunks.

    Called after proposals are emitted so that the next run of the reflective
    pipeline only processes genuinely new changes.

    Parameters
    ----------
    store:
        An open SQLiteStore (caller manages lifecycle).
    chunks_by_file:
        Current-state chunks as returned by ``extract_chunks_for_files``.
    language_adapters:
        Adapter instances keyed by language name; used to fingerprint chunks.
    commit:
        The current commit SHA to record in ``last_seen_commit``.
    """
    from codoc.lang import detect_language, get_adapter
    from codoc.core.fingerprint import fingerprint_chunk

    for file, chunks in chunks_by_file.items():
        lang = detect_language(file)
        if lang is None:
            continue
        adapter = language_adapters.get(lang)
        if adapter is None:
            try:
                adapter = get_adapter(lang)
            except ValueError:
                continue

        for chunk in chunks:
            try:
                fp = fingerprint_chunk(chunk.source, adapter)
            except Exception:
                continue
            cache_key = _chunk_cache_key(file, chunk.symbol_path)
            store.upsert_chunk_fingerprint(
                key=cache_key,
                file=file,
                symbol_path=chunk.symbol_path,
                fingerprint=fp,
                commit=commit,
            )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_current_commit(root_dir: str) -> str:
    """Return the current HEAD commit SHA, or empty string on failure."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return ""


def _emit_cheap_absorb(
    change: ChunkChange,
    all_bindings: list,
    tx_log: TransactionLog,
    author: str = "reflective",
) -> Transaction | None:
    """Emit an ABSORB proposal using the cheap same-file heuristic.

    Finds the one feature whose bindings all live in the same file and emits
    an ABSORB proposal to attach the new chunk to that feature.
    """
    bindings_by_feature: dict[str, list] = {}
    for b in all_bindings:
        bindings_by_feature.setdefault(b.feature_uuid, []).append(b)

    target_feature_uuid: str | None = None
    for feature_uuid, bindings in bindings_by_feature.items():
        if all(b.anchor.file == change.file for b in bindings):
            target_feature_uuid = feature_uuid
            break  # is_cheap_absorb already guarantees exactly one match

    if target_feature_uuid is None:
        return None

    hlc = HLC.now()
    payload: dict = {
        "feature_uuid": target_feature_uuid,
        "symbol_path": change.symbol_path,
        "file": change.file,
        "rationale": "cheap_absorb_heuristic: new chunk in same file as existing feature",
        "current_fingerprint": change.current_fingerprint,
    }

    tx = Transaction(
        hlc=hlc,
        parent_hlcs=[],
        kind=TransactionKind.ABSORB,
        payload=payload,
        author=author,
        proposal=True,
    )

    return tx_log.append_proposal(tx)
