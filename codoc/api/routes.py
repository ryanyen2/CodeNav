"""codoc API route handlers.

All routes open a short-lived SQLiteStore (WAL mode) per-request.
Writes are atomic via SQLite's built-in transaction management.
"""

from __future__ import annotations

import uuid as _uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from codoc.storage.sqlite_store import SQLiteStore
from codoc.storage.jsonl_log import JSONLLog
from codoc.core.log import TransactionLog
from codoc.core.state_derivation import compute_feature_state, BindingResolution
from codoc.pipelines.bootstrap.runner import run_bootstrap, finish_bootstrap
from codoc.pipelines.reflective.runner import run_reflect
from codoc.pipelines.intentional.runner import open_stores
from codoc.pipelines.intentional.amend import amend_feature
from codoc.pipelines.intentional.merge import merge_features
from codoc.pipelines.intentional.rename import rename_feature
from codoc.pipelines.intentional.restructure import restructure_feature
from codoc.pipelines.intentional.retire import retire_feature
from codoc.pipelines.intentional.rewind import rewind_feature
from codoc.pipelines.intentional.split import split_feature
from codoc.model.feature import Feature
from codoc.model.binding import Binding
from codoc.model.anchor import Anchor
from codoc.model.hlc import HLC
from codoc.model.transaction import Transaction, TransactionKind

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class BootstrapResponse(BaseModel):
    chunk_count: int
    cluster_count: int
    proposal_count: int
    proposals: list[dict]


class ReflectResponse(BaseModel):
    changed_files: int
    changed_chunks: int
    proposals_emitted: int
    proposals: list[dict]


class FeatureResponse(BaseModel):
    uuid: str
    slug: str
    parent_uuid: str | None
    intent: str
    retired: bool
    state: str
    binding_count: int


class TransactionResponse(BaseModel):
    hlc: str
    kind: str
    payload: dict
    author: str
    proposal: bool
    accepted_at: str | None
    label: str | None


# ---------------------------------------------------------------------------
# Request body models
# ---------------------------------------------------------------------------


class BootstrapRequest(BaseModel):
    root_dir: str
    repo_name: str = "codebase"
    target_cluster_size: int = 8
    hierarchical: bool = False


class BootstrapFinishRequest(BaseModel):
    root_dir: str


class ReflectRequest(BaseModel):
    root_dir: str
    from_ref: str = "HEAD~1"
    to_ref: str = "HEAD"
    repo_name: str = "codebase"


class ReflectFileRequest(BaseModel):
    root_dir: str
    paths: list[str]


class PlanRequest(BaseModel):
    root_dir: str
    prompt: str
    repo_name: str = "codebase"


class AcceptRequest(BaseModel):
    root_dir: str
    edits: dict | None = None


class RejectRequest(BaseModel):
    root_dir: str


class BulkAcceptRequest(BaseModel):
    root_dir: str
    label: str | None = None
    edits: dict | None = None


class BulkRejectRequest(BaseModel):
    root_dir: str


class LabelRequest(BaseModel):
    root_dir: str
    label: str


class AmendRequest(BaseModel):
    root_dir: str
    feature_uuid: str
    new_intent: str
    author: str = "user"


class RenameRequest(BaseModel):
    root_dir: str
    feature_uuid: str
    new_slug: str
    author: str = "user"


class RetireRequest(BaseModel):
    root_dir: str
    feature_uuid: str
    author: str = "user"


class SplitRequest(BaseModel):
    root_dir: str
    feature_uuid: str
    child_a_slug: str
    child_a_intent: str
    child_a_binding_uuids: list[str]
    child_b_slug: str
    child_b_intent: str
    child_b_binding_uuids: list[str]
    author: str = "user"


class MergeRequest(BaseModel):
    root_dir: str
    source_uuids: list[str]
    target_slug: str
    target_intent: str
    author: str = "user"


class RestructureRequest(BaseModel):
    root_dir: str
    feature_uuid: str
    new_parent_uuid: str | None = None
    author: str = "user"


class RewindRequest(BaseModel):
    root_dir: str
    feature_uuid: str
    target_hlc: str
    author: str = "user"


class StructuralResponse(BaseModel):
    transaction: TransactionResponse
    obligation_uuids: list[str]


class AnchorResolveRequest(BaseModel):
    root_dir: str
    file: str  # repo-relative path
    symbol_path: str | None = None
    ts_query: str | None = None
    occurrence_index: int = 0


class AnchorPositionResponse(BaseModel):
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _codoc_dir(root_dir: str) -> str:
    return str(Path(root_dir) / ".codoc")


def _open_store(codoc_dir: str) -> SQLiteStore:
    store = SQLiteStore(str(Path(codoc_dir) / "codoc.db"))
    store.open()
    return store


def _feature_to_response(feature: Feature, store: SQLiteStore) -> FeatureResponse:
    bindings = store.list_bindings(feature.uuid)
    # Compute state with empty resolutions — conservative, treats unknowns as
    # unresolved (SEVERED if zero bindings resolve, DRAFTING otherwise).
    resolutions: list[BindingResolution] = []
    obligations = store.list_obligations(feature_uuid=feature.uuid, status="pending")
    state = compute_feature_state(feature, bindings, resolutions, obligations)
    return FeatureResponse(
        uuid=feature.uuid,
        slug=feature.slug,
        parent_uuid=feature.parent_uuid,
        intent=feature.intent,
        retired=feature.retired,
        state=state.value,
        binding_count=len(bindings),
    )


def _tx_to_response(tx: Transaction) -> TransactionResponse:
    return TransactionResponse(
        hlc=tx.hlc.to_str(),
        kind=tx.kind.value,
        payload=tx.payload,
        author=tx.author,
        proposal=tx.proposal,
        accepted_at=tx.accepted_at.isoformat() if tx.accepted_at is not None else None,
        label=tx.label,
    )


def _apply_accepted_transaction(
    tx: Transaction,
    store: SQLiteStore,
    jsonl_log: JSONLLog,
) -> None:
    """Apply the side-effects of an accepted transaction to the feature store."""
    kind = tx.kind
    payload = tx.payload

    if kind == TransactionKind.INTRODUCE:
        # Create the Feature record.
        hlc = tx.hlc
        feature = Feature(
            uuid=payload.get("provisional_uuid") or str(_uuid.uuid4()),
            slug=payload.get("slug", "unnamed"),
            title=payload.get("title", ""),
            parent_uuid=payload.get("parent_uuid"),
            intent=payload.get("intent", ""),
            retired=False,
            created_at_hlc=hlc,
            updated_at_hlc=hlc,
        )
        store.upsert_feature(feature)

        # Create Binding records for each candidate_binding in the payload.
        for cb in payload.get("candidate_bindings", []):
            anchor_data = cb.get("anchor", {})
            try:
                anchor = Anchor.model_validate(anchor_data)
            except Exception:
                # Skip malformed anchors rather than aborting the whole accept.
                continue
            binding = Binding(
                uuid=str(_uuid.uuid4()),
                feature_uuid=feature.uuid,
                anchor=anchor,
                fingerprint=cb.get("fingerprint", ""),
                fingerprint_at_hlc=hlc,
                parent_symbol=cb.get("parent_symbol"),
            )
            store.upsert_binding(binding)

    elif kind == TransactionKind.ABSORB:
        # Create a single new Binding.
        hlc = tx.hlc
        anchor_data = payload.get("anchor") or {
            "file": payload.get("file", ""),
            "symbol_path": payload.get("symbol_path"),
        }
        try:
            anchor = Anchor.model_validate(anchor_data)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid anchor in ABSORB payload: {exc}") from exc

        binding = Binding(
            uuid=str(_uuid.uuid4()),
            feature_uuid=payload["feature_uuid"],
            anchor=anchor,
            fingerprint=payload.get("current_fingerprint") or payload.get("fingerprint", ""),
            fingerprint_at_hlc=hlc,
            parent_symbol=payload.get("parent_symbol"),
        )
        store.upsert_binding(binding)

    elif kind == TransactionKind.EVICT:
        # Delete the binding identified by uuid in the payload.
        binding_uuid = payload.get("binding_uuid")
        if binding_uuid:
            store.delete_binding(binding_uuid)
        # If no explicit binding_uuid, attempt lookup by symbol_path.
        else:
            symbol_path = payload.get("symbol_path")
            feature_uuid = payload.get("feature_uuid")
            if symbol_path and feature_uuid:
                bindings = store.list_bindings(feature_uuid)
                for b in bindings:
                    if b.anchor.symbol_path == symbol_path:
                        store.delete_binding(b.uuid)
                        break

    elif kind == TransactionKind.RETIRE_REFLECTIVE:
        feature_uuid = payload.get("feature_uuid")
        if feature_uuid:
            feature = store.get_feature(feature_uuid)
            if feature is not None:
                updated = feature.model_copy(
                    update={"retired": True, "updated_at_hlc": tx.hlc}
                )
                store.upsert_feature(updated)

    elif kind == TransactionKind.RENAME_INFER:
        feature_uuid = payload.get("feature_uuid")
        new_slug = payload.get("new_slug")
        if feature_uuid and new_slug:
            feature = store.get_feature(feature_uuid)
            if feature is not None:
                updated = feature.model_copy(
                    update={"slug": new_slug, "updated_at_hlc": tx.hlc}
                )
                store.upsert_feature(updated)

    elif kind == TransactionKind.REATTRIBUTE:
        # Move binding(s) from one feature to another.
        binding_uuid = payload.get("binding_uuid")
        new_feature_uuid = payload.get("new_feature_uuid")
        if binding_uuid and new_feature_uuid:
            binding = store.get_binding(binding_uuid)
            if binding is not None:
                updated = binding.model_copy(
                    update={"feature_uuid": new_feature_uuid}
                )
                store.upsert_binding(updated)

    elif kind in (TransactionKind.AMEND, TransactionKind.RENAME, TransactionKind.RETIRE):
        # Intentional kinds committed directly — no separate apply step needed;
        # their handlers already mutated the store.  Guard here for completeness.
        pass

    else:
        # All other unhandled kinds: no store mutation required at this stage.
        pass


# ---------------------------------------------------------------------------
# Bootstrap routes
# ---------------------------------------------------------------------------


@router.post("/bootstrap", response_model=BootstrapResponse)
async def bootstrap(body: BootstrapRequest) -> BootstrapResponse:
    """Run the full bootstrap pipeline on root_dir.

    Returns 422 if bootstrap has already been completed (unattributed.json exists).
    """
    codoc_path = Path(body.root_dir) / ".codoc"
    unattributed_path = codoc_path / "unattributed.json"
    if unattributed_path.exists():
        raise HTTPException(
            status_code=422,
            detail=(
                "Bootstrap already completed (unattributed.json exists). "
                "Call POST /bootstrap/finish or use the reflective pipeline."
            ),
        )

    try:
        result = run_bootstrap(
            root_dir=body.root_dir,
            codoc_dir=str(codoc_path),
            repo_name=body.repo_name,
            target_cluster_size=body.target_cluster_size,
            hierarchical=body.hierarchical,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return BootstrapResponse(**result)


@router.post("/bootstrap/finish")
async def bootstrap_finish(body: BootstrapFinishRequest) -> dict:
    """Mark bootstrap as complete; sweep unattributed chunks to unattributed.json."""
    codoc_dir = _codoc_dir(body.root_dir)
    try:
        result = finish_bootstrap(codoc_dir=codoc_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result


# ---------------------------------------------------------------------------
# Reflect route
# ---------------------------------------------------------------------------


@router.post("/reflect", response_model=ReflectResponse)
async def reflect(body: ReflectRequest) -> ReflectResponse:
    """Run the reflective pipeline for the given git ref range."""
    codoc_dir = _codoc_dir(body.root_dir)
    try:
        result = run_reflect(
            root_dir=body.root_dir,
            codoc_dir=codoc_dir,
            from_ref=body.from_ref,
            to_ref=body.to_ref,
            repo_name=body.repo_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ReflectResponse(
        changed_files=result["changed_files"],
        changed_chunks=result["changed_chunks"],
        proposals_emitted=result["proposals_emitted"],
        proposals=result["proposals"],
    )


@router.post("/reflect/file", response_model=ReflectResponse)
async def reflect_file(body: ReflectFileRequest) -> ReflectResponse:
    """Run incremental reflect on specific files (no git refs required)."""
    codoc_dir = _codoc_dir(body.root_dir)
    try:
        from codoc.pipelines.reflective.runner import run_reflect_files
        result = run_reflect_files(
            root_dir=body.root_dir,
            codoc_dir=codoc_dir,
            file_paths=body.paths,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ReflectResponse(
        changed_files=result.get("processed_files", 0),
        changed_chunks=result.get("changed_chunks", 0),
        proposals_emitted=result.get("proposals_emitted", 0),
        proposals=result.get("proposals", []),
    )


@router.post("/plan")
async def run_plan_endpoint(body: PlanRequest) -> dict:
    """Run the planning agent and emit proposals."""
    codoc_dir = _codoc_dir(body.root_dir)
    try:
        from codoc.pipelines.planning.runner import run_plan
        result = run_plan(
            prompt=body.prompt,
            root_dir=body.root_dir,
            codoc_dir=codoc_dir,
            repo_name=body.repo_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result


# ---------------------------------------------------------------------------
# Transaction routes
# ---------------------------------------------------------------------------


@router.get("/tx/pending", response_model=list[TransactionResponse])
async def list_pending(root_dir: str = Query(...)) -> list[TransactionResponse]:
    """Return all pending (proposal=True) transactions sorted by HLC ascending."""
    codoc_dir = _codoc_dir(root_dir)
    store = _open_store(codoc_dir)
    try:
        txs = store.list_transactions(proposal=True, limit=0)
    finally:
        store.close()

    return [_tx_to_response(tx) for tx in txs]


@router.post("/tx/accept-all")
async def accept_all_transactions(body: BulkAcceptRequest) -> dict:
    """Accept all pending proposals, optionally applying a label."""
    from codoc.storage.jsonl_log import JSONLLog
    from codoc.core.log import TransactionLog

    codoc_dir = _codoc_dir(body.root_dir)
    jsonl_path = str(Path(codoc_dir) / "log.jsonl")
    store = _open_store(codoc_dir)
    accepted_count = 0
    failed = []
    try:
        txs = store.list_transactions(proposal=True, limit=0)
        tx_log = TransactionLog(store)
        jsonl_log = JSONLLog(jsonl_path)
        for tx in txs:
            try:
                accepted_tx = tx_log.accept_proposal(tx.hlc.to_str(), edits=body.edits)
                from codoc.core.apply import apply_accepted_transaction
                apply_accepted_transaction(accepted_tx, store)
                jsonl_log.append(accepted_tx)
                if body.label and body.label in _VALID_LABELS:
                    store.set_label(accepted_tx.hlc.to_str(), body.label)
                accepted_count += 1
            except Exception as exc:
                failed.append({"hlc": tx.hlc.to_str(), "error": str(exc)})
    finally:
        store.close()

    return {"accepted": accepted_count, "failed": failed}


@router.post("/tx/reject-all")
async def reject_all_transactions(body: BulkRejectRequest) -> dict:
    """Reject (hard-delete) all pending proposals."""
    codoc_dir = _codoc_dir(body.root_dir)
    store = _open_store(codoc_dir)
    rejected_count = 0
    failed = []
    try:
        txs = store.list_transactions(proposal=True, limit=0)
        tx_log = TransactionLog(store)
        for tx in txs:
            try:
                tx_log.reject_proposal(tx.hlc.to_str())
                rejected_count += 1
            except Exception as exc:
                failed.append({"hlc": tx.hlc.to_str(), "error": str(exc)})
    finally:
        store.close()

    return {"rejected": rejected_count, "failed": failed}


@router.post("/tx/{hlc_str}/accept", response_model=TransactionResponse)
async def accept_transaction(hlc_str: str, body: AcceptRequest) -> TransactionResponse:
    """Accept a pending proposal, optionally patching its payload with edits."""
    codoc_dir = _codoc_dir(body.root_dir)
    jsonl_path = str(Path(codoc_dir) / "log.jsonl")

    store = _open_store(codoc_dir)
    try:
        tx = store.get_transaction(hlc_str)
        if tx is None:
            raise HTTPException(status_code=404, detail=f"Transaction {hlc_str!r} not found")
        if not tx.proposal:
            raise HTTPException(status_code=400, detail=f"Transaction {hlc_str!r} is not a pending proposal")

        tx_log = TransactionLog(store)
        try:
            accepted_tx = tx_log.accept_proposal(hlc_str, edits=body.edits)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        jsonl_log = JSONLLog(jsonl_path)
        from codoc.core.apply import apply_accepted_transaction
        apply_accepted_transaction(accepted_tx, store)
        jsonl_log.append(accepted_tx)

    finally:
        store.close()

    return _tx_to_response(accepted_tx)


@router.post("/tx/{hlc_str}/reject")
async def reject_transaction(hlc_str: str, body: RejectRequest) -> dict:
    """Reject (hard-delete) a pending proposal."""
    codoc_dir = _codoc_dir(body.root_dir)
    store = _open_store(codoc_dir)
    try:
        tx = store.get_transaction(hlc_str)
        if tx is None:
            raise HTTPException(status_code=404, detail=f"Transaction {hlc_str!r} not found")
        if not tx.proposal:
            raise HTTPException(status_code=400, detail=f"Transaction {hlc_str!r} is accepted and cannot be rejected")

        tx_log = TransactionLog(store)
        try:
            tx_log.reject_proposal(hlc_str)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        store.close()

    return {"status": "rejected"}


_VALID_LABELS = {"accept-verbatim", "accept-light-edit", "accept-heavy-edit", "reject"}


@router.post("/tx/{hlc_str}/label", response_model=TransactionResponse)
async def label_transaction(hlc_str: str, body: LabelRequest) -> TransactionResponse:
    """Attach a validation-gate label to a transaction."""
    if body.label not in _VALID_LABELS:
        raise HTTPException(
            status_code=422,
            detail=f"label must be one of: {', '.join(sorted(_VALID_LABELS))}",
        )

    codoc_dir = _codoc_dir(body.root_dir)
    store = _open_store(codoc_dir)
    try:
        tx = store.get_transaction(hlc_str)
        if tx is None:
            raise HTTPException(status_code=404, detail=f"Transaction {hlc_str!r} not found")

        store.update_transaction(hlc_str, {"label": body.label})
        updated_tx = store.get_transaction(hlc_str)
    finally:
        store.close()

    return _tx_to_response(updated_tx)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Intentional transaction routes
# ---------------------------------------------------------------------------


@router.post("/tx/intentional/amend", response_model=TransactionResponse)
async def intentional_amend(body: AmendRequest) -> TransactionResponse:
    """Edit a feature's intent prose (AMEND — committed immediately, no proposal)."""
    codoc_dir = _codoc_dir(body.root_dir)
    store, jsonl_log, tx_log = open_stores(codoc_dir)
    try:
        try:
            tx = amend_feature(
                feature_uuid=body.feature_uuid,
                new_intent=body.new_intent,
                store=store,
                tx_log=tx_log,
                jsonl_log=jsonl_log,
                author=body.author,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        store.close()

    return _tx_to_response(tx)


@router.post("/tx/intentional/rename", response_model=TransactionResponse)
async def intentional_rename(body: RenameRequest) -> TransactionResponse:
    """Edit a feature's slug (RENAME — committed immediately, no proposal)."""
    codoc_dir = _codoc_dir(body.root_dir)
    store, jsonl_log, tx_log = open_stores(codoc_dir)
    try:
        try:
            tx = rename_feature(
                feature_uuid=body.feature_uuid,
                new_slug=body.new_slug,
                store=store,
                tx_log=tx_log,
                jsonl_log=jsonl_log,
                author=body.author,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        store.close()

    return _tx_to_response(tx)


@router.post("/tx/intentional/retire", response_model=TransactionResponse)
async def intentional_retire(body: RetireRequest) -> TransactionResponse:
    """Mark a feature as retired (RETIRE — committed immediately, no proposal)."""
    codoc_dir = _codoc_dir(body.root_dir)
    store, jsonl_log, tx_log = open_stores(codoc_dir)
    try:
        try:
            tx = retire_feature(
                feature_uuid=body.feature_uuid,
                store=store,
                tx_log=tx_log,
                jsonl_log=jsonl_log,
                author=body.author,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        store.close()

    return _tx_to_response(tx)


@router.post("/tx/intentional/split", response_model=StructuralResponse)
async def intentional_split(body: SplitRequest) -> StructuralResponse:
    """Split a feature into two children (Phase 2)."""
    codoc_dir = _codoc_dir(body.root_dir)
    store, jsonl_log, tx_log = open_stores(codoc_dir)
    try:
        try:
            tx, obligations = split_feature(
                feature_uuid=body.feature_uuid,
                child_a_slug=body.child_a_slug,
                child_a_intent=body.child_a_intent,
                child_a_binding_uuids=body.child_a_binding_uuids,
                child_b_slug=body.child_b_slug,
                child_b_intent=body.child_b_intent,
                child_b_binding_uuids=body.child_b_binding_uuids,
                store=store,
                tx_log=tx_log,
                jsonl_log=jsonl_log,
                author=body.author,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        store.close()

    return StructuralResponse(
        transaction=_tx_to_response(tx),
        obligation_uuids=[o.uuid for o in obligations],
    )


@router.post("/tx/intentional/merge", response_model=StructuralResponse)
async def intentional_merge(body: MergeRequest) -> StructuralResponse:
    """Merge multiple features into one new target (Phase 2)."""
    codoc_dir = _codoc_dir(body.root_dir)
    store, jsonl_log, tx_log = open_stores(codoc_dir)
    try:
        try:
            tx, obligations = merge_features(
                source_uuids=body.source_uuids,
                target_slug=body.target_slug,
                target_intent=body.target_intent,
                store=store,
                tx_log=tx_log,
                jsonl_log=jsonl_log,
                author=body.author,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        store.close()

    return StructuralResponse(
        transaction=_tx_to_response(tx),
        obligation_uuids=[o.uuid for o in obligations],
    )


@router.post("/tx/intentional/restructure", response_model=StructuralResponse)
async def intentional_restructure(body: RestructureRequest) -> StructuralResponse:
    """Move a feature to a new parent (Phase 2)."""
    codoc_dir = _codoc_dir(body.root_dir)
    store, jsonl_log, tx_log = open_stores(codoc_dir)
    try:
        try:
            tx, obligations = restructure_feature(
                feature_uuid=body.feature_uuid,
                new_parent_uuid=body.new_parent_uuid,
                store=store,
                tx_log=tx_log,
                jsonl_log=jsonl_log,
                author=body.author,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        store.close()

    return StructuralResponse(
        transaction=_tx_to_response(tx),
        obligation_uuids=[o.uuid for o in obligations],
    )


@router.post("/tx/intentional/rewind", response_model=StructuralResponse)
async def intentional_rewind(body: RewindRequest) -> StructuralResponse:
    """Rewind a feature's slug/intent to a prior HLC state (Phase 2)."""
    codoc_dir = _codoc_dir(body.root_dir)
    store, jsonl_log, tx_log = open_stores(codoc_dir)
    try:
        try:
            tx, obligations = rewind_feature(
                feature_uuid=body.feature_uuid,
                target_hlc_str=body.target_hlc,
                store=store,
                tx_log=tx_log,
                jsonl_log=jsonl_log,
                author=body.author,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        store.close()

    return StructuralResponse(
        transaction=_tx_to_response(tx),
        obligation_uuids=[o.uuid for o in obligations],
    )


# ---------------------------------------------------------------------------
# Feature query routes
# ---------------------------------------------------------------------------


@router.get("/feature/{uuid}", response_model=FeatureResponse)
async def get_feature(uuid: str, root_dir: str = Query(...)) -> FeatureResponse:
    """Return a feature with its computed state and binding count."""
    codoc_dir = _codoc_dir(root_dir)
    store = _open_store(codoc_dir)
    try:
        feature = store.get_feature(uuid)
        if feature is None:
            raise HTTPException(status_code=404, detail=f"Feature {uuid!r} not found")
        response = _feature_to_response(feature, store)
    finally:
        store.close()

    return response


@router.get("/feature/{uuid}/bindings")
async def get_feature_bindings(uuid: str, root_dir: str = Query(...)) -> list[dict]:
    """Return all binding records for a feature."""
    codoc_dir = _codoc_dir(root_dir)
    store = _open_store(codoc_dir)
    try:
        feature = store.get_feature(uuid)
        if feature is None:
            raise HTTPException(status_code=404, detail=f"Feature {uuid!r} not found")
        bindings = store.list_bindings(uuid)
    finally:
        store.close()

    return [b.model_dump() for b in bindings]


@router.get("/feature/{uuid}/history", response_model=list[TransactionResponse])
async def get_feature_history(
    uuid: str,
    root_dir: str = Query(...),
    limit: int = Query(default=50, ge=1, le=1000),
) -> list[TransactionResponse]:
    """Return accepted transactions that mention this feature UUID."""
    codoc_dir = _codoc_dir(root_dir)
    store = _open_store(codoc_dir)
    try:
        feature = store.get_feature(uuid)
        if feature is None:
            raise HTTPException(status_code=404, detail=f"Feature {uuid!r} not found")
        txs = store.list_transactions(proposal=False, feature_uuid=uuid, limit=limit)
    finally:
        store.close()

    return [_tx_to_response(tx) for tx in txs]


# ---------------------------------------------------------------------------
# Tree / status routes
# ---------------------------------------------------------------------------


@router.get("/tree", response_model=list[FeatureResponse])
async def get_tree(
    root_dir: str = Query(...),
    parent_uuid: str = Query(default=""),
) -> list[FeatureResponse]:
    """Return a flat list of features under parent_uuid (root if empty/omitted)."""
    codoc_dir = _codoc_dir(root_dir)
    store = _open_store(codoc_dir)
    try:
        # Empty string → root features (parent_uuid IS NULL).
        # Any other value → children of that UUID.
        features = store.list_features(parent_uuid=parent_uuid if parent_uuid else "")
        responses = [_feature_to_response(f, store) for f in features]
    finally:
        store.close()

    return responses


@router.get("/state")
async def get_state(root_dir: str = Query(...)) -> dict:
    """Return the full repo stage + next-action hint.  This is the canonical
    endpoint the VSCode extension uses to drive the status bar and codoc.sync."""
    from codoc.core.stage import repo_stage
    state = repo_stage(root_dir)
    return state.to_dict()


@router.post("/sync/repo")
async def sync_repo(body: dict) -> dict:
    """State-aware umbrella sync: init → bootstrap → accept → render as needed.

    Body fields (all optional):
        root_dir (str)         — defaults to CODOC_ROOT_DIR env var
        accept_all (bool)      — auto-accept pending proposals
        prune_code (bool)      — delete source lines for RETIRE_REFLECTIVE
        from_ref (str)         — git ref range start (default HEAD~1)
        to_ref (str)           — git ref range end (default HEAD)
        post_commit (bool)     — internal flag from post-commit hook
    """
    from codoc.core.sync_dispatcher import dispatch

    import os
    root_dir = body.get("root_dir") or os.environ.get("CODOC_ROOT_DIR", os.getcwd())
    result = dispatch(
        root_dir,
        accept_all=body.get("accept_all", False),
        prune_code=body.get("prune_code", False),
        from_ref=body.get("from_ref", "HEAD~1"),
        to_ref=body.get("to_ref", "HEAD"),
        post_commit=body.get("post_commit", False),
    )
    return {
        "stage_before": result.stage_before,
        "stage_after": result.stage_after,
        "actions": result.actions,
        "summary": result.summary,
        "pending_count": result.pending_count,
        "feature_count": result.feature_count,
    }


@router.get("/status")
async def get_status(root_dir: str = Query(...)) -> dict:
    """Return a summary of bootstrap state, pending proposals, and entity counts."""
    codoc_dir = _codoc_dir(root_dir)
    codoc_path = Path(codoc_dir)

    bootstrap_done = (codoc_path / "unattributed.json").exists()

    db_path = codoc_path / "codoc.db"
    if not db_path.exists():
        return {
            "bootstrap_done": bootstrap_done,
            "pending_proposals": 0,
            "feature_count": 0,
            "binding_count": 0,
        }

    store = _open_store(codoc_dir)
    try:
        pending = store.list_transactions(proposal=True, limit=0)
        features = store.list_features()
        bindings = store.get_all_bindings()
    finally:
        store.close()

    return {
        "bootstrap_done": bootstrap_done,
        "pending_proposals": len(pending),
        "feature_count": len(features),
        "binding_count": len(bindings),
    }


# ---------------------------------------------------------------------------
# Anchor resolution route
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 1.5 — Projection endpoints
# ---------------------------------------------------------------------------


class RenderResponse(BaseModel):
    files: dict[str, str]
    base_hlc: str


class SyncResponse(BaseModel):
    applied: list[str]
    errors: list[dict]
    status: str
    files: dict[str, str] | None = None


class SyncRequest(BaseModel):
    root_dir: str
    author: str = "user"


@router.get("/tree.codoc", response_model=RenderResponse)
async def get_tree_codoc(root_dir: str = Query(...)) -> RenderResponse:
    """Render DB state to .codoc/tree/ and return the file contents."""
    from codoc.projection.tree_codoc import write_tree

    codoc_dir = _codoc_dir(root_dir)
    store, jsonl_log, tx_log = open_stores(codoc_dir)
    try:
        from codoc.projection.tree_codoc import render_tree_with_meta

        files, _ = render_tree_with_meta(store, tx_log)
        meta = write_tree(codoc_dir, store, tx_log)
    finally:
        store.close()

    return RenderResponse(files=files, base_hlc=meta.base_hlc)


@router.post("/sync", response_model=SyncResponse)
async def post_sync(body: SyncRequest) -> SyncResponse:
    """Parse .codoc/tree/, diff, apply transactions, re-render."""
    from codoc.projection.sync import sync_from_dir

    codoc_dir = _codoc_dir(body.root_dir)
    result = sync_from_dir(codoc_dir, author=body.author)
    return SyncResponse(
        applied=result.applied,
        errors=[
            {
                "kind": e.kind,
                "message": e.message,
                "file": e.file,
                "line": e.line,
            }
            for e in result.errors
        ],
        status=result.status,
        files=result.new_render,
    )


# ---------------------------------------------------------------------------
# Phase 1.5 — New feature query endpoints
# ---------------------------------------------------------------------------


@router.get("/features/severed")
async def get_severed_features(root_dir: str = Query(...)) -> list[dict]:
    """Return features that are in the SEVERED state (all bindings fail to resolve)."""
    from codoc.core.state_derivation import FeatureState

    codoc_dir = _codoc_dir(root_dir)
    store = _open_store(codoc_dir)
    try:
        features = store.list_features()
        result: list[dict] = []
        for feature in features:
            if feature.retired:
                continue
            bindings = store.list_bindings(feature.uuid)
            obligations = store.list_obligations(feature_uuid=feature.uuid, status="pending")
            # Conservative: pass empty resolutions → SEVERED if no resolutions available.
            state = compute_feature_state(feature, bindings, [], obligations)
            if state == FeatureState.SEVERED:
                result.append({
                    "uuid": feature.uuid,
                    "title": feature.title or feature.slug,
                    "slug": feature.slug,
                    "binding_count": len(bindings),
                })
    finally:
        store.close()

    return result


@router.get("/features/{uuid}/binding-candidates")
async def get_binding_candidates(uuid: str, root_dir: str = Query(...)) -> list[dict]:
    """Return top-3 nearest tree-sitter chunks for re-attribution of a feature."""
    codoc_dir = _codoc_dir(root_dir)
    store = _open_store(codoc_dir)
    try:
        feature = store.get_feature(uuid)
        if feature is None:
            raise HTTPException(status_code=404, detail=f"Feature {uuid!r} not found")

        bindings = store.list_bindings(uuid)
        # Collect files where this feature had bindings.
        anchor_files: set[str] = {b.anchor.file for b in bindings}
        anchor_symbol_paths: set[str] = {
            b.anchor.symbol_path for b in bindings if b.anchor.symbol_path
        }

        # Score chunk_fingerprints in the same files as existing bindings.
        scored: list[dict] = []
        if anchor_files:
            placeholders = ",".join("?" * len(anchor_files))
            rows = store._db.execute(
                f"SELECT file, symbol_path FROM chunk_fingerprints WHERE file IN ({placeholders})",
                tuple(anchor_files),
            ).fetchall()
            for row in rows:
                chunk_file = row["file"]
                chunk_symbol = row["symbol_path"]
                score = 2  # same file
                if chunk_symbol and chunk_symbol in anchor_symbol_paths:
                    score = 4  # exact symbol_path match
                scored.append({
                    "file": chunk_file,
                    "symbol_path": chunk_symbol or "",
                    "score": score,
                })

        # Sort descending by score and return top 3.
        scored.sort(key=lambda x: -x["score"])
        top3 = scored[:3]
    finally:
        store.close()

    return top3


@router.get("/bindings/by-file")
async def get_bindings_by_file(
    root_dir: str = Query(...),
    file: str = Query(...),
) -> dict:
    """Return {symbol_path: feature_uuid} for all active bindings in the given file."""
    codoc_dir = _codoc_dir(root_dir)
    store = _open_store(codoc_dir)
    try:
        bindings = store.list_bindings_by_file(file)
        result: dict[str, str] = {
            b.anchor.symbol_path: b.feature_uuid
            for b in bindings
            if b.anchor.symbol_path
        }
    finally:
        store.close()

    return result


@router.post("/anchor/resolve")
async def resolve_anchor_endpoint(body: AnchorResolveRequest) -> AnchorPositionResponse | None:
    """Resolve an Anchor to a (start_line, end_line, start_byte, end_byte) position.

    Returns None when the file's language is unsupported or the anchor cannot
    be resolved against the current file contents.
    """
    from codoc.core.anchor_resolver import resolve_anchor
    from codoc.lang import detect_language, get_adapter

    abs_file = Path(body.root_dir) / body.file
    if not abs_file.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.file}")

    source = abs_file.read_text(encoding="utf-8", errors="replace")
    lang = detect_language(str(abs_file))
    if lang is None:
        return None

    adapter = get_adapter(lang)
    anchor = Anchor(
        file=body.file,
        symbol_path=body.symbol_path,
        ts_query=body.ts_query,
        occurrence_index=body.occurrence_index,
    )
    result = resolve_anchor(anchor, source, adapter)
    if result is None:
        return None

    start_byte, end_byte = result
    source_bytes = source.encode("utf-8")
    start_line = source_bytes[:start_byte].count(b"\n")
    end_line = source_bytes[:end_byte].count(b"\n")
    return AnchorPositionResponse(
        start_line=start_line,
        end_line=end_line,
        start_byte=start_byte,
        end_byte=end_byte,
    )


# ---------------------------------------------------------------------------
# Claude Code integration — hook receiver, SSE stream, live activity, commit gate
# ---------------------------------------------------------------------------

import asyncio
import json as _json
from fastapi.responses import StreamingResponse

_EDIT_TOOLS = ("Edit", "Write", "MultiEdit")


class ClaudeCodeEventRequest(BaseModel):
    """Payload from a Claude Code hook (PreToolUse or PostToolUse)."""
    session_id: str = ""
    transcript_path: str = ""
    cwd: str = ""
    hook_event_name: str = ""  # "PreToolUse" | "PostToolUse"
    tool_name: str = ""        # "Edit" | "Write" | "MultiEdit" | "Read" etc.
    tool_input: dict = {}
    tool_response: dict | None = None
    tool_use_id: str = ""


def _extract_file_paths(tool: str, tool_input: dict) -> list[str]:
    """Return absolute file paths touched by a tool call (one per MultiEdit edit, else 0/1)."""
    if tool == "MultiEdit":
        return [e["file_path"] for e in tool_input.get("edits", []) if e.get("file_path")]
    fp = tool_input.get("file_path", "")
    return [fp] if fp else []


def _resolve_features(db_path: str, rel_path: str) -> tuple[list[str], list[str]]:
    """Look up feature uuids + slugs bound to a file. Best-effort; returns ([], []) on any error."""
    if not db_path or not Path(db_path).exists():
        return [], []
    try:
        store = SQLiteStore(db_path)
        store.open()
        try:
            uuids: list[str] = []
            slugs: list[str] = []
            seen: set[str] = set()
            for b in store.list_bindings_by_file(rel_path):
                if b.feature_uuid in seen:
                    continue
                seen.add(b.feature_uuid)
                uuids.append(b.feature_uuid)
                feat = store.get_feature(b.feature_uuid)
                if feat:
                    slugs.append(feat.slug)
            return uuids, slugs
        finally:
            store.close()
    except Exception:
        return [], []


async def _run_debounced_reflect(
    root_dir: str, codoc_dir: str, rel_path: str, session_id: str
) -> None:
    """Run reflect on a single file and publish reflect_done if proposals were emitted."""
    from codoc.listener.event_bus import BusEvent, bus
    from codoc.pipelines.reflective.runner import run_reflect_files

    try:
        result = run_reflect_files(
            root_dir=root_dir,
            codoc_dir=codoc_dir,
            file_paths=[rel_path],
            session_id=session_id,
            author="claude-code",
        )
        if result.get("proposals_emitted", 0) > 0:
            await bus.publish(BusEvent(
                topic="reflect_done",
                data={"rel_path": rel_path, "proposals": result["proposals"]},
            ))
    except Exception:
        pass


class ActivityEntryResponse(BaseModel):
    session_id: str
    rel_path: str
    tool: str
    started_at: float
    feature_uuids: list[str]
    feature_slugs: list[str]


class CommitPreflightRequest(BaseModel):
    root_dir: str
    staged_files: list[str]  # repo-relative paths


class CommitPreflightResponse(BaseModel):
    blocked: bool
    pending: list[dict]
    message: str


@router.post("/claude-code/event")
async def claude_code_event(body: ClaudeCodeEventRequest) -> dict:
    """Receive a Claude Code hook event (PreToolUse / PostToolUse).

    Returns {} immediately so the hook never blocks the Claude session.
    Side effects: updates live-activity ledger, publishes SSE event,
    schedules debounced reflect on PostToolUse file edits.
    """
    from codoc.listener.debouncer import debouncer
    from codoc.listener.event_bus import BusEvent, bus
    from codoc.listener.ledger import ledger
    from codoc.listener.session_log import log_event

    root_dir = body.cwd or ""
    session_id = body.session_id or "unknown"
    phase = "pre" if body.hook_event_name == "PreToolUse" else "post"
    tool = body.tool_name

    abs_paths = _extract_file_paths(tool, body.tool_input)
    if not abs_paths:
        return {}

    codoc_dir = str(Path(root_dir) / ".codoc") if root_dir else ""
    db_path = str(Path(codoc_dir) / "codoc.db") if codoc_dir else ""

    for abs_path in abs_paths:
        try:
            rel_path = str(Path(abs_path).relative_to(root_dir)) if root_dir else abs_path
        except ValueError:
            rel_path = abs_path

        feature_uuids, feature_slugs = _resolve_features(db_path, rel_path)

        ledger.record(
            session_id=session_id,
            file_path=abs_path,
            rel_path=rel_path,
            tool=f"{body.hook_event_name}:{tool}",
            phase=phase,
            feature_uuids=feature_uuids,
            feature_slugs=feature_slugs,
        )

        await bus.publish(BusEvent(topic="activity", data={
            "session_id": session_id,
            "phase": phase,
            "tool": tool,
            "rel_path": rel_path,
            "feature_uuids": feature_uuids,
            "feature_slugs": feature_slugs,
        }))

        if phase == "post" and tool in _EDIT_TOOLS and root_dir and codoc_dir:
            file_key = f"{root_dir}::{rel_path}"
            # Bind loop-variable values into the closure via defaults.
            async def _cb(rd=root_dir, cd=codoc_dir, rp=rel_path, sid=session_id):
                await _run_debounced_reflect(rd, cd, rp, sid)
            await debouncer.schedule(file_key, _cb)

    if codoc_dir:
        try:
            log_event(codoc_dir, session_id, body.model_dump())
        except Exception:
            pass

    return {}


@router.get("/claude-code/activity", response_model=list[ActivityEntryResponse])
async def get_claude_code_activity(root_dir: str = Query(...)) -> list[ActivityEntryResponse]:
    """Return the current in-memory live-activity ledger snapshot."""
    from codoc.listener.ledger import ledger

    return [
        ActivityEntryResponse(
            session_id=e.session_id,
            rel_path=e.rel_path,
            tool=e.tool,
            started_at=e.started_at,
            feature_uuids=e.feature_uuids,
            feature_slugs=e.feature_slugs,
        )
        for e in ledger.get_active()
    ]


@router.get("/events/stream")
async def events_stream(root_dir: str = Query(...)):
    """Server-Sent Events stream for live codoc events.

    Topics: activity, proposal, accept, reject, reflect_done.
    Sends keepalive comment every 25s to prevent proxy timeouts.
    """
    from codoc.listener.event_bus import bus

    async def generate():
        q = bus.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    data = _json.dumps(event.data)
                    yield f"event: {event.topic}\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/commit/preflight", response_model=CommitPreflightResponse)
async def commit_preflight(body: CommitPreflightRequest) -> CommitPreflightResponse:
    """Check if staged files have unaccepted codoc proposals.

    Called by the git pre-commit hook. Returns blocked=True plus the list of
    pending proposals that touch the staged files, so the user can accept/reject
    before committing.
    """
    codoc_dir = _codoc_dir(body.root_dir)
    store = _open_store(codoc_dir)
    try:
        pending = store.list_transactions(proposal=True, limit=0)
    finally:
        store.close()

    staged_set = set(body.staged_files)

    # Find pending proposals that touch staged files.
    # Proposals carry file info in their payload (e.g. "file" field for EVICT/ABSORB,
    # "bindings" field for INTRODUCE, or we check feature bindings for AMEND/RENAME).
    blocking: list[dict] = []
    for tx in pending:
        payload = tx.payload
        tx_files: set[str] = set()

        # Most reflective proposals have a "file" key directly.
        if "file" in payload:
            tx_files.add(payload["file"])
        # INTRODUCE may have bindings list.
        for binding in payload.get("bindings", []):
            if isinstance(binding, dict) and "file" in binding.get("anchor", {}):
                tx_files.add(binding["anchor"]["file"])

        if tx_files & staged_set or not tx_files:
            # Include if files overlap OR if we can't determine files (conservative).
            blocking.append({
                "hlc": tx.hlc.to_str(),
                "kind": tx.kind.value,
                "slug": payload.get("slug", ""),
                "title": payload.get("title", payload.get("slug", "")),
                "files": list(tx_files),
            })

    blocked = len(blocking) > 0
    message = (
        f"{len(blocking)} pending proposal(s) touch staged files"
        if blocked
        else "No pending proposals — commit is clean"
    )

    return CommitPreflightResponse(blocked=blocked, pending=blocking, message=message)
