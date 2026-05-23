"""Canonical accept-transaction applier.

Single source of truth for the side-effects of flipping a proposal to accepted.
Previously duplicated in ``codoc/cli/tx.py:_apply_accepted_transaction`` and
``codoc/api/routes.py:_apply_accepted_transaction`` — both now delegate here.

Phase 5 will extend ``apply_accepted_transaction`` with code-level patches for
RENAME_INFER (gated by ``payload["rename_symbol"] is True``) and optional
prune-code for RETIRE_REFLECTIVE (gated by ``prune_code=True``).
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codoc.storage.sqlite_store import SQLiteStore
    from codoc.model.transaction import Transaction


def _new_uuid() -> str:
    try:
        import uuid_utils  # type: ignore[import]
        return str(uuid_utils.uuid7())
    except ImportError:
        return str(_uuid.uuid4())


def _resolve_provisional_uuid(provisional_uuid: str, store: "SQLiteStore") -> str | None:
    """Resolve a provisional parent UUID to a confirmed feature UUID.

    Bootstrap INTRODUCE proposals carry ``provisional_uuid`` in their payload.
    When a sibling's parent_uuid references a provisional UUID, look it up
    in the features table to get the real UUID after acceptance.
    """
    feature = store.get_feature(provisional_uuid)
    return feature.uuid if feature is not None else None


@dataclass
class CodePatchSummary:
    """Describes any source-file edits that were applied alongside the tx accept."""
    files_changed: list[str] = field(default_factory=list)
    lines_changed: int = 0
    patch_path: str = ""   # path to .codoc/patches/<hlc>/ if written


def apply_accepted_transaction(
    tx: "Transaction",
    store: "SQLiteStore",
    root_dir: Path | str | None = None,
    *,
    prune_code: bool = False,
    dry_run: bool = False,
) -> CodePatchSummary:
    """Apply the side-effects of an accepted transaction to the store.

    Parameters
    ----------
    tx:
        The accepted transaction.  ``tx.proposal`` should be False at the
        call-site (i.e. already flipped by ``tx_log.accept_proposal``), but
        the applier does not enforce this — it just reads ``tx.kind``.
    store:
        Open SQLiteStore instance.  The caller is responsible for opening/closing.
    root_dir:
        Absolute root of the codebase.  Required for code-level patches (Phase 5).
        If None, code-patch logic is silently skipped.
    prune_code:
        If True and kind == RETIRE_REFLECTIVE, delete the source lines bound to
        the retired feature.  Default False (safe).
    dry_run:
        If True, compute what would change but do not write anything.
    """
    from codoc.model.transaction import TransactionKind
    from codoc.model.feature import Feature
    from codoc.model.binding import Binding
    from codoc.model.anchor import Anchor

    summary = CodePatchSummary()
    kind = tx.kind
    payload = tx.payload
    hlc = tx.hlc

    if kind == TransactionKind.INTRODUCE:
        slug = payload.get("slug", "feature")
        title = payload.get("title", "") or slug
        intent = payload.get("intent", "")
        description = payload.get("description", "")
        purpose = payload.get("purpose", "")
        rationale = payload.get("rationale", "")
        scenario = payload.get("scenario", "")
        status = payload.get("status", "realized")
        needs_slugs: list[str] = payload.get("needs", [])
        parent_uuid = payload.get("parent_uuid")

        # Resolve provisional parent: bootstrap siblings reference each other
        # by provisional_uuid before they're confirmed in the features table.
        if parent_uuid and "-" in parent_uuid:
            resolved = _resolve_provisional_uuid(parent_uuid, store)
            if resolved:
                parent_uuid = resolved

        feature_uuid = payload.get("provisional_uuid") or payload.get("feature_uuid") or _new_uuid()

        if not dry_run:
            feature = Feature(
                uuid=feature_uuid,
                slug=slug,
                title=title,
                parent_uuid=parent_uuid,
                intent=intent,
                description=description,
                purpose=purpose,
                rationale=rationale,
                scenario=scenario,
                status=status,
                retired=False,
                created_at_hlc=hlc,
                updated_at_hlc=hlc,
            )
            store.upsert_feature(feature)

            # Index citations so rename/retire tracking can maintain them.
            try:
                from codoc.core.citations import populate_citations
                populate_citations(feature_uuid, feature, store)
            except Exception:
                pass

            candidate_bindings = payload.get("candidate_bindings") or []
            if not candidate_bindings:
                # Reflective INTRODUCE proposals (single chunk created a new
                # feature) carry symbol_path/file/current_fingerprint at the
                # top level instead of a candidate_bindings list.  Synthesize
                # one so the feature isn't born as a stub.
                sp = payload.get("symbol_path")
                file = payload.get("file")
                fp = payload.get("current_fingerprint") or payload.get("fingerprint", "")
                if sp and file:
                    candidate_bindings = [{
                        "anchor": {"file": file, "symbol_path": sp},
                        "fingerprint": fp,
                    }]

            for cb in candidate_bindings:
                anchor_data = cb.get("anchor", {})
                try:
                    anchor = Anchor.model_validate(anchor_data)
                except Exception:
                    continue
                binding = Binding(
                    uuid=_new_uuid(),
                    feature_uuid=feature_uuid,
                    anchor=anchor,
                    fingerprint=cb.get("fingerprint", ""),
                    fingerprint_at_hlc=hlc,
                    parent_symbol=cb.get("parent_symbol"),
                )
                store.upsert_binding(binding)

            # Create feature edges for 'needs' dependencies
            for needs_slug in needs_slugs:
                matches = store.find_features_by_slug(needs_slug)
                if matches:
                    store.upsert_feature_edge(feature_uuid, matches[0].uuid, "needs")

    elif kind == TransactionKind.ABSORB:
        anchor_data = payload.get("anchor") or {
            "file": payload.get("file", ""),
            "symbol_path": payload.get("symbol_path"),
        }
        try:
            anchor = Anchor.model_validate(anchor_data)
        except Exception:
            return summary  # skip malformed anchors

        if not dry_run:
            # ``target_feature_uuid`` from LLM path; ``feature_uuid`` from
            # heuristic path.  Accept either.
            feature_uuid = (
                payload.get("feature_uuid")
                or payload.get("target_feature_uuid")
                or ""
            )
            if not feature_uuid:
                return summary
            new_fingerprint = (
                payload.get("current_fingerprint")
                or payload.get("fingerprint", "")
            )
            existing_binding_uuid = payload.get("binding_uuid")
            existing = (
                store.get_binding(existing_binding_uuid)
                if existing_binding_uuid else None
            )
            if existing is not None and existing.feature_uuid == feature_uuid:
                # ABSORB on a modified chunk that already belongs to this
                # feature: refresh the fingerprint in place, don't duplicate.
                refreshed = existing.model_copy(update={
                    "fingerprint": new_fingerprint or existing.fingerprint,
                    "fingerprint_at_hlc": hlc,
                })
                store.upsert_binding(refreshed)
            else:
                binding = Binding(
                    uuid=_new_uuid(),
                    feature_uuid=feature_uuid,
                    anchor=anchor,
                    fingerprint=new_fingerprint,
                    fingerprint_at_hlc=hlc,
                    parent_symbol=payload.get("parent_symbol"),
                )
                store.upsert_binding(binding)

            # Many-to-many bindings: a chunk can belong to multiple features.
            # When one ABSORB confirms the chunk's identity (new fingerprint),
            # the *fingerprint* is a property of the chunk, not the attribution.
            # Refresh every other binding pointing at the same (file, symbol_path)
            # so sibling features don't linger as Strained on a now-confirmed chunk.
            if new_fingerprint and anchor.symbol_path:
                for b in store.get_all_bindings():
                    if b.anchor.file != anchor.file:
                        continue
                    if b.anchor.symbol_path != anchor.symbol_path:
                        continue
                    if b.fingerprint == new_fingerprint:
                        continue
                    store.upsert_binding(b.model_copy(update={
                        "fingerprint": new_fingerprint,
                        "fingerprint_at_hlc": hlc,
                    }))

    elif kind == TransactionKind.EVICT:
        binding_uuid = payload.get("binding_uuid")
        if not dry_run:
            if binding_uuid:
                store.delete_binding(binding_uuid)
            else:
                # Fallback: locate binding by symbol_path + feature_uuid.
                symbol_path = payload.get("symbol_path")
                feature_uuid = payload.get("feature_uuid")
                if symbol_path and feature_uuid:
                    for b in store.list_bindings(feature_uuid):
                        if b.anchor.symbol_path == symbol_path:
                            store.delete_binding(b.uuid)
                            break

    elif kind == TransactionKind.RETIRE_REFLECTIVE:
        feature_uuid = payload.get("feature_uuid") or payload.get("affected_feature_uuid")
        if feature_uuid and not dry_run:
            feature = store.get_feature(feature_uuid)
            if feature is not None and not feature.retired:
                store.upsert_feature(feature.model_copy(update={"retired": True, "updated_at_hlc": hlc}))

        # Phase 5: prune_code path (currently a no-op placeholder).
        # if prune_code and root_dir and feature_uuid and not dry_run:
        #     summary = _prune_retired_bindings(feature_uuid, store, root_dir, dry_run)

    elif kind == TransactionKind.RENAME_INFER:
        feature_uuid = payload.get("feature_uuid") or payload.get("affected_feature_uuid")
        new_slug = payload.get("new_slug") or payload.get("slug")
        if feature_uuid and new_slug and not dry_run:
            feature = store.get_feature(feature_uuid)
            if feature is not None:
                store.upsert_feature(feature.model_copy(update={"slug": new_slug, "updated_at_hlc": hlc}))

        # Phase 5: symbol rename in source files when payload["rename_symbol"] is True.
        # if payload.get("rename_symbol") and root_dir and not dry_run:
        #     summary = _rename_symbol_in_source(tx, store, root_dir, dry_run)

    elif kind == TransactionKind.REATTRIBUTE:
        binding_uuid = payload.get("binding_uuid")
        new_feature_uuid = payload.get("new_feature_uuid")
        new_fingerprint = payload.get("new_fingerprint")
        if binding_uuid and new_feature_uuid and not dry_run:
            binding = store.get_binding(binding_uuid)
            if binding is not None:
                updates: dict = {"feature_uuid": new_feature_uuid}
                if new_fingerprint:
                    updates["fingerprint"] = new_fingerprint
                    updates["fingerprint_at_hlc"] = hlc
                store.upsert_binding(binding.model_copy(update=updates))

    elif kind == TransactionKind.MOVED:
        # Chunk moved/renamed: preserve binding UUID, update anchor, update fingerprint.
        # Payload: {binding_uuid, new_file, new_symbol_path, new_fingerprint, old_file, old_symbol_path}
        binding_uuid = payload.get("binding_uuid")
        new_file = payload.get("new_file")
        new_symbol_path = payload.get("new_symbol_path")
        new_fingerprint = payload.get("new_fingerprint")
        if binding_uuid and (new_file or new_symbol_path) and not dry_run:
            binding = store.get_binding(binding_uuid)
            if binding is not None:
                from codoc.model.anchor import Anchor
                updated_anchor = binding.anchor.model_copy(update={
                    k: v for k, v in {
                        "file": new_file,
                        "symbol_path": new_symbol_path,
                    }.items() if v is not None
                })
                updates = {"anchor": updated_anchor}
                if new_fingerprint:
                    # Accepting a MOVED proposal updates the stored fingerprint —
                    # this closes the drift gap for moved chunks.
                    updates["fingerprint"] = new_fingerprint
                    updates["fingerprint_at_hlc"] = hlc
                store.upsert_binding(binding.model_copy(update=updates))

                # Update citations whose target referenced the old symbol path.
                old_file = payload.get("old_file") or binding.anchor.file
                old_sp = payload.get("old_symbol_path") or binding.anchor.symbol_path or ""
                if old_sp and new_symbol_path:
                    old_code_path = f"{old_file}::{old_sp}" if "::" not in old_sp else old_sp
                    new_code_path = f"{new_file or old_file}::{new_symbol_path}" if "::" not in new_symbol_path else new_symbol_path
                    try:
                        store.update_citation_target(old_code_path, new_code_path, "code")
                    except Exception:
                        pass

    elif kind == TransactionKind.FRACTURE:
        # One binding split into N new bindings under the same feature.
        # Payload: {source_binding_uuid, feature_uuid, new_chunks: [{file, symbol_path, fingerprint}]}
        source_binding_uuid = payload.get("source_binding_uuid")
        feature_uuid = payload.get("feature_uuid")
        new_chunks = payload.get("new_chunks", [])

        if source_binding_uuid and feature_uuid and new_chunks and not dry_run:
            store.delete_binding(source_binding_uuid)
            for chunk_info in new_chunks:
                anchor = Anchor(
                    file=chunk_info.get("file", ""),
                    symbol_path=chunk_info.get("symbol_path"),
                )
                store.upsert_binding(Binding(
                    uuid=_new_uuid(),
                    feature_uuid=feature_uuid,
                    anchor=anchor,
                    fingerprint=chunk_info.get("fingerprint", ""),
                    fingerprint_at_hlc=hlc,
                ))

    elif kind == TransactionKind.COALESCE:
        # N bindings merged into one under the same feature.
        # Payload: {source_binding_uuids, survivor_uuid, feature_uuid, new_chunk: {file, symbol_path, fingerprint}}
        source_uuids: list[str] = payload.get("source_binding_uuids", [])
        survivor_uuid = payload.get("survivor_uuid")
        feature_uuid = payload.get("feature_uuid")
        new_chunk = payload.get("new_chunk", {})

        if source_uuids and feature_uuid and new_chunk and not dry_run:
            new_anchor = Anchor(
                file=new_chunk.get("file", ""),
                symbol_path=new_chunk.get("symbol_path"),
            )
            new_fp = new_chunk.get("fingerprint", "")

            for uuid in source_uuids:
                if uuid != survivor_uuid:
                    store.delete_binding(uuid)

            if survivor_uuid:
                binding = store.get_binding(survivor_uuid)
                if binding is not None:
                    store.upsert_binding(binding.model_copy(update={
                        "anchor": new_anchor,
                        "fingerprint": new_fp,
                        "fingerprint_at_hlc": hlc,
                    }))
            else:
                store.upsert_binding(Binding(
                    uuid=_new_uuid(),
                    feature_uuid=feature_uuid,
                    anchor=new_anchor,
                    fingerprint=new_fp,
                    fingerprint_at_hlc=hlc,
                ))

    elif kind == TransactionKind.RETIRE_FILE:
        # Whole file deleted; evict all bindings and retire orphaned features.
        # Payload: {file, affected_feature_uuids, affected_binding_uuids}
        binding_uuids: list[str] = payload.get("affected_binding_uuids", [])
        feature_uuids: list[str] = payload.get("affected_feature_uuids", [])
        retired_file = payload.get("file", "")

        if not dry_run:
            for uuid in binding_uuids:
                store.delete_binding(uuid)

            for feature_uuid in feature_uuids:
                remaining = store.list_bindings(feature_uuid)
                if not remaining:
                    feature = store.get_feature(feature_uuid)
                    if feature is not None and not feature.retired:
                        store.upsert_feature(feature.model_copy(update={
                            "retired": True,
                            "updated_at_hlc": hlc,
                        }))

            # Mark all citations referencing this file as stale.
            if retired_file:
                try:
                    store.retire_citations_for_file(retired_file)
                except Exception:
                    pass

    elif kind == TransactionKind.RENAME_FILE:
        # File moved/renamed; remap all binding anchors to the new path.
        # Payload: {old_file, new_file, affected_binding_uuids, similarity}
        old_file = payload.get("old_file", "")
        new_file = payload.get("new_file", "")
        binding_uuids_to_remap: list[str] = payload.get("affected_binding_uuids", [])

        if old_file and new_file and not dry_run:
            for uuid in binding_uuids_to_remap:
                binding = store.get_binding(uuid)
                if binding is not None and binding.anchor.file == old_file:
                    new_anchor = binding.anchor.model_copy(update={"file": new_file})
                    store.upsert_binding(binding.model_copy(update={
                        "anchor": new_anchor,
                        "fingerprint_at_hlc": hlc,
                    }))

            # Rewrite all citations pointing at old_file → new_file.
            try:
                store.rename_citations_for_file(old_file, new_file)
            except Exception:
                pass

    elif kind in (
        TransactionKind.AMEND,
        TransactionKind.RENAME,
        TransactionKind.RETIRE,
        TransactionKind.SNAPSHOT,
    ):
        # These are either already applied by their intentional runners or
        # are administrative records (SNAPSHOT) with no store side-effects.
        pass

    # SPLIT_FILE, MERGE_FILE, RESTRUCTURE, REWIND, BRANCH, MERGE_BRANCH,
    # INSTATE_CONSTRAINT, LIFT_CONSTRAINT — reserved; no-op.

    return summary
