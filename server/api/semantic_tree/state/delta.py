"""Entity delta: added, removed, modified, renamed, unchanged (content-hash + difflib similarity)."""

import difflib
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from api.semantic_tree.models import CodeEntity
from api.semantic_tree.state.models import EntityFingerprint
from api.semantic_tree.state.fingerprint import (
    compute_entity_fingerprint,
    get_entity_content_sample,
)


def _entity_key(e: CodeEntity) -> str:
    return f"{e.fpath}::{e.name}"


@dataclass
class EntityDelta:
    """Result of comparing old vs new entity fingerprints."""
    added: List[CodeEntity] = field(default_factory=list)
    removed: List[Tuple[str, EntityFingerprint]] = field(default_factory=list)  # entity_key, old_fp
    modified: List[CodeEntity] = field(default_factory=list)
    renamed: List[Tuple[CodeEntity, str]] = field(default_factory=list)  # new_entity, old_entity_key
    unchanged: List[CodeEntity] = field(default_factory=list)


def compute_entity_delta(
    old_fingerprints: Dict[str, EntityFingerprint],
    new_entities: List[CodeEntity],
) -> EntityDelta:
    """
    Compare old fingerprints to new entities. Uses content_hash to detect renames:
    if an entity disappeared (removed) and another appeared (added) with the same content_hash,
    treat as renamed.
    """
    new_by_key: Dict[str, CodeEntity] = {_entity_key(e): e for e in new_entities}
    new_content_hashes: Dict[str, str] = {}  # content_hash -> entity_key
    for k, e in new_by_key.items():
        ch, _ = compute_entity_fingerprint(e)
        new_content_hashes[ch] = k

    delta = EntityDelta()
    used_new_keys: Set[str] = set()

    # Removed: in old, not in new (by key)
    for old_key, old_fp in old_fingerprints.items():
        if old_key not in new_by_key:
            delta.removed.append((old_key, old_fp))

    # Match added/renamed: added entity with content_hash matching a removed -> renamed
    removed_by_content: Dict[str, Tuple[str, EntityFingerprint]] = {
        fp.content_hash: (ek, fp) for ek, fp in delta.removed
    }

    for new_key, new_entity in new_by_key.items():
        content_hash, sig_hash = compute_entity_fingerprint(new_entity)
        if new_key in old_fingerprints:
            old_fp = old_fingerprints[new_key]
            if old_fp.content_hash == content_hash and old_fp.signature_hash == sig_hash:
                delta.unchanged.append(new_entity)
                used_new_keys.add(new_key)
            elif old_fp.content_sample and old_fp.content_sample.strip():
                # Robust diff: use difflib similarity so minor edits don't force re-index
                new_sample = get_entity_content_sample(new_entity)
                ratio = difflib.SequenceMatcher(None, old_fp.content_sample, new_sample).ratio()
                if ratio >= 0.95:
                    delta.unchanged.append(new_entity)
                    used_new_keys.add(new_key)
                else:
                    delta.modified.append(new_entity)
                    used_new_keys.add(new_key)
            else:
                delta.modified.append(new_entity)
                used_new_keys.add(new_key)
        elif content_hash in removed_by_content:
            old_ek, _ = removed_by_content[content_hash]
            delta.renamed.append((new_entity, old_ek))
            used_new_keys.add(new_key)
            # Remove from delta.removed so we don't double-count
            delta.removed = [(ek, fp) for ek, fp in delta.removed if ek != old_ek]
        else:
            delta.added.append(new_entity)
            used_new_keys.add(new_key)

    return delta
