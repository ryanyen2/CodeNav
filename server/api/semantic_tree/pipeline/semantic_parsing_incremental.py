"""Incremental semantic parsing: only run LLM for added/modified entities; reuse cache. Retrieve-then-generate: context = index chunks only."""

import logging
from typing import List, Optional, Dict, Any, Set, Tuple

from api.semantic_tree.models import (
    CodebaseSnapshot,
    CodeEntity,
    SemanticFeature,
    FunctionalArea,
)
from api.semantic_tree.state.models import SemanticCacheEntry
from api.semantic_tree.state.fingerprint import compute_entity_fingerprint
from api.semantic_tree.llm.prompt_loader import load_prompt, format_prompt, parse_solution_json
from api.semantic_tree.llm.completion import complete

logger = logging.getLogger(__name__)

RAG_TOP_K = 40
REMAINING_BATCH = 50


def _entity_key(e: CodeEntity) -> Tuple[str, str]:
    return (e.fpath, e.name)


def _chunks_to_context(hits: List[Tuple[CodeEntity, float, str]]) -> str:
    """Build prompt context from retrieved chunks only (retrieve-then-generate)."""
    parts = []
    for entity, _score, chunk_text in hits:
        ident = f"{entity.fpath}::{entity.name}"
        parts.append(f"### Entity: {entity.name} [{ident}]\n\n```\n{chunk_text}\n```")
    return "\n\n".join(parts)


def run_semantic_parsing_rag_incremental(
    snapshot: CodebaseSnapshot,
    store: Any,
    scope_id: str,
    areas: List[FunctionalArea],
    target_entities: List[CodeEntity],
    semantic_cache: Dict[str, SemanticCacheEntry],
    repo_name: str = "",
    repo_info: str = "",
    provider: str = "openai",
    model: Optional[str] = None,
) -> Tuple[List[SemanticFeature], Dict[str, SemanticCacheEntry]]:
    """
    Run semantic parsing for target_entities (added + modified) via LLM; reuse
    semantic_cache (keyed by content_hash) for all other entities. Returns
    (all_features, updated_cache). store: CodebaseIndex.
    """
    target_keys = {_entity_key(e) for e in target_entities}
    feature_by_entity_key: Dict[Tuple[str, str], SemanticFeature] = {}
    new_cache = dict(semantic_cache)
    entity_by_key = {_entity_key(e): e for e in snapshot.all_entities}

    # 1) From cache for non-target entities
    for e in snapshot.all_entities:
        if _entity_key(e) in target_keys:
            continue
        content_hash, _ = compute_entity_fingerprint(e)
        entry = semantic_cache.get(content_hash)
        if entry:
            feature_by_entity_key[_entity_key(e)] = SemanticFeature(
                entity_name=e.name, features=list(entry.features)
            )
        else:
            feature_by_entity_key[_entity_key(e)] = SemanticFeature(
                entity_name=e.name, features=[e.name.replace("_", " ")]
            )

    # 2) LLM for target_entities only
    if not target_entities:
        return list(feature_by_entity_key.values()), new_cache

    template = load_prompt("semantic_parsing")
    covered: Set[Tuple[str, str]] = set()

    for area in areas:
        if not area.name or not area.name.strip():
            continue
        hits = store.search_with_chunks(scope_id, snapshot, area.name.strip(), top_k=RAG_TOP_K)
        if not hits:
            continue
        entities = [e for e, _s, _c in hits if _entity_key(e) in target_keys]
        if not entities:
            continue
        for e in entities:
            covered.add(_entity_key(e))
        current_area_keys = {_entity_key(e) for e in entities}
        context = _chunks_to_context(hits)
        prompt = format_prompt(template, repo_name=repo_name, repo_info=repo_info)
        prompt += "\n\n" + context
        response = complete(prompt=prompt, provider=provider, model=model)
        data = parse_solution_json(response)
        if not isinstance(data, dict):
            logger.warning("semantic_parsing_rag_incremental response not dict for area %s", area.name)
            continue
        for entity_name, features in data.items():
            name = entity_name.strip()
            feat_list = [str(f) for f in features] if isinstance(features, list) else []
            for ek in current_area_keys:
                if entity_by_key[ek].name == name:
                    feature_by_entity_key[ek] = SemanticFeature(entity_name=entity_by_key[ek].name, features=feat_list)
                    ch, _ = compute_entity_fingerprint(entity_by_key[ek])
                    new_cache[ch] = SemanticCacheEntry(content_hash=ch, entity_name=entity_by_key[ek].name, features=feat_list)
                    break

    remaining_targets = [e for e in target_entities if _entity_key(e) not in covered]
    if remaining_targets:
        batch = remaining_targets[:REMAINING_BATCH]
        batch_keys = {_entity_key(e) for e in batch}
        batch_query = " ".join(e.name for e in batch[:20])
        hits = store.search_with_chunks(scope_id, snapshot, batch_query or "function class", top_k=RAG_TOP_K)
        hits = [(e, s, c) for e, s, c in hits if _entity_key(e) in batch_keys]
        if not hits:
            from api.semantic_tree.indexing.chunker import entity_chunks
            entity_to_chunk = {_entity_key(e): ct for e, ct in entity_chunks(snapshot)}
            hits = [(e, 0.0, entity_to_chunk.get(_entity_key(e), e.name)) for e in batch]
        context = _chunks_to_context(hits)
        prompt = format_prompt(template, repo_name=repo_name, repo_info=repo_info)
        prompt += "\n\n### Remaining entities\n" + context
        response = complete(prompt=prompt, provider=provider, model=model)
        data = parse_solution_json(response)
        if isinstance(data, dict):
            for entity_name, features in data.items():
                name = entity_name.strip()
                feat_list = [str(f) for f in features] if isinstance(features, list) else []
                for ek in batch_keys:
                    if entity_by_key[ek].name == name and ek not in feature_by_entity_key:
                        feature_by_entity_key[ek] = SemanticFeature(entity_name=entity_by_key[ek].name, features=feat_list)
                        ch, _ = compute_entity_fingerprint(entity_by_key[ek])
                        new_cache[ch] = SemanticCacheEntry(content_hash=ch, entity_name=entity_by_key[ek].name, features=feat_list)
                        break
        else:
            for e in batch:
                ek = _entity_key(e)
                if ek not in feature_by_entity_key:
                    feature_by_entity_key[ek] = SemanticFeature(
                        entity_name=e.name, features=[e.name.replace("_", " ")]
                    )

    return list(feature_by_entity_key.values()), new_cache
