"""API routes for semantic tree pipeline: analyze (extract + index + RAG pipeline), search, status."""

import json
import os
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from api.semantic_tree.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    FileInput,
    InterventionResponse,
    MergeSummary,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    StatusResponse,
    SyncRequest,
    SyncResponse,
    DeltaSummary,
    TreeEditRequest,
    TreeEditResponse,
    TreeEditOperationItem,
    TargetModificationArea,
    ApplyTreeEditRequest,
    ApplyTreeEditResponse,
    ApplyRequest,
    ApplyResponse,
    PlannedChangeItem,
)
from api.semantic_tree.extraction.discovery import discover_files
from api.semantic_tree.extraction.python_extractor import extract_python_file
from api.semantic_tree.models import CodebaseSnapshot, FileInfo
from api.semantic_tree.indexing.chunker import entity_chunks
from api.semantic_tree.indexing.vector_store import SemanticVectorStore
from api.semantic_tree.pipeline.domain_discovery import run_domain_discovery
from api.semantic_tree.pipeline.semantic_parsing import run_semantic_parsing_rag
from api.semantic_tree.pipeline.hierarchical_construction import run_hierarchical_construction
from api.semantic_tree.pipeline.tree_assembly import assemble_tree
from api.semantic_tree.pipeline.tree_validation import validate_tree
from api.semantic_tree.pipeline.incremental_forward import incremental_forward
from api.semantic_tree.pipeline.incremental_inverse import apply_inverse_and_update_state
from api.semantic_tree.pipeline.code_applicator import CodeChange
from api.semantic_tree.pipeline.diff_format import changes_to_unified_diff_for_files
from api.semantic_tree.output.tree_serializer import tree_to_markdown, tree_to_json
from api.semantic_tree.logging import PipelineLogger, log_tree_edit, log_apply_tree_edit
from api.semantic_tree.observation_report import (
    build_apply_observations,
    log_observations,
)
from api.semantic_tree.state.persistence import load_sync_state, save_sync_state
from api.semantic_tree.state.delta import compute_entity_delta
from api.semantic_tree.state.fingerprint import compute_entity_fingerprint, compute_file_fingerprint
from api.semantic_tree.state.models import SyncState, EntityFingerprint, SemanticCacheEntry
from api.semantic_tree.state.sync_guard import can_run_forward, can_run_inverse
from api.tools.embedder import get_embedder
from api.config import get_embedder_type

logger = logging.getLogger(__name__)
pipeline_log = PipelineLogger()

router = APIRouter(prefix="/semantic_tree", tags=["semantic_tree"])


def _build_snapshot(
    path: str,
    excluded_dirs: Optional[List[str]] = None,
    excluded_files: Optional[List[str]] = None,
) -> CodebaseSnapshot:
    """Extract codebase snapshot from local path (Python only)."""
    path_resolved = Path(path).resolve()
    if not path_resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    root_dir = str(path_resolved)
    discovered = discover_files(root_dir, excluded_dirs=excluded_dirs, excluded_files=excluded_files)
    files: list[FileInfo] = []

    for rel_path, lang in discovered:
        if lang != "python":
            continue
        full_path = path_resolved / rel_path
        if not full_path.is_file():
            continue
        try:
            source = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("Skip file %s: %s", rel_path, e)
            continue
        file_info = extract_python_file(rel_path, source, root_dir, include_imports=True)
        files.append(file_info)

    return CodebaseSnapshot(root_dir=root_dir, files=files)


def _build_snapshot_from_files(file_inputs: List[FileInput], root_dir: str) -> CodebaseSnapshot:
    """Build codebase snapshot from in-memory file list (Python only). Paths in file_inputs are relative to root_dir."""
    files: list[FileInfo] = []
    for fi in file_inputs:
        path = (fi.path if not fi.path.startswith("/") else fi.path.lstrip("/")).replace("\\", "/")
        if not path.endswith(".py"):
            continue
        file_info = extract_python_file(path, fi.content, root_dir, include_imports=True)
        files.append(file_info)
    return CodebaseSnapshot(root_dir=root_dir, files=files)


def _intervention(step: str, message: str) -> JSONResponse:
    """Return 422 with intervention_required body; agent should stop for user fix."""
    body = InterventionResponse(status="intervention_required", step=step, message=message)
    return JSONResponse(status_code=422, content=body.model_dump())


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """
    Integrated pipeline: extract → index (RAG) → domain discovery → semantic parsing (RAG) → hierarchy → tree.
    Provide either path (local directory) or files + root_dir (in-memory codebase). Output tree_md is in the
    format required by test fixtures (sigils, [path], (entity), deps:) and parseable by parseTreeBlock().
    Requires LLM and embedder configured. Stops with 422 intervention_required on parse/LLM issues.
    """
    use_files = request.files and len(request.files) > 0
    if use_files:
        root_dir = (request.root_dir or "").strip() or ""
        try:
            snapshot = _build_snapshot_from_files(request.files, root_dir)
        except Exception as e:
            logger.exception("Extract from files failed")
            raise HTTPException(status_code=500, detail=str(e))
        index_path = request.index_path or os.path.join(tempfile.gettempdir(), "codenav_index")
    else:
        if not request.path:
            raise HTTPException(status_code=400, detail="Either path or files must be provided")
        try:
            snapshot = _build_snapshot(
                request.path,
                excluded_dirs=request.excluded_dirs,
                excluded_files=request.excluded_files,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Extract failed")
            raise HTTPException(status_code=500, detail=str(e))
        index_path = request.index_path or os.path.join(request.path, ".codenav", "index")

    if not snapshot.all_entities:
        return _intervention("extract", "No entities extracted; check path and filters.")
    embedder_type = get_embedder_type()
    try:
        embedder = get_embedder(embedder_type=embedder_type)
    except Exception as e:
        return _intervention("index", f"Embedder not available: {e}")

    with pipeline_log.stage("extraction", entity_count=len(snapshot.all_entities)):
        pass  # extraction already done above
    store = SemanticVectorStore()
    chunks = entity_chunks(snapshot)
    try:
        with pipeline_log.stage("indexing", entity_count=len(chunks)):
            store.add_entities(chunks, embedder)
    except Exception as e:
        return _intervention("index", f"Failed to build index: {e}")
    if store.size == 0:
        return _intervention("index", "No embeddings produced; check embedder config.")
    try:
        store.save(index_path)
    except Exception as e:
        logger.warning("Could not save index to %s: %s", index_path, e)

    try:
        with pipeline_log.stage("domain_discovery"):
            areas = run_domain_discovery(
                snapshot,
                repo_name=request.repo_name,
                repo_info=None,
                provider=request.provider,
                model=request.model,
            )
    except ValueError as e:
        if "not configured" in str(e).lower() or "environment" in str(e).lower():
            raise HTTPException(status_code=503, detail="LLM not configured or keys missing.")
        return _intervention("domain_discovery", str(e))
    except Exception as e:
        logger.exception("Domain discovery failed")
        return _intervention("domain_discovery", str(e))

    if not areas:
        return _intervention("domain_discovery", "LLM returned no functional areas; check prompt/response.")

    try:
        with pipeline_log.stage("semantic_parsing", areas=len(areas)):
            features = run_semantic_parsing_rag(
                snapshot,
                store=store,
                embedder=embedder,
                areas=areas,
                repo_name=request.repo_name,
                repo_info="",
                provider=request.provider,
                model=request.model,
            )
    except ValueError as e:
        return _intervention("semantic_parsing", str(e))
    except Exception as e:
        logger.exception("Semantic parsing failed")
        return _intervention("semantic_parsing", str(e))

    group_to_entities: dict[str, list[str]] = {}
    for f in snapshot.files:
        group_to_entities[f.fpath] = [e.name for e in f.entities]

    try:
        with pipeline_log.stage("hierarchical_construction"):
            hierarchy = run_hierarchical_construction(
                areas,
                group_to_entities,
                provider=request.provider,
                model=request.model,
            )
    except ValueError as e:
        return _intervention("hierarchical_construction", str(e))
    except Exception as e:
        logger.exception("Hierarchy construction failed")
        return _intervention("hierarchical_construction", str(e))

    with pipeline_log.stage("tree_assembly"):
        tree = assemble_tree(snapshot, features, hierarchy, include_deps=True)
        for w in validate_tree(tree, snapshot):
            logger.warning("[CODENAV] tree validation: %s", w)
    pipeline_log.summary()

    tree_md = tree_to_markdown(tree)
    # Persist sync state after analyze (path-based only) so Apply and incremental Sync work
    if not use_files and request.path:
        root_dir = snapshot.root_dir
        new_entity_fps: dict[str, EntityFingerprint] = {}
        for e in snapshot.all_entities:
            ch, sh = compute_entity_fingerprint(e)
            new_entity_fps[f"{e.fpath}::{e.name}"] = EntityFingerprint(content_hash=ch, signature_hash=sh)
        new_file_fps: dict[str, str] = {}
        for f in snapshot.files:
            full_path = Path(root_dir) / f.fpath
            if full_path.is_file():
                try:
                    new_file_fps[f.fpath] = compute_file_fingerprint(
                        full_path.read_text(encoding="utf-8", errors="replace")
                    )
                except Exception:
                    pass
        cache_entries: dict[str, SemanticCacheEntry] = {}
        for e in snapshot.all_entities:
            ch, _ = compute_entity_fingerprint(e)
            sf = next((x for x in features if x.entity_name == e.name), None)
            if sf:
                cache_entries[ch] = SemanticCacheEntry(
                    content_hash=ch, entity_name=e.name, features=list(sf.features)
                )
        initial_state = SyncState(
            entity_fingerprints=new_entity_fps,
            file_fingerprints=new_file_fps,
            semantic_cache=cache_entries,
            domain_areas=[{"name": a.name} for a in areas],
            hierarchy_cache=[h.model_dump() for h in hierarchy],
            tree_version=0,
            code_version=1,
            last_sync_direction="forward",
            last_tree_md=tree_md,
            root_dir=root_dir,
            index_path=index_path,
        )
        save_sync_state(initial_state, root_dir)

    if request.format == "json":
        return AnalyzeResponse(
            tree_json=tree_to_json(tree),
            root_dir=snapshot.root_dir,
            file_count=len(snapshot.files),
            entity_count=len(snapshot.all_entities),
        )
    return AnalyzeResponse(
        tree_md=tree_md,
        root_dir=snapshot.root_dir,
        file_count=len(snapshot.files),
        entity_count=len(snapshot.all_entities),
    )


@router.post("/sync", response_model=SyncResponse)
async def sync(request: SyncRequest):
    """
    Forward sync (code → tree). Use path for persistent state and incremental updates.
    When force_full=False and state exists at path/.codenav/sync_state.json, runs incremental
    forward (delta-based). Otherwise full pipeline. Returns tree_md/tree_json and is_incremental.
    """
    use_files = request.files and len(request.files) > 0
    if use_files:
        root_dir = (request.root_dir or "").strip() or ""
        try:
            snapshot = _build_snapshot_from_files(request.files, root_dir)
        except Exception as e:
            logger.exception("Extract from files failed")
            raise HTTPException(status_code=500, detail=str(e))
        index_path = request.index_path or os.path.join(tempfile.gettempdir(), "codenav_index")
        old_state = None
    else:
        if not request.path:
            raise HTTPException(status_code=400, detail="Either path or files must be provided")
        try:
            snapshot = _build_snapshot(
                request.path,
                excluded_dirs=request.excluded_dirs,
                excluded_files=request.excluded_files,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Extract failed")
            raise HTTPException(status_code=500, detail=str(e))
        root_dir = snapshot.root_dir
        index_path = request.index_path or os.path.join(request.path, ".codenav", "index")
        old_state = None if request.force_full else load_sync_state(root_dir)
        # Anti-loop guard: if last sync was inverse, only allow forward when code actually changed
        if old_state is not None:
            delta = compute_entity_delta(old_state.entity_fingerprints, snapshot.all_entities)
            allowed, msg = can_run_forward(old_state, delta)
            if not allowed:
                raise HTTPException(status_code=409, detail=msg)

    if not snapshot.all_entities:
        return _intervention("extract", "No entities extracted; check path and filters.")
    embedder_type = get_embedder_type()
    try:
        embedder = get_embedder(embedder_type=embedder_type)
    except Exception as e:
        return _intervention("index", f"Embedder not available: {e}")

    try:
        with pipeline_log.stage("sync", is_incremental=old_state is not None):
            tree, new_state, timing, delta_summary = incremental_forward(
                snapshot,
                index_path,
                old_state,
                embedder,
                repo_name=request.repo_name,
                provider=request.provider,
                model=request.model,
            )
        pipeline_log.summary()
    except ValueError as e:
        if "No index to save" in str(e) or "No valid embeddings" in str(e).lower():
            return _intervention("index", "No valid embeddings produced; check embedder (e.g. Ollama) is running.")
        logger.exception("Sync failed")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("Sync failed")
        raise HTTPException(status_code=500, detail=str(e))

    # When delta was empty, tree is None; use persisted last_tree_md (e.g. from apply_tree_edit)
    tree_md_out = new_state.last_tree_md if tree is None else tree_to_markdown(tree)
    merge_summary_out = None
    # Forward merge: prior tree + re-encoded tree (code wins grounded, user wins underspec)
    if (
        tree is not None
        and old_state is not None
        and old_state.last_tree_md
    ):
        merge_data = _merge_trees_ts(old_state.last_tree_md, tree_to_markdown(tree))
        if not merge_data.get("error") and merge_data.get("merged_md"):
            tree_md_out = merge_data["merged_md"]
            new_state = new_state.model_copy(update={"last_tree_md": tree_md_out})
            save_sync_state(new_state, snapshot.root_dir)
            ms = merge_data.get("merge_summary")
            if ms and isinstance(ms, dict):
                merge_summary_out = MergeSummary(
                    preserved_user_nodes=ms.get("preserved_user_nodes", 0),
                    overwritten_grounded_nodes=ms.get("overwritten_grounded_nodes", 0),
                    surfaced_added=ms.get("surfaced_added", 0),
                    drifted_nodes=ms.get("drifted_nodes", 0),
                )

    tree_json_out = None
    if request.format == "json":
        if tree is not None:
            tree_json_out = tree_to_json(tree)
        # When tree is None we don't have a SemanticTree in Python; client can parse tree_md if needed

    ds = DeltaSummary(**(delta_summary or {})) if delta_summary else None
    if request.format == "json":
        return SyncResponse(
            tree_json=tree_json_out,
            root_dir=snapshot.root_dir,
            file_count=len(snapshot.files),
            entity_count=len(snapshot.all_entities),
            is_incremental=old_state is not None,
            delta_summary=ds,
            timing=timing or None,
            merge_summary=merge_summary_out,
        )
    return SyncResponse(
        tree_md=tree_md_out,
        root_dir=snapshot.root_dir,
        file_count=len(snapshot.files),
        entity_count=len(snapshot.all_entities),
        is_incremental=old_state is not None,
        delta_summary=ds,
        timing=timing or None,
        merge_summary=merge_summary_out,
    )


@router.get("/tree")
async def get_tree(path: str):
    """Return the last synced semantic tree markdown for the given codebase path (from state)."""
    state = load_sync_state(path)
    if not state or not state.last_tree_md:
        raise HTTPException(status_code=404, detail="No tree found for path; run /sync first.")
    return {"tree_md": state.last_tree_md, "root_dir": state.root_dir or path}


def _run_ts_cli(script_basename: str, *md_contents: str) -> dict:
    """Run a TS CLI script with N markdown inputs (temp files). Returns parsed JSON; sets 'error' on failure."""
    root = Path(__file__).resolve().parent.parent.parent.parent
    script = root / "src" / "cli" / script_basename
    if not script.is_file():
        return {"error": f"Script not found: {script_basename}"}
    paths: list[Path] = []
    try:
        for content in md_contents:
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
            f.write(content)
            f.close()
            paths.append(Path(f.name))
        out = subprocess.run(
            ["npx", "tsx", str(script)] + [str(p) for p in paths],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(root),
        )
        if out.returncode != 0:
            err = (out.stderr or out.stdout or "script failed").strip()[:500]
            return {"error": err}
        return json.loads(out.stdout or "{}")
    finally:
        for p in paths:
            p.unlink(missing_ok=True)


def _tree_edit_targets_ts(base_md: str, edited_md: str) -> dict:
    """Call TypeScript tree-edit-targets CLI; returns { operations: [...], error?: str }."""
    data = _run_ts_cli("tree-edit-targets.ts", base_md, edited_md)
    if data.get("error") and "operations" not in data:
        return {"operations": [], "error": data["error"]}
    return data


def _merge_trees_ts(prior_md: str, reencoded_md: str) -> dict:
    """Call TypeScript merge-trees CLI; returns { merged_md, merge_summary, error? }."""
    data = _run_ts_cli("merge-trees.ts", prior_md, reencoded_md)
    if data.get("error"):
        return {"merged_md": "", "merge_summary": None, "error": data["error"]}
    return data


def _tree_edit_data_to_items(data: dict) -> tuple[list[TreeEditOperationItem], list[dict]]:
    """
    Convert TS tree-edit result to response items and raw ops. Single place for parsing
    so tree_edit, apply_tree_edit, and apply stay in sync.
    """
    raw_ops = data.get("operations", [])
    items = []
    for op in raw_ops:
        targets = [
            TargetModificationArea(
                node_path=t.get("node_path", ""),
                fpath=t.get("fpath"),
                entity_name=t.get("entity_name"),
                line_range=tuple(t["line_range"]) if t.get("line_range") else None,
            )
            for t in op.get("targets", [])
        ]
        items.append(
            TreeEditOperationItem(
                op=op.get("op", ""),
                target=op.get("target", ""),
                params=op.get("params") or {},
                targets=targets,
                underspecified=op.get("underspecified"),
                underspec_reason=op.get("underspec_reason"),
            )
        )
    return items, raw_ops


@router.post("/tree_edit", response_model=TreeEditResponse)
async def tree_edit(request: TreeEditRequest):
    """
    Identify target modification areas for a semantic tree edit. Provide either path (to load
    base tree from state) or base_tree_md. edited_tree_md is the user-edited tree. Returns
    operations and code locations (fpath, entity_name, line_range) for each; no code generation.
    """
    if request.path:
        state = load_sync_state(request.path)
        if not state or not state.last_tree_md:
            raise HTTPException(
                status_code=400,
                detail="No sync state for path; run /sync first or provide base_tree_md.",
            )
        base_md = state.last_tree_md
    elif request.base_tree_md:
        base_md = request.base_tree_md
    else:
        raise HTTPException(status_code=400, detail="Provide path or base_tree_md.")

    data = _tree_edit_targets_ts(base_md, request.edited_tree_md)
    if data.get("error"):
        return TreeEditResponse(operations=[], error=data["error"])
    items, _ = _tree_edit_data_to_items(data)
    log_tree_edit(items)
    return TreeEditResponse(operations=items)


@router.post("/apply_tree_edit", response_model=ApplyTreeEditResponse)
async def apply_tree_edit(request: ApplyTreeEditRequest):
    """
    Apply a tree edit (inverse sync): persist the edited tree as canonical and update state.
    No code generation yet — only state update so the next sync does not overwrite the user's tree.
    When code is unchanged, next sync will skip full pipeline and keep this tree (incremental).
    """
    if not request.path:
        raise HTTPException(status_code=400, detail="path is required to apply tree edit (persist state).")
    state = load_sync_state(request.path)
    allowed, msg = can_run_inverse(state)
    if not allowed:
        raise HTTPException(status_code=400, detail=msg)
    base_md = state.last_tree_md
    root_dir = state.root_dir

    data = _tree_edit_targets_ts(base_md, request.edited_tree_md)
    if data.get("error"):
        return ApplyTreeEditResponse(operations=[], applied=False, tree_version=state.tree_version, error=data["error"])
    items, _ = _tree_edit_data_to_items(data)

    new_tree_version = state.tree_version + 1
    new_state = state.model_copy(update={
        "last_tree_md": request.edited_tree_md,
        "last_sync_direction": "inverse",
        "tree_version": new_tree_version,
    })
    save_sync_state(new_state, root_dir)
    log_apply_tree_edit(True, new_tree_version)

    return ApplyTreeEditResponse(operations=items, applied=True, tree_version=new_tree_version)


# When diff is done against state (no base_tree_md), refuse if too many EditFeature ops
# to avoid "edit every file" when backend tree phrasing differs from user's .codoc
_MAX_EDIT_FEATURE_OPS_WITHOUT_BASE = 2

@router.post("/apply", response_model=ApplyResponse)
async def apply(request: ApplyRequest):
    """
    Apply tree edit with LLM code generation (inverse sync). Computes operations from
    base tree vs edited_tree_md. Prefer base_tree_md (tree before this edit) so the diff
    reflects only the user's actual edits; otherwise state.last_tree_md is used and may
    produce many operations if the user's doc phrasing differs from the backend tree.
    """
    if not request.path:
        raise HTTPException(status_code=400, detail="path is required.")
    state = load_sync_state(request.path)
    allowed, msg = can_run_inverse(state)
    if not allowed:
        raise HTTPException(status_code=400, detail=msg)
    root_dir = state.root_dir or request.path
    base_md = request.base_tree_md if request.base_tree_md else state.last_tree_md

    data = _tree_edit_targets_ts(base_md, request.edited_tree_md)
    if data.get("error"):
        return ApplyResponse(
            operations=[],
            applied=False,
            tree_version=state.tree_version,
            error=data["error"],
        )
    items, raw_ops = _tree_edit_data_to_items(data)

    # Safeguard: if client did not send base_tree_md and we got many EditFeature ops,
    # we are likely diffing backend tree vs user doc (different phrasing → every node "changed")
    if not request.base_tree_md and raw_ops:
        edit_feature_count = sum(1 for op in raw_ops if (op.get("op") or "") == "EditFeature")
        if edit_feature_count > _MAX_EDIT_FEATURE_OPS_WITHOUT_BASE:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Diff produced {edit_feature_count} EditFeature operations (max {_MAX_EDIT_FEATURE_OPS_WITHOUT_BASE} without base_tree_md). "
                    "Send base_tree_md (the tree content before this edit) so only your actual edits are applied. "
                    "Otherwise the server compares the backend tree to your doc and may treat every node as changed."
                ),
            )

    try:
        snapshot = _build_snapshot(root_dir)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Build snapshot for apply failed: %s", e)
        return ApplyResponse(
            operations=items,
            applied=False,
            tree_version=state.tree_version,
            error=f"Failed to build codebase snapshot: {e}",
        )

    modified, changes, drift_report, new_state = apply_inverse_and_update_state(
        raw_ops,
        snapshot,
        state,
        request.edited_tree_md,
        provider=request.provider,
        model=request.model,
        dry_run=request.dry_run,
    )
    planned = [
        PlannedChangeItem(
            fpath=c.fpath,
            line_start=c.line_start,
            line_end=c.line_end,
            new_content=c.new_content,
        )
        for c in changes
    ]
    any_underspec = any(getattr(i, "underspecified", False) for i in items)
    generated_artifact_count = sum(1 for i in items if getattr(i, "underspecified", False)) if any_underspec else None
    completion_mode = "best_effort" if any_underspec else None

    unified_diff: Optional[str] = None
    if changes and request.diff_format in ("unified_diff", "search_replace"):
        unified_diff = changes_to_unified_diff_for_files(root_dir, changes, context_lines=3)

    apply_obs = build_apply_observations(
        modified, drift_report, items, completion_mode, generated_artifact_count
    )
    log_observations("apply", apply_obs=apply_obs)

    return ApplyResponse(
        operations=items,
        applied=not request.dry_run and (len(modified) > 0 or new_state is not None),
        modified_fpaths=modified,
        planned_changes=planned,
        drift_report=drift_report,
        tree_version=new_state.tree_version if new_state else state.tree_version,
        completion_mode=completion_mode,
        generated_artifact_count=generated_artifact_count,
        unified_diff=unified_diff,
    )


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """Semantic search over an existing index (e.g. after analyze). Requires embedder configured."""
    if not Path(request.index_path).is_dir():
        raise HTTPException(status_code=404, detail="Index path not found")

    embedder_type = get_embedder_type()
    embedder = get_embedder(embedder_type=embedder_type)

    store = SemanticVectorStore()
    try:
        store.load(request.index_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load index: {e}")

    hits = store.search(request.query, embedder, top_k=request.top_k)
    results = [
        SearchResultItem(
            entity_name=e.name,
            fpath=e.fpath,
            entity_type=e.entity_type,
            distance=dist,
        )
        for e, dist in hits
    ]
    return SearchResponse(results=results)


@router.get("/status", response_model=StatusResponse)
async def status(index_path: str | None = None):
    """Index status if index_path given (e.g. path/.codenav/index after analyze)."""
    if not index_path:
        return StatusResponse()
    p = Path(index_path)
    if not p.is_dir() or not (p / "entities.pkl").exists():
        return StatusResponse(index_path=index_path, index_size=0)
    try:
        store = SemanticVectorStore()
        store.load(index_path)
        return StatusResponse(index_path=index_path, index_size=store.size)
    except Exception:
        return StatusResponse(index_path=index_path, index_size=0)
