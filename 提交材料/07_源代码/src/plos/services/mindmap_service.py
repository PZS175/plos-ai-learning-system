"""思维导图生成服务。

调用本地 Qwen 模型基于错题或文档内容生成思维导图 JSON，并持久化保存。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..ai import ModelManager
from ..core.enums import Role
from ..core.models import ChatMessage
from ..db import Database, get_db
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.mindmap_service")


_MINDMAP_PROMPT = (
    "请根据以下内容生成一张思维导图，以 JSON 格式输出。\n"
    "JSON 结构要求：\n"
    "{\"root\": {\"text\": \"中心主题\", \"children\": ["
    "{\"text\": \"一级节点\", \"children\": [{\"text\": \"二级节点\"}]}]}}\n"
    "只输出 JSON，不要其他解释。内容如下：\n\n{text}"
)


class MindMapService:
    """思维导图生成与保存服务。"""

    def __init__(
        self,
        model_manager: Optional[ModelManager] = None,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
    ):
        self.model_manager = model_manager
        self.db = db or get_db()
        self.user_service = user_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def generate_mindmap(
        self,
        title: str,
        content: str,
        target_type: str,
        target_id: int,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """生成并保存思维导图，返回 graph_json。"""
        uid = self._user_id(user_id)
        graph = self._call_llm_for_mindmap(title, content)

        existing = self.db.fetchone(
            "SELECT id FROM mindmaps WHERE target_type = ? AND target_id = ? AND user_id = ?",
            (target_type, target_id, uid),
        )
        if existing:
            self.db.execute(
                "UPDATE mindmaps SET title = ?, graph_json = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (title, json.dumps(graph, ensure_ascii=False), existing["id"]),
            )
            map_id = existing["id"]
        else:
            map_id = self.db.insert(
                "INSERT INTO mindmaps (user_id, target_type, target_id, title, graph_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (uid, target_type, target_id, title, json.dumps(graph, ensure_ascii=False)),
            )
        logger.info("Generated mindmap id=%d target=%s:%d", map_id, target_type, target_id)
        return graph

    def _call_llm_for_mindmap(self, title: str, content: str) -> Dict[str, Any]:
        """调用本地大模型生成思维导图 JSON。"""
        fallback = {"root": {"text": title, "children": [{"text": "未生成节点"}]}}
        if self.model_manager is None or not self.model_manager.is_text_available():
            return fallback

        prompt = _MINDMAP_PROMPT.format(text=content[:4000])
        messages = [ChatMessage(role=Role.USER, content=prompt)]
        try:
            response = self.model_manager.chat(messages=messages, max_tokens=2048)
            if response is None:
                logger.warning("Mindmap generation returned empty response")
                return fallback
            text = response.content.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            graph = json.loads(text)
            if "root" in graph:
                return graph
        except Exception as e:
            logger.warning("Mindmap generation failed: %s", e)
        return fallback

    def get_mindmap(
        self,
        target_type: str,
        target_id: int,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """获取已保存的思维导图。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT graph_json FROM mindmaps WHERE target_type = ? AND target_id = ? AND user_id = ?",
            (target_type, target_id, uid),
        )
        if row:
            try:
                return json.loads(row["graph_json"])
            except json.JSONDecodeError:
                return None
        return None

    def delete_mindmap(
        self,
        target_type: str,
        target_id: int,
        user_id: Optional[int] = None,
    ) -> None:
        """删除思维导图。"""
        uid = self._user_id(user_id)
        self.db.execute(
            "DELETE FROM mindmaps WHERE target_type = ? AND target_id = ? AND user_id = ?",
            (target_type, target_id, uid),
        )
