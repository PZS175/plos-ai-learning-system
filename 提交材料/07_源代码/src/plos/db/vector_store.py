"""ChromaDB vector store wrapper.

Persists embeddings locally and supports add/query/delete operations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger
from ..utils.paths import get_chroma_dir

logger = get_logger("db.vector_store")


class VectorStore:
    """Local ChromaDB vector store."""

    def __init__(self, persist_dir: Optional[Path] = None, collection_name: str = "documents"):
        self.persist_dir = persist_dir or get_chroma_dir()
        self.collection_name = collection_name
        self._client: Optional[Any] = None
        self._collection: Optional[Any] = None
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        self.persist_dir.mkdir(parents=True, exist_ok=True)

    def _init(self) -> None:
        if self._client is not None:
            return
        try:
            import chromadb
            from chromadb.config import Settings
            self._client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=Settings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(name=self.collection_name)
            logger.info("ChromaDB initialized: %s", self.persist_dir)
        except Exception as e:
            logger.error("Failed to initialize ChromaDB: %s", e)
            raise

    def add(
        self,
        ids: List[str],
        documents: List[str],
        embeddings: List[List[float]],
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self._init()
        if self._collection is None:
            raise RuntimeError("ChromaDB collection not initialized")
        try:
            self._collection.add(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            logger.debug("Added %d vectors to ChromaDB", len(ids))
        except Exception as e:
            logger.error("Failed to add vectors: %s", e)
            raise

    def query(
        self,
        query_embedding: List[float],
        top_k: int = 4,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        self._init()
        if self._collection is None:
            raise RuntimeError("ChromaDB collection not initialized")
        try:
            # 带上向量本身：调用方要拿它算真实余弦相似度
            # （集合用的是默认 L2 距离，向量不是单位向量时该数值没有直观含义）
            try:
                results = self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=top_k,
                    where=where,
                    include=["documents", "metadatas", "distances", "embeddings"],
                )
            except Exception:
                # 个别 Chroma 版本不支持取回向量：退回只取距离，检索本身不能因此不可用
                results = self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=top_k,
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
            return self._format_results(results)
        except Exception as e:
            logger.error("Failed to query vectors: %s", e)
            raise

    def _format_results(self, results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Convert ChromaDB query result to a list of flat dicts."""
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        raw_embeddings = results.get("embeddings")
        embeddings = raw_embeddings[0] if raw_embeddings is not None and len(raw_embeddings) else []
        formatted: List[Dict[str, Any]] = []
        for i, vid in enumerate(ids):
            formatted.append({
                "id": vid,
                "document": documents[i] if i < len(documents) else "",
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "distance": distances[i] if i < len(distances) else 0.0,
                "embedding": embeddings[i] if i < len(embeddings) else None,
            })
        return formatted

    def delete(self, ids: List[str]) -> None:
        self._init()
        if self._collection is None:
            return
        try:
            self._collection.delete(ids=ids)
            logger.debug("Deleted %d vectors from ChromaDB", len(ids))
        except Exception as e:
            logger.error("Failed to delete vectors: %s", e)
            raise

    def count(self) -> int:
        self._init()
        if self._collection is None:
            return 0
        return self._collection.count()

    def reset(self) -> None:
        self._init()
        if self._client is None:
            return
        try:
            self._client.delete_collection(name=self.collection_name)
            self._collection = self._client.get_or_create_collection(name=self.collection_name)
            logger.warning("ChromaDB collection '%s' reset", self.collection_name)
        except Exception as e:
            logger.error("Failed to reset ChromaDB collection: %s", e)
            raise
