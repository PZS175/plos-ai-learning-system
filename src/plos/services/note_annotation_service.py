"""笔记批注高亮服务。

支持对错题、知识库文档、闪卡、聊天记录中的文本片段添加高亮标记和批注笔记。
"""

from __future__ import annotations

from typing import List, Optional

from ..db import Database, get_db
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.note_annotation_service")


class NoteAnnotationService:
    """批注笔记业务服务。"""

    def __init__(self, db: Optional[Database] = None, user_service: Optional[UserService] = None):
        self.db = db or get_db()
        self.user_service = user_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def add_annotation(
        self,
        target_type: str,
        target_id: int,
        selected_text: str,
        note_content: str,
        highlight_color: str = "#F59E0B",
        user_id: Optional[int] = None,
    ) -> int:
        """添加一条批注。"""
        uid = self._user_id(user_id)
        annotation_id = self.db.insert(
            "INSERT INTO note_annotations (user_id, target_type, target_id, selected_text, note_content, highlight_color) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, target_type, target_id, selected_text, note_content, highlight_color),
        )
        logger.info("Added annotation id=%d target=%s:%d", annotation_id, target_type, target_id)
        return annotation_id

    def update_annotation(
        self,
        annotation_id: int,
        note_content: Optional[str] = None,
        highlight_color: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> None:
        """更新批注内容或颜色。"""
        uid = self._user_id(user_id)
        fields = []
        values = []
        if note_content is not None:
            fields.append("note_content = ?")
            values.append(note_content)
        if highlight_color is not None:
            fields.append("highlight_color = ?")
            values.append(highlight_color)
        if not fields:
            return
        fields.append("updated_at = CURRENT_TIMESTAMP")
        values.extend([annotation_id, uid])
        sql = f"UPDATE note_annotations SET {', '.join(fields)} WHERE id = ? AND user_id = ?"
        self.db.execute(sql, tuple(values))

    def delete_annotation(self, annotation_id: int, user_id: Optional[int] = None) -> None:
        """删除批注。"""
        uid = self._user_id(user_id)
        self.db.execute(
            "DELETE FROM note_annotations WHERE id = ? AND user_id = ?",
            (annotation_id, uid),
        )

    def list_annotations(
        self,
        target_type: Optional[str] = None,
        target_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> List[dict]:
        """列出批注，可指定目标类型/ID。"""
        uid = self._user_id(user_id)
        conditions = ["user_id = ?"]
        params = [uid]
        if target_type:
            conditions.append("target_type = ?")
            params.append(target_type)
        if target_id is not None:
            conditions.append("target_id = ?")
            params.append(target_id)
        where = f"WHERE {' AND '.join(conditions)}"
        sql = f"SELECT * FROM note_annotations {where} ORDER BY created_at ASC"
        return self.db.fetchall(sql, tuple(params))

    def get_annotation(self, annotation_id: int, user_id: Optional[int] = None) -> Optional[dict]:
        """获取单条批注。"""
        uid = self._user_id(user_id)
        return self.db.fetchone(
            "SELECT * FROM note_annotations WHERE id = ? AND user_id = ?",
            (annotation_id, uid),
        )
