"""术语词典服务。

支持从阅读笔记、OCR 文本中自动提取专业术语并保存到本地词典库，
提供查询、编辑释义、搜索等功能。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..ai import ModelManager
from ..core.enums import Role
from ..core.models import ChatMessage
from ..db import Database, get_db
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.terminology_service")


_EXTRACT_PROMPT = (
    "你是专业的学术术语提取助手。请从下面这段文本中提取 3-10 个专业术语或关键概念，"
    "并为每个术语给出简明的中文释义。\n\n"
    "文本：\n{text}\n\n"
    "请严格输出 JSON 数组，不要输出任何其他内容：\n"
    "[\n"
    "  {{\"term\": \"术语1\", \"definition\": \"释义1\"}},\n"
    "  {{\"term\": \"术语2\", \"definition\": \"释义2\"}}\n"
    "]"
)


def _extract_json(text: str) -> Optional[list]:
    """从模型输出中提取 JSON 数组。"""
    if not text:
        return None
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].split("```")[0].strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, list) else None
    except Exception:
        return None


class TerminologyService:
    """术语词典业务服务。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
        model_manager: Optional[ModelManager] = None,
    ):
        self.db = db or get_db()
        self.user_service = user_service
        self.model_manager = model_manager

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def extract_terms(
        self,
        text: str,
        source_type: str = "",
        source_id: int = 0,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """从文本中自动提取专业术语并保存到词典。

        返回成功保存的术语列表（含 id）。
        """
        uid = self._user_id(user_id)
        if not text or not text.strip():
            return []

        extracted: List[Dict[str, str]] = []
        if self.model_manager is not None and self.model_manager.is_text_available():
            try:
                prompt = _EXTRACT_PROMPT.format(text=text[:4000])
                response = self.model_manager.chat(messages=[
                    ChatMessage(role=Role.USER, content=prompt)
                ])
                if response and response.content:
                    data = _extract_json(response.content)
                    if data:
                        extracted = [
                            {"term": str(item.get("term", "")), "definition": str(item.get("definition", ""))}
                            for item in data
                            if item.get("term")
                        ]
            except Exception as e:
                logger.warning("Model term extraction failed: %s", e)

        # 兜底：如果模型不可用或提取失败，按简单规则提取可能术语（长度>=4 的中文词组）
        if not extracted:
            logger.info("Falling back to simple term extraction")
            extracted = self._simple_extract(text)

        saved = []
        for item in extracted:
            # 避免重复添加同一术语
            existing = self.db.fetchone(
                "SELECT id FROM terms WHERE user_id = ? AND term = ? LIMIT 1",
                (uid, item["term"]),
            )
            if existing:
                continue
            term_id = self.db.insert(
                "INSERT INTO terms (user_id, term, definition, source_type, source_id, context) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, item["term"], item["definition"], source_type, source_id, text[:1000]),
            )
            saved.append({"id": term_id, **item})
        return saved

    @staticmethod
    def _simple_extract(text: str) -> List[Dict[str, str]]:
        """简单兜底提取：基于常见模式识别可能的术语。"""
        import re
        # 匹配中英文术语：连续字母/数字 或 2-8 个连续中文字符
        matches = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}|[^\x00-\xff]{2,8}", text)
        seen = set()
        result = []
        for m in matches:
            if m in seen or len(m) < 3:
                continue
            seen.add(m)
            result.append({"term": m, "definition": ""})
        return result[:10]

    def list_terms(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """列出当前用户的所有术语。"""
        uid = self._user_id(user_id)
        return self.db.fetchall(
            "SELECT * FROM terms WHERE user_id = ? ORDER BY created_at DESC, id DESC",
            (uid,),
        )

    def search_terms(
        self, keyword: str, user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """按关键词搜索术语。"""
        uid = self._user_id(user_id)
        like = f"%{keyword}%"
        return self.db.fetchall(
            "SELECT * FROM terms WHERE user_id = ? AND (term LIKE ? OR definition LIKE ?) "
            "ORDER BY created_at DESC",
            (uid, like, like),
        )

    def get_term(self, term_id: int, user_id: Optional[int] = None) -> Optional[dict]:
        """获取单条术语。"""
        uid = self._user_id(user_id)
        return self.db.fetchone(
            "SELECT * FROM terms WHERE id = ? AND user_id = ?",
            (term_id, uid),
        )

    def update_definition(
        self,
        term_id: int,
        definition: str,
        user_id: Optional[int] = None,
    ) -> None:
        """更新术语释义。"""
        uid = self._user_id(user_id)
        self.db.execute(
            "UPDATE terms SET definition = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND user_id = ?",
            (definition, term_id, uid),
        )

    def delete_term(self, term_id: int, user_id: Optional[int] = None) -> None:
        """删除术语。"""
        uid = self._user_id(user_id)
        self.db.execute(
            "DELETE FROM terms WHERE id = ? AND user_id = ?",
            (term_id, uid),
        )

    def get_terms_by_source(
        self, source_type: str, source_id: int, user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """查询来源于指定笔记/OCR 的术语。"""
        uid = self._user_id(user_id)
        return self.db.fetchall(
            "SELECT * FROM terms WHERE user_id = ? AND source_type = ? AND source_id = ? "
            "ORDER BY created_at DESC",
            (uid, source_type, source_id),
        )
