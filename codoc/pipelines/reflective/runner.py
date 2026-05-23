"""Top-level reflective pipeline runner (cocoindex-driven).

Called by the post-commit hook or the ``codoc reflect`` CLI command. The
reflective pipeline detects what changed in the working tree, reconciles
those changes against existing bindings, and emits proposals for the user
to review.

The previous git-diff + ``chunk_fingerprints`` table mechanism has been
replaced by snapshotting the LanceDB-backed index before and after a
``cocoindex update``. The snapshot diff is the source of truth for what
chunks were added / modified / removed; everything downstream (move
detection, FRACTURE / COALESCE, LLM escalation, dedup) is unchanged.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from codoc.core.log import TransactionLog
from codoc.lang import Chunk
from codoc.model.hlc import HLC
from codoc.model.transaction import Transaction, TransactionKind
from codoc.pipelines.indexing.reader import ChunkRow, read_all_chunks
from codoc.pipelines.indexing.runner import update_index
from codoc.pipelines.reflective.escalate import (
    emit_evict_proposal,
    is_namespace_absorb,
    should_escalate,
)
from codoc.pipelines.reflective.propose import escalate_to_llm, propose_for_new_file
from codoc.pipelines.reflective.types import ChunkChange
from codoc.storage.jsonl_log import JSONLLog
from codoc.storage.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Diff: LanceDB snapshot → ChunkChange list
# ---------------------------------------------------------------------------


@dataclass
class _ReflectiveDiff:
    changes: list[ChunkChange]
    chunks_by_file: dict[str, list[Chunk]]
    deleted_files: list[str]
    surviving_files: list[str]
    language_adapters: dict
    old_file_sources: dict[str, str]
    old_chunk_sources: dict[tuple[str, str], str]


def _row_to_chunk(row: ChunkRow) -> Chunk:
    return Chunk(
        symbol_path=row.symbol_path,
        file=row.file,
        start_byte=row.start_byte,
        end_byte=row.end_byte,
        source=row.source,
    )


def _build_language_adapters(files) -> dict:
    from codoc.lang import detect_language, get_adapter

    adapters: dict = {}
    for file in files:
        lang = detect_language(file)
        if lang and lang not in adapters:
            try:
                adapters[lang] = get_adapter(lang)
            except ValueError:
                pass
    return adapters


def _scope_rows(rows: list[ChunkRow], file_scope: set[str] | None) -> list[ChunkRow]:
    if file_scope is None:
        return rows
    return [r for r in rows if r.file in file_scope]


def _reconstruct_old_file_source(rows: list[ChunkRow], file: str) -> str:
    """Concatenate chunk sources for *file* in start_byte order.

    This is an approximation — chunks don't cover every byte (e.g., trailing
    whitespace, content not extractable by the adapter) — but it's sufficient
    for whole-file MinHash similarity in :mod:`file_events`.
    """
    file_rows = sorted(
        (r for r in rows if r.file == file),
        key=lambda r: r.start_byte,
    )
    return "\n".join(r.source for r in file_rows)


def _build_diff(
    root_dir: str,
    codoc_dir: str,
    store: SQLiteStore,
    *,
    file_scope: list[str] | None = None,
) -> _ReflectiveDiff:
    """Snapshot LanceDB before/after a cocoindex update, then materialise the diff."""
    scope_set: set[str] | None = set(file_scope) if file_scope is not None else None

    old_rows_all = read_all_chunks(codoc_dir)
    old_rows = _scope_rows(old_rows_all, scope_set)

    update_index(root_dir, codoc_dir)

    new_rows_all = read_all_chunks(codoc_dir)
    new_rows = _scope_rows(new_rows_all, scope_set)

    old_by_key: dict[tuple[str, str], ChunkRow] = {(r.file, r.symbol_path): r for r in old_rows}
    new_by_key: dict[tuple[str, str], ChunkRow] = {(r.file, r.symbol_path): r for r in new_rows}

    chunks_by_file: dict[str, list[Chunk]] = defaultdict(list)
    for r in new_rows:
        chunks_by_file[r.file].append(_row_to_chunk(r))
    for chunks in chunks_by_file.values():
        chunks.sort(key=lambda c: c.start_byte)

    old_files = {r.file for r in old_rows}
    new_files = {r.file for r in new_rows}
    deleted_files = sorted(old_files - new_files)
    surviving_files = sorted(new_files)

    language_adapters = _build_language_adapters(old_files | new_files)

    # Binding lookup: (file, symbol_path) → binding.uuid
    all_bindings = store.get_all_bindings()
    binding_by_anchor: dict[tuple[str, str], str] = {}
    for b in all_bindings:
        binding_by_anchor[(b.anchor.file, b.anchor.symbol_path or "")] = b.uuid

    changes: list[ChunkChange] = []
    all_keys = set(old_by_key) | set(new_by_key)
    for key in all_keys:
        file, symbol_path = key
        old = old_by_key.get(key)
        new = new_by_key.get(key)
        existing_uuid = binding_by_anchor.get((file, symbol_path))

        if old is None and new is not None:
            changes.append(ChunkChange(
                chunk=_row_to_chunk(new),
                symbol_path=symbol_path,
                file=file,
                change_kind="added",
                current_fingerprint=new.tokens_hash,
                stored_fingerprint=None,
                existing_binding_uuid=existing_uuid,
            ))
        elif old is not None and new is None:
            changes.append(ChunkChange(
                chunk=None,
                symbol_path=symbol_path,
                file=file,
                change_kind="removed",
                current_fingerprint=None,
                stored_fingerprint=old.tokens_hash,
                existing_binding_uuid=existing_uuid,
            ))
        elif old is not None and new is not None:
            if old.tokens_hash != new.tokens_hash:
                changes.append(ChunkChange(
                    chunk=_row_to_chunk(new),
                    symbol_path=symbol_path,
                    file=file,
                    change_kind="modified",
                    current_fingerprint=new.tokens_hash,
                    stored_fingerprint=old.tokens_hash,
                    existing_binding_uuid=existing_uuid,
                ))

    # Also synthesize "removed" changes for bindings whose anchor no longer
    # appears in either snapshot (e.g., file remained but binding's symbol_path
    # was renamed away). The pre-cocoindex flow caught this via the
    # chunk_fingerprints table; the snapshot diff alone does not.
    seen_keys = set(old_by_key) | set(new_by_key)
    for b in all_bindings:
        anchor_key = (b.anchor.file, b.anchor.symbol_path or "")
        if anchor_key in seen_keys:
            continue
        # Skip if the file was fully deleted — handled by file_events.
        if b.anchor.file in deleted_files:
            continue
        if scope_set is not None and b.anchor.file not in scope_set:
            continue
        changes.append(ChunkChange(
            chunk=None,
            symbol_path=b.anchor.symbol_path or "",
            file=b.anchor.file,
            change_kind="removed",
            current_fingerprint=None,
            stored_fingerprint=b.fingerprint,
            existing_binding_uuid=b.uuid,
        ))

    # Old chunk sources for move detection: any chunk that disappeared from new_rows.
    old_chunk_sources: dict[tuple[str, str], str] = {}
    for key, row in old_by_key.items():
        if key not in new_by_key:
            old_chunk_sources[key] = row.source

    # Old file sources for file_events MinHash. Reconstruct from chunk sources.
    old_file_sources: dict[str, str] = {
        f: _reconstruct_old_file_source(old_rows, f) for f in deleted_files
    }

    return _ReflectiveDiff(
        changes=changes,
        chunks_by_file=dict(chunks_by_file),
        deleted_files=deleted_files,
        surviving_files=surviving_files,
        language_adapters=language_adapters,
        old_file_sources=old_file_sources,
        old_chunk_sources=old_chunk_sources,
    )


# ---------------------------------------------------------------------------
# Move / FRACTURE / COALESCE detection (adapted to LanceDB-sourced old sources)
# ---------------------------------------------------------------------------


def _run_move_detection(
    changes: list[ChunkChange],
    old_chunk_sources: dict[tuple[str, str], str],
    language_adapters: dict,
) -> tuple[dict, set, set]:
    from codoc.core.chunk_matching.matcher import MatchResult, match_chunk_sets

    removed_chunks = [
        {
            "file": ch.file,
            "symbol_path": ch.symbol_path,
            "source": old_chunk_sources.get((ch.file, ch.symbol_path), ""),
        }
        for ch in changes
        if ch.change_kind == "removed"
    ]
    added_chunks = [
        {
            "file": ch.file,
            "symbol_path": ch.symbol_path,
            "source": ch.chunk.source if ch.chunk is not None else "",
        }
        for ch in changes
        if ch.change_kind == "added"
    ]

    if not removed_chunks or not added_chunks:
        return {}, set(), set()
    if not any(c["source"] for c in removed_chunks):
        return {}, set(), set()

    adapter = next(iter(language_adapters.values()), None)
    results: list[MatchResult] = match_chunk_sets(
        removed_chunks, added_chunks, language_adapter=adapter,
    )

    move_matches: dict[tuple[str, str], MatchResult] = {}
    matched_old: set[tuple[str, str]] = set()
    matched_new: set[tuple[str, str]] = set()
    for m in results:
        move_matches[(m.old_file, m.old_symbol_path)] = m
        matched_old.add((m.old_file, m.old_symbol_path))
        matched_new.add((m.new_file, m.new_symbol_path))
    return move_matches, matched_old, matched_new


def _detect_fracture_coalesce(
    changes: list[ChunkChange],
    old_chunk_sources: dict[tuple[str, str], str],
    matched_old_keys: set[tuple[str, str]],
    matched_new_keys: set[tuple[str, str]],
    store,
    tx_log,
    author: str = "reflective",
) -> tuple[list[Transaction], set[tuple[str, str]], set[tuple[str, str]]]:
    from codoc.core.chunk_matching.minhash import (
        minhash_jaccard as _mh_jac,
        minhash_sketch as _mh_sketch,
    )

    THRESHOLD = 0.35

    unmatched_removed = [
        ch
        for ch in changes
        if ch.change_kind == "removed"
        and (ch.file, ch.symbol_path) not in matched_old_keys
        and ch.existing_binding_uuid is not None
        and old_chunk_sources.get((ch.file, ch.symbol_path))
    ]
    unmatched_added = [
        ch
        for ch in changes
        if ch.change_kind == "added"
        and (ch.file, ch.symbol_path) not in matched_new_keys
        and ch.chunk is not None
    ]

    if not unmatched_removed or not unmatched_added:
        return [], set(), set()

    def _sketch(text: str) -> bytes:
        return _mh_sketch(text.split()) if text else b""

    old_sketches = {
        (ch.file, ch.symbol_path): _sketch(
            old_chunk_sources.get((ch.file, ch.symbol_path), "")
        )
        for ch in unmatched_removed
    }
    new_sketches = {
        (ch.file, ch.symbol_path): _sketch(ch.chunk.source if ch.chunk else "")
        for ch in unmatched_added
    }

    sim: dict[tuple, dict] = {ok: {} for ok in old_sketches}
    for ok, omh in old_sketches.items():
        if not omh:
            continue
        for nk, nmh in new_sketches.items():
            if not nmh:
                continue
            s = _mh_jac(omh, nmh)
            if s >= THRESHOLD:
                sim[ok][nk] = s

    proposals: list[Transaction] = []
    claimed_old: set[tuple[str, str]] = set()
    claimed_new: set[tuple[str, str]] = set()

    old_by_key = {(ch.file, ch.symbol_path): ch for ch in unmatched_removed}
    new_by_key = {(ch.file, ch.symbol_path): ch for ch in unmatched_added}

    # FRACTURE: one old → N new
    for ok, ok_matches in sim.items():
        if ok in claimed_old:
            continue
        eligible_new = [(nk, s) for nk, s in ok_matches.items() if nk not in claimed_new]
        if len(eligible_new) < 2:
            continue
        eligible_new.sort(key=lambda x: -x[1])
        top_new = eligible_new[:5]
        old_ch = old_by_key[ok]
        binding = store.get_binding(old_ch.existing_binding_uuid)
        if binding is None:
            continue
        new_chunks_payload = [
            {
                "file": nk[0],
                "symbol_path": nk[1],
                "fingerprint": new_by_key[nk].current_fingerprint or "",
            }
            for nk, _ in top_new
        ]
        tx = Transaction(
            hlc=HLC.now(),
            parent_hlcs=[],
            kind=TransactionKind.FRACTURE,
            payload={
                "source_binding_uuid": old_ch.existing_binding_uuid,
                "feature_uuid": binding.feature_uuid,
                "old_file": ok[0],
                "old_symbol_path": ok[1],
                "new_chunks": new_chunks_payload,
                "rationale": f"1→{len(top_new)} split detected",
            },
            author=author,
            proposal=True,
            label=f"fracture {ok[1]} → {len(top_new)} chunks",
        )
        try:
            stamped = tx_log.append_proposal(tx)
            proposals.append(stamped)
            claimed_old.add(ok)
            for nk, _ in top_new:
                claimed_new.add(nk)
        except Exception:
            pass

    # COALESCE: N old → one new
    for nk in new_by_key:
        if nk in claimed_new:
            continue
        eligible_old = [
            (ok, sim[ok].get(nk, 0.0))
            for ok in old_by_key
            if ok not in claimed_old and nk in sim.get(ok, {})
        ]
        if len(eligible_old) < 2:
            continue
        eligible_old.sort(key=lambda x: -x[1])
        top_old = eligible_old[:5]
        feature_uuids = set()
        source_binding_uuids: list[str] = []
        for ok, _ in top_old:
            old_ch = old_by_key[ok]
            b = store.get_binding(old_ch.existing_binding_uuid)
            if b is None:
                break
            feature_uuids.add(b.feature_uuid)
            source_binding_uuids.append(old_ch.existing_binding_uuid)
        if len(feature_uuids) != 1 or len(source_binding_uuids) != len(top_old):
            continue
        feature_uuid = next(iter(feature_uuids))
        survivor_uuid = source_binding_uuids[0]
        new_ch = new_by_key[nk]
        tx = Transaction(
            hlc=HLC.now(),
            parent_hlcs=[],
            kind=TransactionKind.COALESCE,
            payload={
                "source_binding_uuids": source_binding_uuids,
                "survivor_uuid": survivor_uuid,
                "feature_uuid": feature_uuid,
                "new_chunk": {
                    "file": nk[0],
                    "symbol_path": nk[1],
                    "fingerprint": new_ch.current_fingerprint or "",
                },
                "rationale": f"{len(top_old)}→1 merge detected",
            },
            author=author,
            proposal=True,
            label=f"coalesce {len(top_old)} chunks → {nk[1]}",
        )
        try:
            stamped = tx_log.append_proposal(tx)
            proposals.append(stamped)
            claimed_new.add(nk)
            for ok, _ in top_old:
                claimed_old.add(ok)
        except Exception:
            pass

    return proposals, claimed_old, claimed_new


def _emit_moved_proposal(
    old_change: ChunkChange,
    match_result,
    new_fingerprint: str | None,
    tx_log: TransactionLog,
    author: str = "reflective",
) -> Transaction:
    payload: dict = {
        "binding_uuid": old_change.existing_binding_uuid,
        "old_file": old_change.file,
        "old_symbol_path": old_change.symbol_path,
        "new_file": match_result.new_file,
        "new_symbol_path": match_result.new_symbol_path,
        "new_fingerprint": new_fingerprint or "",
        "score": round(match_result.score, 4),
        "evidence": match_result.evidence,
    }
    tx = Transaction(
        hlc=HLC.now(),
        parent_hlcs=[],
        kind=TransactionKind.MOVED,
        payload=payload,
        author=author,
        proposal=True,
        label=f"moved {old_change.symbol_path} → {match_result.new_symbol_path}",
    )
    return tx_log.append_proposal(tx)


def _emit_cheap_absorb(
    change: ChunkChange,
    all_bindings: list,
    tx_log: TransactionLog,
    author: str = "reflective",
) -> Transaction | None:
    bindings_by_feature: dict[str, list] = {}
    for b in all_bindings:
        bindings_by_feature.setdefault(b.feature_uuid, []).append(b)
    target_feature_uuid: str | None = None
    for feature_uuid, bindings in bindings_by_feature.items():
        if all(b.anchor.file == change.file for b in bindings):
            target_feature_uuid = feature_uuid
            break
    if target_feature_uuid is None:
        return None
    payload: dict = {
        "feature_uuid": target_feature_uuid,
        "symbol_path": change.symbol_path,
        "file": change.file,
        "rationale": "cheap_absorb_heuristic: new chunk in same file as existing feature",
        "current_fingerprint": change.current_fingerprint,
    }
    tx = Transaction(
        hlc=HLC.now(),
        parent_hlcs=[],
        kind=TransactionKind.ABSORB,
        payload=payload,
        author=author,
        proposal=True,
    )
    return tx_log.append_proposal(tx)


# ---------------------------------------------------------------------------
# Pending-proposal lookup helpers (kept identical to pre-cocoindex behavior)
# ---------------------------------------------------------------------------


def _get_pending_plan_slugs(store) -> dict[str, str]:
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
    slug = tx_summary.get("slug", "") or tx_summary.get("symbol_path", "")
    if not slug:
        return
    feature_uuid = tx_summary.get("feature_uuid", "")
    if feature_uuid:
        feature = store.get_feature(feature_uuid)
        if feature and feature.slug in pending_plan_slugs:
            tx_summary["plan_aligned"] = True
            tx_summary["plan_session_id"] = pending_plan_slugs[feature.slug]


# ---------------------------------------------------------------------------
# Core orchestrator
# ---------------------------------------------------------------------------


def _orchestrate(
    diff: _ReflectiveDiff,
    *,
    root_dir: str,
    repo_name: str,
    store: SQLiteStore,
    tx_log: TransactionLog,
    jsonl_log: JSONLLog,
    author: str = "reflective",
) -> dict:
    """Run move-detection / FRACTURE / batch / escalation / dedup over a diff."""
    from codoc.pipelines.reflective.file_events import detect_file_events

    pending_plan_slugs = _get_pending_plan_slugs(store)

    proposals_emitted = 0
    evicted_directly = 0
    escalated_to_llm_count = 0
    proposal_summaries: list[dict] = []

    # --- File-level events: RENAME_FILE / RETIRE_FILE ---
    file_event_proposals, claimed_deleted, claimed_surviving = detect_file_events(
        deleted_files=diff.deleted_files,
        surviving_files=diff.surviving_files,
        root_dir=root_dir,
        from_ref=None,
        store=store,
        tx_log=tx_log,
        author=author,
        old_file_sources=diff.old_file_sources,
    )

    # Filter changes for chunks in files claimed by file events.
    changes = [
        ch
        for ch in diff.changes
        if ch.file not in claimed_surviving and ch.file not in claimed_deleted
    ]

    # --- Move detection ---
    move_matches, matched_old_keys, matched_new_keys = _run_move_detection(
        changes, diff.old_chunk_sources, diff.language_adapters,
    )
    added_changes_by_key: dict[tuple[str, str], ChunkChange] = {
        (ch.file, ch.symbol_path): ch for ch in changes if ch.change_kind == "added"
    }

    # --- FRACTURE / COALESCE detection ---
    fracture_proposals, fracture_claimed_old, fracture_claimed_new = (
        _detect_fracture_coalesce(
            changes=changes,
            old_chunk_sources=diff.old_chunk_sources,
            matched_old_keys=matched_old_keys,
            matched_new_keys=matched_new_keys,
            store=store,
            tx_log=tx_log,
            author=author,
        )
    )

    # --- Pre-seed proposal_summaries with file-level + fracture/coalesce ---
    for tx in file_event_proposals + fracture_proposals:
        proposals_emitted += 1
        jsonl_log.append(tx)
        proposal_summaries.append({
            "hlc": tx.hlc.to_str(),
            "kind": tx.kind.value,
            "symbol_path": tx.payload.get(
                "file", tx.payload.get("old_file", "")
            ),
            "file": tx.payload.get("file", tx.payload.get("old_file", "")),
        })

    # --- Build pending-proposals set to avoid re-firing ---
    pending_proposals = store.list_transactions(proposal=True, limit=0)
    pending_file_symbols: set[tuple[str, str]] = set()
    for p in pending_proposals:
        pf = p.payload.get("file", "")
        ps = p.payload.get("symbol_path", "")
        if pf and ps:
            pending_file_symbols.add((pf, ps))

    # --- New-file pre-pass: batch new files into single LLM call ---
    new_file_keys: set[tuple[str, str]] = set()
    changes_by_file: dict[str, list[ChunkChange]] = {}
    for ch in changes:
        changes_by_file.setdefault(ch.file, []).append(ch)

    for file, file_changes in changes_by_file.items():
        is_all_new = all(
            ch.change_kind == "added" and ch.existing_binding_uuid is None
            for ch in file_changes
        )
        has_pending = any(
            (ch.file, ch.symbol_path) in pending_file_symbols
            for ch in file_changes
        )
        if is_all_new and not has_pending and len(file_changes) > 1:
            batch_txs = propose_for_new_file(
                file=file,
                changes=file_changes,
                store=store,
                tx_log=tx_log,
                root_dir=root_dir,
                repo_name=repo_name,
                author=author,
                language_adapters=diff.language_adapters,
            )
            for tx in batch_txs:
                proposals_emitted += 1
                jsonl_log.append(tx)
                summary = {
                    "hlc": tx.hlc.to_str(),
                    "kind": tx.kind.value,
                    "symbol_path": file,
                    "file": file,
                }
                _maybe_tag_plan_aligned(summary, store, pending_plan_slugs)
                proposal_summaries.append(summary)
            for ch in file_changes:
                new_file_keys.add((ch.file, ch.symbol_path))

    # --- Per-change routing ---
    all_bindings = store.get_all_bindings()
    for change in changes:
        key = (change.file, change.symbol_path)

        if key in new_file_keys:
            continue
        if key in fracture_claimed_old or key in fracture_claimed_new:
            continue
        if key in pending_file_symbols:
            continue

        if change.change_kind == "removed":
            if key in matched_old_keys:
                match = move_matches[key]
                new_ch = added_changes_by_key.get(
                    (match.new_file, match.new_symbol_path)
                )
                new_fp = new_ch.current_fingerprint if new_ch else None
                tx = _emit_moved_proposal(change, match, new_fp, tx_log, author=author)
                proposals_emitted += 1
                jsonl_log.append(tx)
                summary = {
                    "hlc": tx.hlc.to_str(),
                    "kind": tx.kind.value,
                    "symbol_path": change.symbol_path,
                    "file": change.file,
                }
                _maybe_tag_plan_aligned(summary, store, pending_plan_slugs)
                proposal_summaries.append(summary)
            elif change.existing_binding_uuid is not None:
                tx = emit_evict_proposal(change, tx_log, author=author)
                evicted_directly += 1
                proposals_emitted += 1
                jsonl_log.append(tx)
                summary = {
                    "hlc": tx.hlc.to_str(),
                    "kind": tx.kind.value,
                    "symbol_path": change.symbol_path,
                    "file": change.file,
                }
                _maybe_tag_plan_aligned(summary, store, pending_plan_slugs)
                proposal_summaries.append(summary)
            continue

        if key in matched_new_keys:
            continue

        if change.change_kind == "added" and change.existing_binding_uuid is None:
            if is_namespace_absorb(change, all_bindings):
                tx = _emit_cheap_absorb(change, all_bindings, tx_log, author=author)
                if tx is not None:
                    proposals_emitted += 1
                    jsonl_log.append(tx)
                    summary = {
                        "hlc": tx.hlc.to_str(),
                        "kind": tx.kind.value,
                        "symbol_path": change.symbol_path,
                        "file": change.file,
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
            repo_name=repo_name,
            author=author,
        )
        if tx is not None:
            proposals_emitted += 1
            jsonl_log.append(tx)
            summary = {
                "hlc": tx.hlc.to_str(),
                "kind": tx.kind.value,
                "symbol_path": change.symbol_path,
                "file": change.file,
            }
            _maybe_tag_plan_aligned(summary, store, pending_plan_slugs)
            proposal_summaries.append(summary)

    # --- Dedup ---
    if proposals_emitted > 0:
        from codoc.pipelines.reflective.dedup import dedup_proposals

        all_emitted_txs = [
            tx
            for tx in store.list_transactions(proposal=True, limit=0)
            if any(s["hlc"] == tx.hlc.to_str() for s in proposal_summaries)
        ]
        deduped = dedup_proposals(all_emitted_txs, store)
        proposals_emitted = len(deduped)

    total_current_chunks = sum(len(c) for c in diff.chunks_by_file.values())
    changed_chunk_count = len(diff.changes)
    skipped_unchanged = total_current_chunks - sum(
        1 for ch in diff.changes if ch.change_kind in ("added", "modified")
    )

    return {
        "changed_chunks": changed_chunk_count,
        "skipped_unchanged": max(skipped_unchanged, 0),
        "evicted_directly": evicted_directly,
        "escalated_to_llm": escalated_to_llm_count,
        "proposals_emitted": proposals_emitted,
        "proposals": proposal_summaries,
    }


def _render_tree_if_needed(codoc_dir: str, proposals_emitted: int) -> None:
    if proposals_emitted <= 0:
        return
    from codoc.pipelines.intentional.runner import open_stores
    from codoc.projection.tree_codoc import write_tree

    store, _, tx_log = open_stores(codoc_dir)
    try:
        write_tree(codoc_dir, store, tx_log)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_reflect(
    root_dir: str,
    codoc_dir: str,
    from_ref: str = "HEAD~1",     # accepted for back-compat; ignored
    to_ref: str = "HEAD",          # accepted for back-compat; ignored
    repo_name: str = "codebase",
    node_id: str = "default",
) -> dict:
    """Run the reflective pipeline against the current working tree.

    The reflective pipeline snapshots the cocoindex-managed chunk index
    before and after an incremental update, then reconciles the diff against
    existing bindings.

    ``from_ref`` and ``to_ref`` are accepted for backward compatibility with
    callers (the git post-commit hook, older CLI invocations) but are no
    longer used — the snapshot diff supplies the changeset.

    Returns
    -------
    dict
        ``{changed_files, changed_chunks, skipped_unchanged, evicted_directly,
        escalated_to_llm, proposals_emitted, proposals}``.
    """
    codoc_path = Path(codoc_dir)
    db_path = str(codoc_path / "codoc.db")
    log_path = str(codoc_path / "log.jsonl")

    with SQLiteStore(db_path) as store:
        jsonl_log = JSONLLog(log_path)
        tx_log = TransactionLog(store, node_id=node_id)

        diff = _build_diff(root_dir, codoc_dir, store)
        result = _orchestrate(
            diff,
            root_dir=root_dir,
            repo_name=repo_name,
            store=store,
            tx_log=tx_log,
            jsonl_log=jsonl_log,
        )

    _render_tree_if_needed(codoc_dir, result["proposals_emitted"])

    result["changed_files"] = len({
        ch.file for ch in diff.changes
    })
    return result


def run_reflect_files(
    root_dir: str,
    codoc_dir: str,
    file_paths: list[str],
    node_id: str = "default",
    session_id: str | None = None,
    author: str = "reflective",
) -> dict:
    """Incremental reflect scoped to specific files (on-save / API hook).

    Used by Flow 2 (bottom-up real-time reflection). Internally delegates to
    the same orchestrator as :func:`run_reflect`, with the diff restricted to
    the provided files.
    """
    codoc_path = Path(codoc_dir)
    db_path = str(codoc_path / "codoc.db")
    log_path = str(codoc_path / "log.jsonl")

    with SQLiteStore(db_path) as store:
        jsonl_log = JSONLLog(log_path)
        tx_log = TransactionLog(store, node_id=node_id)

        diff = _build_diff(root_dir, codoc_dir, store, file_scope=file_paths)
        result = _orchestrate(
            diff,
            root_dir=root_dir,
            repo_name="codebase",
            store=store,
            tx_log=tx_log,
            jsonl_log=jsonl_log,
            author=author,
        )

    _render_tree_if_needed(codoc_dir, result["proposals_emitted"])

    result["processed_files"] = len(file_paths)
    return result
