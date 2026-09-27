"""用户账户服务。

支持多用户隔离：创建、切换、删除用户。
当前选中用户 ID 持久化在 settings 表，所有业务数据通过 user_id 外键隔离。
密码使用 PBKDF2-HMAC-SHA256 加盐哈希存储，不保存明文。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import List, Optional

from ..db import Database, get_db
from ..utils.logger import get_logger

logger = get_logger("services.user_service")

# PBKDF2 参数：迭代次数与盐长度
_PBKDF2_ITERATIONS = 100_000
_SALT_BYTES = 16
_HASH_BYTES = 32


def _hash_password(password: str) -> str:
    """对密码做 PBKDF2-HMAC-SHA256 加盐哈希，返回 ``salt$hash`` 字符串。

    盐与哈希均用 base64 编码存储，便于数据库 TEXT 字段保存。
    """
    if not password:
        return ""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_HASH_BYTES
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(derived).decode("ascii")
    return f"{salt_b64}${hash_b64}"


def _verify_password(password: str, stored: str) -> bool:
    """校验密码与存储的哈希是否匹配。

    - ``stored`` 为空表示该账号未设置密码（旧账号兼容），直接通过。
    - 格式不符或不匹配均返回 False。
    """
    if not stored:
        return True
    if not password:
        return False
    try:
        salt_b64, hash_b64 = stored.split("$", 1)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=len(expected)
    )
    return secrets.compare_digest(derived, expected)


class UserService:
    """用户账户管理服务。"""

    _CURRENT_USER_KEY = "current_user_id"

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_db()
        self._ensure_default_user()

    def _ensure_default_user(self) -> None:
        """确保至少存在一个默认用户，并将现有 user_id=0 的数据归到默认用户下。"""
        users = self.list_users()
        if users:
            return
        default_id = self.create_user("default", "默认用户")
        self.set_current_user(default_id)
        # 迁移旧数据
        self._migrate_legacy_data(default_id)
        logger.info("Created default user id=%d and migrated legacy data", default_id)

    def _migrate_legacy_data(self, user_id: int) -> None:
        """将旧版本中 user_id 为 0 或 NULL 的数据迁移到指定用户。"""
        tables = ["error_book", "flashcards", "conversations", "messages", "documents", "chunks", "study_logs"]
        conn = self.db.get_connection()
        for table in tables:
            try:
                conn.execute(
                    f"UPDATE {table} SET user_id = ? WHERE user_id = 0 OR user_id IS NULL",
                    (user_id,),
                )
            except Exception as e:
                logger.warning("Failed to migrate legacy data for %s: %s", table, e)
        try:
            conn.commit()
        except Exception as e:
            logger.warning("Failed to commit legacy migration: %s", e)

    def create_user(self, username: str, nickname: str = "", password: str = "") -> int:
        """创建新用户，返回 user_id。

        ``password`` 为空表示无密码账号（兼容旧逻辑）；否则存储 PBKDF2 哈希。
        """
        password_hash = _hash_password(password)
        user_id = self.db.insert(
            "INSERT INTO users (username, nickname, password_hash) VALUES (?, ?, ?)",
            (username, nickname or username, password_hash),
        )
        logger.info("Created user id=%d username=%s", user_id, username)
        return user_id

    def delete_user(self, user_id: int) -> None:
        """删除用户及其所有关联数据。"""
        if user_id == self.get_current_user_id():
            # 切换到一个安全用户再删除
            other = self.db.fetchone(
                "SELECT id FROM users WHERE id != ? LIMIT 1", (user_id,)
            )
            if other:
                self.set_current_user(other["id"])
            else:
                # 最后一个用户，创建新的默认用户
                new_id = self.create_user("default", "默认用户")
                self.set_current_user(new_id)

        tables = [
            "error_book", "flashcards", "conversations", "messages", "documents",
            "chunks", "study_logs", "study_plans", "study_plan_tasks",
            "note_annotations", "statistics_records", "mindmaps", "ocr_records",
            # 新增模块的用户数据表（避免删号后残留造成串号）
            "notes", "textbook_cache", "review_schedules",
            "practice_questions", "practice_records", "user_stat",
            "subjective_grades", "terms", "exam_papers",
            "asr_records", "diagram_records", "learning_packages",
            "teaching_style_config",
        ]
        conn = self.db.get_connection()
        # exam_paper_questions 无 user_id，须在删除试卷前通过试卷归属清理孤儿题目
        try:
            conn.execute(
                "DELETE FROM exam_paper_questions WHERE paper_id IN "
                "(SELECT id FROM exam_papers WHERE user_id = ?)",
                (user_id,),
            )
        except Exception as e:
            logger.warning("Failed to delete exam_paper_questions for user %s: %s", user_id, e)
        for table in tables:
            try:
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            except Exception as e:
                logger.warning("Failed to delete user data from %s: %s", table, e)
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        logger.info("Deleted user id=%d", user_id)

    def list_users(self) -> List[dict]:
        """列出所有用户（不返回密码哈希）。"""
        return self.db.fetchall(
            "SELECT id, username, nickname, created_at FROM users ORDER BY created_at ASC"
        )

    def get_user(self, user_id: int) -> Optional[dict]:
        """获取单个用户信息（不返回密码哈希）。"""
        return self.db.fetchone(
            "SELECT id, username, nickname, created_at FROM users WHERE id = ?",
            (user_id,),
        )

    def verify_password(self, user_id: int, password: str) -> bool:
        """校验用户密码。

        - 未设置密码的账号（旧账号兼容）直接通过，方便迁移。
        - 校验失败或用户不存在返回 False。
        """
        row = self.db.fetchone(
            "SELECT password_hash FROM users WHERE id = ?", (user_id,)
        )
        if not row:
            return False
        return _verify_password(password, row.get("password_hash") or "")

    def set_current_user(self, user_id: int) -> None:
        """设置当前登录用户。"""
        self.db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (self._CURRENT_USER_KEY, str(user_id)),
        )
        logger.info("Current user set to id=%d", user_id)

    def get_current_user_id(self) -> int:
        """获取当前用户 ID，不存在则返回默认用户。"""
        row = self.db.fetchone(
            "SELECT value FROM settings WHERE key = ?", (self._CURRENT_USER_KEY,)
        )
        if row:
            try:
                return int(row["value"])
            except (ValueError, TypeError):
                pass
        # 兜底：返回第一个用户
        users = self.list_users()
        if users:
            user_id = users[0]["id"]
            self.set_current_user(user_id)
            return user_id
        # 极端情况创建默认用户
        default_id = self.create_user("default", "默认用户")
        self.set_current_user(default_id)
        return default_id

    def get_current_user(self) -> Optional[dict]:
        """获取当前用户信息。"""
        return self.get_user(self.get_current_user_id())

    def rename_user(self, user_id: int, nickname: str) -> None:
        """修改用户昵称。"""
        self.db.execute(
            "UPDATE users SET nickname = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (nickname, user_id),
        )
