"""FAISS index for entity-level semantic search (references api.tools.embedder)."""

import logging
import pickle
from pathlib import Path
from typing import List, Optional, Set, Tuple, Any

import numpy as np

from api.semantic_tree.models import CodeEntity

logger = logging.getLogger(__name__)


def _entity_key(e: CodeEntity) -> str:
    return f"{e.fpath}::{e.name}"

try:
    import faiss
except ImportError:
    faiss = None


def _embed_single(embedder: Any, text: str) -> Optional[List[float]]:
    """Call embedder with one string; return embedding list or None."""
    try:
        out = embedder(input=text)
        if out and getattr(out, "data", None) and len(out.data) > 0:
            emb = out.data[0].embedding
            if hasattr(emb, "tolist"):
                return emb.tolist()
            return list(emb)
    except Exception as e:
        logger.warning("Embedding failed for chunk: %s", e)
    return None


class SemanticVectorStore:
    """
    Entity-level FAISS index. Requires embedder from api.tools.embedder.get_embedder().
    No default embedder; caller must pass it so keys are configured before use.
    """

    def __init__(self) -> None:
        self._index: Optional[Any] = None
        self._entities: List[CodeEntity] = []
        self._entity_keys: List[str] = []  # parallel to _entities for tombstone lookup
        self._dim: Optional[int] = None
        self._tombstones: Set[str] = set()  # entity keys removed from codebase

    def add_entities(
        self,
        entities_with_chunks: List[Tuple[CodeEntity, str]],
        embedder: Any,
    ) -> None:
        """
        Embed each chunk and add to FAISS index. embedder must be from get_embedder().
        Calls embedder one string at a time (Ollama-compatible).
        """
        if faiss is None:
            raise RuntimeError("faiss-cpu is not installed")

        vectors: List[List[float]] = []
        entities: List[CodeEntity] = []

        for entity, chunk_text in entities_with_chunks:
            emb = _embed_single(embedder, chunk_text)
            if emb is None:
                continue
            if self._dim is None:
                self._dim = len(emb)
            if len(emb) != self._dim:
                logger.warning("Skip entity %s: embedding dim %s != %s", entity.name, len(emb), self._dim)
                continue
            vectors.append(emb)
            entities.append(entity)

        if not vectors:
            logger.warning("No valid embeddings produced")
            return

        matrix = np.array(vectors, dtype=np.float32)
        if self._index is None:
            self._index = faiss.IndexFlatL2(self._dim)
            self._entities = []
            self._entity_keys = []
        self._index.add(matrix)
        self._entities.extend(entities)
        self._entity_keys.extend(_entity_key(e) for e in entities)
        logger.info("Added %s entities to semantic vector store", len(entities))

    def add_entities_incremental(
        self,
        entities_with_chunks: List[Tuple[CodeEntity, str]],
        embedder: Any,
    ) -> None:
        """Append new or updated entities without rebuilding. Same as add_entities when index exists."""
        self.add_entities(entities_with_chunks, embedder)

    def mark_tombstones(self, entity_keys: List[str]) -> None:
        """Mark these entity keys as removed (excluded from search). Rebuild when tombstone ratio > 30%."""
        self._tombstones.update(entity_keys)
        logger.info("Marked %s tombstones (total %s)", len(entity_keys), len(self._tombstones))

    @property
    def needs_rebuild(self) -> bool:
        """True when tombstone ratio > 30% so full rebuild is cheaper."""
        n = len(self._entities)
        if n == 0:
            return False
        return len(self._tombstones) / n > 0.3

    def rebuild(
        self,
        all_chunks: List[Tuple[CodeEntity, str]],
        embedder: Any,
    ) -> None:
        """Full rebuild: new index from all_chunks, clear tombstones."""
        self._index = None
        self._entities = []
        self._entity_keys = []
        self._tombstones = set()
        self.add_entities(all_chunks, embedder)
        logger.info("Rebuilt semantic vector store (%s entities)", self.size)

    def search(
        self,
        query: str,
        embedder: Any,
        top_k: int = 10,
    ) -> List[Tuple[CodeEntity, float]]:
        """Return (entity, L2 distance) for top_k nearest. Smaller distance = more similar."""
        if self._index is None or not self._entities:
            return []

        emb = _embed_single(embedder, query)
        if emb is None or len(emb) != self._dim:
            return []

        q = np.array([emb], dtype=np.float32)
        # Request extra to allow for skipping tombstoned
        k_request = min(top_k * 3, len(self._entities)) if self._tombstones else min(top_k, len(self._entities))
        distances, indices = self._index.search(q, k_request)
        result: List[Tuple[CodeEntity, float]] = []
        for i, idx in enumerate(indices[0]):
            if idx < 0 or idx >= len(self._entities):
                continue
            if self._entity_keys and idx < len(self._entity_keys) and self._entity_keys[idx] in self._tombstones:
                continue
            result.append((self._entities[idx], float(distances[0][i])))
            if len(result) >= top_k:
                break
        return result

    def save(self, path: str) -> None:
        """Persist index and entity list to directory path."""
        if self._index is None:
            raise ValueError("No index to save")
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(p / "index.faiss"))
        with open(p / "entities.pkl", "wb") as f:
            pickle.dump(self._entities, f)
        with open(p / "dim.txt", "w") as f:
            f.write(str(self._dim))

    def load(self, path: str) -> None:
        """Load index and entity list from directory."""
        if faiss is None:
            raise RuntimeError("faiss-cpu is not installed")
        p = Path(path)
        self._index = faiss.read_index(str(p / "index.faiss"))
        with open(p / "entities.pkl", "rb") as f:
            self._entities = pickle.load(f)
        self._entity_keys = [_entity_key(e) for e in self._entities]
        self._tombstones = set()
        with open(p / "dim.txt") as f:
            self._dim = int(f.read().strip())

    @property
    def size(self) -> int:
        return len(self._entities) if self._entities else 0
