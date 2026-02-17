"""Incremental forward sync: code delta → tree update (reuse cache and index where possible)."""

import logging
from pathlib import Path
from typing import Optional, Tuple, Any, Dict

from api.semantic_tree.models import (
    CodebaseSnapshot,
    SemanticTree,
    FunctionalArea,
    SemanticFeature,
    HierarchyMapping,
)
from api.semantic_tree.state.models import SyncState, EntityFingerprint
from api.semantic_tree.state.fingerprint import compute_entity_fingerprint, compute_file_fingerprint
from api.semantic_tree.state.persistence import load_sync_state, save_sync_state
from api.semantic_tree.state.delta import compute_entity_delta
from api.semantic_tree.indexing.chunker import entity_chunks
from api.semantic_tree.indexing.vector_store import SemanticVectorStore, _entity_key
from api.semantic_tree.pipeline.domain_discovery import run_domain_discovery
from api.semantic_tree.pipeline.semantic_parsing_incremental import run_semantic_parsing_rag_incremental
from api.semantic_tree.pipeline.hierarchical_construction import run_hierarchical_construction
from api.semantic_tree.pipeline.tree_assembly import assemble_tree
from api.semantic_tree.pipeline.tree_validation import validate_tree
from api.semantic_tree.output.tree_serializer import tree_to_markdown
from api.semantic_tree.logging import log_sync

logger = logging.getLogger(__name__)


def incremental_forward(
    snapshot: CodebaseSnapshot,
    index_path: str,
    old_state: Optional[SyncState],
    embedder: Any,
    repo_name: str = "",
    provider: str = "openai",
    model: Optional[str] = None,
) -> Tuple[Optional[SemanticTree], SyncState, dict, Optional[dict], bool, Optional[dict]]:
    """
    Run forward sync (code → tree). If old_state exists and matches root_dir, use
    incremental path (delta, cached semantic, incremental index) or patch path when possible.
    When delta is empty (no code change), skips LLM/index and returns (None, new_state, ...);
    caller should use new_state.last_tree_md as the tree output (avoids redundant pipeline).
    Returns (tree or None, new_state, timing_dict, delta_summary or None, is_patch_based, patch_summary or None).
    Patch path: no embeddings, no FAISS, no domain/hierarchy LLM; in-place tree patch only.
    """
    root_dir = snapshot.root_dir
    all_chunks = entity_chunks(snapshot)

    # New fingerprints
    new_entity_fps: dict[str, EntityFingerprint] = {}
    for e in snapshot.all_entities:
        ch, sh = compute_entity_fingerprint(e)
        new_entity_fps[_entity_key(e)] = EntityFingerprint(content_hash=ch, signature_hash=sh)

    # File fingerprints (for "did file set change")
    new_file_fps: dict[str, str] = {}
    for f in snapshot.files:
        path = Path(root_dir) / f.fpath
        if path.is_file():
            try:
                new_file_fps[f.fpath] = compute_file_fingerprint(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

    if old_state is None or old_state.root_dir != root_dir:
        # Cold start: full pipeline (caller does extraction, we do rest; state built at end)
        logger.info("[SYNC] full pipeline (no state or root_dir mismatch) | entities=%s", len(snapshot.all_entities))
        from api.semantic_tree.pipeline.semantic_parsing import run_semantic_parsing_rag
        store = SemanticVectorStore()
        store.add_entities(all_chunks, embedder)
        store.save(index_path)
        areas = run_domain_discovery(snapshot, repo_name=repo_name, repo_info=None, provider=provider, model=model)
        features = run_semantic_parsing_rag(
            snapshot, store=store, embedder=embedder, areas=areas,
            repo_name=repo_name, provider=provider, model=model,
        )
        group_to_entities = {f.fpath: [e.name for e in f.entities] for f in snapshot.files}
        hierarchy = run_hierarchical_construction(areas, group_to_entities, provider=provider, model=model)
        tree = assemble_tree(snapshot, features, hierarchy, include_deps=True)
        for w in validate_tree(tree, snapshot):
            logger.warning("[CODENAV] tree validation: %s", w)
        tree_md = tree_to_markdown(tree)
        from api.semantic_tree.state.models import SemanticCacheEntry
        cache_entries = {}
        for e in snapshot.all_entities:
            ch, _ = compute_entity_fingerprint(e)
            sf = next((x for x in features if x.entity_name == e.name), None)
            if sf:
                cache_entries[ch] = SemanticCacheEntry(content_hash=ch, entity_name=e.name, features=list(sf.features))
        new_state = SyncState(
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
        save_sync_state(new_state, root_dir)
        log_sync("full", len(snapshot.all_entities), None, "full", "full")
        return tree, new_state, {}, None, False, None

    # Incremental path
    delta = compute_entity_delta(old_state.entity_fingerprints, snapshot.all_entities)
    delta_summary = {
        "added": len(delta.added),
        "removed": len(delta.removed),
        "modified": len(delta.modified),
        "renamed": len(delta.renamed),
        "unchanged": len(delta.unchanged),
    }
    target_count = len(delta.added) + len(delta.modified)

    # No code change: keep existing tree (e.g. from apply_tree_edit), skip LLM and index
    if not delta.added and not delta.removed and not delta.modified:
        logger.info(
            "[SYNC] incremental | delta empty (unchanged=%s) | reusing last_tree_md (no LLM/index)",
            len(delta.unchanged),
        )
        new_state = SyncState(
            entity_fingerprints=new_entity_fps,
            file_fingerprints=new_file_fps,
            semantic_cache=old_state.semantic_cache,
            domain_areas=old_state.domain_areas,
            hierarchy_cache=old_state.hierarchy_cache,
            tree_version=old_state.tree_version,
            code_version=old_state.code_version,
            last_sync_direction=old_state.last_sync_direction,
            last_tree_md=old_state.last_tree_md,
            root_dir=root_dir,
            index_path=index_path,
        )
        save_sync_state(new_state, root_dir)
        log_sync("incremental", len(snapshot.all_entities), delta_summary, "reuse", "reuse")
        return None, new_state, {}, delta_summary, False, None

    # Patch path: in-place tree update, no embeddings/FAISS/domain/hierarchy LLM
    if old_state.last_tree_md and old_state.hierarchy_cache:
        from api.semantic_tree.output.tree_parser import parse_tree_markdown
        from api.semantic_tree.pipeline.tree_patcher import patch_tree, build_node_index
        from api.semantic_tree.pipeline.targeted_semantic_update import update_features_for_new_entities

        logger.info(
            "[SYNC] patch path | delta added=%s removed=%s modified=%s renamed=%s (no index/embedding)",
            len(delta.added), len(delta.removed), len(delta.modified), len(delta.renamed),
        )
        tree = parse_tree_markdown(old_state.last_tree_md)
        patch_result = patch_tree(
            tree, delta, snapshot,
            old_state.hierarchy_cache,
            old_state.semantic_cache,
        )
        # Refresh feature text for new entities (no cache hit) and for modified entities (so codoc reflects current behavior)
        entity_keys_to_refresh = list(patch_result.needs_feature_update) + [
            nid for nid in patch_result.modified_nodes
            if "::" in nid
        ]  # only leaf nodes have fpath::entity_name
        if entity_keys_to_refresh:
            by_id, _ = build_node_index(tree)
            updated_cache = update_features_for_new_entities(
                entity_keys_to_refresh,
                snapshot,
                tree,
                by_id,
                old_state.semantic_cache,
                repo_name=repo_name,
                provider=provider,
                model=model,
            )
        else:
            updated_cache = old_state.semantic_cache
        tree_md = tree_to_markdown(tree)
        new_state = SyncState(
            entity_fingerprints=new_entity_fps,
            file_fingerprints=new_file_fps,
            semantic_cache=updated_cache,
            domain_areas=old_state.domain_areas,
            hierarchy_cache=old_state.hierarchy_cache,
            tree_version=old_state.tree_version,
            code_version=old_state.code_version + 1,
            last_sync_direction="forward",
            last_tree_md=tree_md,
            root_dir=root_dir,
            index_path=index_path,
        )
        save_sync_state(new_state, root_dir)
        patch_summary = {
            "modified": len(patch_result.modified_nodes),
            "added": len(patch_result.added_nodes),
            "removed": len(patch_result.removed_nodes),
            "needs_feature_update": len(patch_result.needs_feature_update),
        }
        log_sync("patch", len(snapshot.all_entities), delta_summary, "none", "targeted" if entity_keys_to_refresh else "none")
        return tree, new_state, {}, delta_summary, True, patch_summary

    # Full incremental rebuild path (embedding + RAG + hierarchy)
    logger.info(
        "[SYNC] incremental | delta added=%s removed=%s modified=%s unchanged=%s | index_only=%s entities (no full reindex)",
        len(delta.added),
        len(delta.removed),
        len(delta.modified),
        len(delta.unchanged),
        target_count,
    )
    store = SemanticVectorStore()
    store.load(index_path)

    # Index: tombstones for removed; add for added+modified; rebuild if needed
    if delta.removed:
        removed_keys = [ek for ek, _ in delta.removed]
        store.mark_tombstones(removed_keys)
    did_rebuild = False
    if delta.added or delta.modified:
        target_keys = {_entity_key(e) for e in (delta.added + delta.modified)}
        to_add_chunks = [(e, ct) for e, ct in all_chunks if _entity_key(e) in target_keys]
        if to_add_chunks:
            store.add_entities_incremental(to_add_chunks, embedder)
            store.save(index_path)
    if store.needs_rebuild:
        store.rebuild(all_chunks, embedder)
        store.save(index_path)
        did_rebuild = True

    # Domain: reuse if file set unchanged
    old_fpaths = set(old_state.file_fingerprints.keys())
    new_fpaths = set(new_file_fps.keys())
    if old_fpaths == new_fpaths:
        areas = [FunctionalArea(name=a["name"]) for a in old_state.domain_areas]
    else:
        areas = run_domain_discovery(snapshot, repo_name=repo_name, repo_info=None, provider=provider, model=model)

    # Semantic: incremental (target = added + modified)
    target_entities = delta.added + delta.modified
    features, updated_cache = run_semantic_parsing_rag_incremental(
        snapshot,
        store=store,
        embedder=embedder,
        areas=areas,
        target_entities=target_entities,
        semantic_cache=old_state.semantic_cache,
        repo_name=repo_name,
        provider=provider,
        model=model,
    )

    # Hierarchy: re-run (could pass old as hint later)
    group_to_entities = {f.fpath: [e.name for e in f.entities] for f in snapshot.files}
    hierarchy = run_hierarchical_construction(areas, group_to_entities, provider=provider, model=model)

    tree = assemble_tree(snapshot, features, hierarchy, include_deps=True)
    for w in validate_tree(tree, snapshot):
        logger.warning("[CODENAV] tree validation: %s", w)
    tree_md = tree_to_markdown(tree)

    new_state = SyncState(
        entity_fingerprints=new_entity_fps,
        file_fingerprints=new_file_fps,
        semantic_cache=updated_cache,
        domain_areas=[{"name": a.name} for a in areas],
        hierarchy_cache=[h.model_dump() if hasattr(h, "model_dump") else h for h in hierarchy],
        tree_version=old_state.tree_version,
        code_version=old_state.code_version + 1,
        last_sync_direction="forward",
        last_tree_md=tree_md,
        root_dir=root_dir,
        index_path=index_path,
    )
    save_sync_state(new_state, root_dir)

    index_action = "rebuild" if did_rebuild else f"incremental({target_count})"
    log_sync("incremental", len(snapshot.all_entities), delta_summary, index_action, f"incremental({target_count})")

    timing = {}
    return tree, new_state, timing, delta_summary, False, None
