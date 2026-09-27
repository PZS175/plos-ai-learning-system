"""RAG（检索增强生成）知识库服务。

负责文档解析、文本分块、向量入库、语义检索。业务层不依赖 Qt。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..ai import ModelManager
from ..ai.multimodal import DocumentParser
from ..core.models import SearchResult
from ..db import Database, VectorStore, get_db
from ..utils.exceptions import DocumentParseError, ModelConnectionError
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.rag_service")


def _similarity(query_vector: Sequence[float], stored, distance: float) -> float:
    """把向量库的距离换算成 0~1 的「相关度」。

    Chroma 集合用的是默认 L2 距离，而 nomic-embed-text 返回的向量不是单位向量：
    实测同一份讲义的距离能到 300 以上，沿用 ``1 - distance`` 会在界面上显示成
    「相关度：-317.517」，等于把唯一命中的那一段标成最不相关。
    这里优先算真实余弦相似度（更符合直觉），拿不到向量时退化为单调压缩。
    """
    if stored is not None and query_vector is not None and len(stored) == len(query_vector):
        dot = norm_q = norm_s = 0.0
        for a, b in zip(query_vector, stored):
            a = float(a)
            b = float(b)
            dot += a * b
            norm_q += a * a
            norm_s += b * b
        if norm_q > 0.0 and norm_s > 0.0:
            cosine = dot / (math.sqrt(norm_q) * math.sqrt(norm_s))
            return max(0.0, min(1.0, cosine))
    return 1.0 / (1.0 + max(0.0, float(distance)))


class RAGService:
    """本地知识库 RAG 服务。"""

    def __init__(
        self,
        model_manager: ModelManager,
        vector_store: Optional[VectorStore] = None,
        db: Optional[Database] = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        user_service: Optional[UserService] = None,
    ):
        self.model_manager = model_manager
        self.vector_store = vector_store or VectorStore()
        self.db = db or get_db()
        self.parser = DocumentParser()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.user_service = user_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def add_document(
        self, file_path: Path, user_id: Optional[int] = None
    ) -> int:
        """解析文档、分块、生成向量并入库。返回 document_id。

        对 PDF 教材优先使用目录（TOC）智能分块，保留章节结构与层级；
        非 PDF 或无目录时降级为普通字符切分。
        """
        uid = self._user_id(user_id)
        if not self.model_manager.is_embedding_available():
            raise ModelConnectionError(
                self.model_manager.backend_type.value,
                detail="嵌入模型不可用，无法建立知识库。",
            )

        if not DocumentParser.is_supported(file_path):
            raise DocumentParseError(f"不支持的文件类型：{file_path.suffix}")

        try:
            _, _, page_count = self.parser.parse(file_path)
        except DocumentParseError:
            raise
        except Exception as e:
            logger.error("Document parse failed: %s", e)
            raise DocumentParseError(f"文档解析失败：{e}") from e

        # 保存文档记录
        document_id = self.db.insert(
            "INSERT INTO documents (user_id, filename, file_path, file_type, page_count) VALUES (?, ?, ?, ?, ?)",
            (uid, file_path.name, str(file_path), file_path.suffix.lower(), page_count),
        )

        # 分块策略：PDF 优先按目录章节切分
        if file_path.suffix.lower() == ".pdf":
            try:
                chunks = self.parser.chunk_pdf_by_toc(
                    file_path, self.chunk_size, self.chunk_overlap
                )
            except Exception as e:
                logger.warning("TOC chunking failed, fallback to plain chunking: %s", e)
                text, _, _ = self.parser.parse(file_path)
                chunks = [
                    {
                        "content": c,
                        "chapter": "",
                        "level": 0,
                        "page_num": 0,
                    }
                    for c in self.parser.chunk_text(
                        text, self.chunk_size, self.chunk_overlap
                    )
                ]
        else:
            text, _, _ = self.parser.parse(file_path)
            chunks = [
                {
                    "content": c,
                    "chapter": "",
                    "level": 0,
                    "page_num": 0,
                }
                for c in self.parser.chunk_text(
                    text, self.chunk_size, self.chunk_overlap
                )
            ]

        logger.info(
            "Document %s parsed into %d chunks", file_path.name, len(chunks)
        )

        if not chunks:
            return document_id

        chunk_texts = [c["content"] for c in chunks]
        try:
            embeddings = self.model_manager.embed(chunk_texts)
        except Exception as e:
            logger.error("Embedding generation failed: %s", e)
            raise ModelConnectionError(
                self.model_manager.backend_type.value,
                detail=f"嵌入生成失败：{e}",
            ) from e

        ids = [f"doc-{document_id}-chunk-{i}" for i in range(len(chunks))]
        metadatas: List[Dict[str, Any]] = [
            {
                "document_id": document_id,
                "filename": file_path.name,
                "chunk_index": i,
                "page_num": chunk.get("page_num", 0),
                "chapter": chunk.get("chapter", ""),
                "level": chunk.get("level", 0),
            }
            for i, chunk in enumerate(chunks)
        ]

        self.vector_store.add(
            ids=ids,
            documents=chunk_texts,
            embeddings=[e.embedding for e in embeddings],
            metadatas=metadatas,
        )

        # 写入 chunks 表，保存向量 id 引用与章节信息
        for i, chunk in enumerate(chunks):
            self.db.insert(
                "INSERT INTO chunks (user_id, document_id, chunk_index, content, page_num, chapter, level, vector_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uid,
                    document_id,
                    i,
                    chunk["content"],
                    chunk.get("page_num", 0),
                    chunk.get("chapter", ""),
                    chunk.get("level", 0),
                    ids[i],
                ),
            )

        logger.info("Added document id=%d user_id=%d to vector store", document_id, uid)
        return document_id

    def search(self, query: str, top_k: int = 4) -> List[SearchResult]:
        """语义检索知识库。"""
        if not self.model_manager.is_embedding_available():
            raise ModelConnectionError(
                self.model_manager.backend_type.value,
                detail="嵌入模型不可用，无法检索知识库。",
            )

        try:
            query_embedding = self.model_manager.embed_query(query)
        except Exception as e:
            logger.error("Query embedding failed: %s", e)
            raise ModelConnectionError(
                self.model_manager.backend_type.value,
                detail=f"查询嵌入失败：{e}",
            ) from e

        query_vector = query_embedding.embedding
        try:
            raw_results = self.vector_store.query(
                query_embedding=query_vector,
                top_k=top_k,
            )
        except Exception as e:
            logger.error("Vector search failed: %s", e)
            raise

        results: List[SearchResult] = []
        for item in raw_results:
            meta = item.get("metadata", {})
            results.append(
                SearchResult(
                    content=item.get("document", ""),
                    source=meta.get("filename", "未知来源"),
                    score=_similarity(
                        query_vector,
                        item.get("embedding"),
                        float(item.get("distance", 0.0)),
                    ),
                    metadata=meta,
                )
            )
        return results

    def list_documents(self, user_id: Optional[int] = None) -> List[dict]:
        uid = self._user_id(user_id)
        return self.db.fetchall(
            "SELECT id, filename, file_type, page_count, is_favorite, created_at FROM documents WHERE user_id = ? ORDER BY created_at DESC",
            (uid,),
        )

    def toggle_favorite(self, document_id: int, user_id: Optional[int] = None) -> bool:
        """切换文档收藏状态，返回新的收藏状态。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT is_favorite FROM documents WHERE id = ? AND user_id = ?",
            (document_id, uid),
        )
        if row is None:
            raise ValueError(f"文档不存在：{document_id}")
        new_state = 0 if row.get("is_favorite", 0) else 1
        self.db.execute(
            "UPDATE documents SET is_favorite = ? WHERE id = ? AND user_id = ?",
            (new_state, document_id, uid),
        )
        return bool(new_state)

    def list_favorites(self, user_id: Optional[int] = None) -> List[dict]:
        """列出当前用户收藏的文档。"""
        uid = self._user_id(user_id)
        return self.db.fetchall(
            "SELECT id, filename, file_type, page_count, created_at FROM documents WHERE user_id = ? AND is_favorite = 1 ORDER BY created_at DESC",
            (uid,),
        )

    def delete_document(self, document_id: int, user_id: Optional[int] = None) -> None:
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT vector_id FROM chunks WHERE document_id = ? AND user_id = ?",
            (document_id, uid),
        )
        vector_ids = [row["vector_id"] for row in rows if row["vector_id"]]
        if vector_ids:
            self.vector_store.delete(vector_ids)
        self.db.execute(
            "DELETE FROM chunks WHERE document_id = ? AND user_id = ?",
            (document_id, uid),
        )
        self.db.execute(
            "DELETE FROM documents WHERE id = ? AND user_id = ?",
            (document_id, uid),
        )
        logger.info("Deleted document id=%d", document_id)
