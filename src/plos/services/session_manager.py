"""对话会话管理器。

负责创建、读取、更新、删除对话（conversation）及其消息（message），
所有数据持久化到 SQLite。
"""

from __future__ import annotations

from typing import List, Optional

from ..core.enums import Role
from ..core.models import ChatMessage
from ..db import get_db
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.session_manager")


class SessionManager:
    """管理对话会话与消息记录。"""

    def __init__(self, db=None, user_service: Optional[UserService] = None):
        self.db = db or get_db()
        self.user_service = user_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def create_conversation(self, title: str = "新对话", user_id: Optional[int] = None) -> int:
        """创建新会话，返回 conversation_id。"""
        uid = self._user_id(user_id)
        conversation_id = self.db.insert(
            "INSERT INTO conversations (user_id, title) VALUES (?, ?)",
            (uid, title),
        )
        logger.info("Created conversation id=%d user_id=%d", conversation_id, uid)
        return conversation_id

    def rename_conversation(self, conversation_id: int, title: str, user_id: Optional[int] = None) -> None:
        uid = self._user_id(user_id)
        self.db.execute(
            "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (title, conversation_id, uid),
        )

    def delete_conversation(self, conversation_id: int, user_id: Optional[int] = None) -> None:
        uid = self._user_id(user_id)
        self.db.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, uid),
        )
        logger.info("Deleted conversation id=%d", conversation_id)

    def list_conversations(self, limit: int = 50, user_id: Optional[int] = None) -> List[dict]:
        uid = self._user_id(user_id)
        return self.db.fetchall(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE user_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (uid, limit),
        )

    def add_message(
        self,
        conversation_id: int,
        role: Role,
        content: str,
        images: Optional[List[str]] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """添加一条消息。"""
        uid = self._user_id(user_id)
        images_str = ",".join(images) if images else None
        with self.db.transaction():
            cursor = self.db._connect().execute(
                "INSERT INTO messages (user_id, conversation_id, role, content, images) VALUES (?, ?, ?, ?, ?)",
                (uid, conversation_id, role.value, content, images_str),
            )
            message_id = cursor.lastrowid or 0
            self.db._connect().execute(
                "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
                (conversation_id, uid),
            )
        return message_id

    def get_messages(self, conversation_id: int, user_id: Optional[int] = None) -> List[ChatMessage]:
        """获取某会话的所有消息，按时间正序。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT role, content, images FROM messages WHERE conversation_id = ? AND user_id = ? "
            "ORDER BY created_at ASC",
            (conversation_id, uid),
        )
        messages: List[ChatMessage] = []
        for row in rows:
            images = row["images"].split(",") if row["images"] else []
            messages.append(
                ChatMessage(
                    role=Role(row["role"]),
                    content=row["content"],
                    images=images,
                )
            )
        return messages

    def clear_messages(self, conversation_id: int, user_id: Optional[int] = None) -> None:
        uid = self._user_id(user_id)
        self.db.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, uid),
        )
