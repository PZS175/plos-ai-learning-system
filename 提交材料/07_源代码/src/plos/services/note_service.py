"""富文本笔记服务：笔记本（文件夹）分类管理，笔记 CRUD。

笔记内容以 HTML 富文本存储（QTextEdit 原生 toHtml），同时保存纯文本副本便于搜索。
所有数据按 user_id 隔离，纯本地存储。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..db import Database, get_db
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.notes")


class NoteService:
    """富文本笔记本服务。"""

    def __init__(self, db: Optional[Database] = None, user_service: Optional[UserService] = None) -> None:
        self.db = db or get_db()
        self.user_service = user_service
        self._ensure_table()

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def _ensure_table(self) -> None:
        """幂等建表。"""
        try:
            self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    notebook TEXT NOT NULL DEFAULT '默认笔记本',
                    title TEXT NOT NULL DEFAULT '无标题笔记',
                    content_html TEXT NOT NULL DEFAULT '',
                    content_text TEXT NOT NULL DEFAULT '',
                    linked_kp TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id)")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_notes_nb ON notes(user_id, notebook)")
        except Exception as e:
            logger.error("Ensure notes table failed: %s", e)

    def list_notebooks(self, user_id: Optional[int] = None) -> List[str]:
        """列出当前用户的所有笔记本名称。"""
        uid = self._user_id(user_id)
        try:
            rows = self.db.fetchall(
                "SELECT DISTINCT notebook FROM notes WHERE user_id = ? AND notebook != '' ORDER BY notebook",
                (uid,),
            )
            names = [r["notebook"] for r in rows]
            if "默认笔记本" not in names:
                names.insert(0, "默认笔记本")
            return names
        except Exception as e:
            logger.error("List notebooks failed: %s", e)
            return ["默认笔记本"]

    def list_notes(
        self, notebook: Optional[str] = None, keyword: str = "", user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """列出笔记，可按笔记本和关键词过滤。"""
        uid = self._user_id(user_id)
        try:
            sql = "SELECT id, notebook, title, content_text, linked_kp, created_at, updated_at " \
                  "FROM notes WHERE user_id = ?"
            params: List[Any] = [uid]
            if notebook:
                sql += " AND notebook = ?"
                params.append(notebook)
            if keyword:
                sql += " AND (title LIKE ? OR content_text LIKE ?)"
                params.extend([f"%{keyword}%", f"%{keyword}%"])
            sql += " ORDER BY updated_at DESC"
            rows = self.db.fetchall(sql, tuple(params))
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("List notes failed: %s", e)
            return []

    def get_note(self, note_id: int, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        uid = self._user_id(user_id)
        try:
            row = self.db.fetchone(
                "SELECT * FROM notes WHERE id = ? AND user_id = ?", (note_id, uid)
            )
            return dict(row) if row else None
        except Exception as e:
            logger.error("Get note failed: %s", e)
            return None

    def save_note(
        self,
        title: str,
        content_html: str,
        content_text: str,
        notebook: str = "默认笔记本",
        linked_kp: str = "",
        note_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """新建或更新笔记，返回笔记 id。"""
        uid = self._user_id(user_id)
        now = datetime.now().isoformat(timespec="seconds")
        title = title.strip() or "无标题笔记"
        notebook = notebook.strip() or "默认笔记本"
        try:
            if note_id:
                self.db.execute(
                    "UPDATE notes SET title=?, content_html=?, content_text=?, notebook=?, "
                    "linked_kp=?, updated_at=? WHERE id=? AND user_id=?",
                    (title, content_html, content_text, notebook, linked_kp, now, note_id, uid),
                )
                logger.info("Updated note id=%d", note_id)
                return note_id
            note_id = self.db.insert(
                "INSERT INTO notes (user_id, notebook, title, content_html, content_text, linked_kp, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, notebook, title, content_html, content_text, linked_kp, now, now),
            )
            logger.info("Created note id=%d title=%s", note_id, title)
            return note_id
        except Exception as e:
            logger.error("Save note failed: %s", e)
            raise RuntimeError(f"保存笔记失败：{e}") from e

    def delete_note(self, note_id: int, user_id: Optional[int] = None) -> bool:
        uid = self._user_id(user_id)
        try:
            cur = self.db.execute(
                "DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, uid)
            )
            return getattr(cur, "rowcount", 1) > 0
        except Exception as e:
            logger.error("Delete note failed: %s", e)
            return False
