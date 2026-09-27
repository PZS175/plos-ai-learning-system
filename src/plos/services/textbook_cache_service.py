"""教材本地缓存管理服务。

管理用户下载的教材离线缓存：记录元信息、删除、清空、磁盘上限控制。
缓存目录：<数据目录>/textbook_cache/
低配机器（≤7GB）调用方应禁用缓存入口，服务内所有方法均安全降级。
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..db import Database, get_db
from ..utils.logger import get_logger
from ..utils.paths import get_data_dir
from .user_service import UserService

logger = get_logger("services.textbook_cache")

# 默认磁盘上限 500MB
DEFAULT_CACHE_LIMIT_MB = 500


class TextbookCacheService:
    """教材离线缓存管理。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self.db = db or get_db()
        self.user_service = user_service
        self.cache_dir = cache_dir or (get_data_dir() / "textbook_cache")
        self._ensure_table()
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Cannot create cache dir %s: %s", self.cache_dir, e)

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
                CREATE TABLE IF NOT EXISTS textbook_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    downloaded_at TEXT NOT NULL,
                    subject TEXT DEFAULT ''
                )
                """
            )
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_textbook_cache_user ON textbook_cache(user_id)"
            )
        except Exception as e:
            logger.error("Ensure textbook_cache table failed: %s", e)

    def _get_limit_mb(self) -> int:
        """读取磁盘上限设置（MB）。"""
        try:
            row = self.db.fetchone(
                "SELECT value FROM settings WHERE key = ?", ("textbook_cache_limit_mb",)
            )
            return int(row["value"]) if row else DEFAULT_CACHE_LIMIT_MB
        except Exception:
            return DEFAULT_CACHE_LIMIT_MB

    def set_limit_mb(self, limit_mb: int) -> None:
        """设置磁盘上限（MB）。"""
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("textbook_cache_limit_mb", str(max(50, int(limit_mb)))),
            )
        except Exception as e:
            logger.error("Set cache limit failed: %s", e)

    def cache_file(self, src_path: Path, display_name: str, subject: str = "",
                   user_id: Optional[int] = None) -> Dict[str, Any]:
        """将已下载的教材文件登记/复制到缓存目录。

        返回缓存记录 dict；超过磁盘上限抛 RuntimeError 由 UI 提示。
        """
        uid = self._user_id(user_id)
        try:
            src_path = Path(src_path)
            if not src_path.exists():
                raise FileNotFoundError(f"教材文件不存在：{src_path}")

            size = src_path.stat().st_size
            limit_bytes = self._get_limit_mb() * 1024 * 1024
            used = self.get_total_size(uid)
            if used + size > limit_bytes:
                raise RuntimeError(
                    f"缓存空间不足：已用 {used / 1024 / 1024:.1f}MB，"
                    f"本书 {size / 1024 / 1024:.1f}MB，"
                    f"上限 {self._get_limit_mb()}MB。请清理缓存或在设置中调大上限。"
                )

            safe_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{src_path.name}"
            dest = self.cache_dir / safe_name
            shutil.copy2(src_path, dest)

            now = datetime.now().isoformat(timespec="seconds")
            cache_id = self.db.insert(
                "INSERT INTO textbook_cache (user_id, name, file_path, file_size, downloaded_at, subject) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, display_name, str(dest), size, now, subject),
            )
            logger.info("Textbook cached: %s (%d bytes)", display_name, size)
            return {
                "id": cache_id,
                "name": display_name,
                "file_path": str(dest),
                "file_size": size,
                "downloaded_at": now,
                "subject": subject,
            }
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("Cache textbook failed: %s", e)
            raise RuntimeError(f"缓存教材失败：{e}") from e

    def list_cache(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """列出当前用户缓存的教材。"""
        uid = self._user_id(user_id)
        try:
            rows = self.db.fetchall(
                "SELECT * FROM textbook_cache WHERE user_id = ? ORDER BY downloaded_at DESC",
                (uid,),
            )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("List cache failed: %s", e)
            return []

    def get_total_size(self, user_id: Optional[int] = None) -> int:
        """当前用户缓存总字节数。"""
        uid = self._user_id(user_id)
        try:
            row = self.db.fetchone(
                "SELECT COALESCE(SUM(file_size), 0) AS total FROM textbook_cache WHERE user_id = ?",
                (uid,),
            )
            return int(row["total"]) if row else 0
        except Exception:
            return 0

    def delete_cache(self, cache_id: int, user_id: Optional[int] = None) -> bool:
        """删除单本缓存（文件 + 记录）。"""
        uid = self._user_id(user_id)
        try:
            row = self.db.fetchone(
                "SELECT file_path FROM textbook_cache WHERE id = ? AND user_id = ?",
                (cache_id, uid),
            )
            if not row:
                return False
            try:
                p = Path(row["file_path"])
                if p.exists():
                    p.unlink()
            except Exception as e:
                logger.warning("Delete cache file failed: %s", e)
            self.db.execute(
                "DELETE FROM textbook_cache WHERE id = ? AND user_id = ?", (cache_id, uid)
            )
            logger.info("Cache deleted: id=%d", cache_id)
            return True
        except Exception as e:
            logger.error("Delete cache failed: %s", e)
            return False

    def clear_all(self, user_id: Optional[int] = None) -> int:
        """清空当前用户全部缓存，返回删除数量。"""
        uid = self._user_id(user_id)
        try:
            items = self.list_cache(uid)
            for item in items:
                try:
                    p = Path(item["file_path"])
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass
            self.db.execute("DELETE FROM textbook_cache WHERE user_id = ?", (uid,))
            logger.info("Cleared %d cache items for user %d", len(items), uid)
            return len(items)
        except Exception as e:
            logger.error("Clear all cache failed: %s", e)
            return 0
