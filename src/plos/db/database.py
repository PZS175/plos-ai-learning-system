"""SQLite database manager with schema initialization.

Uses sqlite3 with foreign keys and row factory for dict-like access.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger
from ..utils.paths import get_db_path

logger = get_logger("db.database")

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


class Database:
    """按数据库路径缓存的 SQLite 单例管理器。

    同一路径全局共享一个实例；不同路径（如测试用的临时库）
    各自持有独立实例，避免单例忽略构造参数导致的串库问题。
    """

    _instances: Dict[Path, "Database"] = {}

    def __new__(cls, db_path: Optional[Path] = None):
        path = Path(db_path or get_db_path()).resolve()
        if path not in cls._instances:
            instance = super().__new__(cls)
            instance._initialized = False
            cls._instances[path] = instance
        return cls._instances[path]

    def __init__(self, db_path: Optional[Path] = None):
        if self._initialized:
            return
        self.db_path = db_path or get_db_path()
        self._initialized = True
        self._ensure_dir()
        self._lock = threading.RLock()
        self._connection: Optional[sqlite3.Connection] = None
        self._init_schema()

    def _ensure_dir(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self):
        """原子事务上下文：持锁执行多条语句，成功提交、异常回滚。

        所有跨线程共享本连接的多语句序列都应使用它包裹。
        """
        with self._lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _connect(self) -> sqlite3.Connection:
        if self._connection is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._connection = conn
        return self._connection

    def _init_schema(self) -> None:
        if not SCHEMA_FILE.exists():
            logger.error("Schema file not found: %s", SCHEMA_FILE)
            return
        sql = SCHEMA_FILE.read_text(encoding="utf-8")
        conn = self._connect()
        try:
            conn.executescript(sql)
            conn.commit()
            logger.info("Database initialized: %s", self.db_path)
        except sqlite3.Error as e:
            logger.error("Failed to initialize database schema: %s", e)
            raise
        from .migrations import run_migrations

        run_migrations(self)

    def _column_exists(self, table: str, column: str) -> bool:
        """检查表中是否已存在指定列。"""
        try:
            cursor = self._connect().execute(f"PRAGMA table_info({table})")
            return any(row[1] == column for row in cursor.fetchall())
        except sqlite3.Error as e:
            logger.warning("Failed to check column %s.%s: %s", table, column, e)
            return False

    def get_connection(self) -> sqlite3.Connection:
        return self._connect()

    def execute(
        self, query: str, parameters: tuple = ()
    ) -> sqlite3.Cursor:
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(query, parameters)
                conn.commit()
                return cursor
            except sqlite3.Error as e:
                logger.error("SQL execute error: %s | query=%s | params=%s", e, query, parameters)
                raise

    def fetchone(self, query: str, parameters: tuple = ()) -> Optional[Dict[str, Any]]:
        cursor = self.execute(query, parameters)
        row = cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self, query: str, parameters: tuple = ()) -> List[Dict[str, Any]]:
        cursor = self.execute(query, parameters)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def insert(self, query: str, parameters: tuple = ()) -> int:
        """Insert and return the last row id."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(query, parameters)
                conn.commit()
                return cursor.lastrowid or 0
            except sqlite3.Error as e:
                logger.error("SQL insert error: %s | query=%s", e, query)
                raise

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            logger.info("Database connection closed")

    def __del__(self):
        # 解释器退出阶段 logger/锁可能已被回收，关闭失败静默忽略
        try:
            self.close()
        except Exception:
            pass


def get_db(db_path: Optional[Path] = None) -> Database:
    """Get the singleton Database instance."""
    return Database(db_path)
