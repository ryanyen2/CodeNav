"""
Entity-level code index using CocoIndex (SentenceTransformer + PostgreSQL/pgvector).
Replaces FAISS + external embedder: fast local embeddings, incremental updates, persistent index.
"""

import logging
import os
from contextlib import contextmanager
from typing import Any, List, Optional, Tuple

from api.semantic_tree.models import CodebaseSnapshot, CodeEntity

logger = logging.getLogger(__name__)

# Lazy init so cocoindex is only loaded when indexing is used
_cocoindex_init: bool = False
_table_name: str = "codenav_code_embeddings"


def _ensure_cocoindex_init() -> None:
    global _cocoindex_init
    if _cocoindex_init:
        return
    try:
        import cocoindex
        cocoindex.init()
        _cocoindex_init = True
    except Exception as e:
        logger.warning("cocoindex.init() failed: %s", e)
        raise RuntimeError(
            "CocoIndex not available. Set COCOINDEX_DATABASE_URL (e.g. postgresql://localhost/codoc) and install: pip install 'cocoindex[embeddings]' psycopg[binary] pgvector"
        ) from e


def _get_database_url() -> str:
    url = os.environ.get("COCOINDEX_DATABASE_URL") or os.environ.get("CODENAV_INDEX_DATABASE_URL")
    if not url:
        url = "postgresql://localhost/codoc"
        logger.info("Using default index database: %s (set COCOINDEX_DATABASE_URL to override)", url)
    return url


def _entity_key(e: CodeEntity) -> str:
    return f"{e.fpath}::{e.name}"


@contextmanager
def _connection():
    """Context manager for a single DB connection (pgvector)."""
    url = _get_database_url()
    try:
        from psycopg_pool import ConnectionPool
        from pgvector.psycopg import register_vector
    except ImportError as e:
        raise RuntimeError(
            "psycopg and pgvector required for code index. Install: pip install 'psycopg[binary]' pgvector"
        ) from e
    pool = ConnectionPool(url, min_size=0, max_size=2)
    try:
        with pool.connection() as conn:
            register_vector(conn)
            yield conn
    finally:
        pool.close()


def _ensure_table(conn: Any) -> None:
    """Create code_embeddings table with pgvector if not exists."""
    cur = conn.cursor()
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception:
        pass
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS codenav_code_embeddings (
            scope_id TEXT NOT NULL,
            fpath TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT 'function',
            chunk_text TEXT NOT NULL,
            embedding vector(384) NOT NULL,
            PRIMARY KEY (scope_id, fpath, entity_name)
        )
        """
    )
    conn.commit()
    cur.close()


# Module-level transform flow (cocoindex convention). Set after first init.
_embedding_flow: Any = None


def _get_embedding_flow():
    global _embedding_flow
    if _embedding_flow is not None:
        return _embedding_flow
    _ensure_cocoindex_init()
    import cocoindex
    import numpy as np
    from numpy.typing import NDArray

    @cocoindex.transform_flow()
    def code_to_embedding(
        text: cocoindex.DataSlice[str],
    ) -> cocoindex.DataSlice[NDArray[np.float32]]:
        return text.transform(
            cocoindex.functions.SentenceTransformerEmbed(
                model="sentence-transformers/all-MiniLM-L6-v2"
            )
        )

    _embedding_flow = code_to_embedding
    return _embedding_flow


def _embed_text(text: str) -> Optional[List[float]]:
    """Embed one string using CocoIndex SentenceTransformer (eval on transform_flow)."""
    flow = _get_embedding_flow()
    try:
        vec = flow.eval(text)
        if vec is None:
            return None
        if hasattr(vec, "tolist"):
            return vec.tolist()
        if hasattr(vec, "__iter__") and not isinstance(vec, str):
            return list(vec)
        return None
    except Exception as e:
        logger.warning("Embedding failed: %s", e)
        return None


class CodebaseIndex:
    """
    Entity-level code index backed by CocoIndex (SentenceTransformer) and PostgreSQL/pgvector.
    Replaces SemanticVectorStore: no FAISS, no external embedder. Scope by scope_id (e.g. root_dir).
    """

    def __init__(self) -> None:
        self._table = _table_name

    def add_entities(
        self,
        scope_id: str,
        entities_with_chunks: List[Tuple[CodeEntity, str]],
    ) -> int:
        """Embed and upsert entities into the index. Returns number of entities added."""
        _ensure_cocoindex_init()
        added = 0
        with _connection() as conn:
            _ensure_table(conn)
            cur = conn.cursor()
            for entity, chunk_text in entities_with_chunks:
                emb = _embed_text(chunk_text)
                if emb is None:
                    continue
                # pgvector expects a string format for vector
                vec_str = "[" + ",".join(str(x) for x in emb) + "]"
                entity_type = getattr(entity, "entity_type", "function") or "function"
                cur.execute(
                    """
                    INSERT INTO codenav_code_embeddings
                    (scope_id, fpath, entity_name, entity_type, chunk_text, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (scope_id, fpath, entity_name)
                    DO UPDATE SET entity_type = EXCLUDED.entity_type,
                                  chunk_text = EXCLUDED.chunk_text,
                                  embedding = EXCLUDED.embedding
                    """,
                    (scope_id, entity.fpath, entity.name, entity_type, chunk_text, vec_str),
                )
                added += 1
            conn.commit()
            cur.close()
        if added:
            logger.info("CodebaseIndex: added %s entities to scope %s", added, scope_id)
        return added

    def delete_entities(self, scope_id: str, entity_keys: List[str]) -> None:
        """Remove entities by scope and entity keys (fpath::name)."""
        if not entity_keys:
            return
        with _connection() as conn:
            cur = conn.cursor()
            for key in entity_keys:
                if "::" in key:
                    fpath, entity_name = key.split("::", 1)
                    cur.execute(
                        "DELETE FROM codenav_code_embeddings WHERE scope_id = %s AND fpath = %s AND entity_name = %s",
                        (scope_id, fpath, entity_name),
                    )
            conn.commit()
            cur.close()
        logger.info("CodebaseIndex: deleted %s entities from scope %s", len(entity_keys), scope_id)

    def clear_scope(self, scope_id: str) -> None:
        """Remove all rows for this scope (full rebuild)."""
        with _connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM codenav_code_embeddings WHERE scope_id = %s", (scope_id,))
            conn.commit()
            cur.close()
        logger.info("CodebaseIndex: cleared scope %s", scope_id)

    def search(
        self,
        scope_id: str,
        snapshot: CodebaseSnapshot,
        query: str,
        top_k: int = 10,
    ) -> List[Tuple[CodeEntity, float]]:
        """Return (entity, similarity_score) for top_k nearest. Uses snapshot to map fpath+name -> CodeEntity."""
        entity_by_key = {(e.fpath, e.name): e for e in snapshot.all_entities}
        rows = self._search_raw(scope_id, query, top_k)
        result: List[Tuple[CodeEntity, float]] = []
        for fpath, entity_name, entity_type, score in rows:
            key = (fpath, entity_name)
            if key in entity_by_key:
                result.append((entity_by_key[key], float(score)))
        return result

    def search_raw(
        self,
        scope_id: str,
        query: str,
        top_k: int = 10,
    ) -> List[Tuple[str, str, str, float]]:
        """Return (fpath, entity_name, entity_type, score) for top_k. For /search route (no snapshot)."""
        return self._search_raw(scope_id, query, top_k)

    def _search_raw(
        self,
        scope_id: str,
        query: str,
        top_k: int,
    ) -> List[Tuple[str, str, str, float]]:
        _ensure_cocoindex_init()
        query_emb = _embed_text(query)
        if not query_emb:
            return []
        vec_str = "[" + ",".join(str(x) for x in query_emb) + "]"
        with _connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT fpath, entity_name, entity_type, 1 - (embedding <=> %s::vector) AS score
                FROM codenav_code_embeddings
                WHERE scope_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (vec_str, scope_id, vec_str, top_k),
            )
            rows = cur.fetchall()
            cur.close()
        return [(r[0], r[1], r[2], float(r[3])) for r in rows]

    def size(self, scope_id: str) -> int:
        """Return number of entities indexed for this scope."""
        with _connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM codenav_code_embeddings WHERE scope_id = %s", (scope_id,))
            row = cur.fetchone()
            cur.close()
            return row[0] if row else 0


def get_codebase_index() -> CodebaseIndex:
    """Return the shared codebase index (Postgres-backed)."""
    return CodebaseIndex()
