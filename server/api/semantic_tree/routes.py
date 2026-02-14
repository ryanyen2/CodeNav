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
from api.semantic_tree.pipeline.incremental_forward import incremental_forward
from api.semantic_tree.output.tree_serializer import tree_to_markdown, tree_to_json
from api.semantic_tree.logging import PipelineLogger
from api.semantic_tree.state.persistence import load_sync_state
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
    pipeline_log.summary()

    if request.format == "json":
        return AnalyzeResponse(
            tree_json=tree_to_json(tree),
            root_dir=snapshot.root_dir,
            file_count=len(snapshot.files),
            entity_count=len(snapshot.all_entities),
        )
    return AnalyzeResponse(
        tree_md=tree_to_markdown(tree),
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
    except Exception as e:
        logger.exception("Sync failed")
        raise HTTPException(status_code=500, detail=str(e))

    ds = DeltaSummary(**(delta_summary or {})) if delta_summary else None
    if request.format == "json":
        return SyncResponse(
            tree_json=tree_to_json(tree),
            root_dir=snapshot.root_dir,
            file_count=len(snapshot.files),
            entity_count=len(snapshot.all_entities),
            is_incremental=old_state is not None,
            delta_summary=ds,
            timing=timing or None,
        )
    return SyncResponse(
        tree_md=tree_to_markdown(tree),
        root_dir=snapshot.root_dir,
        file_count=len(snapshot.files),
        entity_count=len(snapshot.all_entities),
        is_incremental=old_state is not None,
        delta_summary=ds,
        timing=timing or None,
    )


@router.get("/tree")
async def get_tree(path: str):
    """Return the last synced semantic tree markdown for the given codebase path (from state)."""
    state = load_sync_state(path)
    if not state or not state.last_tree_md:
        raise HTTPException(status_code=404, detail="No tree found for path; run /sync first.")
    return {"tree_md": state.last_tree_md, "root_dir": state.root_dir or path}


def _tree_edit_targets_ts(base_md: str, edited_md: str) -> dict:
    """Call TypeScript tree-edit-targets CLI; returns { operations: [...], error?: str }."""
    root = Path(__file__).resolve().parent.parent.parent.parent
    script = root / "src" / "cli" / "tree-edit-targets.ts"
    if not script.is_file():
        return {"operations": [], "error": "Tree edit targets script not found; ensure CodeNav src is available."}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f1:
        f1.write(base_md)
        base_path = f1.name
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f2:
            f2.write(edited_md)
            edited_path = f2.name
        try:
            out = subprocess.run(
                ["npx", "tsx", str(script), base_path, edited_path],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(root),
            )
            if out.returncode != 0 and out.stderr:
                return {"operations": [], "error": out.stderr.strip()[:500]}
            data = json.loads(out.stdout or "{}")
            return data
        finally:
            Path(edited_path).unlink(missing_ok=True)
    finally:
        Path(base_path).unlink(missing_ok=True)


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

    items = []
    for op in data.get("operations", []):
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
            )
        )
    return TreeEditResponse(operations=items)


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
