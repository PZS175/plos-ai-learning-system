"""全局搜索服务。

跨表检索当前用户的学习数据：错题、闪卡、知识库文档、AI 对话历史、OCR 识别记录。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..db import Database, get_db
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.search_service")


class SearchService:
    """全局搜索业务服务。"""

    def __init__(self, db: Optional[Database] = None, user_service: Optional[UserService] = None):
        self.db = db or get_db()
        self.user_service = user_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def search(
        self,
        keyword: str,
        scope: Optional[List[str]] = None,
        limit: int = 20,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """全局搜索，返回统一格式的结果列表。

        scope 可选范围：error_book, flashcard, document, conversation, ocr_record。
        每个结果包含：type, id, title, snippet, target_page（部分类型需要）。
        """
        uid = self._user_id(user_id)
        scopes = scope or ["error_book", "flashcard", "document", "conversation", "ocr_record"]
        like = f"%{keyword}%"
        results: List[Dict[str, Any]] = []

        if "error_book" in scopes:
            rows = self.db.fetchall(
                "SELECT id, question, answer, analysis, subject, knowledge_point FROM error_book "
                "WHERE user_id = ? AND (question LIKE ? OR answer LIKE ? OR analysis LIKE ? OR subject LIKE ? OR knowledge_point LIKE ?) "
                "ORDER BY updated_at DESC LIMIT ?",
                (uid, like, like, like, like, like, limit),
            )
            for r in rows:
                results.append({
                    "type": "error_book",
                    "id": r["id"],
                    "title": f"错题 #{r['id']} - {r['subject'] or '未分类'}",
                    "subtitle": r["knowledge_point"] or "",
                    "snippet": r["question"][:200],
                })

        if "flashcard" in scopes:
            rows = self.db.fetchall(
                "SELECT id, front_content, back_content, subject, tags FROM flashcards "
                "WHERE user_id = ? AND (front_content LIKE ? OR back_content LIKE ? OR subject LIKE ? OR tags LIKE ?) "
                "ORDER BY next_review ASC LIMIT ?",
                (uid, like, like, like, like, limit),
            )
            for r in rows:
                results.append({
                    "type": "flashcard",
                    "id": r["id"],
                    "title": f"闪卡 #{r['id']} - {r['subject'] or '未分类'}",
                    "subtitle": r["tags"] or "",
                    "snippet": r["front_content"][:200],
                })

        if "document" in scopes:
            rows = self.db.fetchall(
                "SELECT id, filename FROM documents "
                "WHERE user_id = ? AND filename LIKE ? ORDER BY created_at DESC LIMIT ?",
                (uid, like, limit),
            )
            for r in rows:
                results.append({
                    "type": "document",
                    "id": r["id"],
                    "title": r["filename"],
                    "subtitle": "知识库文档",
                    "snippet": r["filename"],
                })

        if "conversation" in scopes:
            rows = self.db.fetchall(
                "SELECT c.id, c.title, m.content FROM conversations c "
                "LEFT JOIN messages m ON m.conversation_id = c.id AND m.user_id = c.user_id "
                "WHERE c.user_id = ? AND (c.title LIKE ? OR m.content LIKE ?) "
                "GROUP BY c.id ORDER BY c.updated_at DESC LIMIT ?",
                (uid, like, like, limit),
            )
            for r in rows:
                results.append({
                    "type": "conversation",
                    "id": r["id"],
                    "title": r["title"] or "新对话",
                    "subtitle": "AI 对话",
                    "snippet": (r["content"] or "")[:200],
                })

        if "ocr_record" in scopes:
            rows = self.db.fetchall(
                "SELECT id, source_path, recognized_text FROM ocr_records "
                "WHERE user_id = ? AND (source_path LIKE ? OR recognized_text LIKE ?) "
                "ORDER BY created_at DESC LIMIT ?",
                (uid, like, like, limit),
            )
            for r in rows:
                results.append({
                    "type": "ocr_record",
                    "id": r["id"],
                    "title": f"OCR 记录 #{r['id']}",
                    "subtitle": r["source_path"] or "",
                    "snippet": r["recognized_text"][:200],
                })

        return results
