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
                _apply_accepted_transaction(accepted_tx, store, jsonl_log)
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
        _apply_accepted_transaction(accepted_tx, store, jsonl_log)
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
